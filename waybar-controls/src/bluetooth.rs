use crate::{Panel, button, clear, horizontal, label, row, scrollable, spawn, vertical};
use glib::variant::{FromVariant, ObjectPath, ToVariant};
use gtk::{gio, glib, prelude::*};
use std::{
    cell::{Cell, RefCell},
    rc::{Rc, Weak},
    time::Duration,
};

const BLUEZ: &str = "org.bluez";
const ADAPTER: &str = "org.bluez.Adapter1";
const DEVICE: &str = "org.bluez.Device1";
const BATTERY: &str = "org.bluez.Battery1";
const AGENT: &str = "org.bluez.AgentManager1";
const AGENT_PATH: &str = "/local/waybar/BluetoothAgent";
const AGENT_XML: &str = include_str!("bluez-agent.xml");

struct DeviceInfo {
    path: glib::GString,
    name: String,
    paired: bool,
    known: bool,
    connected: bool,
    battery: Option<u8>,
}

struct Agent {
    path: glib::GString,
    object: gio::RegistrationId,
    request: Option<gio::DBusMethodInvocation>,
}

#[derive(Clone, Copy)]
enum Prompt {
    Notice,
    Confirm,
    Passkey,
    Pin,
}

struct Bluetooth {
    panel: Weak<Panel>,
    manager: gio::DBusObjectManagerClient,
    bus: gio::DBusConnection,
    power: gtk::Switch,
    status: gtk::Label,
    scan: gtk::Button,
    devices: gtk::Box,
    prompt: gtk::Box,
    agent: RefCell<Option<Agent>>,
    previous_power: Cell<Option<bool>>,
    power_pending: Cell<bool>,
    scan_pending: Cell<bool>,
    discovery: RefCell<Option<glib::GString>>,
    scan_timer: RefCell<Option<glib::SourceId>>,
    refresh: RefCell<Option<glib::SourceId>>,
    pending_device: RefCell<Option<glib::GString>>,
}

pub(crate) fn open(panel: &Rc<Panel>) {
    panel
        .body
        .append(&label("Loading Bluetooth devices…", "secondary"));
    let weak = Rc::downgrade(panel);
    spawn(async move {
        // Discovery and pairing are tied to a D-Bus peer. Give each popup its own peer so
        // BlueZ releases pending operations when the popup closes or the controller exits.
        let result = async {
            let address =
                gio::dbus_address_get_for_bus_sync(gio::BusType::System, gio::Cancellable::NONE)?;
            let flags = gio::DBusConnectionFlags::AUTHENTICATION_CLIENT
                | gio::DBusConnectionFlags::MESSAGE_BUS_CONNECTION;
            let bus =
                gio::DBusConnection::for_address_future(address.as_str(), flags, None).await?;
            let manager = gio::DBusObjectManagerClient::new_future(
                &bus,
                gio::DBusObjectManagerClientFlags::DO_NOT_AUTO_START,
                BLUEZ,
                "/",
            )
            .await?;
            Ok::<_, glib::Error>((bus, manager))
        }
        .await;
        let Some(panel) = weak.upgrade().filter(|panel| !panel.closed.get()) else {
            if let Ok((bus, _)) = result {
                let _ = bus.close_future().await;
            }
            return;
        };
        clear(&panel.body);
        match result {
            Ok((bus, manager)) => Bluetooth::build(&panel, bus, manager),
            Err(_) => panel
                .body
                .append(&label("Bluetooth service is unavailable.", "secondary")),
        }
    });
}

fn property<T: FromVariant>(proxy: &gio::DBusProxy, key: &str) -> Option<T> {
    proxy.cached_property(key)?.get()
}

fn flag(proxy: &gio::DBusProxy, key: &str) -> bool {
    property(proxy, key).unwrap_or(false)
}

fn proxy(object: &gio::DBusObject, interface: &str) -> Option<gio::DBusProxy> {
    gio::prelude::DBusObjectExt::interface(object, interface)?
        .downcast()
        .ok()
}

