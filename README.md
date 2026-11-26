# potetarch-dotfile

My Arch Linux and Hyprland desktop with a floating lavender Waybar, custom
GTK menus for Wi-Fi, VPNs, Bluetooth, and sound, plus a matching notification
center, volume and brightness overlay, and lock screen.

![Live Hyprland workspace with the purple wallpaper, Waybar, and Fastfetch in Kitty](screenshots/workspace.png)

*Arch Linux and Hyprland with Fastfetch in Kitty. The cover is a live desktop
capture. The component previews below use sample data.*

## Motivation

I wanted a simple Hyprland setup I could use every day. The design stays
minimal, with a compact bar, a muted palette, and controls that open when
needed. Everyday tasks like switching workspaces, connecting to Wi-Fi,
changing audio devices, and checking notifications are easy to reach from
the bar or keyboard.

See the [changelog](CHANGELOG.md) for updates.

## Install

On Arch Linux, from your regular user account:

```sh
git clone https://github.com/wasiulhaque/potetarch-dotfile.git
cd potetarch-dotfile
./install.sh
```

The installer upgrades Arch and installs the desktop packages through Pacman.
It uses `yay` for Vicinae and Waypaper if either is missing. Install those
AUR packages yourself or have `yay` available.

If Rust and Cargo are available, the installer builds the Waybar controller
from the checked-in Cargo lockfile. The first build needs access to crates.io
or a populated Cargo cache. If Rust is unavailable, answer **Y** at the prompt
to install the original Python panels with the rest of the setup. Answering
**N** lets a full install add Rust and build the controller.

Run `./install.sh --config-only` to skip Pacman and AUR installs. The Rust
backend needs Cargo, a C toolchain, `pkg-config`, GTK 4, GTK4 Layer Shell 1.3+,
and libnm. If Rust is unavailable in this mode, choose Python or install
Rust yourself and rerun. Both options use Python 3 for desktop scripts.

The script copies the files to `${XDG_CONFIG_HOME:-~/.config}` and backs up
files it replaces under `${XDG_STATE_HOME:-~/.local/state}/potetarch/`. It
prints the backup path. To restore, copy the backed-up files to your config
directory and remove any newly installed files you no longer want. Log out
and back into Hyprland to load the full setup. For Bluetooth on a machine
with a supported adapter, start and enable BlueZ with
`sudo systemctl enable --now bluetooth.service`.

The source desktop runs Hyprland 0.57.2 and Waybar 0.16.0. If you are switching
from a legacy `hyprland.conf`, make sure the session loads `hyprland.lua`.

## Hyprland

The [Lua config](config/hypr/hyprland.lua) puts the main application commands,
display scaling, gaps, borders, and media key increments at the top. Displays
use their preferred mode, automatic positioning, and 1× scaling. The desktop
uses Dwindle tiling, 2 px inner gaps, 5 px outer gaps, lavender active borders,
10 px window rounding, shadows, and background blur.

Window entry uses a spring with a pop-in transition. Workspaces slide
horizontally. The US keyboard layout and natural touchpad scrolling are enabled,
and a three-finger horizontal swipe changes workspaces. The config includes
rules for the Vicinae layer, maximize requests, XWayland drag windows, and the
Hyprland run dialog. The [HyprMod settings file](config/hypr/hyprland-gui.lua)
hides the default logo and splash.

Startup launches the Rust Waybar controller when selected, Waybar, the volume and
brightness overlay, Vicinae, Hypridle, and awww, then restores the wallpaper
through Waypaper. Hyprland also starts SwayNC and the KDE PolicyKit agent as
systemd user services.

### General shortcuts

| Shortcut | Action |
| --- | --- |
| Super + Return / E | Open Kitty / Dolphin |
| Super + Space or R | Open Vicinae |
| Super + N / L | Open notifications / lock the screen |
| Super + Q / M | Close the focused window / log out |
| Super + Up / Down | Focus the window above / below |
| Super + V / P / J | Toggle floating / pseudotiling / the split direction |
| Super + left-drag / right-drag | Move / resize the focused window |
| Brightness, audio, and playback keys | Adjust brightness or volume in 5% steps, mute sound or mic, and control playback |

### Workspace shortcuts

These shortcuts make multiple workspaces easy to use on a single screen.
Use the arrow keys to move between tasks, jump straight to a numbered
workspace, or send a window to the next workspace and follow it. An empty
workspace is one shortcut away when you need a clean space.

