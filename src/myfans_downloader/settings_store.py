"""Validated, atomic persistence for application settings."""

from __future__ import annotations

import configparser
import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

from myfans_downloader.runtime_paths import RuntimePaths
from myfans_downloader.secure_storage import protect_secret, unprotect_secret

DEFAULT_FILENAME_PATTERN = "{creator}_{date}_{title}_{id}"
ALLOWED_FILENAME_FIELDS = {"{creator}", "{date}", "{title}", "{id}"}
logger = logging.getLogger(__name__)


class SettingsError(ValueError):
    pass


class SettingsStore:
    def __init__(self, paths: RuntimePaths):
        self.paths = paths
        self._lock = threading.RLock()
        self._ensure_config()

    def _defaults(self) -> dict[str, str]:
        return {
            "filename_pattern": DEFAULT_FILENAME_PATTERN,
            "filename_separator": "_",
            "auth_token": "",
            "thread_count": "10",
            "output_dir": str(self.paths.downloads_dir),
            "write_metadata": "0",
        }

    def _ensure_config(self) -> None:
        if self.paths.config_file.exists():
            return

        self._write(self._defaults())

    def _read(self) -> dict[str, str]:
        parser = configparser.ConfigParser()
        parser.read(self.paths.config_file, encoding="utf-8")
        defaults = self._defaults()
        section = parser["Settings"] if parser.has_section("Settings") else {}
        values = {key: str(section.get(key, value)) for key, value in defaults.items()}
        try:
            values["auth_token"] = unprotect_secret(values["auth_token"])
        except OSError as exc:
            logger.warning("Stored Auth Token could not be decrypted: %s", exc)
            values["auth_token"] = ""
        return values

    def _write(self, values: dict[str, str]) -> None:
        parser = configparser.ConfigParser()
        stored_values = dict(values)
        stored_values["auth_token"] = protect_secret(values["auth_token"])
        parser["Settings"] = stored_values
        parser["Filename"] = {
            "pattern": values["filename_pattern"],
            "separator": values["filename_separator"],
        }
        parser["Threads"] = {"threads": values["thread_count"]}

        self.paths.config_dir.mkdir(parents=True, exist_ok=True)
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                dir=self.paths.config_dir,
                prefix="config-",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                parser.write(temp_file)
                temp_file.flush()
                os.fsync(temp_file.fileno())
                temp_name = temp_file.name
            os.replace(temp_name, self.paths.config_file)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.remove(temp_name)

    @staticmethod
    def _validate_pattern(value: Any) -> str:
        pattern = str(value or "").strip()
        if not pattern or len(pattern) > 180:
            raise SettingsError(
                "Filename pattern must be between 1 and 180 characters."
            )
        fields = set(re.findall(r"\{[^{}]+\}", pattern))
        unknown_fields = fields - ALLOWED_FILENAME_FIELDS
        if unknown_fields:
            raise SettingsError(f"Unsupported filename field: {min(unknown_fields)}")
        without_fields = re.sub(r"\{[^{}]+\}", "", pattern)
        if "{" in without_fields or "}" in without_fields:
            raise SettingsError("Filename pattern contains an unmatched brace.")
        return pattern

    def _normalize_output_dir(self, value: Any) -> Path:
        raw = str(value or "").strip()
        if not raw:
            raise SettingsError("Download directory is required.")
        path = Path(os.path.expandvars(os.path.expanduser(raw)))
        if not path.is_absolute():
            path = self.paths.data_dir / path
        path = path.resolve()
        path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            raise SettingsError("Download directory is not a directory.")
        return path

    def get(self, *, include_token: bool = False) -> dict[str, Any]:
        with self._lock:
            values = self._read()
        try:
            thread_count = max(1, min(32, int(values["thread_count"])))
        except ValueError:
            thread_count = 10
        write_metadata = 1 if values["write_metadata"] == "1" else 0
        result: dict[str, Any] = {
            "filename_pattern": values["filename_pattern"],
            "filename_separator": values["filename_separator"],
            "thread_count": thread_count,
            "output_dir": values["output_dir"],
            "write_metadata": write_metadata,
            "auth_token_set": bool(values["auth_token"].strip()),
        }
        if include_token:
            result["auth_token"] = values["auth_token"]
        return result

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise SettingsError("Invalid settings payload.")

        with self._lock:
            current = self._read()
            try:
                thread_count = int(payload.get("thread_count", current["thread_count"]))
            except (TypeError, ValueError) as exc:
                raise SettingsError("Thread count must be a number.") from exc
            if not 1 <= thread_count <= 32:
                raise SettingsError("Thread count must be between 1 and 32.")

            separator = str(
                payload.get("filename_separator", current["filename_separator"])
            )
            if len(separator) > 8 or any(char in '<>:"/\\|?*' for char in separator):
                raise SettingsError("Filename separator is invalid.")

            write_metadata = str(
                payload.get("write_metadata", current["write_metadata"])
            )
            if write_metadata not in {"0", "1"}:
                raise SettingsError("Metadata setting must be 0 or 1.")

            output_dir = self._normalize_output_dir(
                payload.get("output_dir", current["output_dir"])
            )
            values = {
                "filename_pattern": self._validate_pattern(
                    payload.get("filename_pattern", current["filename_pattern"])
                ),
                "filename_separator": separator,
                "auth_token": current["auth_token"],
                "thread_count": str(thread_count),
                "output_dir": str(output_dir),
                "write_metadata": write_metadata,
            }

            new_token = str(payload.get("auth_token", "")).strip()
            if new_token:
                values["auth_token"] = new_token
            self._write(values)

        return self.get()
