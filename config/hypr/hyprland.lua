-- =============================================================================
-- HYPRLAND | Vanilla Arch + Waybar + Vicinae + SwayNC
-- =============================================================================
-- Derived from Hyprland defaults and the local Waybar starter kit.
--
-- Edit the QUICK SETTINGS below for common changes. The rest is arranged by
-- feature, with each keyboard shortcut defined only once.
--
-- Hyprland starts SwayNC through its systemd user service.
--
-- After editing: hyprctl reload
-- To inspect errors: hyprctl configerrors
-- Docs: https://wiki.hypr.land/configuring/
-- =============================================================================


-- =============================================================================
-- 01. QUICK SETTINGS                  <--- Most frequent changes go here
-- =============================================================================

local mod = "SUPER"

-- Applications
local terminal     = "kitty"
local file_manager = "dolphin"
local launcher     = "vicinae toggle"
local notification = "swaync-client -t"
local lock_screen  = "hyprlock"

-- Display and appearance
local display_scale = 1                  -- 1 = 100%, 1.25 = 125%
local gaps_inner    = 2
local gaps_outer    = 5
local border_width  = 2
local corner_radius = 10

-- Media keys
local brightness_step = "5%"
local volume_step     = "5%"


-- =============================================================================
-- 02. MONITORS
-- =============================================================================
-- Applies to monitors without a more specific rule.
-- We can add TV/mirroring profiles later without changing your keybindings.

hl.monitor({
    output   = "",
    mode     = "preferred",
    position = "auto",
    scale    = display_scale,
})


-- =============================================================================
-- 03. ENVIRONMENT & INPUT
-- =============================================================================

hl.env("XCURSOR_SIZE", "24")
hl.env("HYPRCURSOR_SIZE", "24")

hl.config({
    input = {
        kb_layout    = "us",
        follow_mouse = 1,
        sensitivity  = 0,
        touchpad = {
            natural_scroll = true,          -- Two-finger natural scrolling
        },
    },
})

-- Three-finger swipe to change workspaces.
hl.gesture({
    fingers   = 3,
    direction = "horizontal",
    action    = "workspace",
})


-- =============================================================================
-- 04. WINDOW APPEARANCE & LAYOUT
-- =============================================================================

hl.config({
    general = {
        gaps_in     = gaps_inner,
        gaps_out    = gaps_outer,
        border_size = border_width,
        col = {
            active_border = "rgba(b8b2e9ff)",
            inactive_border = "rgba(595959aa)",
        },
        resize_on_border = false,
        allow_tearing    = false,
        layout           = "dwindle",
    },

    decoration = {
        rounding         = corner_radius,
        rounding_power   = 2,
        active_opacity   = 1.0,
        inactive_opacity = 1.0,
        shadow = {
            enabled      = true,
            range        = 4,
            render_power = 3,
            color        = 0xee1a1a1a,
        },
        blur = {
            enabled  = true,
            size     = 3,
            passes   = 1,
            vibrancy = 0.1696,
        },
    },

    animations = {
        enabled = true,
    },

    dwindle = {
        preserve_split = true,
    },

    master = {
        new_status = "master",
    },

    scrolling = {
        fullscreen_on_one_column = true,
    },

    misc = {
        force_default_wallpaper = -1,
        disable_hyprland_logo   = false,
    },

    layers = {
        allow_new_top_layers_over_existing_fullscreen = true,
    },
})


-- =============================================================================
-- 05. ANIMATIONS
-- =============================================================================
-- Original animation curves and timings, kept to preserve the current look.

hl.curve("easeOutQuint",   { type = "bezier", points = { {0.23, 1},    {0.32, 1}    } })
hl.curve("linear",         { type = "bezier", points = { {0, 0},       {1, 1}       } })
hl.curve("almostLinear",   { type = "bezier", points = { {0.5, 0.5},   {0.75, 1}    } })
hl.curve("quick",          { type = "bezier", points = { {0.15, 0},    {0.1, 1}     } })
hl.curve("easy",           { type = "spring", mass = 1, stiffness = 238.1191, damping = 24.21279333 })