async fn call(
    bus: &gio::DBusConnection,
    path: &str,
    interface: &str,
    method: &str,
    parameters: Option<&glib::Variant>,
    timeout: i32,
) -> Result<glib::Variant, glib::Error> {
    bus.call_future(
        Some(BLUEZ),
        path,
        interface,
        method,
        parameters,
        None,
        gio::DBusCallFlags::NO_AUTO_START,
        timeout,
    )
    .await
    .map_err(|mut error| {
        gio::DBusError::strip_remote_error(&mut error);
        error
    })
}

impl Bluetooth {
    fn build(panel: &Rc<Panel>, bus: gio::DBusConnection, manager: gio::DBusObjectManagerClient) {
        let toolbar = horizontal(8);
        let status = label("", "secondary");
        status.set_hexpand(true);
        toolbar.append(&status);
        let scan = gtk::Button::with_label("Scan devices");
        scan.add_css_class("flat");
        toolbar.append(&scan);
        let power = gtk::Switch::builder()
            .valign(gtk::Align::Center)
            .tooltip_text("Enable Bluetooth")
            .build();
        toolbar.append(&power);
        panel.body.append(&toolbar);
        let prompt = vertical(6);
        prompt.set_visible(false);
        panel.body.append(&prompt);
        let devices = vertical(6);
        panel.body.append(&scrollable(&devices, None));

        let bluetooth = Rc::new(Self {
            panel: Rc::downgrade(panel),
            manager,
            bus,
            power,
            status,
            scan,
            devices,
            prompt,
            agent: RefCell::default(),
            previous_power: Cell::default(),
            power_pending: Cell::default(),
            scan_pending: Cell::default(),
            discovery: RefCell::default(),
            scan_timer: RefCell::default(),
            refresh: RefCell::default(),
            pending_device: RefCell::default(),
        });

        let retained = bluetooth.clone();
        panel.when_closed(move || retained.close());
        let mut bindings = Vec::new();
        for signal in [
            "object-added",
            "object-removed",
            "interface-added",
            "interface-removed",
            "interface-proxy-properties-changed",
        ] {
            let weak = Rc::downgrade(&bluetooth);
            bindings.push(bluetooth.manager.connect_local(signal, false, move |_| {
                if let Some(bluetooth) = weak.upgrade() {
                    bluetooth.queue_refresh();
                }
                None
            }));
        }
        let manager = bluetooth.manager.clone();
        panel.when_closed(move || {
            for binding in bindings {
                manager.disconnect(binding);
            }
        });
        let weak = Rc::downgrade(&bluetooth);
        bluetooth.power.connect_state_set(move |_, enabled| {
            let Some(bluetooth) = weak.upgrade() else {
                return glib::Propagation::Proceed;
            };
            if let Some(adapter) = bluetooth.adapter()
                && enabled != flag(&adapter, "Powered")
            {
                bluetooth.set_power(adapter.object_path(), enabled);
                return glib::Propagation::Stop;
            }
            glib::Propagation::Proceed
        });
        let weak = Rc::downgrade(&bluetooth);
        bluetooth.scan.connect_clicked(move |_| {
            if let Some(bluetooth) = weak.upgrade() {
                if bluetooth.discovery.borrow().is_some() {
                    bluetooth.stop_scan();
                } else {
                    bluetooth.start_scan();
                }
            }
        });
        bluetooth.render();
    }

    fn adapter(&self) -> Option<gio::DBusProxy> {
        self.manager
            .objects()
            .iter()
            .filter_map(|object| proxy(object, ADAPTER))
            .min_by_key(|adapter| adapter.object_path())
    }