| Shortcut | Action |
| --- | --- |
| Super + Left / Right | Switch to an adjacent workspace |
| Super + Shift + Left / Right | Move the focused window and follow it |
| Super + Shift + Down | Jump to the next empty workspace on this monitor |
| Super + 1–0 / Super + Shift + 1–0 | Switch to workspace 1–10 / move the focused window there |
| Super + S / Super + Shift + S | Toggle the scratchpad / send the focused window there |
| Super + scroll | Cycle through existing workspaces |
| Three-finger horizontal swipe | Switch workspaces on a touchpad |

### Volume and brightness overlay

The [overlay](config/hypr/osd.py) shows the current level near the bottom
of the focused display when volume, mute, or backlight changes. It uses the
Waybar palette, clears itself after about 1.4 seconds, and lets pointer input
pass through. Volume up also unmutes the output. Brightness feedback requires
a backlight device exposed by the system.

![Two overlay previews showing 42 percent volume and 68 percent brightness](screenshots/osd-preview.png)

## Waybar

The [bar layout](config/waybar/config.jsonc) uses one 36 px floating panel per
display. The [stylesheet](config/waybar/style.css) supplies the translucent
violet background, thin border, rounded corners, JetBrains Mono Nerd Font,
and matching active, hover, urgent, and tooltip colors.

- **Left** has an Arch icon that opens Vicinae, three persistent workspace
  indicators, the focused window title, and CPU, RAM, and temperature readings.
  Workspaces 1–3 are always shown. Higher-numbered workspaces appear when
  Hyprland creates them on that display and disappear when it removes them.
- **Center** has the clock and date, an alternate long date on click, and a
  calendar tooltip.
- **Right** has Tailscale status, Wi-Fi, Bluetooth and sound controls, battery,
  power profile, tray, an unread notification count, and current weather.
  Wi-Fi and sound icons reflect signal and volume, and the battery icon
  reflects charge level and charging state. The tray uses 16 px icons.
  Brightness changes are shown by the overlay.

The [Tailscale module](config/waybar/tailscale-status.py) changes the dot icon’s
opacity with connection state and shows a status tooltip. [Weather](config/waybar/weather.py)
uses Open-Meteo and refreshes about every 15 minutes after a successful fetch.
To enable it, create `weather-location.json` next to the installed script with
your coordinates, for example `{"latitude": 51.51, "longitude": -0.13}`, and
restart Waybar. The coordinates are sent to Open-Meteo.

Battery status depends on available hardware. The temperature module
uses the Intel `coretemp` sensor by default. Set the sensor in Waybar’s config
to match your hardware.

## Wi-Fi, VPN, Bluetooth, and sound menus

The network, Bluetooth, and volume modules open [custom Rust and GTK 4 panels](waybar-controls/).
With the Rust backend, Hyprland starts one controller at login. Waybar clicks
open or toggle panels over the user D-Bus, avoiding a new process and GTK initialization on each
click. If the controller is absent, the first click starts it on demand. With
the Python backend, the same Waybar buttons launch the original GTK panels.
The panels use Wayland layer shell to open below the bar on the selected
display. The media overlay, Bluetooth status, unread badge, and weather
helpers still use Python.

| Wi-Fi and VPN | Sound |
| --- | --- |
| ![Wi-Fi menu preview with a radio switch, nearby networks, and VPN controls](screenshots/wifi-preview.png) | ![Sound menu preview with output and input device lists and volume sliders](screenshots/audio-preview.png) |

| Expanded Wi-Fi details | Bluetooth |
| --- | --- |
| ![Expanded Wi-Fi panel with link rates and addresses](screenshots/wifi-details-preview.png) | ![Bluetooth menu showing a scan, paired devices, and headphones at 80 percent battery](screenshots/bluetooth-preview.png) |

