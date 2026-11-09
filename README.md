# 🤖 Assistant - AI Desktop Assistant

> [!NOTE]
> **Fork & Attribution**: This project is a fork of [Mofakir](https://github.com/bonnjalal/Mofakir), created by [Abdelilah Jalal (@bonnjalal)](https://github.com/bonnjalal). Huge thanks to Abdelilah for his outstanding work building the original native Linux agentic desktop assistant!

Assistant is a native, agentic AI desktop assistant built specifically for Linux environments. Instead of living inside a restricted web browser sandbox, Assistant runs as a lightweight native PyQt6 GUI that bridges your offline (or online) LLM directly to your window manager, clipboard, active application context, and file system.

It can see your screen, read your copied text or binary clipboard assets, organize your Obsidian vaults, run local bash commands with interactive UI safety prompts, and hold spoken conversations with automatic multilingual speech-to-text and optional text-to-speech.

---

## Verified Test Environments & Hardware

The system has been fully verified and tested on the following setup:

### Hardware Specifications
* **Operating System:** NixOS
* **Desktop Environment:** Hyprland Window Manager (Wayland)
* **Memory:** 32 GB RAM (Shared memory)
* **Audio Server:** PipeWire (via `wireplumber` & `pw-play`)
* **Local LLM:** Gemma-4-e2b hosted on a local llama-cpp server / FastFlowLM (with optional Fallback LLM)
* **Application Launcher:** `uwsm app` wrapper with XDG desktop detection and terminal wrapping (via `kitty`)
* CPU: AMD Ryzen 7 8845HS (16) @ 5.14 GHz
* GPU: Integrated Laptop GPU

---

## Installation & Setup

### NixOS (Nix Flakes)

If you use NixOS with Flakes enabled, Assistant is entirely packaged with all system and audio dependencies.

#### Run Directly
```bash
nix run github:egara/assistant
```

#### Home Manager Integration
Add the Home Manager module to your configuration to declare your settings, context, and selected voice models:

Add the flake to your configuration:
```nix
assistant.url = "github:egara/assistant";
```
```nix
{ inputs, pkgs, ... }: {
  imports = [ inputs.assistant.homeManagerModules.default ];

  programs.assistant = {
    enable = true;
    context = ''
    Information about you or your machine
    '';
    settings = {
      llm = {
        model = "gemma4-it:12b";
        base_url = "http://127.0.0.1:52625/v1";
        api_key = "sk-none";
      };
      voice = {
        tts_engine = "none"; # "none", "online", or "local"
      };
    };
  };
}
```

---

### Standard Linux (Debian, Ubuntu, etc.)

```bash
git clone https://github.com/egara/assistant.git
cd assistant
chmod +x install.sh
./install.sh
```

---

## Hyprland Window Rules & Shortcuts

If you are using Hyprland, to integrate Assistant seamlessly into your workspace, copy the following rules and hotkeys into your `~/.config/hypr/assistant.conf` (and load it in your main config with `source = ~/.config/hypr/assistant.conf`):

```ini
# ==========================================
# ASSISTANT WINDOW RULES
# ==========================================
windowrulev2 = float, title:^(Assistant AI)$
windowrulev2 = size 40% 50%, title:^(Assistant AI)$
windowrulev2 = center, title:^(Assistant AI)$
windowrulev2 = opacity 0.85 0.70, title:^(Assistant AI)$
windowrulev2 = animation slide, title:^(Assistant AI)$

# ==========================================
# ASSISTANT KEYBINDS
# ==========================================

# 1. AUTO MODE (VAD: Automatically stops recording when you pause talking)
bind = SUPER, A, exec, assistant --voice
bind = SUPER ALT, A, exec, assistant --clipboard --voice
bind = SUPER CTRL, A, exec, assistant --screen --voice

# 2. MANUAL TOGGLE MODE (Push-to-Talk: Perfect for loud environments. Press to start, press to stop)
bind = SUPER SHIFT, A, exec, assistant --voice --toggle
bind = SUPER SHIFT ALT, A, exec, assistant --clipboard --toggle
bind = SUPER SHIFT CTRL, A, exec, assistant --screen --toggle
```

---

## Comprehensive Configuration Guide (`config.json`)

Assistant generates its configuration file at `~/.config/assistant/config.json`. Below is a detailed technical breakdown of every sub-object parameter and how to tweak it to match your workflow.

### 1. The `"llm"` Block

This block configures Assistant's multi-model intelligence engine. It supports multiple model providers and local/cloud backends simultaneously (`FastFlowLM`, `llama.cpp`, `Ollama`, `vLLM`, `Groq`, `OpenRouter`, `OpenAI`). You can seamlessly switch between configured models at runtime using the `/model` command.

```json
"llm": {
    "default": "gemma",
    "history_duration_minutes": 15,
    "system_context": "~/.config/assistant/context.md",
    "models": {
        "gemma": {
            "provider_name": "local",
            "api_key": "sk-none",
            "base_url": "http://127.0.0.1:52625/v1",
            "model": "gemma4-it:12b",
            "vision_enabled": true,
            "timeout": 180.0
        },
        "glm": {
            "provider_name": "local",
            "api_key": "sk-none",
            "base_url": "http://127.0.0.1:8010/v1",
            "model": "GLM-5.3-Flash",
            "vision_enabled": true,
            "timeout": 180.0
        }
    }
}
```

* **`default`** *(String)*: The default model identifier from the `models` dictionary that Assistant starts with upon launching.
* **`history_duration_minutes`** *(Integer/Float)*: Controls memory management. Drops historical message logs from the sliding context window if the file modification timestamp exceeds this duration.
* **`system_context`** *(String)*: Absolute path pointing to a persistent system context profile markdown file.
* **`models`** *(Object)*: Key-value map of available models. Each entry contains:
  * **`model`** / **`name`** *(String)*: Precise model identifier string passed to the completion endpoint.
  * **`base_url`** *(String)*: Endpoint of the OpenAI-compatible inference server.
  * **`api_key`** *(String)*: Authentication token (`"sk-none"` for local servers).
  * **`vision_enabled`** *(Boolean)*: Enables base64 vision payloads via screen/clipboard capture.
  * **`timeout`** *(Float, Optional)*: Request timeout in seconds (defaults to `180.0`). Prevents unexpected cuts during large context prefill sequences.
  * **`provider_name`** *(String, Optional)*: Informational label (e.g., `"local"`, `"groq"`).

> [!NOTE]
> **Backward Compatibility**: If you use the classic format with a single `model` and `fallback`, Assistant automatically maps them into the models catalog as `primary` and `fallback`.

---

### 2. The `"obsidian"` Block

Enables agentic integration with your local personal knowledge graph.

```json
"obsidian": {
    "api_key": "PASTE_YOUR_API_KEY_HERE",
    "url": "https://127.0.0.1:27124"
}
```

* **`api_key`** *(String)*: The authentication token generated inside your Obsidian application under *Settings -> Local REST API -> Auth Tokens*.
* **`url`** *(String)*: The HTTPS server link exposed locally by the Obsidian API plugin.

---

### 3. The `"voice"` Block

Controls the speech-to-text (STT) and text-to-speech (TTS) pipelines.

```json
"voice": {
    "tts_engine": "none",
    "whisper_model": "~/.local/share/assistant/models/ggml-small.bin",
    "silence_duration": "0.8",
    "silence_threshold": "1%",
    "asr": {
        "enabled": true,
        "endpoint": "http://127.0.0.1:52625/v1/audio/transcriptions",
        "model": "whisper",
        "language": "es",
        "timeout": 10.0
    },
    "tts_local_models": {
        "en": "~/.local/share/assistant/models/en_US-lessac-medium.onnx",
        "es": "~/.local/share/assistant/models/es_ES-sharvard-medium.onnx"
    },
    "tts_online_voices": {
        "en": "en-US-GuyNeural",
        "es": "es-ES-AlvaroNeural"
    }
}
```

* **`asr`** *(Object, Optional)*: Configures hardware-accelerated Automatic Speech Recognition (ASR) via an OpenAI-compatible transcription endpoint (such as FastFlowLM running with `--asr 1`):
    * **`enabled`** *(Boolean)*: Enables remote ASR (defaults to `true` when the block is present).
    * **`endpoint`** *(String)*: OpenAI-compatible audio transcriptions endpoint (e.g. `http://127.0.0.1:52625/v1/audio/transcriptions`).
    * **`model`** *(String)*: Model identifier passed to the endpoint (defaults to `"whisper"`).
    * **`language`** *(String, Optional)*: ISO language code hint (e.g. `"es"` or `"en"`).
    * **`timeout`** *(Float, Optional)*: Request timeout in seconds before falling back (defaults to `10.0`).
    * **`api_key`** *(String, Optional)*: Optional Bearer token for remote ASR providers.
    * **Automatic Fallback**: If the remote ASR endpoint is unreachable or errors out, Assistant seamlessly falls back to the local CPU `whisper-cli`.
* **`silence_duration`** *(String/Float, Optional)*: Silence duration in seconds required to automatically detect end-of-speech (defaults to `0.8`).
* **`silence_threshold`** *(String, Optional)*: Energy threshold for silence cut (defaults to `"1%"`).
* **`tts_engine`** *(String)*: Options are `"none"`, `"online"`, or `"local"`.
    * `none` (or `disabled`): Disables speech synthesis entirely. The assistant displays text without speaking.
    * `online`: Connects to Microsoft Edge's cloud neural speech synthesis stream. Requires an active network connection.
    * `local`: Switches to 100% private, zero-network processing using the compiled **Piper ONNX** engine.
* **`whisper_model`** *(String)*: Absolute path to your local compiled Whisper GGML model binary file (used as local fallback).
* **`tts_local_models`** *(Object)*: Key-value map pairing ISO language strings to the absolute local directory path of your Piper `.onnx` speech files.
* **`tts_online_voices`** *(Object)*: Key-value map tracking language codes to specific Microsoft Edge cloud neural voice structures.

---

### 4. The `"mcp_servers"` Block

Enables dynamic integration with external **Model Context Protocol (MCP)** servers over HTTP (SSE or Streamable HTTP). All tools exposed by configured MCP servers are automatically discovered and registered with the local LLM.

```json
"mcp_servers": {
    "time": {
        "url": "http://127.0.0.1:32770/sse",
        "transport": "sse"
    },
    "searxng": {
        "url": "http://127.0.0.1:32771/mcp",
        "transport": "streamable_http"
    }
}
```

* **`url`** *(String)*: The HTTP/SSE endpoint of the running MCP server.
* **`transport`** *(String)*: The transport protocol (`"sse"` or `"streamable_http"`).
* **SearXNG Priority & Fallback**: When an MCP server providing `searxng_web_search` is registered, web queries use SearXNG by default. If the SearXNG server is unreachable or fails, Assistant automatically falls back to DuckDuckGo scraping.

---

### Common Configuration Profiles

#### Profile A: Local Model with Speech-to-Text only (TTS Disabled)
Use this profile for fast interactions where you speak to the assistant but read answers on screen without audio feedback.

```json
{
  "llm": {
    "provider_name": "local",
    "api_key": "sk-none",
    "base_url": "http://127.0.0.1:52625/v1",
    "model": "gemma4-it:12b",
    "vision_enabled": true,
    "system_context": "~/.config/assistant/context.md"
  },
  "voice": {
    "tts_engine": "none"
  }
}
```

#### Profile B: 100% Offline Local Model with Local TTS (`piper`)
Use this profile if you want total privacy with local speech synthesis.

```json
{
  "llm": {
    "provider_name": "local",
    "api_key": "sk-none",
    "base_url": "http://127.0.0.1:52625/v1",
    "model": "gemma4-it:12b",
    "vision_enabled": true,
    "system_context": "~/.config/assistant/context.md"
  },
  "decision_router": {
    "enabled": true,
    "endpoint": "http://127.0.0.1:8000/v1/systemone",
    "model": "d3:edge",
    "timeout": 1.5,
    "confidence_threshold": 0.60
  },
  "voice": {
    "tts_engine": "local"
  }
}
```

### 5. The `"decision_router"` Block (FastFlowLM / d3 Integration)
When running FastFlowLM with decision models (`flm serve ... --decision d3:edge`), Assistant can use FastFlowLM's `/v1/systemone` endpoint to perform ultra-fast (~50 ms) semantic intent classification and tool routing before calling the primary LLM:
* `"enabled"` *(bool)*: Enables or disables the decision router (default: `false`).
* `"endpoint"` *(string)*: URL of the decision model endpoint (default: `"http://127.0.0.1:8000/v1/systemone"`).
* `"model"` *(string)*: Model tag or identifier (default: `"d3:edge"`).
* `"timeout"` *(float)*: Network timeout in seconds before falling back to sending all tools (default: `1.5`).
* `"confidence_threshold"` *(float)*: Minimum probability required to narrow down tools (default: `0.60`). If lower, all tools are safely passed.

Tool categories routed by the decision engine:
* `conversational`: Pure chit-chat, explanations, or code guidance. No tools injected (saves context and avoids hallucinations).
* `desktop`: Hyprland window/workspace management and system shortcuts.
* `apps`: Launching desktop and terminal applications.
* `filesystem`: File manipulation (`read_file`, `write_file`, `replace_in_file`, `execute_command`).
* `notes`: Local Obsidian vault actions (`obsidian_search`, `obsidian_read`, `obsidian_write`).
* `weather`: Open-Meteo weather lookups (`get_weather`).
* `web_search`: Internet search via SearXNG MCP or DuckDuckGo (`web_search`).

---

## Modern User Interface & Slash Commands

Assistant features a clean Material Design GUI styled with **SicOS Nerd Font** icons and high contrast:
* **Interactive Help & Reset**: Click the `?` icon in the upper-right corner or the dedicated microphone toggle on the left of the input field.
* **Interactive Model Selector Badge**: The active model pill badge right below the input box shows the current model and acts as a clickable dropdown selector to switch models on the fly without typing commands.
* **Auto-Focus & Smart Scroll**: Automatic focus on the input box upon launch, auto-scrolling during fast token streaming, and free manual scrolling when inspecting historical outputs.

### Built-in Slash Commands & Shortcuts
You can type the following commands directly into the prompt box:
* **/model**: Lists all available configured models indicating which one is active, or toggles to the next model in the list. Use `/model <name>` (e.g. `/model glm`) to switch directly to a specific model.
* **/voice** or **/tts**: Toggles text-to-speech spoken audio output on/off.
* **/about**: Displays application version and architectural details.
* **/new** or **/reset**: Resets the current conversation context, wipes the chat memory file, and starts a fresh session.
* **/clear**: Clears messages from the screen and purges context history.
* **/help**: Displays an interactive help modal with quick command tips.
* **`v`**: Typing `v` and pressing Enter instantly toggles push-to-talk voice recording mode.
* **Speaker Button (`󰕾` / `󰝟`)**: Dedicated button next to the help button that toggles TTS audio output on demand (disabled by default on startup).

### System Tray Integration
Assistant lives unobtrusively in your desktop environment's system tray:
* **Minimize on Close**: Closing the main window (`Ctrl+Q` or window close button) hides the window to the system tray instead of terminating the background process.
* **Nerd Font Robot Tray Icon**: Visual robot icon (`󰚩`) that changes color dynamically based on activity state:
  * **Idle** (`#828c99` muted gray): Waiting for user interaction in resting state.
  * **Listening** (`#7fd962` vibrant green): Actively recording microphone input.
  * **Routing** (`#ffb454` amber): FastFlowLM `d3:edge` decision model is categorizing intent and filtering tools.
  * **Thinking / Processing** (`#59c2ff` SicOS cyan/blue): The model is thinking or processing/executing system tools.
* **Mouse Interactions**:
  * **Left Click**: Restores and focuses the main window (`Maximize`).
  * **Right Click**: Opens a context menu with:
    * **Maximize**: Shows the conversation window.
    * **Talk / Stop talking**: Toggles continuous background voice conversation without showing the window. All spoken dialogues, tool invocations, and answers are persisted to history and visible upon maximizing.
    * **Exit**: Fully terminates the Assistant process and removes the tray icon.

---

## Agentic Architecture & System Safety

Unlike basic chat interfaces, Assistant parses your prompt and dynamically decides whether it needs to invoke a native tool.

### Multi-Round Agentic Loop & Reasoning Models
Requests are resolved through a multi-round agentic loop instead of a fixed two-turn flow:

* **Chained tool execution**: Every round executes *all* requested tools (native + MCP) and feeds the results back to the model, supporting arbitrarily chained calls (e.g. two parallel `web_search` queries followed by a synthesis). The loop is capped at 4 tool rounds; the final round withholds the tools schema to force a plain text answer.
* **Reasoning model support**: Reasoning models (GLM / DeepSeek style) stream their chain of thought in a separate `reasoning_content` field. Assistant captures it and surfaces a live "Thinking…" status snippet instead of appearing frozen, and logs it for diagnostics.
* **Silent completions**: Successful state-changing desktop actions (app launches, `hyprctl dispatch`, `wpctl set-volume`, `brightnessctl set`) still finish immediately with a silent "Hecho." turn without conversational clutter. Read-only inspection commands (`hyprctl clients/binds/monitors`, `wpctl status`, …) keep the agentic loop alive so the model can use their results instead of ending the request mid-investigation.
* **LLM diagnostics**: Requests, executed tools (with arguments), captured reasoning, and answers are logged to `~/.local/state/assistant/assistant.log`, making model-specific issues easy to debug.

### Built-in & MCP Tools
1. `get_current_time(timezone)`: Retrieves current date, time, weekday, and timezone natively from the host system (with optional IANA timezone conversion, replacing external time MCP servers).
2. `open_application(app, target)`: Universally detects installed desktop applications (`.desktop` entries via XDG) and CLI tools (`yazi`, `btop`, etc.). Automatically wraps GUI tools with `uwsm app` for Wayland session consistency and spawns terminal apps inside `kitty`. Silent execution prevents repetitive audio or unnecessary chat turns.
3. `get_weather(location)`: Fetches precise meteorological data and multi-day forecasts via Open-Meteo's geocoding API with localized WMO status codes.
4. `manage_desktop(action, target, window_address)`: Hyprland workspace and window manager controller:
   * **Versioned Knowledge Base**: Dynamically detects the installed Hyprland version and chooses the exact dispatcher syntax (e.g., Lua dispatchers for v0.57+).
   * **User Mutable Knowledge Layer**: Learns and saves unknown dispatchers to `~/.local/share/assistant/hyprland_knowledge.json`.
   * **Window Targeting**: Targets and focuses specific windows for operations such as `close`, `float`, and `fullscreen`.
   * **Silent Feedback**: Fast window/workspace movements execute silently without interrupting the voice flow.
4. `read_file(path)`: Directly reads the contents of any file on the local filesystem.
5. `write_file(path, content)`: Creates or updates local files with specified content and creates directories if necessary.
6. `replace_in_file(path, target, replacement)`: Finds and replaces text within an existing file.
7. `obsidian_search` / `obsidian_read` / `obsidian_write`: Manages notes using the encrypted Obsidian Local REST API.
8. `web_search(query)`: Searches the web prioritizing local SearXNG (via MCP) with automatic DuckDuckGo fallback.
9. `execute_command(command)`: Runs local bash pipelines (with detached background execution for GUI apps).
10. `get_system_knowledge(topic)`: Consults SicOS modular system knowledge or live Hyprland shortcuts. Topics include `'index'`, `'hardware'` (audio volume with `wpctl`, brightness with `brightnessctl`, display scaling), `'apps'` (installed desktop tools & TUIs), `'hyprland'` (window management & layouts), or `'shortcuts'` (live compositor keybindings via `hyprctl binds`).
11. **Dynamic MCP Tools**: Any additional tools exposed by configured MCP servers (e.g. `current_time`, `convert_time`, `searxng_web_search`, etc.).

### Execution Safety Guardrails & Threading Isolation
Assistant classifies all system bash commands before executing them:

* **Auto-Approval Safe List:** Commands starting with safe utilities (`ls`, `cat`, `echo`, `uptime`, `free`, `df`, `playerctl`, `wpctl`, `uname`, `fastfetch`) run instantly in the background without user interruption.
* **Interactive Approval UI:** If the model tries to run an unverified command (e.g., `mkdir`, `nixos-rebuild`, or installing a package), Assistant suspends execution, displays an interactive **"⚠️ Action Required"** dialog inside the chat interface, and waits for explicit approval before proceeding.
