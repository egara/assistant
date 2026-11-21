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
Add the Home Manager module to your configuration to declare your settings, default role, and selected voice models:

Add the flake input:
```nix
assistant.url = "github:egara/assistant";
```

##### Option A: Fully Managed via Nix (Default)
In this mode, Home Manager writes and maintains both `~/.config/assistant/config.json` and `~/.config/assistant/roles/default.md` declaratively:

```nix
{ inputs, pkgs, ... }: {
  imports = [ inputs.assistant.homeManagerModules.default ];

  programs.assistant = {
    enable = true;

    defaultRole = ''
      You are Assistant, an agentic AI running locally on NixOS with Hyprland.
      Respond concisely and execute system commands or window actions when asked.
      Always reply in the user's language.
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

##### Option B: Managing Config and Roles Locally (Independent of Nix)
If you already maintain your own `config.json` or roles files locally (e.g. via dotfiles repo, secrets manager, or manual edits) and don't want Home Manager to overwrite or hardcode them, you can disable their generation:

```nix
{ inputs, pkgs, ... }: {
  imports = [ inputs.assistant.homeManagerModules.default ];

  programs.assistant = {
    enable = true;

    # Disable Home Manager generation of config.json and roles/default.md
    enableConfig = false;
    enableDefaultRole = false;
  };
}
```

When disabled, Assistant will look for your existing local files at:
* `~/.config/assistant/config.json`
* `~/.config/assistant/roles/default.md`

##### Starter Templates for Local Files

If you choose to manage your files outside of Nix (`enableConfig = false`, `enableDefaultRole = false`), you can use the following templates as a starting point:

###### 1. Template `~/.config/assistant/config.json`
Create directory `mkdir -p ~/.config/assistant` and save the following structure:

```json
{
  "llm": {
    "default": "local-main",
    "models": {
      "local-main": {
        "provider_name": "local",
        "api_key": "sk-none",
        "base_url": "http://127.0.0.1:52625/v1",
        "model": "gemma4-it:12b",
        "vision_enabled": true,
        "timeout": 120.0
      },
      "fast-edge": {
        "provider_name": "local",
        "api_key": "sk-none",
        "base_url": "http://127.0.0.1:8000/v1",
        "model": "Hermes-3-8B",
        "vision_enabled": false,
        "timeout": 60.0
      }
    }
  },
  "decision_router": {
    "enabled": false,
    "endpoint": "http://127.0.0.1:8000/v1/systemone",
    "model": "d3:edge",
    "timeout": 1.5,
    "confidence_threshold": 0.60
  },
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
  },
  "mcp_servers": {
    "searxng": {
      "url": "http://127.0.0.1:32771/mcp",
      "transport": "streamable_http",
      "type": "web_search",
      "priority": 10,
      "description": "Internet search via local SearXNG instance."
    }
  },
  "obsidian": {
    "api_key": "YOUR_OBSIDIAN_REST_API_KEY",
    "url": "https://127.0.0.1:27124"
  }
}
```

###### 2. Template `~/.config/assistant/roles/default.md`
Create `~/.config/assistant/roles/default.md` with your assistant's personality and instructions:

```markdown
You are Assistant, an agentic AI desktop companion running locally on Linux (NixOS / Hyprland).

