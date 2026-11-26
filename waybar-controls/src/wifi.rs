use crate::{
    Panel, button, clear, command, command_output, horizontal, label, launch, row, scrollable,
    spawn, vertical,
};
use glib::translate::IntoGlib;
use gtk::{gio, glib, prelude::*};
use nm::{
    ActiveConnectionState as State, NM80211ApFlags as Flags, NM80211ApSecurityFlags as Security,
    prelude::{ActiveConnectionExt, ConnectionExt, DeviceExt},
};
use std::{
    cell::{Cell, RefCell},
    collections::{HashMap, HashSet},
    net::IpAddr,
    rc::{Rc, Weak},
};

const NM: &str = "org.freedesktop.NetworkManager";
const NM_PATH: &str = "/org/freedesktop/NetworkManager";
const FIELDS: [&str; 9] = [
    "Wi-Fi standard",
    "Frequency",
    "Signal",
    "Receive rate",
    "Transmit rate",
    "Local IPv4",
    "Local IPv6",
    "Gateway",
    "Public IP",
];

struct Wifi {
    panel: Weak<Panel>,
    network: nm::Client,
    device: Option<nm::DeviceWifi>,
    radio: gtk::Switch,
    scan: gtk::Button,
    connected: gtk::Box,
    networks: gtk::Box,
    scroll: gtk::ScrolledWindow,
    passwords: gtk::Box,
    vpn: gtk::Box,
    tailscale: gtk::Box,
    details: gtk::Revealer,
    values: [gtk::Label; 9],
    detail_connection: RefCell<Option<glib::GString>>,
    detail_timer: RefCell<Option<glib::SourceId>>,
    detail_generation: Cell<u64>,
    radio_busy: Cell<bool>,
}

pub(crate) fn open(panel: &Rc<Panel>) {
    panel.body.append(&label("Loading networks…", "secondary"));
    let weak = Rc::downgrade(panel);
    spawn(async move {
        let result = nm::Client::new_future().await;
        let Some(panel) = weak.upgrade().filter(|panel| !panel.closed.get()) else {
            return;
        };
        clear(&panel.body);
        match result {
            Ok(network) if network.property::<bool>("nm-running") => Wifi::build(&panel, network),
            _ => panel
                .body
                .append(&label("NetworkManager is unavailable.", "secondary")),
        }
    });
}

fn active_ap(device: &nm::DeviceWifi) -> Option<nm::AccessPoint> {
    device.property("active-access-point")
}

fn secured(ap: &nm::AccessPoint) -> bool {
    ap.flags().contains(Flags::PRIVACY) || !ap.wpa_flags().is_empty() || !ap.rsn_flags().is_empty()
}

fn watch(
    panel: &Panel,
    object: &impl IsA<glib::Object>,
    signal: &str,
    callback: impl Fn() + 'static,
) {
    let id = object.connect_local(signal, false, move |_| {
        callback();
        None
    });
    let object = object.clone().upcast::<glib::Object>();
    panel.when_closed(move || object.disconnect(id));
}

