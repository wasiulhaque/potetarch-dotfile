# Changelog

## 2026-11-26

### Added

- Prompt for the original Python Waybar panels when Rust is unavailable.
  Both backends use the same Waybar buttons and desktop configuration.

### Changed

- Ported the Wi-Fi, VPN, Bluetooth, and sound panels from Python to Rust
  while retaining their GTK 4 styling and controls.
- Start a single controller with Hyprland and route Waybar clicks over the
  user D-Bus. The launcher starts the controller on demand if needed.
- Build the controller from source using its Cargo lockfile during install.
  The installer places it alongside the Waybar launch script.

## 2026-11-25

### Added

- Added a BlueZ-backed Bluetooth status module and GTK panel for adapter
  power, discovery, pairing, and connection management. Devices with reported
  battery levels show their charge in the panel and contribute to the bar
  battery indicator.
- Added expandable details for the connected Wi-Fi network. The panel shows
  link standard, frequency, signal, link rates, local addresses, gateway,
  and the public IP of the current route. Expanding the panel requests the
  public IP from ipify.
- Added a SwayNC unread badge to Waybar. Viewing the notification center
  resets the unread counter while preserving history.
- Added a click-through volume, mute, and brightness overlay on the focused
  display. It watches audio and backlight changes and hides automatically.

### Changed

- Tightened the spacing on the right side of Waybar. Wi-Fi and sound use
  level-based icons, the battery has separate charge and charging icons, and
  tray icons are 16 px. The overlay takes over brightness feedback from the
  bar’s standalone backlight module.
- Kept the connected Wi-Fi row visible above the scrollable network list and
  gave the Wi-Fi and Bluetooth panels fixed heights.
- Made the volume-up key unmute the default output before raising its level.
- Added BlueZ, `iw`, `curl`, and GUdev to the installer’s package list.
- Refreshed the README gallery for the new Waybar and SwayNC states, and
  added previews of Bluetooth, Wi-Fi details, and the media overlay.