hl.animation({ leaf = "global",        enabled = true, speed = 10,   bezier = "default" })
hl.animation({ leaf = "border",        enabled = true, speed = 5.39, bezier = "easeOutQuint" })
hl.animation({ leaf = "windows",       enabled = true, speed = 4.79, spring = "easy" })
hl.animation({ leaf = "windowsIn",     enabled = true, speed = 4.1,  spring = "easy",         style = "popin 87%" })
hl.animation({ leaf = "windowsOut",    enabled = true, speed = 1.49, bezier = "linear",       style = "popin 87%" })
hl.animation({ leaf = "fadeIn",        enabled = true, speed = 1.73, bezier = "almostLinear" })
hl.animation({ leaf = "fadeOut",       enabled = true, speed = 1.46, bezier = "almostLinear" })
hl.animation({ leaf = "fade",          enabled = true, speed = 3.03, bezier = "quick" })
hl.animation({ leaf = "layers",        enabled = true, speed = 3.81, bezier = "easeOutQuint" })
hl.animation({ leaf = "layersIn",      enabled = true, speed = 4,    bezier = "easeOutQuint", style = "fade" })
hl.animation({ leaf = "layersOut",     enabled = true, speed = 1.5,  bezier = "linear",       style = "fade" })
hl.animation({ leaf = "fadeLayersIn",  enabled = true, speed = 1.79, bezier = "almostLinear" })
hl.animation({ leaf = "fadeLayersOut", enabled = true, speed = 1.39, bezier = "almostLinear" })
-- Workspace animations: horizontal slide
hl.animation({ leaf = "workspaces",    enabled = true, speed = 3.5, bezier = "easeOutQuint", style = "slide" })
hl.animation({ leaf = "workspacesIn",  enabled = true, speed = 3.5, bezier = "easeOutQuint", style = "slide" })
hl.animation({ leaf = "workspacesOut", enabled = true, speed = 3.5, bezier = "easeOutQuint", style = "slide" })
hl.animation({ leaf = "zoomFactor",    enabled = true, speed = 7,    bezier = "quick" })


-- =============================================================================
-- 06. AUTOSTART
-- =============================================================================
-- Keep one startup method per program. The notification daemon runs as a user service.
-- If vicinae.service is enabled, comment out "vicinae server" here as well.

hl.on("hyprland.start", function()
    hl.exec_cmd("systemctl --user import-environment WAYLAND_DISPLAY XDG_CURRENT_DESKTOP && systemctl --user start swaync.service plasma-polkit-agent.service")
    hl.exec_cmd("waybar")
    hl.exec_cmd([[python3 "${XDG_CONFIG_HOME:-$HOME/.config}/hypr/osd.py"]])
    hl.exec_cmd("vicinae server")
    hl.exec_cmd("hypridle")
    hl.exec_cmd("awww-daemon")
    hl.exec_cmd("sh -c 'sleep 1 && waypaper --restore'")
end)


-- =============================================================================
-- 07. KEYBINDINGS
-- =============================================================================
-- Common syntax: hl.bind("SUPER + KEY", hl.dsp.some_action(...))

-- ==========================================
-- SCREENSHOTS
-- ==========================================

-- Screenshot selected region
hl.bind("Print",
    hl.dsp.exec_cmd("hyprshot -m region"))

-- Screenshot current monitor
hl.bind("SHIFT + Print",
    hl.dsp.exec_cmd("hyprshot -m output"))

-- Screenshot selected window
hl.bind("CTRL + Print",
    hl.dsp.exec_cmd("hyprshot -m window"))

-- 7A. Applications ------------------------------------------------------------

hl.bind(mod .. " + Q",      hl.dsp.window.close())             -- Close focused window
hl.bind(mod .. " + Return", hl.dsp.exec_cmd(terminal))        -- Open Kitty
hl.bind(mod .. " + Space",  hl.dsp.exec_cmd(launcher))        -- Vicinae launcher
hl.bind(mod .. " + R",      hl.dsp.exec_cmd(launcher))        -- Alternate launcher key
hl.bind(mod .. " + E",      hl.dsp.exec_cmd(file_manager))    -- File manager
hl.bind(mod .. " + N",      hl.dsp.exec_cmd(notification))    -- SwayNC notification center
hl.bind(mod .. " + L",      hl.dsp.exec_cmd(lock_screen))     -- Lock screen

-- Preserve original logout behavior.
hl.bind(mod .. " + M", hl.dsp.exec_cmd(
    "command -v hyprshutdown >/dev/null 2>&1 && hyprshutdown || hyprctl dispatch 'hl.dsp.exit()'"
))

-- 7B. Workspace navigation ----------------------------------------------------

hl.bind(mod .. " + left",  hl.dsp.focus({ workspace = "-1" }))
hl.bind(mod .. " + right", hl.dsp.focus({ workspace = "+1" }))

-- Jump to the next empty workspace on this monitor (moves NO windows).
hl.bind(mod .. " + SHIFT + down", hl.dsp.focus({ workspace = "emptynm" }))

-- Move the focused window to an adjacent workspace AND follow it.
hl.bind(mod .. " + SHIFT + left",  hl.dsp.window.move({ workspace = "-1", follow = true }))
hl.bind(mod .. " + SHIFT + right", hl.dsp.window.move({ workspace = "+1", follow = true }))

