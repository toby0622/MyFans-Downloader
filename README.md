# MyfansDownloader

A powerful, multi-threaded batch downloader for Myfans.jp with a stunning, Swiss brutalist-style web GUI. It allows you to reliably download both videos and images from user profiles, track progress in real-time, and easily abort tasks.

## Features
- 🚀 **Blazing Fast**: Multi-threaded concurrent downloading for m3u8 video segments and image batches.
- 🎨 **Premium Brutalist Web GUI**: A visually striking dark-mode interface built with CSS Grid and modern design principles.
- 📊 **Real-time Queue & Progress**: Monitor fetch progress and individual file segment downloads dynamically.
- ⏹️ **Safe Interruption**: A global cancellation mechanism that lets you hit "STOP" at any time to gracefully halt all backend threads.
- 🔐 **Token Integration**: Automatically manages API tokens directly via the settings UI.

## File Structure
```
MyfansDownloader/
├── app.py                   # Main Flask application
├── config.ini               # User settings and API tokens (Git ignored)
├── requirements.txt         # Python dependencies
├── README.md                # Project documentation
├── .gitignore               # Git ignored files
├── scripts/                 # Core backend logic
│   ├── myfans_dl.py         # Primary download engine
│   ├── download_state.py    # Download state manager for UI syncing
│   └── filename_utils.py    # Path sanitation and processing
├── static/                  # Static web assets
│   └── css/style.css        # Brutalist theme styles
└── templates/               # Flask HTML templates
    └── index.html           # Main web interface
```

## Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/yourusername/MyfansDownloader.git
   cd MyfansDownloader
   ```

2. **Install dependencies:**
   Make sure you have Python 3.8+ and `ffmpeg` installed.
   ```bash
   pip install -r requirements.txt
   ```
   *(Note: FFmpeg must be in your system PATH to process m3u8 video segments).*

## Usage

1. **Start the Web Server:**
   Run the following command to launch the Flask application:
   ```bash
   python app.py
   ```

2. **Access the GUI:**
   Open your browser and navigate to `http://127.0.0.1:5000`

3. **Configure Settings:**
   Click the **SETTINGS** button in the top right corner. Enter your `Auth Token` (obtained from your browser's network tab when logged into myfans.jp). 

4. **Start Downloading:**
   - Enter the creator's **Username**.
   - Select what you want to download (Videos or Images).
   - Select your download mode (Free, Subscribed, or All).
   - Click **DOWNLOAD**.

You will see the **FETCH PROGRESS** and **ACTIVE QUEUE** update in real time as files are resolved and downloaded to the `downloads/` folder.

## Disclaimer
This project is for educational purposes only. Please respect the copyright of the content creators and adhere to the terms of service of the platform.
