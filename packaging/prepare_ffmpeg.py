"""Download a redistributable LGPL FFmpeg build for the standalone EXE."""

from __future__ import annotations

import hashlib
import shutil
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / ".build-tools"
FFMPEG_DIR = TOOLS_DIR / "ffmpeg"
BIN_DIR = FFMPEG_DIR / "bin"
ARCHIVE = TOOLS_DIR / "ffmpeg-lgpl.zip"
FFMPEG_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
    "ffmpeg-master-latest-win64-lgpl.zip"
)
CHECKSUMS_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/checksums.sha256"
)
ARCHIVE_NAME = "ffmpeg-master-latest-win64-lgpl.zip"
LICENSE_URL = "https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/COPYING.LGPLv2.1"


def safe_target(name: str) -> Path:
    target = (TOOLS_DIR / name).resolve()
    root = TOOLS_DIR.resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"Unsafe path in FFmpeg archive: {name}")
    return target


def main() -> None:
    ffmpeg = BIN_DIR / "ffmpeg.exe"
    ffprobe = BIN_DIR / "ffprobe.exe"
    license_file = FFMPEG_DIR / "COPYING.LGPLv2.1"
    if ffmpeg.is_file() and ffprobe.is_file() and license_file.is_file():
        return

    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(FFMPEG_URL, ARCHIVE)
    with urllib.request.urlopen(CHECKSUMS_URL) as response:
        checksums = response.read().decode("utf-8")
    expected = next(
        (
            line.split()[0]
            for line in checksums.splitlines()
            if line.strip().endswith(ARCHIVE_NAME)
        ),
        None,
    )
    if not expected:
        raise RuntimeError("FFmpeg checksum was not present in the release manifest.")
    digest = hashlib.sha256()
    with ARCHIVE.open("rb") as archive_file:
        for chunk in iter(lambda: archive_file.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest().lower() != expected.lower():
        raise RuntimeError("FFmpeg archive checksum verification failed.")
    with zipfile.ZipFile(ARCHIVE) as archive:
        members = archive.infolist()
        for member in members:
            safe_target(member.filename)
        archive.extractall(TOOLS_DIR)

    extracted_roots = sorted(
        path
        for path in TOOLS_DIR.iterdir()
        if path.is_dir() and path.name.startswith("ffmpeg-")
    )
    if not extracted_roots:
        raise RuntimeError("The FFmpeg archive had an unexpected layout.")
    extracted_bin = extracted_roots[-1] / "bin"
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(extracted_bin / "ffmpeg.exe", ffmpeg)
    shutil.copy2(extracted_bin / "ffprobe.exe", ffprobe)
    urllib.request.urlretrieve(LICENSE_URL, license_file)

    for extracted in extracted_roots:
        shutil.rmtree(extracted)
    ARCHIVE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
