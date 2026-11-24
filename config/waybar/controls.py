import ctypes
import getpass
import json
import subprocess
import sys

import gi

# Load before GTK binds any Wayland symbols.
ctypes.CDLL("libgtk4-layer-shell.so.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("NM", "1.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Gtk4LayerShell as Layer, NM

AP_FLAGS = getattr(NM, "80211ApFlags")
SECURITY = getattr(NM, "80211ApSecurityFlags")
PANEL_WIDTH = 345
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
        card.set_halign(Gtk.Align.START)
        card.set_valign(Gtk.Align.START)
        card.set_margin_start(left)
        card.set_margin_top(max(0, round(top)) + PANEL_GAP)
        overlay.add_overlay(card)
        header = Gtk.Box(spacing=8)
        title = label("Wi-Fi" if kind == "wifi" else "Sound", "title")
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
        name.set_hexpand(True)
        row.append(name)
        if detail:
            row.append(label(detail, "secondary"))
        control = button("", action, *("selected",) if selected else ())
        control.set_child(row)
        box.append(control)

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

        self.network_rows = vertical()
        self.body.append(scrollable(self.network_rows, 192))
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
        clear(self.network_rows)
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
            secure = bool(ap.get_flags() & AP_FLAGS.PRIVACY)
            networks.setdefault((raw, secure), ap)

        for (ssid, secure), ap in networks.items():
            name = NM.utils_ssid_to_utf8(ssid)
            selected = ap == active
            status = "Connected" if selected else ("Secured" if secure else "Open")
            detail = f"{status} · {ap.get_strength()}%"
            self.add_row(self.network_rows, name, detail,
                         lambda ap=ap: self.choose_wifi(ap), selected)
        if not networks:
            self.network_rows.append(label("No nearby networks found", "secondary"))

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
        elif ap.get_flags() & AP_FLAGS.PRIVACY:
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
            application_id="local.waybar.Controls",
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
        if len(args) not in (2, 3, 4) or args[1] not in ("wifi", "audio"):
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
