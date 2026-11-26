import ctypes
import getpass
from ipaddress import ip_address
import json
import re
import subprocess
import sys

import gi

# Load before GTK binds any Wayland symbols.
ctypes.CDLL("libgtk4-layer-shell.so.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("NM", "1.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Gtk4LayerShell as Layer, NM, Pango

from bluetooth import ADAPTER, DEVICE, Bluetooth, PairingAgent, property_value

AP_FLAGS = getattr(NM, "80211ApFlags")
SECURITY = getattr(NM, "80211ApSecurityFlags")
PANEL_WIDTH = 345
FIXED_PANEL_HEIGHTS = {"wifi": 460, "bluetooth": 360}
PANEL_TITLES = {"wifi": "Wi-Fi", "audio": "Sound", "bluetooth": "Bluetooth"}
PANEL_GAP = 5
PULSE_VOLUME_NORMAL = 65536
VPN_TYPES = {"vpn", "wireguard"}
CSS = """
window {
  background: transparent;
  color: #cfccdc;
  font-family: "JetBrainsMono Nerd Font", "JetBrains Mono", sans-serif;
  font-size: 12px;
  font-weight: 500;
}
.card {
  /* Waybar’s rendered background on the current desktop. */
  background: #1c2030;
  border: 1px solid rgba(165, 151, 221, 0.29);
  border-radius: 12px;
  padding: 12px;
}
.title { font-size: 14px; font-weight: 700; }
.heading { color: #b8b2e9; font-weight: 700; }
.secondary { color: #b0acbf; }
.error { color: #f2a8b2; }
.wifi-details {
  background: #242331;
  border: 1px solid #5b526f;
  border-radius: 8px;
  padding: 8px;
}
button, entry {
  min-height: 0;
  border: 1px solid transparent;
  border-radius: 7px;
  padding: 5px 7px;
  background: transparent;
  box-shadow: none;
  color: #cfccdc;
}
entry { background: #242331; border-color: #5b526f; }
button:hover { background: #3d394d; color: #fff; }
button:focus-visible, entry:focus-visible { border-color: #b8b2e9; }
button.selected { background: #56506d; color: #f3efff; }
button.settings { background: #242331; }
button.settings:hover { background: #3d394d; }
switch { background: #242331; border-color: #5b526f; }
switch:checked { background: #56506d; }
switch slider { background: #c5c1d5; }
scale { padding: 4px 6px; }
scale trough { background: #242331; border: 1px solid #5b526f; }
scale trough highlight { background: #b8b2e9; border-color: #b8b2e9; box-shadow: none; }
scale slider { background: #c5c1d5; box-shadow: none; }
"""


def wifi_is_secured(ap):
    return bool(ap.get_flags() & AP_FLAGS.PRIVACY or ap.get_wpa_flags() or ap.get_rsn_flags())


def wifi_link_details(iface, frequency):
    try:
        output = subprocess.check_output(
            ["iw", "dev", iface, "link"], text=True, timeout=1, stderr=subprocess.DEVNULL
        )
    except (OSError, subprocess.SubprocessError):
        return "Unavailable", {}, ""

    fields = dict(line.strip().split(":", 1) for line in output.splitlines() if ":" in line)
    rates = {
        direction: fields.get(f"{direction} bitrate", "").strip()
        for direction in ("rx", "tx")
    }
    rate_info = " ".join(rates.values())
    if "EHT-MCS" in rate_info:
        standard = "Wi-Fi 7 · 802.11be"
    elif "HE-MCS" in rate_info:
        standard = "Wi-Fi 6E · 802.11ax" if 5925 <= frequency <= 7125 else "Wi-Fi 6 · 802.11ax"
    elif "VHT-MCS" in rate_info:
        standard = "Wi-Fi 5 · 802.11ac"
    elif re.search(r"(?:^|\s)MCS \d", rate_info):
        standard = "Wi-Fi 4 · 802.11n"
    else:
        standard = "Unavailable"

    speeds = {}
    for direction, rate in rates.items():
        match = re.match(r"(\d+(?:\.\d+)?) MBit/s\b", rate)
        if match:
            speeds[direction] = f"{match[1]} Mb/s"
    return standard, speeds, fields.get("signal", "").strip()


def label(text, css_class=None):
    widget = Gtk.Label(label=text, xalign=0, wrap=True, max_width_chars=32)
    if css_class:
        widget.add_css_class(css_class)
    return widget


def button(text, action, *classes):
    widget = Gtk.Button(label=text)
    widget.connect("clicked", lambda _: action())
    for css_class in classes:
        widget.add_css_class(css_class)
    return widget


def vertical(spacing=6):
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)


def scrollable(child, maximum):
    view = Gtk.ScrolledWindow(
        hscrollbar_policy=Gtk.PolicyType.NEVER,
        max_content_height=maximum,
        propagate_natural_height=True,
    )
    view.set_child(child)
    return view


