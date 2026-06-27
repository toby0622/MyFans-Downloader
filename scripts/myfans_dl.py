import datetime
import os
import pathlib
import shutil
import time
import json
import uuid
from queue import Queue, Empty
import subprocess
import configparser
from tqdm import tqdm
import concurrent.futures
import threading
import m3u8
import logging
from logging.handlers import RotatingFileHandler
from typing import Optional, List, Dict, Any, Tuple
from urllib.parse import urljoin
import requests
from requests import Session
import re

# Log file path: use config/ directory to match app.py
log_dir = os.getenv('CONFIG_DIR', 'config')
os.makedirs(log_dir, exist_ok=True)
log_file = os.getenv('LOG_FILE', os.path.join(log_dir, 'myfans_downloader.log'))

# Create logger
logger = logging.getLogger('myfans_downloader')
logger.setLevel(logging.INFO)

# Create handlers
console_handler = logging.StreamHandler()
file_handler = RotatingFileHandler(log_file, maxBytes=10485760, backupCount=5)  # 10MB per file

# Create formatters
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
file_handler.setFormatter(formatter)

# Add handlers to logger
logger.addHandler(console_handler)
logger.addHandler(file_handler)

# Prevent log propagation to avoid duplicate logs
logger.propagate = False


def log_and_notify(level, message, progress_queue=None):
    """Log a message and optionally push it to the progress queue for the UI."""
    getattr(logger, level)(message)
    if progress_queue:
        progress_queue.put(message)

def get_headers():
    config_file_path = os.path.join(os.getenv('CONFIG_DIR', 'config'), 'config.ini')
    config = configparser.ConfigParser()
    if os.path.isfile(config_file_path):
        config.read(config_file_path)
        
    auth_token = os.getenv('AUTH_TOKEN', config.get('Settings', 'auth_token', fallback=''))
    
    if not auth_token.strip():
        raise ValueError("Missing authorization token in configuration. Please save your Auth Token in Settings.")
        
    return {
        'authorization': f"Token token={auth_token}",
        'google-ga-data': 'event328',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'
    }

def get_posts_for_page(base_url, page, headers):
    url = base_url + str(page)
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    json_data = response.json()
    return json_data.get("data", [])

