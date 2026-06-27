import sqlite3
import json
import os
from datetime import datetime
import threading
import logging

logger = logging.getLogger(__name__)


class DownloadState:
    """Tracks download progress and completed files using SQLite for persistence.

    Session-level data (active downloads, progress) is kept in memory.
    Completed file records are persisted to SQLite for O(1) duplicate checks.
    On first run, existing JSON state is automatically migrated.
    """

    def __init__(self, state_dir="config"):
        os.makedirs(state_dir, exist_ok=True)
        self.db_path = os.path.join(state_dir, "download_state.db")
        self._lock = threading.Lock()
        self._init_db()
        self._migrate_from_json(state_dir)
        # In-memory session state for UI progress tracking (cleared each session)
        self.downloads = {}
        self._scan_existing_files()

    # ------------------------------------------------------------------
    # Database helpers
    # ------------------------------------------------------------------

    def _init_db(self):
        """Initialize SQLite database and create tables if they don't exist."""
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS completed_files (
                    file_id TEXT PRIMARY KEY,
                    completed_at TEXT NOT NULL
                )
            """)

    def _connect(self):
        """Create a new SQLite connection with WAL mode for better concurrency."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    # ------------------------------------------------------------------
    # Migration & scanning
    # ------------------------------------------------------------------

    def _migrate_from_json(self, state_dir):
        """One-time migration from legacy download_state.json to SQLite.

        After a successful migration the old JSON file is renamed to
        ``download_state.json.bak`` so the migration is not repeated.
        """
        json_path = os.path.join(state_dir, "download_state.json")
        if not os.path.exists(json_path):
            return
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            completed = data.get("completed_files", [])
            if completed:
                now = datetime.now().isoformat()
                with self._connect() as conn:
                    conn.executemany(
                        "INSERT OR IGNORE INTO completed_files (file_id, completed_at) VALUES (?, ?)",
                        [(str(entry), now) for entry in completed],
                    )
                logger.info("Migrated %d entries from JSON to SQLite", len(completed))
            # Rename old file so migration won't run again
            backup_path = json_path + ".bak"
            os.rename(json_path, backup_path)
            logger.info("Renamed legacy JSON state file to %s", backup_path)
        except Exception as e:
            logger.error("Error migrating from JSON state: %s", e)

    def _scan_existing_files(self):
        """Walk the downloads directory and register any untracked media files."""
        downloads_dir = os.getenv("DOWNLOADS_DIR", "./downloads")
        if not os.path.exists(downloads_dir):
            return
        media_extensions = (".mp4", ".jpg", ".png", ".webp", ".gif")
        new_files = []
        for root, _, files in os.walk(downloads_dir):
            for file in files:
                if file.endswith(media_extensions):
                    new_files.append(file)
        if new_files:
            now = datetime.now().isoformat()
            with self._lock:
                with self._connect() as conn:
                    conn.executemany(
                        "INSERT OR IGNORE INTO completed_files (file_id, completed_at) VALUES (?, ?)",
                        [(f, now) for f in new_files],
                    )

    # ------------------------------------------------------------------
    # Public API — session state (in-memory, for UI)
    # ------------------------------------------------------------------

    def add_download(self, post_id, status="pending", segments_total=0, segments_downloaded=0):
        """Register a new download in the session state for UI tracking."""
        self.downloads[post_id] = {
            "status": status,
            "start_time": datetime.now().isoformat(),
            "segments_total": segments_total,
            "segments_downloaded": segments_downloaded,
            "last_updated": datetime.now().isoformat(),
        }

    def update_progress(self, post_id, segments_downloaded):
        """Update segment download progress for the UI."""
        if post_id in self.downloads:
            self.downloads[post_id]["segments_downloaded"] = segments_downloaded
            self.downloads[post_id]["last_updated"] = datetime.now().isoformat()

    # ------------------------------------------------------------------
    # Public API — persistent state (SQLite)
    # ------------------------------------------------------------------

    def mark_completed(self, post_id):
        """Mark a post as completed in both session state and persistent storage."""
        if post_id in self.downloads:
            self.downloads[post_id]["status"] = "completed"
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO completed_files (file_id, completed_at) VALUES (?, ?)",
                    (str(post_id), datetime.now().isoformat()),
                )

    def mark_failed(self, post_id, error):
        """Mark a download as failed in session state."""
        if post_id in self.downloads:
            self.downloads[post_id]["status"] = "failed"

    def is_completed(self, post_id):
        """Check if a file has already been downloaded (indexed lookup)."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM completed_files WHERE file_id = ? LIMIT 1",
                (str(post_id),),
            )
            return cursor.fetchone() is not None

    def is_file_exists(self, filename):
        """Check if a file already exists in the completed records."""
        return self.is_completed(filename)

    def get_progress(self, post_id):
        """Get current download progress from session state."""
        return self.downloads.get(post_id, {})

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def get_serializable_state(self):
        """Return JSON-serializable state for the web UI status endpoint."""
        return {
            "downloads": self.downloads.copy(),
        }

    def save_state(self):
        """No-op — SQLite writes are immediate. Kept for API compatibility."""
        pass