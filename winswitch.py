#!/usr/bin/env python3
"""winswitch — Windows-style Alt+Tab for Hyprland (GTK4 layer-shell).

Runs as a daemon. The Hyprland bind sends 'next'/'prev' over a Unix socket.
First press opens a centered overlay (MRU order, previous window pre-selected);
holding Alt + tapping Tab cycles; releasing Alt commits; mouse click commits;
Esc cancels. Layout (row/grid) and display (icons/thumbnails) are configurable.
"""
import os, sys, json, socket, subprocess, threading, time
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gtk, Gdk, GLib, Gio, Gtk4LayerShell as LS

try:
    import tomllib
except Exception:
    tomllib = None

CONFIG = os.path.expanduser("~/.config/winswitch/config.toml")
CACHE = os.path.expanduser("~/.cache/winswitch")
SOCK = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "winswitch.sock")
DEFAULTS = {
    "layout": "row",          # row | grid
    "display": "icons",       # icons | thumbnails (thumbnails fall back to icons if none cached)
    "columns": 5,             # grid columns
    "thumb_width": 260, "thumb_height": 160,
    "icon_size": 96,
    "title_len": 28,
    "title_size": 15,        # px, window-title font size
}


def load_config():
    c = dict(DEFAULTS)
    if tomllib and os.path.exists(CONFIG):
        try:
            with open(CONFIG, "rb") as f:
                c.update(tomllib.load(f))
        except Exception:
            pass
    return c


def hypr(args):
    try:
        return subprocess.check_output(["hyprctl", *args], text=True)
    except Exception:
        return ""


def list_windows():
    try:
        cl = json.loads(hypr(["clients", "-j"]) or "[]")
    except Exception:
        cl = []
    wins = [w for w in cl if w.get("mapped") and (w.get("title") or "").strip()]
    wins.sort(key=lambda w: w.get("focusHistoryID", 9999))
    return wins


def build_css(cfg):
    return ("""
#winswitch { background: rgba(15,15,17,0.85); border-radius: 18px; padding: 22px; border: 1px solid rgba(255,255,255,0.08); }
.wtile { background: transparent; border-radius: 12px; padding: 10px; border: 2px solid transparent; }
.wtile.selected { background: rgba(77,184,189,0.18); border: 2px solid #4db8bd; }
.wthumb { border-radius: 8px; background: rgba(0,0,0,0.35); }
.wtitle { color: #d8d8d8; font-size: %dpx; margin-top: 8px; }
.wtile.selected .wtitle { color: #ffffff; }
""" % int(cfg.get("title_size", 15))).encode()


