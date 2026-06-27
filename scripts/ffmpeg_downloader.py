import os
import sys
import urllib.request
import zipfile
import tarfile
import shutil
import logging
import platform

logger = logging.getLogger(__name__)

# Detect platform and set appropriate download URL and binary names
_SYSTEM = platform.system().lower()

if _SYSTEM == "windows":
    FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
    FFMPEG_BIN = "ffmpeg.exe"
    FFPROBE_BIN = "ffprobe.exe"
elif _SYSTEM == "linux":
    FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"
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

FFMPEG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ffmpeg")
BIN_DIR = os.path.join(FFMPEG_DIR, "bin")

def reporthook(blocknum, blocksize, totalsize):
    readsofar = blocknum * blocksize
    if totalsize > 0:
        percent = readsofar * 1e2 / totalsize
        s = "\rDownloading ffmpeg: %5.1f%% %*d / %d bytes" % (
            percent, len(str(totalsize)), readsofar, totalsize)
        sys.stderr.write(s)
        if readsofar >= totalsize:
            sys.stderr.write("\n")
    else:
        sys.stderr.write(f"\rDownloading ffmpeg: {readsofar} bytes")

def _is_ffmpeg_installed():
    """Check if ffmpeg binaries are already available (local or system PATH)."""
    local_ffmpeg = os.path.join(BIN_DIR, FFMPEG_BIN)
    local_ffprobe = os.path.join(BIN_DIR, FFPROBE_BIN)
    if os.path.exists(local_ffmpeg) and os.path.exists(local_ffprobe):
        return True
    # Also check system PATH
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

def download_and_setup_ffmpeg():
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
        print(msg)
        return False

    logger.info(f"ffmpeg not found in {BIN_DIR}. Downloading from github...")
    print(f"ffmpeg not found. Downloading from github...")
    os.makedirs(FFMPEG_DIR, exist_ok=True)

    is_tar = FFMPEG_URL.endswith(('.tar.xz', '.tar.gz'))
    archive_ext = ".tar.xz" if FFMPEG_URL.endswith('.tar.xz') else ".tar.gz" if FFMPEG_URL.endswith('.tar.gz') else ".zip"
    archive_path = os.path.join(FFMPEG_DIR, f"ffmpeg{archive_ext}")

    try:
        urllib.request.urlretrieve(FFMPEG_URL, archive_path, reporthook)
        logger.info("Extracting ffmpeg...")
        print("Extracting ffmpeg...")

        if is_tar:
            with tarfile.open(archive_path, 'r:*') as tar:
                tar.extractall(FFMPEG_DIR)
        else:
            with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                zip_ref.extractall(FFMPEG_DIR)
        
        # Find the extracted folder (contains "ffmpeg" in name, is a directory, not "bin")
        extracted_folder = None
        for item in os.listdir(FFMPEG_DIR):
            if os.path.isdir(os.path.join(FFMPEG_DIR, item)) and "ffmpeg" in item.lower() and item != "bin":
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

        logger.info("ffmpeg setup completed successfully.")
        print("ffmpeg setup completed successfully.")
        return True
    except Exception as e:
        logger.error(f"Failed to setup ffmpeg: {e}")
        print(f"Failed to setup ffmpeg: {e}")
        return False

def ensure_ffmpeg_in_path():
    if os.path.isdir(BIN_DIR) and BIN_DIR not in os.environ.get("PATH", ""):
        os.environ["PATH"] += os.pathsep + BIN_DIR
