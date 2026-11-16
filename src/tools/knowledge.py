"""Knowledge retrieval tools for SicOS modular docs and live Hyprland keybindings."""

import json
import os
import subprocess

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_system_knowledge",
            "description": "Consult SicOS modular knowledge or live Hyprland shortcuts. Available topics: 'index' (overview), 'hardware' (audio/volume with wpctl, brightness with brightnessctl, monitors), 'apps' (installed tools and whether GUI/TUI), 'hyprland' (desktop architecture & layouts), or 'shortcuts' (active keyboard shortcuts from compositor).",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Topic to consult: 'index', 'hardware', 'apps', 'hyprland', or 'shortcuts'.",
                    }
                },
                "required": ["topic"],
            },
        },
    }
]

_user_knowledge_dir = os.path.expanduser("~/.config/assistant/knowledge")
_system_knowledge_dir = ""


def configure_knowledge(user_dir=None, system_dir=None):
    """Configure knowledge base directory locations."""
    global _user_knowledge_dir, _system_knowledge_dir
    if user_dir:
        _user_knowledge_dir = user_dir
    if system_dir:
        _system_knowledge_dir = system_dir


def get_sicos_shortcuts():
    """Retrieve and format active Hyprland keybindings dynamically."""
    try:
        res = subprocess.run(["hyprctl", "binds", "-j"], capture_output=True, text=True, timeout=3)
        if res.returncode != 0:
            return "Could not retrieve keybindings from hyprctl."
        binds = json.loads(res.stdout)
        lines = []
        for b in binds:
            if not b.get("has_description") or "mouse" in b.get("dispatcher", "").lower():
                continue
            m = b.get("modmask", 0)
            mods = []
            if (m // 64) % 2 >= 1:
                mods.append("Super")
            if (m // 4) % 2 >= 1:
                mods.append("Ctrl")
            if (m // 8) % 2 >= 1:
                mods.append("Alt")
            if (m // 1) % 2 >= 1:
                mods.append("Shift")
            key = b.get("key", "")
            if key in ("SUPER_L", "SUPER_R"):
                key = "Super"
            elif not key:
                key = f"code:{b.get('keycode')}"

            if key not in mods:
                combo = "+".join(mods + [key]) if mods else key
            else:
                combo = "+".join(mods)
            desc = b.get("description", "Unknown action")
            lines.append(f"- **{combo}**: {desc}")

        unique_lines = sorted(list(set(lines)))
        return "# Hyprland Keybindings (Live from Compositor)\n\n" + "\n".join(unique_lines)
    except Exception as e:
        return f"Error reading shortcuts: {e}"


def get_system_knowledge(topic="index"):
    """Query modular Markdown knowledge base or live shortcuts."""
    topic_clean = topic.strip().lower()
    if topic_clean in ("shortcuts", "binds", "keybindings"):
        return get_sicos_shortcuts()

    # Topic file mapping
    valid_topics = {
        "index": "index.md",
        "hardware": "hardware.md",
        "audio": "hardware.md",
        "sound": "hardware.md",
        "volume": "hardware.md",
        "brightness": "hardware.md",
        "monitors": "hardware.md",
        "apps": "apps.md",
        "applications": "apps.md",
        "tools": "apps.md",
        "hyprland": "hyprland.md",
        "desktop": "hyprland.md",
        "windows": "hyprland.md",
    }

    target_filename = valid_topics.get(topic_clean, f"{topic_clean}.md")

    # 1. Check user knowledge (~/.config/assistant/knowledge/)
    user_file = os.path.join(_user_knowledge_dir, target_filename)
    if os.path.exists(user_file):
        try:
            with open(user_file, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception as e:
            return f"Error reading {user_file}: {e}"

    # 2. Check factory knowledge (src/knowledge/)
    if _system_knowledge_dir:
        factory_file = os.path.join(_system_knowledge_dir, target_filename)
        if os.path.exists(factory_file):
            try:
                with open(factory_file, "r", encoding="utf-8") as f:
                    return f.read().strip()
            except Exception as e:
                return f"Error reading {factory_file}: {e}"

    return (
        f"Topic '{topic}' not found. Available topics: 'index', 'hardware' (audio/brightness), "
        "'apps' (installed tools), 'hyprland' (desktop & layouts), 'shortcuts' (live keybindings)."
    )
