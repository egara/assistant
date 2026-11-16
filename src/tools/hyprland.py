"""Hyprland window and workspace management tools for Assistant."""

import json
import os
import re
import subprocess

_system_knowledge_file = None
_user_knowledge_file = os.path.expanduser("~/.local/share/assistant/hyprland_knowledge.json")


def configure_hyprland(system_knowledge_file=None, user_knowledge_file=None):
    """Configure Hyprland recipe JSON file locations."""
    global _system_knowledge_file, _user_knowledge_file
    if system_knowledge_file is not None:
        _system_knowledge_file = system_knowledge_file
    if user_knowledge_file is not None:
        _user_knowledge_file = user_knowledge_file


def get_hyprland_version():
    """Detect current Hyprland version (e.g. '0.57.0' -> '0.57')."""
    try:
        res = subprocess.run(["hyprctl", "version"], capture_output=True, text=True, timeout=2)
        if res.returncode == 0:
            m = re.search(r"Hyprland\s+([0-9]+\.[0-9]+)", res.stdout)
            if m:
                return m.group(1)
    except:
        pass
    return "default"


def load_hyprland_recipes():
    """Load and merge Hyprland recipes (Factory system recipes + User mutable recipes)."""
    merged = {}
    # 1. System/factory recipes (base)
    if _system_knowledge_file and os.path.exists(_system_knowledge_file):
        try:
            with open(_system_knowledge_file, "r", encoding="utf-8") as f:
                merged = json.load(f)
        except Exception as e:
            print(f"[Hyprland Knowledge] Error loading system recipes: {e}")

    # 2. User mutable recipes in ~/.local/share/assistant (highest priority)
    if _user_knowledge_file and os.path.exists(_user_knowledge_file):
        try:
            with open(_user_knowledge_file, "r", encoding="utf-8") as f:
                user_recipes = json.load(f)
                for ver, acts in user_recipes.items():
                    if ver.startswith("_"):
                        # Allow the user layer to override KB metadata (e.g. _command_intents)
                        if ver == "_command_intents":
                            merged[ver] = acts
                        continue
                    if ver not in merged:
                        merged[ver] = {}
                    merged[ver].update(acts)
        except Exception as e:
            print(f"[Hyprland Knowledge] Error loading user recipes: {e}")

    return merged


def save_user_hyprland_recipe(version, action, recipe_dict):
    """Persist newly learned recipe in mutable ~/.local/share/assistant/hyprland_knowledge.json."""
    try:
        user_data = {}
        if _user_knowledge_file and os.path.exists(_user_knowledge_file):
            try:
                with open(_user_knowledge_file, "r", encoding="utf-8") as f:
                    user_data = json.load(f)
            except:
                user_data = {}

        if version not in user_data:
            user_data[version] = {}
        user_data[version][action] = recipe_dict

        if _user_knowledge_file:
            os.makedirs(os.path.dirname(_user_knowledge_file), exist_ok=True)
            with open(_user_knowledge_file, "w", encoding="utf-8") as f:
                json.dump(user_data, f, indent=2)
        return True
    except Exception as e:
        print(f"[Hyprland Knowledge] Failed to save recipe: {e}")
        return False


def discover_and_learn_action(action, version):
    """Introspect Hyprland hl.dsp dispatchers in real time and learn unknown actions."""
    try:
        norm = action.lower().replace("-", "_").replace(" ", "_")
        script = 'local t = {}; for k, v in pairs(hl.dsp) do if type(v) == "function" then table.insert(t, k) elseif type(v) == "table" then for sk, sv in pairs(v) do if type(sv) == "function" then table.insert(t, k .. "." .. sk) end end end end; return table.concat(t, ",")'
        res = subprocess.run(["hyprctl", "repl", script], capture_output=True, text=True, timeout=2)
        if res.returncode != 0:
            return None

        candidates = [c.strip() for c in res.stdout.split(",") if c.strip()]
        matched_call = None
        for cand in candidates:
            cand_simple = cand.replace(".", "_")
            if norm == cand_simple or norm in cand_simple:
                matched_call = cand
                break

        if not matched_call:
            # Common synonyms mapping
            synonyms = {
                "center": "window.center",
                "center_window": "window.center",
                "pin": "window.pin",
                "float": "window.float",
                "floating": "window.float",
                "tile": "window.float",
                "fullscreen": "window.fullscreen",
                "kill": "window.close",
                "kill_window": "window.close",
                "close": "window.close",
                "bring_to_top": "window.bring_to_top",
                "toggle_special": "workspace.toggle_special",
                "special_workspace": "workspace.toggle_special",
                "group_toggle": "group.toggle",
                "toggle_group": "group.toggle",
            }
            if norm in synonyms and synonyms[norm] in candidates:
                matched_call = synonyms[norm]

        if matched_call:
            if matched_call.startswith("window."):
                fn_name = matched_call.split("window.", 1)[1]
                recipe = {
                    "type": "lua_repl",
                    "script": (
                        "local target_win = nil\n"
                        "local id = [[{window}]]\n"
                        "if id ~= '' then\n"
                        "  for _, w in ipairs(hl.get_windows()) do\n"
                        "    if w.address == id or w.class:lower():find(id:lower(), 1, true) or w.title:lower():find(id:lower(), 1, true) then\n"
                        "      target_win = w\n"
                        "      break\n"
                        "    end\n"
                        "  end\n"
                        "end\n"
                        "if target_win then\n"
                        "  hl.dispatch(hl.dsp.focus({ window = target_win }))\n"
                        f"  local res = hl.dispatch(hl.dsp.window.{fn_name}())\n"
                        "  return res and res.ok or false\n"
                        "else\n"
                        f"  local res = hl.dispatch(hl.dsp.window.{fn_name}())\n"
                        "  return res and res.ok or false\n"
                        "end"
                    ),
                    "description": f"Auto-discovered window dispatcher {matched_call}",
                }
            else:
                recipe = {
                    "type": "lua_eval",
                    "command": f"return hl.dispatch(hl.dsp.{matched_call}()).ok",
                    "description": f"Auto-discovered dispatcher {matched_call}",
                }
            save_user_hyprland_recipe(version, action, recipe)
            return recipe

    except Exception as e:
        print(f"[Hyprland Auto-Discovery] Error: {e}")

    return None


