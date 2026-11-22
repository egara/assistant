#!/usr/bin/env bash
set -e

GREEN='\033[1;32m'
BLUE='\033[1;34m'
YELLOW='\033[1;33m'
RED='\033[1;31m'
BOLD='\033[1m'
NC='\033[0m'

if [ "$EUID" -eq 0 ]; then
  echo -e "${RED}Please do NOT run this script as root.${NC}"
  exit 1
fi

SHARE_DIR="$HOME/.local/share/assistant"
STATE_DIR="$HOME/.local/state/assistant"
CONFIG_DIR="$HOME/.config/assistant"
LOCAL_BIN="$HOME/.local/bin"
TEMP_DIR="/tmp/assistant"

AUTO_CONFIRM=false
CONFIG_CHOICE="" # "delete", "keep", or "" (ask)

# Parse command-line options
for arg in "$@"; do
  case $arg in
    -y|--yes)
      AUTO_CONFIRM=true
      ;;
    --delete-config|--purge)
      CONFIG_CHOICE="delete"
      ;;
    --keep-config)
      CONFIG_CHOICE="keep"
      ;;
    -h|--help)
      echo "Usage: ./uninstall.sh [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  -y, --yes          Skip initial confirmation and proceed with uninstall"
      echo "  --delete-config    Delete ~/.config/assistant without asking"
      echo "  --keep-config      Preserve ~/.config/assistant without asking"
      echo "  --purge            Alias for --delete-config"
      echo "  -h, --help         Show this help message"
      exit 0
      ;;
    *)
      echo -e "${YELLOW}Warning: Unknown option '$arg' ignored.${NC}"
      ;;
  esac
done

echo -e "${BLUE}==========================================${NC}"
echo -e "${BLUE}       🗑️  Uninstalling Assistant AI       ${NC}"
echo -e "${BLUE}==========================================${NC}"

# Detect existing components
HAS_PROCESS=false
if pgrep -f "assistant-gui" >/dev/null 2>&1 || pgrep -f "assistant.sh" >/dev/null 2>&1; then
  HAS_PROCESS=true
fi

echo -e "\n${BOLD}The following components and paths have been detected:${NC}"

echo -e "\n${YELLOW}1. Active Processes:${NC}"
if [ "$HAS_PROCESS" = true ]; then
  echo -e "   ${RED}● Running Assistant processes found (will be terminated)${NC}"
else
  echo -e "   ${GREEN}○ No running Assistant processes detected${NC}"
fi

echo -e "\n${YELLOW}2. Executable Wrappers (in ~/.local/bin):${NC}"
for bin in "$LOCAL_BIN/assistant" "$LOCAL_BIN/assistant-gui"; do
  if [ -f "$bin" ] || [ -L "$bin" ]; then
    echo -e "   ${RED}● $bin${NC}"
  else
    echo -e "   ${NC}○ $bin (not present)${NC}"
  fi
done

echo -e "\n${YELLOW}3. Application Data & Binaries (in ~/.local/share/assistant):${NC}"
if [ -d "$SHARE_DIR" ]; then
  SIZE_SHARE=$(du -sh "$SHARE_DIR" 2>/dev/null | cut -f1)
  echo -e "   ${RED}● $SHARE_DIR ($SIZE_SHARE)${NC}"
  [ -d "$SHARE_DIR/venv" ] && echo -e "     - Python virtual environment (venv)"
  [ -d "$SHARE_DIR/bin" ] && echo -e "     - Audio binaries (whisper-cli, piper)"
  [ -d "$SHARE_DIR/models" ] && echo -e "     - Quantized AI models (Whisper & Piper voices)"
  [ -d "$SHARE_DIR/lib" ] && echo -e "     - Python modules & packages"
  [ -d "$SHARE_DIR/sounds" ] && echo -e "     - Audio feedback files"
else
  echo -e "   ${NC}○ $SHARE_DIR (not present)${NC}"
fi

echo -e "\n${YELLOW}4. Runtime State, Logs & History (in ~/.local/state/assistant):${NC}"
if [ -d "$STATE_DIR" ]; then
  SIZE_STATE=$(du -sh "$STATE_DIR" 2>/dev/null | cut -f1)
  echo -e "   ${RED}● $STATE_DIR ($SIZE_STATE)${NC}"
  [ -f "$STATE_DIR/assistant.log" ] && echo -e "     - Diagnostics log (assistant.log)"
  [ -f "$STATE_DIR/chat_history.json" ] && echo -e "     - Message context history (chat_history.json)"
else
  echo -e "   ${NC}○ $STATE_DIR (not present)${NC}"
fi

echo -e "\n${YELLOW}5. Temporary Files:${NC}"
if [ -d "$TEMP_DIR" ]; then
  echo -e "   ${RED}● $TEMP_DIR (runtime buffers and sockets)${NC}"
