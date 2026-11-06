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

This block configures Assistant's core intelligence engine. It utilizes an OpenAI-compatible API scheme, meaning it can connect to local backends (`FastFlowLM`, `llama.cpp`, `Ollama`, `vLLM`) or external clouds (`Groq`, `OpenRouter`, `OpenAI`).

```json
"llm": {
    "provider_name": "local",
    "api_key": "sk-none",
    "base_url": "http://127.0.0.1:52625/v1",
    "model": "gemma4-it:12b",
    "vision_enabled": true,
    "history_duration_minutes": 15,
    "system_context": "~/.config/assistant/context.md",
    "fallback": {
        "provider_name": "local",
        "api_key": "sk-none",
        "base_url": "http://127.0.0.1:8010/v1",
        "model": "GLM-5.3-Flash",
        "vision_enabled": true
    }
}
```

* **`provider_name`** *(String)*: An informational label for your setup (e.g., `"local"`, `"groq"`, `"openrouter"`).
* **`api_key`** *(String)*: The authentication token for your provider. Set this to `"sk-none"` for local offline servers.
* **`base_url`** *(String)*: The target network endpoint of your inference server. Ensure it points to the base directory of the completions route (usually ends with `/v1`).
* **`model`** *(String)*: The precise model identifier string passed in the payload API request.
* **`vision_enabled`** *(Boolean)*: `true` or `false`. When enabled, Assistant allows binary base64 vision payloads via the screen/clipboard mechanics.
* **`history_duration_minutes`** *(Integer/Float)*: Controls memory management. Drops historical message logs from the sliding context window if the file modification timestamp exceeds this duration.
* **`system_context`** *(String)*: Absolute path pointing to a persistent system context profile markdown file.
* **`fallback`** *(Object, Optional)*: Secondary LLM configuration (`base_url`, `model`, `api_key`, `vision_enabled`). If the primary model endpoint is unreachable or times out, Assistant automatically routes requests through the fallback model and updates the active model indicator in the UI.

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

* **`tts_engine`** *(String)*: Options are `"none"`, `"online"`, or `"local"`.
    * `none` (or `disabled`): Disables speech synthesis entirely. The assistant displays text without speaking.
    * `online`: Connects to Microsoft Edge's cloud neural speech synthesis stream. Requires an active network connection.
    * `local`: Switches to 100% private, zero-network processing using the compiled **Piper ONNX** engine.
* **`whisper_model`** *(String)*: Absolute path to your local compiled Whisper GGML model binary file.
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
  "voice": {
    "tts_engine": "local"
  }
}
```

---

## Modern User Interface & Slash Commands

Assistant features a clean Material Design GUI styled with **SicOS Nerd Font** icons and high contrast:
* **Interactive Help & Reset**: Click the `?` icon in the upper-right corner or the dedicated microphone toggle on the left of the input field.
* **Active LLM Badge**: The currently connected model is dynamically shown right below the prompt input box.
* **Auto-Focus & Smart Scroll**: Automatic focus on the input box upon launch, auto-scrolling during fast token streaming, and free manual scrolling when inspecting historical outputs.

### Built-in Slash Commands & Shortcuts
You can type the following commands directly into the prompt box:
* **/new** or **/reset**: Resets the current conversation context, wipes the chat memory file, and starts a fresh session.
* **/clear**: Clears messages from the screen and purges context history.
* **/help**: Displays an interactive help modal with quick command tips.
* **`v`**: Typing `v` and pressing Enter instantly toggles push-to-talk voice recording mode.

---

## Agentic Architecture & System Safety

Unlike basic chat interfaces, Assistant parses your prompt and dynamically decides whether it needs to invoke a native tool.

### Built-in & MCP Tools
1. `open_application(app, target)`: Universally detects installed desktop applications (`.desktop` entries via XDG) and CLI tools (`yazi`, `btop`, etc.). Automatically wraps GUI tools with `uwsm app` for Wayland session consistency and spawns terminal apps inside `kitty`. Silent execution prevents repetitive audio or unnecessary chat turns.
2. `get_weather(location)`: Fetches precise meteorological data and multi-day forecasts via Open-Meteo's geocoding API with localized WMO status codes.
3. `manage_desktop(action, target, window_address)`: Hyprland workspace and window manager controller:
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
10. **Dynamic MCP Tools**: Any additional tools exposed by configured MCP servers (e.g. `current_time`, `convert_time`, `searxng_web_search`, etc.).

### Execution Safety Guardrails & Threading Isolation
Assistant classifies all system bash commands before executing them:

* **Auto-Approval Safe List:** Commands starting with safe utilities (`ls`, `cat`, `echo`, `uptime`, `free`, `df`, `playerctl`, `wpctl`, `uname`, `fastfetch`) run instantly in the background without user interruption.
* **Interactive Approval UI:** If the model tries to run an unverified command (e.g., `mkdir`, `nixos-rebuild`, or installing a package), Assistant suspends execution, displays an interactive **"⚠️ Action Required"** dialog inside the chat interface, and waits for explicit approval before proceeding.
