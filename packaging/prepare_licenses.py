"""Collect installed dependency licenses for the standalone release."""

from __future__ import annotations

import re
import shutil
import sys
from importlib.metadata import distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = (ROOT / ".build-tools" / "licenses").resolve()
REQUIRED_DISTRIBUTIONS = {"m3u8", "pywebview", "requests", "tqdm", "urllib3"}
LICENSE_NAMES = ("license", "licence", "copying", "notice")


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-") or "unknown"


def _license_files(distribution) -> list[Path]:
    files: list[Path] = []
    for entry in distribution.files or ():
        if any(name in entry.name.lower() for name in LICENSE_NAMES):
            source = Path(distribution.locate_file(entry)).resolve()
            if source.is_file():
                files.append(source)
    return files


def main() -> None:
    expected_root = (ROOT / ".build-tools").resolve()
    if expected_root not in OUTPUT_DIR.parents:
        raise RuntimeError(f"Unsafe license output directory: {OUTPUT_DIR}")
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True)

    index_lines = [
        "# Bundled dependency licenses",
        "",
        "Generated from the exact Python environment used to build the executable.",
        "",
        "| Package | Version | Declared license |",
        "| --- | --- | --- |",
    ]
    discovered: set[str] = set()
    copied_count = 0

    for distribution in sorted(
        distributions(), key=lambda item: item.metadata.get("Name", "").lower()
    ):
        name = distribution.metadata.get("Name", "unknown")
        normalized_name = name.lower().replace("_", "-")
        version = distribution.version
        declared = distribution.metadata.get(
            "License-Expression"
        ) or distribution.metadata.get("License", "See included license files")
        if not declared or "\n" in declared:
            declared = "See included license files"

        license_files = _license_files(distribution)
        if not license_files:
            continue
        discovered.add(normalized_name)
        package_dir = OUTPUT_DIR / f"{_safe_name(name)}-{_safe_name(version)}"
        package_dir.mkdir()
        for index, source in enumerate(license_files, start=1):
            destination = package_dir / source.name
            if destination.exists():
                destination = package_dir / f"{index}-{source.name}"
            shutil.copy2(source, destination)
            copied_count += 1
        index_lines.append(f"| {name} | {version} | {declared} |")

    missing = REQUIRED_DISTRIBUTIONS - discovered
    if missing:
        raise RuntimeError(
            "Missing license files for required distributions: "
            + ", ".join(sorted(missing))
        )

    python_license = Path(sys.base_prefix) / "LICENSE_PYTHON.txt"
    if not python_license.is_file():
        raise FileNotFoundError(f"Python license file is missing: {python_license}")
    shutil.copy2(python_license, OUTPUT_DIR / "PYTHON_LICENSE.txt")
    copied_count += 1

    ffmpeg_license = ROOT / ".build-tools" / "ffmpeg" / "COPYING.LGPLv2.1"
    if not ffmpeg_license.is_file():
        raise FileNotFoundError(f"FFmpeg license file is missing: {ffmpeg_license}")
    shutil.copy2(ffmpeg_license, OUTPUT_DIR / "FFMPEG_COPYING.LGPLv2.1.txt")
    copied_count += 1

    (OUTPUT_DIR / "README.md").write_text(
        "\n".join(index_lines) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"Collected {copied_count} license and notice files in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
