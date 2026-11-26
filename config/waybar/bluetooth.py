import json
from dataclasses import dataclass
from html import escape

from gi.repository import Gio, GLib

BLUEZ = "org.bluez"
ADAPTER = f"{BLUEZ}.Adapter1"
DEVICE = f"{BLUEZ}.Device1"
BATTERY = f"{BLUEZ}.Battery1"
AGENT = f"{BLUEZ}.Agent1"
AGENT_MANAGER = f"{BLUEZ}.AgentManager1"
AGENT_PATH = "/local/waybar/BluetoothAgent"
AGENT_XML = """<node><interface name="org.bluez.Agent1">
  <method name="Release"/>
  <method name="RequestPinCode"><arg type="o" direction="in"/><arg type="s" direction="out"/></method>
  <method name="DisplayPinCode"><arg type="o"/><arg type="s"/></method>
  <method name="RequestPasskey"><arg type="o" direction="in"/><arg type="u" direction="out"/></method>
  <method name="DisplayPasskey"><arg type="o"/><arg type="u"/><arg type="q"/></method>
  <method name="RequestConfirmation"><arg type="o"/><arg type="u"/></method>
  <method name="RequestAuthorization"><arg type="o"/></method>
  <method name="AuthorizeService"><arg type="o"/><arg type="s"/></method>
  <method name="Cancel"/>
</interface></node>"""


def property_value(proxy, key, default=None):
    value = proxy.get_cached_property(key) if proxy is not None else None
    return value.unpack() if value is not None else default


@dataclass(frozen=True)
class Device:
    path: str
    name: str
    paired: bool
    known: bool
    connected: bool
    battery: int | None


class Bluetooth:
    def __init__(self, changed):
        self.manager = Gio.DBusObjectManagerClient.new_for_bus_sync(
            Gio.BusType.SYSTEM, Gio.DBusObjectManagerClientFlags.DO_NOT_AUTO_START,
            BLUEZ, "/", None, None, None,
        )
        self.bus = self.manager.get_connection()
        self.changed = changed
        self.refresh_source = 0
        self.handlers = [
            self.manager.connect(signal, self.queue_refresh)
            for signal in ("object-added", "object-removed", "interface-added", "interface-removed")
        ]
        self.handlers.append(self.manager.connect("interface-proxy-properties-changed", self.on_properties))

    def on_properties(self, _manager, _object, proxy, changed, _invalidated):
        fields = changed.unpack()
        relevant = {
            ADAPTER: {"Powered", "Discovering"},
            DEVICE: {"Paired", "Bonded", "Trusted", "Connected", "Alias", "Name"},
            BATTERY: {"Percentage"},
        }
        if relevant.get(proxy.get_interface_name(), set()).intersection(fields):
            self.queue_refresh()

    def queue_refresh(self, *_args):
        if not self.refresh_source:
            self.refresh_source = GLib.idle_add(self.refresh)

    def refresh(self):
        self.refresh_source = 0
        self.changed()
        return GLib.SOURCE_REMOVE

    def close(self):
        if self.refresh_source:
            GLib.source_remove(self.refresh_source)
            self.refresh_source = 0
        for handler in self.handlers:
            self.manager.disconnect(handler)
        self.handlers.clear()

    def adapter(self):
        for obj in sorted(self.manager.get_objects(), key=lambda item: item.get_object_path()):
            if proxy := obj.get_interface(ADAPTER):
                return proxy
        return None

    def devices(self, adapter):
        result = []
        for obj in self.manager.get_objects():
            proxy = obj.get_interface(DEVICE)
            if property_value(proxy, "Adapter") != adapter.get_object_path():
                continue
            name = property_value(proxy, "Alias") or property_value(proxy, "Address", "Bluetooth device")
            name = " ".join(name.split())
            paired = property_value(proxy, "Paired", False) or property_value(proxy, "Bonded", False)
            connected = property_value(proxy, "Connected", False)
            battery = property_value(obj.get_interface(BATTERY), "Percentage") if connected else None
            result.append(Device(
                obj.get_object_path(), name, paired,
                paired or connected or property_value(proxy, "Trusted", False),
                connected, battery if battery is not None and 0 <= battery <= 100 else None,
            ))
        return sorted(result, key=lambda device: (not device.known, not device.connected, device.name.casefold()))

    def request(self, path, interface, method, parameters=None, callback=None, timeout=9000):
        def finished(bus, result):
            error = None
            try:
                bus.call_finish(result)
            except GLib.Error as exc:
                Gio.DBusError.strip_remote_error(exc)
                error = exc.message
            if callback:
                callback(error)

        self.bus.call(
            BLUEZ, path, interface, method, parameters, None,
            Gio.DBusCallFlags.NO_AUTO_START, timeout, None, finished,
        )

    def power(self, adapter, enabled, callback):
        self.request(adapter.get_object_path(), "org.freedesktop.DBus.Properties", "Set",
                     GLib.Variant("(ssv)", (ADAPTER, "Powered", GLib.Variant("b", enabled))), callback)