- **Wi-Fi** shows nearby networks with signal strength and security status,
  highlights the connected network, and exposes a radio switch and rescan
  button. Saved networks can reconnect directly. Supported secured networks
  can be joined with an inline password prompt. **Network settings** opens
  NetworkManager’s connection editor. Click the connected network to expand
  live details for Wi-Fi standard, frequency, signal, Rx and Tx rates, local
  addresses, and gateway. Opening the details also queries [ipify](https://www.ipify.org/)
  for the internet-facing address of the current route, including a VPN exit
  node if one is in use.
- **VPN** lists saved NetworkManager VPN and WireGuard profiles and lets you
  connect or disconnect them. **Add VPN…** opens the connection editor.
  Tailscale gets its own status and switch when available. Changes use the
  configured Tailscale operator or the desktop PolicyKit prompt.
- **Bluetooth** can turn the adapter on or off, scan for nearby devices,
  and pair, connect, or disconnect them. The menu separates known and nearby
  devices, handles PIN and passkey prompts, and shows battery percentages
  where BlueZ reports them. The bar shows the lowest reported battery level
  among connected devices.
- **Sound** lists output and input devices, lets you change the default
  device, and gives the selected output and input their own volume sliders.
  The sliders preserve channel balance. **Audio settings** opens Pavucontrol.
  In Waybar, scroll adjusts volume and right-click toggles output mute.

The Wi-Fi menu uses NetworkManager. The sound menu uses PipeWire’s PulseAudio
compatibility layer. Tailscale is optional and uses your existing daemon and
sign-in.

## SwayNC notifications

The [notification center](config/swaync/config.json) opens below Waybar at the
top right. It contains Do Not Disturb, media controls that hide when idle, and
notification history. The [CSS](config/swaync/style.css) applies the same font
and violet palette to the center, notification cards, and media controls.
Left-click the Waybar bell or press **Super + N** to open it. Right-click the
bell to toggle Do Not Disturb. The bell’s badge counts unread notifications.
Opening the center clears the badge while preserving notification history.
A SwayNC receive hook and a D-Bus listener keep the count in sync.

![SwayNC preview with the unread count cleared while notifications remain in history](screenshots/notifications-preview.png)

## Hyprlock and Hypridle

![Hyprlock preview with a centered clock, date, password field, and keyboard layout](screenshots/hyprlock-preview.png)

[Hyprlock](config/hypr/hyprlock.conf) uses a solid dark background with a
rounded, lavender-bordered card. The time, date, password field, and keyboard
layout share the Waybar palette. The password field has separate checking and
failure states. Click the keyboard layout indicator to cycle layouts.

Press **Super + L** to lock. [Hypridle](config/hypr/hypridle.conf) handles
session lock requests and waits for the lock screen to activate before sleep.

## Vicinae, Kitty, and wallpaper

![Illustrated preview of the Vicinae launcher over Kitty](screenshots/desktop-preview.png)

- [**Vicinae**](config/vicinae/settings.json) uses the One Dark theme, JetBrains
  Mono Nerd Font, a compact launcher, and a floating status bar at 90% window
  opacity. Hyprland blurs the launcher background and disables its layer
  animation. AI and Raycast compatibility providers are disabled.
- [**Kitty**](config/kitty/kitty.conf) uses Bogster (Dark), an 8 px inset on
  the top and left, and Zsh as its shell.
- [**Waypaper**](config/waypaper/config.ini) uses awww and fill scaling with
  animated transitions. Pick an image in Waypaper or run
  `./install.sh --config-only --wallpaper /path/to/image.png`. Keep the image
  available at the selected path.

## Screenshot shortcuts

| Shortcut | Capture |
| --- | --- |
| Print | Select a region |
| Shift + Print | Select an output |
| Ctrl + Print | Select a window |

Hyprshot saves a PNG and copies it to the clipboard. Set `HYPRSHOT_DIR` in
your session environment to choose the save folder.

## To-do

- [ ] Add a hotkey to open a monitor layout picker.
- [ ] Add profiles for mirroring, extending the desktop, using only the
  built-in display, and using only the external display.
- [ ] Handle monitor disconnects and restore a usable layout automatically.

## Credits

- The original desktop and visual inspiration came from
  [retrilzzy/dotfiles](https://github.com/retrilzzy/dotfiles).
- The local `hyprland-waybar-arch-starter.zip` supplied the initial Waybar and
  SwayNC layouts and integration snippets used for this version.
- The compositor config also builds on [Hyprland’s default config and Lua API](https://wiki.hypr.land/).
- Kitty uses the Bogster (Dark) port by lhs, based on [Bogster by vv9k](https://github.com/vv9k/bogster).
- The Waybar status icon adapts the dot geometry from the [Tailscale favicon](https://tailscale.com/favicon.svg).
- OpenAI Codex helped package the dotfiles, write the installer and README,
  and create the configuration-based preview illustrations.

## License

Original work in this repository is licensed under the GNU General Public
License, version 3 only ([GPL-3.0-only](LICENSE)). Third-party components
retain their upstream licensing terms.
