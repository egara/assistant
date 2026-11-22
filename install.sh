#!/usr/bin/env bash
set -e

GREEN='\033[1;32m'
BLUE='\033[1;34m'
YELLOW='\033[1;33m'
RED='\033[1;31m'
NC='\033[0m' 
USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "${BLUE}==========================================${NC}"
echo -e "${BLUE}       🤖 Installing Assistant AI         ${NC}"
echo -e "${BLUE}==========================================${NC}"

if [ "$EUID" -eq 0 ]; then
  echo -e "${RED}Please do NOT run this script as root.${NC}"
  exit 1
fi

SHARE_DIR="$HOME/.local/share/assistant"
BIN_DIR="$SHARE_DIR/bin"
MODELS_DIR="$SHARE_DIR/models"
SOUNDS_DIR="$SHARE_DIR/sounds"
LIB_DIR="$SHARE_DIR/lib"
VENV_DIR="$SHARE_DIR/venv"
LOCAL_BIN="$HOME/.local/bin"
CONFIG_DIR="$HOME/.config/assistant"

mkdir -p "$BIN_DIR" "$MODELS_DIR" "$SOUNDS_DIR" "$LIB_DIR" "$LOCAL_BIN" "$CONFIG_DIR"

echo -e "\n${YELLOW}[1/6] Detecting OS and installing system dependencies...${NC}"
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
    OS_LIKE=$ID_LIKE
else
    echo -e "${RED}Cannot detect OS (/etc/os-release missing). Installation aborted.${NC}"
    exit 1
fi

if [[ "$OS" == "arch" || "$OS_LIKE" == *"arch"* || "$OS" == "manjaro" || "$OS" == "endeavouros" || "$OS" == "cachyos" || "$OS" == "garuda" || "$OS" == "artix" || "$OS" == "omarchy" || -f /etc/arch-release ]] || command -v pacman >/dev/null 2>&1; then
    echo -e "${BLUE}Detected Arch Linux / Arch-based distribution.${NC}"
    sudo pacman -Syu --needed --noconfirm sox libpulse wireplumber jq pipewire pipewire-pulse wl-clipboard grim playerctl python python-pip python-virtualenv wget curl base-devel git xclip maim xdotool wmctrl cmake xcb-util-cursor ffmpeg libnotify
elif [[ "$OS" == "ubuntu" || "$OS" == "debian" || "$OS_LIKE" == *"debian"* || "$OS_LIKE" == *"ubuntu"* ]]; then
    echo -e "${BLUE}Detected Debian / Ubuntu distribution.${NC}"
    sudo apt-get update
    sudo apt-get install -y sox libsox-fmt-all jq pipewire-bin wl-clipboard grim playerctl python3-venv python3-pip wget curl build-essential git xclip maim xdotool wmctrl cmake libxcb-cursor0 ffmpeg libnotify-bin
elif [[ "$OS" == "fedora" || "$OS_LIKE" == *"fedora"* ]]; then
    echo -e "${BLUE}Detected Fedora distribution.${NC}"
    sudo dnf install -y sox jq pipewire-utils wl-clipboard grim playerctl python3-virtualenv python3-pip wget curl make automake gcc gcc-c++ git xclip maim xdotool wmctrl cmake xcb-util-cursor ffmpeg libnotify
else
    echo -e "${RED}Unsupported Linux distribution: OS=$OS, OS_LIKE=$OS_LIKE.${NC}"
    echo -e "${YELLOW}Please install the required dependencies manually and retry.${NC}"
    exit 1
fi

echo -e "\n${YELLOW}[2/6] Setting up isolated Python environment and dependencies...${NC}"
if [ ! -d "$VENV_DIR" ]; then python3 -m venv "$VENV_DIR"; fi
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install openai ddgs requests beautifulsoup4 edge-tts langdetect PyQt6 pypdf mcp

echo -e "\n${YELLOW}[3/6] Setting up AI Audio Binaries...${NC}"
if [ ! -f "$BIN_DIR/whisper-cli" ] || (command -v ldd >/dev/null && ldd "$BIN_DIR/whisper-cli" 2>&1 | grep -q "not found"); then
    echo "Rebuilding/repairing whisper-cli..."
    rm -rf /tmp/whisper_build
    git clone https://github.com/ggerganov/whisper.cpp.git /tmp/whisper_build
    cd /tmp/whisper_build
    
    cmake -B build -DBUILD_SHARED_LIBS=OFF
    cmake --build build --config Release -j$(nproc) || true
    
    WHISPER_BIN=""
    if [ -f "build/bin/whisper-cli" ]; then
        WHISPER_BIN="build/bin/whisper-cli"
    elif [ -f "build/bin/main" ]; then
        WHISPER_BIN="build/bin/main"
    else
        WHISPER_BIN=$(find . -type f \( -name "whisper-cli" -o -name "main" \) -executable | head -n 1)
    fi
    
    if [ -n "$WHISPER_BIN" ]; then
        cp "$WHISPER_BIN" "$BIN_DIR/whisper-cli"
    else
        echo -e "${RED}FATAL ERROR: Failed to find compiled whisper binary! Installation aborted.${NC}"
        exit 1
    fi
    
    cd "$REPO_DIR"
    rm -rf /tmp/whisper_build
