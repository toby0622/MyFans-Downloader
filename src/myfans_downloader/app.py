"""Windows desktop entry point. No HTTP server is started by this application."""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import sys
import threading
from ctypes import wintypes
from typing import Any

from myfans_downloader.runtime_paths import initialize_runtime

APP_TITLE = "MyFans Downloader"
MUTEX_NAME = "Local\\MyFansDownloader.Desktop.Singleton"
logger = logging.getLogger(__name__)


class SingleInstance:
    """Windows named mutex preventing conflicting desktop instances."""

    ERROR_ALREADY_EXISTS = 183

    def __init__(self) -> None:
        self._handle: Any = None
        self._kernel32: Any = None

    def acquire(self) -> bool:
        if os.name != "nt":
            return True
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateMutexW.argtypes = (
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        )
        self._kernel32.CreateMutexW.restype = wintypes.HANDLE
        self._kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        self._kernel32.CloseHandle.restype = wintypes.BOOL
        self._handle = self._kernel32.CreateMutexW(None, False, MUTEX_NAME)
        return (
            bool(self._handle) and ctypes.get_last_error() != self.ERROR_ALREADY_EXISTS
        )

    def close(self) -> None:
        if self._handle and self._kernel32 is not None:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def _message_box(message: str, *, error: bool = False) -> None:
    if os.name == "nt":
        flags = 0x10 if error else 0x40
        ctypes.windll.user32.MessageBoxW(None, message, APP_TITLE, flags)
    else:
        print(message, file=sys.stderr if error else sys.stdout)


def _self_test() -> int:
    """Exercise bundled resources and backend initialization without opening a UI."""
    paths = initialize_runtime()
    import webview

    from myfans_downloader.desktop_api import DesktopApi
    from myfans_downloader.ffmpeg import (
        download_and_setup_ffmpeg,
        ensure_ffmpeg_in_path,
    )
    from myfans_downloader.logging_config import configure_logging
    from myfans_downloader.secure_storage import protect_secret, unprotect_secret
    from myfans_downloader.settings_store import SettingsStore

    configure_logging(paths.config_dir)
    settings = SettingsStore(paths)
    api = DesktopApi(paths, settings)
    bootstrap = api.bootstrap()
    required_assets = (
        paths.ui_dir / "index.html",
        paths.ui_dir / "app.js",
        paths.ui_dir / "css" / "style.css",
        paths.ui_dir / "favicon.svg",
    )
    if not all(asset.is_file() for asset in required_assets):
        raise RuntimeError("One or more packaged UI assets are missing")
    if not hasattr(webview, "create_window") or not hasattr(webview, "FOLDER_DIALOG"):
        raise RuntimeError("The desktop WebView runtime is incomplete")
    if not bootstrap["settings"] or not download_and_setup_ffmpeg():
        raise RuntimeError("The packaged backend did not initialize")
    ensure_ffmpeg_in_path()
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("Packaged FFmpeg executables are unavailable")
    protected_probe = protect_secret("myfans-self-test")
    if unprotect_secret(protected_probe) != "myfans-self-test":
        raise RuntimeError("Windows DPAPI token protection failed")

    probe = {"renderer": None, "loaded": False, "bridge": False}
    probe_done = threading.Event()
    window = webview.create_window(
        APP_TITLE,
        url=(paths.ui_dir / "index.html").resolve().as_uri(),
        js_api=api,
        hidden=True,
    )

    def on_initialized(renderer: str) -> bool:
        probe["renderer"] = renderer
        return renderer == "edgechromium"

    def on_loaded() -> None:
        probe["loaded"] = True

        def verify_bridge() -> None:
            try:
                for _ in range(40):
                    ready = window.evaluate_js(
                        "document.documentElement.dataset.applicationReady === 'true'"
                    )
                    if ready is True:
                        probe["bridge"] = True
                        break
                    probe_done.wait(0.1)
            finally:
                probe_done.set()
                window.destroy()

        threading.Thread(
            target=verify_bridge, name="webview-probe", daemon=True
        ).start()

    def watchdog() -> None:
        if not probe_done.wait(20):
            window.destroy()

    window.events.initialized += on_initialized
    window.events.loaded += on_loaded
    webview.start(
        watchdog,
        debug=False,
        http_server=False,
        private_mode=True,
        storage_path=str(paths.webview_storage_dir),
    )
    if (
        probe["renderer"] != "edgechromium"
        or not probe["loaded"]
        or not probe["bridge"]
    ):
        raise RuntimeError(
            "The Edge WebView2 application window failed its startup probe"
        )
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        try:
            return _self_test()
        except Exception:
            logger.exception("Packaged self-test failed")
            return 1

    instance = SingleInstance()
    if not instance.acquire():
        _message_box("MyFans Downloader is already running.")
        instance.close()
        return 0

    try:
        paths = initialize_runtime()

        from myfans_downloader.logging_config import configure_logging

        configure_logging(paths.config_dir)
        logger.info("Starting %s", APP_TITLE)

        import webview

        from myfans_downloader.desktop_api import DesktopApi
        from myfans_downloader.settings_store import SettingsStore

        settings = SettingsStore(paths)
        api = DesktopApi(paths, settings)
        index_path = (paths.ui_dir / "index.html").resolve()
        if not index_path.is_file():
            raise FileNotFoundError(f"Application UI is missing: {index_path}")

        window = webview.create_window(
            APP_TITLE,
            url=index_path.as_uri(),
            js_api=api,
            width=1280,
            height=820,
            min_size=(960, 640),
            background_color="#000000",
            text_select=True,
            confirm_close=True,
        )
        api._attach_window(window)
        renderer = {"name": None}

        def validate_renderer(name: str) -> bool:
            renderer["name"] = name
            if name != "edgechromium":
                logger.error("Unsupported WebView renderer selected: %s", name)
                return False
            return True

        window.events.initialized += validate_renderer
        window.events.closing += api.prepare_shutdown

        webview.start(
            debug=False,
            http_server=False,
            private_mode=True,
            storage_path=str(paths.webview_storage_dir),
        )
        if renderer["name"] != "edgechromium":
            raise RuntimeError(
                "Microsoft Edge WebView2 Runtime is required. Install or repair WebView2 and try again."
            )
        logger.info("Application stopped")
        return 0
    except Exception as exc:
        logging.getLogger(__name__).exception("Fatal application error")
        _message_box(f"MyFans Downloader could not start:\n\n{exc}", error=True)
        return 1
    finally:
        instance.close()


if __name__ == "__main__":
    raise SystemExit(main())
