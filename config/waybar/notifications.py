import json
import os
from pathlib import Path
import re
import sys

from gi.repository import Gio, GLib

CENTER = "org.erikreider.swaync.cc"
CENTER_PATH = "/org/erikreider/swaync/cc"
FREEDESKTOP = "org.freedesktop.Notifications"
EVENT = "local.waybar.Notifications"
EVENT_PATH = "/local/waybar/Notifications"


def emit_received():
    if os.environ.get("SWAYNC_HINT_TRANSIENT") in {"true", "1"}:
        return
    notification_id = int(os.environ.get("SWAYNC_ID", "0"))
    replaced_id = int(os.environ.get("SWAYNC_REPLACES_ID", "0"))
    if notification_id:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.emit_signal(None, EVENT_PATH, EVENT, "Received",
                        GLib.Variant("(uu)", (notification_id, replaced_id)))
        bus.flush_sync(None)


class NotificationBadge:
    def __init__(self):
        output = re.sub(r"[^\w.-]", "_", os.environ.get("WAYBAR_OUTPUT_NAME", "default"))
        self.state_path = Path(GLib.get_user_runtime_dir()) / f"waybar-notifications-{output}.json"
        self.total = 0
        self.existing = 0
        self.unread = set()
        self.owner = None
        self.visible = False
        self.last_output = None

        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.center = Gio.DBusProxy.new_sync(
            self.bus, Gio.DBusProxyFlags.DO_NOT_AUTO_START | Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES,
            None, CENTER, CENTER_PATH, CENTER, None,
        )
        self.center.connect("g-signal", self.on_center)
        self.center.connect("notify::g-name-owner", self.sync)
        self.bus.signal_subscribe(
            FREEDESKTOP, FREEDESKTOP, "NotificationClosed", "/org/freedesktop/Notifications",
            None, Gio.DBusSignalFlags.NONE, self.on_closed,
        )
        self.bus.signal_subscribe(
            None, EVENT, "Received", EVENT_PATH, None, Gio.DBusSignalFlags.NONE, self.on_received,
        )
        self.sync()

    def sync(self, *_):
        self.owner = self.center.get_name_owner()
        self.unread.clear()
        self.existing = 0
        if self.owner is None:
            self.total = 0
            self.visible = False
            self.publish()
            return
        try:
            (data,) = self.center.call_sync(
                "GetSubscribeData", None, Gio.DBusCallFlags.NO_AUTO_START, 2000, None
            ).unpack()
        except GLib.Error:
            return
        _, visible, count, _ = data
        self.existing = count
        try:
            saved = json.loads(self.state_path.read_text())
            if saved.get("owner") == self.owner:
                self.unread = set(saved["unread"])
                self.existing = saved["existing"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self.update(count, visible)

    def update(self, count, visible):
        self.total = count
        self.visible = visible
        if visible or count == 0:
            self.unread.clear()
            self.existing = 0
        else:
            self.existing = min(self.existing, max(0, count - len(self.unread)))
        self.publish()

    def on_center(self, _proxy, _sender, signal, params):
        if signal == "SubscribeV2":
            count, _dnd, visible, _inhibited = params.unpack()
            self.update(count, visible)

    def on_closed(self, _bus, _sender, _path, _interface, _signal, params):
        notification_id, _reason = params.unpack()
        self.unread.discard(notification_id)
        self.publish()

    def on_received(self, _bus, _sender, _path, _interface, _signal, params):
        notification_id, replaced_id = params.unpack()
        if self.owner and not self.visible:
            self.unread.discard(replaced_id)
            self.unread.add(notification_id)
            self.existing = min(self.existing, max(0, self.total - len(self.unread)))
            self.publish()

    def publish(self):
        count = min(self.total, self.existing + len(self.unread))
        noun = "notification" if count == 1 else "notifications"
        payload = {"text": str(count), "tooltip": f"{count} unread {noun}"}
        if payload != self.last_output:
            print(json.dumps(payload), flush=True)
            self.last_output = payload
        if self.owner:
            try:
                GLib.file_set_contents(str(self.state_path), json.dumps({
                    "owner": self.owner, "existing": self.existing, "unread": sorted(self.unread),
                }).encode())
            except GLib.Error:
                pass


if __name__ == "__main__":
    if sys.argv[1:] == ["--received"]:
        try:
            emit_received()
        except (GLib.Error, ValueError):
            pass
    else:
        badge = NotificationBadge()
        GLib.MainLoop().run()