    fn devices(&self, adapter: &gio::DBusProxy) -> Vec<DeviceInfo> {
        let mut devices: Vec<_> = self
            .manager
            .objects()
            .iter()
            .filter_map(|object| {
                let device = proxy(object, DEVICE)?;
                let parent: ObjectPath = property(&device, "Adapter")?;
                if parent.as_str() != adapter.object_path().as_str() {
                    return None;
                }
                let name = property::<String>(&device, "Alias")
                    .filter(|name| !name.is_empty())
                    .or_else(|| property(&device, "Address"))
                    .unwrap_or_else(|| "Bluetooth device".into());
                let name = name.split_whitespace().collect::<Vec<_>>().join(" ");
                let connected = flag(&device, "Connected");
                let paired = flag(&device, "Paired") || flag(&device, "Bonded");
                let battery = if connected {
                    proxy(object, BATTERY)
                        .and_then(|battery| property::<u8>(&battery, "Percentage"))
                        .filter(|value| *value <= 100)
                } else {
                    None
                };
                Some(DeviceInfo {
                    path: device.object_path(),
                    name,
                    connected,
                    paired,
                    known: paired || connected || flag(&device, "Trusted"),
                    battery,
                })
            })
            .collect();
        devices
            .sort_by_key(|device| (!device.known, !device.connected, device.name.to_lowercase()));
        devices
    }

    fn queue_refresh(self: &Rc<Self>) {
        if self.refresh.borrow().is_some() {
            return;
        }
        let weak = Rc::downgrade(self);
        self.refresh
            .replace(Some(glib::idle_add_local_once(move || {
                if let Some(bluetooth) = weak.upgrade() {
                    bluetooth.refresh.take();
                    bluetooth.render();
                }
            })));
    }

    fn render(self: &Rc<Self>) {
        let adapter = self.adapter();
        let powered = adapter
            .as_ref()
            .is_some_and(|adapter| flag(adapter, "Powered"));
        let previous_power = self.previous_power.replace(Some(powered));
        let start_scan = powered && previous_power == Some(false);
        self.power.set_state(powered);
        self.power.set_active(powered);
        self.power.set_sensitive(
            adapter.is_some() && !self.power_pending.get() && !self.scan_pending.get(),
        );
        self.scan.set_visible(powered);
        self.scan.set_sensitive(!self.scan_pending.get());
        self.scan.set_label(if self.discovery.borrow().is_some() {
            "Stop scan"
        } else {
            "Scan devices"
        });
        clear(&self.devices);
        let Some(adapter) = adapter else {
            self.status.set_text("No Bluetooth adapter");
            self.stop_scan();
            return;
        };
        if !powered {
            self.status.set_text("Bluetooth off");
            self.stop_scan();
            return;
        }
        let scanning = self.discovery.borrow().is_some() || flag(&adapter, "Discovering");
        self.status.set_text(if scanning {
            "Scanning…"
        } else {
            "Bluetooth on"
        });
        let devices = self.devices(&adapter);
        let pending = self.pending_device.borrow();
        for (known, heading) in [(true, "Known devices"), (false, "Nearby devices")] {
            if !devices.iter().any(|device| device.known == known) {
                continue;
            }
            self.devices.append(&label(heading, "heading"));
            for device in devices.iter().filter(|device| device.known == known) {
                let detail = if pending.as_ref() == Some(&device.path) {
                    "Working…".into()
                } else if device.connected {
                    device.battery.map_or_else(
                        || "Connected".into(),
                        |percent| format!("Connected · {percent}%"),
                    )
                } else if device.paired {
                    "Paired".into()
                } else if device.known {
                    "Known".into()
                } else {
                    "Pair".into()
                };
                let weak = Rc::downgrade(self);
                let path = device.path.clone();
                let paired = device.paired;
                let connected = device.connected;
                let control = row(
                    &self.devices,
                    &device.name,
                    &detail,
                    true,
                    connected,
                    move || {
                        if let Some(bluetooth) = weak.upgrade() {
                            bluetooth.select(&path, paired, connected);
                        }
                    },
                );
                control.set_sensitive(pending.is_none());
            }
        }
        drop(pending);
        if devices.is_empty() {
            self.devices.append(&label(
                if scanning {
                    "Looking for devices…"
                } else {
                    "No devices found"
                },
                "secondary",
            ));
        }
        if start_scan {
            self.start_scan();
        }
    }

