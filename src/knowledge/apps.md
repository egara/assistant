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

## 4. Terminal & System Monitors
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
