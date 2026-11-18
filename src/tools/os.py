"""Operating system and filesystem tools for Assistant."""

import getpass
import os
import shutil
import subprocess

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the text contents of a file at the given absolute or relative path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to read (e.g. '~/prueba/ana.txt' or '/home/user/document.txt').",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file at the given path with new text content. Creates parent directories if needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Target file path (e.g. '~/prueba/resumen.txt').",
                    },
                    "content": {
                        "type": "string",
                        "description": "The exact full text content to write into the file.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_in_file",
            "description": "Replace occurrences of a target string or pattern in a file with replacement text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Target file path to modify (e.g. '~/prueba/ana.txt').",
                    },
                    "target": {
                        "type": "string",
                        "description": "The exact string or word to be replaced.",
                    },
                    "replacement": {
                        "type": "string",
                        "description": "The new replacement string.",
                    },
                },
                "required": ["path", "target", "replacement"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_application",
            "description": "Open or launch any desktop application, file, or URL. When opening a file or playing media (audio/video), pass the file path in 'target'; if 'app' is 'xdg-open' or an uninstalled tool (e.g. mpv), it automatically opens with your configured system default application (e.g. qmmp for audio, vlc for video). Terminal-based apps automatically run inside kitty, and all apps launch under uwsm.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app": {
                        "type": "string",
                        "description": "Application name (e.g. 'xdg-open', 'yazi', 'google-chrome', 'firefox', 'nautilus', 'zed', 'qmmp', 'vlc'). Use 'xdg-open' or leave default when opening files with their default association.",
                    },
                    "target": {
                        "type": "string",
                        "description": "Optional file path, directory path, or URL to open with the application (e.g. '~/Downloads', '~/Documents/report.pdf', 'https://github.com').",
                    },
                },
                "required": ["app"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_command",
            "description": "Execute a bash shell command. For GUI applications, open commands, or long-running processes, append '&' or invoke the command normally (they will run detached in background).",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
]

# Fast in-memory cache of desktop applications discovered from XDG_DATA_DIRS
_DESKTOP_CACHE = None


def get_desktop_cache():
    global _DESKTOP_CACHE
    if _DESKTOP_CACHE is not None:
        return _DESKTOP_CACHE

    cache = {}
    data_dirs = os.environ.get("XDG_DATA_DIRS", "").split(":")
    # Add standard NixOS profile paths if not in XDG_DATA_DIRS
    current_user = os.environ.get("USER") or getpass.getuser()
    extra_dirs = [
        os.path.expanduser("~/.nix-profile/share"),
        f"/etc/profiles/per-user/{current_user}/share",
        "/run/current-system/sw/share",
    ]
    for ed in extra_dirs:
        if ed not in data_dirs:
            data_dirs.append(ed)

    for d in data_dirs:
        apps_dir = os.path.join(d, "applications")
        if not os.path.isdir(apps_dir):
            continue
        try:
            for entry in os.listdir(apps_dir):
                if entry.endswith(".desktop"):
                    full_p = os.path.join(apps_dir, entry)
                    base_name = entry[:-8].lower()
                    try:
                        with open(full_p, "r", encoding="utf-8", errors="ignore") as fp:
                            content = fp.read()
                            is_terminal = "Terminal=true" in content
                            cache[base_name] = {"is_terminal": is_terminal, "path": full_p}
                    except OSError:
                        pass
        except OSError:
            pass

    _DESKTOP_CACHE = cache
    return _DESKTOP_CACHE