impl Wifi {
    fn build(panel: &Rc<Panel>, network: nm::Client) {
        let devices: Vec<_> = network
            .devices()
            .into_iter()
            .filter_map(|d| d.downcast().ok())
            .collect();
        let device = devices
            .iter()
            .find(|d| active_ap(d).is_some())
            .or_else(|| devices.first())
            .cloned();

        let radio = gtk::Switch::builder()
            .valign(gtk::Align::Center)
            .tooltip_text("Enable Wi-Fi")
            .build();
        let scan = gtk::Button::with_label("Rescan");
        scan.add_css_class("flat");
        let connected = vertical(0);
        let networks = vertical(6);
        let passwords = vertical(6);

        let grid = gtk::Grid::builder()
            .column_spacing(14)
            .row_spacing(5)
            .build();
        grid.add_css_class("wifi-details");
        let values = FIELDS.map(|_| {
            let field = label("", "");
            field.set_wrap_mode(gtk::pango::WrapMode::WordChar);
            field.set_hexpand(true);
            field.set_selectable(true);
            field
        });
        for (index, (field, value)) in FIELDS.iter().zip(&values).enumerate() {
            let title = label(field, "secondary");
            title.set_valign(gtk::Align::Start);
            grid.attach(&title, 0, index as i32, 1, 1);
            grid.attach(value, 1, index as i32, 1, 1);
        }
        values[8].set_tooltip_text(Some(
            "Internet-facing address for the current route, including any active VPN exit node",
        ));
        let details = gtk::Revealer::builder()
            .transition_type(gtk::RevealerTransitionType::SlideDown)
            .transition_duration(160)
            .child(&grid)
            .build();
        let content = vertical(6);
        content.append(&details);
        content.append(&networks);
        let scroll = scrollable(&content, None);

        if device.is_some() {
            let toolbar = horizontal(8);
            let caption = label("Wireless", "heading");
            caption.set_hexpand(true);
            toolbar.append(&caption);
            toolbar.append(&scan);
            toolbar.append(&radio);
            panel.body.append(&toolbar);
            panel.body.append(&connected);
            panel.body.append(&scroll);
            panel.body.append(&passwords);
        } else {
            panel
                .body
                .append(&label("No Wi-Fi adapter found.", "secondary"));
        }
        panel
            .body
            .append(&gtk::Separator::new(gtk::Orientation::Horizontal));
        let toolbar = horizontal(8);
        let caption = label("VPN", "heading");
        caption.set_hexpand(true);
        toolbar.append(&caption);
        let weak = Rc::downgrade(panel);
        toolbar.append(&button("Add VPN…", "flat", move || {
            if let Some(panel) = weak.upgrade() {
                panel.open_settings(&["nm-connection-editor", "--create", "--type=vpn"]);
            }
        }));
        panel.body.append(&toolbar);
        let tailscale = vertical(0);
        let vpn = vertical(6);
        panel.body.append(&tailscale);
        panel.body.append(&scrollable(&vpn, Some(112)));

        let wifi = Rc::new(Self {
            panel: Rc::downgrade(panel),
            network,
            device,
            radio,
            scan,
            connected,
            networks,
            scroll,
            passwords,
            vpn,
            tailscale,
            details,
            values,
            detail_connection: RefCell::default(),
            detail_timer: RefCell::default(),
            detail_generation: Cell::default(),
            radio_busy: Cell::default(),
        });
        let retained = wifi.clone();
        panel.when_closed(move || retained.stop_details());
        wifi.bind(panel);
        wifi.render_vpn();
        wifi.load_tailscale();
        if wifi.device.is_some() {
            wifi.render_networks();
            if wifi.network.wireless_get_enabled() {
                wifi.rescan();
            }
        }
    }

    fn bind(self: &Rc<Self>, panel: &Panel) {
        for signal in [
            "notify::active-connections",
            "connection-added",
            "connection-removed",
        ] {
            let weak = Rc::downgrade(self);
            watch(panel, &self.network, signal, move || {
                if let Some(wifi) = weak.upgrade() {
                    wifi.render_vpn();
                }
            });
        }
        let Some(device) = &self.device else {
            return;
        };
        let weak = Rc::downgrade(self);
        watch(
            panel,
            &self.network,
            "notify::wireless-enabled",
            move || {
                if let Some(wifi) = weak.upgrade() {
                    wifi.render_networks();
                }
            },
        );
        for signal in [
            "notify::last-scan",
            "notify::active-access-point",
            "access-point-added",
            "access-point-removed",
            "notify::active-connection",
        ] {
            let weak = Rc::downgrade(self);
            watch(panel, device, signal, move || {
                if let Some(wifi) = weak.upgrade() {
                    wifi.render_networks();
                }
            });
        }
        let weak = Rc::downgrade(self);
        self.scan.connect_clicked(move |_| {
            if let Some(wifi) = weak.upgrade() {
                wifi.rescan();
            }
        });
        let weak = Rc::downgrade(self);
        self.radio.connect_state_set(move |_, enabled| {
            let Some(wifi) = weak.upgrade() else {
                return glib::Propagation::Proceed;
            };
            if enabled == wifi.network.wireless_get_enabled() {
                return glib::Propagation::Proceed;
            }
            wifi.set_radio(enabled);
            glib::Propagation::Stop
        });
    }

    fn set_radio(self: &Rc<Self>, enabled: bool) {
        if self.radio_busy.replace(true) {
            return;
        }
        self.radio.set_sensitive(false);
        let weak = Rc::downgrade(self);
        spawn(async move {
            let result = async {
                let bus = gio::bus_get_future(gio::BusType::System).await?;
                bus.call_future(
                    Some(NM),
                    NM_PATH,
                    "org.freedesktop.DBus.Properties",
                    "Set",
                    Some(&(NM, "WirelessEnabled", enabled.to_variant()).to_variant()),
                    None,
                    gio::DBusCallFlags::NONE,
                    5000,
                )
                .await
            }
            .await;
            if let Some(wifi) = weak.upgrade() {
                wifi.radio_busy.set(false);
                wifi.error(result.as_ref().err().map_or("", |error| error.message()));
                wifi.render_networks();
            }
        });
    }