def verify_video_file(file_path: str) -> bool:
    """Verify if a video file is valid"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", file_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        return result.returncode == 0
    except Exception as e:
        logger.error(f"Error verifying video file {file_path}: {e}")
        return False

def safe_urljoin(base: str, url: str) -> str:
    """Safely join URL parts ensuring no None values"""
    if not base or not url:
        raise ValueError("Base URL and URL parts must not be None")
    return urljoin(base, url)

def make_request(session: requests.Session, url: str, headers: dict, timeout: int = 30) -> requests.Response:
    """Make a request ensuring proper type safety"""
    if not url:
        raise ValueError("URL cannot be None")
    return session.get(url, headers=headers, timeout=timeout)

def DL_File(m3u8_url_download, output_file, input_post_id, chunk_size=1024*1024, max_retries=3, retry_delay=5, progress_queue=None, download_state=None, cancel_event=None):
    try:
        # Get segment download threads from environment or use default
        segment_threads = int(os.getenv('SEGMENT_DOWNLOAD_THREADS', '15'))
        logger.info(f"Using {segment_threads} threads for segment downloads")
        
        # Add M3U8 URL validation
        if not m3u8_url_download:
            logger.error(f"Invalid M3U8 URL for post {input_post_id}")
            return False

        # Check if file already exists and is complete
        if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            if verify_video_file(output_file):
                message = f"Verified existing file: {os.path.basename(output_file)}"
                logger.info(message)
                if progress_queue:
                    progress_queue.put(message)
                if download_state:
                    download_state.mark_completed(input_post_id)
                return True
            else:
                message = f"Corrupted file found, redownloading: {os.path.basename(output_file)}"
                logger.warning(message)
                if progress_queue:
                    progress_queue.put(message)
                os.remove(output_file)

        # Setup directories
        output_folder = os.path.dirname(output_file)
        random_name = str(uuid.uuid4())
        ts_file =  os.path.join(output_folder, random_name + '.ts')
        temp_folder = os.path.join(output_folder, random_name + '.ts_parts')

        os.makedirs(output_folder, exist_ok=True)
        os.makedirs(temp_folder, exist_ok=True)

        # Setup session with headers
        headers = get_headers()
        session = requests.Session()
        session.headers.update(headers)
        
        # Use connection pooling for better performance
        adapter = requests.adapters.HTTPAdapter(pool_connections=segment_threads, 
                                               pool_maxsize=segment_threads,
                                               max_retries=max_retries)
        session.mount('http://', adapter)
        session.mount('https://', adapter)

        for attempt in range(max_retries):
            try:
                # Get master playlist
                logger.info(f"Fetching master M3U8 from URL: {m3u8_url_download}")
                response = session.get(m3u8_url_download, timeout=30)
                response.raise_for_status()
                master_content = response.text

                # Parse master playlist
                master_playlist = m3u8.loads(master_content)
                master_playlist.base_uri = os.path.dirname(m3u8_url_download) + '/'

                if not master_playlist.playlists:
                    logger.error(f"No variants found in master playlist")
                    continue

                # Get highest quality variant
                variant = sorted(
                    [p for p in master_playlist.playlists if p.stream_info and p.stream_info.bandwidth],
                    key=lambda x: x.stream_info.bandwidth,
                    reverse=True
                )[0]

                # Get variant playlist URL
                base_uri = os.path.dirname(m3u8_url_download)
                if not base_uri:
                    base_uri = m3u8_url_download
                variant_url = safe_urljoin(base_uri + '/', variant.uri if variant.uri else '')
                logger.info(f"Fetching variant playlist from: {variant_url}")

                # Get variant playlist
                response = session.get(variant_url, timeout=30)
                response.raise_for_status()
                variant_content = response.text

                # Parse variant playlist
                playlist = m3u8.loads(variant_content)
                playlist.base_uri = os.path.dirname(variant_url) + '/'

                if not playlist.segments:
                    logger.error(f"No segments found in variant playlist")
                    continue

                total_segments = len(playlist.segments)
                logger.info(f"Found {total_segments} segments for post {input_post_id}")
                
                if progress_queue:
                    progress_queue.put(f"Downloading {total_segments} segments with {segment_threads} parallel threads")

                if download_state:
                    download_state.add_download(input_post_id, segments_total=total_segments)

                # Download segments concurrently
                segment_files = [None] * total_segments  # Pre-allocate list with correct order
                processed_count = 0
                
                def download_segment(i, segment):
                    if cancel_event and cancel_event.is_set():
                        return i, None
                    nonlocal processed_count
                    
                    if not segment.uri:
                        logger.error(f"Invalid segment {i}: missing URI")
                        return i, None
                        
                    seg_path = os.path.join(temp_folder, f"segment_{i:05d}.ts")
                    
                    # Skip if segment already exists
                    if os.path.exists(seg_path) and os.path.getsize(seg_path) > 0:
                        return i, seg_path
                        
                    # Try to download segment with retries
                    for seg_retry in range(3):
                        try:
                            seg_url = safe_urljoin(playlist.base_uri, segment.uri) if not segment_uri_is_absolute(segment.uri) else segment.uri
                            response = session.get(seg_url, timeout=30)
                            response.raise_for_status()

                            with open(seg_path, 'wb') as f:
                                f.write(response.content)

                            if os.path.exists(seg_path) and os.path.getsize(seg_path) > 0:
                                return i, seg_path
                        except Exception as e:
                            logger.error(f"Error downloading segment {i}: {str(e)}")
                            if seg_retry == 2:  # Last attempt
                                return i, None
                            time.sleep(retry_delay)
                    
                    return i, None

                # Use ThreadPoolExecutor for concurrent downloads
                with tqdm(total=total_segments, desc=f"Segments for {input_post_id}") as pbar:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=segment_threads) as executor:
                        futures = {executor.submit(download_segment, i, segment): i 
                                  for i, segment in enumerate(playlist.segments)}
                        
                        for future in concurrent.futures.as_completed(futures):
                            if cancel_event and cancel_event.is_set():
                                logger.info(f"Cancellation requested during post {input_post_id} segments download")
                                # Try to cancel remaining futures
                                for f in futures.keys():
                                    f.cancel()
                                raise Exception("Cancelled by user")
                                
                            try:
                                idx, file_path = future.result()
                                if file_path:
                                    segment_files[idx] = file_path
                                processed_count += 1
                                pbar.update(1)
                                if download_state:
                                    download_state.update_progress(input_post_id, processed_count)
                                
                                # Log progress occasionally
                                if processed_count % 50 == 0 or processed_count == total_segments:
                                    success_rate = len([f for f in segment_files if f]) / processed_count * 100
                                    log_and_notify('info', f"Progress: {processed_count}/{total_segments} segments ({success_rate:.1f}% success)", progress_queue)
                            except Exception as e:
                                logger.error(f"Error processing segment result: {str(e)}")
                
                # Filter out None values (failed downloads)
                valid_segments = [f for f in segment_files if f]
                success_rate = len(valid_segments) / total_segments * 100
                
                logger.info(f"Downloaded {len(valid_segments)}/{total_segments} segments ({success_rate:.1f}% success)")
                if progress_queue:
                    progress_queue.put(f"Downloaded {len(valid_segments)}/{total_segments} segments ({success_rate:.1f}% success)")

                if len(valid_segments) < total_segments * 0.9:  # Less than 90% segments downloaded
                    logger.error(f"Too many failed segments: only {success_rate:.1f}% downloaded successfully")
                    if attempt < max_retries - 1:  # Not the last attempt
                        logger.info(f"Retrying download, attempt {attempt + 2}/{max_retries}")
                        continue

                # Merge segments
                logger.info("Merging segments...")
                if progress_queue:
                    progress_queue.put("Merging segments...")
                
                with open(ts_file, 'wb') as outfile:
                    for seg_file in valid_segments:
                        if os.path.exists(seg_file):
                            with open(seg_file, 'rb') as infile:
                                outfile.write(infile.read())

                # Convert to MP4
                logger.info("Converting to MP4...")
                if progress_queue:
                    progress_queue.put("Converting to MP4...")
                
                result = subprocess.run(
                    ["ffmpeg", "-y", "-i", ts_file, "-c", "copy", output_file],
                    capture_output=True,
                    text=True
                )

                if result.returncode != 0:
                    logger.error(f"FFmpeg error: {result.stderr}")
                    continue

                # Verify final file
                if verify_video_file(output_file):
                    # Cleanup
                    try:
                        if os.path.exists(ts_file):
                            os.remove(ts_file)
                        
                        # Delete segments
                        for seg_file in valid_segments:
                            if os.path.exists(seg_file):
                                os.remove(seg_file)
                                
                        # Remove temp directory
                        if os.path.exists(temp_folder):
                            os.rmdir(temp_folder)
                    except Exception as e:
                        logger.warning(f"Error during cleanup: {str(e)}")
                    
                    logger.info(f"Successfully downloaded {input_post_id}")
                    if progress_queue:
                        progress_queue.put(f"Successfully downloaded {input_post_id}")
                    
                    if download_state:
                        download_state.mark_completed(input_post_id)
                    return True

            except Exception as e:
                logger.error(f"Download attempt {attempt + 1} failed: {str(e)}")
                if progress_queue:
                    progress_queue.put(f"Download attempt {attempt + 1} failed: {str(e)}")
                
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)

        return False

    except Exception as e:
        logger.exception(f"Fatal error in DL_File: {str(e)}")
        if progress_queue:
            progress_queue.put(f"Fatal error: {str(e)}")
        return False

def segment_uri_is_absolute(uri: str) -> bool:
    return uri.lower().startswith(("http://", "https://"))

def process_post_id(input_post_id, session, headers, selected_resolution, output_dir, filename_config, progress_bar=None, progress_queue=None, download_state=None, cancel_event=None):
    try:
        if cancel_event and cancel_event.is_set():
            return False
            
        # Use the passed session instead of creating new ones
        data, resolution_info, error = get_video_info(input_post_id, session, headers)
        
        if error:
            message = f"Error fetching video info for post ID {input_post_id}: {error}"
            logger.error(message)
            if progress_queue:
                progress_queue.put(message)
            return False

        # Log available resolutions
        if resolution_info:
            logger.info(f"Available resolutions for post {input_post_id}: {list(resolution_info.keys())}")
        else:
            logger.error(f"No resolution info available for post {input_post_id}")
            return False

        # Check if it's a video post
        if not data.get('videos', {}).get('main'):
            message = f"Post ID {input_post_id} is not a video post"
            logger.error(message)
            if progress_queue:
                progress_queue.put(message)
            return False

        # Select resolution with fallback logging
        if selected_resolution == 'best':
            for res in ['uhd', 'fhd', 'hd', 'sd', 'ld']:
                if res in resolution_info:
                    selected_resolution = res
                    logger.info(f"Selected best available resolution for post {input_post_id}: {res}")
                    break

        # Verify selected resolution exists
        if selected_resolution not in resolution_info:
            available = ', '.join(resolution_info.keys())
            message = f"Resolution {selected_resolution} not available for post {input_post_id}. Available: {available}"
            logger.warning(message)
            if progress_queue:
                progress_queue.put(message)
            # Try fallback
            for res in ['uhd', 'fhd', 'hd', 'sd', 'ld']:
                if res in resolution_info:
                    selected_resolution = res
                    message = f"Falling back to {res} resolution"
                    logger.info(message)
                    if progress_queue:
                        progress_queue.put(message)
                    break
            else:
                logger.error(f"No valid resolution found for post {input_post_id}")
                return False

        # Get video URL
        video_url = resolution_info[selected_resolution].get("url")
        if not video_url:
            logger.error(f"No video URL found for post {input_post_id}")
            return False

        # Log video URL (masked for security)
        masked_url = video_url[:30] + "..." + video_url[-30:] if len(video_url) > 60 else video_url
        logger.info(f"Video URL for post {input_post_id}: {masked_url}")

        # Check access level with detailed logging
        logger.info(f"Post {input_post_id} - Free: {data.get('free')}, Subscribed: {data.get('subscribed')}")
        if data.get('free') is False and not data.get('subscribed'):
            message = f"No access to post ID {input_post_id} (subscription required)"
            logger.error(message)
            if progress_queue:
                progress_queue.put(message)
            return False

        # Validate URL before attempting download
        if not validate_video_url(video_url, headers, session=session):
            logger.error(f"Video URL validation failed for post {input_post_id}")
            return False

        # Setup output path
        output_folder = str(os.path.join(output_dir, data['user']['username'], "videos"))
        filename = None
        full_path = None
        for max_length in list(range(100, 10, -10)): # start at 100, decrease by 10.
            try:
                filename = generate_filename(data, filename_config, output_dir, max_length=max_length)
                full_path = os.path.join(output_folder, filename)
                path = pathlib.Path(str(full_path))
                # if it already exists we can exit out
                if path.exists():
                    break
                path.with_suffix('longextension') # to ensure metadata works (e.g. .webp.json)
                # verify the path works by creating and deleting the file.
                path.touch()
                if path.exists():
                    path.unlink()
                    break
            except Exception as e:
                logger.debug(f"Invalid path with length {max_length} ({str(e)}), reducing...")

        # Check existing file
        if os.path.exists(full_path) and os.path.getsize(full_path) > 0:
            if verify_video_file(full_path):
                generate_metadata(data, filename, output_folder)
                update_file_date(data, full_path)
                message = f"File already exists and verified: {filename}"
                logger.info(message)
                if progress_queue:
                    progress_queue.put(message)
                if progress_bar:
                    progress_bar.update(1)
                return True
            else:
                message = f"Corrupted file found, will redownload: {filename}"
                logger.warning(message)
                if progress_queue:
                    progress_queue.put(message)
                os.remove(full_path)

        # Create output directory
        os.makedirs(output_folder, exist_ok=True)

        # Start download
        message = f"Starting download of video {input_post_id}"
        logger.info(message)
        if progress_queue:
            progress_queue.put(message)

        success = DL_File(
            video_url,
            full_path,
            input_post_id,
            progress_queue=progress_queue,
            download_state=download_state,
            cancel_event=cancel_event
        )

        if success:
            generate_metadata(data, filename, output_folder)
            update_file_date(data, full_path)
            message = f"Successfully downloaded video: {filename}"
            logger.info(message)
        else:
            message = f"Failed to download video for post ID {input_post_id}"
            logger.error(message)
        
        if progress_queue:
            progress_queue.put(message)
        if progress_bar:
            progress_bar.update(1)
        
        return success

    except Exception as e:
        error = f"Error processing post {input_post_id}: {str(e)}"
        logger.error(error)
        if progress_queue:
            progress_queue.put(error)
        if progress_bar:
            progress_bar.update(1)
        return False

def download_videos_concurrently(session, post_ids, selected_resolution, output_dir, filename_config, progress_queue=None, download_state=None, cancel_event=None):
    max_workers = 1  # Forced sequential
    
    headers = get_headers()
    total_posts = len(post_ids)
    message = f"Starting download of {total_posts} posts strictly one at a time..."
    logger.info(message)
    if progress_queue:
        progress_queue.put(message)
    
    progress_bar = tqdm(total=total_posts, desc="Downloading videos", unit="video")

    def process_post(post_id):
        return process_post_id(
            post_id, session, headers, selected_resolution, 
            output_dir, filename_config, progress_bar, progress_queue, 
            download_state, cancel_event
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for post_id in post_ids:
            if cancel_event and cancel_event.is_set():
                break
            future = executor.submit(process_post, post_id)
            futures[future] = post_id
        
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception as e:
                logger.error(f"Error in thread: {e}")

    progress_bar.close()
    if progress_queue:
        progress_queue.put("Download process completed")

def download_single_file(session, post_id, selected_resolution, output_dir, filename_config):
    headers = get_headers()
    try:
        response = session.get(f"https://api.myfans.jp/api/v2/posts/{post_id}", headers=headers)
        response.raise_for_status()
        process_post_id(post_id, session, headers, selected_resolution, output_dir, filename_config)
    except requests.RequestException as e:
        print(f"API request failed: {e}")

def check_disk_space(path, required_bytes):
    """Check if there's enough disk space available"""
    try:
        total, used, free = shutil.disk_usage(path)
        return free >= required_bytes
    except Exception as e:
        logger.error(f"Failed to check disk space: {e}")
        return False

