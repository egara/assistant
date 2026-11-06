# 🤖 AGENTS.md — Development Guidelines & Project Memory

This file serves as persistent contextual memory for AI agents (and developers) working on the **Assistant** codebase across fresh sessions.

---

## 📌 Non-Negotiable Directives & Workflow Rules

1. **Keep `README.md` in Sync**:
   - Any new feature, tool, command, configuration parameter, or architectural change **must be immediately documented** in [`README.md`](file:///home/egarcia/Development/git/assistant/README.md). Users look directly at the README to see what Assistant offers and how to configure it.

2. **Git Commits & Push Practice**:
   - When requested or completing logical milestones, commit and push changes directly to `origin/main`.
   - **Granular commits**: Always group `git add` and commits **by feature or functionality** (`feat(...)`, `fix(...)`, `docs(...)`, `style(...)`, `perf(...)`). Never create large monolithic commits mixing unrelated changes.

3. **Check Knowledge Files & User Paths**:
   - The system base knowledge lives in [`src/hyprland_knowledge.json`](file:///home/egarcia/Development/git/assistant/src/hyprland_knowledge.json).
   - The user mutable layer is `~/.local/share/assistant/hyprland_knowledge.json`.
   - User runtime state lives in `~/.local/state/assistant/` (history) and configuration in `~/.config/assistant/config.json`.

---

## 🏗️ Project Architecture & Key Components

* **GUI / Frontend (`src/ui.qml`)**:
  - Native PyQt6 + QML (QtQuick / Controls 2) interface with Material Design styling and SicOS Nerd Font icons.
  - Symmetrical mic toggle, active LLM model indicator, auto-scrolling streaming, free historical scrolling, and help modal.

* **Backend / Core Engine (`src/assistant-gui.py`)**:
  - **Threaded Inference**: Streaming OpenAI-compatible client connecting to local inference (e.g. llama.cpp, FastFlowLM) or remote providers.
  - **Fallback LLM**: Automatic failover to secondary `fallback` endpoint if primary LLM fails.
  - **Built-in Slash Commands**: `/new`, `/reset`, `/clear`, `/help`, and quick voice toggle `'v'`.
  - **Silent Action Execution**: Successful Hyprland actions and app launches execute silently without second-turn audio clutter.
  - **Voice Pipeline**: Whisper STT (GGML local) + Edge-TTS / Piper ONNX local TTS.

* **Agentic Tools Available**:
  - `open_application(app, target)`: XDG `.desktop` resolution, `uwsm app` execution for Wayland apps, terminal wrapping inside `kitty` for TUIs (`yazi`, `btop`, etc.).
  - `get_weather(location)`: Open-Meteo API with localized WMO status codes and multi-day forecast.
  - `manage_desktop(action, target, window_address)`: Hyprland workspace/window dispatch with versioned recipe resolution and runtime auto-discovery (`hl.dsp`).
  - `read_file(path)`, `write_file(path, content)`, `replace_in_file(path, target, replacement)`: Dedicated filesystem tools.
  - `execute_command(command)`: Bash pipeline execution with safety classification (auto-approved vs interactive UI modal).
  - `obsidian_search`, `obsidian_read`, `obsidian_write`: Local REST API integration with Obsidian.
  - `web_search(query)`: SearXNG MCP priority with automatic DuckDuckGo fallback.
  - Dynamic MCP tools over SSE / Streamable HTTP.

* **Packaging (`flake.nix`)**:
  - Fully packaged for NixOS with Home Manager module.
  - Generates the wrapper script that seeds models, sounds, and user knowledge base `hyprland_knowledge.json` into `~/.local/share/assistant/`.

---

## 🛠️ Verification & Testing Routines

- Syntax check: `python3 -m py_compile src/assistant-gui.py`
- Git sanity checks: `git status`, `git diff` before committing.
