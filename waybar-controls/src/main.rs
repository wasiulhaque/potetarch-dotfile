mod audio;
mod bluetooth;
mod wifi;

use gtk::{gdk, gio, glib, prelude::*};
use layer::LayerShell;
use serde_json::Value;
use std::{
    cell::{Cell, RefCell},
    env,
    io::{Read, Write},
    os::unix::net::UnixStream,
    path::PathBuf,
    rc::Rc,
    time::Duration,
};

const WIDTH: i32 = 345;
const GAP: i32 = 5;

#[derive(Clone, Copy, PartialEq, Eq)]
enum Kind {
    Wifi,
    Audio,
    Bluetooth,
}

impl Kind {
    fn parse(value: &str) -> Option<Self> {
        match value {
            "wifi" => Some(Self::Wifi),
            "audio" => Some(Self::Audio),
            "bluetooth" => Some(Self::Bluetooth),
            _ => None,
        }
    }

    fn title(self) -> &'static str {
        match self {
            Self::Wifi => "Wi-Fi",
            Self::Audio => "Sound",
            Self::Bluetooth => "Bluetooth",
        }
    }

    fn height(self) -> Option<i32> {
        match self {
            Self::Wifi => Some(460),
            Self::Audio => None,
            Self::Bluetooth => Some(360),
        }
    }
}

struct Panel {
    kind: Kind,
    output: String,
    window: gtk::ApplicationWindow,
    body: gtk::Box,
    message: gtk::Label,
    closed: Cell<bool>,
    cleanup: RefCell<Vec<Box<dyn FnOnce()>>>,
}

type ActivePanel = Rc<RefCell<Option<Rc<Panel>>>>;

impl Panel {
    fn new(app: &gtk::Application, kind: Kind, output: &str, position: &str) -> Option<Rc<Self>> {
        let cursor: Value = if position.is_empty() {
            hypr("cursorpos").unwrap_or_default()
        } else {
            serde_json::from_str(position).unwrap_or_default()
        };
        let x = cursor["x"].as_f64();
        let y = cursor["y"].as_f64();
        let display = gdk::Display::default()?;
        let monitors: Vec<_> = display
            .monitors()
            .iter::<gdk::Monitor>()
            .filter_map(Result::ok)
            .collect();
        let monitor = monitors
            .iter()
            .find(|m| m.connector().as_deref() == Some(output))
            .or_else(|| {
                monitors.iter().find(|m| {
                    let g = m.geometry();
                    x.zip(y).is_some_and(|(x, y)| {
                        x >= f64::from(g.x())
                            && x < f64::from(g.x() + g.width())
                            && y >= f64::from(g.y())
                            && y < f64::from(g.y() + g.height())
                    })
                })
            })
            .or_else(|| monitors.first())?;
        let g = monitor.geometry();
        let left = (x.unwrap_or(f64::from(g.x() + g.width())) - f64::from(g.x() + WIDTH / 2))
            .round() as i32;
        let left = left.clamp(GAP, (g.width() - WIDTH - GAP).max(GAP));
        let top = (bar_bottom(monitor, y) - g.y()).max(0) + GAP;

        let window = gtk::ApplicationWindow::builder().application(app).build();
        window.init_layer_shell();
        window.set_namespace(Some("waybar-controls"));
        window.set_layer(layer::Layer::Overlay);
        window.set_keyboard_mode(layer::KeyboardMode::OnDemand);
        window.set_exclusive_zone(-1);
        window.set_monitor(Some(monitor));
        window.set_respect_close(true);
        for edge in [
            layer::Edge::Top,
            layer::Edge::Right,
            layer::Edge::Bottom,
            layer::Edge::Left,
        ] {
            window.set_anchor(edge, true);
        }

        let overlay = gtk::Overlay::new();
        let backdrop = vertical(0);
        let dismiss = gtk::GestureClick::builder().button(0).build();
        let weak = window.downgrade();
        dismiss.connect_released(move |_, _, _, _| {
            if let Some(window) = weak.upgrade() {
                window.close();
            }
        });
        backdrop.add_controller(dismiss);
        overlay.set_child(Some(&backdrop));
        window.set_child(Some(&overlay));

        let keys = gtk::EventControllerKey::new();
        let weak = window.downgrade();
        keys.connect_key_pressed(move |_, key, _, _| {
            if key == gdk::Key::Escape {
                if let Some(window) = weak.upgrade() {
                    window.close();
                }
                return glib::Propagation::Stop;
            }
            glib::Propagation::Proceed
        });
        window.add_controller(keys);

        let card = vertical(8);
        card.add_css_class("card");
        card.set_size_request(WIDTH, -1);
        card.set_margin_start(left);
        card.set_margin_top(top);
        if let Some(height) = kind.height() {
            card.set_margin_end((g.width() - left - WIDTH).max(0));
            card.set_margin_bottom((g.height() - top - height).max(0));
        } else {
            card.set_halign(gtk::Align::Start);
            card.set_valign(gtk::Align::Start);
        }
        overlay.add_overlay(&card);

        let header = horizontal(8);
        let title = label(kind.title(), "title");
        title.set_hexpand(true);
        header.append(&title);
        let weak = window.downgrade();
        let close = button("×", "flat", move || {
            if let Some(window) = weak.upgrade() {
                window.close();
            }
        });
        close.set_tooltip_text(Some("Close"));
        header.append(&close);
        card.append(&header);

        let body = vertical(8);
        card.append(&body);
        let message = label("", "error");
        message.set_visible(false);
        card.append(&message);

        let panel = Rc::new(Self {
            kind,
            output: output.into(),
            window,
            body,
            message,
            closed: Cell::new(false),
            cleanup: RefCell::default(),
        });
        let weak = Rc::downgrade(&panel);
        panel.window.connect_close_request(move |_| {
            if let Some(panel) = weak.upgrade() {
                panel.closed.set(true);
                for callback in panel.cleanup.take() {
                    callback();
                }
            }
            glib::Propagation::Proceed
        });

        let settings = match kind {
            Kind::Wifi => Some(("Network settings", "nm-connection-editor")),
            Kind::Audio => Some(("Audio settings", "pavucontrol")),
            Kind::Bluetooth => None,
        };
        if let Some((title, program)) = settings {
            let weak = Rc::downgrade(&panel);
            card.append(&button(title, "settings", move || {
                if let Some(panel) = weak.upgrade() {
                    panel.open_settings(&[program]);
                }
            }));
        }
        Some(panel)
    }

