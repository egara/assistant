"""Tools package for Assistant."""

from .time import get_current_time, TOOL_DEFINITIONS as TIME_TOOLS
from .weather import get_weather, TOOL_DEFINITIONS as WEATHER_TOOLS
from .obsidian import (
    search_obsidian,
    read_obsidian,
    write_obsidian,
    configure_obsidian,
    TOOL_DEFINITIONS as OBSIDIAN_TOOLS,
)
from .web import (
    deep_web_search,
    perform_web_search,
    TOOL_DEFINITIONS as WEB_TOOLS,
)
from .os import (
    read_file,
    write_file,
    replace_in_file,
    open_application,
    execute_command,
    get_desktop_cache,
    resolve_fuzzy_target_path,
    TOOL_DEFINITIONS as OS_TOOLS,
)
from .knowledge import (
    get_system_knowledge,
    get_sicos_shortcuts,
    configure_knowledge,
    TOOL_DEFINITIONS as KNOWLEDGE_TOOLS,
)
from .hyprland import (
    get_hyprland_version,
    load_hyprland_recipes,
    save_user_hyprland_recipe,
    discover_and_learn_action,
    build_manage_desktop_tool,
    manage_desktop,
    configure_hyprland,
)
from .cinema import get_cinema_showtimes, TOOL_DEFINITIONS as CINEMA_TOOLS
from .dispatch import dispatch_tool

__all__ = [
    # Time
    "get_current_time",
    "TIME_TOOLS",
    # Weather
    "get_weather",
    "WEATHER_TOOLS",
    # Cinema
    "get_cinema_showtimes",
    "CINEMA_TOOLS",
    # Obsidian
    "search_obsidian",
    "read_obsidian",
    "write_obsidian",
    "configure_obsidian",
    "OBSIDIAN_TOOLS",
    # Web
    "deep_web_search",
    "perform_web_search",
    "WEB_TOOLS",
    # OS & Filesystem
    "read_file",
    "write_file",
    "replace_in_file",
    "open_application",
    "execute_command",
    "get_desktop_cache",
    "resolve_fuzzy_target_path",
    "OS_TOOLS",
    # Knowledge
    "get_system_knowledge",
    "get_sicos_shortcuts",
    "configure_knowledge",
    "KNOWLEDGE_TOOLS",
    # Hyprland
    "get_hyprland_version",
    "load_hyprland_recipes",
    "save_user_hyprland_recipe",
    "discover_and_learn_action",
    "build_manage_desktop_tool",
    "manage_desktop",
    "configure_hyprland",
    "dispatch_tool",
]
