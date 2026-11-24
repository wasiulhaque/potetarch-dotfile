#!/usr/bin/env bash
set -euo pipefail

usage() {
    printf 'Usage: %s [--config-only] [--wallpaper FILE]\n' "${0##*/}"
}

config_only=false
wallpaper=
while (($#)); do
    case $1 in
        --config-only) config_only=true; shift ;;
        --wallpaper)
            if (($# < 2)) || [[ ! -f $2 || ! -r $2 ]]; then
                printf 'Provide a readable wallpaper file.\n' >&2
                exit 1
            fi
            wallpaper=$(realpath -- "$2")
            shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 1 ;;
    esac
done

if ((EUID == 0)); then
    printf 'Run this installer as your desktop user.\n' >&2
    exit 1
fi

source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
config_dir=${XDG_CONFIG_HOME:-"$HOME/.config"}
state_dir=${XDG_STATE_HOME:-"$HOME/.local/state"}

if ! $config_only; then
    if [[ ! -f /etc/arch-release ]] || ! command -v pacman >/dev/null; then
        printf 'Package installation targets Arch Linux. Use --config-only on other systems.\n' >&2
        exit 1
    fi

    repo_packages=(
        awww brightnessctl dolphin gtk4 gtk4-layer-shell hypridle hyprland hyprlock
        hyprshot kitty libnm libnotify libpulse networkmanager nm-connection-editor
        pavucontrol pipewire-pulse playerctl polkit-kde-agent power-profiles-daemon
        python python-gobject swaync ttf-jetbrains-mono-nerd waybar wireplumber zsh
    )
    aur_packages=()
    pacman -Qq vicinae >/dev/null 2>&1 || pacman -Qq vicinae-bin >/dev/null 2>&1 || aur_packages+=(vicinae-bin)
    pacman -Qq waypaper >/dev/null 2>&1 || aur_packages+=(waypaper)
    if (("${#aur_packages[@]}")) && ! command -v yay >/dev/null; then
        printf 'Install yay to fetch the AUR packages (%s), or install them manually and rerun.\n' "${aur_packages[*]}" >&2
        exit 1
    fi
    sudo pacman -Syu --needed "${repo_packages[@]}"
    if (("${#aur_packages[@]}")); then
        yay -S --needed "${aur_packages[@]}"
    fi
fi

staging=$(mktemp -d)
trap 'rm -rf -- "$staging"' EXIT
cp -R -- "$source_dir/config/." "$staging/"
python3 - "$staging/waypaper/config.ini" "$config_dir/waypaper/config.ini" "$wallpaper" <<'PY'
from configparser import ConfigParser
from pathlib import Path
import sys

template, existing = map(Path, sys.argv[1:3])
wallpaper = sys.argv[3]
if "\n" in wallpaper or "\r" in wallpaper:
    raise SystemExit("The wallpaper path must fit on one line.")

settings = ConfigParser(interpolation=None)
settings.read(template, encoding="utf-8")
if wallpaper:
    settings["Settings"]["wallpaper"] = wallpaper
elif existing.is_file():
    previous = ConfigParser(interpolation=None)
    previous.read(existing, encoding="utf-8")
    for key in ("folder", "monitors", "wallpaper", "use_xdg_state"):
        if previous.has_option("Settings", key):
            settings["Settings"][key] = previous["Settings"][key]
with template.open("w", encoding="utf-8") as output:
    settings.write(output)
PY

# Back up every file that will be replaced before writing any of the configs.
mapfile -d '' -t files < <(cd -- "$staging" && find . -type f -printf '%P\0' | sort -z)
backup_dir=
for file in "${files[@]}"; do
    target=$config_dir/$file
    if [[ -e $target || -L $target ]]; then
        if [[ -d $target ]]; then
            printf 'Expected a file at %s.\n' "$target" >&2
            exit 1
        fi
        if [[ -z $backup_dir ]]; then
            mkdir -p -- "$state_dir/potetarch"
            backup_dir=$(mktemp -d "$state_dir/potetarch/backup-XXXXXXXX")
            printf 'Backing up existing files to %s\n' "$backup_dir"
        fi
        mkdir -p -- "$backup_dir/${file%/*}"
        cp -a -- "$target" "$backup_dir/$file"
    fi
done
for file in "${files[@]}"; do
    target_dir=$config_dir/${file%/*}
    mkdir -p -- "$target_dir"
    temporary=$(mktemp "$target_dir/.potetarch-XXXXXXXX")
    if ! cp -- "$staging/$file" "$temporary" || ! mv -fT -- "$temporary" "$config_dir/$file"; then
        rm -f -- "$temporary"
        exit 1
    fi
done

printf 'Installed desktop configs under %s. Log out and back into Hyprland to load the full setup.\n' "$config_dir"