    fn rescan(self: &Rc<Self>) {
        let Some(device) = self.device.clone() else {
            return;
        };
        let weak = Rc::downgrade(self);
        spawn(async move {
            // NetworkManager can reject a scan while another one is underway.
            let _ = device.request_scan_future().await;
            if let Some(wifi) = weak.upgrade() {
                wifi.render_networks();
            }
        });
    }

    fn error(&self, message: &str) {
        if let Some(panel) = self.panel.upgrade() {
            panel.error(message);
        }
    }

    fn render_networks(self: &Rc<Self>) {
        let Some(device) = &self.device else {
            return;
        };
        let enabled = self.network.wireless_get_enabled();
        self.radio.set_state(enabled);
        self.radio.set_active(enabled);
        self.radio.set_sensitive(!self.radio_busy.get());
        self.scan.set_sensitive(enabled);
        clear(&self.connected);
        clear(&self.networks);

        let connection = if enabled {
            device.property::<Option<nm::ActiveConnection>>("active-connection")
        } else {
            None
        };
        let path = connection.map(|connection| nm::prelude::ObjectExt::path(&connection));
        if *self.detail_connection.borrow() != path {
            self.detail_connection.replace(path);
            self.details.set_reveal_child(false);
            self.stop_details();
        }
        if !enabled {
            clear(&self.passwords);
            self.networks.append(&label("Wi-Fi is off", "secondary"));
            return;
        }

        let active = active_ap(device);
        let mut points = device.access_points();
        points.sort_by_key(|ap| std::cmp::Reverse((active.as_ref() == Some(ap), ap.strength())));
        let mut seen = HashSet::new();
        for ap in points {
            let Some(ssid) = ap
                .property::<Option<glib::Bytes>>("ssid")
                .filter(|ssid| !ssid.is_empty())
            else {
                continue;
            };
            let secure = secured(&ap);
            if !seen.insert((ssid.clone(), secure)) {
                continue;
            }
            let title = nm::functions::utils_ssid_to_utf8(ssid.as_ref());
            let selected = active.as_ref() == Some(&ap);
            let status = if selected {
                "Connected"
            } else if secure {
                "Secured"
            } else {
                "Open"
            };
            let detail = format!(
                "{status} · {}%{}",
                ap.strength(),
                if selected { "  ▾" } else { "" }
            );
            let weak = Rc::downgrade(self);
            let control = row(
                if selected {
                    &self.connected
                } else {
                    &self.networks
                },
                title.as_str(),
                &detail,
                true,
                selected,
                move || {
                    if let Some(wifi) = weak.upgrade() {
                        if selected {
                            wifi.toggle_details();
                        } else {
                            wifi.choose(ap.clone());
                        }
                    }
                },
            );
            if selected {
                control.set_tooltip_text(Some("Show or hide connection details"));
            }
        }
        if seen.is_empty() {
            self.networks
                .append(&label("No nearby networks found", "secondary"));
        }
    }

    fn choose(self: &Rc<Self>, ap: nm::AccessPoint) {
        self.error("");
        clear(&self.passwords);
        let Some(device) = &self.device else {
            return;
        };
        if active_ap(device).as_ref() == Some(&ap) {
            return;
        }
        let connections: Vec<_> = device
            .available_connections()
            .into_iter()
            .map(|c| c.upcast())
            .collect();
        if let Some(saved) = ap.filter_connections(&connections).first() {
            self.activate(&ap, Some(saved), None, None);
            return;
        }
        let flags = ap.rsn_flags() | ap.wpa_flags();
        let key = if flags.contains(Security::KEY_MGMT_PSK) {
            "wpa-psk"
        } else if flags.contains(Security::KEY_MGMT_SAE) {
            "sae"
        } else if flags.intersects(Security::KEY_MGMT_OWE | Security::KEY_MGMT_OWE_TM) {
            self.activate(&ap, None, Some("owe"), None);
            return;
        } else if secured(&ap) {
            self.error("Use Network settings to configure this network’s security.");
            return;
        } else {
            self.activate(&ap, None, None, None);
            return;
        };

        let ssid = ap
            .property::<Option<glib::Bytes>>("ssid")
            .map(|bytes| nm::functions::utils_ssid_to_utf8(bytes.as_ref()))
            .unwrap_or_default();
        let password = gtk::PasswordEntry::builder()
            .placeholder_text(format!("Password for {ssid}"))
            .show_peek_icon(true)
            .build();
        let weak = Rc::downgrade(self);
        let entry = password.downgrade();
        let submit = move || {
            let (Some(wifi), Some(password)) = (weak.upgrade(), entry.upgrade()) else {
                return;
            };
            let secret = password.text();
            if secret.is_empty() {
                wifi.error("Enter the Wi-Fi password.");
            } else {
                password.set_text("");
                wifi.activate(&ap, None, Some(key), Some(secret.as_str()));
            }
        };
        let activate = submit.clone();
        password.connect_activate(move |_| activate());
        self.passwords.append(&password);
        self.passwords.append(&button("Connect", "", submit));
        password.grab_focus();
    }