def start_download(username, post_type, download_type, progress_queue, download_state=None, post_id=None, resolution='best', cancel_event=None):
    """Handle downloads initiated from the web interface"""
    try:
        if post_id:
            # Single post download
            message = f"Starting download for post ID: {post_id}"
            logger.info(message)
            progress_queue.put(message)
            
            session = requests.Session()
            headers = get_headers()
            config_file_path = os.path.join(os.getenv('CONFIG_DIR', 'config'), 'config.ini')
            
            config = configparser.ConfigParser()
            config.read(config_file_path)
            
            output_dir = os.getenv('DOWNLOADS_DIR', config.get('Settings', 'output_dir'))
            filename_config = read_filename_config(config)
            
            if post_type == 'videos':
                download_single_file(session, post_id, resolution, output_dir, filename_config)
            else:  # images
                handle_image_download(post_id, session, headers, output_dir, filename_config, progress_queue)
            progress_queue.put("DONE")
            return

        message = f"Starting download for user: {username}, type: {post_type}, mode: {download_type}"
        logger.info(message)
        progress_queue.put(message)
        
        session = requests.Session()
        headers = get_headers()  # Cache headers once for the entire download session
        config_file_path = os.path.join(os.getenv('CONFIG_DIR', 'config'), 'config.ini')
        
        if not os.path.isfile(config_file_path):
            error = "Error: Configuration missing! Please go to the SETTINGS page and click 'SAVE CONFIGURATION' first."
            logger.error(error)
            progress_queue.put(error)
            return
            
        config = configparser.ConfigParser()
        config.read(config_file_path)
        
        # Get configuration
        output_dir = os.getenv('DOWNLOADS_DIR', config.get('Settings', 'output_dir'))
        filename_config = read_filename_config(config)

        user_info_url = f"https://api.myfans.jp/api/v2/users/show_by_username?username={username}"
        message = f"Fetching user info from: {user_info_url}"
        logger.info(message)
        progress_queue.put(message)

        response = session.get(user_info_url, headers=headers)
        response.raise_for_status()
        user_data = response.json()

        message = f"Successfully retrieved user data for: {username}"
        logger.info(message)
        progress_queue.put(message)

        # Fetch posts
        back_number_plan = user_data.get('current_back_number_plan')
        user_id = user_data.get('id')

        if not user_id:
            error = "Failed to retrieve user ID. Please check the username and try again."
            logger.error(error)
            progress_queue.put(error)
            return

        message = f"Found user ID: {user_id}"
        logger.info(message)
        progress_queue.put(message)

        # Process downloads based on type
        if post_type == 'videos':
            if download_state:
                download_state.add_download('FETCHING', status='fetching', segments_total=0)
            # Fetch regular posts
            base_url = f"https://api.myfans.jp/api/v2/users/{user_id}/posts?page="
            progress_queue.put("Fetching regular posts...")
            video_posts = []
            page = 1
            
            while True:
                if cancel_event and cancel_event.is_set():
                    logger.info("Cancellation requested during regular posts fetch")
                    if download_state:
                        download_state.mark_completed('FETCHING')
                    return
                    
                try:
                    message = f"Fetching page {page} of regular posts..."
                    logger.info(message)
                    progress_queue.put(message)
                    
                    if download_state:
                        download_state.update_progress('FETCHING', page)
                    
                    response = session.get(base_url + str(page), headers=headers)
                    response.raise_for_status()
                    json_data = response.json()
                    
                    if not json_data.get("data") or len(json_data["data"]) == 0:
                        message = "No more regular posts found"
                        logger.info(message)
                        progress_queue.put(message)
                        break
                        
                    current_page_videos = [post for post in json_data["data"] if post.get("kind") == "video"]
                    video_posts.extend(current_page_videos)
                    
                    message = f"Found {len(current_page_videos)} videos on page {page}"
                    logger.info(message)
                    progress_queue.put(message)
                    
                    page += 1
                    
                except requests.RequestException as e:
                    error = f"Error fetching page {page}: {e}"
                    logger.error(error)
                    progress_queue.put(error)
                    break

            # Fetch back number plan posts if available
            if back_number_plan:
                message = "Starting to fetch back number plan posts..."
                logger.info(message)
                progress_queue.put(message)
                
                back_plan_url = f"https://api.myfans.jp/api/v2/users/{user_id}/back_number_posts?page="
                page = 1
                
                while True:
                    if cancel_event and cancel_event.is_set():
                        logger.info("Cancellation requested during back plan posts fetch")
                        if download_state:
                            download_state.mark_completed('FETCHING')
                        return
                        
                    try:
                        message = f"Fetching back plan page {page}..."
                        logger.info(message)
                        progress_queue.put(message)
                        
                        if download_state:
                            download_state.update_progress('FETCHING', page)
                            
                        response = session.get(back_plan_url + str(page), headers=headers)
                        response.raise_for_status()
                        json_data = response.json()
                        
                        if not json_data.get("data") or len(json_data["data"]) == 0:
                            message = "No more back plan posts found"
                            logger.info(message)
                            progress_queue.put(message)
                            break
                            
                        current_page_videos = [post for post in json_data["data"] if post.get("kind") == "video"]
                        video_posts.extend(current_page_videos)
                        
                        message = f"Found {len(current_page_videos)} back plan videos on page {page}"
                        logger.info(message)
                        progress_queue.put(message)
                        
                        page += 1
                        
                    except requests.RequestException as e:
                        error = f"Error fetching back plan page {page}: {e}"
                        logger.error(error)
                        progress_queue.put(error)
                        break

            message = f"Total video posts found: {len(video_posts)}"
            logger.info(message)
            progress_queue.put(message)
            
            if download_state:
                download_state.mark_completed('FETCHING')

            # Filter posts based on download_type
            if download_type == 'free':
                filtered_posts = [post for post in video_posts if post.get("free")]
            elif download_type == 'subscribed':
                filtered_posts = [post for post in video_posts if not post.get("free")]
            else:
                filtered_posts = video_posts

            # Check which files already exist
            existing_files, missing_files = check_existing_files(filtered_posts, output_dir, filename_config)

            message = f"Found {len(existing_files)} existing files, {len(missing_files)} files to download"
            logger.info(message)
            progress_queue.put(message)

            if missing_files:
                message = f"Starting download of {len(missing_files)} missing files..."
                logger.info(message)
                progress_queue.put(message)
                download_videos_concurrently(session, missing_files, resolution, output_dir, filename_config, progress_queue, download_state, cancel_event=cancel_event)
            else:
                message = "All files already downloaded!"
                logger.info(message)
                progress_queue.put(message)

            progress_queue.put("DONE")

        elif post_type == 'images':
            if download_state:
                download_state.add_download('FETCHING', status='fetching', segments_total=0)
            base_url = f"https://api.myfans.jp/api/v2/users/{user_id}/posts?page="
            progress_queue.put("Fetching image posts...")
            image_posts = []
            page = 1
            
            while True:
                if cancel_event and cancel_event.is_set():
                    logger.info("Cancellation requested during image posts fetch")
                    if download_state:
                        download_state.mark_completed('FETCHING')
                    return
                    
                try:
                    message = f"Fetching page {page} of image posts..."
                    logger.info(message)
                    progress_queue.put(message)
                    
                    if download_state:
                        download_state.update_progress('FETCHING', page)
                    
                    response = session.get(base_url + str(page), headers=headers)
                    response.raise_for_status()
                    json_data = response.json()
                    
                    if not json_data.get("data"):
                        break
                        
                    current_page_images = [post for post in json_data["data"] if post.get("kind") == "image"]
                    image_posts.extend(current_page_images)
                    
                    message = f"Found {len(current_page_images)} images on page {page}"
                    logger.info(message)
                    progress_queue.put(message)
                    
                    page += 1
                    
                except requests.RequestException as e:
                    error = f"Error fetching page {page}: {e}"
                    logger.error(error)
                    progress_queue.put(error)
                    break

            # Filter posts based on download_type
            if download_state:
                download_state.mark_completed('FETCHING')
            if download_type == 'free':
                filtered_posts = [post for post in image_posts if post.get("free")]
            elif download_type == 'subscribed':
                filtered_posts = [post for post in image_posts if not post.get("free")]
            else:
                filtered_posts = image_posts

            message = f"Starting download of {len(filtered_posts)} filtered image posts..."
            logger.info(message)
            progress_queue.put(message)

            post_ids = [post.get("id") for post in filtered_posts]
            download_images_concurrently(session, post_ids, output_dir, filename_config, progress_queue, download_state, cancel_event=cancel_event)

        progress_queue.put("DONE")
        
    except Exception as e:
        error = f"Error: {str(e)}"
        logger.error(error)
        progress_queue.put(error)
        raise

