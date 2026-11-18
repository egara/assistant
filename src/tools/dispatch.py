"""Agentic tool dispatcher for Assistant.

Maps tool names to corresponding execution functions and evaluates
silent/active completion policies.
"""

from typing import Any, Callable, Dict, Optional, Tuple

from tools.hyprland import manage_desktop
from tools.knowledge import get_system_knowledge
from tools.obsidian import read_obsidian, search_obsidian, write_obsidian
from tools.os import (
    execute_command as os_execute_command,
    open_application,
    read_file,
    replace_in_file,
    write_file,
)
from tools.time import get_current_time
from tools.weather import get_weather
from tools.web import perform_web_search


def dispatch_tool(
    tool_name: str,
    args: Dict[str, Any],
    execute_command_safe: Optional[Callable[[str], str]] = None,
    mcp_manager: Optional[Any] = None,
    command_intents: Optional[Dict[str, list]] = None,
) -> Tuple[str, bool]:
    """Execute a single tool call by name.

    Returns a tuple ``(results_text, is_silent)``. ``is_silent`` marks desktop
    operations that succeeded without user-facing output (app launches, volume
    or brightness changes, window management), which let the generator finish
    immediately without extra conversational chatter.
    """
    if execute_command_safe is None:
        execute_command_safe = os_execute_command

    if tool_name == "get_current_time":
        return get_current_time(args.get("timezone", "")), False
    if tool_name == "open_application":
        results = open_application(args.get("app", ""), args.get("target", ""))
        return results, results.startswith("Application launched in background:")
    if tool_name == "get_weather":
        return get_weather(args.get("location", "")), False
    if tool_name == "web_search":
        return perform_web_search(args.get("query", ""), mcp_manager=mcp_manager), False
    if tool_name == "read_file":
        return read_file(args.get("path", "")), False
    if tool_name == "write_file":
        return write_file(args.get("path", ""), args.get("content", "")), True
    if tool_name == "replace_in_file":
        return (
            replace_in_file(
                args.get("path", ""),
                args.get("target", ""),
                args.get("replacement", ""),
            ),
            True,
        )
    if tool_name == "obsidian_search":
        return search_obsidian(args.get("query", "")), False
    if tool_name == "obsidian_read":
        return read_obsidian(args.get("filename", "")), False
    if tool_name == "obsidian_write":
        return write_obsidian(args.get("filename", ""), args.get("content", "")), True
    if tool_name == "manage_desktop":
        results = manage_desktop(
            args.get("action", ""),
            args.get("target", ""),
            args.get("window_address", ""),
        )
        return results, results.startswith("Success")
    if tool_name == "get_system_knowledge":
        return get_system_knowledge(args.get("topic", "index")), False
    if tool_name == "execute_command":
        cmd = args.get("command", "")
        results = execute_command_safe(cmd)
        cmd_clean = cmd.strip()
        parts = cmd_clean.split()
        is_app_launch = results.startswith("Application launched in background:")
        intents = command_intents or {}
        intent_tokens = intents.get(parts[0], []) if len(parts) > 1 else []
        is_action = any(parts[1].startswith(tok) for tok in intent_tokens)
        no_error = "error" not in results.lower()
        silent = is_app_launch or (is_action and no_error)
        return results, silent
    if mcp_manager is not None and hasattr(mcp_manager, "tools") and tool_name in mcp_manager.tools:
        return mcp_manager.call_tool(tool_name, args), False

    return "Done.", False
