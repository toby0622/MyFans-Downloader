from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from myfans_downloader.download_state import DownloadState
from myfans_downloader.downloader import generate_filename
from myfans_downloader.logging_config import redact_log_text
from myfans_downloader.myfans_api import ENDPOINTS
from myfans_downloader.runtime_paths import RuntimePaths, get_runtime_paths
from myfans_downloader.secure_storage import protect_secret, unprotect_secret
from myfans_downloader.settings_store import SettingsError, SettingsStore


def temporary_paths(root: Path) -> RuntimePaths:
    return RuntimePaths(
        resource_dir=root,
        ui_dir=root / "myfans_downloader" / "ui",
        data_dir=root / "data",
        config_dir=root / "data",
        config_file=root / "data" / "config.ini",
        downloads_dir=root / "downloads",
        ffmpeg_dir=root / "ffmpeg",
        log_file=root / "data" / "app.log",
        webview_storage_dir=root / "data" / "webview",
        frozen=True,
    )


class SettingsStoreTests(unittest.TestCase):
    def test_defaults_are_absolute_and_token_is_not_returned(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = temporary_paths(Path(temp_dir))
            with patch.dict(os.environ, {}, clear=False):
                store = SettingsStore(paths)
                settings = store.get()
            self.assertTrue(Path(settings["output_dir"]).is_absolute())
            self.assertFalse(settings["auth_token_set"])
            self.assertNotIn("auth_token", settings)
            self.assertIn("{id}", settings["filename_pattern"])

    def test_save_is_validated_and_preserves_an_unsubmitted_token(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = temporary_paths(Path(temp_dir))
            store = SettingsStore(paths)
            with (
                patch(
                    "myfans_downloader.settings_store.protect_secret",
                    side_effect=lambda value: "dpapi:test-protected" if value else "",
                ),
                patch(
                    "myfans_downloader.settings_store.unprotect_secret",
                    side_effect=lambda value: (
                        "secret-token" if value == "dpapi:test-protected" else value
                    ),
                ),
            ):
                first = store.save(
                    {
                        "filename_pattern": "{creator}_{date}_{id}",
                        "filename_separator": "_",
                        "thread_count": 6,
                        "output_dir": str(paths.downloads_dir),
                        "write_metadata": "1",
                        "auth_token": "secret-token",
                    }
                )
                self.assertTrue(first["auth_token_set"])
                store.save({"auth_token": "", "thread_count": 4})
                self.assertEqual(
                    store.get(include_token=True)["auth_token"], "secret-token"
                )
                config_text = paths.config_file.read_text(encoding="utf-8")
                self.assertNotIn("secret-token", config_text)
                self.assertIn("dpapi:", config_text)
                with self.assertRaises(SettingsError):
                    store.save({"filename_pattern": "{unknown}"})


class SecureStorageTests(unittest.TestCase):
    def test_plaintext_token_is_rejected(self):
        with self.assertRaises(OSError):
            unprotect_secret("plaintext-token")

    def test_dpapi_round_trip_when_available(self):
        try:
            protected = protect_secret("round-trip-secret")
        except OSError as exc:
            self.skipTest(f"DPAPI is unavailable in this sandbox: {exc}")
        self.assertNotIn("round-trip-secret", protected)
        self.assertEqual(unprotect_secret(protected), "round-trip-secret")


class LoggingSecurityTests(unittest.TestCase):
    def test_secrets_and_signed_url_queries_are_redacted(self):
        message = (
            "authorization: Bearer super-secret-value "
            "https://cdn.example/video.m3u8?token=signed-secret&expires=123"
        )
        redacted = redact_log_text(message)
        self.assertNotIn("super-secret-value", redacted)
        self.assertNotIn("signed-secret", redacted)
        self.assertIn("[redacted]", redacted)


class DownloadStateTests(unittest.TestCase):
    def test_fetch_sentinel_is_not_persisted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = DownloadState(temp_dir)
            state.add_download("FETCHING", status="fetching")
            state.mark_completed("FETCHING")
            self.assertEqual(state.get_serializable_state()["downloads"], {})
            self.assertFalse(state.is_completed("FETCHING"))

    def test_completed_ids_are_queried_in_batches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = DownloadState(temp_dir)
            for post_id in ("one", "three"):
                state.mark_completed(post_id)
            self.assertEqual(
                state.completed_ids(["one", "two", "three"]), {"one", "three"}
            )


class DownloadHelpersTests(unittest.TestCase):
    def test_endpoint_builder_quotes_identifiers(self):
        self.assertTrue(ENDPOINTS.post("a/b").endswith("/posts/a%2Fb"))
        self.assertTrue(
            ENDPOINTS.user_posts("user/id").endswith("/users/user%2Fid/posts")
        )

    def test_filename_is_safe_and_gets_unique_id(self):
        post = {
            "id": "post-123",
            "title": "Unsafe: title",
            "posted_at": "2026-08-15T10:00:00+00:00",
            "user": {"username": "creator"},
        }
        filename = generate_filename(
            post,
            {"pattern": "{creator}_{date}_{title}", "separator": "-"},
        )
        self.assertIn("post-123", filename)
        self.assertNotIn(":", filename)
        self.assertTrue(filename.endswith(".mp4"))


class DesktopArchitectureTests(unittest.TestCase):
    def test_source_runtime_data_is_outside_the_package(self):
        with patch.dict(os.environ, {}, clear=True):
            paths = get_runtime_paths()
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(paths.data_dir, root / ".runtime")
        self.assertEqual(paths.downloads_dir, root / "downloads")
        self.assertEqual(paths.ui_dir, root / "src" / "myfans_downloader" / "ui")

    def test_frontend_has_no_http_fallback(self):
        root = Path(__file__).resolve().parents[1]
        package = root / "src" / "myfans_downloader"
        app_source = (package / "app.py").read_text(encoding="utf-8")
        javascript = (package / "ui" / "app.js").read_text(encoding="utf-8")
        html = (package / "ui" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("Flask", app_source)
        self.assertNotIn("app.run", app_source)
        self.assertNotIn("EventSource", javascript)
        self.assertNotIn("fetch(", javascript)
        self.assertNotIn("DESKTOP APPLICATION", html)
        self.assertNotIn("APPLICATION READY", html + javascript + app_source)
        self.assertNotIn("runtimeText", html + javascript + app_source)
        self.assertIn("connect-src 'none'", html)
        self.assertNotIn("configparser", app_source)


if __name__ == "__main__":
    unittest.main()