-- Go directly to workspace 1..10 with Super + 1..0.
-- Move focused window to workspace 1..10 with Super + Shift + 1..0.
for i = 1, 10 do
    local key = i % 10
    hl.bind(mod .. " + " .. key,         hl.dsp.focus({ workspace = i }))
    hl.bind(mod .. " + SHIFT + " .. key, hl.dsp.window.move({ workspace = i }))
end

-- Super + mouse wheel: cycle through existing workspaces.
hl.bind(mod .. " + mouse_down", hl.dsp.focus({ workspace = "e+1" }))
hl.bind(mod .. " + mouse_up",   hl.dsp.focus({ workspace = "e-1" }))

-- 7C. Window controls ---------------------------------------------------------

-- Super + Up/Down: move keyboard focus between windows.
-- Left/Right are intentionally reserved for workspace switching above.
hl.bind(mod .. " + up",   hl.dsp.focus({ direction = "up" }))
hl.bind(mod .. " + down", hl.dsp.focus({ direction = "down" }))

hl.bind(mod .. " + V", hl.dsp.window.float({ action = "toggle" }))
hl.bind(mod .. " + P", hl.dsp.window.pseudo())
hl.bind(mod .. " + J", hl.dsp.layout("togglesplit"))

-- Scratchpad / special workspace.
hl.bind(mod .. " + S",         hl.dsp.workspace.toggle_special("magic"))
hl.bind(mod .. " + SHIFT + S", hl.dsp.window.move({ workspace = "special:magic" }))

-- Super + left-click drag to move; right-click drag to resize.
hl.bind(mod .. " + mouse:272", hl.dsp.window.drag(),   { mouse = true })
hl.bind(mod .. " + mouse:273", hl.dsp.window.resize(), { mouse = true })

-- 7D. Brightness & audio ------------------------------------------------------

hl.bind("XF86MonBrightnessUp",   hl.dsp.exec_cmd("brightnessctl -e4 -n2 set " .. brightness_step .. "+"), { locked = true, repeating = true })
hl.bind("XF86MonBrightnessDown", hl.dsp.exec_cmd("brightnessctl -e4 -n2 set " .. brightness_step .. "-"), { locked = true, repeating = true })

hl.bind("XF86AudioRaiseVolume", hl.dsp.exec_cmd("wpctl set-mute @DEFAULT_AUDIO_SINK@ 0 && wpctl set-volume -l 1 @DEFAULT_AUDIO_SINK@ " .. volume_step .. "+"), { locked = true, repeating = true })
hl.bind("XF86AudioLowerVolume", hl.dsp.exec_cmd("wpctl set-volume @DEFAULT_AUDIO_SINK@ " .. volume_step .. "-"),      { locked = true, repeating = true })
hl.bind("XF86AudioMute",        hl.dsp.exec_cmd("wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle"),                    { locked = true, repeating = true })
hl.bind("XF86AudioMicMute",     hl.dsp.exec_cmd("wpctl set-mute @DEFAULT_AUDIO_SOURCE@ toggle"),                  { locked = true, repeating = true })

-- Media playback keys require playerctl.
hl.bind("XF86AudioNext",  hl.dsp.exec_cmd("playerctl next"),       { locked = true })
hl.bind("XF86AudioPrev",  hl.dsp.exec_cmd("playerctl previous"),   { locked = true })
hl.bind("XF86AudioPlay",  hl.dsp.exec_cmd("playerctl play-pause"), { locked = true })
hl.bind("XF86AudioPause", hl.dsp.exec_cmd("playerctl play-pause"), { locked = true })


-- =============================================================================
-- 08. WINDOW & LAYER RULES
-- =============================================================================

-- Vicinae: blurred launcher background, no entry/exit animation.
hl.layer_rule({
    name         = "vicinae-appearance",
    match        = { namespace = "vicinae" },
    blur         = true,
    ignore_alpha = 0,
    no_anim      = true,
})

-- Preserve original maximize-request suppression.
hl.window_rule({
    name           = "suppress-maximize-events",
    match          = { class = ".*" },
    suppress_event = "maximize",
})

-- Preserve original XWayland drag workaround.
hl.window_rule({
    name = "fix-xwayland-drags",
    match = {
        class      = "^$",
        title      = "^$",
        xwayland   = true,
        float      = true,
        fullscreen = false,
        pin        = false,
    },
    no_focus = true,
})

-- Keep the existing positioning rule for the Hyprland-run window.
hl.window_rule({
    name  = "move-hyprland-run",
    match = { class = "hyprland-run" },
    move  = "20 monitor_h-120",
    float = true,
})

-- =============================================================================
-- END
-- =============================================================================

-- HyprMod managed settings
require("hyprland-gui")
