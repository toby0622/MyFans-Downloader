<p align="center">
  <img src="packaging/app-icon.png" width="128" height="128" alt="MyFans Downloader icon">
</p>

<h1 align="center">MyFans Downloader</h1>

<p align="center">
  A private-by-default Windows desktop application for downloading MyFans
  content that you are authorized to access.
</p>

<p align="center">
  <a href="https://github.com/toby0622/MyFans-Downloader/releases/latest"><img src="https://img.shields.io/github/v/release/toby0622/MyFans-Downloader" alt="Latest release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/toby0622/MyFans-Downloader" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/platform-Windows%2010%2F11-0078D4" alt="Windows 10 and 11">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB" alt="Python 3.10 or newer">
</p>

> [!IMPORTANT]
> Use this application only for content you own or are authorized to download.
> Respect creator rights, applicable law, and the MyFans terms of service. This
> project is not affiliated with or endorsed by MyFans.

## Download

Download the current Windows build from
[GitHub Releases](https://github.com/toby0622/MyFans-Downloader/releases/latest):

- `MyFansDownloader.exe` — standalone 64-bit Windows application.
- `MyFansDownloader.exe.sha256` — SHA-256 checksum for the executable.
- `THIRD_PARTY_LICENSES.zip` — license texts for bundled components.

No Python installation or separate FFmpeg installation is required for the
release executable.

### Verify the download

Place both release files in the same directory, open PowerShell there, and run:

```powershell
$expected = (Get-Content .\MyFansDownloader.exe.sha256).Split()[0].ToUpperInvariant()
$actual = (Get-FileHash -Algorithm SHA256 .\MyFansDownloader.exe).Hash
$actual -eq $expected
```

The command must return `True` before you run the executable.

> [!WARNING]
> Version `1.0.0` is not code-signed. Windows SmartScreen may therefore show an
> unknown-publisher warning. Verify the SHA-256 checksum and run the file only
> if you trust this repository. You can also build the executable from source.

## Features

- Native Windows application window powered by Microsoft Edge WebView2.
- Single-file executable with embedded HTML/CSS/JavaScript, FFmpeg, and FFprobe.
- No Flask server, localhost port, external browser UI, or hosted fallback.
- Versioned MyFans API boundary with centralized endpoints, headers, retries,
  and timeouts.
- Video quality selection and concurrent HLS segment downloads.
- Concurrent image downloads with atomic file replacement.
- Strict media checks: incomplete HLS segment sets are never merged.
- SQLite completion index to avoid downloading completed posts again.
- Configurable filename format, worker count, output directory, and JSON
  metadata generation.
- Safe cancellation, bounded UI events, rotating logs, and single-instance
  protection.

## Security and privacy

The desktop UI is loaded from packaged local files and communicates with Python
only through the pywebview JavaScript bridge.

- The UI Content Security Policy includes `connect-src 'none'`; it cannot make
  network requests by itself.
- The authorization token is never returned to the UI after it is saved.
- The token is encrypted at rest with Windows DPAPI and can be decrypted only by
  the same Windows user account.
- Tokens are not passed through process-environment variables.
- Credentials and signed URL query strings are redacted before log output.
- Writable data is stored outside the executable and PyInstaller extraction
  directory.
- Private runtime files, logs, downloads, build caches, and release binaries are
  excluded by `.gitignore`.

Never upload `config.ini`, `.runtime/`, `config/`, `downloads/`, or application
logs to an issue or commit. See [SECURITY.md](SECURITY.md) for reporting and
credential-handling guidance.

## System requirements

- 64-bit Windows 10 or Windows 11.
- Microsoft Edge WebView2 Runtime.
- A MyFans account and an authorization token for content you are permitted to
  access.

WebView2 is normally included with supported Windows installations. If the
application reports that WebView2 is unavailable, install or repair the runtime
from Microsoft before trying again.

## Using the application

1. Start `MyFansDownloader.exe`.
2. Open **Settings**.
3. Enter your authorization token and choose a download directory.
4. Save the configuration.
5. Return to **Content**, select video or image mode, and start the download.

For a single post, select **Download Single Item** and enter the post ID. For a
creator archive, enter the username without `@` and select the required filter.

## Local data

Packaged application data is stored in:

```text
%LOCALAPPDATA%\MyFansDownloader\
├── config.ini              # settings; token protected with Windows DPAPI
├── download_state.db       # completed-post index
├── myfans_downloader.log   # rotating application log
└── webview\                # private WebView profile
```

The default download directory is:

```text
%USERPROFILE%\Downloads\MyFansDownloader\
```

When running from source, private application data uses the ignored `.runtime\`
directory and downloads use the ignored `downloads\` directory.

## Run from source

```powershell
git clone https://github.com/toby0622/MyFans-Downloader.git
cd MyFans-Downloader
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m myfans_downloader
```

Running from source still opens the native application window. It does not start
an HTTP service.

## Build the Windows executable

```powershell
.\build.ps1
```

The build script:

1. Creates an isolated `.venv-build` environment.
2. Installs the pinned runtime and build dependencies.
3. Downloads the redistributable LGPL FFmpeg build.
4. Verifies FFmpeg against its published SHA-256 manifest.
5. Generates matching SVG, PNG, favicon, and multi-resolution Windows icons.
6. Builds the single-file application with PyInstaller.
7. Writes the executable checksum to
   `dist\MyFansDownloader.exe.sha256`.
8. Collects the exact dependency license texts used by the build.

Outputs:

```text
dist\
├── MyFansDownloader.exe
├── MyFansDownloader.exe.sha256
├── LICENSE.txt
├── THIRD_PARTY_NOTICES.md
└── THIRD_PARTY_LICENSES.zip
```

For a repeat build using the existing local build environment:

```powershell
.\build.ps1 -SkipDependencyInstall
```

## Test

After installing the project in a virtual environment:

```powershell
.\.venv\Scripts\python.exe -m compileall -q src tests packaging
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The packaged executable also supports a non-interactive startup check used for
release verification:

```powershell
.\dist\MyFansDownloader.exe --self-test
if ($LASTEXITCODE -ne 0) { throw "Self-test failed" }
```

The self-test validates packaged UI assets, the native JavaScript bridge,
WebView2, DPAPI, FFmpeg, and FFprobe without showing the normal application
window.

## Architecture

```text
Packaged UI (file://, connect-src 'none')
                    │
                    │ native pywebview bridge
                    ▼
             DesktopApi (Python)
               │             │
               │             ├── DPAPI settings / SQLite state
               │             └── bounded progress event buffer
               ▼
        Centralized MyFans API layer
               │
               ├── image stream → atomic file replacement
               └── HLS segments → embedded FFmpeg → verified MP4
```

```text
src/myfans_downloader/
├── __main__.py          # module entry point
├── app.py               # native application lifecycle
├── desktop_api.py       # JavaScript bridge and task orchestration
├── downloader.py        # download and media-processing logic
├── myfans_api.py        # remote API boundary
├── settings_store.py    # validated and encrypted settings
├── download_state.py    # SQLite completion index
├── runtime_paths.py     # source and frozen path policy
├── ffmpeg.py            # bundled/runtime FFmpeg management
└── ui/                  # packaged HTML, CSS, JavaScript, and favicon
packaging/               # icon, version metadata, and build preparation
tests/                   # unit and architecture tests
MyFansDownloader.spec    # PyInstaller definition
build.ps1                # reproducible Windows build
```

MyFans API URLs exist only in `src/myfans_downloader/myfans_api.py`. A platform
API change therefore requires an application update and cannot be redirected by
an editable user URL setting.

## Known limitations

- The release build currently supports 64-bit Windows only.
- The executable is not code-signed and does not include an automatic updater.
- MyFans API or media-delivery changes may require a new application release.
- A DPAPI-protected token cannot be moved to another Windows account; enter the
  token again after changing accounts or computers.

## Changelog and licenses

- Release history: [CHANGELOG.md](CHANGELOG.md)
- Project license: [MIT License](LICENSE)
- Embedded dependency notices: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

FFmpeg and FFprobe remain separate executables licensed under LGPL 2.1 or later.
The build embeds the applicable FFmpeg license file.