def download_images_concurrently(session, post_ids, output_dir, filename_config, progress_queue=None, download_state=None, max_workers=1, cancel_event=None):
    headers = get_headers()
    total_posts = len(post_ids)
    
    progress_bar = tqdm(total=total_posts, desc="Downloading images", unit="post")

    def process_post(post_id):
        if cancel_event and cancel_event.is_set():
            return
            
        try:
            if download_state and download_state.is_completed(post_id):
                progress_bar.update(1)
                return

            url = f"https://api.myfans.jp/api/v2/posts/{post_id}"
            response = session.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()

            images = data.get('images', [])
            if not images:
                progress_bar.update(1)
                return

            name_creator = data['user']['username']
            output_folder = os.path.join(output_dir, name_creator, "images")
            os.makedirs(output_folder, exist_ok=True)

            for idx, image in enumerate(images):
                if cancel_event and cancel_event.is_set():
                    raise Exception("Cancelled by user")
                image_url = image.get('url')
                if not image_url:
                    continue

                ext = pathlib.Path(image_url).suffix
                file_name = generate_filename(data, filename_config, output_folder, ext)
                if len(images) > 1:
                    base, ext = os.path.splitext(file_name)
                    file_name = f"{base}_{idx + 1}{ext}"

                full_path = os.path.join(output_folder, file_name)
                
                if os.path.exists(full_path):
                    continue

                img_response = session.get(image_url, headers=headers)
                img_response.raise_for_status()

                with open(full_path, 'wb') as f:
                    f.write(img_response.content)

                generate_metadata(data, file_name, output_folder, ext.replace('.', ''))
                update_file_date(data, full_path)

            if download_state:
                download_state.mark_completed(post_id)
            progress_bar.update(1)

        except Exception as e:
            logger.error(f"Error downloading images for post {post_id}: {str(e)}")
            progress_bar.update(1)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for post_id in post_ids:
            if cancel_event and cancel_event.is_set():
                break
            future = executor.submit(process_post, post_id)
            futures[future] = post_id
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception as e:
                if progress_queue:
                    progress_queue.put(f"Error: {e}")

    progress_bar.close()
    if progress_queue:
        progress_queue.put("Image download process completed")


