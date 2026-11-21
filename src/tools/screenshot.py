"""Screenshot tool for Assistant.

Captures the user screen or active window on Wayland (Hyprland via grim) or X11 (maim/import),
returning base64 image data and local path to enable multimodal vision analysis.
"""

import base64
import glob
import logging
import os
import shutil
import subprocess
import time
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger("assistant.tools.screenshot")

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "take_screenshot",
            "description": (
                "Capture a screenshot of the user's screen or active display to inspect, "
                "troubleshoot, or analyze visual errors, windows, UI elements, or contents "
                "currently displayed. Automatically hides and restores the assistant interface."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "enum": ["fullscreen", "active_window"],
                        "default": "fullscreen",
                        "description": "Area to capture: 'fullscreen' for the whole monitor or 'active_window' for the focused window.",
                    }
                },
            },
        },
    }
]


def _find_screenshot_binary(cmd_name: str) -> Optional[str]:
    """Find command binary in system PATH or Nix store."""
    found = shutil.which(cmd_name)
    if found:
        return found
    # Fallback search for NixOS environments if not directly in PATH
    for nix_bin in [
        f"/run/current-system/sw/bin/{cmd_name}",
        f"/etc/profiles/per-user/{os.environ.get('USER', 'root')}/bin/{cmd_name}",
    ]:
        if os.path.exists(nix_bin) and os.access(nix_bin, os.X_OK):
            return nix_bin

    # Search in nix store patterns if binary was not wrapped or linked yet
    store_matches = glob.glob(f"/nix/store/*{cmd_name}*/bin/{cmd_name}")
    if store_matches:
        for match in sorted(store_matches, reverse=True):
            if os.path.isfile(match) and os.access(match, os.X_OK):
                return match
    return None


def take_screenshot(
    target: str = "fullscreen",
    hide_window_callback: Optional[Callable[[], None]] = None,
    show_window_callback: Optional[Callable[[], None]] = None,
) -> Tuple[str, Optional[str], Optional[str]]:
    """Capture a screenshot, optionally hiding and restoring the Assistant GUI window.

    Returns:
        Tuple of (status_text, file_path, base64_image_data).
    """
    tmp_dir = "/tmp/assistant"
    try:
        os.makedirs(tmp_dir, exist_ok=True)
    except OSError as e:
        return f"Error creating temporary directory: {e}", None, None

    filename = f"screenshot_{int(time.time() * 1000)}.png"
    file_path = os.path.join(tmp_dir, filename)

    is_wayland = (
        os.environ.get("XDG_SESSION_TYPE") == "wayland"
        or bool(os.environ.get("WAYLAND_DISPLAY"))
    )

    # 1. Hide the Assistant window if a callback is provided
    if hide_window_callback is not None:
        try:
            hide_window_callback()
            # Allow the display compositor a brief moment to clear the buffer
            time.sleep(0.18)
        except Exception as e:
            logger.warning(f"Failed to execute hide_window_callback: {e}")

    cmd = []
    geom = None

    try:
        if is_wayland:
            grim_bin = _find_screenshot_binary("grim")
            if not grim_bin:
                return (
                    "Error: 'grim' is not installed or available in PATH for Wayland screen capture.",
                    None,
                    None,
                )

            if target == "active_window":
                hyprctl_bin = _find_screenshot_binary("hyprctl")
                jq_bin = _find_screenshot_binary("jq")
                if hyprctl_bin and jq_bin:
                    try:
                        res = subprocess.run(
                            f"{hyprctl_bin} -j activewindow | {jq_bin} -r '\"\\(.at[0]),\\(.at[1]) \\(.size[0])x\\(.size[1])\"'",
                            shell=True,
                            capture_output=True,
                            text=True,
                            timeout=2,
                        )
                        output_geom = res.stdout.strip()
                        if output_geom and output_geom != "null,null nullxnull":
                            geom = output_geom
                    except (subprocess.SubprocessError, OSError) as e:
                        logger.warning(f"Failed to query active window geometry: {e}")

            if geom:
                cmd = [grim_bin, "-g", geom, file_path]
            else:
                cmd = [grim_bin, file_path]
        else:
            # X11 fallback
            maim_bin = _find_screenshot_binary("maim")
            if maim_bin:
                if target == "active_window":
                    xdotool_bin = _find_screenshot_binary("xdotool")
                    if xdotool_bin:
                        cmd = [maim_bin, "-i", f"$({xdotool_bin} getactivewindow)", file_path]
                    else:
                        cmd = [maim_bin, file_path]
                else:
                    cmd = [maim_bin, file_path]
            else:
                import_bin = _find_screenshot_binary("import")
                if import_bin:
                    cmd = [import_bin, "-window", "root", file_path]
                else:
                    return (
                        "Error: Neither 'grim' nor 'maim'/'import' found for screenshot capture.",
                        None,
                        None,
                    )

        logger.info(f"Taking screenshot with command: {' '.join(cmd)}")
        if isinstance(cmd, list) and any("$" in part for part in cmd):
            # Command contains shell expansion
            result = subprocess.run(
                " ".join(cmd), shell=True, capture_output=True, text=True, timeout=5
            )
        else:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)

        if result.returncode != 0:
            err_msg = result.stderr.strip() or "Unknown capture error"
            logger.error(f"Screenshot failed: {err_msg}")
            return f"Screenshot error: {err_msg}", None, None

        if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
            return "Screenshot error: Output image file was not created or is empty.", None, None

        with open(file_path, "rb") as img_f:
            b64_data = base64.b64encode(img_f.read()).decode("utf-8")

        return (
            f"Screenshot successfully captured and saved to {file_path}. Inspecting image...",
            file_path,
            b64_data,
        )

    finally:
        # 2. Always restore the Assistant window if callback is provided
        if show_window_callback is not None:
            try:
                show_window_callback()
            except Exception as e:
                logger.warning(f"Failed to execute show_window_callback: {e}")
