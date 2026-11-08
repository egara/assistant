#!/usr/bin/env bash

## DYNAMIC ARGUMENT PARSING
MODE_VOICE=false
MODE_CLIPBOARD=false
MODE_SCREEN=false
TOGGLE_FLAG=""

for arg in "$@"; do
    case $arg in
        --voice) MODE_VOICE=true ;;
        --clipboard) MODE_CLIPBOARD=true ;;
        --screen) MODE_SCREEN=true ;;
        --toggle) TOGGLE_FLAG="--toggle" ;;
    esac
done

CONFIG_FILE="$HOME/.config/assistant/config.json"
TEMP_DIR="/tmp/assistant"
PID_FILE="$TEMP_DIR/initial_recording.pid"
SOUND_DIR="$HOME/.local/share/assistant/sounds"
mkdir -p "$TEMP_DIR" "$SOUND_DIR"

if [ ! -f "$CONFIG_FILE" ]; then assistant-gui ""; exit 1; fi

WHISPER_MODEL=$(jq -r '.voice.whisper_model // empty' "$CONFIG_FILE" 2>/dev/null)
ASR_ENABLED=$(jq -r '.voice.asr.enabled // false' "$CONFIG_FILE" 2>/dev/null)
ASR_ENDPOINT=$(jq -r '.voice.asr.endpoint // empty' "$CONFIG_FILE" 2>/dev/null)
ASR_MODEL=$(jq -r '.voice.asr.model // "whisper"' "$CONFIG_FILE" 2>/dev/null)
ASR_LANG=$(jq -r '.voice.asr.language // empty' "$CONFIG_FILE" 2>/dev/null)
ASR_API_KEY=$(jq -r '.voice.asr.api_key // empty' "$CONFIG_FILE" 2>/dev/null)
SILENCE_DURATION=$(jq -r '.voice.silence_duration // "0.8"' "$CONFIG_FILE" 2>/dev/null)
SILENCE_THRESHOLD=$(jq -r '.voice.silence_threshold // "1%"' "$CONFIG_FILE" 2>/dev/null)

if [[ -z "$WHISPER_MODEL" ]]; then
    WHISPER_MODEL="$HOME/.local/share/assistant/models/ggml-small.bin"
else
    WHISPER_MODEL="${WHISPER_MODEL/#\~/$HOME}"
fi

# Only download fallback whisper model if remote ASR is disabled and model doesn't exist
if [[ "$ASR_ENABLED" != "true" || -z "$ASR_ENDPOINT" ]]; then
    if [ ! -f "$WHISPER_MODEL" ]; then
        notify-send -u normal "Assistant AI" "Downloading Whisper model (ggml-small.bin) for the first time..."
        mkdir -p "$(dirname "$WHISPER_MODEL")"
        wget -q --show-progress -O "$WHISPER_MODEL" "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin"
    fi
fi

if pgrep -f "assistant-gui" > /dev/null; then
    echo "toggle" > "$TEMP_DIR/gui_signal"
    exit 0
fi

if [[ "$TOGGLE_FLAG" == "--toggle" && -f "$PID_FILE" ]]; then
    if kill -0 $(cat "$PID_FILE") 2>/dev/null; then
        kill $(cat "$PID_FILE") 2>/dev/null
        rm -f "$PID_FILE"
        pw-play "$SOUND_DIR/stop.wav" 2>/dev/null &
        exit 0
    fi
fi

if pgrep -x "rec" > /dev/null; then
    notify-send -u critical "🎙️ Mic Busy!" "Assistant is already recording."
    exit 1
fi

## CONTEXT GATHERING
ACTIVE_WIN=""
if command -v hyprctl &> /dev/null && [ "$XDG_SESSION_TYPE" == "wayland" ]; then
    ACTIVE_WIN=$(hyprctl activewindow | head -n 1 | awk '{print $2}')
fi
WIN_CONTEXT=""
if [[ -n "$ACTIVE_WIN" && "$ACTIVE_WIN" != "Invalid" ]]; then
    WIN_CONTEXT="[SYSTEM: The user's active window address is 0x$ACTIVE_WIN.] "
fi