def get_available_resolutions(main_videos):
    """Get all available resolutions from video data"""
    resolutions = {}
    for video in main_videos:
        res = video.get("resolution")
        if res:
            # Map API resolutions to display names
            res_map = {
                'uhd': '4K',
                'fhd': '1080p (Full HD)',
                'hd': '720p (HD)',
                'sd': '480p (SD)',
                'ld': '360p (LD)'
            }
            resolutions[res] = res_map.get(res, res)
    
    # Always add 'best' option
    resolutions['best'] = 'Best Available'
    return resolutions

def get_video_info(input_post_id, session, headers):
    try:
        url = f"https://api.myfans.jp/api/v2/posts/{input_post_id}"
        response = session.get(url, headers=headers)
        response.raise_for_status()
        
        data = response.json()
        main_videos = data.get('videos', {}).get('main', [])
        
        if not main_videos:
            logger.error(f"No video content found for post {input_post_id}")
            return None, None, "No videos found"
            
        logger.info(f"Found {len(main_videos)} video variants for post {input_post_id}")
        
        available_resolutions = []
        resolution_info = {}
        
        for video in main_videos:
            res = video.get("resolution")
            if res:
                available_resolutions.append(res)
                resolution_info[res] = {
                    "url": video.get("url"),
                    "size": video.get("size", 0),
                    "duration": video.get("duration", 0)
                }
        
        return data, resolution_info, None
    except requests.RequestException as e:
        logger.error(f"API request failed for post {input_post_id}: {str(e)}")
        return None, None, str(e)
    except Exception as e:
        logger.error(f"Unexpected error for post {input_post_id}: {str(e)}")
        return None, None, str(e)

