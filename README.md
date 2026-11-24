# Potetarch Hyprland desktop

An Arch Linux desktop with a lavender Waybar, GTK menus for Wi-Fi, VPNs and audio,
and matching SwayNC and Hyprlock styles. It includes Hyprland’s Lua config,
Hypridle, Vicinae, Kitty, and Waypaper with the awww backend.

## Install

From an Arch Linux user account with Git and `sudo` available:

```sh
git clone https://github.com/wasiulhaque/potetarch-dotfile.git
cd potetarch-dotfile
./install.sh
```

The installer upgrades Arch and installs the desktop packages with Pacman. It
uses `yay` for Vicinae and Waypaper if either is missing. Have `yay` ready or
install the AUR packages first. If your packages are already set up, run
`./install.sh --config-only` to copy the configs using Python 3.

Files go into `${XDG_CONFIG_HOME:-~/.config}`. The installer backs up files it
replaces under `${XDG_STATE_HOME:-~/.local/state}/potetarch/` and prints the
backup directory. To restore an earlier configuration, copy the files from
that backup into your config directory and remove any newly installed files
you no longer want. The installer leaves your live session running. Log out
and select Hyprland to start the full setup.

This setup uses Hyprland’s Lua API. The source desktop runs Hyprland 0.57.2
and Waybar 0.16.0. If you are switching from a legacy `hyprland.conf`, make sure
your session loads `hyprland.lua`.

## Desktop and menus

| Control | Action |
| --- | --- |
| Super + Space or the Arch icon | Open Vicinae |
| Super + Return | Open Kitty |
| Super + E | Open Dolphin |
| Super + N or the bell | Toggle SwayNC |
| Super + L | Lock the screen with Hyprlock |
| Waybar network icon | Open Wi-Fi and VPN controls |
| Waybar volume indicator | Open the audio menu |
| Print / Shift + Print / Ctrl + Print | Capture a region / output / window |

- **Network and audio** use NetworkManager and PipeWire’s PulseAudio compatibility
  layer. Keep NetworkManager running if it manages your connection, and use one
  notification daemon so SwayNC can claim the notification service.
- **Tailscale** integration detects the Tailscale client and its daemon when
  available. It uses your machine’s normal PolicyKit authorization for state
  changes. Set up Tailscale separately if you use it.
- **Weather** uses Open-Meteo. Add a local `weather-location.json` alongside
  `weather.py` with numeric coordinates such as
  `{"latitude": 51.51, "longitude": -0.13}`, then restart Waybar. The module
  sends those coordinates when fetching weather.
- **Wallpaper** can be selected in Waypaper or with
  `./install.sh --wallpaper /path/to/image.png`. Keep that image
  available at the selected path. The Peanuts wallpaper from the local setup
  has no verified redistribution source in the starter files.
- **Hardware modules** include battery and backlight. If you use a non-Intel
  system, set the temperature sensor in `waybar/config.jsonc` for your hardware.
- **Monitor layout** defaults to preferred resolution, automatic placement, and
  scale 1. Edit the monitor rule in `hypr/hyprland.lua` if needed. HyprMod and
  HyprMonCfg can be installed separately for GUI editing.

## Screenshots and lock screen

The three Print key shortcuts use Hyprshot to select a region, output, or
window, then save a PNG and copy it to the clipboard. Set `HYPRSHOT_DIR` in
your session environment to choose a screenshot folder.

The [Hyprlock theme](config/hypr/hyprlock.conf) uses the Waybar palette and
includes a clock, date, password field, and keyboard layout indicator.
[Hypridle](config/hypr/hypridle.conf) invokes Hyprlock before suspend.

## Credits

- The original desktop setup and visual inspiration came from
  [retrilzzy/dotfiles](https://github.com/retrilzzy/dotfiles).
- The locally distributed `hyprland-waybar-arch-starter.zip` provided the
  initial Waybar and SwayNC layouts and Hyprland integration snippets used
  to build this version.
- The compositor config also builds on [Hyprland’s default configuration
  and Lua API](https://wiki.hypr.land/).
- Kitty uses the Bogster (Dark) port by lhs, based on
  [Bogster by vv9k](https://github.com/vv9k/bogster).
  The Waybar status icon adapts the dot geometry from the
  [Tailscale favicon](https://tailscale.com/favicon.svg).