class WinSwitch(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.eendi.winswitch",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.win = None
        self.windows = []
        self.tiles = []
        self.sel = 0
        self.visible = False
        self.cfg = load_config()
        self._last_adv = 0.0

    # ---- lifecycle ----
    def do_activate(self):
        self._css = Gtk.CssProvider()
        self._css.load_from_data(build_css(self.cfg))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self._css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.win = Gtk.Window(application=self)
        self.win.set_name("winswitch")
        LS.init_for_window(self.win)
        LS.set_layer(self.win, LS.Layer.OVERLAY)
        LS.set_keyboard_mode(self.win, LS.KeyboardMode.EXCLUSIVE)

        kc = Gtk.EventControllerKey()
        kc.connect("key-pressed", self.on_key_press)
        kc.connect("key-released", self.on_key_release)
        self.win.add_controller(kc)
        self.win.set_visible(False)

        self.hold()  # stay alive as a daemon even while hidden
        threading.Thread(target=self._sock_thread, daemon=True).start()

    # ---- socket ----
    def _sock_thread(self):
        try:
            os.unlink(SOCK)
        except OSError:
            pass
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(SOCK)
        os.chmod(SOCK, 0o600)
        s.listen(8)
        while True:
            conn, _ = s.accept()
            data = conn.recv(64).decode().strip()
            conn.close()
            if data:
                GLib.idle_add(self.command, data.splitlines()[0])

    def command(self, cmd):
        if cmd == "cancel":
            if self.visible:
                self.cancel()
        elif cmd in ("next", "prev"):
            d = 1 if cmd == "next" else -1
            if not self.visible:
                self.show(start=d)
            else:
                self._advance(d)   # Hyprland re-fires the bind while we're open -> cycle here
        return False

    # ---- show / build ----
    def show(self, start=1):
        self.cfg = load_config()
        self._css.load_from_data(build_css(self.cfg))
        self.windows = list_windows()
        if not self.windows:
            return
        self.sel = (start if start >= 0 else len(self.windows) - 1)
        self.sel %= len(self.windows)
        self._build()
        self.visible = True
        self.win.set_visible(True)
        self.win.present()

    def _icon_widget(self, w):
        cfg = self.cfg
        # thumbnail mode: use cached capture if present
        if cfg["display"] == "thumbnails":
            p = os.path.join(CACHE, (w.get("address", "").replace("0x", "")) + ".png")
            if os.path.exists(p):
                pic = Gtk.Picture.new_for_filename(p)
                pic.set_content_fit(Gtk.ContentFit.COVER)
                pic.set_hexpand(True)
                pic.set_vexpand(True)
                # fixed-size, overflow-clipped box -> exact thumbnail dims, no window over-sizing
                box = Gtk.Box()
                box.add_css_class("wthumb")
                box.set_overflow(Gtk.Overflow.HIDDEN)
                box.set_size_request(int(cfg["thumb_width"]), int(cfg["thumb_height"]))
                box.set_halign(Gtk.Align.CENTER)
                box.set_valign(Gtk.Align.CENTER)
                box.append(pic)
                return box
        # icon fallback
        it = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        cand = [(w.get("initialClass") or "").lower(), (w.get("class") or "").lower()]
        cand += [c.split(".")[-1] for c in cand if c]
        name = next((c for c in cand if c and it.has_icon(c)), "application-x-executable")
        img = Gtk.Image.new_from_icon_name(name)
        img.set_pixel_size(cfg["icon_size"])
        return img

    def _build(self):
        cfg = self.cfg
        self.tiles = []
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        if cfg["layout"] == "grid":
            container = Gtk.Grid(row_spacing=8, column_spacing=8)
        else:
            container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        cols = max(1, int(cfg["columns"]))

        for i, w in enumerate(self.windows):
            tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            tile.add_css_class("wtile")
            tile.append(self._icon_widget(w))
            t = (w.get("title") or "").strip()
            if len(t) > cfg["title_len"]:
                t = t[: cfg["title_len"] - 1] + "…"
            lbl = Gtk.Label(label=t)
            lbl.add_css_class("wtitle")
            lbl.set_max_width_chars(cfg["title_len"])
            lbl.set_ellipsize(3)  # END
            tile.append(lbl)
            # mouse: hover selects, click commits
            click = Gtk.GestureClick()
            click.connect("released", lambda g, n, x, y, idx=i: self.commit(idx))
            tile.add_controller(click)
            motion = Gtk.EventControllerMotion()
            motion.connect("enter", lambda c, x, y, idx=i: self._select(idx))
            tile.add_controller(motion)
            self.tiles.append(tile)
            if cfg["layout"] == "grid":
                container.attach(tile, i % cols, i // cols, 1, 1)
            else:
                container.append(tile)

        outer.append(container)
        self.win.set_child(outer)
        self._refresh_sel()

    # ---- selection ----
    def _select(self, idx):
        if 0 <= idx < len(self.tiles):
            self.sel = idx
            self._refresh_sel()

    def _refresh_sel(self):
        for i, t in enumerate(self.tiles):
            if i == self.sel:
                t.add_css_class("selected")
            else:
                t.remove_css_class("selected")

    def _move(self, delta):
        if self.windows:
            self.sel = (self.sel + delta) % len(self.windows)
            self._refresh_sel()

    def _advance(self, delta):
        # debounced: the socket (Hyprland bind) and a stray key event can both fire per Tab
        now = time.monotonic()
        if now - self._last_adv < 0.04:
            return
        self._last_adv = now
        self._move(delta)

    # ---- input ----
    def on_key_press(self, ctrl, keyval, keycode, state):
        cfg = self.cfg
        cols = max(1, int(cfg["columns"])) if cfg["layout"] == "grid" else 1
        if keyval in (Gdk.KEY_Tab,):
            self._advance(1)
        elif keyval in (Gdk.KEY_ISO_Left_Tab,):
            self._advance(-1)
        elif keyval == Gdk.KEY_Right:
            self._move(1)
        elif keyval == Gdk.KEY_Left:
            self._move(-1)
        elif keyval == Gdk.KEY_Down and cfg["layout"] == "grid":
            self._move(cols)
        elif keyval == Gdk.KEY_Up and cfg["layout"] == "grid":
            self._move(-cols)
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self.commit(self.sel)
        elif keyval == Gdk.KEY_Print:
            self.screenshot()
        elif keyval == Gdk.KEY_Escape:
            self.cancel()
        return True

    def on_key_release(self, ctrl, keyval, keycode, state):
        # releasing Alt commits — the Windows behaviour
        if keyval in (Gdk.KEY_Alt_L, Gdk.KEY_Alt_R, Gdk.KEY_Meta_L, Gdk.KEY_Meta_R):
            self.commit(self.sel)
        return False

    # ---- actions ----
    def commit(self, idx):
        if not self.visible:
            return
        self.hide()
        if 0 <= idx < len(self.windows):
            addr = self.windows[idx].get("address")
            if addr:
                subprocess.Popen(["hyprctl", "dispatch", "focuswindow", f"address:{addr}"])

    def cancel(self):
        self.hide()

    def hide(self):
        self.visible = False
        self.win.set_visible(False)

    def screenshot(self):
        # capture the whole screen (overlay included) -> clipboard + ~/Pictures/Screenshots
        subprocess.Popen(
            "d=~/Pictures/Screenshots; mkdir -p \"$d\"; f=$d/winswitch-$(date +%s).png; "
            "grim \"$f\" && wl-copy < \"$f\" && "
            "notify-send -a winswitch 'Switcher captured' \"$f\"",
            shell=True)


if __name__ == "__main__":
    WinSwitch().run(None)