def handle_image_download(post_id, session, headers, output_dir, filename_config, progress_queue=None):
    """Handle downloading of a single image post"""
    try:
        url = f"https://api.myfans.jp/api/v2/posts/{post_id}"
        response = session.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()

        images = data.get('images', [])
        if not images:
            error = f"No images found for post ID {post_id}"
            logger.error(error)
            if progress_queue:
                progress_queue.put(error)
            return False

        name_creator = data['user']['username']
        output_folder = os.path.join(output_dir, name_creator, "images")
        os.makedirs(output_folder, exist_ok=True)

        for idx, image in enumerate(images):
            image_url = image.get('url')
            if not image_url:
                continue

            ext = pathlib.Path(image_url).suffix
            file_name = generate_filename(data, filename_config, output_folder, ext)
            if len(images) > 1:
                base, ext = os.path.splitext(file_name)
                file_name = f"{base}_{idx + 1}{ext}"

            full_path = os.path.join(output_folder, file_name)
            
            if os.path.exists(full_path):
                message = f"Image already exists: {file_name}"
                logger.info(message)
                generate_metadata(data, file_name, output_folder, ext)
                update_file_date(data, full_path)
                # if progress_queue:
                #     progress_queue.put(message)
                continue

            img_response = session.get(image_url, headers=headers)
            img_response.raise_for_status()

            with open(full_path, 'wb') as f:
                f.write(img_response.content)

            generate_metadata(data, file_name, output_folder, ext.replace('.', ''))
            update_file_date(data, full_path)

            message = f"Downloaded image: {file_name}"
            logger.info(message)
            if progress_queue:
                progress_queue.put(message)

        return True

    except Exception as e:
        error = f"Error downloading images for post {post_id}: {str(e)}"
        logger.error(error)
        if progress_queue:
            progress_queue.put(error)
        return False