fi

if [ ! -f "$BIN_DIR/piper" ]; then
    if [ "$(uname -m)" == "x86_64" ]; then
        echo "Downloading Piper TTS Binary..."
        rm -f /tmp/piper.tar.gz
        
        PIPER_URL="https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_x86_64.tar.gz"
        DOWNLOAD_SUCCESS=false
        
        MIRRORS=(
            "$PIPER_URL"
            "https://ghp.ci/$PIPER_URL"
            "https://ghfast.top/$PIPER_URL"
            "https://mirror.ghproxy.com/$PIPER_URL"
        )
        
        for url in "${MIRRORS[@]}"; do
            echo "Attempting download from: $url"
            if wget -c --tries=3 -U "$USER_AGENT" -q --show-progress -O /tmp/piper.tar.gz "$url"; then
                DOWNLOAD_SUCCESS=true
                break
            fi
        done
        
        if [ "$DOWNLOAD_SUCCESS" = true ] && [ -s /tmp/piper.tar.gz ]; then
            tar -xzf /tmp/piper.tar.gz -C /tmp/
            cp -r /tmp/piper/* "$BIN_DIR/"
            rm -rf /tmp/piper.tar.gz /tmp/piper
        else
            echo -e "${RED}FATAL ERROR: Failed to download Piper TTS. Installation aborted.${NC}"
            rm -f /tmp/piper.tar.gz
            exit 1
        fi
    fi
fi

echo -e "\n${YELLOW}[4/6] Downloading Local AI Models...${NC}"
cd "$MODELS_DIR"

needs_download=true
if [ -f "ggml-small.bin" ]; then
    filesize=$(stat -c%s "ggml-small.bin" 2>/dev/null || wc -c < "ggml-small.bin")
    if [ "$filesize" -ge 200000000 ]; then needs_download=false; fi
fi

if [ "$needs_download" = true ]; then 
    echo "Downloading Whisper Small (Q8 Quantized) model (~250MB)..."
    WHISPER_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small-q8_0.bin"
    HF_MIRROR="https://hf-mirror.com/ggerganov/whisper.cpp/resolve/main/ggml-small-q8_0.bin"
    
    if ! wget -c --tries=3 -U "$USER_AGENT" -q --show-progress -O ggml-small.bin "$WHISPER_URL"; then
        echo "Official link failed, trying HF-Mirror..."
        if ! wget -c --tries=3 -U "$USER_AGENT" -q --show-progress -O ggml-small.bin "$HF_MIRROR"; then
            echo -e "${RED}FATAL ERROR: Failed to download Whisper model. Installation aborted.${NC}"
            exit 1
        fi
    fi
fi

dl_piper() {
    if [ ! -f "$5.onnx" ]; then
        echo "  -> Downloading TTS Voice: $5..."
        
        local url1="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/$1/$2/$3/$4/$5.onnx"
        local url2="https://hf-mirror.com/rhasspy/piper-voices/resolve/v1.0.0/$1/$2/$3/$4/$5.onnx"
        
        if ! wget -c --tries=3 -U "$USER_AGENT" -q --show-progress -O "$5.onnx" "$url1"; then
            if ! wget -c --tries=3 -U "$USER_AGENT" -q --show-progress -O "$5.onnx" "$url2"; then
                echo -e "${RED}FATAL ERROR: Failed to download TTS voice model $5.onnx.${NC}"
                exit 1
            fi
        fi
        
        local json1="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/$1/$2/$3/$4/$5.onnx.json"
        local json2="https://hf-mirror.com/rhasspy/piper-voices/resolve/v1.0.0/$1/$2/$3/$4/$5.onnx.json"
        
        if ! wget -c --tries=3 -U "$USER_AGENT" -q -O "$5.onnx.json" "$json1"; then
            if ! wget -c --tries=3 -U "$USER_AGENT" -q -O "$5.onnx.json" "$json2"; then
                echo -e "${RED}FATAL ERROR: Failed to download TTS voice JSON $5.onnx.json.${NC}"
                exit 1
            fi
        fi
    fi
}

echo -e "\n${BLUE}Select TTS languages to install for offline voice synthesis:${NC}"
echo "  en - English              de - German         ru - Russian"
echo "  ar - Arabic               it - Italian        zh - Chinese"
echo "  fr - French               ja - Japanese       es - Spanish (Default)"
echo "  pt - Portuguese           all - Install all"

# Use tty redirection to read input safely in headless or pipeline environments
read -p "Enter languages separated by space (default: es en): " selected_langs </dev/tty || selected_langs="es en"

if [ -z "$selected_langs" ]; then selected_langs="es en"; fi
if [[ "$selected_langs" == *"all"* ]]; then selected_langs="es en ar fr de it ja pt ru zh"; fi

for lang in $selected_langs; do
    case $lang in
        en) dl_piper "en" "en_US" "lessac" "medium" "en_US-lessac-medium" ;;
        ar) dl_piper "ar" "ar_JO" "kareem" "medium" "ar_JO-kareem-medium" ;;
        fr) dl_piper "fr" "fr_FR" "upmc" "medium" "fr_FR-upmc-medium" ;;
        es) dl_piper "es" "es_ES" "sharvard" "medium" "es_ES-sharvard-medium" ;;
        de) dl_piper "de" "de_DE" "thorsten" "medium" "de_DE-thorsten-medium" ;;
        it) dl_piper "it" "it_IT" "riccardo" "xlow" "it_IT-riccardo-xlow" ;;
        ja) dl_piper "ja" "ja_JP" "dani" "low" "ja_JP-dani-low" ;;
        pt) dl_piper "pt" "pt_PT" "tugao" "medium" "pt_PT-tugao-medium" ;;
        ru) dl_piper "ru" "ru_RU" "denis" "medium" "ru_RU-denis-medium" ;;
        zh) dl_piper "zh" "zh_CN" "huashan" "medium" "zh_CN-huashan-medium" ;;
        *) echo -e "${YELLOW}Warning: Unknown language code '$lang', skipping.${NC}" ;;
    esac
done

# Force return to repository directory before starting the copy actions
cd "$REPO_DIR"

echo -e "\n${YELLOW}[5/6] Finalizing modules, assets, knowledge and configurations...${NC}"

# 1. UI Sounds
if [ -d "$REPO_DIR/sounds" ] && [ "$(ls -A "$REPO_DIR/sounds" 2>/dev/null)" ]; then 
    cp "$REPO_DIR/sounds"/* "$SOUNDS_DIR/"
else
    echo "Generating default UI sounds using sox..."
    sox -n "$SOUNDS_DIR/start.wav" synth 0.1 sine 800 vol 0.5 || true
    sox -n "$SOUNDS_DIR/stop.wav" synth 0.15 sine 400 vol 0.5 || true
fi

# 2. Configuration file (~/.config/assistant/config.json)
if [ ! -f "$CONFIG_DIR/config.json" ] && [ -f "$REPO_DIR/config.json" ]; then 
    cp "$REPO_DIR/config.json" "$CONFIG_DIR/config.json"
fi

# 3. Base Hyprland recipe knowledge
if [ -f "$REPO_DIR/src/hyprland_knowledge.json" ]; then
    cp "$REPO_DIR/src/hyprland_knowledge.json" "$SHARE_DIR/hyprland_knowledge.json"
    if [ ! -f "$SHARE_DIR/hyprland_knowledge.json" ]; then
        cp "$REPO_DIR/src/hyprland_knowledge.json" "$SHARE_DIR/hyprland_knowledge.json"
    fi
fi

# 4. Modular markdown knowledge base
if [ -d "$REPO_DIR/src/knowledge" ]; then
    mkdir -p "$SHARE_DIR/knowledge" "$CONFIG_DIR/knowledge"
    cp -r "$REPO_DIR/src/knowledge"/* "$SHARE_DIR/knowledge/"
    for f in "$REPO_DIR/src/knowledge"/*; do
        bname=$(basename "$f")
        if [ ! -f "$CONFIG_DIR/knowledge/$bname" ]; then
            cp "$f" "$CONFIG_DIR/knowledge/$bname"
        fi
    done
fi

# 5. Roles system
if [ -d "$REPO_DIR/src/roles" ]; then
    mkdir -p "$SHARE_DIR/roles" "$CONFIG_DIR/roles"
    cp -r "$REPO_DIR/src/roles"/* "$SHARE_DIR/roles/"
    if [ -f "$REPO_DIR/src/roles/default.md" ] && [ ! -f "$CONFIG_DIR/roles/default.md" ]; then
        cp "$REPO_DIR/src/roles/default.md" "$CONFIG_DIR/roles/default.md"
    fi
fi

# 6. Install full Python package modules into $SHARE_DIR/lib
cp -r "$REPO_DIR/src/properties" "$LIB_DIR/"
cp -r "$REPO_DIR/src/tools" "$LIB_DIR/"
cp -r "$REPO_DIR/src/routing" "$LIB_DIR/"
cp -r "$REPO_DIR/src/commands" "$LIB_DIR/"
cp -r "$REPO_DIR/src/mcp_client" "$LIB_DIR/"
cp -r "$REPO_DIR/src/llm" "$LIB_DIR/"
cp -r "$REPO_DIR/src/voice" "$LIB_DIR/"
if [ -d "$REPO_DIR/src/roles" ]; then cp -r "$REPO_DIR/src/roles" "$LIB_DIR/"; fi
if [ -d "$REPO_DIR/src/knowledge" ]; then cp -r "$REPO_DIR/src/knowledge" "$LIB_DIR/"; fi
if [ -f "$REPO_DIR/src/hyprland_knowledge.json" ]; then cp "$REPO_DIR/src/hyprland_knowledge.json" "$LIB_DIR/"; fi

# Core scripts and UI
cp "$REPO_DIR/trigger.sh" "$SHARE_DIR/assistant.sh"
cp "$REPO_DIR/src/assistant-gui.py" "$SHARE_DIR/assistant-gui.py"
cp "$REPO_DIR/src/ui.qml" "$SHARE_DIR/ui.qml"
cp "$REPO_DIR/src/ui.qml" "$LIB_DIR/ui.qml"
if [ -f "$REPO_DIR/uninstall.sh" ]; then
    cp "$REPO_DIR/uninstall.sh" "$SHARE_DIR/uninstall.sh"
    chmod +x "$SHARE_DIR/uninstall.sh"
fi
chmod +x "$SHARE_DIR/assistant.sh" "$SHARE_DIR/assistant-gui.py"

echo -e "\n${YELLOW}[6/6] Creating executable wrappers in ~/.local/bin...${NC}"

cat << 'EOF' > "$LOCAL_BIN/assistant-gui"
#!/usr/bin/env bash
SHARE_DIR="$HOME/.local/share/assistant"
BIN_DIR="$SHARE_DIR/bin"
VENV_DIR="$SHARE_DIR/venv"
LIB_DIR="$SHARE_DIR/lib"
LOCAL_BIN="$HOME/.local/bin"

export PATH="$LOCAL_BIN:$BIN_DIR:$VENV_DIR/bin:$PATH"
export PYTHONPATH="$LIB_DIR:$PYTHONPATH"
export LD_LIBRARY_PATH="$BIN_DIR:$LD_LIBRARY_PATH"

exec "$VENV_DIR/bin/python3" "$SHARE_DIR/assistant-gui.py" "$@"
EOF

cat << 'EOF' > "$LOCAL_BIN/assistant"
#!/usr/bin/env bash
SHARE_DIR="$HOME/.local/share/assistant"
BIN_DIR="$SHARE_DIR/bin"
VENV_DIR="$SHARE_DIR/venv"
LIB_DIR="$SHARE_DIR/lib"
LOCAL_BIN="$HOME/.local/bin"

export PATH="$LOCAL_BIN:$BIN_DIR:$VENV_DIR/bin:$PATH"
export PYTHONPATH="$LIB_DIR:$PYTHONPATH"
export LD_LIBRARY_PATH="$BIN_DIR:$LD_LIBRARY_PATH"

exec "$SHARE_DIR/assistant.sh" "$@"
EOF

chmod +x "$LOCAL_BIN/assistant" "$LOCAL_BIN/assistant-gui"

# Also symlink wrappers into internal bin directory
ln -sf "$LOCAL_BIN/assistant-gui" "$BIN_DIR/assistant-gui"
ln -sf "$LOCAL_BIN/assistant" "$BIN_DIR/assistant"

echo -e "\n${GREEN}        🎉 INSTALLATION COMPLETE 🎉       ${NC}"
echo -e "${BLUE}You can now run '${GREEN}assistant${BLUE}' or '${GREEN}assistant --voice${BLUE}' from anywhere!${NC}"
echo -e "${YELLOW}Ensure ~/.local/bin is in your PATH (e.g. in ~/.bashrc or ~/.zshrc):${NC}"
echo -e "  export PATH=\"\$HOME/.local/bin:\$PATH\"\n"
echo -e "${BLUE}To cleanly uninstall Assistant in the future, run: '${GREEN}./uninstall.sh${BLUE}' or '${GREEN}~/.local/share/assistant/uninstall.sh${BLUE}'\n${NC}"
