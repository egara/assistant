# 🤖 AGENTS.md — Development Guidelines & Project Memory

This document serves as the persistent contextual memory for AI agents (and human developers) working on the **Assistant** codebase across fresh sessions. All agents must read and strictly follow the guidelines detailed below.

---

## 📌 Non-Negotiable Directives & Workflow Rules

1. **Keep `README.md` in Sync**:
   - Any new feature, tool, command, configuration parameter, or architectural change **must be immediately documented** in [`README.md`](file:///home/egarcia/Development/git/assistant/README.md).
   - Users and operators refer directly to `README.md` to see what Assistant offers, its internal architecture, and how to configure it.

2. **Commit & Push Practice**:
   - **Granular & Atomic Commits**: Group `git add` and commit changes **strictly by feature or functionality** using Conventional Commits (`feat(...)`, `fix(...)`, `docs(...)`, `style(...)`, `refactor(...)`, `perf(...)`). Never create large monolithic commits mixing unrelated changes.
   - **English Commit Messages & Documentation**: Commit messages, code docstrings, and architectural markdown documentation must be written in **English**.
   - **Target Branch & Push**: Verify the current branch (e.g. `devel` or `main`) and push completed atomic commits to the appropriate remote branch (`origin/<branch>`).

3. **Python Best Practices**:
   - **No bare `except:` clauses**: Always catch explicit exception types (e.g. `OSError`, `subprocess.SubprocessError`, `json.JSONDecodeError`, `requests.RequestException`).
   - **No Hardcoded User Paths**: Never hardcode user paths or usernames (e.g. `egarcia`). Use `os.environ.get("USER") or getpass.getuser()` and `os.path.expanduser("~")`.
   - **Clean Imports & Decoupling**: Do not leave unused or orphaned imports in core modules. Keep domain logic decoupled into dedicated packages.

4. **Nix Packaging & File Synchronization (`flake.nix`)**:
   - Whenever a new Python package directory is created under `src/` (such as `src/tools/`, `src/routing/`, or `src/commands/`), ensure it is installed in `flake.nix` under `$out/lib/assistant/` so that Nix builds and wrapped binaries retain full module access via `PYTHONPATH`.

5. **Knowledge Files & Runtime Paths**:
   - Base system recipe knowledge: [`src/hyprland_knowledge.json`](file:///home/egarcia/Development/git/assistant/src/hyprland_knowledge.json).
   - Base system modular docs: `src/knowledge/*.md`.
   - User mutable recipe layer: `~/.local/share/assistant/hyprland_knowledge.json`.
   - User mutable modular docs: `~/.config/assistant/knowledge/*.md`.
   - User runtime state: `~/.local/state/assistant/` (contains `chat_history.json` and `assistant.log`).
   - User configuration: `~/.config/assistant/config.json`.

---

## 🏗️ Project Architecture & Directory Structure

```text
assistant/
├── flake.nix                  # Nix Flake build definition & Home Manager module
├── trigger.sh                 # Launcher script with dynamic VAD/toggle modes
├── README.md                  # User-facing documentation & architecture overview
├── AGENTS.md                  # Persistent instructions and guidelines for AI agents
└── src/
    ├── assistant-gui.py       # Core application entrypoint & PyQt6 QObject backend
    ├── ui.qml                 # Native QtQuick / Material Design graphical user interface
    ├── hyprland_knowledge.json# Built-in Hyprland action recipes & command intents
    ├── knowledge/             # Modular markdown documentation (hardware, apps, shortcuts...)
    ├── tools/                 # Domain-scoped execution tools package
    │   ├── __init__.py        # Public tool definitions & exports
    │   ├── dispatch.py        # Centralized tool execution & silent completion policy
    │   ├── time.py            # Real-time clock & IANA timezone resolution
    │   ├── weather.py         # Open-Meteo & wttr.in weather forecast tools
    │   ├── obsidian.py        # Obsidian Local REST API integration
    │   ├── web.py             # SearXNG MCP priority & DuckDuckGo search fallback
    │   ├── os.py              # OS apps launcher (uwsm/kitty), bash execution & filesystem tools
    │   ├── knowledge.py       # Modular markdown knowledge queries & live keybindings
    │   └── hyprland.py        # Version-aware Hyprland dispatch & runtime auto-discovery
    ├── routing/               # Decision Router package (System One / FastFlowLM)
    │   ├── __init__.py        # Exports DecisionRouter and category mapping
    │   └── routing.py         # FastFlowLM /v1/systemone d3:edge intent classification
    ├── mcp/                   # Model Context Protocol (MCP) package
    │   ├── __init__.py        # Exports MCPManager and MCP_AVAILABLE
    │   └── manager.py         # SSE/HTTP transport connections and tool discovery
    ├── llm/                   # Multi-Model LLM Catalog package
    │   ├── __init__.py        # Exports ModelCatalog & AIGeneratorThread
    │   ├── catalog.py         # Model presets, endpoint discovery & selector structure
    │   └── generator.py       # Multi-round agentic loop worker & reasoning extraction
    ├── voice/                 # Voice synthesis and audio playback package
    │   ├── __init__.py        # Exports TTSWorkerThread & stop_all_media_playback
    │   └── tts.py             # Piper/Edge-TTS synthesis, language detection & playback
    └── commands/              # Interactive slash command registry package
        ├── __init__.py        # Factory create_default_registry()
        ├── base.py            # BaseCommand, CommandContext & CommandRegistry
        ├── session.py         # /new, /reset, /clear lifecycle commands
        ├── system.py          # /help, /about information commands
        ├── voice.py           # /voice, /tts speech output toggle commands
        └── model.py           # /model listing & dynamic switching
```

### Core Components Details

* **Core Engine (`src/assistant-gui.py`)**:
  - Connects frontend QML signals with background worker threads (`AIGeneratorThread`, `TTSWorkerThread`).
  - Implements multi-round agentic tool execution with canonical OpenAI `tool_call_id` returns.
  - Multi-model catalog supporting runtime switching and fallback endpoints.
  - UI command safety confirmation dialogs for non-whitelisted bash commands.

* **Tools System (`src/tools/`)**:
  - Each domain is isolated into its own module (`time`, `weather`, `obsidian`, `web`, `os`, `knowledge`, `hyprland`).
  - Exports JSON schemas (`TOOL_DEFINITIONS`) and execution functions cleanly aggregated in `src/tools/__init__.py`.

* **Decision Router (`src/routing/`)**:
  - Encapsulated in `DecisionRouter` class.
  - Calls FastFlowLM `d3:edge` model at `/v1/systemone` to classify user intent into domain categories within ~50ms.
  - Evaluates temporal anchors and filters the tools payload passed to the LLM to minimize token latency.

* **Slash Commands (`src/commands/`)**:
  - Uses the Command/Registry pattern.
  - Commands receive a decoupled `CommandContext` to interact with session state, audio toggles, and model catalogs.
  - `/help` dynamically reads metadata directly from registered command instances.

* **Frontend UI (`src/ui.qml`)**:
  - Native PyQt6 + QML (QtQuick Controls 2 Material Design).
  - Features real-time token streaming, active model indicator, interactive safety approval modal, push-to-talk voice recording, and model selector menu.

---

## 🛠️ Verification & Testing Routines

Before committing and pushing changes:
1. **Python Compilation**:
   ```bash
   python3 -m py_compile src/assistant-gui.py src/tools/*.py src/routing/*.py src/commands/*.py
   ```
2. **Environment & Import Verification (Nix)**:
   ```bash
   nix develop --command python3 -c "import tools, routing, commands; print('Imports valid!')"
   ```
3. **Git Sanity Check**:
   ```bash
   git status
   git diff
   ```
