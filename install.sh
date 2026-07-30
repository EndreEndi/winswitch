#!/usr/bin/env bash
# winswitch installer — copies the daemons + client into place, drops an example
# config, and wires the Alt+Tab keybinds into hyprland.conf (idempotent).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
share="$HOME/.local/share/winswitch"
bin="$HOME/.local/bin"
cfg_dir="$HOME/.config/winswitch"
hypr="${HYPR_CONF:-$HOME/.config/hypr/hyprland.conf}"

layer_lib="${GTK4_LAYER_SHELL:-/usr/lib/libgtk4-layer-shell.so}"

say() { printf '\033[1;36m::\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }

# --- dependency check (warn only; don't abort) --------------------------------
say "Checking dependencies…"
missing=()
for c in hyprctl grim wl-copy python3; do
    command -v "$c" >/dev/null 2>&1 || missing+=("$c")
done
python3 -c 'import gi; gi.require_version("Gtk","4.0"); gi.require_version("Gtk4LayerShell","1.0")' 2>/dev/null \
    || missing+=("python-gobject + gtk4 + gtk4-layer-shell")
[ -f "$layer_lib" ] || warn "gtk4-layer-shell lib not found at $layer_lib — set GTK4_LAYER_SHELL=/path/to/libgtk4-layer-shell.so"
if [ ${#missing[@]} -gt 0 ]; then
    warn "Missing: ${missing[*]}"
    warn "Arch: sudo pacman -S gtk4 gtk4-layer-shell python-gobject grim wl-clipboard"
fi

# --- copy files ---------------------------------------------------------------
say "Installing files…"
mkdir -p "$share" "$bin" "$cfg_dir"
install -m 0644 "$here/winswitch.py"        "$share/winswitch.py"
install -m 0644 "$here/winswitch-thumbd.py" "$share/winswitch-thumbd.py"
install -m 0755 "$here/winswitch-send"      "$bin/winswitch-send"
if [ -e "$cfg_dir/config.toml" ]; then
    say "Keeping existing $cfg_dir/config.toml"
else
    install -m 0644 "$here/config.example.toml" "$cfg_dir/config.toml"
fi

# --- wire hyprland.conf (idempotent) -----------------------------------------
if [ -f "$hypr" ] && grep -q 'winswitch (Windows-style Alt+Tab)' "$hypr"; then
    say "hyprland.conf already has the winswitch block — leaving it."
elif [ -f "$hypr" ]; then
    say "Adding winswitch keybinds to $hypr"
    cat >> "$hypr" <<EOF

# --- winswitch (Windows-style Alt+Tab) ---
exec-once = env LD_PRELOAD=$layer_lib python3 $share/winswitch.py
exec-once = python3 $share/winswitch-thumbd.py
bind = ALT, Tab, exec, $bin/winswitch-send next
bind = ALT SHIFT, Tab, exec, $bin/winswitch-send prev
# --- end winswitch ---
EOF
else
    warn "No hyprland.conf at $hypr — add these lines to your Hyprland config manually:"
    cat <<EOF
exec-once = env LD_PRELOAD=$layer_lib python3 $share/winswitch.py
exec-once = python3 $share/winswitch-thumbd.py
bind = ALT, Tab, exec, $bin/winswitch-send next
bind = ALT SHIFT, Tab, exec, $bin/winswitch-send prev
EOF
fi

say "Done. Reload Hyprland (hyprctl reload) or log out/in, then press Alt+Tab."
say "To start it right now without relogging:"
echo "    env LD_PRELOAD=$layer_lib python3 $share/winswitch.py &"
echo "    python3 $share/winswitch-thumbd.py &"