class PairingAgent:
    def __init__(self, bluetooth, device, prompt):
        self.bluetooth = bluetooth
        self.device = device
        self.prompt = prompt
        self.invocation = None
        self.closed = False
        self.registered = False
        self.info = Gio.DBusNodeInfo.new_for_xml(AGENT_XML)
        self.export = bluetooth.bus.register_object(AGENT_PATH, self.info.interfaces[0], self.on_method, None, None)

    def start(self, callback):
        def registered(error):
            if not error:
                self.registered = True
            if self.closed:
                self.unregister()
            else:
                callback(error)

        self.bluetooth.request("/org/bluez", AGENT_MANAGER, "RegisterAgent",
                               GLib.Variant("(os)", (AGENT_PATH, "KeyboardDisplay")), registered)

    def on_method(self, _bus, sender, _path, _interface, method, parameters, invocation):
        if sender != self.bluetooth.manager.get_name_owner():
            invocation.return_dbus_error(f"{BLUEZ}.Error.Rejected", "Bluetooth request rejected")
            return
        args = parameters.unpack()
        if method in ("Release", "Cancel"):
            self.answer(False)
            self.prompt(None, None)
            invocation.return_value(None)
        elif self.closed or args[0] != self.device.path:
            invocation.return_dbus_error(f"{BLUEZ}.Error.Rejected", "Bluetooth request rejected")
        elif method in ("DisplayPinCode", "DisplayPasskey"):
            code = f"{args[1]:06d}" if method == "DisplayPasskey" else args[1]
            self.prompt(f"Enter {code} on the device, then press Enter there.", None)
            invocation.return_value(None)
        else:
            self.answer(False)
            self.invocation = invocation
            if method == "RequestConfirmation":
                self.prompt(f"Confirm this code on the device: {args[1]:06d}", "confirm")
            elif method == "RequestPasskey":
                self.prompt("Enter the six-digit passkey shown on the device.", "passkey")
            elif method == "RequestPinCode":
                self.prompt("Enter the PIN for this device.", "pin")
            else:
                self.prompt("Allow this device to pair?" if method == "RequestAuthorization"
                            else "Allow this Bluetooth service?", "confirm")

    def answer(self, accepted, value=None):
        if self.invocation is None:
            return
        invocation, self.invocation = self.invocation, None
        if accepted:
            invocation.return_value(value)
        else:
            invocation.return_dbus_error(f"{BLUEZ}.Error.Rejected", "Pairing request declined")

    def unregister(self):
        if self.registered:
            self.registered = False
            self.bluetooth.request("/org/bluez", AGENT_MANAGER, "UnregisterAgent", GLib.Variant("(o)", (AGENT_PATH,)))

    def close(self):
        self.closed = True
        self.answer(False)
        self.unregister()
        self.bluetooth.bus.unregister_object(self.export)


def bar_status(bluetooth):
    adapter = bluetooth.adapter()
    text = "<span size='125%'>󰂯</span>"
    if adapter is None or not property_value(adapter, "Powered", False):
        tooltip = "Bluetooth off" if adapter is not None else "Bluetooth adapter unavailable"
        return {"text": text, "tooltip": tooltip, "class": "off"}

    connected = [device for device in bluetooth.devices(adapter) if device.connected]
    battery_levels = [device.battery for device in connected if device.battery is not None]
    if battery_levels:
        percent = min(battery_levels)
        blocks = round(percent / 20)
        full, empty = "━" * blocks, "━" * (5 - blocks)
        text += f"  <span foreground='#b8b2e9'>{full}</span><span foreground='#716c80'>{empty}</span> {percent}%"
    tooltip = ["Bluetooth on"]
    for device in connected:
        battery = f" · {device.battery}%" if device.battery is not None else ""
        tooltip.append(f"{escape(device.name, quote=False)}{battery}")
    return {"text": text, "tooltip": "\n".join(tooltip), "class": "connected" if connected else "on"}


if __name__ == "__main__":
    last_output = None

    def publish():
        global last_output
        payload = bar_status(bluetooth)
        if payload != last_output:
            print(json.dumps(payload), flush=True)
            last_output = payload

    bluetooth = Bluetooth(publish)
    publish()
    GLib.MainLoop().run()
