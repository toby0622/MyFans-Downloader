"""Narrow Python API exposed only to the packaged desktop UI."""

from __future__ import annotations

import logging
import os
import re
import threading
from collections import deque
from typing import Any

from myfans_downloader import downloader
from myfans_downloader.download_state import DownloadState
from myfans_downloader.runtime_paths import RuntimePaths
from myfans_downloader.settings_store import SettingsError, SettingsStore

logger = logging.getLogger(__name__)


class ProgressEventBuffer:
    """Thread-safe, bounded progress stream consumed by the WebView bridge."""

    def __init__(self, max_events: int = 2000):
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._next_id = 1
        self._lock = threading.Lock()

    def put(self, message: Any) -> None:
        with self._lock:
            self._events.append({"id": self._next_id, "message": str(message)})
            self._next_id += 1

    def get_since(self, after_id: int) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(event) for event in self._events if event["id"] > after_id]


class DesktopApi:
    _USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
    _POST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
    _RESOLUTIONS = frozenset({"best", "uhd", "fhd", "hd", "sd", "ld"})
    _POST_TYPES = frozenset({"videos", "images"})
    _DOWNLOAD_TYPES = frozenset({"all", "free", "subscribed", "single"})

    def __init__(self, paths: RuntimePaths, settings: SettingsStore):
        self.paths = paths
        self.settings = settings
        self.events = ProgressEventBuffer()
        self.download_state = DownloadState(str(paths.config_dir))
        self.cancel_event = threading.Event()
        self._download_lock = threading.Lock()
        self._window: Any = None

    def _attach_window(self, window: Any) -> None:
        self._window = window

    def bootstrap(self) -> dict[str, Any]:
        return {
            "settings": self.settings.get(),
            "status": self.download_state.get_serializable_state(),
            "running": self._download_lock.locked(),
            "data_dir": str(self.paths.data_dir),
        }

    def get_status(self) -> dict[str, Any]:
        return {
            "state": self.download_state.get_serializable_state(),
            "running": self._download_lock.locked(),
        }

    def get_events(self, after_id: int = 0) -> dict[str, Any]:
        try:
            cursor = max(0, int(after_id))
        except (TypeError, ValueError):
            cursor = 0
        return {
            "events": self.events.get_since(cursor),
            "running": self._download_lock.locked(),
        }

    def get_settings(self) -> dict[str, Any]:
        return self.settings.get()

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            settings = self.settings.save(payload)
            logger.info("Application settings updated")
            return {"ok": True, "settings": settings}
        except (SettingsError, OSError) as exc:
            logger.warning("Settings validation failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    def choose_output_directory(self) -> dict[str, Any]:
        if self._window is None:
            return {"ok": False, "error": "Application window is not ready."}
        try:
            import webview

            selection = self._window.create_file_dialog(webview.FOLDER_DIALOG)
            if not selection:
                return {"ok": True, "cancelled": True}
            path = selection[0] if isinstance(selection, (list, tuple)) else selection
            return {"ok": True, "path": str(path)}
        except Exception as exc:
            logger.exception("Could not open folder picker")
            return {"ok": False, "error": str(exc)}

    def open_output_directory(self) -> dict[str, Any]:
        try:
            output_dir = self.settings.get()["output_dir"]
            os.makedirs(output_dir, exist_ok=True)
            if os.name == "nt":
                os.startfile(output_dir)  # type: ignore[attr-defined]
            else:
                raise OSError("Opening folders is only supported by the Windows build.")
            return {"ok": True}
        except Exception as exc:
            logger.exception("Could not open download directory")
            return {"ok": False, "error": str(exc)}

    def start_download(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            request_data = self._validate_download(payload)
            if not self.settings.get()["auth_token_set"]:
                raise ValueError(
                    "Set your MyFans Auth Token in Settings before downloading."
                )
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

        if not self._download_lock.acquire(blocking=False):
            return {"ok": False, "error": "A download is already in progress."}

        self.cancel_event.clear()
        self.download_state.reset_session()
        self.events.put("Initializing download process...")

        def worker() -> None:
            try:
                if request_data["post_type"] == "videos":
                    from myfans_downloader.ffmpeg import (
                        download_and_setup_ffmpeg,
                        ensure_ffmpeg_in_path,
                    )

                    if not download_and_setup_ffmpeg(progress_queue=self.events):
                        raise RuntimeError(
                            "FFmpeg setup failed. Check the application log and your network connection."
                        )
                    ensure_ffmpeg_in_path()

                app_settings = self.settings.get(include_token=True)
                downloader.start_download(
                    request_data["username"],
                    request_data["post_type"],
                    request_data["download_type"],
                    self.events,
                    self.download_state,
                    app_settings=app_settings,
                    cancel_event=self.cancel_event,
                    post_id=request_data["post_id"],
                    resolution=request_data["resolution"],
                )
            except Exception as exc:
                logger.exception("Download task failed")
                self.events.put(f"Error: {exc}")
            finally:
                self.events.put("DONE")
                self._download_lock.release()

        threading.Thread(target=worker, name="download-worker", daemon=True).start()
        return {"ok": True}

    def stop_download(self) -> dict[str, Any]:
        if not self._download_lock.locked():
            return {"ok": True, "running": False}
        self.cancel_event.set()
        self.events.put("Download cancellation requested by user...")
        return {"ok": True, "running": True}

    def prepare_shutdown(self) -> bool:
        if self._download_lock.locked():
            logger.info("Application is closing; cancelling the active download")
            self.cancel_event.set()
        return True

    def _validate_download(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise TypeError("Invalid download request.")

        post_type = str(payload.get("type", "videos"))
        download_type = str(payload.get("download_type", "all"))
        resolution = str(payload.get("resolution", "best"))
        if post_type not in self._POST_TYPES:
            raise ValueError("Invalid content type.")
        if download_type not in self._DOWNLOAD_TYPES:
            raise ValueError("Invalid download mode.")
        if resolution not in self._RESOLUTIONS:
            raise ValueError("Invalid video resolution.")

        username: str | None = None
        post_id: str | None = None
        if download_type == "single":
            post_id = str(payload.get("post_id", "")).strip()
            if not self._POST_ID_RE.fullmatch(post_id):
                raise ValueError("Post ID is empty or invalid.")
        else:
            username = str(payload.get("username", "")).strip().lstrip("@")
            if not self._USERNAME_RE.fullmatch(username):
                raise ValueError(
                    "Username is empty or contains unsupported characters."
                )

        return {
            "username": username,
            "post_id": post_id,
            "post_type": post_type,
            "download_type": download_type,
            "resolution": resolution,
        }
