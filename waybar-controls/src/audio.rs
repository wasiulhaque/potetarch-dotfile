use crate::{Panel, clear, command, label, row, scrollable, spawn, vertical};
use futures_util::{join, lock::Mutex};
use gtk::{glib, prelude::*};
use serde_json::Value;
use std::{
    cell::RefCell,
    collections::HashMap,
    rc::{Rc, Weak},
    time::Duration,
};

const NORMAL_VOLUME: f64 = 65536.0;

#[derive(Clone, Default)]
pub(crate) struct Service(Rc<Mutex<()>>);

type DeviceKey = (&'static str, String);

struct PendingVolume {
    timer: glib::SourceId,
    percent: f64,
    ratios: Rc<Vec<f64>>,
}

struct Audio {
    panel: Weak<Panel>,
    service: Service,
    pending: RefCell<HashMap<DeviceKey, PendingVolume>>,
}

pub(crate) fn open(panel: &Rc<Panel>, service: Service) {
    panel
        .body
        .append(&label("Loading audio devices…", "secondary"));
    let audio = Rc::new(Audio {
        panel: Rc::downgrade(panel),
        service,
        pending: RefCell::default(),
    });
    let retained = audio.clone();
    panel.when_closed(move || {
        for (key, pending) in retained.pending.take() {
            pending.timer.remove();
            retained.set_volume(key, pending.percent, pending.ratios);
        }
    });
    audio.refresh();
}

async fn audio_json(action: &str, resource: Option<&str>) -> Option<Value> {
    let mut args = vec!["pactl", "--format=json", action];
    args.extend(resource);
    serde_json::from_str(&command(&args, 2).await?).ok()
}

impl Audio {
    fn refresh(self: &Rc<Self>) {
        let weak = Rc::downgrade(self);
        let service = self.service.clone();
        spawn(async move {
            let _guard = service.0.lock().await;
            let (info, sinks, sources) = join!(
                audio_json("info", None),
                audio_json("list", Some("sinks")),
                audio_json("list", Some("sources")),
            );
            if let Some(audio) = weak.upgrade() {
                audio.render(info, sinks, sources);
            }
        });
    }

    fn render(self: &Rc<Self>, info: Option<Value>, sinks: Option<Value>, sources: Option<Value>) {
        let Some(panel) = self.panel.upgrade() else {
            return;
        };
        clear(&panel.body);
        panel.body.set_sensitive(true);

        let (Some(info), Some(sinks), Some(sources)) = (info, sinks, sources) else {
            panel.error("Audio service is unavailable.");
            return;
        };
        for (heading, kind, default_field, devices) in [
            ("Output", "sink", "default_sink_name", &sinks),
            ("Input", "source", "default_source_name", &sources),
        ] {
            panel.body.append(&label(heading, "heading"));
            let rows = vertical(6);
            let default = info[default_field].as_str();
            let mut devices: Vec<_> = devices.as_array().into_iter().flatten().collect();
            devices.sort_by_key(|device| device["name"].as_str() != default);
            let mut selected_device = None;
            for device in devices {
                let Some(name) = device["name"].as_str() else {
                    continue;
                };
                if kind == "source" && name.ends_with(".monitor") {
                    continue;
                }
                let selected = Some(name) == default;
                if selected {
                    selected_device = Some((name, device));
                } else if device["ports"].as_array().is_some_and(|ports| {
                    !ports.is_empty() && ports.iter().all(|p| p["availability"] == "not available")
                }) {
                    continue;
                }

                let title = device["properties"]["node.nick"]
                    .as_str()
                    .filter(|value| !value.is_empty())
                    .or_else(|| device["description"].as_str())
                    .unwrap_or(name);
                let weak = Rc::downgrade(self);
                let name = name.to_owned();
                row(
                    &rows,
                    title,
                    if selected { "✓" } else { "" },
                    false,
                    selected,
                    move || {
                        if let Some(audio) = weak.upgrade() {
                            audio.select(kind, name.clone());
                        }
                    },
                );
            }
            if rows.first_child().is_none() {
                rows.append(&label("No devices available", "secondary"));
            }
            panel.body.append(&scrollable(&rows, Some(110)));
            if let Some((name, device)) = selected_device {
                panel
                    .body
                    .append(&self.slider(heading, (kind, name.into()), device));
            }
        }
    }

    fn slider(self: &Rc<Self>, heading: &str, key: DeviceKey, device: &Value) -> gtk::Scale {
        let channels: Vec<_> = device["volume"]
            .as_object()
            .into_iter()
            .flat_map(|o| o.values())
            .filter_map(|channel| channel["value"].as_f64())
            .collect();
        let peak = channels
            .iter()
            .copied()
            .reduce(f64::max)
            .unwrap_or(NORMAL_VOLUME);
        let percent = (peak * 100.0 / NORMAL_VOLUME).round();
        let ratios = Rc::new(
            channels
                .iter()
                .map(|channel| if peak == 0.0 { 1.0 } else { channel / peak })
                .collect(),
        );
        let (percent, ratios) = self
            .pending
            .borrow()
            .get(&key)
            .map_or((percent, ratios), |pending| {
                (pending.percent, pending.ratios.clone())
            });
        let slider =
            gtk::Scale::with_range(gtk::Orientation::Horizontal, 0.0, percent.max(100.0), 1.0);
        slider.set_value(percent);
        slider.set_draw_value(true);
        slider.set_value_pos(gtk::PositionType::Right);
        slider.set_format_value_func(|_, value| format!("{value:.0}%"));
        slider.set_tooltip_text(Some(&format!("{heading} volume")));
        let weak = Rc::downgrade(self);
        slider.connect_value_changed(move |slider| {
            if let Some(audio) = weak.upgrade() {
                audio.queue_volume(key.clone(), slider.value().round(), ratios.clone());
            }
        });
        slider
    }

    fn queue_volume(self: &Rc<Self>, key: DeviceKey, percent: f64, ratios: Rc<Vec<f64>>) {
        if let Some(previous) = self.pending.borrow_mut().remove(&key) {
            previous.timer.remove();
        }
        let weak = Rc::downgrade(self);
        let target = key.clone();
        let timer = glib::timeout_add_local_once(Duration::from_millis(75), move || {
            if let Some(audio) = weak.upgrade() {
                let pending = audio.pending.borrow_mut().remove(&target);
                if let Some(pending) = pending {
                    audio.set_volume(target, pending.percent, pending.ratios);
                }
            }
        });
        self.pending.borrow_mut().insert(
            key,
            PendingVolume {
                timer,
                percent,
                ratios,
            },
        );
    }

    fn set_volume(&self, (kind, name): DeviceKey, percent: f64, ratios: Rc<Vec<f64>>) {
        let service = self.service.clone();
        let panel = self.panel.clone();
        spawn(async move {
            let subcommand = format!("set-{kind}-volume");
            let mut values: Vec<_> = ratios
                .iter()
                .map(|ratio| format!("{:.0}", NORMAL_VOLUME * percent / 100.0 * ratio))
                .collect();
            if values.is_empty() {
                values.push(format!("{percent:.0}%"));
            }
            let mut args = vec!["pactl", &subcommand, &name];
            args.extend(values.iter().map(String::as_str));
            let _guard = service.0.lock().await;
            if command(&args, 2).await.is_none()
                && let Some(panel) = panel.upgrade()
            {
                panel.error("Could not change the volume.");
            }
        });
    }

    fn select(self: &Rc<Self>, kind: &'static str, name: String) {
        if let Some(panel) = self.panel.upgrade() {
            panel.body.set_sensitive(false);
        }
        let weak = Rc::downgrade(self);
        let service = self.service.clone();
        spawn(async move {
            let _guard = service.0.lock().await;
            let success = command(&["pactl", &format!("set-default-{kind}"), &name], 2)
                .await
                .is_some();
            if let Some(audio) = weak.upgrade()
                && let Some(panel) = audio.panel.upgrade()
            {
                if success {
                    panel.error("");
                    audio.refresh();
                } else {
                    panel.error("Could not switch audio device.");
                    panel.body.set_sensitive(true);
                }
            }
        });
    }
}