    fn activate(
        self: &Rc<Self>,
        ap: &nm::AccessPoint,
        saved: Option<&nm::Connection>,
        key: Option<&str>,
        secret: Option<&str>,
    ) {
        let Some(device) = &self.device else {
            return;
        };
        let path = nm::prelude::ObjectExt::path(ap);
        let future = if let Some(saved) = saved {
            self.network
                .activate_connection_future(Some(saved), Some(device), Some(path.as_str()))
        } else {
            let connection = nm::SimpleConnection::new();
            if let Some(key) = key {
                let setting = nm::SettingWirelessSecurity::new();
                setting.set_key_mgmt(Some(key));
                setting.set_psk(secret);
                connection.add_setting(setting);
            }
            self.network.add_and_activate_connection_future(
                Some(&connection),
                Some(device),
                Some(path.as_str()),
            )
        };
        let weak = Rc::downgrade(self);
        spawn(async move {
            let result = future.await;
            let Some(wifi) = weak.upgrade() else {
                return;
            };
            match result {
                Ok(connection) => wifi.watch_connection(&connection, false),
                Err(error) => wifi.error(error.message()),
            }
        });
    }

    fn watch_connection(self: &Rc<Self>, connection: &nm::ActiveConnection, vpn: bool) {
        let Some(panel) = self.panel.upgrade() else {
            return;
        };
        let weak = Rc::downgrade(self);
        let id = connection.connect_state_changed(move |connection, _state, reason| {
            if let Some(wifi) = weak.upgrade() {
                wifi.connection_status(connection, connection.state(), reason, vpn);
            }
        });
        let retained = connection.clone();
        panel.when_closed(move || retained.disconnect(id));
        self.connection_status(
            connection,
            connection.state(),
            nm::ActiveConnectionStateReason::Unknown.into_glib() as u32,
            vpn,
        );
    }

    fn connection_status(
        self: &Rc<Self>,
        connection: &nm::ActiveConnection,
        state: State,
        reason: u32,
        vpn: bool,
    ) {
        if vpn {
            if state == State::Deactivated
                && reason != nm::ActiveConnectionStateReason::UserDisconnected.into_glib() as u32
            {
                self.error(&format!("{} disconnected.", connection.id()));
            }
            self.render_vpn();
        } else if state == State::Activated {
            clear(&self.passwords);
            self.error("");
            self.render_networks();
        } else if state == State::Deactivated {
            self.error("Could not connect. Check the network settings and try again.");
        }
    }

    fn toggle_details(self: &Rc<Self>) {
        if self.details.reveals_child() {
            self.details.set_reveal_child(false);
            self.stop_details();
            return;
        }
        if self.device.as_ref().and_then(active_ap).is_none() {
            return;
        }
        for index in [0, 2, 3, 4, 8] {
            self.values[index].set_text("Loading…");
        }
        self.scroll
            .vadjustment()
            .set_value(self.scroll.vadjustment().lower());
        self.details.set_reveal_child(true);
        self.refresh_details();
        let weak = Rc::downgrade(self);
        self.detail_timer
            .replace(Some(glib::timeout_add_seconds_local(3, move || {
                if let Some(wifi) = weak.upgrade() {
                    wifi.refresh_details();
                    glib::ControlFlow::Continue
                } else {
                    glib::ControlFlow::Break
                }
            })));
        let generation = self.detail_generation.get();
        let weak = Rc::downgrade(self);
        self.values[8].set_text("Looking up…");
        spawn(async move {
            let address = command(
                &[
                    "curl",
                    "--fail",
                    "--silent",
                    "--max-time",
                    "4",
                    "https://api64.ipify.org",
                ],
                4,
            )
            .await
            .and_then(|stdout| stdout.trim().parse::<IpAddr>().ok())
            .map(|ip| ip.to_string());
            if let Some(wifi) = weak
                .upgrade()
                .filter(|wifi| wifi.detail_generation.get() == generation)
            {
                wifi.values[8].set_text(address.as_deref().unwrap_or("Unavailable"));
            }
        });
    }