## Guidelines:
- Respond concisely and execute system commands or window actions when asked.
- Do not hallucinate. If you do not know something, search the web or consult system knowledge tools.
- Always reply in the user's preferred language.
- Silent actions: simple movements and adjustments are applied directly without chatting.
```

#### Module Options Reference

| Option | Type | Default | Description |
|---|---|---|---|
| `programs.assistant.enable` | `boolean` | `false` | Enables Assistant installation and configuration. |
| `programs.assistant.package` | `package` | `self.packages.${pkgs.stdenv.hostPlatform.system}.default` | The Assistant package derivation to install. |
| `programs.assistant.enableConfig` | `boolean` | `true` | When `true`, generates `~/.config/assistant/config.json` from `settings`. Set to `false` to use your local config. |
| `programs.assistant.enableDefaultRole` | `boolean` | `true` | When `true`, generates `~/.config/assistant/roles/default.md` from `defaultRole`. Set to `false` to use your local role file. |
| `programs.assistant.settings` | `attrs` | `{}` | Key-value settings serialized into `config.json` when `enableConfig = true`. |
| `programs.assistant.defaultRole` | `lines` | `""` | Markdown instructions serialized into `roles/default.md` when `enableDefaultRole = true`. |



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

# 1. WINDOW TOGGLE & HEADLESS VOICE MODE (Short / Long Press)
# Standard hyprland.conf syntax (using 'o' flag for long press):
bind = SUPER, A, exec, assistant
bindo = SUPER, A, exec, assistant --voice

# Lua syntax (hyprland.lua):
# hl.bind("SUPER + A", hl.dsp.exec_cmd("assistant"), { release = true, description = "Toggle Assistant AI Window" })
# hl.bind("SUPER + A", hl.dsp.exec_cmd("assistant --voice"), { long_press = true, description = "Headless Voice Mode" })

# 2. CONTEXT AWARE MODES
bind = SUPER ALT, A, exec, assistant --clipboard --voice
bind = SUPER CTRL, A, exec, assistant --screen --voice

# 3. MANUAL TOGGLE MODE (Push-to-Talk: Perfect for loud environments. Press to start, press to stop)
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
  * **`max_tool_rounds`** *(Integer, Optional)*: Maximum number of chained tool-execution rounds per user request (defaults to `6`, hard-capped at `12`). The final allowed round withholds the tools schema to force a plain text answer. Raise it for strong agentic models that chain several searches before answering, or lower it for lightweight models to keep them on a tight budget. If a turn ends without any result, a visible fallback message is shown instead of leaving the request unanswered.

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
    "silence_threshold": "4%",
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
* **`silence_threshold`** *(String, Optional)*: Energy threshold for silence cut (defaults to `"4%"`).
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

#### Redundancy and Priority Selection (Local vs Remote)
You can configure multiple MCP servers for the same service (e.g., a local Docker SearXNG instance and a LAN/remote server) by assigning them the same **`type`** and setting a **`priority`** integer:

```json
"mcp_servers": {
    "searxng_local": {
        "url": "http://127.0.0.1:32771/mcp",
        "transport": "streamable_http",
        "type": "web_search",
        "priority": 100,
        "description": "Búsqueda en Internet a través de Docker local SearXNG."
    },
    "searxng_lan": {
        "url": "http://192.168.1.50:32771/mcp",
        "transport": "streamable_http",
        "type": "web_search",
        "priority": 50,
        "description": "Búsqueda en Internet a través de servidor SearXNG en la LAN."
    }
}
```

* **Startup Probe & Redundancy Resolution**: Upon startup, Assistant tests connectivity to all configured MCP servers in parallel. For each group of servers sharing the same `type`, Assistant elects the responsive server with the **highest priority** as active. Other responsive instances in the same group remain in standby.
* **Warm Disconnection Notice**: If the active MCP server goes down during the session, Assistant immediately informs the user that the server has become unavailable, prompting a restart to re-probe available candidates without hanging or getting stuck.
* **`url`** *(String)*: The HTTP/SSE endpoint of the running MCP server.
* **`transport`** *(String)*: The transport protocol (`"sse"` or `"streamable_http"`).
* **`priority`** *(Integer)*: Higher values take precedence when multiple servers share the same `type`.
* **SearXNG Priority & Fallback**: When an MCP server providing web search is active, queries route through SearXNG. If no SearXNG MCP server is reachable, Assistant falls back to DuckDuckGo scraping.

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
    "vision_enabled": true
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
    "vision_enabled": true
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
* `desktop_and_apps`: Hyprland window/workspace management, launching desktop apps, visual screen inspection, screenshot capture, and webcam surveillance (`open_application`, `manage_desktop`, `get_system_knowledge`, `take_screenshot`, `start_surveillance`, `stop_surveillance`, `get_surveillance_status`).
* `filesystem`: File manipulation (`read_file`, `write_file`, `replace_in_file`, `execute_command`).
* `notes`: Local Obsidian vault actions (`obsidian_search`, `obsidian_read`, `obsidian_write`).
* `weather`: Open-Meteo weather lookups (`get_weather`).
* `cinema`: Cinema showtimes and movie listings from Tu Cine (`get_cinema_showtimes`).
* `web_search`: Internet search via SearXNG MCP or DuckDuckGo (`web_search`).

### 6. The `"surveillance"` Block (Autonomous Webcam Monitoring)
Configures autonomous webcam surveillance intervals and cooldowns:
* `"interval_seconds"` *(int)*: Interval between camera frame captures and vision checks in seconds (default: `5`).
* `"trigger_cooldown_seconds"` *(float)*: Cooldown time in seconds between alert dispatches and saving snapshots when conditions remain active (default: `10.0`).

```json
"surveillance": {
  "interval_seconds": 5,
  "trigger_cooldown_seconds": 10.0
}
```

---

## Modern User Interface & Slash Commands

Assistant features a clean Material Design GUI styled with **SicOS Nerd Font** icons and high contrast:
* **Interactive Help & Reset**: Click the `?` icon in the upper-right corner or the dedicated microphone toggle on the left of the input field.
* **Interactive Model Selector Badge**: The active model pill badge right below the input box shows the current model and acts as a clickable dropdown selector to switch models on the fly without typing commands.
* **Multimodal File & Image Attachments (`+` Button & Clipboard `Ctrl+V`)**: Attach files via the integrated `+` button in the prompt bar, or paste copied screenshots/images directly with `Ctrl+V`. Includes removable chips preview and vision capability indicators.
* **Agentic Screen Inspection & Screenshot (`take_screenshot`)**: When asking Assistant to analyze errors, inspect what is on screen, or take a screenshot, the LLM autonomously triggers `take_screenshot`. Assistant temporarily hides its window to capture the clean desktop or active window via `grim` (Wayland) or `maim` (X11), restores the window, and injects the image directly into the multimodal loop for visual diagnosis.
* **Autonomous Webcam Surveillance Mode (`start_surveillance` / `stop_surveillance`)**: Ask Assistant to enter surveillance mode to monitor dynamic events (e.g., *"ponte en modo vigilancia y avísame cuando veas a una persona"*, *"vigila si pasa un gato"*). Assistant samples webcam frames periodically (default 5s) via `ffmpeg`, inspects them with multimodal vision models, saves triggered frames to `~/Downloads`, and issues critical desktop notifications (`notify-send`) and voice alerts. Surveillance remains continuously active (with intelligent alert cooldown) until explicitly asked to stop.
* **Auto-Focus & Smart Scroll**: Automatic focus on the input box upon launch, auto-scrolling during fast token streaming, and free manual scrolling when inspecting historical outputs.

### Built-in Slash Commands & Shortcuts
You can type the following commands directly into the prompt box:
* **/model**: Lists all available configured models indicating which one is active, or toggles to the next model in the list. Use `/model <name>` (e.g. `/model glm`) to switch directly to a specific model.
* **/role**: Lists all available system and user roles indicating which one is active. Use `/role <nombre>` (e.g. `/role news`, `/role default`) to switch roles dynamically and alter the system prompt.
* **/mcp** or **/servers**: Displays the status, priority, and active selection of all configured MCP servers.
* **/voice** or **/tts**: Toggles text-to-speech spoken audio output on/off.
* **/about**: Displays application version and architectural details.
* **/new** or **/reset**: Resets the current conversation context, wipes the chat memory file, and starts a fresh session.
* **/clear**: Clears messages from the screen and purges context history.
* **/help**: Displays an interactive help modal with quick command tips.
* **`v`**: Typing `v` and pressing Enter instantly toggles push-to-talk voice recording mode.
* **Speaker Button (`󰕾` / `󰝟`)**: Dedicated button next to the help button that toggles TTS audio output on demand (disabled by default on startup).

---

## 🎭 Roles & Persona Customization (`/role`)

Assistant supports specialized **Roles** that adjust the model's behavior, instructions, and system prompt dynamically without restarting the application:

### Hierarchy & Discovery
1. **System Roles (`src/roles/`)**:
   - The system ships with a base `default` role (`default.md` and `index.json`).
2. **User Roles (`~/.config/assistant/roles/`)**:
   - **Overriding `default`**: To customize the base role, simply create `~/.config/assistant/roles/default.md`. Assistant will prioritize your user file over the built-in system prompt.
   - **Adding New Roles**: Drop any Markdown file into `~/.config/assistant/roles/` (e.g. `news.md`). You can also create `roles.json` to configure human-readable names and descriptions:
     ```json
     {
       "roles": {
         "news": {
           "name": "News Researcher",
           "description": "Periodista y analista de noticias de actualidad y última hora.",
           "file": "news.md"
         }
       }
     }
     ```
   - **Dynamic Switching**: Use `/role` to list available roles or `/role news` to activate one. The active role instructions are loaded into the context of the active session on the fly.

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

### MCP Servers Configuration & Dynamic Routing

Assistant supports the Model Context Protocol (MCP) using SSE and streamable HTTP transports. Servers are configured under the `"mcp_servers"` key in `~/.config/assistant/config.json`:

```json
{
  "mcp_servers": {
    "searxng": {
      "url": "http://127.0.0.1:32771/mcp",
      "transport": "streamable_http",
      "type": "web_search",
      "priority": 10,
      "description": "Internet search via local SearXNG instance."
    },
    "company_rag": {
      "url": "http://127.0.0.1:8501/mcp",
      "transport": "sse",
      "type": "rag",
      "description": "Internal technical documentation, employee onboarding manuals, and corporate wiki."
    }
  }
}
```

#### MCP Server Attributes & Supported Types:
* **`type`**: Semantic category for routing and execution:
  * `"web_search"`: Provider for web searches. Tools from these servers are unified transparently behind the canonical `web_search` tool (ordered by `priority`), with DuckDuckGo as automatic fallback. The LLM only sees a single clean `web_search` schema.
  * `"rag"` / `"knowledge_base"`: Semantic document retrieval. The Decision Router dynamically registers this category and routes related user queries here.
  * `"filesystem"` / `"notes"` / `"desktop_and_apps"`: Merges tools directly into standard system categories.
  * Custom types (e.g. `"github"`, `"home_automation"`): Dynamically registered in the `DecisionRouter` with their provided `description`.
  * Omitted or `"always"`: Tools are treated as global tools and injected whenever tools are active (non-conversational turns).
* **`priority`** *(integer, default 0)*: Disambiguates multiple providers of the same type (e.g. multiple search engines).
* **`description`** *(string)*: Informs the FastFlowLM `d3:edge` router criteria so it accurately routes user intents to this server's tools.

### Multi-Round Agentic Loop & Reasoning Models
Requests are resolved through a multi-round agentic loop instead of a fixed two-turn flow:

* **Chained tool execution**: Every round executes *all* requested tools (native + MCP) and feeds canonical OpenAI-compliant tool messages (`role: "tool"`, `tool_call_id`) back to the model, maintaining full compatibility with both compact models (e.g. Gemma 4, Llama 3) and large reasoning models. The loop is capped at 4 tool rounds (with automatic loop prevention if identical calls repeat); the final round withholds the tools schema to force a plain text answer.
* **Reasoning model support**: Reasoning models (GLM / DeepSeek style) stream their chain of thought in a separate `reasoning_content` field. Assistant captures it and surfaces a live "Thinking…" status snippet instead of appearing frozen, and logs it for diagnostics.
* **Silent completions**: Successful state-changing desktop actions (app launches, `hyprctl dispatch`, `wpctl set-volume`, `brightnessctl set`) still finish immediately with a silent "Hecho." turn without conversational clutter. Read-only inspection commands (`hyprctl clients/binds/monitors`, `wpctl status`, …) keep the agentic loop alive so the model can use their results instead of ending the request mid-investigation.
* **LLM diagnostics**: Requests, executed tools (with arguments), captured reasoning, and answers are logged to `~/.local/state/assistant/assistant.log`, making model-specific issues easy to debug.

### Modular Architecture (`src/properties/`, `src/tools/`, `src/routing/`, `src/commands/`, `src/mcp/`, `src/llm/` & `src/voice/`)
The codebase follows a modular design separating configuration and properties, execution tools, intent routing, UI slash commands, model catalogs, voice engines, and external protocol managers:
* **`src/properties/manager.py`**: Singleton Properties & Configuration Manager (`PropertiesManager`) centralizing path resolution (config, data, sounds, state, knowledge, roles, QML), loading and managing `config.json`, roles management (system & user roles), dynamic runtime properties (`active_model_key`, `tts_enabled`, `is_voice_mode`), and property change listeners.
* **`src/voice/tts.py`**: Audio and speech synthesis worker (`TTSWorkerThread`, `stop_all_media_playback`) managing Edge-TTS / Piper ONNX local execution, language detection, and PipeWire playback.
* **`src/llm/generator.py`**: Multi-round agentic loop worker (`AIGeneratorThread`, `extract_reasoning`), streaming tokens, DeepSeek/GLM reasoning capture, command approval safety checks, and loop prevention.
* **`src/llm/catalog.py`**: Multi-model intelligence manager (`ModelCatalog`) managing presets, OpenAI client instantiation, runtime model catalog, endpoint discovery (`/models`), and QML selector grouping.
* **`src/tools/dispatch.py`**: Domain-wide tool dispatcher (`dispatch_tool`) mapping tool calls to implementations and evaluating silent desktop completion policies.
* **`src/mcp_client/manager.py`**: Model Context Protocol client manager (`MCPManager`) coordinating SSE/HTTP transport connections, dynamic tool discovery, and remote tool execution.
* **`src/commands/`**: Command/Registry pattern implementing interactive slash commands (`/new`, `/reset`, `/clear`, `/help`, `/about`, `/voice`, `/tts`, `/model`, `/role`, `/mcp`) through decoupled modules (`session.py`, `system.py`, `voice.py`, `model.py`, `role.py`, `mcp.py`).
* **`src/routing/routing.py`**: Encapsulates `DecisionRouter` for FastFlowLM / d3:edge System One categorization, dynamic category-to-tools resolution, latency measurement, and fallback handling.
* **`src/tools/time.py`**: Local time/date resolution with IANA timezone support (`get_current_time`).
* **`src/tools/weather.py`**: Weather forecast resolution with Open-Meteo & wttr.in fallback (`get_weather`).
* **`src/tools/cinema.py`**: Free and native cinema showtimes and movie schedule extractor based on Tu Cine / `tucine.app` (`get_cinema_showtimes`).
* **`src/tools/obsidian.py`**: Obsidian Local REST API connector (`search_obsidian`, `read_obsidian`, `write_obsidian`).
* **`src/tools/web.py`**: Web search with SearXNG MCP priority and DuckDuckGo fallback (`perform_web_search`).
* **`src/tools/os.py`**: OS, desktop app discovery (`uwsm app` / `kitty`), and filesystem operations (`read_file`, `write_file`, `replace_in_file`, `open_application`, `execute_command`).
* **`src/tools/knowledge.py`**: SicOS markdown knowledge base queries and dynamic compositor shortcuts (`get_system_knowledge`, `get_sicos_shortcuts`).
* **`src/tools/hyprland.py`**: Versioned Hyprland dispatch, recipe management, and auto-discovery (`manage_desktop`, `load_hyprland_recipes`, `discover_and_learn_action`).

### Built-in & MCP Tools
1. `get_current_time(timezone)`: Retrieves real-time date, time, weekday, and timezone natively from the host system (with optional IANA timezone conversion, replacing external time MCP servers). Anchors time-sensitive queries, relative dates ('hoy', 'ayer'), and web searches.
2. `open_application(app, target)`: Universally detects installed desktop applications (`.desktop` entries via XDG) and CLI tools (`yazi`, `btop`, etc.). Automatically wraps GUI tools with `uwsm app` for Wayland session consistency and spawns terminal apps inside `kitty`. Silent execution prevents repetitive audio or unnecessary chat turns.
3. `get_weather(location)`: Fetches precise meteorological data and multi-day forecasts via Open-Meteo's geocoding API with localized WMO status codes.
4. `get_cinema_showtimes(cinema, date, movie)`: Extracts live cinema schedules, movie listings, formats (ATMOS, VOSE, Digital), and showtimes directly from `tucine.app` without requiring any API key or subscription. Automatically resolves cinema aliases (e.g. *Odeón León*, *Cines Van Gogh*) and queries showtimes across the entire week by relative days (*hoy*, *mañana*, *sábado*, etc.).
5. `manage_desktop(action, target, window_address)`: Hyprland workspace and window manager controller:
   * **Versioned Knowledge Base**: Dynamically detects the installed Hyprland version and chooses the exact dispatcher syntax (e.g., Lua dispatchers for v0.57+).
   * **KB-Driven Tool Schema**: The `manage_desktop` action enum and descriptions are generated from `hyprland_knowledge.json`, so new or user-learned actions become available to the model without code changes. `_command_intents` in the same file classifies shell commands as state-changing (silent) vs read-only (agentic loop continues).
   * **User Mutable Knowledge Layer**: Learns and saves unknown dispatchers to `~/.local/share/assistant/hyprland_knowledge.json`.
   * **Window Targeting**: Targets and focuses specific windows for operations such as `focus_window`, `close`, `float`, and `fullscreen`.
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