def validate_video_url(url, headers, session=None):
    """Validate video URL is accessible"""
    try:
        if session is None:
            session = requests.Session()
        
        response = session.head(url, headers=headers, allow_redirects=True, timeout=10)
        
        if response.status_code != 200:
            logger.error(f"URL validation failed with status code {response.status_code}")
            return False
            
        content_type = response.headers.get('content-type', '')
        valid_types = ['video', 'application/vnd.apple.mpegurl', 'application/x-mpegurl']
        if not any(t in content_type.lower() for t in valid_types):
            logger.error(f"Invalid content type: {content_type}")
            return False
            
        return True
        
    except Exception as e:
        logger.error(f"URL validation error: {str(e)}")
        return False

def check_existing_files(filtered_posts: List[Dict], output_dir: str, filename_config: Dict) -> Tuple[List[str], List[str]]:
    """
    Check which files already exist and verify their integrity.
    Returns tuple of (existing_files, missing_files) where each is a list of post IDs.
    """
    existing_files = []
    missing_files = []
    
    for post in filtered_posts:
        post_id = post.get('id')
        if not post_id:
            continue

        # Get post date
        post_date = post.get('posted_at', '').split('T')[0] if post.get('posted_at') else 'unknown_date'
        
        # Get username
        username = post.get('user', {}).get('username', 'unknown')
        
        # Generate possible filenames (both old and new patterns)
        possible_filenames = [
            # New pattern with post ID
            generate_filename(post, filename_config, output_dir, '.mp4'),
            # Old pattern with {title}
            f"{username}_{post_date}_{{title}}.mp4",
            f"{username}_{post_date}_{{title}}_1.mp4"  # For split videos
        ]
        
        output_folder = os.path.join(output_dir, username, "videos")
        
        # Check if any of the possible filenames exist
        found_valid_file = False
        for filename in possible_filenames:
            full_path = os.path.join(output_folder, filename)
            if os.path.exists(full_path) and os.path.getsize(full_path) > 0:
                if verify_video_file(full_path):
                    existing_files.append(post_id)
                    # also update metadata and file dates (temp)
                    generate_metadata(post, filename, output_folder)
                    update_file_date(post, full_path)
                    logger.info(f"Found existing verified file: {filename}")
                    found_valid_file = True
                    break
                else:
                    logger.warning(f"Found corrupted file, will redownload: {filename}")
                    try:
                        os.remove(full_path)
                    except OSError as e:
                        logger.error(f"Error removing corrupted file: {e}")
        
        if not found_valid_file:
            missing_files.append(post_id)
            
    return existing_files, missing_files

