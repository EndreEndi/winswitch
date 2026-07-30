#!/usr/bin/env python3
"""winswitch thumbnail daemon.

Listens to Hyprland's event socket; whenever a window becomes the active (top,
fully-visible) window, it grabs a snapshot with grim after a short settle delay
and caches a downscaled PNG at ~/.cache/winswitch/<addr>.png. The switcher shows
these. Closed windows' thumbnails are pruned. This is how we get thumbnails of
windows that aren't currently on screen — we snapshot each as you last saw it.
"""
import os, socket, subprocess, json, threading
import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

CACHE = os.path.expanduser("~/.cache/winswitch")
os.makedirs(CACHE, exist_ok=True)

# single instance: an abstract unix socket auto-releases when the process dies
_lock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
try:
    _lock.bind("\0winswitch-thumbd")
except OSError:
    raise SystemExit(0)
XDG = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
HIS = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
EV_SOCK = f"{XDG}/hypr/{HIS}/.socket2.sock"
MAXW, MAXH = 520, 320          # thumbnail cap (px), aspect preserved
SETTLE = 0.35                  # seconds to let the window render before snapping

_timers = {}


def cache_file(bare_hex):
    return os.path.join(CACHE, bare_hex + ".png")


def geom_of(addr0x):
    try:
        cl = json.loads(subprocess.check_output(["hyprctl", "clients", "-j"], text=True))
    except Exception:
        return None
    for w in cl:
        if w.get("address") == addr0x and w.get("mapped"):
            x, y = w.get("at", [0, 0])
            ww, hh = w.get("size", [0, 0])
            if ww > 0 and hh > 0:
                return f"{x},{y} {ww}x{hh}"
    return None


def capture(bare_hex):
    # only snap while this window is STILL the active/visible/on-top one — grim captures the
    # visible screen, so snapping a background/other-workspace window grabs the wrong content
    try:
        aw = json.loads(subprocess.check_output(["hyprctl", "activewindow", "-j"], text=True))
    except Exception:
        return
    if aw.get("address") != "0x" + bare_hex:
        return
    g = geom_of("0x" + bare_hex)
    if not g:
        return
    tmp = f"/tmp/wsthumb_{os.getpid()}_{bare_hex}.png"
    r = subprocess.run(["grim", "-g", g, tmp], stderr=subprocess.DEVNULL)
    if r.returncode == 0 and os.path.exists(tmp):
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(tmp, MAXW, MAXH, True)
            pb.savev(cache_file(bare_hex), "png", [], [])
        except Exception:
            try:
                os.replace(tmp, cache_file(bare_hex))
            except OSError:
                pass
    try:
        os.remove(tmp)
    except OSError:
        pass


def on_active(bare_hex):
    if not bare_hex:
        return
    t = _timers.pop(bare_hex, None)
    if t:
        t.cancel()
    t = threading.Timer(SETTLE, capture, args=(bare_hex,))
    _timers[bare_hex] = t
    t.start()


def on_close(bare_hex):
    try:
        os.remove(cache_file(bare_hex))
    except OSError:
        pass


def run():
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(EV_SOCK)
    # snapshot whatever is active right now
    try:
        a = json.loads(subprocess.check_output(["hyprctl", "activewindow", "-j"], text=True))
        if a.get("address"):
            on_active(a["address"].replace("0x", ""))
    except Exception:
        pass
    buf = b""
    while True:
        data = s.recv(8192)
        if not data:
            break
        buf += data
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.decode("utf-8", "replace")
            ev, sep, arg = line.partition(">>")
            if not sep:
                continue
            if ev == "activewindowv2":
                on_active(arg.strip())
            elif ev == "closewindow":
                on_close(arg.strip())


if __name__ == "__main__":
    while True:
        try:
            run()
        except Exception:
            import time
            time.sleep(1)  # reconnect if the event socket drops