def clear(box):
    while child := box.get_first_child():
        box.remove(child)


def pactl(*args):
    return subprocess.run(
        ["pactl", *args], check=True, capture_output=True, text=True, timeout=2
    ).stdout


def waybar_bottom(connector, cursor, monitor_top):
    fallback = cursor.get("y", monitor_top + 30) + 12
    try:
        layers = subprocess.check_output(["hyprctl", "-j", "layers"], text=True, timeout=1)
        levels = json.loads(layers).get(connector, {}).get("levels", {})
    except (OSError, ValueError, subprocess.SubprocessError):
        return fallback
    for surfaces in levels.values():
        for surface in surfaces:
            if surface["namespace"] == "waybar":
                return surface["y"] + surface["h"]
    return fallback


class Panel(Gtk.ApplicationWindow):
    def __init__(self, app, kind, output, cursor):
        super().__init__(application=app)
        self.kind = kind
        self.output = output
        self.subscriptions = []
        self.closed = False
        self.pending_volumes = {}
        self.wifi_detail_timer = 0
        self.public_ip_request = None
        self.bluetooth = None
        self.bt_agent = None
        Layer.init_for_window(self)
        Layer.set_namespace(self, "waybar-controls")
        Layer.set_layer(self, Layer.Layer.OVERLAY)
        Layer.set_keyboard_mode(self, Layer.KeyboardMode.ON_DEMAND)
        Layer.set_exclusive_zone(self, -1)
        Layer.set_respect_close(self, True)
        for edge in (Layer.Edge.TOP, Layer.Edge.RIGHT, Layer.Edge.BOTTOM, Layer.Edge.LEFT):
            Layer.set_anchor(self, edge, True)

        monitors = list(self.get_display().get_monitors())
        monitor = next((item for item in monitors if output and item.get_connector() == output), None)
        if monitor is None:
            x, y = cursor.get("x"), cursor.get("y")
            for item in monitors:
                geometry = item.get_geometry()
                if (x is not None and y is not None
                    and geometry.x <= x < geometry.x + geometry.width
                    and geometry.y <= y < geometry.y + geometry.height):
                    monitor = item
                    break
        monitor = monitor or monitors[0]
        Layer.set_monitor(self, monitor)
        geometry = monitor.get_geometry()
        anchor = cursor.get("x", geometry.x + geometry.width)
        left = round(anchor - geometry.x - PANEL_WIDTH / 2)
        left = max(PANEL_GAP, min(left, geometry.width - PANEL_WIDTH - PANEL_GAP))
        top = waybar_bottom(monitor.get_connector(), cursor, geometry.y) - geometry.y

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)
        self.connect("close-request", self.on_close)

        overlay = Gtk.Overlay()
        backdrop = Gtk.Box()
        dismiss = Gtk.GestureClick(button=0)
        dismiss.connect("released", lambda *_: self.close())
        backdrop.add_controller(dismiss)
        overlay.set_child(backdrop)
        self.set_child(overlay)

        card = vertical(8)
        card.add_css_class("card")
        card.set_size_request(PANEL_WIDTH, -1)
        panel_top = max(0, round(top)) + PANEL_GAP
        card.set_margin_start(left)
        card.set_margin_top(panel_top)
        if kind in FIXED_PANEL_HEIGHTS:
            card.set_halign(Gtk.Align.FILL)
            card.set_valign(Gtk.Align.FILL)
            card.set_margin_end(max(0, geometry.width - left - PANEL_WIDTH))
            card.set_margin_bottom(max(0, geometry.height - panel_top - FIXED_PANEL_HEIGHTS[kind]))
        else:
            card.set_halign(Gtk.Align.START)
            card.set_valign(Gtk.Align.START)
        overlay.add_overlay(card)
        header = Gtk.Box(spacing=8)
        title = label(PANEL_TITLES[kind], "title")
        title.set_hexpand(True)
        header.append(title)
        close = button("×", self.close, "flat")
        close.set_tooltip_text("Close")
        header.append(close)
        card.append(header)

        self.body = vertical(8)
        card.append(self.body)
        self.message = label("", "error")
        self.message.set_visible(False)
        card.append(self.message)
        if kind == "wifi":
            self.build_wifi()
            card.append(button("Network settings", lambda: self.open_settings("nm-connection-editor"), "settings"))
        elif kind == "bluetooth":
            self.build_bluetooth()
        else:
            self.show_audio()
            card.append(button("Audio settings", lambda: self.open_settings("pavucontrol"), "settings"))

    def on_key(self, _, keyval, *_args):
        if keyval != Gdk.KEY_Escape:
            return False
        self.close()
        return True

    def on_close(self, *_args):
        self.closed = True
        self.stop_wifi_detail_updates()
        if self.bluetooth:
            self.stop_bluetooth_scan()
            if self.bt_agent:
                self.bluetooth.request(self.bt_agent.device.path, DEVICE, "CancelPairing")
                self.bt_agent.close()
            self.bluetooth.close()
        for key, (timer, value, ratios) in tuple(self.pending_volumes.items()):
            GLib.source_remove(timer)
            self.set_audio_volume(key, value, ratios)
        for obj, handler in self.subscriptions:
            GObject.signal_handler_disconnect(obj, handler)
        self.subscriptions.clear()
        return False

    def open_settings(self, *command):
        subprocess.Popen(command)
        self.close()

    def error(self, message):
        self.message.set_text(message)
        self.message.set_visible(bool(message))

    def show_audio(self):
        try:
            info = json.loads(pactl("--format=json", "info"))
            groups = [
                ("Output", "sink", json.loads(pactl("--format=json", "list", "sinks"))),
                ("Input", "source", json.loads(pactl("--format=json", "list", "sources"))),
            ]
        except (OSError, ValueError, subprocess.SubprocessError):
            self.error("Audio service is unavailable.")
            return

        clear(self.body)
        for heading, kind, devices in groups:
            self.body.append(label(heading, "heading"))
            rows = vertical()
            default = info.get(f"default_{kind}_name")
            selected_device = None
            for device in sorted(devices, key=lambda item: item["name"] != default):
                name = device["name"]
                if kind == "source" and name.endswith(".monitor"):
                    continue
                selected = name == default
                if selected:
                    selected_device = device
                ports = device.get("ports", [])
                if not selected and ports and all(port["availability"] == "not available" for port in ports):
                    continue
                title = device.get("properties", {}).get("node.nick") or device.get("description") or name
                self.add_row(rows, title, "✓" if selected else "",
                             lambda kind=kind, name=name: self.select_audio(kind, name), selected)
            if not rows.get_first_child():
                rows.append(label("No devices available", "secondary"))
            self.body.append(scrollable(rows, 110))
            if selected_device:
                self.body.append(self.volume_slider(heading, kind, selected_device))

    def volume_slider(self, heading, kind, device):
        channels = [channel["value"] for channel in device.get("volume", {}).values()]
        maximum = max(channels, default=PULSE_VOLUME_NORMAL)
        volume = round(maximum * 100 / PULSE_VOLUME_NORMAL)
        ratios = [channel / maximum for channel in channels] if maximum else [1] * len(channels)
        key = (kind, device["name"])
        if key in self.pending_volumes:
            _, volume, ratios = self.pending_volumes[key]

        slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, max(100, volume), 1)
        slider.set_value(volume)
        slider.set_draw_value(True)
        slider.set_value_pos(Gtk.PositionType.RIGHT)
        slider.set_format_value_func(lambda _slider, value: f"{value:.0f}%")
        slider.set_tooltip_text(f"{heading} volume")
        slider.connect("value-changed", lambda widget: self.queue_volume(key, widget, ratios))
        return slider

    def queue_volume(self, key, slider, ratios):
        if key in self.pending_volumes:
            GLib.source_remove(self.pending_volumes[key][0])
        volume = round(slider.get_value())
        timer = GLib.timeout_add(75, self.set_audio_volume, key, volume, ratios)
        self.pending_volumes[key] = timer, volume, ratios

    def set_audio_volume(self, key, volume, ratios):
        self.pending_volumes.pop(key, None)
        kind, name = key
        values = [str(round(PULSE_VOLUME_NORMAL * volume / 100 * ratio)) for ratio in ratios]
        try:
            pactl(f"set-{kind}-volume", name, *(values or [f"{volume}%"]))
        except (OSError, subprocess.SubprocessError):
            if not self.closed:
                self.error("Could not change the volume.")
        return GLib.SOURCE_REMOVE

    def select_audio(self, kind, name):
        try:
            pactl(f"set-default-{kind}", name)
        except (OSError, subprocess.SubprocessError):
            self.error("Could not switch audio device.")
            return
        self.error("")
        self.show_audio()

    def add_row(self, box, title, detail, action, selected=False):
        row = Gtk.Box(spacing=8)
        name = label(title)
        if self.kind in FIXED_PANEL_HEIGHTS:
            name.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        name.set_hexpand(True)
        row.append(name)
        if detail:
            row.append(label(detail, "secondary"))
        control = button("", action, *("selected",) if selected else ())
        control.set_child(row)
        box.append(control)
        return control

    def build_bluetooth(self):
        try:
            self.bluetooth = Bluetooth(self.show_bluetooth)
        except GLib.Error:
            self.body.append(label("Bluetooth service is unavailable.", "secondary"))
            return

        self.bt_powered = None
        self.bt_power_pending = False
        self.bt_scan_adapter = None
        self.bt_scan_pending = False
        self.bt_scan_timer = 0
        self.bt_pending_device = None
        toolbar = Gtk.Box(spacing=8)
        self.bt_status = label("", "secondary")
        self.bt_status.set_hexpand(True)
        toolbar.append(self.bt_status)
        self.bt_scan = button("Scan devices", self.toggle_bluetooth_scan, "flat")
        toolbar.append(self.bt_scan)
        self.bt_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.bt_switch.set_tooltip_text("Enable Bluetooth")
        self.bt_switch.connect("state-set", self.toggle_bluetooth_power)
        toolbar.append(self.bt_switch)
        self.body.append(toolbar)

        self.bt_prompt = vertical()
        self.bt_prompt.set_visible(False)
        self.body.append(self.bt_prompt)
        self.bt_devices = vertical()
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroll.set_child(self.bt_devices)
        self.body.append(scroll)
        self.show_bluetooth()

    def show_bluetooth(self):
        if self.closed:
            return
        adapter = self.bluetooth.adapter()
        powered = property_value(adapter, "Powered", False)
        start_scan = powered and self.bt_powered is False
        self.bt_powered = powered
        self.bt_switch.set_state(powered)
        self.bt_switch.set_active(powered)
        self.bt_switch.set_sensitive(adapter is not None and not self.bt_power_pending and not self.bt_scan_pending)
        self.bt_scan.set_visible(powered)
        self.bt_scan.set_sensitive(not self.bt_scan_pending)
        self.bt_scan.set_label("Stop scan" if self.bt_scan_adapter else "Scan devices")

        clear(self.bt_devices)
        if not powered:
            self.bt_status.set_text("Bluetooth off" if adapter is not None else "No Bluetooth adapter")
            self.stop_bluetooth_scan()
            return

        scanning = self.bt_scan_adapter or property_value(adapter, "Discovering", False)
        self.bt_status.set_text("Scanning…" if scanning else "Bluetooth on")
        devices = self.bluetooth.devices(adapter)
        known = [device for device in devices if device.known]
        nearby = [device for device in devices if not device.known]
        for heading, group in (("Known devices", known), ("Nearby devices", nearby)):
            if not group:
                continue
            self.bt_devices.append(label(heading, "heading"))
            for device in group:
                if device.path == self.bt_pending_device:
                    detail = "Working…"
                elif device.connected:
                    detail = "Connected"
                    if device.battery is not None:
                        detail += f" · {device.battery}%"
                else:
                    detail = "Paired" if device.paired else ("Known" if device.known else "Pair")
                row = self.add_row(self.bt_devices, device.name, detail,
                                   lambda device=device: self.select_bluetooth_device(device),
                                   device.connected)
                row.set_sensitive(self.bt_pending_device is None)
        if not devices:
            empty = "Looking for devices…" if scanning else "No devices found"
            self.bt_devices.append(label(empty, "secondary"))
        if start_scan:
            self.start_bluetooth_scan()

    def toggle_bluetooth_power(self, _switch, enabled):
        adapter = self.bluetooth.adapter()
        if adapter is None or enabled == property_value(adapter, "Powered", False):
            return False
        self.error("")
        self.bt_power_pending = True
        self.bt_switch.set_sensitive(False)

        def finished(error):
            if self.closed:
                return
            self.bt_power_pending = False
            if error:
                self.error(f"Could not turn Bluetooth {'on' if enabled else 'off'}: {error}")
            self.show_bluetooth()

        self.bluetooth.power(adapter, enabled, finished)
        return True

    def toggle_bluetooth_scan(self):
        if self.bt_scan_adapter:
            self.stop_bluetooth_scan()
            self.show_bluetooth()
        else:
            self.start_bluetooth_scan()

    def start_bluetooth_scan(self):
        adapter = self.bluetooth.adapter()
        if self.bt_scan_pending or self.bt_scan_adapter or not property_value(adapter, "Powered", False):
            return
        self.error("")
        path = adapter.get_object_path()
        self.bt_scan_adapter = path
        self.bt_scan_pending = True
        self.show_bluetooth()

        def started(error):
            self.bt_scan_pending = False
            if self.bt_scan_adapter != path:
                if not error:
                    self.bluetooth.request(path, ADAPTER, "StopDiscovery")
                self.show_bluetooth()
                return
            if error:
                self.bt_scan_adapter = None
                if not self.closed:
                    self.error(f"Could not scan: {error}")
            else:
                self.bt_scan_timer = GLib.timeout_add_seconds(15, self.finish_bluetooth_scan)
            self.show_bluetooth()

        self.bluetooth.request(path, ADAPTER, "StartDiscovery", callback=started)

    def finish_bluetooth_scan(self):
        self.bt_scan_timer = 0
        self.stop_bluetooth_scan()
        self.show_bluetooth()
        return GLib.SOURCE_REMOVE

    def stop_bluetooth_scan(self):
        if self.bt_scan_timer:
            GLib.source_remove(self.bt_scan_timer)
            self.bt_scan_timer = 0
        path, self.bt_scan_adapter = self.bt_scan_adapter, None
        if path and not self.bt_scan_pending:
            self.bluetooth.request(path, ADAPTER, "StopDiscovery")

    def select_bluetooth_device(self, device):
        self.error("")
        self.bt_pending_device = device.path
        self.show_bluetooth()
        if device.connected or device.paired:
            method = "Disconnect" if device.connected else "Connect"
            self.bluetooth.request(device.path, DEVICE, method, callback=self.finish_bluetooth_device,
                                   timeout=25000)
            return
        try:
            self.bt_agent = PairingAgent(self.bluetooth, device, self.show_bluetooth_prompt)
        except GLib.Error as exc:
            self.finish_bluetooth_device(exc.message)
            return

        def agent_ready(error):
            if error:
                self.finish_bluetooth_device(error)
            elif not self.closed:
                self.bluetooth.request(device.path, DEVICE, "Pair", callback=paired, timeout=60000)

        def paired(error):
            if self.closed:
                return
            if error:
                self.finish_bluetooth_device(error)
            else:
                self.show_bluetooth_prompt(None, None)
                self.bluetooth.request(device.path, DEVICE, "Connect",
                                       callback=self.finish_bluetooth_device, timeout=25000)

        self.bt_agent.start(agent_ready)

    def show_bluetooth_prompt(self, message, kind):
        if self.closed:
            return
        clear(self.bt_prompt)
        self.bt_prompt.set_visible(bool(message))
        if not message:
            return
        self.bt_prompt.append(label(message))
        if not kind:
            return
        entry = Gtk.Entry() if kind in ("pin", "passkey") else None
        if entry is not None:
            entry.set_max_length(6 if kind == "passkey" else 16)
            if kind == "passkey":
                entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
            self.bt_prompt.append(entry)

        def confirm():
            answer = None
            if entry is not None:
                text = entry.get_text().strip()
                if kind == "passkey" and not re.fullmatch(r"[0-9]{6}", text):
                    self.error("Enter the six-digit passkey from the device.")
                    return
                if not text:
                    self.error("Enter the PIN for this device.")
                    return
                answer = GLib.Variant("(u)", (int(text),)) if kind == "passkey" else GLib.Variant("(s)", (text,))
            self.error("")
            self.bt_agent.answer(True, answer)
            self.show_bluetooth_prompt(None, None)

        actions = Gtk.Box(spacing=8)
        actions.append(button("Cancel", lambda: self.reject_bluetooth_prompt(), "settings"))
        actions.append(button("Confirm", confirm, "selected"))
        self.bt_prompt.append(actions)
        if entry is not None:
            entry.connect("activate", lambda _: confirm())
            entry.grab_focus()

    def reject_bluetooth_prompt(self):
        self.bt_agent.answer(False)
        self.show_bluetooth_prompt(None, None)

    def finish_bluetooth_device(self, error):
        if self.closed:
            return
        if self.bt_agent:
            self.bt_agent.close()
            self.bt_agent = None
        self.bt_pending_device = None
        self.show_bluetooth_prompt(None, None)
        if error:
            self.error(error)
        self.show_bluetooth()

    def build_wifi(self):
        try:
            self.network = NM.Client.new(None)
        except GLib.Error:
            self.body.append(label("NetworkManager is unavailable.", "secondary"))
            return
        devices = [device for device in self.network.get_devices() if isinstance(device, NM.DeviceWifi)]
        self.wifi = next((device for device in devices if device.get_active_access_point()),
                         devices[0] if devices else None)
        if self.wifi is None:
            self.body.append(label("No Wi-Fi adapter found.", "secondary"))
            self.build_vpn()
            return

        toolbar = Gtk.Box(spacing=8)
        caption = label("Wireless", "heading")
        caption.set_hexpand(True)
        toolbar.append(caption)
        self.scan = button("Rescan", self.scan_wifi, "flat")
        toolbar.append(self.scan)
        self.radio = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.radio.set_tooltip_text("Enable Wi-Fi")
        self.radio.connect("state-set", self.toggle_wifi)
        toolbar.append(self.radio)
        self.body.append(toolbar)

        self.connected_row = vertical(0)
        self.body.append(self.connected_row)
        self.detail_connection = None
        wifi_content = vertical()
        self.build_wifi_details(wifi_content)
        self.network_rows = vertical()
        wifi_content.append(self.network_rows)
        self.wifi_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=False,
            vexpand=True,
        )
        self.wifi_scroll.set_child(wifi_content)
        self.body.append(self.wifi_scroll)
        self.password_box = vertical()
        self.body.append(self.password_box)
        for obj, signal in [
            (self.network, "notify::wireless-enabled"),
            (self.wifi, "notify::last-scan"),
            (self.wifi, "notify::active-access-point"),
            (self.wifi, "access-point-added"),
            (self.wifi, "access-point-removed"),
        ]:
            self.subscriptions.append((obj, obj.connect(signal, self.show_wifi)))
        self.show_wifi()
        self.build_vpn()
        if self.network.wireless_get_enabled():
            self.scan_wifi()

    def toggle_wifi(self, _switch, enabled):
        if enabled == self.network.wireless_get_enabled():
            return False
        self.network.dbus_set_property(
            "/org/freedesktop/NetworkManager", "org.freedesktop.NetworkManager",
            "WirelessEnabled", GLib.Variant("b", enabled), 5000, None,
            self.radio_changed, None,
        )
        return True

    def radio_changed(self, client, result, _data):
        try:
            client.dbus_set_property_finish(result)
            self.error("")
        except GLib.Error as exc:
            self.error(exc.message)
        self.show_wifi()

    def scan_wifi(self):
        self.wifi.request_scan_async(None, self.scanned, None)

    def scanned(self, device, result, _data):
        try:
            device.request_scan_finish(result)
        except GLib.Error:
            # NetworkManager can reject redundant scans while one is underway.
            pass
        self.show_wifi()

    def show_wifi(self, *_args):
        enabled = self.network.wireless_get_enabled()
        self.radio.set_state(enabled)
        self.radio.set_active(enabled)
        self.scan.set_sensitive(enabled)
        clear(self.connected_row)
        clear(self.network_rows)
        connection = self.wifi.get_active_connection() if enabled else None
        connection_path = connection.get_path() if connection else None
        if connection_path != self.detail_connection:
            self.wifi_details.set_reveal_child(False)
            self.stop_wifi_detail_updates()
            self.detail_connection = connection_path
        if not enabled:
            clear(self.password_box)
            self.network_rows.append(label("Wi-Fi is off", "secondary"))
            return

        active = self.wifi.get_active_access_point()
        networks = {}
        points = sorted(
            self.wifi.get_access_points(),
            key=lambda ap: (ap == active, ap.get_strength()),
            reverse=True,
        )
        for ap in points:
            ssid = ap.get_ssid()
            if not ssid:
                continue
            raw = ssid.get_data()
            if not raw:
                continue
            secure = wifi_is_secured(ap)
            networks.setdefault((raw, secure), ap)

        for (ssid, secure), ap in networks.items():
            name = NM.utils_ssid_to_utf8(ssid)
            selected = ap == active
            status = "Connected" if selected else ("Secured" if secure else "Open")
            detail = f"{status} · {ap.get_strength()}%"
            if selected:
                row = self.add_row(self.connected_row, name, f"{detail}  ▾",
                                   self.toggle_wifi_details, True)
                row.set_tooltip_text("Show or hide connection details")
            else:
                self.add_row(self.network_rows, name, detail, lambda ap=ap: self.choose_wifi(ap))
        if not networks:
            self.network_rows.append(label("No nearby networks found", "secondary"))

    def build_wifi_details(self, content):
        grid = Gtk.Grid(column_spacing=14, row_spacing=5)
        grid.add_css_class("wifi-details")
        self.wifi_detail_values = {}
        for row, field in enumerate((
            "Wi-Fi standard", "Frequency", "Signal", "Receive rate", "Transmit rate",
            "Local IPv4", "Local IPv6", "Gateway", "Public IP",
        )):
            title = label(field, "secondary")
            title.set_valign(Gtk.Align.START)
            value = label("")
            value.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            value.set_hexpand(True)
            value.set_selectable(True)
            grid.attach(title, 0, row, 1, 1)
            grid.attach(value, 1, row, 1, 1)
            self.wifi_detail_values[field] = value
        self.wifi_detail_values["Public IP"].set_tooltip_text(
            "Internet-facing address for the current route, including any active VPN exit node"
        )
        self.wifi_details = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, transition_duration=160
        )
        self.wifi_details.set_child(grid)
        content.append(self.wifi_details)

    def toggle_wifi_details(self):
        if self.wifi_details.get_reveal_child():
            self.wifi_details.set_reveal_child(False)
            self.stop_wifi_detail_updates()
            return
        if not self.refresh_wifi_details():
            return
        scroll = self.wifi_scroll.get_vadjustment()
        scroll.set_value(scroll.get_lower())
        self.wifi_details.set_reveal_child(True)
        self.wifi_detail_timer = GLib.timeout_add_seconds(3, self.refresh_wifi_details)
        self.lookup_public_ip()

    def refresh_wifi_details(self):
        ap = self.wifi.get_active_access_point()
        if not ap:
            self.wifi_detail_timer = 0
            self.stop_wifi_detail_updates()
            self.wifi_details.set_reveal_child(False)
            return GLib.SOURCE_REMOVE
        frequency = ap.get_frequency()
        standard, speeds, signal = wifi_link_details(self.wifi.get_iface(), frequency)
        ip4, ip6 = self.wifi.get_ip4_config(), self.wifi.get_ip6_config()
        addresses = [
            "\n".join(address.get_address() for address in config.get_addresses()) if config else ""
            for config in (ip4, ip6)
        ]
        gateway = next((config.get_gateway() for config in (ip4, ip6)
                        if config and config.get_gateway()), None)
        values = {
            "Wi-Fi standard": standard,
            "Frequency": f"{frequency} MHz" if frequency else "",
            "Signal": f"{ap.get_strength()}% · {signal}" if signal else f"{ap.get_strength()}%",
            "Receive rate": speeds.get("rx"),
            "Transmit rate": speeds.get("tx"),
            "Local IPv4": addresses[0],
            "Local IPv6": addresses[1],
            "Gateway": gateway,
        }
        for field, value in values.items():
            self.wifi_detail_values[field].set_text(value or "Unavailable")
        return GLib.SOURCE_CONTINUE

    def lookup_public_ip(self):
        value = self.wifi_detail_values["Public IP"]
        value.set_text("Looking up…")
        try:
            self.public_ip_request = Gio.Subprocess.new(
                ["curl", "--fail", "--silent", "--max-time", "4", "https://api64.ipify.org"],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE,
            )
        except GLib.Error:
            value.set_text("Unavailable")
            return
        self.public_ip_request.communicate_utf8_async(None, None, self.public_ip_result)

    def public_ip_result(self, process, result):
        if process != self.public_ip_request:
            return
        self.public_ip_request = None
        try:
            _, response, _ = process.communicate_utf8_finish(result)
            address = str(ip_address(response.strip())) if process.get_successful() else None
        except (GLib.Error, ValueError):
            address = None
        self.wifi_detail_values["Public IP"].set_text(address or "Unavailable")

    def stop_wifi_detail_updates(self):
        if self.wifi_detail_timer:
            GLib.source_remove(self.wifi_detail_timer)
            self.wifi_detail_timer = 0
        if self.public_ip_request:
            self.public_ip_request.force_exit()
            self.public_ip_request = None

    def build_vpn(self):
        self.body.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        toolbar = Gtk.Box(spacing=8)
        caption = label("VPN", "heading")
        caption.set_hexpand(True)
        toolbar.append(caption)
        toolbar.append(button("Add VPN…", lambda: self.open_settings(
            "nm-connection-editor", "--create", "--type=vpn"), "flat"))
        self.body.append(toolbar)
        self.tailscale_row = vertical(0)
        self.body.append(self.tailscale_row)
        self.vpn_rows = vertical()
        self.body.append(scrollable(self.vpn_rows, 112))

        for signal in ("notify::active-connections", "connection-added", "connection-removed"):
            handler = self.network.connect(signal, self.show_vpn_profiles)
            self.subscriptions.append((self.network, handler))
        self.show_tailscale()
        self.show_vpn_profiles()

    def show_tailscale(self):
        clear(self.tailscale_row)
        try:
            status = subprocess.run(
                ["tailscale", "status", "--json", "--peers=false"],
                capture_output=True, text=True, timeout=2,
            )
            state = json.loads(status.stdout).get("BackendState")
        except (OSError, ValueError, subprocess.SubprocessError):
            return
        row = Gtk.Box(spacing=8)
        title = label("Tailscale")
        title.set_hexpand(True)
        row.append(title)
        online = state in ("Running", "Starting")
        captions = {"Running": "Connected", "Starting": "Connecting",
                    "Stopped": "Disconnected", "NeedsLogin": "Sign-in required",
                    "NeedsMachineAuth": "Approval required"}
        row.append(label(captions.get(state, "Unavailable"), "secondary"))
        control = Gtk.Switch(active=online, valign=Gtk.Align.CENTER)
        control.set_tooltip_text("Connect or disconnect Tailscale")
        control.set_sensitive(state in ("Running", "Starting", "Stopped"))
        control.connect("state-set", self.toggle_tailscale)
        row.append(control)
        self.tailscale_row.append(row)

    def toggle_tailscale(self, _switch, enabled):
        action = "up" if enabled else "down"
        try:
            prefs = subprocess.check_output(["tailscale", "debug", "prefs"], text=True,
                                            stderr=subprocess.DEVNULL, timeout=2)
            operator = json.loads(prefs).get("OperatorUser") == getpass.getuser()
        except (OSError, ValueError, subprocess.SubprocessError):
            operator = False
        if operator:
            command = ["tailscale", action]
        else:
            # Close the overlay so the desktop authentication prompt can receive input.
            command = ["sh", "-c",
                       'systemctl --user start plasma-polkit-agent.service && '
                       'exec pkexec --disable-internal-agent /usr/bin/tailscale "$1"',
                       "tailscale-vpn", action]
        self.close()
        subprocess.Popen(command)
        return True

    def show_vpn_profiles(self, *_args):
        clear(self.vpn_rows)
        states = NM.ActiveConnectionState
        active = {
            connection.get_uuid(): connection
            for connection in self.network.get_active_connections()
            if connection.get_state() in (states.ACTIVATING, states.ACTIVATED, states.DEACTIVATING)
        }
        profiles = [profile for profile in self.network.get_connections()
                    if profile.get_connection_type() in VPN_TYPES]
        for profile in sorted(profiles, key=lambda item: item.get_id().casefold()):
            connection = active.get(profile.get_uuid())
            self.add_row(self.vpn_rows, profile.get_id(), "Disconnect" if connection else "Connect",
                         lambda profile=profile, connection=connection:
                             self.toggle_vpn_profile(profile, connection), bool(connection))

    def toggle_vpn_profile(self, profile, connection):
        self.error("")
        if connection:
            self.network.deactivate_connection_async(connection, None, self.vpn_changed, False)
        else:
            self.network.activate_connection_async(profile, None, None, None, self.vpn_changed, True)

    def vpn_changed(self, client, result, connecting):
        if self.closed:
            return
        try:
            if connecting:
                connection = client.activate_connection_finish(result)
                handler = connection.connect("state-changed", self.vpn_state_changed)
                self.subscriptions.append((connection, handler))
            else:
                client.deactivate_connection_finish(result)
        except GLib.Error as exc:
            self.error(exc.message)
        self.show_vpn_profiles()

    def vpn_state_changed(self, connection, state, reason, *_args):
        if (state == NM.ActiveConnectionState.DEACTIVATED
            and reason != NM.ActiveConnectionStateReason.USER_DISCONNECTED):
            self.error(f"{connection.get_id()} disconnected.")
        self.show_vpn_profiles()

    def choose_wifi(self, ap):
        self.error("")
        clear(self.password_box)
        if ap == self.wifi.get_active_access_point():
            return
        saved = ap.filter_connections(self.wifi.get_available_connections())
        if saved:
            self.network.activate_connection_async(
                saved[0], self.wifi, ap.get_path(), None, self.activated, False
            )
            return
        flags = ap.get_rsn_flags() | ap.get_wpa_flags()
        if flags & SECURITY.KEY_MGMT_PSK:
            key_mgmt = "wpa-psk"
        elif flags & SECURITY.KEY_MGMT_SAE:
            key_mgmt = "sae"
        elif flags & (SECURITY.KEY_MGMT_OWE | SECURITY.KEY_MGMT_OWE_TM):
            self.connect_wifi(ap, "owe")
            return
        elif wifi_is_secured(ap):
            self.error("Use Network settings to configure this network’s security.")
            return
        else:
            self.connect_wifi(ap)
            return

        password = Gtk.PasswordEntry(
            placeholder_text=f"Password for {NM.utils_ssid_to_utf8(ap.get_ssid().get_data())}",
            show_peek_icon=True,
        )

        def connect():
            secret = password.get_text()
            if not secret:
                self.error("Enter the Wi-Fi password.")
                return
            password.set_text("")
            self.connect_wifi(ap, key_mgmt, secret)

        password.connect("activate", lambda _: connect())
        self.password_box.append(password)
        self.password_box.append(button("Connect", connect))
        password.grab_focus()

    def connect_wifi(self, ap, key_mgmt=None, secret=None):
        connection = NM.SimpleConnection.new()
        if key_mgmt:
            security = NM.SettingWirelessSecurity.new()
            security.set_property("key-mgmt", key_mgmt)
            if secret:
                security.set_property("psk", secret)
            connection.add_setting(security)
        self.network.add_and_activate_connection_async(
            connection, self.wifi, ap.get_path(), None, self.activated, True
        )

    def activated(self, client, result, added):
        if self.closed:
            return
        try:
            finish = (client.add_and_activate_connection_finish if added
                      else client.activate_connection_finish)
            connection = finish(result)
        except GLib.Error as exc:
            self.error(exc.message)
            return
        if connection.get_state() == NM.ActiveConnectionState.ACTIVATING:
            handler = connection.connect("state-changed", self.connection_state)
            self.subscriptions.append((connection, handler))
        else:
            self.connection_state(connection, connection.get_state())

    def connection_state(self, _connection, state, *_args):
        if state == NM.ActiveConnectionState.ACTIVATED:
            clear(self.password_box)
            self.error("")
            self.show_wifi()
        elif state == NM.ActiveConnectionState.DEACTIVATED:
            self.error("Could not connect. Check the network settings and try again.")


class Controls(Gtk.Application):
    def __init__(self):
        super().__init__(
            application_id="local.waybar.ControlsPython",
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )

    def do_startup(self):
        Gtk.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def do_command_line(self, command):
        args = command.get_arguments()
        if len(args) not in (2, 3, 4) or args[1] not in PANEL_TITLES:
            return 2
        kind = args[1]
        output = args[2] if len(args) >= 3 else ""
        try:
            cursor = json.loads(args[3]) if len(args) == 4 else {}
        except ValueError:
            cursor = {}
        previous = self.get_active_window()
        if previous:
            if (previous.kind, previous.output) == (kind, output):
                previous.close()
                return 0
            panel = Panel(self, kind, output, cursor)
            previous.close()
        else:
            panel = Panel(self, kind, output, cursor)
        panel.present()
        return 0


if __name__ == "__main__":
    raise SystemExit(Controls().run(sys.argv))