def generate_filename(post: Dict, filename_config: Dict, output_dir: str, ext:str = '.mp4', max_length:int=100) -> str:
    """Generate a unique filename for the video"""
    username = post.get('user', {}).get('username', 'unknown')
    post_id = post.get('id', 'unknown')
    
    # Debug: log available date fields
    date_fields = [field for field in post.keys() if 'date' in field.lower() or 'time' in field.lower() or 'at' in field.lower()]
    logger.debug(f"Available date fields for post {post_id}: {date_fields}")
    
    # Try each possible date field explicitly
    post_date = None
    if date_obj := get_post_date(post):
        post_date = date_obj.strftime('%Y-%m-%d')
        logger.info(f"Using date: {post_date} for post {post_id}")

    # Fallback to "unknown_date" if no date field is found
    if not post_date:
        post_date = "unknown_date"
        logger.warning(f"No date found for post {post_id}, dumping post data for debug")
        # Log first 500 chars of post data for debugging
        logger.debug(f"Post data excerpt: {str(post)[:500]}...")

    # Get title or use part of post ID
    title = post.get('title', '')
    if not title or title.strip() == '':
        title = post.get('body', '')
    if not title or title.strip() == '':
        title = post_id[:8]  # Use first 8 chars of post ID as title
        
    # Clean the title
    title = clean_filename(title, max_length)
    
    # Get separator
    separator = filename_config.get('separator', '_')
    
    # Generate filename based on pattern
    pattern = filename_config.get('pattern', '{creator}_{date}_{id}')
    filename = pattern.replace('{creator}', username) \
                     .replace('{date}', post_date) \
                     .replace('{title}', title) \
                     .replace('{id}', post_id)
    
    # Remove duplicate post_id in filename (if present)
    base_name = os.path.splitext(filename)[0]
    if base_name.endswith(f"_{post_id}") and f"_{post_id}" in base_name[:-len(post_id)-1]:
        filename = base_name[:-len(post_id)-1] + ext
    
    # Ensure extension
    if not filename.endswith(ext):
        filename += ext

    logger.info(f"Generated filename for post {post_id}: {filename}")
    return filename


def generate_metadata(post: Dict, filename: str, output_dir: str, ext: str = 'mp4'):
    enabled = int(os.getenv('WRITE_METADATA', '0'))
    if not enabled:
        return
    # User
    userdata = post.get('user', {})
    username = userdata.get('username', '')
    user_id  = userdata.get('id', '')
    # Post data
    post_id = post.get('id', '')
    post_body = post.get('body', '')
    # Date
    date_obj = get_post_date(post)
    post_date = None
    if date_obj:
        post_date = date_obj.strftime('%Y-%m-%d %H:%M:%S')

    metadata_path = os.path.join(output_dir, f"{filename}.json")
    metadata = {
        "service": "myfans",
        "category": "myfans",
        "subcategory": "myfans",
        "id": str(post_id),
        "is_preview": False,
        "user": str(user_id),
        "username": username,
        "content": post_body,
        "post_id": str(post_id),
        "type": "attachment",
        "extension": ext,
        "date": post_date,
        "post_date": post_date,
        "media_date": post_date
    }
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    update_file_date(post, metadata_path)


def get_post_date(post: Dict) -> datetime.datetime | None:
    post_date_str = None
    try:
        if post.get('posted_at') and isinstance(post.get('posted_at'), str):
            post_date_str = post.get('posted_at')
        elif post.get('created_at') and isinstance(post.get('created_at'), str):
            post_date_str = post.get('created_at')
        elif post.get('published_at') and isinstance(post.get('published_at'), str):
            post_date_str = post.get('published_at')
        elif post.get('timestamp') and isinstance(post.get('timestamp'), (int, float)):
            timestamp = post.get('timestamp')
            return datetime.datetime.fromtimestamp(timestamp)
        if post_date_str:
            return datetime.datetime.fromisoformat(post_date_str)
    except Exception as e:
        logger.error(f"Failed to parse date for post {post.get('id', 'unknown')} ({str(e)})")
    return None

def update_file_date(post: Dict, full_path: str):
    date_obj = get_post_date(post)
    if date_obj:
        timestamp = date_obj.timestamp()
        os.utime(full_path, (timestamp, timestamp))

def clean_filename(filename: str, max_length:int = 100) -> str:
    """Clean a string to make it safe for filenames"""
    # Replace problematic characters
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        filename = filename.replace(char, '_')
    
    # Remove or replace other problematic characters
    filename = re.sub(r'[\x00-\x1f]', '', filename)
    filename = filename.strip('. ')  # Remove leading/trailing dots and spaces
    
    # Limit length
    if len(filename) > max_length:
        name, ext = os.path.splitext(filename)
        filename = name[:max_length-len(ext)] + ext
        
    return filename if filename else 'unnamed'

def read_filename_config(config: configparser.ConfigParser) -> Dict:
    """Read filename configuration from config file"""
    try:
        filename_config = {
            'pattern': config.get('Filename', 'pattern', fallback='{creator}_{date}_{id}'),
            'separator': config.get('Filename', 'separator', fallback='_'),
            'numbers': config.get('Filename', 'numbers', fallback=''),
            'letters': config.get('Filename', 'letters', fallback='')
        }
        return filename_config
    except Exception as e:
        logger.error(f"Error reading filename config: {e}")
        return {
            'pattern': '{creator}_{date}_{id}',
            'separator': '_',
            'numbers': '',
            'letters': ''
        }

def validate_filename_config(filename_config: Dict) -> bool:
    """Validate filename configuration"""
    required_keys = ['pattern', 'separator']
    
    # Check for required keys
    for key in required_keys:
        if key not in filename_config:
            logger.error(f"Missing required key in filename config: {key}")
            return False
            
    # Validate pattern
    pattern = filename_config['pattern']
    required_fields = ['{creator}', '{date}', '{id}']
    
    for field in required_fields:
        if field not in pattern:
            logger.error(f"Missing required field in filename pattern: {field}")
            return False
            
    return True