    fn error(&self, message: &str) {
        if let Some(panel) = self.panel.upgrade() {
            panel.error(message);
        }
    }

    fn set_power(self: &Rc<Self>, path: glib::GString, enabled: bool) {
        if self.power_pending.replace(true) {
            return;
        }
        self.error("");
        self.power.set_sensitive(false);
        let weak = Rc::downgrade(self);
        let bus = self.bus.clone();
        spawn(async move {
            let parameters = (ADAPTER, "Powered", enabled.to_variant()).to_variant();
            let result = call(
                &bus,
                &path,
                "org.freedesktop.DBus.Properties",
                "Set",
                Some(&parameters),
                9000,
            )
            .await;
            if let Some(bluetooth) = weak.upgrade() {
                bluetooth.power_pending.set(false);
                if let Err(error) = result {
                    bluetooth.error(&format!(
                        "Could not turn Bluetooth {}: {error}",
                        if enabled { "on" } else { "off" }
                    ));
                }
                bluetooth.render();
            }
        });
    }

    fn start_scan(self: &Rc<Self>) {
        let Some(adapter) = self.adapter().filter(|adapter| flag(adapter, "Powered")) else {
            return;
        };
        if self.scan_pending.get() || self.discovery.borrow().is_some() {
            return;
        }
        self.error("");
        let path = adapter.object_path();
        self.discovery.replace(Some(path.clone()));
        self.scan_pending.set(true);
        self.render();

        let bus = self.bus.clone();
        let weak = Rc::downgrade(self);
        spawn(async move {
            let result = call(&bus, &path, ADAPTER, "StartDiscovery", None, 9000).await;
            let Some(bluetooth) = weak.upgrade() else {
                return;
            };
            bluetooth.scan_pending.set(false);
            if let Err(error) = result {
                bluetooth.discovery.take();
                bluetooth.error(&format!("Could not scan: {error}"));
            } else if bluetooth.discovery.borrow().as_ref() == Some(&path) {
                let weak = Rc::downgrade(&bluetooth);
                bluetooth
                    .scan_timer
                    .replace(Some(glib::timeout_add_local_once(
                        Duration::from_secs(15),
                        move || {
                            if let Some(bluetooth) = weak.upgrade() {
                                bluetooth.scan_timer.take();
                                bluetooth.stop_scan();
                                bluetooth.render();
                            }
                        },
                    )));
            }
            bluetooth.render();
        });
    }

    fn stop_scan(self: &Rc<Self>) {
        if let Some(timer) = self.scan_timer.take() {
            timer.remove();
        }
        let Some(path) = self.discovery.take() else {
            return;
        };
        if self.scan_pending.replace(true) {
            return;
        }
        self.scan.set_sensitive(false);
        let bus = self.bus.clone();
        let weak = Rc::downgrade(self);
        spawn(async move {
            let result = call(&bus, &path, ADAPTER, "StopDiscovery", None, 9000).await;
            if let Some(bluetooth) = weak.upgrade() {
                bluetooth.scan_pending.set(false);
                if let Err(error) = result
                    && bluetooth
                        .adapter()
                        .is_some_and(|adapter| flag(&adapter, "Powered"))
                {
                    bluetooth.error(&format!("Could not stop scanning: {error}"));
                }
                bluetooth.render();
            }
        });
    }

