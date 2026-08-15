"""Stable resource and user-data paths for source and frozen builds."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

APP_DIR_NAME = "MyFansDownloader"


@dataclass(frozen=True)
class RuntimePaths:
    resource_dir: Path
    ui_dir: Path
    data_dir: Path
    config_dir: Path
    config_file: Path
    downloads_dir: Path
    ffmpeg_dir: Path
    log_file: Path
    webview_storage_dir: Path
    frozen: bool


def _expanded_path(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value))).resolve()


def get_runtime_paths() -> RuntimePaths:
    """Return paths that never point at PyInstaller's temporary extraction dir."""
    frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        resource_dir = Path(sys._MEIPASS).resolve()
        project_dir = resource_dir
    else:
        project_dir = Path(__file__).resolve().parents[2]
        resource_dir = project_dir
    ui_dir = (
        resource_dir / "myfans_downloader" / "ui"
        if frozen
        else Path(__file__).resolve().parent / "ui"
    )

    data_override = os.getenv("MYFANS_DATA_DIR")
    if data_override:
        config_dir = _expanded_path(data_override)
        data_dir = config_dir
    elif frozen:
        local_app_data = os.getenv("LOCALAPPDATA")
        base = (
            Path(local_app_data)
            if local_app_data
            else Path.home() / "AppData" / "Local"
        )
        data_dir = (base / APP_DIR_NAME).resolve()
        config_dir = data_dir
    else:
        data_dir = (project_dir / ".runtime").resolve()
        config_dir = data_dir

    downloads_override = os.getenv("MYFANS_DOWNLOADS_DIR")
    if downloads_override:
        downloads_dir = _expanded_path(downloads_override)
    elif frozen:
        downloads_dir = (Path.home() / "Downloads" / APP_DIR_NAME).resolve()
    else:
        downloads_dir = (project_dir / "downloads").resolve()

    ffmpeg_override = os.getenv("MYFANS_FFMPEG_DIR")
    if ffmpeg_override:
        ffmpeg_dir = _expanded_path(ffmpeg_override)
    else:
        ffmpeg_dir = (data_dir / "ffmpeg").resolve()

    return RuntimePaths(
        resource_dir=resource_dir,
        ui_dir=ui_dir.resolve(),
        data_dir=data_dir,
        config_dir=config_dir,
        config_file=config_dir / "config.ini",
        downloads_dir=downloads_dir,
        ffmpeg_dir=ffmpeg_dir,
        log_file=config_dir / "myfans_downloader.log",
        webview_storage_dir=data_dir / "webview",
        frozen=frozen,
    )


def initialize_runtime() -> RuntimePaths:
    """Create writable directories and publish runtime tool paths."""
    paths = get_runtime_paths()
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    paths.downloads_dir.mkdir(parents=True, exist_ok=True)
    paths.webview_storage_dir.mkdir(parents=True, exist_ok=True)

    os.environ["MYFANS_DATA_DIR"] = str(paths.data_dir)
    os.environ["MYFANS_RESOURCE_DIR"] = str(paths.resource_dir)
    os.environ["MYFANS_FFMPEG_DIR"] = str(paths.ffmpeg_dir)
    return paths
