#!/usr/bin/env bash
# winswitch uninstaller — stops the daemons, removes installed files, and strips
# the keybind block from hyprland.conf. Leaves your config.toml unless --purge.
set -euo pipefail

share="$HOME/.local/share/winswitch"
bin="$HOME/.local/bin"
cfg_dir="$HOME/.config/winswitch"
cache="$HOME/.cache/winswitch"
hypr="${HYPR_CONF:-$HOME/.config/hypr/hyprland.conf}"
purge=0; [ "${1:-}" = "--purge" ] && purge=1

say() { printf '\033[1;36m::\033[0m %s\n' "$*"; }

say "Stopping daemons…"
pkill -f "$share/winswitch.py" 2>/dev/null || true
pkill -f "$share/winswitch-thumbd.py" 2>/dev/null || true

say "Removing files…"
rm -f "$share/winswitch.py" "$share/winswitch-thumbd.py" "$bin/winswitch-send"
rmdir "$share" 2>/dev/null || true
rm -rf "$cache"

if [ -f "$hypr" ] && grep -q 'winswitch (Windows-style Alt+Tab)' "$hypr"; then
    say "Removing keybind block from $hypr"
    sed -i '/# --- winswitch (Windows-style Alt+Tab) ---/,/# --- end winswitch ---/d' "$hypr"
fi

if [ "$purge" = 1 ]; then
    say "Purging config…"
    rm -rf "$cfg_dir"
else
    say "Kept $cfg_dir (use --purge to remove)."
fi
say "Done."