def resolve_fuzzy_target_path(raw_target):
    """Clean and resolve file paths even if the LLM hallucinated tildes or abbreviated intermediate folders."""
    if not raw_target:
        return ""
    target_clean = raw_target.strip().strip("'\"`")
    if target_clean.startswith(("http://", "https://")):
        return target_clean

    home = os.path.expanduser("~")
    # Fix broken concatenation like /home/egarcia~/ or /home/username/~/
    if target_clean.startswith(f"{home}~"):
        target_clean = home + target_clean[len(home) + 1:]
    elif target_clean.startswith(f"{home}/~"):
        target_clean = home + target_clean[len(home) + 2:]

    expanded = os.path.expanduser(target_clean)
    if os.path.exists(expanded):
        return expanded

    # If Gemma or another LLM abbreviated the path or simplified the filename (e.g. Mardom.mp3 instead of 'Mardom ft... .mp3')
    filename = os.path.basename(expanded).strip("_*` \t")
    name_stem, ext = os.path.splitext(filename)

    # Determine smartest search root: if path provided a valid folder hint (like ~/Downloads), search there first!
    parent_hint = os.path.dirname(expanded)
    search_root = parent_hint if (parent_hint and os.path.exists(parent_hint) and parent_hint != home) else home

    candidates = []
    # 1. First pass: exact filename match
    for root, dirs, files in os.walk(search_root):
        dirs[:] = [
            d for d in dirs
            if not d.startswith(".") and d not in (
                "node_modules", "target", "build", "dist", ".cache", ".git"
            )
        ]
        if filename in files:
            candidates.append(os.path.join(root, filename))
            if len(candidates) >= 5:
                break

    # If not found in parent hint, scan home for exact filename
    if not candidates and search_root != home:
        for root, dirs, files in os.walk(home):
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".") and d not in (
                    "node_modules", "target", "build", "dist", ".cache", ".git"
                )
            ]
            if filename in files:
                candidates.append(os.path.join(root, filename))
                if len(candidates) >= 5:
                    break

    # 2. Second pass: Substring / stem matching (e.g. user/LLM says 'Mardom.mp3' for 'Mardom ft Miss Blanche... .mp3')
    if not candidates and name_stem and len(name_stem) >= 3:
        for scan_dir in ([search_root] if search_root == home else [search_root, home]):
            for root, dirs, files in os.walk(scan_dir):
                dirs[:] = [
                    d for d in dirs
                    if not d.startswith(".") and d not in (
                        "node_modules", "target", "build", "dist", ".cache", ".git"
                    )
                ]
                for f in files:
                    f_stem, f_ext = os.path.splitext(f)
                    if ext and f_ext.lower() != ext.lower():
                        continue
                    if name_stem.lower() in f_stem.lower():
                        candidates.append(os.path.join(root, f))
                        if len(candidates) >= 10:
                            break
                if candidates:
                    break
            if candidates:
                break

    if len(candidates) == 1:
        return candidates[0]
    elif len(candidates) > 1:
        # Score candidates based on folder/name tokens mentioned in the prompt path
        tokens = [t.strip("_*` \t").lower() for t in target_clean.replace("...", "/").split("/") if len(t) > 2]
        best_cand = candidates[0]
        max_score = -1
        for cand in candidates:
            cand_lower = cand.lower()
            score = sum(1 for tok in tokens if tok in cand_lower)
            if score > max_score:
                max_score = score
                best_cand = cand
        return best_cand

    return expanded


def read_file(path):
    try:
        resolved = os.path.expanduser(path.strip())
        if not os.path.exists(resolved):
            return f"Error: File '{resolved}' does not exist."
        if not os.path.isfile(resolved):
            return f"Error: '{resolved}' is not a regular file."
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(50000)
        return f"--- File: {resolved} ---\n{content}"
    except Exception as e:
        return f"Error reading file '{path}': {e}"


def write_file(path, content):
    try:
        resolved = os.path.expanduser(path.strip())
        parent_dir = os.path.dirname(resolved)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Success: File '{resolved}' written successfully ({len(content)} characters)."
    except Exception as e:
        return f"Error writing file '{path}': {e}"


def replace_in_file(path, target, replacement):
    try:
        resolved = os.path.expanduser(path.strip())
        if not os.path.exists(resolved):
            return f"Error: File '{resolved}' does not exist."
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        if target not in text:
            return f"Warning: Target text '{target}' was not found in '{resolved}'."
        count = text.count(target)
        new_text = text.replace(target, replacement)
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(new_text)
        return f"Success: Replaced {count} occurrence(s) of '{target}' in '{resolved}'."
    except Exception as e:
        return f"Error replacing text in '{path}': {e}"


