#!/usr/bin/env bash
# winswitch installer — copies the daemons + client into place, drops an example
# config, and wires the Alt+Tab submap into hyprland.lua (idempotent).
# Requires Hyprland 0.55+ with the Lua config (hl.define_submap / hl.is_key_down).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
share="$HOME/.local/share/winswitch"
bin="$HOME/.local/bin"
cfg_dir="$HOME/.config/winswitch"
hypr="${HYPR_LUA:-$HOME/.config/hypr/hyprland.lua}"

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

# --- wire hyprland.lua (idempotent) ------------------------------------------
# NOTE: heredoc is unquoted so $share/$bin/$layer_lib expand; the Lua ".." and
# "{}" are literal (no shell metachars), and Lua uses WS (no leading $).
read -r -d '' lua_block <<EOF || true
-- --- winswitch (Windows-style Alt+Tab) ---
local WS = "$bin/winswitch-send"
hl.exec_cmd("env LD_PRELOAD=$layer_lib python3 $share/winswitch.py")
hl.exec_cmd("python3 $share/winswitch-thumbd.py")
hl.define_submap("winswitch", function()
    hl.bind("Tab",               hl.dsp.exec_cmd(WS .. " next"))
    hl.bind("ALT + Tab",         hl.dsp.exec_cmd(WS .. " next"))
    hl.bind("SHIFT + Tab",       hl.dsp.exec_cmd(WS .. " prev"))
    hl.bind("ALT + SHIFT + Tab", hl.dsp.exec_cmd(WS .. " prev"))
    hl.bind("escape", function() hl.exec_cmd(WS .. " cancel"); hl.dispatch(hl.dsp.submap("reset")) end)
end)
local function ws_winswitch_poll()
    if hl.get_current_submap() ~= "winswitch" then return end
    if hl.is_key_down("Alt_L") or hl.is_key_down("Alt_R") then
        hl.timer(ws_winswitch_poll, {timeout = 30, type = "oneshot"})
    else
        hl.exec_cmd(WS .. " commit"); hl.dispatch(hl.dsp.submap("reset"))
    end
end
local function ws_winswitch_open(dir)
    hl.exec_cmd(WS .. " " .. dir)
    hl.dispatch(hl.dsp.submap("winswitch"))
    hl.timer(ws_winswitch_poll, {timeout = 45, type = "oneshot"})
end
hl.bind("ALT + Tab",         function() ws_winswitch_open("next") end)
hl.bind("ALT + SHIFT + Tab", function() ws_winswitch_open("prev") end)
-- --- end winswitch ---
EOF

if [ -f "$hypr" ] && grep -q 'winswitch (Windows-style Alt+Tab)' "$hypr"; then
    say "hyprland.lua already has the winswitch block — leaving it."
elif [ -f "$hypr" ]; then
    say "Adding winswitch submap to $hypr"
    printf '\n%s\n' "$lua_block" >> "$hypr"
else
    warn "No hyprland.lua at $hypr (this needs Hyprland's Lua config, 0.55+)."
    warn "Add this block to your hyprland.lua manually:"
    printf '%s\n' "$lua_block"
fi

say "Done. Reload Hyprland (hyprctl reload) or log out/in, then press Alt+Tab."
say "To start it right now without relogging:"
echo "    env LD_PRELOAD=$layer_lib python3 $share/winswitch.py &"
echo "    python3 $share/winswitch-thumbd.py &"