CLIP_CONTEXT=""
if [[ "$MODE_CLIPBOARD" == true ]]; then
    if [ "$XDG_SESSION_TYPE" == "wayland" ] && command -v wl-paste &> /dev/null; then
        MIME_TYPE=$(wl-paste --list-types | head -n 1)
        if [[ "$MIME_TYPE" == text/* ]]; then CLIP_CONTEXT="Regarding this text: '''$(wl-paste)'''. "
        elif [[ "$MIME_TYPE" == image/* ]]; then wl-paste > "$TEMP_DIR/clipboard.png"; CLIP_CONTEXT="[IMAGE PATH: $TEMP_DIR/clipboard.png] "
        elif [[ "$MIME_TYPE" == "text/uri-list" ]]; then CLIP_CONTEXT="[FILE PATH: $(printf '%b' "$(wl-paste | head -n 1 | sed 's/^file:\/\///' | sed 's/%/\\x/g')")] "; fi
    fi
elif [[ "$MODE_SCREEN" == true ]]; then
    rm -f "$TEMP_DIR/screen.png"
    if [ "$XDG_SESSION_TYPE" == "wayland" ] && command -v grim &> /dev/null; then grim "$TEMP_DIR/screen.png"; fi
    if [ -f "$TEMP_DIR/screen.png" ]; then CLIP_CONTEXT="[IMAGE PATH: $TEMP_DIR/screen.png] Regarding this screenshot: "; fi
fi

USER_PROMPT=""
LAUNCH_FLAG="--text"

if [[ "$MODE_VOICE" == true ]]; then
    LAUNCH_FLAG="--voice"
    pw-play "$SOUND_DIR/start.wav" 2>/dev/null &
    
    if [[ "$TOGGLE_FLAG" == "--toggle" ]]; then
        notify-send -t 3000 "🎙️ Assistant" "Listening... Press shortcut again to stop."
        rec -q -r 16000 -c 1 -b 16 -e signed-integer "$TEMP_DIR/voice.wav" &
        echo $! > "$PID_FILE"
        wait $! 
    else
        rec -q -r 16000 -c 1 -b 16 -e signed-integer "$TEMP_DIR/voice.wav" silence 1 0.1 "$SILENCE_THRESHOLD" 1 "$SILENCE_DURATION" "$SILENCE_THRESHOLD"
        pw-play "$SOUND_DIR/stop.wav" 2>/dev/null &
    fi
    
    RAW_TRANSCRIPT=""
    if [[ "$ASR_ENABLED" == "true" && -n "$ASR_ENDPOINT" && -f "$TEMP_DIR/voice.wav" ]]; then
        CURL_ARGS=(-s -X POST "$ASR_ENDPOINT" --max-time 10 -F "file=@$TEMP_DIR/voice.wav" -F "model=$ASR_MODEL")
        if [[ -n "$ASR_LANG" ]]; then
            CURL_ARGS+=(-F "language=$ASR_LANG")
        fi
        if [[ -n "$ASR_API_KEY" ]]; then
            CURL_ARGS+=(-H "Authorization: Bearer $ASR_API_KEY")
        fi
        RAW_TRANSCRIPT=$(curl "${CURL_ARGS[@]}" 2>/dev/null | jq -r '.text // empty' 2>/dev/null)
    fi

    # Fallback to local whisper-cli if ASR was not used or failed
    if [[ -z "$RAW_TRANSCRIPT" && -f "$TEMP_DIR/voice.wav" && -f "$WHISPER_MODEL" ]]; then
        RAW_TRANSCRIPT=$(whisper-cli -m "$WHISPER_MODEL" -f "$TEMP_DIR/voice.wav" -nt -l auto 2>/dev/null)
    fi
    USER_PROMPT=$(echo "$RAW_TRANSCRIPT" | sed -e 's/\[.*\]//g' | xargs)
fi

# Launch PyQt6 GUI with combined context and voice modes
if [[ -n "$CLIP_CONTEXT$USER_PROMPT" ]]; then
    FINAL_PROMPT="$WIN_CONTEXT$CLIP_CONTEXT$USER_PROMPT"
else
    FINAL_PROMPT=""
fi
assistant-gui "$FINAL_PROMPT" "$LAUNCH_FLAG" &