def build_manage_desktop_tool(recipes):
    """Build the manage_desktop tool schema from the Hyprland knowledge base.

    The action enum and descriptions come from the recipe KB, so new or
    user-learned actions become available to the model without code changes.
    """
    actions = []
    for ver_key, ver_recipes in recipes.items():
        if ver_key.startswith("_") or not isinstance(ver_recipes, dict):
            continue
        for action, recipe in ver_recipes.items():
            if action.startswith("_"):
                continue
            desc = recipe.get("description", "") if isinstance(recipe, dict) else ""
            if not any(action == name for name, _ in actions):
                actions.append((action, desc))
    actions.sort()
    action_docs = "; ".join(
        f"'{name}' ({desc})" if desc else f"'{name}'" for name, desc in actions
    )
    return {
        "type": "function",
        "function": {
            "name": "manage_desktop",
            "description": (
                "Manage workspaces and windows on Hyprland using version-aware recipes. "
                f"Available actions: {action_docs}. "
                "IMPORTANT: If the user mentions a specific application or window (e.g. 'cierra chrome', "
                "'minimiza firefox', 'foco en firefox'), you MUST provide 'window_address' with the "
                "application name/class (e.g. 'chrome', 'firefox') so the action is applied to that "
                "specific window instead of the currently active/focused window."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [name for name, _ in actions],
                        "description": "Desktop action to perform.",
                    },
                    "target": {
                        "type": "string",
                        "description": "Target workspace number (e.g. '1', '2') or name. Required for workspace actions.",
                    },
                    "window_address": {
                        "type": "string",
                        "description": "Target application name, class, title, or address (e.g. 'chrome', 'google-chrome', 'firefox', 'kitty', '0x...'). When specified, targets and focuses this window before performing the action. If omitted, applies to the currently active window.",
                    },
                },
                "required": ["action"],
            },
        },
    }


def manage_desktop(action, target="", window_address=""):
    try:
        ver = get_hyprland_version()
        if ver == "default":
            # Check basic hyprctl presence
            check = subprocess.run(["hyprctl", "version"], capture_output=True, text=True, timeout=2)
            if check.returncode != 0:
                return "Blocked: Only Hyprland supported."

        recipes = load_hyprland_recipes()
        ver_recipes = recipes.get(ver) or recipes.get("default", {})
        recipe = ver_recipes.get(action)

        # Auto-discover if not currently in knowledge base
        learned_notice = ""
        if not recipe:
            discovered = discover_and_learn_action(action, ver)
            if discovered:
                recipe = discovered
                learned_notice = f" [Learned & saved to ~/.local/share/assistant/hyprland_knowledge.json]"
            else:
                return f"Action '{action}' could not be executed or auto-discovered on Hyprland (v{ver})."

        target_str = str(target or "").strip()
        ws_param = target_str if target_str.isdigit() else f"[[{target_str}]]"
        win_param = str(window_address or "").strip()

        recipe_type = recipe.get("type")
        if recipe_type == "lua_eval":
            code = recipe.get("command", "").replace("{target}", ws_param).replace("{window}", win_param)
            res = subprocess.run(["hyprctl", "eval", code], capture_output=True, text=True, timeout=3)
            # hyprctl eval ALWAYS exits 0, even when the Lua script fails or the
            # dispatch result is false (e.g. 'Invalid workspace' warnings), so the
            # process exit code is not an honest success signal. Warnings/errors in
            # the output are the only reliable failure indicator.
            output = (res.stdout + res.stderr).lower()
            success = res.returncode == 0 and "warning" not in output and "error" not in output
            if success:
                return f"Success on Hyprland (v{ver}).{learned_notice}"
            return f"Failed: {(res.stdout + res.stderr).strip() or 'unknown error'}"

        elif recipe_type == "lua_repl":
            code = recipe.get("script", "").replace("{target}", ws_param).replace("{window}", win_param)
            res = subprocess.run(["hyprctl", "repl", code], capture_output=True, text=True, timeout=3)
            return f"Success on Hyprland (v{ver}).{learned_notice}" if "true" in res.stdout else f"Failed: {res.stdout.strip()}"

        elif recipe_type == "cli":
            cmd = recipe.get("command", "").replace("{target}", target_str).replace("{window}", win_param)
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=3)
            return f"Success on Hyprland (v{ver}).{learned_notice}" if res.returncode == 0 else f"Failed: {res.stderr or res.stdout}"

        return f"Unknown recipe type for action {action}."
    except Exception as e:
        return f"Error: {e}"
