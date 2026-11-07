# Hyprland Architecture in SicOS

SicOS uses Hyprland with Lua configuration (`hyprland.lua`).

## 1. Window & Workspace Management Tool
Always use `manage_desktop(action, target, window_address)` instead of running raw bash commands whenever possible:
- **`focus_workspace`**: Switch to a workspace (e.g. `target='1'`, `target='2'`).
- **`move_to_workspace`**: Move the active window (or window named in `window_address`) to a workspace.
- **`close_window`**: Close active window or specific window by class/title (e.g. `window_address='firefox'`).
- **`toggle_floating`**: Toggle floating/tiling mode on target window.
- **`toggle_fullscreen`**: Toggle fullscreen mode.
- **`pin_window`**: Pin window across all workspaces.
- **`center_window`**: Center target window on screen.

## 2. Dynamic Layouts in SicOS
SicOS supports switching layouts per workspace or globally:
- **Dwindle**: Default dynamic bspwm-style tiling (`Super+F1`).
- **Master**: Master-stack layout (`Super+F2`).
- **Scrolling**: Horizontal window carousel layout (`Super+F3`).
- **Monocle**: Single active window maximized (`Super+F4`).

## 3. Advanced Lua Interop
Hyprland v0.55+ uses Lua configurations. To dispatch direct Lua commands when needed:
`hyprctl eval '<lua code>'` (e.g. `hyprctl eval 'hl.dispatch(hl.dsp.focus({ workspace = 1 }))'`).
Do NOT use `hyprctl keyword` as it is incompatible with non-legacy parsers.
