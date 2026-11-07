# SicOS Knowledge Index

SicOS is an opinionated, high-performance Linux desktop environment running on NixOS and Hyprland.
Knowledge is divided into modular topics:

- **`hardware`**: Volume/audio control (PipeWire/WirePlumber `wpctl`), screen brightness (`brightnessctl`), displays & multi-monitor management (`kanshictl`, `sicos-monitors`).
- **`apps`**: Installed applications, categorized by GUI and TUI, their executable commands, launch flags, and purpose.
- **`hyprland`**: Desktop architecture, window management rules, layouts (Dwindle, Master, Scrolling, Monocle), and `manage_desktop` actions.
- **`shortcuts`**: Live keyboard shortcuts currently configured in Hyprland (retrieved directly from the compositor).

To inspect any topic, use the tool `get_knowledge(topic="<topic_name>")`.
