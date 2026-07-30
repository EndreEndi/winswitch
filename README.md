# winswitch

A **Windows-style Alt+Tab** window switcher for **Hyprland** (Wayland). It behaves exactly like the Windows task switcher: a centered overlay of live window thumbnails, hold **Alt** and tap **Tab** to cycle, release **Alt** to commit — or just click the one you want.

Built with GTK4 + [gtk4-layer-shell](https://github.com/wmww/gtk4-layer-shell), driven by two tiny Python daemons. No compositor patches, no external switcher frameworks.

## What it does

- **True Windows behaviour** — first `Alt+Tab` opens the overlay with the *previous* window pre-selected (MRU order). Keep Alt held and tap Tab to move the selection; **releasing Alt** activates the highlighted window.
- **Live thumbnails** — a background daemon snapshots each window as you last saw it, so you get real previews of windows that aren't currently on screen (not just icons). Falls back to the app icon when no snapshot exists yet.
- **Mouse support** — hover to highlight, click to switch. Works while Alt is held.
- **Fully keyboard driven** — `Tab` / `Shift+Tab` and arrow keys cycle, `Enter`/`Space` commit, `Esc` cancels.
- **Configurable layout** — horizontal **row** (Win7/10 strip) or **grid**, thumbnails or icons, tunable sizes and title length.
- **`Print` inside the switcher** captures the overlay to clipboard + `~/Pictures/Screenshots` (handy for sharing your layout).
- Lightweight: two always-resident Python processes, activated over a Unix socket by your normal `Alt+Tab` keybind.

## How it works

| Component | Role |
|-----------|------|
| `winswitch.py` | The overlay daemon. A GTK4 layer-shell surface on the `OVERLAY` layer with exclusive keyboard focus. Listens on `$XDG_RUNTIME_DIR/winswitch.sock`. |
| `winswitch-thumbd.py` | Thumbnail daemon. Follows Hyprland's event socket; when a window becomes the active/visible one it grabs a `grim` snapshot and caches a downscaled PNG in `~/.cache/winswitch/`. |
| `winswitch-send` | Tiny client the keybind runs: sends `next` / `prev` / `cancel` to the socket. |

Because Hyprland re-fires the held `Alt+Tab` bind while the overlay owns the keyboard, cycling is driven through the socket (debounced), which is what makes the hold-and-tap feel identical to Windows.

## Requirements

- **Hyprland** (uses `hyprctl` and its event socket)
- `gtk4`, `gtk4-layer-shell`, `python-gobject` (PyGObject)
- `grim` (thumbnails + overlay screenshot), `wl-clipboard`
- Arch: `sudo pacman -S gtk4 gtk4-layer-shell python-gobject grim wl-clipboard`

## Install

```bash
git clone <this-repo> winswitch && cd winswitch
./install.sh
```

The installer copies the daemons to `~/.local/share/winswitch/`, the client to `~/.local/bin/`, an example config to `~/.config/winswitch/config.toml` (only if you don't already have one), and appends a keybind block to `~/.config/hypr/hyprland.conf` (only if not already present). Reload Hyprland (or log out/in) and press `Alt+Tab`.

Remove everything with `./uninstall.sh`.

## Configuration

`~/.config/winswitch/config.toml` — changes apply on the next `Alt+Tab`, no restart:

```toml
layout       = "row"        # "row" (Win7/10 strip) | "grid"
display      = "thumbnails" # "thumbnails" | "icons"
columns      = 5            # columns when layout = "grid"
icon_size    = 96           # px, icon mode
thumb_width  = 260          # px, thumbnail mode
thumb_height = 160
title_len    = 28           # max chars of window title shown
title_size   = 20           # px, window-title font size
```

## Keybinds

The installer adds these to `hyprland.conf`:

```ini
exec-once = env LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so python3 ~/.local/share/winswitch/winswitch.py
exec-once = python3 ~/.local/share/winswitch/winswitch-thumbd.py
bind = ALT, Tab, exec, ~/.local/bin/winswitch-send next
bind = ALT SHIFT, Tab, exec, ~/.local/bin/winswitch-send prev
```

> **Note:** the overlay daemon **must** be launched with `LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so` — otherwise gtk4-layer-shell doesn't hook GTK early enough and the overlay becomes an ordinary focus-stealing window.

## License

MIT — see [LICENSE](LICENSE).
