# MyfansDownloader

A powerful, multi-threaded batch downloader for Myfans.jp with a stunning, Swiss brutalist-style web GUI. It allows you to reliably download both videos and images from user profiles, track progress in real-time, and easily abort tasks.

## Features

- 🚀 **Blazing Fast** — Multi-threaded concurrent downloading for M3U8 video segments and image batches.
- 🎬 **Auto FFmpeg Setup** — Automatically downloads and configures FFmpeg on first run (Windows & Linux). macOS users can install via Homebrew.
- 🎨 **Premium Brutalist Web GUI** — A visually striking dark-mode interface built with CSS Grid and modern design principles.
- 📊 **Real-time Queue & Progress** — Monitor fetch progress and individual file segment downloads via Server-Sent Events (SSE) with keepalive heartbeat.
- ⏹️ **Safe Interruption** — A global cancellation mechanism that lets you hit "STOP" at any time to gracefully halt all backend threads.
- 🔐 **Token Integration** — Manages API tokens directly via the settings UI with masked display for security.
- 💾 **SQLite Persistence** — Tracks completed downloads in a lightweight SQLite database to prevent redundant re-downloads, even across restarts.
- 🔒 **Concurrent Download Protection** — Prevents double-triggering of downloads with a server-side lock.

<img width="3416" height="1694" alt="image" src="https://github.com/user-attachments/assets/cf8415f3-7edb-4285-adb0-4e807b5cff19" />

## File Structure

```
MyfansDownloader/
├── app.py                   # Main Flask application (web routes & SSE progress)
├── requirements.txt         # Python dependencies
├── README.md                # Project documentation
├── LICENSE                  # MIT License
├── .gitignore               # Git ignored files
├── config/                  # Runtime configuration (Git ignored)
│   ├── config.ini           # User settings and API tokens
│   └── download_state.db   # SQLite database tracking completed downloads
├── scripts/                 # Core backend logic
│   ├── myfans_dl.py         # Primary download engine
│   ├── download_state.py    # SQLite-based download state manager
│   └── ffmpeg_downloader.py # Cross-platform auto FFmpeg setup
├── static/                  # Static web assets
│   ├── css/style.css        # Brutalist theme styles
│   └── favicon.svg          # Site favicon
└── templates/               # Flask HTML templates
    ├── base.html            # Base template
    ├── index.html           # Main download interface
    └── settings.html        # Settings page
```

## Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/toby0622/MYFANS-DOWNLOADER.git
   cd MYFANS-DOWNLOADER
   ```

2. **Install dependencies:**

   Make sure you have **Python 3.10+** installed.
   ```bash
   pip install -r requirements.txt
   ```

3. **FFmpeg** (handled automatically):
   - **Windows / Linux** — FFmpeg is downloaded and configured automatically on first run.
   - **macOS** — Install manually via Homebrew:
     ```bash
     brew install ffmpeg
     ```

## Usage

1. **Start the Web Server:**
   ```bash
   python app.py
   ```

2. **Access the GUI:**

   Open your browser and navigate to `http://127.0.0.1:5000`

3. **Configure Settings:**

   Click the **SETTINGS** button in the top right corner. Enter your `Auth Token`.

   > **How to get your Auth Token:** Log into myfans.jp → press `F12` to open Developer Tools → go to the **Network** tab → click on any API request → look for the `Authorization` header → copy the value after `Token token=`.

4. **Start Downloading:**
   - Enter the creator's **Username**.
   - Select what you want to download (**Videos** or **Images**).
   - Select your download mode (**Free**, **Subscribed**, or **All**).
   - Click **DOWNLOAD**.

   The **FETCH PROGRESS** and **ACTIVE QUEUE** panels will update in real time as files are resolved and downloaded to the `downloads/` folder.

## Architecture

```
Browser (index.html)
   │
   ├── POST /download  →  app.py  →  myfans_dl.start_download()
   │                          │              │
   │                          │              ├── Fetches post list from API
   │                          │              ├── Downloads M3U8 segments (ThreadPoolExecutor)
   │                          │              └── Merges via FFmpeg → .mp4
   │                          │
   ├── GET  /progress   →  SSE stream (with heartbeat keepalive)
   │
   └── GET  /status     →  DownloadState.get_serializable_state()
                                    │
                                    └── SQLite: config/download_state.db
```

## Disclaimer

This project is for educational purposes only. Please respect the copyright of the content creators and adhere to the terms of service of the platform.
