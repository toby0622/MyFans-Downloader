import concurrent.futures
import datetime
import json
import logging
import os
import pathlib
import re
import shutil
import subprocess
import time
import uuid
from urllib.parse import urljoin, urlparse

import m3u8
import requests
from tqdm import tqdm

from myfans_downloader.myfans_api import (
    DEFAULT_TIMEOUT,
    ENDPOINTS,
    MyFansApi,
)

logger = logging.getLogger(__name__)


class DownloadCancelled(RuntimeError):
    """Raised when the user cancels an active download."""


_DOWNLOAD_ERRORS = (
    AttributeError,
    KeyError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    requests.RequestException,
    subprocess.SubprocessError,
)


def log_and_notify(level, message, progress_queue=None):
    """Log a message and optionally push it to the progress queue for the UI."""
    getattr(logger, level)(message)
    if progress_queue:
        progress_queue.put(message)


def _require_headers(headers):
    if not headers:
        raise ValueError("Authenticated request headers are required")
    return headers


def _subprocess_creation_flags() -> int:
    if os.name == "nt":
        return subprocess.CREATE_NO_WINDOW
    return 0


def verify_video_file(file_path: str) -> bool:
    """Verify if a video file is valid"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", file_path],
            capture_output=True,
            check=False,
            creationflags=_subprocess_creation_flags(),
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError) as e:
        logger.error(f"Error verifying video file {file_path}: {e}")
        return False


def safe_urljoin(base: str, url: str) -> str:
    """Safely join URL parts ensuring no None values"""
    if not base or not url:
        raise ValueError("Base URL and URL parts must not be None")
    return urljoin(base, url)


def DL_File(
    m3u8_url_download,
    output_file,
    input_post_id,
    headers=None,
    chunk_size=1024 * 1024,
    max_retries=3,
    retry_delay=5,
    progress_queue=None,
    download_state=None,
    cancel_event=None,
    segment_threads=10,
):
    try:
        segment_threads = max(1, min(32, int(segment_threads)))
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
        ts_file = os.path.join(output_folder, random_name + ".ts")
        temp_folder = os.path.join(output_folder, random_name + ".ts_parts")

        os.makedirs(output_folder, exist_ok=True)
        os.makedirs(temp_folder, exist_ok=True)

        # Setup a dedicated session while reusing the authenticated API headers.
        headers = _require_headers(headers)
        session = requests.Session()
        session.headers.update(headers)

        # Use connection pooling for better performance
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=segment_threads,
            pool_maxsize=segment_threads,
            max_retries=max_retries,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        for attempt in range(max_retries):
            try:
                # Get master playlist
                logger.info("Fetching the master playlist for post %s", input_post_id)
                response = session.get(m3u8_url_download, timeout=30)
                response.raise_for_status()
                master_content = response.text

                # Parse master playlist
                master_playlist = m3u8.loads(master_content)
                master_base_uri = response.url.rsplit("/", 1)[0] + "/"
                master_playlist.base_uri = master_base_uri

                if master_playlist.playlists:
                    variants = [
                        item
                        for item in master_playlist.playlists
                        if item.uri and item.stream_info and item.stream_info.bandwidth
                    ]
                    if not variants:
                        raise ValueError(
                            "Master playlist contains no usable video variants"
                        )
                    variant = max(variants, key=lambda item: item.stream_info.bandwidth)
                    variant_url = safe_urljoin(master_base_uri, variant.uri)
                    logger.info("Fetching the selected variant playlist")
                    response = session.get(variant_url, timeout=30)
                    response.raise_for_status()
                    playlist = m3u8.loads(response.text)
                    playlist.base_uri = response.url.rsplit("/", 1)[0] + "/"
                else:
                    # Some posts return a media playlist directly instead of a master.
                    playlist = master_playlist

                if not playlist.segments:
                    logger.error("No segments found in variant playlist")
                    continue

                total_segments = len(playlist.segments)
                logger.info(f"Found {total_segments} segments for post {input_post_id}")

                if progress_queue:
                    progress_queue.put(
                        f"Downloading {total_segments} segments with {segment_threads} parallel threads"
                    )

                if download_state:
                    download_state.add_download(
                        input_post_id, segments_total=total_segments
                    )

                # Download segments concurrently
                segment_files = [
                    None
                ] * total_segments  # Pre-allocate list with correct order
                processed_count = 0

                playlist_base_uri = playlist.base_uri

                def download_segment(i, segment, base_uri=playlist_base_uri):
                    if cancel_event and cancel_event.is_set():
                        return i, None
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
                            seg_url = (
                                safe_urljoin(base_uri, segment.uri)
                                if not segment_uri_is_absolute(segment.uri)
                                else segment.uri
                            )
                            response = session.get(seg_url, timeout=30)
                            response.raise_for_status()

                            part_path = seg_path + ".part"
                            with open(part_path, "wb") as f:
                                f.write(response.content)
                            os.replace(part_path, seg_path)

                            if (
                                os.path.exists(seg_path)
                                and os.path.getsize(seg_path) > 0
                            ):
                                return i, seg_path
                        except (OSError, ValueError, requests.RequestException) as e:
                            logger.error(f"Error downloading segment {i}: {e!s}")
                            if seg_retry == 2:  # Last attempt
                                return i, None
                            time.sleep(retry_delay)

                    return i, None

                # Use ThreadPoolExecutor for concurrent downloads
                with (
                    tqdm(
                        total=total_segments, desc=f"Segments for {input_post_id}"
                    ) as pbar,
                    concurrent.futures.ThreadPoolExecutor(
                        max_workers=segment_threads
                    ) as executor,
                ):
                        futures = {
                            executor.submit(download_segment, i, segment): i
                            for i, segment in enumerate(playlist.segments)
                        }

                        for future in concurrent.futures.as_completed(futures):
                            if cancel_event and cancel_event.is_set():
                                logger.info(
                                    f"Cancellation requested during post {input_post_id} segments download"
                                )
                                # Try to cancel remaining futures
                                for f in futures:
                                    f.cancel()
                                raise DownloadCancelled("Cancelled by user")

                            try:
                                idx, file_path = future.result()
                                if file_path:
                                    segment_files[idx] = file_path
                                processed_count += 1
                                pbar.update(1)
                                if download_state:
                                    download_state.update_progress(
                                        input_post_id, processed_count
                                    )

                                # Log progress occasionally
                                if (
                                    processed_count % 50 == 0
                                    or processed_count == total_segments
                                ):
                                    success_rate = (
                                        len([f for f in segment_files if f])
                                        / processed_count
                                        * 100
                                    )
                                    log_and_notify(
                                        "info",
                                        f"Progress: {processed_count}/{total_segments} segments ({success_rate:.1f}% success)",
                                        progress_queue,
                                    )
                            except _DOWNLOAD_ERRORS as e:
                                logger.error(f"Error processing segment result: {e!s}")

                # Filter out None values (failed downloads)
                valid_segments = [f for f in segment_files if f]
                success_rate = len(valid_segments) / total_segments * 100

                logger.info(
                    f"Downloaded {len(valid_segments)}/{total_segments} segments ({success_rate:.1f}% success)"
                )
                if progress_queue:
                    progress_queue.put(
                        f"Downloaded {len(valid_segments)}/{total_segments} segments ({success_rate:.1f}% success)"
                    )

                if len(valid_segments) != total_segments:
                    logger.error("Refusing to merge an incomplete segment set")
                    if attempt < max_retries - 1:
                        logger.info(
                            f"Retrying download, attempt {attempt + 2}/{max_retries}"
                        )
                        continue
                    raise RuntimeError(
                        f"Only {len(valid_segments)} of {total_segments} video segments were downloaded"
                    )

                # Merge segments
                logger.info("Merging segments...")
                if progress_queue:
                    progress_queue.put("Merging segments...")

                with open(ts_file, "wb") as outfile:
                    for seg_file in valid_segments:
                        if os.path.exists(seg_file):
                            with open(seg_file, "rb") as infile:
                                outfile.write(infile.read())

                # Convert to MP4
                logger.info("Converting to MP4...")
                if progress_queue:
                    progress_queue.put("Converting to MP4...")

                result = subprocess.run(
                    ["ffmpeg", "-y", "-i", ts_file, "-c", "copy", output_file],
                    capture_output=True,
                    check=False,
                    text=True,
                    creationflags=_subprocess_creation_flags(),
                )

                if result.returncode != 0:
                    logger.error(f"FFmpeg error: {result.stderr}")
                    if os.path.exists(output_file):
                        os.remove(output_file)
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
                    except OSError as e:
                        logger.warning(f"Error during cleanup: {e!s}")

                    logger.info(f"Successfully downloaded {input_post_id}")
                    if progress_queue:
                        progress_queue.put(f"Successfully downloaded {input_post_id}")

                    if download_state:
                        download_state.mark_completed(input_post_id)
                    session.close()
                    return True

            except _DOWNLOAD_ERRORS as e:
                logger.error(f"Download attempt {attempt + 1} failed: {e!s}")
                if progress_queue:
                    progress_queue.put(f"Download attempt {attempt + 1} failed: {e!s}")
                if cancel_event and cancel_event.is_set():
                    break
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)

        session.close()
        if os.path.exists(ts_file):
            os.remove(ts_file)
        if os.path.isdir(temp_folder):
            shutil.rmtree(temp_folder, ignore_errors=True)
        if os.path.exists(output_file) and not verify_video_file(output_file):
            os.remove(output_file)
        return False

    except _DOWNLOAD_ERRORS as e:
        logger.exception("Fatal error in DL_File")
        if progress_queue:
            progress_queue.put(f"Fatal error: {e!s}")
        session_value = locals().get("session")
        if session_value is not None:
            session_value.close()
        temp_value = locals().get("temp_folder")
        if temp_value and os.path.isdir(temp_value):
            shutil.rmtree(temp_value, ignore_errors=True)
        return False


def segment_uri_is_absolute(uri: str) -> bool:
    return uri.lower().startswith(("http://", "https://"))


def process_post_id(
    input_post_id,
    session,
    headers,
    selected_resolution,
    output_dir,
    filename_config,
    progress_bar=None,
    progress_queue=None,
    download_state=None,
    cancel_event=None,
):
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
            logger.info(
                f"Available resolutions for post {input_post_id}: {list(resolution_info.keys())}"
            )
        else:
            logger.error(f"No resolution info available for post {input_post_id}")
            return False

        # Check if it's a video post
        if not data.get("videos", {}).get("main"):
            message = f"Post ID {input_post_id} is not a video post"
            logger.error(message)
            if progress_queue:
                progress_queue.put(message)
            return False

        # Select resolution with fallback logging
        if selected_resolution == "best":
            for res in ["uhd", "fhd", "hd", "sd", "ld"]:
                if res in resolution_info:
                    selected_resolution = res
                    logger.info(
                        f"Selected best available resolution for post {input_post_id}: {res}"
                    )
                    break

        # Verify selected resolution exists
        if selected_resolution not in resolution_info:
            available = ", ".join(resolution_info.keys())
            message = f"Resolution {selected_resolution} not available for post {input_post_id}. Available: {available}"
            logger.warning(message)
            if progress_queue:
                progress_queue.put(message)
            # Try fallback
            for res in ["uhd", "fhd", "hd", "sd", "ld"]:
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

        logger.info("Selected a video stream for post %s", input_post_id)

        # Check access level with detailed logging
        logger.info(
            f"Post {input_post_id} - Free: {data.get('free')}, Subscribed: {data.get('subscribed')}"
        )
        if data.get("free") is False and not data.get("subscribed"):
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
        creator_dir = clean_filename(str(data["user"]["username"]), 80)
        output_folder = str(os.path.join(output_dir, creator_dir, "videos"))
        os.makedirs(output_folder, exist_ok=True)
        filename = None
        full_path = None
        path_valid = False
        for max_length in list(range(100, 10, -10)):  # start at 100, decrease by 10.
            try:
                filename = generate_filename(
                    data, filename_config, max_length=max_length
                )
                full_path = os.path.join(output_folder, filename)
                path = pathlib.Path(str(full_path))
                # if it already exists we can exit out
                if path.exists():
                    path_valid = True
                    break
                path.with_suffix(
                    ".longextension"
                )  # to ensure metadata works (e.g. .webp.json)
                # verify the path works by creating and deleting the file.
                path.touch()
                if path.exists():
                    path.unlink()
                    path_valid = True
                    break
            except (OSError, ValueError) as e:
                logger.debug(
                    f"Invalid path with length {max_length} ({e!s}), reducing..."
                )

        if not path_valid or filename is None or full_path is None:
            raise OSError(
                "Could not create a valid output filename in the download directory"
            )

        # Check existing file
        if os.path.exists(full_path) and os.path.getsize(full_path) > 0:
            if verify_video_file(full_path):
                generate_metadata(
                    data,
                    filename,
                    output_folder,
                    enabled=filename_config["write_metadata"],
                )
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

        # Start download
        message = f"Starting download of video {input_post_id}"
        logger.info(message)
        if progress_queue:
            progress_queue.put(message)

        success = DL_File(
            video_url,
            full_path,
            input_post_id,
            headers=headers,
            progress_queue=progress_queue,
            download_state=download_state,
            cancel_event=cancel_event,
            segment_threads=filename_config["thread_count"],
        )

        if success:
            generate_metadata(
                data,
                filename,
                output_folder,
                enabled=filename_config["write_metadata"],
            )
            update_file_date(data, full_path)
            message = f"Successfully downloaded video: {filename}"
            logger.info(message)
        else:
            message = f"Failed to download video for post ID {input_post_id}"
            logger.error(message)
            if download_state:
                download_state.mark_failed(input_post_id, message)

        if progress_queue:
            progress_queue.put(message)
        if progress_bar:
            progress_bar.update(1)

        return success

    except _DOWNLOAD_ERRORS as e:
        error = f"Error processing post {input_post_id}: {e!s}"
        logger.error(error)
        if progress_queue:
            progress_queue.put(error)
        if progress_bar:
            progress_bar.update(1)
        return False


def download_videos_concurrently(
    session,
    post_ids,
    selected_resolution,
    output_dir,
    filename_config,
    progress_queue=None,
    download_state=None,
    cancel_event=None,
    headers=None,
):
    max_workers = 1  # Forced sequential

    headers = _require_headers(headers)
    total_posts = len(post_ids)
    message = f"Starting download of {total_posts} posts strictly one at a time..."
    logger.info(message)
    if progress_queue:
        progress_queue.put(message)

    progress_bar = tqdm(total=total_posts, desc="Downloading videos", unit="video")

    def process_post(post_id):
        return process_post_id(
            post_id,
            session,
            headers,
            selected_resolution,
            output_dir,
            filename_config,
            progress_bar,
            progress_queue,
            download_state,
            cancel_event,
        )

    failures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for post_id in post_ids:
            if cancel_event and cancel_event.is_set():
                break
            future = executor.submit(process_post, post_id)
            futures[future] = post_id

        for future in concurrent.futures.as_completed(futures):
            try:
                if not future.result():
                    failures.append(futures[future])
            except _DOWNLOAD_ERRORS as e:
                logger.error(f"Error in thread: {e}")
                failures.append(futures[future])

    progress_bar.close()
    if progress_queue:
        progress_queue.put("Download process completed")
    if failures and not (cancel_event and cancel_event.is_set()):
        raise RuntimeError(f"{len(failures)} video post(s) failed to download")


def download_single_file(
    session,
    post_id,
    selected_resolution,
    output_dir,
    filename_config,
    headers=None,
    progress_queue=None,
    download_state=None,
    cancel_event=None,
):
    headers = _require_headers(headers)
    return process_post_id(
        post_id,
        session,
        headers,
        selected_resolution,
        output_dir,
        filename_config,
        progress_queue=progress_queue,
        download_state=download_state,
        cancel_event=cancel_event,
    )


def _fetch_all_posts(
    api,
    user_id,
    kind,
    progress_queue,
    download_state,
    cancel_event,
    *,
    back_number=False,
):
    label = "back number" if back_number else "regular"
    posts = []
    page = 1
    while True:
        if cancel_event and cancel_event.is_set():
            logger.info("Cancellation requested during %s post fetch", label)
            return posts
        message = f"Fetching page {page} of {label} posts..."
        logger.info(message)
        progress_queue.put(message)
        if download_state:
            download_state.update_progress("FETCHING", page)
        page_data = api.get_posts(user_id, page, back_number=back_number)
        if not page_data:
            break
        matching = [post for post in page_data if post.get("kind") == kind]
        posts.extend(matching)
        progress_queue.put(f"Found {len(matching)} {kind} posts on page {page}")
        page += 1
        time.sleep(0.3)
    return posts


def start_download(
    username,
    post_type,
    download_type,
    progress_queue,
    download_state=None,
    *,
    app_settings,
    post_id=None,
    resolution="best",
    cancel_event=None,
):
    """Handle one validated download task from the desktop application."""
    api = None
    try:
        auth_token = str(app_settings.get("auth_token", "")).strip()
        if not auth_token:
            raise ValueError("An Auth Token is required.")
        api = MyFansApi(auth_token)
        session = api.session
        headers = api.headers
        output_dir = str(app_settings["output_dir"])
        os.makedirs(output_dir, exist_ok=True)
        filename_config = {
            "pattern": str(app_settings["filename_pattern"]),
            "separator": str(app_settings["filename_separator"]),
            "thread_count": int(app_settings["thread_count"]),
            "write_metadata": bool(app_settings["write_metadata"]),
        }

        if post_id:
            if download_state and download_state.is_completed(post_id):
                progress_queue.put(f"Post {post_id} was already downloaded; skipping.")
                return
            message = f"Starting download for post ID: {post_id}"
            logger.info(message)
            progress_queue.put(message)
            if post_type == "videos":
                success = download_single_file(
                    session,
                    post_id,
                    resolution,
                    output_dir,
                    filename_config,
                    headers=headers,
                    progress_queue=progress_queue,
                    download_state=download_state,
                    cancel_event=cancel_event,
                )
            else:
                success = handle_image_download(
                    post_id,
                    session,
                    headers,
                    output_dir,
                    filename_config,
                    progress_queue,
                    cancel_event=cancel_event,
                )
                if success and download_state:
                    download_state.mark_completed(post_id)
            if not success and not (cancel_event and cancel_event.is_set()):
                raise RuntimeError(f"Download failed for post {post_id}")
            return

        message = f"Starting download for user: {username}, type: {post_type}, mode: {download_type}"
        logger.info(message)
        progress_queue.put(message)
        user_data = api.get_user(username)
        user_id = user_data.get("id")
        if not user_id:
            raise ValueError("Could not find that username on MyFans.")

        if download_state:
            download_state.add_download("FETCHING", status="fetching", segments_total=0)
        kind = "video" if post_type == "videos" else "image"
        posts = _fetch_all_posts(
            api, user_id, kind, progress_queue, download_state, cancel_event
        )
        if user_data.get("current_back_number_plan") and not (
            cancel_event and cancel_event.is_set()
        ):
            posts.extend(
                _fetch_all_posts(
                    api,
                    user_id,
                    kind,
                    progress_queue,
                    download_state,
                    cancel_event,
                    back_number=True,
                )
            )
        if download_state:
            download_state.mark_completed("FETCHING")
        if cancel_event and cancel_event.is_set():
            return

        # De-duplicate posts returned by overlapping collections.
        unique_posts = {str(post.get("id")): post for post in posts if post.get("id")}
        posts = list(unique_posts.values())
        if download_type == "free":
            filtered_posts = [post for post in posts if post.get("free")]
        elif download_type == "subscribed":
            filtered_posts = [post for post in posts if not post.get("free")]
        else:
            filtered_posts = posts

        if download_state:
            completed_ids = download_state.completed_ids(
                str(post["id"]) for post in filtered_posts
            )
            if completed_ids:
                progress_queue.put(
                    f"Skipping {len(completed_ids)} post(s) recorded as complete."
                )
                filtered_posts = [
                    post
                    for post in filtered_posts
                    if str(post["id"]) not in completed_ids
                ]

        progress_queue.put(f"Found {len(filtered_posts)} matching {kind} posts")
        if post_type == "videos":
            existing_files, missing_files = check_existing_files(
                filtered_posts, output_dir, filename_config
            )
            progress_queue.put(
                f"Found {len(existing_files)} existing files, {len(missing_files)} files to download"
            )
            if missing_files:
                download_videos_concurrently(
                    session,
                    missing_files,
                    resolution,
                    output_dir,
                    filename_config,
                    progress_queue,
                    download_state,
                    cancel_event=cancel_event,
                    headers=headers,
                )
            else:
                progress_queue.put("All files are already downloaded.")
        else:
            post_ids = [post.get("id") for post in filtered_posts]
            download_images_concurrently(
                session,
                post_ids,
                output_dir,
                filename_config,
                progress_queue,
                download_state,
                cancel_event=cancel_event,
                headers=headers,
            )
    except Exception:
        logger.exception("Download task failed")
        raise
    finally:
        if api is not None:
            api.close()


def download_images_concurrently(
    session,
    post_ids,
    output_dir,
    filename_config,
    progress_queue=None,
    download_state=None,
    max_workers=None,
    cancel_event=None,
    headers=None,
):
    headers = _require_headers(headers)
    total_posts = len(post_ids)
    if max_workers is None:
        max_workers = max(1, min(32, int(filename_config["thread_count"])))

    progress_bar = tqdm(total=total_posts, desc="Downloading images", unit="post")
    failures = []

    def process_post(post_id):
        if cancel_event and cancel_event.is_set():
            return

        try:
            response = session.get(
                ENDPOINTS.post(post_id), headers=headers, timeout=DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            data = response.json()

            images = data.get("images", [])
            if not images:
                raise ValueError(f"No images found for post {post_id}")

            name_creator = clean_filename(str(data["user"]["username"]), 80)
            output_folder = os.path.join(output_dir, name_creator, "images")
            os.makedirs(output_folder, exist_ok=True)

            for idx, image in enumerate(images):
                if cancel_event and cancel_event.is_set():
                    raise DownloadCancelled("Cancelled by user")
                image_url = image.get("url")
                if not image_url:
                    continue

                ext = _image_extension(image_url)
                file_name = generate_filename(data, filename_config, ext)
                if len(images) > 1:
                    base, ext = os.path.splitext(file_name)
                    file_name = f"{base}_{idx + 1}{ext}"

                full_path = os.path.join(output_folder, file_name)

                if os.path.exists(full_path) and os.path.getsize(full_path) > 0:
                    continue
                _download_image_file(
                    session, image_url, headers, full_path, cancel_event=cancel_event
                )

                generate_metadata(
                    data,
                    file_name,
                    output_folder,
                    ext.replace(".", ""),
                    enabled=filename_config["write_metadata"],
                )
                update_file_date(data, full_path)

            if download_state:
                download_state.mark_completed(post_id)
        except _DOWNLOAD_ERRORS as e:
            logger.error(f"Error downloading images for post {post_id}: {e!s}")
            failures.append((post_id, str(e)))
            if download_state:
                download_state.mark_failed(post_id, e)
            if progress_queue:
                progress_queue.put(f"Error downloading images for post {post_id}: {e}")
            raise
        finally:
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
            except _DOWNLOAD_ERRORS as e:
                logger.debug("Image worker failed: %s", e)

    progress_bar.close()
    if progress_queue:
        progress_queue.put("Image download process completed")
    if failures and not (cancel_event and cancel_event.is_set()):
        raise RuntimeError(f"{len(failures)} image post(s) failed to download")


def get_video_info(input_post_id, session, headers):
    try:
        response = session.get(
            ENDPOINTS.post(input_post_id), headers=headers, timeout=DEFAULT_TIMEOUT
        )
        response.raise_for_status()

        data = response.json()
        main_videos = data.get("videos", {}).get("main", [])

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
                    "duration": video.get("duration", 0),
                }

        return data, resolution_info, None
    except requests.RequestException as e:
        logger.error(f"API request failed for post {input_post_id}: {e!s}")
        return None, None, str(e)
    except (AttributeError, KeyError, TypeError, ValueError) as e:
        logger.error(f"Unexpected error for post {input_post_id}: {e!s}")
        return None, None, str(e)


def _image_extension(image_url):
    extension = pathlib.Path(urlparse(image_url).path).suffix.lower()
    if (
        not extension
        or len(extension) > 10
        or not re.fullmatch(r"\.[a-z0-9]+", extension)
    ):
        return ".jpg"
    return extension


def _download_image_file(session, image_url, headers, full_path, cancel_event=None):
    part_path = full_path + ".part"
    try:
        with session.get(
            image_url, headers=headers, timeout=DEFAULT_TIMEOUT, stream=True
        ) as response:
            response.raise_for_status()
            with open(part_path, "wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if cancel_event and cancel_event.is_set():
                        raise RuntimeError("Cancelled by user")
                    if chunk:
                        output.write(chunk)
        if os.path.getsize(part_path) == 0:
            raise ValueError("Downloaded image was empty")
        os.replace(part_path, full_path)
    finally:
        if os.path.exists(part_path):
            os.remove(part_path)


def handle_image_download(
    post_id,
    session,
    headers,
    output_dir,
    filename_config,
    progress_queue=None,
    cancel_event=None,
):
    """Handle downloading of a single image post"""
    try:
        response = session.get(
            ENDPOINTS.post(post_id), headers=headers, timeout=DEFAULT_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()

        images = data.get("images", [])
        if not images:
            error = f"No images found for post ID {post_id}"
            logger.error(error)
            if progress_queue:
                progress_queue.put(error)
            return False

        name_creator = clean_filename(str(data["user"]["username"]), 80)
        output_folder = os.path.join(output_dir, name_creator, "images")
        os.makedirs(output_folder, exist_ok=True)

        for idx, image in enumerate(images):
            image_url = image.get("url")
            if not image_url:
                continue

            ext = _image_extension(image_url)
            file_name = generate_filename(data, filename_config, ext)
            if len(images) > 1:
                base, ext = os.path.splitext(file_name)
                file_name = f"{base}_{idx + 1}{ext}"

            full_path = os.path.join(output_folder, file_name)

            if os.path.exists(full_path) and os.path.getsize(full_path) > 0:
                message = f"Image already exists: {file_name}"
                logger.info(message)
                generate_metadata(
                    data,
                    file_name,
                    output_folder,
                    ext,
                    enabled=filename_config["write_metadata"],
                )
                update_file_date(data, full_path)
                # if progress_queue:
                #     progress_queue.put(message)
                continue

            _download_image_file(
                session, image_url, headers, full_path, cancel_event=cancel_event
            )

            generate_metadata(
                data,
                file_name,
                output_folder,
                ext.replace(".", ""),
                enabled=filename_config["write_metadata"],
            )
            update_file_date(data, full_path)

            message = f"Downloaded image: {file_name}"
            logger.info(message)
            if progress_queue:
                progress_queue.put(message)

        return True

    except _DOWNLOAD_ERRORS as e:
        error = f"Error downloading images for post {post_id}: {e!s}"
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
            logger.error(
                f"URL validation failed with status code {response.status_code}"
            )
            return False

        content_type = response.headers.get("content-type", "")
        valid_types = [
            "video",
            "application/vnd.apple.mpegurl",
            "application/x-mpegurl",
        ]
        if not any(t in content_type.lower() for t in valid_types):
            logger.error(f"Invalid content type: {content_type}")
            return False

        return True

    except requests.RequestException as e:
        logger.error(f"URL validation error: {e!s}")
        return False


def check_existing_files(
    filtered_posts: list[dict], output_dir: str, filename_config: dict
) -> tuple[list[str], list[str]]:
    """
    Check which files already exist and verify their integrity.
    Returns tuple of (existing_files, missing_files) where each is a list of post IDs.
    """
    existing_files = []
    missing_files = []

    for post in filtered_posts:
        post_id = post.get("id")
        if not post_id:
            continue

        # Get username
        username = clean_filename(
            str(post.get("user", {}).get("username", "unknown")), 80
        )

        possible_filenames = [generate_filename(post, filename_config, ".mp4")]

        output_folder = os.path.join(output_dir, username, "videos")

        # Check if any of the possible filenames exist
        found_valid_file = False
        for filename in possible_filenames:
            full_path = os.path.join(output_folder, filename)
            if os.path.exists(full_path) and os.path.getsize(full_path) > 0:
                if verify_video_file(full_path):
                    existing_files.append(post_id)
                    generate_metadata(
                        post,
                        filename,
                        output_folder,
                        enabled=filename_config["write_metadata"],
                    )
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


def generate_filename(
    post: dict, filename_config: dict, ext: str = ".mp4", max_length: int = 100
) -> str:
    """Generate a unique filename for the video"""
    username = str(post.get("user", {}).get("username", "unknown"))
    post_id = str(post.get("id", "unknown"))

    # Debug: log available date fields
    date_fields = [
        field
        for field in post
        if "date" in field.lower() or "time" in field.lower() or "at" in field.lower()
    ]
    logger.debug(f"Available date fields for post {post_id}: {date_fields}")

    # Try each possible date field explicitly
    post_date = None
    if date_obj := get_post_date(post):
        post_date = date_obj.strftime("%Y-%m-%d")
        logger.info(f"Using date: {post_date} for post {post_id}")

    # Fallback to "unknown_date" if no date field is found
    if not post_date:
        post_date = "unknown_date"
        logger.warning(f"No date found for post {post_id}, dumping post data for debug")
        # Log first 500 chars of post data for debugging
        logger.debug(f"Post data excerpt: {str(post)[:500]}...")

    # Get title or use part of post ID
    title = post.get("title", "")
    if not title or title.strip() == "":
        title = post.get("body", "")
    if not title or title.strip() == "":
        title = post_id[:8]  # Use first 8 chars of post ID as title

    # Clean the title
    title = clean_filename(title, max_length)

    # Get separator
    separator = filename_config.get("separator", "_")

    # Generate filename based on pattern
    pattern = filename_config.get("pattern", "{creator}_{date}_{id}")
    if "{id}" not in pattern:
        pattern = f"{pattern}{separator}{{id}}"
    if separator != "_":
        pattern = pattern.replace("_", separator)
    filename = (
        pattern.replace("{creator}", username)
        .replace("{date}", post_date)
        .replace("{title}", title)
        .replace("{id}", post_id)
    )

    # Remove duplicate post_id in filename (if present)
    base_name = os.path.splitext(filename)[0]
    if (
        base_name.endswith(f"_{post_id}")
        and f"_{post_id}" in base_name[: -len(post_id) - 1]
    ):
        filename = base_name[: -len(post_id) - 1] + ext

    # Ensure extension
    if not filename.lower().endswith(ext.lower()):
        filename += ext

    filename = clean_filename(filename, max_length)

    logger.info(f"Generated filename for post {post_id}: {filename}")
    return filename


def generate_metadata(
    post: dict,
    filename: str,
    output_dir: str,
    ext: str = "mp4",
    *,
    enabled: bool = False,
):
    if not enabled:
        return
    # User
    userdata = post.get("user", {})
    username = userdata.get("username", "")
    user_id = userdata.get("id", "")
    # Post data
    post_id = post.get("id", "")
    post_body = post.get("body", "")
    # Date
    date_obj = get_post_date(post)
    post_date = None
    if date_obj:
        post_date = date_obj.strftime("%Y-%m-%d %H:%M:%S")

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
        "media_date": post_date,
    }
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    update_file_date(post, metadata_path)


def get_post_date(post: dict) -> datetime.datetime | None:
    post_date_str = None
    try:
        if post.get("posted_at") and isinstance(post.get("posted_at"), str):
            post_date_str = post.get("posted_at")
        elif post.get("created_at") and isinstance(post.get("created_at"), str):
            post_date_str = post.get("created_at")
        elif post.get("published_at") and isinstance(post.get("published_at"), str):
            post_date_str = post.get("published_at")
        elif post.get("timestamp") and isinstance(post.get("timestamp"), (int, float)):
            timestamp = post.get("timestamp")
            return (
                datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)
                .astimezone()
                .replace(tzinfo=None)
            )
        if post_date_str:
            return datetime.datetime.fromisoformat(post_date_str)
    except (OSError, OverflowError, TypeError, ValueError) as e:
        logger.error(
            f"Failed to parse date for post {post.get('id', 'unknown')} ({e!s})"
        )
    return None


def update_file_date(post: dict, full_path: str):
    date_obj = get_post_date(post)
    if date_obj:
        timestamp = date_obj.timestamp()
        os.utime(full_path, (timestamp, timestamp))


def clean_filename(filename: str, max_length: int = 100) -> str:
    """Clean a string to make it safe for filenames"""
    # Replace problematic characters
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        filename = filename.replace(char, "_")

    # Remove or replace other problematic characters
    filename = re.sub(r"[\x00-\x1f]", "", filename)
    filename = filename.strip(". ")  # Remove leading/trailing dots and spaces

    # Limit length
    if len(filename) > max_length:
        name, ext = os.path.splitext(filename)
        filename = name[: max_length - len(ext)] + ext

    if not filename:
        return "unnamed"
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }
    stem = os.path.splitext(filename)[0]
    if stem.upper() in reserved:
        filename = f"_{filename}"
    return filename