def open_application(app, target=""):
    """Universally launch any application with optional target (file, directory, URL).
    Prioritizes xdg-open for files when the specified app is not installed or when opening files/URLs.
    """
    try:
        app_clean = (app or "").strip().lower()
        target_str = (target or "").strip()

        # Resolve target argument (cleaning tildes, markdown ellipses, or truncated paths)
        target_arg = ""
        resolved_target = ""
        if target_str:
            resolved_target = resolve_fuzzy_target_path(target_str)
            target_arg = f"'{resolved_target}'"

        # Map common aliases to real binary names
        aliases = {
            "chrome": "google-chrome-stable",
            "google-chrome": "google-chrome-stable",
            "zed": "zeditor",
            "code": "code",
            "terminal": "kitty",
            "files": "nautilus",
            "browser": "firefox",
        }
        binary = aliases.get(app_clean, app_clean)

        # Generic requests or missing specific player (e.g. mpv not installed)
        generic_app_names = {
            "", "default", "xdg-open", "open", "media", "player", "media_player",
            "reproductor", "reproduce", "audio", "video", "music", "musica"
        }

        # Check if the requested binary is actually available on the system PATH or in desktop cache
        desktop_cache = get_desktop_cache()
        is_binary_available = bool(shutil.which(binary)) or (binary in desktop_cache)

        # 1. PRIMARY ROUTE: If opening a file/URL and either the requested app is generic,
        # or the requested binary does NOT exist on the system (e.g. mpv), route directly via xdg-open.
        # This respects NixOS xdg.mimeApps.defaultApplications (e.g. qmmp for audio, vlc for video, zed, papers).
        if target_str and (binary in generic_app_names or not is_binary_available):
            if shutil.which("xdg-open"):
                cmd = f"uwsm app -- xdg-open {target_arg}".strip()
                subprocess.Popen(
                    cmd,
                    shell=True,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                )
                return f"Application launched in background (via xdg-open): {cmd}"

        # 2. If no target was given and the app is not installed, report clearly
        if not is_binary_available:
            return f"Error: Application '{app}' is not installed on this system."

        # Check if the app is a TUI app (via desktop entry Terminal=true or common list)
        known_tui = {"yazi", "btop", "htop", "lazygit", "lazyssh", "herdr", "nvtop", "ranger", "neovim", "nvim"}
        is_terminal_app = binary in known_tui
        if not is_terminal_app:
            for name, meta in desktop_cache.items():
                if binary == name or binary in name:
                    if meta.get("is_terminal"):
                        is_terminal_app = True
                    break

        # Build final execution command
        if is_terminal_app:
            cmd = f"uwsm app -- kitty {binary} {target_arg}".strip()
        else:
            cmd = f"uwsm app -- {binary} {target_arg}".strip()

        subprocess.Popen(
            cmd,
            shell=True,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        return f"Application launched in background: {cmd}"
    except Exception as e:
        return f"Error launching application '{app}': {e}"


def execute_command(command):
    try:
        cmd_str = command.strip().rstrip("&").strip()

        # Terminal UI (TUI) applications that require a terminal emulator (Kitty) to display
        tui_apps = [
            "yazi",
            "btop",
            "htop",
            "lazygit",
            "lazyssh",
            "herdr",
            "nvtop",
            "ranger",
            "neovim",
            "nvim",
        ]

        # Common GUI applications intended to run as detached desktop processes
        gui_launchers = [
            "google-chrome",
            "google-chrome-stable",
            "chromium",
            "firefox",
            "nautilus",
            "kitty",
            "vlc",
            "mpv",
            "xdg-open",
            "gtk-launch",
            "code",
            "zed",
            "zeditor",
            "spotify",
            "steam",
            "discord",
            "telegram-desktop",
            "obsidian",
        ]

        # 1. Check if it's a TUI app that needs to be launched inside Kitty
        # Also clean invalid flags invented by LLMs (e.g. yazi does not have '-d', it takes paths positionally)
        if "yazi -d " in cmd_str:
            cmd_str = cmd_str.replace("yazi -d ", "yazi ")

        is_tui = any(
            cmd_str == app or cmd_str.startswith(f"{app} ")
            for app in tui_apps
        )
        if is_tui:
            cmd_str = f"kitty {cmd_str}"

        # 2. Check if it is a desktop app (GUI or wrapped TUI) that should launch under uwsm app
        is_desktop_app = (
            cmd_str.startswith("uwsm app --")
            or is_tui
            or any(
                cmd_str == app
                or cmd_str.startswith(f"{app} ")
                or f" {app} " in cmd_str
                for app in gui_launchers
            )
        )

        if is_desktop_app:
            # Wrap with 'uwsm app --' if on Hyprland/Wayland and not already prefixed
            if not cmd_str.startswith("uwsm app --"):
                cmd_str = f"uwsm app -- {cmd_str}"

            # Launch detached in a new session and redirect std streams
            subprocess.Popen(
                cmd_str,
                shell=True,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            return f"Application launched in background: {cmd_str}"

        # 3. Regular non-GUI command execution
        res = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=10
        )
        combined = (res.stdout + res.stderr).strip()
        if not combined:
            if res.returncode == 0:
                return "Command executed successfully with no errors (exit code 0)."
            else:
                return f"Command finished with exit code {res.returncode}."
        return combined[:3000]
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after 10 seconds. If launching a GUI app, please specify running it in the background."
    except Exception as e:
        return f"Error: {e}"