    fn select(self: &Rc<Self>, path: &glib::GString, paired: bool, connected: bool) {
        if self.pending_device.borrow().is_some() {
            return;
        }
        self.error("");
        self.pending_device.replace(Some(path.clone()));
        self.render();
        let bus = self.bus.clone();
        let path = path.clone();
        let weak = Rc::downgrade(self);
        if !paired
            && !connected
            && let Err(error) = self.open_agent(&path)
        {
            self.finish_device(Some(error.message()));
            return;
        }
        spawn(async move {
            let result = if !paired && !connected {
                async {
                    let object = ObjectPath::try_from(AGENT_PATH).expect("valid D-Bus path");
                    call(
                        &bus,
                        "/org/bluez",
                        AGENT,
                        "RegisterAgent",
                        Some(&(&object, "KeyboardDisplay").to_variant()),
                        9000,
                    )
                    .await?;
                    let result = async {
                        call(&bus, &path, DEVICE, "Pair", None, 60000).await?;
                        if let Some(bluetooth) = weak.upgrade() {
                            bluetooth.show_prompt("", Prompt::Notice);
                        }
                        call(&bus, &path, DEVICE, "Connect", None, 25000).await
                    }
                    .await;
                    let _ = call(
                        &bus,
                        "/org/bluez",
                        AGENT,
                        "UnregisterAgent",
                        Some(&(object,).to_variant()),
                        9000,
                    )
                    .await;
                    result
                }
                .await
            } else {
                call(
                    &bus,
                    &path,
                    DEVICE,
                    if connected { "Disconnect" } else { "Connect" },
                    None,
                    25000,
                )
                .await
            };
            if let Some(bluetooth) = weak.upgrade() {
                bluetooth.finish_device(result.as_ref().err().map(|error| error.message()));
            }
        });
    }

    fn open_agent(self: &Rc<Self>, path: &glib::GString) -> Result<(), glib::Error> {
        let details = gio::DBusNodeInfo::for_xml(AGENT_XML)?;
        let info = details
            .lookup_interface("org.bluez.Agent1")
            .expect("agent introspection is defined");
        let weak = Rc::downgrade(self);
        let object = self
            .bus
            .register_object(AGENT_PATH, &info)
            .method_call(move |_, sender, _, _, method, params, request| {
                if let Some(bluetooth) = weak.upgrade() {
                    bluetooth.agent_request(sender, method, params, request);
                } else {
                    rejected(request);
                }
            })
            .build()?;
        self.agent.replace(Some(Agent {
            path: path.clone(),
            object,
            request: None,
        }));
        Ok(())
    }

    fn agent_request(
        self: &Rc<Self>,
        sender: Option<&str>,
        method: &str,
        params: glib::Variant,
        request: gio::DBusMethodInvocation,
    ) {
        let mut agent = self.agent.borrow_mut();
        let Some(agent) = agent.as_mut() else {
            rejected(request);
            return;
        };
        let owner = self.manager.name_owner();
        if sender.is_none() || sender != owner.as_deref() {
            rejected(request);
            return;
        }
        if matches!(method, "Release" | "Cancel") {
            if let Some(pending) = agent.request.take() {
                rejected(pending);
            }
            self.show_prompt("", Prompt::Notice);
            request.return_value(None);
            return;
        }
        let path = params
            .try_child_value(0)
            .and_then(|value| value.get::<ObjectPath>());
        if path.as_ref().map(|path| path.as_str()) != Some(agent.path.as_str()) {
            rejected(request);
            return;
        }
        let (message, prompt) = match method {
            "DisplayPinCode" | "DisplayPasskey" => {
                let code = if method == "DisplayPinCode" {
                    params.child_value(1).get::<String>().unwrap_or_default()
                } else {
                    format!(
                        "{:06}",
                        params.child_value(1).get::<u32>().unwrap_or_default()
                    )
                };
                self.show_prompt(
                    &format!("Enter {code} on the device, then press Enter there."),
                    Prompt::Notice,
                );
                request.return_value(None);
                return;
            }
            "RequestConfirmation" => (
                format!(
                    "Confirm this code on the device: {:06}",
                    params.child_value(1).get::<u32>().unwrap_or_default()
                ),
                Prompt::Confirm,
            ),
            "RequestPasskey" => (
                "Enter the six-digit passkey shown on the device.".into(),
                Prompt::Passkey,
            ),
            "RequestPinCode" => ("Enter the PIN for this device.".into(), Prompt::Pin),
            "RequestAuthorization" => ("Allow this device to pair?".into(), Prompt::Confirm),
            "AuthorizeService" => ("Allow this Bluetooth service?".into(), Prompt::Confirm),
            _ => {
                rejected(request);
                return;
            }
        };
        if let Some(pending) = agent.request.replace(request) {
            rejected(pending);
        }
        self.show_prompt(&message, prompt);
    }