    fn stop_details(&self) {
        self.detail_generation
            .set(self.detail_generation.get().wrapping_add(1));
        if let Some(timer) = self.detail_timer.take() {
            timer.remove();
        }
    }

    fn refresh_details(self: &Rc<Self>) {
        let Some(device) = &self.device else {
            return;
        };
        let Some(ap) = active_ap(device) else {
            self.details.set_reveal_child(false);
            self.stop_details();
            return;
        };
        let frequency = ap.frequency();
        if frequency == 0 {
            self.values[1].set_text("Unavailable");
        } else {
            self.values[1].set_text(&format!("{frequency} MHz"));
        }
        let configs =
            ["ip4-config", "ip6-config"].map(|prop| device.property::<Option<nm::IPConfig>>(prop));
        for (index, config) in configs.iter().enumerate() {
            let addresses = config
                .as_ref()
                .map(|config| {
                    config
                        .addresses()
                        .into_iter()
                        .map(|addr| addr.address().to_string())
                        .collect::<Vec<_>>()
                        .join("\n")
                })
                .unwrap_or_default();
            self.values[5 + index].set_text(if addresses.is_empty() {
                "Unavailable"
            } else {
                &addresses
            });
        }
        let gateway = configs
            .iter()
            .flatten()
            .find_map(|cfg| cfg.property::<Option<String>>("gateway"));
        self.values[7].set_text(gateway.as_deref().unwrap_or("Unavailable"));

        let iface = device.iface().to_string();
        let generation = self.detail_generation.get();
        let weak = Rc::downgrade(self);
        spawn(async move {
            let data = command(&["iw", "dev", &iface, "link"], 1)
                .await
                .unwrap_or_default();
            let Some(wifi) = weak
                .upgrade()
                .filter(|wifi| wifi.detail_generation.get() == generation)
            else {
                return;
            };
            let Some(current) = wifi
                .device
                .as_ref()
                .and_then(active_ap)
                .filter(|current| current == &ap)
            else {
                return;
            };
            let link = Link::parse(&data, frequency);
            wifi.values[0].set_text(link.standard);
            let strength = format!(
                "{}%{}",
                current.strength(),
                link.signal
                    .map_or_else(String::new, |signal| format!(" · {signal}"))
            );
            wifi.values[2].set_text(&strength);
            wifi.values[3].set_text(&link.receive);
            wifi.values[4].set_text(&link.transmit);
        });
    }

    fn render_vpn(self: &Rc<Self>) {
        clear(&self.vpn);
        let active: HashMap<_, _> = self
            .network
            .active_connections()
            .into_iter()
            .filter(|connection| {
                matches!(
                    connection.state(),
                    State::Activating | State::Activated | State::Deactivating
                )
            })
            .map(|connection| {
                (
                    nm::prelude::ActiveConnectionExt::uuid(&connection),
                    connection,
                )
            })
            .collect();
        let mut profiles: Vec<_> = self
            .network
            .connections()
            .into_iter()
            .filter(|profile| matches!(profile.connection_type().as_str(), "vpn" | "wireguard"))
            .collect();
        profiles.sort_by_key(|profile| profile.id().to_lowercase());
        for profile in profiles {
            let connection = active.get(&profile.uuid()).cloned();
            let connected = connection.is_some();
            let id = profile.id();
            let weak = Rc::downgrade(self);
            row(
                &self.vpn,
                id.as_str(),
                if connected { "Disconnect" } else { "Connect" },
                true,
                connected,
                move || {
                    let Some(wifi) = weak.upgrade() else {
                        return;
                    };
                    wifi.error("");
                    let weak = Rc::downgrade(&wifi);
                    let network = wifi.network.clone();
                    let profile = profile.clone();
                    let connection = connection.clone();
                    spawn(async move {
                        let result = if let Some(connection) = connection {
                            network
                                .deactivate_connection_future(&connection)
                                .await
                                .map(|_| None)
                        } else {
                            network
                                .activate_connection_future(
                                    Some(&profile),
                                    None::<&nm::Device>,
                                    None,
                                )
                                .await
                                .map(Some)
                        };
                        if let Some(wifi) = weak.upgrade() {
                            match result {
                                Ok(Some(connection)) => wifi.watch_connection(&connection, true),
                                Err(error) => wifi.error(error.message()),
                                _ => {}
                            }
                            wifi.render_vpn();
                        }
                    });
                },
            );
        }
    }

