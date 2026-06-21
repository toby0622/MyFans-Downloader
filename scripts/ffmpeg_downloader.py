import os
import sys
import urllib.request
import zipfile
import shutil
import logging

logger = logging.getLogger(__name__)

FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
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

def download_and_setup_ffmpeg():
    if os.path.exists(os.path.join(BIN_DIR, "ffmpeg.exe")) and os.path.exists(os.path.join(BIN_DIR, "ffprobe.exe")):
        logger.info("ffmpeg is already installed.")
        return True

    logger.info(f"ffmpeg not found in {BIN_DIR}. Downloading from github...")
    print(f"ffmpeg not found. Downloading from github...")
    os.makedirs(FFMPEG_DIR, exist_ok=True)
    zip_path = os.path.join(FFMPEG_DIR, "ffmpeg.zip")

    try:
        urllib.request.urlretrieve(FFMPEG_URL, zip_path, reporthook)
        logger.info("Extracting ffmpeg...")
        print("Extracting ffmpeg...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(FFMPEG_DIR)
        
        # Find the extracted folder
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
            
            # Clean up extracted folder and zip
            shutil.rmtree(extracted_folder)
            
        if os.path.exists(zip_path):
            os.remove(zip_path)
            
        logger.info("ffmpeg setup completed successfully.")
        print("ffmpeg setup completed successfully.")
        return True
    except Exception as e:
        logger.error(f"Failed to setup ffmpeg: {e}")
        print(f"Failed to setup ffmpeg: {e}")
        return False

def ensure_ffmpeg_in_path():
    if BIN_DIR not in os.environ["PATH"]:
        os.environ["PATH"] += os.pathsep + BIN_DIR
