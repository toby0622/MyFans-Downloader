import hashlib
import logging
import os
import platform
import shutil
import sys
import tarfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Detect platform and set appropriate download URL and binary names
_SYSTEM = platform.system().lower()

if _SYSTEM == "windows":
    FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-lgpl.zip"
    FFMPEG_BIN = "ffmpeg.exe"
    FFPROBE_BIN = "ffprobe.exe"
elif _SYSTEM == "linux":
    FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-lgpl.tar.xz"
    FFMPEG_BIN = "ffmpeg"
    FFPROBE_BIN = "ffprobe"
elif _SYSTEM == "darwin":
    # macOS — no static build from BtbN; users should install via Homebrew
    FFMPEG_URL = None
    FFMPEG_BIN = "ffmpeg"
    FFPROBE_BIN = "ffprobe"
else:
    FFMPEG_URL = None
    FFMPEG_BIN = "ffmpeg"
    FFPROBE_BIN = "ffprobe"

FFMPEG_CHECKSUMS_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/checksums.sha256"
    if FFMPEG_URL
    else None
)

FFMPEG_DIR = os.getenv(
    "MYFANS_FFMPEG_DIR",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        ".runtime",
        "ffmpeg",
    ),
)
BIN_DIR = os.path.join(FFMPEG_DIR, "bin")
BUNDLED_RESOURCE_DIR = os.getenv(
    "MYFANS_RESOURCE_DIR",
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
)
BUNDLED_BIN_DIR = os.path.join(BUNDLED_RESOURCE_DIR, "ffmpeg", "bin")


def _available_bin_dir():
    for candidate in dict.fromkeys((BIN_DIR, BUNDLED_BIN_DIR)):
        if os.path.exists(os.path.join(candidate, FFMPEG_BIN)) and os.path.exists(
            os.path.join(candidate, FFPROBE_BIN)
        ):
            return candidate
    return None


def reporthook(blocknum, blocksize, totalsize):
    if sys.stderr is None:
        return
    readsofar = blocknum * blocksize
    if totalsize > 0:
        percent = readsofar * 1e2 / totalsize
        s = (
            f"\rDownloading ffmpeg: {percent:5.1f}% "
            f"{readsofar:{len(str(totalsize))}d} / {totalsize:d} bytes"
        )
        sys.stderr.write(s)
        if readsofar >= totalsize:
            sys.stderr.write("\n")
    else:
        sys.stderr.write(f"\rDownloading ffmpeg: {readsofar} bytes")


def _is_ffmpeg_installed():
    """Check if ffmpeg binaries are already available (local or system PATH)."""
    if _available_bin_dir():
        return True
    # Also check system PATH
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _notify(message, progress_queue=None):
    logger.info(message)
    if progress_queue:
        progress_queue.put(message)
    elif sys.stderr is not None:
        print(message)


def _validated_archive_target(member_name):
    root = Path(FFMPEG_DIR).resolve()
    target = (root / member_name).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"Unsafe path in FFmpeg archive: {member_name}")


def _extract_archive(archive_path, is_tar):
    if is_tar:
        with tarfile.open(archive_path, "r:*") as archive:
            for member in archive.getmembers():
                _validated_archive_target(member.name)
                if member.issym() or member.islnk():
                    raise ValueError("FFmpeg archive contains unsupported links")
            archive.extractall(FFMPEG_DIR)
    else:
        with zipfile.ZipFile(archive_path, "r") as archive:
            for member in archive.infolist():
                _validated_archive_target(member.filename)
            archive.extractall(FFMPEG_DIR)


def _verify_download(archive_path):
    archive_name = os.path.basename(urlparse(FFMPEG_URL).path)
    with urllib.request.urlopen(FFMPEG_CHECKSUMS_URL, timeout=30) as response:
        manifest = response.read().decode("utf-8")
    expected = next(
        (
            line.split()[0]
            for line in manifest.splitlines()
            if line.strip().endswith(archive_name)
        ),
        None,
    )
    if not expected:
        raise RuntimeError("FFmpeg checksum is missing from the release manifest")
    digest = hashlib.sha256()
    with open(archive_path, "rb") as archive_file:
        for chunk in iter(lambda: archive_file.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest().lower() != expected.lower():
        raise RuntimeError("FFmpeg archive checksum verification failed")


def download_and_setup_ffmpeg(progress_queue=None):
    if _is_ffmpeg_installed():
        logger.info("ffmpeg is already installed.")
        return True

    if FFMPEG_URL is None:
        msg = (
            f"Automatic ffmpeg download is not supported on {platform.system()}. "
            "Please install ffmpeg manually (e.g. 'brew install ffmpeg' on macOS, "
            "'apt install ffmpeg' on Debian/Ubuntu)."
        )
        logger.warning(msg)
        if progress_queue:
            progress_queue.put(msg)
        return False

    _notify("FFmpeg not found. Downloading the video tools...", progress_queue)
    os.makedirs(FFMPEG_DIR, exist_ok=True)

    is_tar = FFMPEG_URL.endswith((".tar.xz", ".tar.gz"))
    archive_ext = (
        ".tar.xz"
        if FFMPEG_URL.endswith(".tar.xz")
        else ".tar.gz"
        if FFMPEG_URL.endswith(".tar.gz")
        else ".zip"
    )
    archive_path = os.path.join(FFMPEG_DIR, f"ffmpeg{archive_ext}")

    try:
        urllib.request.urlretrieve(FFMPEG_URL, archive_path, reporthook)
        _verify_download(archive_path)
        _notify("Extracting FFmpeg...", progress_queue)
        _extract_archive(archive_path, is_tar)

        # Find the extracted folder (contains "ffmpeg" in name, is a directory, not "bin")
        extracted_folder = None
        for item in os.listdir(FFMPEG_DIR):
            if (
                os.path.isdir(os.path.join(FFMPEG_DIR, item))
                and "ffmpeg" in item.lower()
                and item != "bin"
            ):
                extracted_folder = os.path.join(FFMPEG_DIR, item)
                break

        if extracted_folder:
            # Move bin folder up
            src_bin = os.path.join(extracted_folder, "bin")
            if os.path.exists(src_bin):
                if os.path.exists(BIN_DIR):
                    shutil.rmtree(BIN_DIR)
                shutil.move(src_bin, BIN_DIR)

            # Clean up extracted folder
            shutil.rmtree(extracted_folder)

        if os.path.exists(archive_path):
            os.remove(archive_path)

        # On Linux/macOS, ensure binaries are executable
        if _SYSTEM != "windows":
            for binary in [FFMPEG_BIN, FFPROBE_BIN]:
                bin_path = os.path.join(BIN_DIR, binary)
                if os.path.exists(bin_path):
                    os.chmod(bin_path, 0o755)

        _notify("FFmpeg setup completed successfully.", progress_queue)
        return True
    except (
        OSError,
        RuntimeError,
        ValueError,
        tarfile.TarError,
        urllib.error.URLError,
        zipfile.BadZipFile,
    ) as e:
        logger.error(f"Failed to setup ffmpeg: {e}")
        if progress_queue:
            progress_queue.put(f"Failed to setup FFmpeg: {e}")
        return False


def ensure_ffmpeg_in_path():
    selected_bin_dir = _available_bin_dir()
    if selected_bin_dir and selected_bin_dir not in os.environ.get("PATH", ""):
        current_path = os.environ.get("PATH", "")
        os.environ["PATH"] = selected_bin_dir + (
            os.pathsep + current_path if current_path else ""
        )