    fn load_tailscale(self: &Rc<Self>) {
        let weak = Rc::downgrade(self);
        spawn(async move {
            let Some((_, status)) =
                command_output(&["tailscale", "status", "--json", "--peers=false"], 2).await
            else {
                return;
            };
            let Ok(status) = serde_json::from_str::<serde_json::Value>(&status) else {
                return;
            };
            let Some(wifi) = weak.upgrade() else {
                return;
            };
            let state = status["BackendState"].as_str().unwrap_or_default();
            let row = horizontal(8);
            let title = label("Tailscale", "");
            title.set_hexpand(true);
            row.append(&title);
            let caption = match state {
                "Running" => "Connected",
                "Starting" => "Connecting",
                "Stopped" => "Disconnected",
                "NeedsLogin" => "Sign-in required",
                "NeedsMachineAuth" => "Approval required",
                _ => "Unavailable",
            };
            row.append(&label(caption, "secondary"));
            let toggle = gtk::Switch::builder()
                .active(matches!(state, "Running" | "Starting"))
                .valign(gtk::Align::Center)
                .tooltip_text("Connect or disconnect Tailscale")
                .sensitive(matches!(state, "Running" | "Starting" | "Stopped"))
                .build();
            toggle.connect_state_set(move |toggle, enabled| {
                let Some(wifi) = weak.upgrade() else { return glib::Propagation::Stop; };
                let panel = wifi.panel.clone();
                toggle.set_sensitive(false);
                spawn(async move {
                    let prefs = command(&["tailscale", "debug", "prefs"], 2).await
                        .and_then(|prefs| serde_json::from_str::<serde_json::Value>(&prefs).ok());
                    let user = glib::user_name();
                    let operator = prefs.as_ref().and_then(|prefs| prefs["OperatorUser"].as_str()) == user.to_str();
                    let action = if enabled { "up" } else { "down" };
                    if let Some(panel) = panel.upgrade() { panel.window.close(); }
                    if operator { let _ = launch(&["tailscale", action]); }
                    else {
                        let _ = launch(&["sh", "-c", "systemctl --user start plasma-polkit-agent.service && exec pkexec --disable-internal-agent /usr/bin/tailscale \"$1\"", "tailscale-vpn", action]);
                    }
                });
                glib::Propagation::Stop
            });
            row.append(&toggle);
            wifi.tailscale.append(&row);
        });
    }
}

struct Link<'a> {
    standard: &'a str,
    signal: Option<&'a str>,
    receive: String,
    transmit: String,
}

impl<'a> Link<'a> {
    fn parse(data: &'a str, frequency: u32) -> Self {
        let fields: HashMap<_, _> = data
            .lines()
            .filter_map(|line| line.trim().split_once(':'))
            .collect();
        let rx = fields.get("rx bitrate").copied().unwrap_or_default();
        let tx = fields.get("tx bitrate").copied().unwrap_or_default();
        let contains = |key| rx.contains(key) || tx.contains(key);
        let standard = if contains("EHT-MCS") {
            "Wi-Fi 7 · 802.11be"
        } else if contains("HE-MCS") {
            if (5925..=7125).contains(&frequency) {
                "Wi-Fi 6E · 802.11ax"
            } else {
                "Wi-Fi 6 · 802.11ax"
            }
        } else if contains("VHT-MCS") {
            "Wi-Fi 5 · 802.11ac"
        } else if contains(" MCS ") {
            "Wi-Fi 4 · 802.11n"
        } else {
            "Unavailable"
        };

        fn speed(value: &str) -> String {
            let mut words = value.split_whitespace();
            let rate = words.next();
            if words.next() == Some("MBit/s")
                && let Some(rate) = rate
            {
                return format!("{rate} Mb/s");
            }
            "Unavailable".into()
        }

        Self {
            standard,
            signal: fields
                .get("signal")
                .map(|value| value.trim())
                .filter(|value| !value.is_empty()),
            receive: speed(rx),
            transmit: speed(tx),
        }
    }
}
