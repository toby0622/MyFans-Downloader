import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone


class DownloadState:
    """Tracks download progress and completed files using SQLite for persistence.

    Session-level data (active downloads, progress) is kept in memory.
    Completed file records are persisted to SQLite for O(1) duplicate checks.
    """

    def __init__(self, state_dir: str):
        os.makedirs(state_dir, exist_ok=True)
        self.db_path = os.path.join(state_dir, "download_state.db")
        self._lock = threading.RLock()
        self._init_db()
        # In-memory session state for UI progress tracking (cleared each session)
        self.downloads = {}

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

    @contextmanager
    def _connect(self):
        """Create a new SQLite connection with WAL mode for better concurrency."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Public API — session state (in-memory, for UI)
    # ------------------------------------------------------------------

    def add_download(
        self, post_id, status="pending", segments_total=0, segments_downloaded=0
    ):
        """Register a new download in the session state for UI tracking."""
        with self._lock:
            self.downloads[post_id] = {
                "status": status,
                "start_time": datetime.now(timezone.utc).isoformat(),
                "segments_total": segments_total,
                "segments_downloaded": segments_downloaded,
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }

    def update_progress(self, post_id, segments_downloaded):
        """Update segment download progress for the UI."""
        with self._lock:
            if post_id in self.downloads:
                self.downloads[post_id]["segments_downloaded"] = segments_downloaded
                self.downloads[post_id]["last_updated"] = datetime.now(
                    timezone.utc
                ).isoformat()

    def reset_session(self):
        """Clear completed UI rows while keeping persistent completion records."""
        with self._lock:
            self.downloads.clear()

    # ------------------------------------------------------------------
    # Public API — persistent state (SQLite)
    # ------------------------------------------------------------------

    def mark_completed(self, post_id):
        """Mark a post as completed in both session state and persistent storage."""
        with self._lock:
            if post_id == "FETCHING":
                self.downloads.pop(post_id, None)
                return
            if post_id in self.downloads:
                self.downloads[post_id]["status"] = "completed"
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO completed_files (file_id, completed_at) VALUES (?, ?)",
                    (str(post_id), datetime.now(timezone.utc).isoformat()),
                )

    def mark_failed(self, post_id, error):
        """Mark a download as failed in session state."""
        with self._lock:
            if post_id in self.downloads:
                self.downloads[post_id]["status"] = "failed"
                self.downloads[post_id]["error"] = str(error)

    def is_completed(self, post_id):
        """Check if a file has already been downloaded (indexed lookup)."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM completed_files WHERE file_id = ? LIMIT 1",
                (str(post_id),),
            )
            return cursor.fetchone() is not None

    def completed_ids(self, post_ids) -> set[str]:
        """Return completed IDs in bounded SQLite batches."""
        identifiers = list(dict.fromkeys(str(post_id) for post_id in post_ids))
        completed: set[str] = set()
        with self._connect() as conn:
            for offset in range(0, len(identifiers), 900):
                batch = identifiers[offset : offset + 900]
                placeholders = ",".join("?" for _ in batch)
                rows = conn.execute(
                    f"SELECT file_id FROM completed_files WHERE file_id IN ({placeholders})",
                    batch,
                )
                completed.update(str(row[0]) for row in rows)
        return completed

    def get_progress(self, post_id):
        """Get current download progress from session state."""
        with self._lock:
            return dict(self.downloads.get(post_id, {}))

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def get_serializable_state(self):
        """Return JSON-serializable state for the web UI status endpoint."""
        with self._lock:
            return {
                "downloads": {
                    key: dict(value) for key, value in self.downloads.items()
                },
            }
