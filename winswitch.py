#!/usr/bin/env python3
"""winswitch — Windows-style Alt+Tab for Hyprland (GTK4 layer-shell).

Display-only daemon: the overlay takes NO keyboard grab. All keyboard input is
owned by the Hyprland "winswitch" submap (see the Lua config), which sends
'next'/'prev'/'commit'/'cancel' over a Unix socket and polls the physical Alt
key (hl.is_key_down) to commit the instant Alt is released. The overlay also
handles the mouse itself (hover selects, click commits). First 'next' opens a
centered overlay (MRU order, previous window pre-selected); holding Alt + tapping
Tab cycles; releasing Alt commits. Layout (row/grid) and display
(icons/thumbnails) are configurable.
"""
import os, json, socket, subprocess, threading, time
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GLib, Gio, GdkPixbuf, Gtk4LayerShell as LS

try:
    import tomllib
except Exception:
    tomllib = None

CONFIG = os.path.expanduser("~/.config/winswitch/config.toml")
CACHE = os.path.expanduser("~/.cache/winswitch")
SOCK = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "winswitch.sock")

# single instance: an abstract unix socket auto-releases when the process dies,
# so a second launch (e.g. a stray exec-once) just exits instead of piling up.
_lock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
try:
    _lock.bind("\0winswitch-overlay")
except OSError:
    raise SystemExit(0)
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
    def real(w):
        sz = w.get("size") or [0, 0]
        return bool(w.get("mapped") and not w.get("hidden")
                    and (w.get("title") or "").strip()
                    and sz[0] > 0 and sz[1] > 0
                    and w.get("workspace", {}).get("id", 1) >= 0)
    wins = [w for w in cl if real(w)]
    wins.sort(key=lambda w: w.get("focusHistoryID", 9999))
    return wins


def build_css(cfg):
    return ("""
#winswitch { background: rgba(15,15,17,0.85); border-radius: 18px; padding: 22px; border: 1px solid rgba(255,255,255,0.08); }
.wtile { background: transparent; border-radius: 10px; padding: 0; border: 2px solid transparent; }
.wtile.selected { background: rgba(77,184,189,0.18); border: 2px solid #4db8bd; }
.wthumb { border-radius: 8px; background: rgba(0,0,0,0.35); }
.wtitle { color: #d8d8d8; font-size: %dpx; }
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
        # NO keyboard grab: an exclusive grab makes Hyprland continuously toggle
        # the held Alt modifier (jittery release/press), making a real Alt-release
        # impossible to detect. So the overlay takes NO keyboard grab; all input is
        # driven by the Hyprland "winswitch" submap (see hyprland.lua), which polls
        # the physical Alt key and sends next/prev/commit/cancel over the socket.
        LS.set_keyboard_mode(self.win, LS.KeyboardMode.NONE)

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
        # commands arrive over the socket from the Hyprland "winswitch" submap
        if cmd == "cancel":
            if self.visible:
                self.cancel()
        elif cmd == "commit":
            # sent when the submap's Alt-poll sees the physical Alt key go up
            if self.visible:
                self.commit(self.sel)
        elif cmd in ("next", "prev"):
            d = 1 if cmd == "next" else -1
            if not self.visible:
                self.show(start=d)
            else:
                self._advance(d)
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
                W, H = int(cfg["thumb_width"]), int(cfg["thumb_height"])
                try:
                    # pre-crop to EXACTLY WxH (COVER). Gtk.Picture would otherwise report
                    # the source image's size as its natural size, inflating the tile and
                    # leaving empty gaps above/below the thumbnail.
                    src = GdkPixbuf.Pixbuf.new_from_file(p)
                    sw, sh = src.get_width(), src.get_height()
                    scale = max(W / sw, H / sh)
                    nw, nh = max(W, round(sw * scale)), max(H, round(sh * scale))
                    scaled = src.scale_simple(nw, nh, GdkPixbuf.InterpType.BILINEAR)
                    crop = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, scaled.get_has_alpha(), 8, W, H)
                    scaled.copy_area((nw - W) // 2, (nh - H) // 2, W, H, crop, 0, 0)
                    pic = Gtk.Picture.new_for_paintable(Gdk.Texture.new_for_pixbuf(crop))
                except Exception:
                    pic = Gtk.Picture.new_for_filename(p)
                    pic.set_content_fit(Gtk.ContentFit.COVER)
                pic.set_size_request(W, H)
                box = Gtk.Box()
                box.add_css_class("wthumb")
                box.set_overflow(Gtk.Overflow.HIDDEN)
                box.set_size_request(W, H)
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

    def _app_icon(self, w, size=20):
        it = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        cand = [(w.get("initialClass") or "").lower(), (w.get("class") or "").lower()]
        cand += [c.split(".")[-1] for c in cand if c]
        name = next((c for c in cand if c and it.has_icon(c)), "application-x-executable")
        img = Gtk.Image.new_from_icon_name(name)
        img.set_pixel_size(size)
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
            tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            tile.add_css_class("wtile")
            # header: app icon + title, ABOVE the thumbnail
            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            header.set_size_request(int(cfg["thumb_width"]), -1)
            ic = self._app_icon(w)
            ic.set_valign(Gtk.Align.CENTER)
            header.append(ic)
            t = (w.get("title") or "").strip()
            if len(t) > cfg["title_len"]:
                t = t[: cfg["title_len"] - 1] + "…"
            lbl = Gtk.Label(label=t)
            lbl.add_css_class("wtitle")
            lbl.set_hexpand(True)
            lbl.set_xalign(0)
            lbl.set_width_chars(1)  # tiny natural width -> a long title can't widen the tile past the thumbnail
            lbl.set_valign(Gtk.Align.CENTER)
            lbl.set_ellipsize(3)  # END
            header.append(lbl)
            tile.append(header)
            tile.append(self._icon_widget(w))
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
        # debounced: rapid repeated "next"/"prev" socket commands collapse to one step
        now = time.monotonic()
        if now - self._last_adv < 0.04:
            return
        self._last_adv = now
        self._move(delta)

    # ---- actions ----
    def commit(self, idx):
        if not self.visible:
            return
        self.hide()
        if 0 <= idx < len(self.windows):
            addr = self.windows[idx].get("address")
            if addr:
                # Lua config: `hyprctl dispatch focuswindow address:X` is parsed as
                # Lua and fails -- the dispatcher must be the hl.dsp.focus form.
                subprocess.Popen(["hyprctl", "dispatch",
                                  'hl.dsp.focus({ window = "address:%s" })' % addr])

    def cancel(self):
        self.hide()

    def hide(self):
        self.visible = False
        self.win.set_visible(False)


if __name__ == "__main__":
    WinSwitch().run(None)