    fn when_closed(&self, callback: impl FnOnce() + 'static) {
        self.cleanup.borrow_mut().push(Box::new(callback));
    }

    fn error(&self, message: &str) {
        if !self.closed.get() {
            self.message.set_text(message);
            self.message.set_visible(!message.is_empty());
        }
    }

    fn open_settings(&self, args: &[&str]) {
        match launch(args) {
            Ok(()) => self.window.close(),
            Err(error) => self.error(&error.to_string()),
        }
    }
}

fn launch(args: &[&str]) -> Result<(), glib::Error> {
    let args: Vec<_> = args.iter().map(std::ffi::OsStr::new).collect();
    gio::Subprocess::newv(&args, gio::SubprocessFlags::NONE)?;
    Ok(())
}

fn spawn(task: impl Future<Output = ()> + 'static) {
    glib::MainContext::default().spawn_local(task);
}

async fn command_output(args: &[&str], seconds: u64) -> Option<(bool, String)> {
    let args: Vec<_> = args.iter().map(std::ffi::OsStr::new).collect();
    let process = gio::Subprocess::newv(
        &args,
        gio::SubprocessFlags::STDOUT_PIPE | gio::SubprocessFlags::STDERR_SILENCE,
    )
    .ok()?;
    let timer = Rc::new(RefCell::new(None));
    let timeout = timer.clone();
    let child = process.clone();
    timer.replace(Some(glib::timeout_add_local_once(
        Duration::from_secs(seconds),
        move || {
            timeout.take();
            child.force_exit();
        },
    )));
    let result = process.communicate_utf8_future(None).await;
    if let Some(timer) = timer.take() {
        timer.remove();
    }
    let (stdout, _) = result.ok()?;
    Some((process.is_successful(), stdout.unwrap_or_default().into()))
}

async fn command(args: &[&str], seconds: u64) -> Option<String> {
    let (success, stdout) = command_output(args, seconds).await?;
    success.then_some(stdout)
}

fn hypr(request: &str) -> Option<Value> {
    let path = PathBuf::from(env::var_os("XDG_RUNTIME_DIR")?)
        .join("hypr")
        .join(env::var_os("HYPRLAND_INSTANCE_SIGNATURE")?)
        .join(".socket.sock");
    let mut socket = UnixStream::connect(path).ok()?;
    let timeout = Some(Duration::from_millis(150));
    socket.set_read_timeout(timeout).ok()?;
    socket.set_write_timeout(timeout).ok()?;
    write!(socket, "j/{request}").ok()?;
    let mut data = Vec::new();
    socket.read_to_end(&mut data).ok()?;
    serde_json::from_slice(&data).ok()
}

fn bar_bottom(monitor: &gdk::Monitor, y: Option<f64>) -> i32 {
    let fallback = || y.map_or(monitor.geometry().y() + 30, |y| y.round() as i32) + 12;
    let Some(layers) = hypr("layers") else {
        return fallback();
    };
    let Some(connector) = monitor.connector() else {
        return fallback();
    };
    layers[connector.as_str()]["levels"]
        .as_object()
        .into_iter()
        .flat_map(|levels| levels.values().filter_map(Value::as_array).flatten())
        .find(|surface| surface["namespace"] == "waybar")
        .and_then(|surface| Some((surface["y"].as_i64()? + surface["h"].as_i64()?) as i32))
        .unwrap_or_else(fallback)
}

