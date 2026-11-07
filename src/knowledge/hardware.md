# Hardware & Multimedia Control in SicOS

SicOS uses modern Wayland-native utilities for audio, brightness, and display control.

## 1. Audio Control (PipeWire / WirePlumber)
Do NOT use `amixer`, `alsamixer`, or `pactl`. Always use **`wpctl`**.

- **Set exact volume**:
  - `wpctl set-volume @DEFAULT_AUDIO_SINK@ 1.0` (100% volume)
  - `wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.5` (50% volume)
  - `wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.0` (Mute / 0%)
- **Raise volume**:
  - `wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%+`
  - `wpctl set-volume @DEFAULT_AUDIO_SINK@ 10%+`
- **Lower volume**:
  - `wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%-`
  - `wpctl set-volume @DEFAULT_AUDIO_SINK@ 10%-`
- **Toggle Mute**:
  - `wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle`
  - `wpctl set-mute @DEFAULT_AUDIO_SINK@ 1` (Mute)
  - `wpctl set-mute @DEFAULT_AUDIO_SINK@ 0` (Unmute)

## 2. Screen Brightness (`brightnessctl`)
SicOS uses `brightnessctl` to control display backlights.

- **Set exact brightness**:
  - `brightnessctl set 100%`
  - `brightnessctl set 50%`
- **Increase brightness**:
  - `brightnessctl set +10%`
  - `brightnessctl set +5%`
- **Decrease brightness**:
  - `brightnessctl set 10%-`
  - `brightnessctl set 5%-`
- **Query current brightness**:
  - `brightnessctl -m`

## 3. Displays & Monitors (`kanshi` / `hyprctl`)
- **Reload multi-monitor setup**:
  - `kanshictl reload`
- **Open visual monitor manager**:
  - `sicos-monitors.sh` or trigger via keybinding `Super+K`.
- **Live scaling**:
  - Uses `sicos-monitor-scale.sh` or `hyprctl eval` for Hyprland Lua monitor dispatch.
