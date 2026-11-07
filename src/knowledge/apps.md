# Applications & System Tools in SicOS

All desktop applications should be opened using `open_application(app='...', target='...')`.
`open_application` automatically launches GUIs via `uwsm app --` and wraps TUIs in `kitty`.

## 1. File Managers
- **`nautilus`** (GUI): Graphical file browser.
- **`yazi`** (TUI): Fast asynchronous terminal file manager. Usage: `open_application(app='yazi', target='~/Downloads')`.

## 2. Web Browsers
- **`firefox`** (GUI): Default web browser.
- **`google-chrome`** / **`chromium`** (GUI): Alternative web browser.

## 3. Editors & IDEs
- **`zed`** / **`zeditor`** (GUI): High-performance code editor.
- **`neovim`** / **`nvim`** (TUI): Modern extensible vim editor in terminal.
- **`obsidian`** (GUI): Markdown knowledge base and note-taking vault.

## 4. Media & Audio/Video Players
- **`xdg-open`** (System Default): The primary and recommended method to open any document, audio file, or video file. SicOS automatically maps file types to default apps (`qmmp` for MP3/audio, `vlc` for video, `papers` for PDF, `feh` for images, `zed` for code/text).
- **`qmmp`** (GUI): Default audio and music player (Winamp-like lightweight player configured for `audio/mpeg`).
- **`vlc`** (GUI): Default media player for video streams and movie files (`video/mp4`, `video/webm`, `video/x-matroska`).
- **`pw-play`** (CLI): Native PipeWire command-line audio player.
- **Notice**: `mpv` is NOT installed. To open/play media or files, call `open_application(app='xdg-open', target='<file_path>')` or `open_application(app='qmmp'|'vlc', target='<file_path>')`.

## 5. Terminal & System Monitors
- **`kitty`** (GUI): Default GPU-accelerated terminal emulator.
- **`btop`** (TUI): System resource and process monitor.
- **`nvtop`** (TUI): GPU resource monitor.
- **`herdr`** (TUI): Multi-server Docker and cluster manager.
- **`lazygit`** (TUI): Simple terminal UI for git commands.
- **`lazyssh`** (TUI): Terminal SSH connection manager.

## 5. Desktop Shell & Launchers
- **`walker`** (GUI): Application launcher and clipboard manager (`Super` or `Super+Ctrl+V`).
- **`wlogout`** (GUI): Session logout, shutdown, suspend, reboot screen.
- **SicOS Control Center / QuickShell**: Native QML desktop bar, clock, system tray, battery, and workspace indicators.
