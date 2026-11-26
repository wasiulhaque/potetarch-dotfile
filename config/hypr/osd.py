import ctypes
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import gi

# Layer-shell must load before GTK binds the Wayland symbols.
ctypes.CDLL("libgtk4-layer-shell.so.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("GUdev", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Gtk4LayerShell as Layer, GUdev

BOTTOM_MARGIN = 52
DISPLAY_TIME = 1400
VOLUME_ICONS = ("󰕿", "󰖀", "󰕾")
CSS = """
window { background: transparent; }
.osd {
  background: #1c2030;
  border: 1px solid rgba(165, 151, 221, 0.29);
  border-radius: 12px;
  padding: 12px 15px;
  color: #cfccdc;
  font-family: "JetBrainsMono Nerd Font", "JetBrains Mono", monospace;
  font-size: 12px;
}
.osd .icon { color: #b8b2e9; font-size: 18px; }
.osd progressbar trough {
  min-height: 5px;
  background: #3d394d;
  border: none;
  border-radius: 3px;
  box-shadow: none;
}
.osd progressbar progress {
  min-height: 5px;
  background: #b8b2e9;
  border: none;
  border-radius: 3px;
  box-shadow: none;
}
"""


def make_click_through(surface):
    # Gdk's Region argument normally requires Pycairo. Create an empty region
    # with the same system Cairo library GTK uses.
    pointer = ctypes.c_void_p
    capsule_pointer = ctypes.pythonapi.PyCapsule_GetPointer
    capsule_pointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
    capsule_pointer.restype = pointer

    cairo = ctypes.CDLL("libcairo.so.2")
    cairo.cairo_region_create.argtypes = []
    cairo.cairo_region_create.restype = pointer
    cairo.cairo_region_destroy.argtypes = [pointer]
    gtk = ctypes.CDLL("libgtk-4.so.1")
    gtk.gdk_surface_set_input_region.argtypes = [pointer, pointer]
    gtk.gdk_surface_set_input_region.restype = None

    region = cairo.cairo_region_create()
    try:
        gtk.gdk_surface_set_input_region(capsule_pointer(surface.__gpointer__, None), region)
    finally:
        cairo.cairo_region_destroy(region)


def read_volume():
    try:
        output = subprocess.check_output(
            ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
            text=True, stderr=subprocess.DEVNULL, timeout=1,
            env=os.environ | {"LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.fullmatch(r"Volume: ([0-9.]+)( \[MUTED\])?\s*", output)
    if not match:
        return None
    return round(float(match[1]) * 100), bool(match[2])


def read_brightness(device):
    try:
        maximum = int((device / "max_brightness").read_text())
        value = int((device / "brightness").read_text())
    except (OSError, ValueError):
        return None
    return round(value * 100 / maximum) if maximum else None


class ControlsOSD(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="local.hyprland.ControlsOSD")
        self.window = None
        self.hide_source = 0
        self.volume_update = 0
        self.volume = None
        self.brightness = {}

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self.volume = read_volume()
        self.start_audio_watch()
        self.backlights = GUdev.Client.new(["backlight"])
        for device in self.backlights.query_by_subsystem("backlight"):
            path = Path(device.get_sysfs_path())
            self.brightness[path] = read_brightness(path)
        self.backlights.connect("uevent", self.on_backlight_event)

    def do_activate(self):
        pass

    def start_audio_watch(self):
        try:
            process = Gio.Subprocess.new(
                ["env", "LC_ALL=C", "stdbuf", "-oL", "pactl", "subscribe"],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE,
            )
        except GLib.Error:
            GLib.timeout_add_seconds(2, self.start_audio_watch)
            return GLib.SOURCE_REMOVE
        self.audio_process = process
        stream = Gio.DataInputStream.new(process.get_stdout_pipe())
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self.on_audio_event)
        return GLib.SOURCE_REMOVE

    def on_audio_event(self, stream, result):
        try:
            line, _ = stream.read_line_finish_utf8(result)
        except GLib.Error:
            line = None
        if line is None:
            GLib.timeout_add_seconds(2, self.start_audio_watch)
            return
        if re.match(r"Event '(?:change|new|remove)' on (?:sink|server)(?: |$)", line):
            if not self.volume_update:
                self.volume_update = GLib.timeout_add(60, self.update_volume)
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self.on_audio_event)

    def update_volume(self):
        self.volume_update = 0
        value = read_volume()
        previous, self.volume = self.volume, value
        if previous is not None and value is not None and value != previous:
            percent, muted = value
            icon = "󰖁" if muted else VOLUME_ICONS[min(percent // 34, 2)]
            self.show_osd(icon, percent, muted)
        return GLib.SOURCE_REMOVE

    def on_backlight_event(self, _client, action, device):
        path = Path(device.get_sysfs_path())
        if action == "remove":
            self.brightness.pop(path, None)
            return
        previous = self.brightness.get(path)
        current = read_brightness(path)
        self.brightness[path] = current
        if previous is not None and current is not None and previous != current:
            self.show_osd("󰃠", current)

    def focused_monitor(self):
        monitors = list(Gdk.Display.get_default().get_monitors())
        if len(monitors) < 2:
            return monitors[0] if monitors else None
        try:
            entries = json.loads(subprocess.check_output(
                ["hyprctl", "-j", "monitors"], text=True, timeout=1, stderr=subprocess.DEVNULL
            ))
            focused = next((screen["name"] for screen in entries if screen.get("focused")), None)
        except (OSError, ValueError, subprocess.SubprocessError):
            focused = None
        return next((monitor for monitor in monitors if monitor.get_connector() == focused), monitors[0])

    def create_window(self):
        window = Gtk.ApplicationWindow(application=self, decorated=False)
        Layer.init_for_window(window)
        Layer.set_namespace(window, "volume-brightness-osd")
        Layer.set_layer(window, Layer.Layer.OVERLAY)
        Layer.set_anchor(window, Layer.Edge.BOTTOM, True)
        Layer.set_margin(window, Layer.Edge.BOTTOM, BOTTOM_MARGIN)
        Layer.set_keyboard_mode(window, Layer.KeyboardMode.NONE)

        card = Gtk.Box(spacing=12)
        card.add_css_class("osd")
        self.icon = Gtk.Label()
        self.icon.add_css_class("icon")
        self.icon.set_size_request(22, -1)
        self.meter = Gtk.ProgressBar(valign=Gtk.Align.CENTER)
        self.meter.set_size_request(136, -1)
        self.value = Gtk.Label(xalign=1, width_chars=5)
        card.append(self.icon)
        card.append(self.meter)
        card.append(self.value)
        window.set_child(card)
        return window

    def show_osd(self, icon, percent, muted=False):
        if self.window is None:
            self.window = self.create_window()
        monitor = self.focused_monitor()
        if monitor != Layer.get_monitor(self.window):
            Layer.set_monitor(self.window, monitor)
        self.icon.set_text(icon)
        self.meter.set_fraction(0 if muted else max(0, min(percent, 100)) / 100)
        self.value.set_text("Muted" if muted else f"{percent}%")
        if not self.window.get_visible():
            self.window.present()
            make_click_through(self.window.get_surface())
        if self.hide_source:
            GLib.source_remove(self.hide_source)
        self.hide_source = GLib.timeout_add(DISPLAY_TIME, self.hide_osd)

    def hide_osd(self):
        self.hide_source = 0
        self.window.set_visible(False)
        return GLib.SOURCE_REMOVE


if __name__ == "__main__":
    raise SystemExit(ControlsOSD().run(sys.argv))