else
  echo -e "   ${NC}○ $TEMP_DIR (not present)${NC}"
fi

echo -e "\n${YELLOW}6. Configuration & Custom Roles:${NC}"
if [ -d "$CONFIG_DIR" ]; then
  echo -e "   ${BLUE}ℹ $CONFIG_DIR exists (will prompt separately whether to keep or remove)${NC}"
else
  echo -e "   ${NC}○ $CONFIG_DIR (not present)${NC}"
fi

echo ""

read_input() {
  local prompt="$1"
  local var_name="$2"
  if [ -t 0 ]; then
    read -r -p "$prompt" "$var_name"
  else
    echo -n "$prompt"
    if ! read -r "$var_name"; then
      eval "$var_name='n'"
    fi
  fi
}

# First Confirmation: Proceed with uninstall?
if [ "$AUTO_CONFIRM" != true ]; then
  read_input "Are you sure you want to proceed with uninstalling Assistant? [y/N]: " confirm_uninstall
  case "$confirm_uninstall" in
    [yY]|[yY][eE][sS])
      ;;
    *)
      echo -e "\n${YELLOW}Uninstallation cancelled by user. No files were removed.${NC}"
      exit 0
      ;;
  esac
fi

# Second Confirmation: Delete configuration directory?
DELETE_CONFIG=false
if [ -d "$CONFIG_DIR" ]; then
  if [ "$CONFIG_CHOICE" == "delete" ]; then
    DELETE_CONFIG=true
  elif [ "$CONFIG_CHOICE" == "keep" ]; then
    DELETE_CONFIG=false
  else
    echo -e "\n${YELLOW}------------------------------------------------------------${NC}"
    echo -e "${BOLD}Configuration Directory Notice:${NC}"
    echo -e "Assistant stores your settings, custom roles, and knowledge at:"
    echo -e "  ${BLUE}$CONFIG_DIR${NC}"
    echo -e "If you keep it, your configuration and custom roles will be preserved"
    echo -e "in case you reinstall Assistant in the future."
    echo -e "${YELLOW}------------------------------------------------------------${NC}"

    read_input "Do you want to DELETE the configuration directory (~/.config/assistant)? [y/N]: " confirm_config
    case "$confirm_config" in
      [yY]|[yY][eE][sS])
        DELETE_CONFIG=true
        ;;
      *)
        DELETE_CONFIG=false
        ;;
    esac
  fi
fi

echo -e "\n${BLUE}Removing Assistant components...${NC}"

# 1. Terminate running processes
if [ "$HAS_PROCESS" = true ]; then
  echo -e "  -> Stopping running Assistant processes..."
  killall -q assistant-gui 2>/dev/null || true
  pkill -f "assistant-gui" 2>/dev/null || true
  pkill -f "assistant.sh" 2>/dev/null || true
  if [ -f "$TEMP_DIR/initial_recording.pid" ]; then
    kill -9 "$(cat "$TEMP_DIR/initial_recording.pid")" 2>/dev/null || true
  fi
  sleep 0.5
fi

# 2. Remove executable wrappers
echo -e "  -> Removing executable wrappers from ~/.local/bin..."
rm -f "$LOCAL_BIN/assistant" "$LOCAL_BIN/assistant-gui"

# 3. Remove application data and libraries
echo -e "  -> Removing data directory: ~/.local/share/assistant..."
rm -rf "$SHARE_DIR"

# 4. Remove runtime state and logs
echo -e "  -> Removing state directory: ~/.local/state/assistant..."
rm -rf "$STATE_DIR"

# 5. Clean up temporary runtime files
echo -e "  -> Cleaning temporary runtime files..."
rm -rf "$TEMP_DIR" /tmp/whisper_build /tmp/piper.tar.gz /tmp/piper 2>/dev/null || true

# 6. Handle configuration directory
if [ "$DELETE_CONFIG" = true ]; then
  echo -e "  -> ${RED}Removing configuration directory: ~/.config/assistant...${NC}"
  rm -rf "$CONFIG_DIR"
else
  if [ -d "$CONFIG_DIR" ]; then
    echo -e "  -> ${GREEN}Preserved configuration directory: ~/.config/assistant${NC}"
  fi
fi

echo -e "\n${GREEN}==========================================${NC}"
echo -e "${GREEN}      ✨ UNINSTALLATION COMPLETE ✨       ${NC}"
echo -e "${GREEN}==========================================${NC}"
if [ "$DELETE_CONFIG" = true ]; then
  echo -e "${BLUE}Assistant has been completely removed from your system (no leftover files).${NC}\n"
else
  echo -e "${BLUE}Assistant application files removed.${NC}"
  if [ -d "$CONFIG_DIR" ]; then
    echo -e "${YELLOW}Your configuration was kept at: $CONFIG_DIR${NC}"
    echo -e "To delete it manually later, run: rm -rf ~/.config/assistant\n"
  fi
fi
