# winswitch

A **Windows-style Alt+Tab** window switcher for **Hyprland** (Wayland). It behaves exactly like the Windows task switcher: a centered overlay of live window thumbnails, hold **Alt** and tap **Tab** to cycle, release **Alt** to commit — or just click the one you want.

Built with GTK4 + [gtk4-layer-shell](https://github.com/wmww/gtk4-layer-shell), driven by two tiny Python daemons. No compositor patches, no external switcher frameworks.

## What it does

- **True Windows behaviour** — first `Alt+Tab` opens the overlay with the *previous* window pre-selected (MRU order). Keep Alt held and tap Tab to move the selection; **releasing Alt** activates the highlighted window.
- **Live thumbnails** — a background daemon snapshots each window as you last saw it, so you get real previews of windows that aren't currently on screen (not just icons). Falls back to the app icon when no snapshot exists yet.
- **Mouse support** — hover to highlight, click to switch. Works while Alt is held.
- **Keyboard driven by a Hyprland submap** — `Alt+Tab` enters a switcher submap; while it's active `Tab` / `Shift+Tab` cycle, `Esc` cancels, and **releasing Alt commits** (detected instantly by polling the physical Alt key).
- **Configurable layout** — horizontal **row** (Win7/10 strip) or **grid**, thumbnails or icons, tunable sizes and title length.
- Lightweight: two always-resident Python processes, activated over a Unix socket by the Hyprland submap.

## How it works

| Component | Role |
|-----------|------|
| `winswitch.py` | The overlay daemon. A GTK4 layer-shell surface on the `OVERLAY` layer that takes **no keyboard grab** (display + mouse only). Listens on `$XDG_RUNTIME_DIR/winswitch.sock`. |
| `winswitch-thumbd.py` | Thumbnail daemon. Follows Hyprland's event socket; when a window becomes the active/visible one it grabs a `grim` snapshot and caches a downscaled PNG in `~/.cache/winswitch/`. |
| `winswitch-send` | Tiny client the submap runs: sends `next` / `prev` / `commit` / `cancel` to the socket. |
| Hyprland `winswitch` submap | Owns the keyboard while switching (see [Keybinds](#keybinds)). `Alt+Tab` enters it and opens the overlay; `Tab`/`Shift+Tab` cycle; a chained one-shot timer polls `hl.is_key_down("Alt_L")` and, the instant Alt is up, sends `commit` and exits the submap. |

**Why a submap instead of the overlay grabbing the keyboard?** On Hyprland's Lua config, when a layer-shell surface holds an *exclusive* keyboard grab, Hyprland continuously toggles the held `Alt` modifier to it (jittery release/press events), which makes a genuine Alt-release impossible to detect without a laggy debounce. Letting the compositor own the input via a submap — and polling the true key state with `hl.is_key_down` — is what makes release-to-commit **instant** and reliable. (A submap release-bind can't be used either: it only fires for an `Alt` pressed *after* the submap is entered, not the `Alt` you're already holding from `Alt+Tab`.)

## Requirements

- **Hyprland 0.55+ with the Lua config** (`hyprland.lua`) — the switcher uses `hl.define_submap`, `hl.is_key_down` and `hl.timer`, which don't exist in the legacy `hyprland.conf` parser.
- Uses `hyprctl` and Hyprland's event socket
- `gtk4`, `gtk4-layer-shell`, `python-gobject` (PyGObject)
- `grim` (thumbnails), `wl-clipboard`
- Arch: `sudo pacman -S gtk4 gtk4-layer-shell python-gobject grim wl-clipboard`

## Install

```bash
git clone <this-repo> winswitch && cd winswitch
./install.sh
```

The installer copies the daemons to `~/.local/share/winswitch/`, the client to `~/.local/bin/`, an example config to `~/.config/winswitch/config.toml` (only if you don't already have one), and appends the submap + autostart block to `~/.config/hypr/hyprland.lua` (only if not already present). Reload Hyprland (`hyprctl reload`) or log out/in, then press `Alt+Tab`.

Remove everything with `./uninstall.sh`.

## Configuration

`~/.config/winswitch/config.toml` — changes apply on the next `Alt+Tab`, no restart:

```toml
layout       = "row"        # "row" (Win7/10 strip) | "grid"
display      = "thumbnails" # "thumbnails" | "icons"
columns      = 5            # columns when layout = "grid"
row_max      = 3            # row layout: max tiles per row before wrapping to a grid
icon_size    = 96           # px, icon mode
thumb_width  = 260          # px, thumbnail mode
thumb_height = 160
title_len    = 28           # max chars of window title shown
title_size   = 20           # px, window-title font size
```

In **row** layout the strip stays a single row until it would exceed `row_max`
tiles (or the screen width), then wraps onto additional rows so nothing is ever
clipped, however many windows are open.

## Keybinds

The installer adds this block to `hyprland.lua`. `Alt+Tab` opens the switcher and enters the `winswitch` submap; inside it `Tab`/`Shift+Tab` cycle and `Esc` cancels; a polled timer commits the moment you release Alt:

```lua
local WS = os.getenv("HOME") .. "/.local/bin/winswitch-send"

-- autostart the daemons (single-instance locked, so re-running is safe)
hl.exec_cmd("env LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so python3 " ..
            os.getenv("HOME") .. "/.local/share/winswitch/winswitch.py")
hl.exec_cmd("python3 " .. os.getenv("HOME") .. "/.local/share/winswitch/winswitch-thumbd.py")

hl.define_submap("winswitch", function()
    hl.bind("Tab",               hl.dsp.exec_cmd(WS .. " next"))
    hl.bind("ALT + Tab",         hl.dsp.exec_cmd(WS .. " next"))
    hl.bind("SHIFT + Tab",       hl.dsp.exec_cmd(WS .. " prev"))
    hl.bind("ALT + SHIFT + Tab", hl.dsp.exec_cmd(WS .. " prev"))
    hl.bind("escape", function() hl.exec_cmd(WS .. " cancel"); hl.dispatch(hl.dsp.submap("reset")) end)
end)

-- poll the physical Alt key; commit the instant it is released
local function ws_poll()
    if hl.get_current_submap() ~= "winswitch" then return end
    if hl.is_key_down("Alt_L") or hl.is_key_down("Alt_R") then
        hl.timer(ws_poll, {timeout = 30, type = "oneshot"})
    else
        hl.exec_cmd(WS .. " commit"); hl.dispatch(hl.dsp.submap("reset"))
    end
end

local function ws_open(dir)
    hl.exec_cmd(WS .. " " .. dir)
    hl.dispatch(hl.dsp.submap("winswitch"))
    hl.timer(ws_poll, {timeout = 45, type = "oneshot"})
end

hl.bind("ALT + Tab",         function() ws_open("next") end)
hl.bind("ALT + SHIFT + Tab", function() ws_open("prev") end)
```

> **Note:** the overlay daemon **must** be launched with `LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so` — otherwise gtk4-layer-shell doesn't hook GTK early enough and the overlay becomes an ordinary focus-stealing window.

## License

MIT — see [LICENSE](LICENSE).