fn vertical(spacing: i32) -> gtk::Box {
    gtk::Box::new(gtk::Orientation::Vertical, spacing)
}
fn horizontal(spacing: i32) -> gtk::Box {
    gtk::Box::new(gtk::Orientation::Horizontal, spacing)
}

fn label(text: &str, class: &str) -> gtk::Label {
    let label = gtk::Label::builder()
        .label(text)
        .xalign(0.0)
        .wrap(true)
        .max_width_chars(32)
        .build();
    if !class.is_empty() {
        label.add_css_class(class);
    }
    label
}

fn button(text: &str, class: &str, action: impl Fn() + 'static) -> gtk::Button {
    let button = gtk::Button::with_label(text);
    if !class.is_empty() {
        button.add_css_class(class);
    }
    button.connect_clicked(move |_| action());
    button
}

fn clear(container: &gtk::Box) {
    while let Some(child) = container.first_child() {
        container.remove(&child);
    }
}

fn scrollable(child: &impl IsA<gtk::Widget>, height: Option<i32>) -> gtk::ScrolledWindow {
    let view = gtk::ScrolledWindow::builder()
        .hscrollbar_policy(gtk::PolicyType::Never)
        .child(child)
        .build();
    if let Some(height) = height {
        view.set_max_content_height(height);
        view.set_propagate_natural_height(true);
    } else {
        view.set_vexpand(true);
    }
    view
}

fn row(
    container: &gtk::Box,
    title: &str,
    detail: &str,
    wrap: bool,
    selected: bool,
    action: impl Fn() + 'static,
) -> gtk::Button {
    let contents = horizontal(8);
    let name = label(title, "");
    if wrap {
        name.set_wrap_mode(gtk::pango::WrapMode::WordChar);
    }
    name.set_hexpand(true);
    contents.append(&name);
    if !detail.is_empty() {
        contents.append(&label(detail, "secondary"));
    }
    let control = button("", if selected { "selected" } else { "" }, action);
    control.set_child(Some(&contents));
    container.append(&control);
    control
}

fn show_popup(
    app: &gtk::Application,
    active: &ActivePanel,
    audio: &audio::Service,
    kind: &str,
    output: &str,
    position: &str,
) -> bool {
    let Some(kind) = Kind::parse(kind) else {
        return false;
    };
    let previous = active.take();
    if let Some(previous) = previous {
        previous.window.close();
        if previous.kind == kind && previous.output == output {
            return true;
        }
    }
    let Some(panel) = Panel::new(app, kind, output, position) else {
        return false;
    };
    let weak = Rc::downgrade(active);
    panel.when_closed(move || {
        if let Some(active) = weak.upgrade() {
            active.take();
        }
    });
    active.replace(Some(panel.clone()));
    match kind {
        Kind::Wifi => wifi::open(&panel),
        Kind::Audio => audio::open(&panel, audio.clone()),
        Kind::Bluetooth => bluetooth::open(&panel),
    }
    panel.window.present();
    true
}

fn run() -> glib::ExitCode {
    let id = env::var("WAYBAR_CONTROLS_APP_ID").unwrap_or_else(|_| "local.waybar.Controls".into());
    let app = gtk::Application::builder()
        .application_id(id)
        .flags(gio::ApplicationFlags::HANDLES_COMMAND_LINE)
        .build();
    let active: ActivePanel = Default::default();
    let audio = audio::Service::default();
    let startup_active = active.clone();
    let startup_audio = audio.clone();
    app.connect_startup(move |app| {
        let css = gtk::CssProvider::new();
        css.load_from_data(include_str!("theme.css"));
        if let Some(display) = gdk::Display::default() {
            gtk::style_context_add_provider_for_display(
                &display,
                &css,
                gtk::STYLE_PROVIDER_PRIORITY_APPLICATION,
            );
        }
        let action = gio::SimpleAction::new(
            "popup",
            Some(<(String, String, String)>::static_variant_type().as_ref()),
        );
        let app_weak = app.downgrade();
        let active = startup_active.clone();
        let audio = startup_audio.clone();
        action.connect_activate(move |_, args| {
            if let (Some(app), Some((kind, output, position))) = (
                app_weak.upgrade(),
                args.and_then(glib::Variant::get::<(String, String, String)>),
            ) {
                show_popup(&app, &active, &audio, &kind, &output, &position);
            }
        });
        app.add_action(&action);
        // The command handler can open zero windows when started at login.
        let hold = app.hold();
        app.connect_shutdown(move |_| {
            let _ = &hold;
        });
    });
    app.connect_command_line(move |app, invocation| {
        let args = invocation.arguments();
        let arg = |index| {
            args.get(index)
                .and_then(|value: &std::ffi::OsString| value.to_str())
                .unwrap_or("")
        };
        if arg(1) == "--daemon"
            || (args.len() >= 2
                && args.len() <= 4
                && show_popup(app, &active, &audio, arg(1), arg(2), arg(3)))
        {
            glib::ExitCode::SUCCESS
        } else {
            glib::ExitCode::FAILURE
        }
    });
    app.run()
}

fn main() -> glib::ExitCode {
    run()
}