    fn show_prompt(self: &Rc<Self>, message: &str, prompt: Prompt) {
        clear(&self.prompt);
        self.prompt.set_visible(!message.is_empty());
        if message.is_empty() {
            return;
        }
        self.prompt.append(&label(message, ""));
        if matches!(prompt, Prompt::Notice) {
            return;
        }
        let entry = if matches!(prompt, Prompt::Passkey | Prompt::Pin) {
            let entry = gtk::Entry::builder()
                .max_length(if matches!(prompt, Prompt::Passkey) {
                    6
                } else {
                    16
                })
                .build();
            if matches!(prompt, Prompt::Passkey) {
                entry.set_input_purpose(gtk::InputPurpose::Digits);
            }
            self.prompt.append(&entry);
            Some(entry)
        } else {
            None
        };
        let weak = Rc::downgrade(self);
        let entry_weak = entry.as_ref().map(ObjectExt::downgrade);
        let confirm = move || {
            let Some(bluetooth) = weak.upgrade() else {
                return;
            };
            let text = entry_weak
                .as_ref()
                .and_then(|entry| entry.upgrade())
                .map(|entry| entry.text())
                .unwrap_or_default();
            let answer = match prompt {
                Prompt::Passkey => {
                    let text = text.trim();
                    if text.len() != 6 || !text.bytes().all(|byte| byte.is_ascii_digit()) {
                        bluetooth.error("Enter the six-digit passkey from the device.");
                        return;
                    }
                    Some((text.parse::<u32>().expect("six decimal digits"),).to_variant())
                }
                Prompt::Pin => {
                    if text.trim().is_empty() {
                        bluetooth.error("Enter the PIN for this device.");
                        return;
                    }
                    Some((text.trim(),).to_variant())
                }
                _ => None,
            };
            bluetooth.error("");
            bluetooth.answer(true, answer.as_ref());
        };
        let actions = horizontal(8);
        let weak = Rc::downgrade(self);
        actions.append(&button("Cancel", "settings", move || {
            if let Some(bluetooth) = weak.upgrade() {
                bluetooth.answer(false, None);
            }
        }));
        actions.append(&button("Confirm", "selected", confirm.clone()));
        self.prompt.append(&actions);
        if let Some(entry) = entry {
            entry.connect_activate(move |_| confirm());
            entry.grab_focus();
        }
    }

    fn answer(self: &Rc<Self>, accepted: bool, answer: Option<&glib::Variant>) {
        if let Some(request) = self
            .agent
            .borrow_mut()
            .as_mut()
            .and_then(|agent| agent.request.take())
        {
            if accepted {
                request.return_value(answer);
            } else {
                rejected(request);
            }
        }
        self.show_prompt("", Prompt::Notice);
    }

    fn finish_device(self: &Rc<Self>, error: Option<&str>) {
        if let Some(agent) = self.agent.take() {
            if let Some(request) = agent.request {
                rejected(request);
            }
            let _ = self.bus.unregister_object(agent.object);
        }
        self.pending_device.take();
        self.show_prompt("", Prompt::Notice);
        if let Some(error) = error {
            self.error(error);
        }
        self.render();
    }

    fn close(&self) {
        if let Some(source) = self.refresh.take() {
            source.remove();
        }
        if let Some(source) = self.scan_timer.take() {
            source.remove();
        }
        let path = self.agent.take().map(|agent| {
            if let Some(request) = agent.request {
                rejected(request);
            }
            let _ = self.bus.unregister_object(agent.object);
            agent.path
        });
        let bus = self.bus.clone();
        spawn(async move {
            if let Some(path) = path {
                let _ = call(&bus, &path, DEVICE, "CancelPairing", None, 1000).await;
            }
            let _ = bus.close_future().await;
        });
    }
}

fn rejected(request: gio::DBusMethodInvocation) {
    request.return_dbus_error("org.bluez.Error.Rejected", "Pairing request declined");
}
