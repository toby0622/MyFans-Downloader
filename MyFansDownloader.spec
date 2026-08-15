from pathlib import Path

root = Path.cwd()
ffmpeg_bin = root / ".build-tools" / "ffmpeg" / "bin"
ffmpeg_license = root / ".build-tools" / "ffmpeg" / "COPYING.LGPLv2.1"
license_dir = root / ".build-tools" / "licenses"
required_files = [
    ffmpeg_bin / "ffmpeg.exe",
    ffmpeg_bin / "ffprobe.exe",
    ffmpeg_license,
    license_dir / "README.md",
]
missing = [str(path) for path in required_files if not path.is_file()]
if missing:
    raise FileNotFoundError("Run packaging/prepare_ffmpeg.py first: " + ", ".join(missing))

datas = [
    (str(root / "src" / "myfans_downloader" / "ui"), "myfans_downloader/ui"),
    (str(root / "LICENSE"), "."),
    (str(root / "THIRD_PARTY_NOTICES.md"), "."),
    (str(ffmpeg_license), "ffmpeg"),
    (str(license_dir), "licenses"),
]
binaries = [
    (str(ffmpeg_bin / "ffmpeg.exe"), "ffmpeg/bin"),
    (str(ffmpeg_bin / "ffprobe.exe"), "ffmpeg/bin"),
]

a = Analysis(
    [str(root / "src" / "myfans_downloader" / "__main__.py")],
    pathex=[str(root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "flask",
        "tkinter",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "cefpython3",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="MyFansDownloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(root / "packaging" / "app.ico"),
    version=str(root / "packaging" / "version_info.txt"),
)
