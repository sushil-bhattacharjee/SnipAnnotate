#!/usr/bin/env python3
"""
snip_annotate.py — a small Snipping-Tool-like screen capture + annotation app.

Features
  * New Snip (region)  — drag a rectangle over a dimmed frozen screen
  * Full Screen        — grab everything (all monitors on Windows)
  * Annotate           — Pen, Highlighter, Line, Arrow, Rectangle, Ellipse, Text
  * Color / width pickers, Undo (Ctrl+Z), Clear
  * Save PNG (Ctrl+S), Copy to clipboard (Ctrl+C, Windows native; Linux needs xclip/wl-copy)
  * Open an existing image file and annotate it

Requirements:  Python 3.9+,  pip install pillow
Run:           python snip_annotate.py
"""
from __future__ import annotations

import io
import os
import platform
import subprocess
import sys
import tempfile
import time
import tkinter as tk
from tkinter import colorchooser, filedialog, font as tkfont, messagebox, simpledialog

try:
    from PIL import Image, ImageDraw, ImageFont, ImageGrab, ImageTk
except ImportError:
    print("Pillow is required:  pip install pillow")
    sys.exit(1)

try:
    import cv2
    import numpy as np
except ImportError:          # recording is optional; snipping works without it
    cv2 = None
    np = None

__version__ = "9.6"


def _install_crash_log():
    """v8.5: pythonw.exe has no console, so stderr is thrown away and a hard
    failure (a Tcl panic, an import error, an abort) leaves NOTHING behind — the
    app just vanishes. Mirror stdout/stderr into a log file, and log any
    uncaught exception, so a crash is always diagnosable.
    """
    log_path = os.path.join(tempfile.gettempdir(), "snipannotate_error.log")

    class _Tee:
        def __init__(self, stream, path):
            self.stream = stream
            self.path = path

        def write(self, data):
            if self.stream is not None:
                try:
                    self.stream.write(data)
                except Exception:
                    pass
            if data.strip():
                try:
                    with open(self.path, "a", encoding="utf-8") as f:
                        f.write(data)
                except Exception:
                    pass

        def flush(self):
            if self.stream is not None:
                try:
                    self.stream.flush()
                except Exception:
                    pass

    sys.stdout = _Tee(sys.stdout, log_path)
    sys.stderr = _Tee(sys.stderr, log_path)

    def _hook(exc_type, exc, tb):
        import traceback as _tb
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n===== UNCAUGHT {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
                _tb.print_exception(exc_type, exc, tb, file=f)
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n===== START v{__version__} "
                    f"{time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
    except Exception:
        pass
    return log_path


LOG_PATH = _install_crash_log()

IS_WINDOWS = platform.system() == "Windows"


def _windows_ocr(img: "Image.Image") -> str:
    """v4: OCR using the engine built into Windows 10/11 (Windows.Media.Ocr).

    Nothing is downloaded: the engine is part of the OS. Only a small `winrt`
    bridge is needed, and it is optional — an ImportError here is surfaced to
    the user with the exact pip command.
    """
    import asyncio
    import io as _io

    # v8.6: winrt's async machinery needs the Foundation projection at runtime.
    # It was NOT imported here, so the failure surfaced deep inside
    #   winrt/runtime/_internals.py -> op.completed = on_complete
    #   ModuleNotFoundError: No module named 'winrt.windows.foundation'
    # …which escaped as a hard crash. Importing it up front turns a missing
    # package into a clean ImportError that we can explain.
    import winrt.windows.foundation                                     # noqa: F401
    from winrt.windows.graphics.imaging import BitmapDecoder            # noqa: F401
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    # v9.3: the Windows engine rejects bitmaps above OcrEngine.max_image_dimension
    # (typically 10000 px, but can be lower). Downscale proportionally if needed —
    # a full 7680-wide two-monitor page must not hard-fail.
    try:
        limit = int(OcrEngine.max_image_dimension)
    except Exception:
        limit = 10000
    src_orig = img
    src_img = img
    if max(src_img.size) > limit:
        f = limit / max(src_img.size)
        src_img = src_img.resize((max(int(src_img.width * f), 1),
                                  max(int(src_img.height * f), 1)))
    img = src_img

    async def _run() -> str:
        buf = _io.BytesIO()
        img.convert("RGB").save(buf, "PNG")
        data = buf.getvalue()

        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream.get_output_stream_at(0))
        # v8.1: newer winrt bindings want a bytes-like object here; older ones
        # wanted a list of ints. Passing list(data) raised
        #   TypeError: a bytes-like object is required, not 'list'
        try:
            writer.write_bytes(data)
        except TypeError:
            writer.write_bytes(list(data))
        await writer.store_async()
        await writer.flush_async()
        stream.seek(0)

        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()

        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            langs = OcrEngine.available_recognizer_languages
            if not langs:
                raise RuntimeError(
                    "No OCR language pack is installed. Add one in "
                    "Settings → Time & language → Language & region.")
            engine = OcrEngine.try_create_from_language(langs[0])

        result = await engine.recognize_async(bitmap)
        # v9.4: structured output — per-word text + bounding box so the app
        # can overlay selectable text on the picture (Snipping-Tool style).
        words = []
        for li, line in enumerate(result.lines):
            for w in line.words:
                r = w.bounding_rect
                words.append({"t": w.text, "line": li,
                              "x0": r.x, "y0": r.y,
                              "x1": r.x + r.width, "y1": r.y + r.height})
        return words

    try:
        loop = asyncio.new_event_loop()
        try:
            words = loop.run_until_complete(_run())
        finally:
            loop.close()
        if img is not src_orig:                    # v9.4: undo the downscale
            fx = src_orig.width / img.width
            fy = src_orig.height / img.height
            for w in words:
                w["x0"] *= fx; w["x1"] *= fx
                w["y0"] *= fy; w["y1"] *= fy
        return words
    except ImportError:
        raise
    except Exception as e:
        raise RuntimeError(str(e)) from e


def _enum_window_rects():
    """v4: [(title, (l, t, r, b)), …] for visible, non-minimised top-level windows.

    Uses the same ctypes/user32 route as the monitor enumeration — no new
    dependency. Returned in Z-order (topmost first), so the smallest window under
    the cursor wins when they overlap.
    """
    if platform.system() != "Windows":
        return []
    import ctypes
    from ctypes import wintypes
    u32 = ctypes.windll.user32
    out = []

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        if not u32.IsWindowVisible(hwnd):
            return True
        if u32.IsIconic(hwnd):                        # minimised
            return True
        length = u32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        u32.GetWindowTextW(hwnd, buf, length + 1)
        r = wintypes.RECT()
        if not u32.GetWindowRect(hwnd, ctypes.byref(r)):
            return True
        w, h = r.right - r.left, r.bottom - r.top
        if w < 40 or h < 40:                          # skip slivers / tool windows
            return True
        out.append((buf.value, (r.left, r.top, r.right, r.bottom)))
        return True

    u32.EnumWindows(EnumWindowsProc(_cb), 0)
    return out
IS_MAC = platform.system() == "Darwin"

if IS_WINDOWS:
    # Per-monitor DPI awareness: tk coordinates == physical pixels, so the
    # region selector's math is exact on every monitor.
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def virtual_origin() -> tuple[int, int]:
    """Top-left of the Windows virtual desktop (can be negative)."""
    if not IS_WINDOWS:
        return (0, 0)
    import ctypes
    SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
    u = ctypes.windll.user32
    return (u.GetSystemMetrics(SM_XVIRTUALSCREEN),
            u.GetSystemMetrics(SM_YVIRTUALSCREEN))


def virtual_geom() -> dict | None:
    """v9.2: the FULL virtual desktop as {x, y, w, h} — the bounding box of
    every monitor. Used to place ONE overlay spanning all screens, so a snip
    can be dragged anywhere (or across screens) like the Windows Snipping Tool.
    Returns None off-Windows (selectors fall back to a fullscreen overlay)."""
    if not IS_WINDOWS:
        return None
    import ctypes
    u = ctypes.windll.user32
    SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
    SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
    return {"x": u.GetSystemMetrics(SM_XVIRTUALSCREEN),
            "y": u.GetSystemMetrics(SM_YVIRTUALSCREEN),
            "w": u.GetSystemMetrics(SM_CXVIRTUALSCREEN),
            "h": u.GetSystemMetrics(SM_CYVIRTUALSCREEN)}


# --------------------------------------------------------------------------- #
# Capture                                                                      #
# --------------------------------------------------------------------------- #

def grab_fullscreen() -> Image.Image:
    if IS_WINDOWS:
        return ImageGrab.grab(all_screens=True)
    # On Linux, ImageGrab uses gnome-screenshot/xdisplay; fall back to scrot/import.
    try:
        return ImageGrab.grab()
    except Exception:
        tmp = os.path.join(tempfile.gettempdir(), f"snip_{int(time.time())}.png")
        for cmd in (["gnome-screenshot", "-f", tmp],
                    ["scrot", tmp],
                    ["import", "-window", "root", tmp]):
            try:
                subprocess.run(cmd, check=True, timeout=15,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                img = Image.open(tmp).convert("RGB")
                os.unlink(tmp)
                return img
            except Exception:
                continue
        raise RuntimeError("No screenshot backend found (install gnome-screenshot or scrot).")


class RegionSelector:
    """Fullscreen frozen-screenshot overlay; drag to pick a rectangle."""

    def __init__(self, root: tk.Tk, screenshot: Image.Image,
                 geom: dict | None = None):
        """geom = {x, y, w, h}: place the overlay on that monitor exactly.
        Without geom, fall back to a fullscreen overlay on the primary."""
        self.root = root
        self.shot = screenshot
        self.result: Image.Image | None = None
        self.box: tuple | None = None      # (x0, y0, x1, y1) in shot coords

        self.top = tk.Toplevel(root)
        if geom:
            self.top.overrideredirect(True)
            self.top.geometry(f"{geom['w']}x{geom['h']}+{geom['x']}+{geom['y']}")
        else:
            self.top.attributes("-fullscreen", True)
        self.top.attributes("-topmost", True)
        self.top.configure(cursor="crosshair")

        if geom:
            sw, sh = geom["w"], geom["h"]
        else:
            sw, sh = self.top.winfo_screenwidth(), self.top.winfo_screenheight()
        # scale factor between the (possibly multi-monitor / HiDPI) shot and this window
        self.sx = self.shot.width / sw
        self.sy = self.shot.height / sh

        disp = self.shot if (sw, sh) == self.shot.size else self.shot.resize((sw, sh))
        dim = Image.new("RGB", disp.size, (0, 0, 0))
        self.dimmed = Image.blend(disp, dim, 0.45)
        self.tk_dim = ImageTk.PhotoImage(self.dimmed)
        self.tk_full = ImageTk.PhotoImage(disp)
        self.disp = disp

        self.cv = tk.Canvas(self.top, width=sw, height=sh, highlightthickness=0)
        self.cv.pack()
        self.cv.create_image(0, 0, anchor="nw", image=self.tk_dim)
        self.rect_img_id = None
        self.rect_outline = None
        self.start = None

        self.cv.bind("<ButtonPress-1>", self.on_press)
        self.cv.bind("<B1-Motion>", self.on_drag)
        self.cv.bind("<ButtonRelease-1>", self.on_release)
        self.top.bind("<Escape>", lambda e: self.close(None))
        self.top.focus_force()
        self.top.grab_set()
        root.wait_window(self.top)

    def on_press(self, e):
        self.start = (e.x, e.y)

    def on_drag(self, e):
        if not self.start:
            return
        x0, y0 = self.start
        x1, y1 = e.x, e.y
        # show the *bright* image inside the selection, dimmed outside
        if self.rect_img_id:
            self.cv.delete(self.rect_img_id)
        if self.rect_outline:
            self.cv.delete(self.rect_outline)
        bx0, by0, bx1, by1 = min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)
        if bx1 - bx0 > 2 and by1 - by0 > 2:
            crop = self.disp.crop((bx0, by0, bx1, by1))
            self._tk_crop = ImageTk.PhotoImage(crop)
            self.rect_img_id = self.cv.create_image(bx0, by0, anchor="nw", image=self._tk_crop)
        self.rect_outline = self.cv.create_rectangle(bx0, by0, bx1, by1,
                                                     outline="#00b0ff", width=2)

    def on_release(self, e):

        if not self.start:
            return self.close(None)
        x0, y0 = self.start
        x1, y1 = e.x, e.y
        bx0, by0 = min(x0, x1), min(y0, y1)
        bx1, by1 = max(x0, x1), max(y0, y1)
        if bx1 - bx0 < 4 or by1 - by0 < 4:
            return self.close(None)
        box = (int(bx0 * self.sx), int(by0 * self.sy),
               int(bx1 * self.sx), int(by1 * self.sy))
        self.box = box                     # selection in shot coordinates
        self.close(self.shot.crop(box))

    def close(self, result):
        self.result = result
        self.top.grab_release()
        self.top.destroy()


# --------------------------------------------------------------------------- #
# Annotator                                                                    #
# --------------------------------------------------------------------------- #

TOOLS = ("pen", "highlight", "line", "arrow", "darrow", "rect", "rrect",
         "ellipse", "triangle", "diamond", "pentagon", "hexagon", "star",
         "callout", "check", "cross", "text")


class WindowPicker:
    """v4: hover to highlight a window, click to capture it."""

    def __init__(self, root, screenshot, rects):
        self.root, self.shot, self.result = root, screenshot, None
        # Topmost-first: the LAST match under the cursor in Z-order is the one on
        # top, so we search the list in order and keep the smallest hit.
        self.rects = rects

        self.top = tk.Toplevel(root)
        self.top.attributes("-fullscreen", True)
        self.top.attributes("-topmost", True)
        self.top.configure(cursor="hand2")

        sw = self.top.winfo_screenwidth()
        sh = self.top.winfo_screenheight()
        self.sx = self.shot.width / sw
        self.sy = self.shot.height / sh
        vx, vy = virtual_origin()
        self.vx, self.vy = vx, vy

        disp = self.shot if (sw, sh) == self.shot.size else self.shot.resize((sw, sh))
        self.dimmed = Image.blend(disp, Image.new("RGB", disp.size, (0, 0, 0)), 0.45)
        self.tk_dim = ImageTk.PhotoImage(self.dimmed)
        self.tk_full = ImageTk.PhotoImage(disp)

        self.cv = tk.Canvas(self.top, width=sw, height=sh, highlightthickness=0)
        self.cv.pack()
        self.cv.create_image(0, 0, anchor="nw", image=self.tk_dim)
        self.hi_id = None
        self.out_id = None
        self.label_id = None
        self.hover = None

        self.cv.bind("<Motion>", self.on_move)
        self.cv.bind("<ButtonPress-1>", self.on_click)
        self.top.bind("<Escape>", lambda e: self.close())
        self.top.focus_force()
        self.top.grab_set()
        root.wait_window(self.top)

    def _hit(self, sx, sy):
        """Smallest window whose rect contains the (screen) point."""
        best, best_area = None, None
        for title, (l, t, r, b) in self.rects:
            if l <= sx <= r and t <= sy <= b:
                area = (r - l) * (b - t)
                if best_area is None or area < best_area:
                    best, best_area = (title, (l, t, r, b)), area
        return best

    def on_move(self, e):
        # canvas -> screen coords
        sx = int(e.x * self.sx) + self.vx
        sy = int(e.y * self.sy) + self.vy
        hit = self._hit(sx, sy)
        if hit == self.hover:
            return
        self.hover = hit
        for i in (self.hi_id, self.out_id, self.label_id):
            if i:
                self.cv.delete(i)
        self.hi_id = self.out_id = self.label_id = None
        if not hit:
            return
        title, (l, t, r, b) = hit
        # canvas rect for that window
        cx0 = (l - self.vx) / self.sx
        cy0 = (t - self.vy) / self.sy
        cx1 = (r - self.vx) / self.sx
        cy1 = (b - self.vy) / self.sy
        # bright crop of the window on top of the dimmed backdrop
        crop = self.tk_full
        self.hi_id = self.cv.create_image(0, 0, anchor="nw", image=crop)
        self.cv.create_rectangle(0, 0, 0, 0)          # keep ids monotonic
        self.cv.coords(self.hi_id, 0, 0)
        # clip by drawing the dim over everything except the window rect
        self.cv.delete(self.hi_id)
        self.hi_id = None
        self.out_id = self.cv.create_rectangle(cx0, cy0, cx1, cy1,
                                               outline="#38bdf8", width=3)
        self.label_id = self.cv.create_text(
            cx0 + 8, max(cy0 - 12, 10), anchor="w",
            text=title[:70], fill="#e2e8f0",
            font=("Segoe UI", 10, "bold"))

    def on_click(self, e):
        if not self.hover:
            return
        _title, (l, t, r, b) = self.hover
        box = (l - self.vx, t - self.vy, r - self.vx, b - self.vy)
        box = (max(box[0], 0), max(box[1], 0),
               min(box[2], self.shot.width), min(box[3], self.shot.height))
        if box[2] > box[0] and box[3] > box[1]:
            self.result = self.shot.crop(box)
        self.close()

    def close(self):
        try:
            self.top.grab_release()
        except Exception:
            pass
        self.top.destroy()


class FreeformSelector:
    """v4: lasso an arbitrary shape; outside the path becomes transparent."""

    def __init__(self, root, screenshot, geom=None):
        self.root, self.shot, self.result = root, screenshot, None
        self.points = []

        self.top = tk.Toplevel(root)
        if geom:
            self.top.overrideredirect(True)
            self.top.geometry(f"{geom['w']}x{geom['h']}+{geom['x']}+{geom['y']}")
            sw, sh = geom["w"], geom["h"]
        else:
            self.top.attributes("-fullscreen", True)
            sw = self.top.winfo_screenwidth()
            sh = self.top.winfo_screenheight()
        self.top.attributes("-topmost", True)
        self.top.configure(cursor="crosshair")

        self.sx = self.shot.width / sw
        self.sy = self.shot.height / sh

        disp = self.shot if (sw, sh) == self.shot.size else self.shot.resize((sw, sh))
        self.dimmed = Image.blend(disp, Image.new("RGB", disp.size, (0, 0, 0)), 0.45)
        self.tk_dim = ImageTk.PhotoImage(self.dimmed)

        self.cv = tk.Canvas(self.top, width=sw, height=sh, highlightthickness=0)
        self.cv.pack()
        self.cv.create_image(0, 0, anchor="nw", image=self.tk_dim)
        self.line_id = None

        self.cv.bind("<ButtonPress-1>", self.on_press)
        self.cv.bind("<B1-Motion>", self.on_drag)
        self.cv.bind("<ButtonRelease-1>", self.on_release)
        self.top.bind("<Escape>", lambda e: self.close())
        self.top.focus_force()
        self.top.grab_set()
        root.wait_window(self.top)

    def on_press(self, e):
        self.points = [(e.x, e.y)]

    def on_drag(self, e):
        if not self.points:
            return
        self.points.append((e.x, e.y))
        if self.line_id:
            self.cv.delete(self.line_id)
        flat = [c for p in self.points for c in p]
        if len(flat) >= 4:
            self.line_id = self.cv.create_line(*flat, fill="#4ade80", width=2)

    def on_release(self, e):
        if len(self.points) < 3:
            self.close()
            return
        # map canvas points -> image points
        pts = [(int(x * self.sx), int(y * self.sy)) for x, y in self.points]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        box = (max(min(xs), 0), max(min(ys), 0),
               min(max(xs), self.shot.width), min(max(ys), self.shot.height))
        if box[2] - box[0] < 5 or box[3] - box[1] < 5:
            self.close()
            return
        mask = Image.new("L", self.shot.size, 0)
        ImageDraw.Draw(mask).polygon(pts, fill=255)
        rgba = self.shot.convert("RGBA")
        rgba.putalpha(mask)
        self.result = rgba.crop(box)
        self.close()

    def close(self):
        try:
            self.top.grab_release()
        except Exception:
            pass
        self.top.destroy()


class Recorder:
    """Threaded screen recorder -> temp MP4; converted at save time if needed."""

    FPS = 12

    def __init__(self, app, bbox_virtual: tuple, win_offset: tuple,
                 autostart: bool = True):
        """bbox_virtual: (x0,y0,x1,y1) in virtual-desktop *image* coords.
        win_offset: virtual origin (vx, vy) to translate to window coords.
        autostart=False (v7): show the region + a ▶ Start button and WAIT."""
        import threading
        self.app = app
        self.bbox = bbox_virtual
        self.vx, self.vy = win_offset
        self.stop_flag = threading.Event()
        self.t0 = time.time()
        w = (bbox_virtual[2] - bbox_virtual[0]) // 2 * 2   # even dims for codecs
        h = (bbox_virtual[3] - bbox_virtual[1]) // 2 * 2
        self.size = (w, h)
        self.tmp = os.path.join(tempfile.gettempdir(),
                                f"snip_rec_{int(self.t0)}.mp4")
        # v6: VideoWriter fails SILENTLY when the codec is unavailable — you get a
        # 0-byte file and no error. Try mp4v, then fall back to MJPG/.avi, and
        # verify isOpened() before we pretend to be recording.
        self.writer = None
        self.err = None
        for fourcc, ext in (("mp4v", ".mp4"), ("MJPG", ".avi"), ("XVID", ".avi")):
            path = os.path.splitext(self.tmp)[0] + ext
            w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*fourcc),
                                self.FPS, self.size)
            if w.isOpened():
                self.writer = w
                self.tmp = path
                self.codec = fourcc
                break
            w.release()
        if self.writer is None:
            self.err = ("No usable video codec was found (tried mp4v, MJPG, XVID).\n"
                        "Try:  pip install --upgrade opencv-python")
            raise RuntimeError(self.err)

        self.frames = 0
        self.errors = 0
        self.last_error = ""
        self.black = 0                              # v6.1: count all-black frames
        self.border = None
        self.running = False                       # v7
        self.thread = None
        self._threading = threading
        self._build_border()                       # v6
        self._build_bar()
        if autostart:
            self.begin()

    # ---------------- v7: explicit start ----------------
    def begin(self, countdown: int = 3):
        """Count down, then actually start capturing."""
        if self.running:
            return
        if countdown > 0:
            self.timer.config(text=str(countdown), fg="#fbbf24")
            self.bar.after(700, lambda: self.begin(countdown - 1))
            return
        self.running = True
        self.t0 = time.time()
        self.timer.config(fg="#e5e7eb")
        if hasattr(self, "start_btn"):
            self.start_btn.pack_forget()
        self.stop_btn.pack(side="left", padx=10, pady=4)
        self.rec_lbl.config(text="⏺ REC")
        self.thread = self._threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        self._tick()

    def cancel(self):
        """v7: abandon an armed (not yet started) recording."""
        self.stop_flag.set()
        try:
            self.writer.release()
            os.remove(self.tmp)
        except Exception:
            pass
        for w in ("bar", "border"):
            x = getattr(self, w, None)
            if x:
                try:
                    x.destroy()
                except Exception:
                    pass
        self.app.recorder = None
        self.app.root.deiconify()
        self.app.status.config(text="Recording cancelled.")

    def _build_border(self):
        """v6: a red outline around the area being recorded — you could not see
        WHAT was being captured before."""
        try:
            x = self.bbox[0] + self.vx
            y = self.bbox[1] + self.vy
            w, h = self.size
            self.border = tk.Toplevel(self.app.root)
            self.border.overrideredirect(True)
            self.border.attributes("-topmost", True)
            self.border.attributes("-alpha", 0.9)
            self.border.geometry(f"{w}x{h}+{x}+{y}")
            self.border.configure(bg="#ef4444")
            # punch a hole: an inner frame the colour of nothing -> only the rim shows
            inner = tk.Frame(self.border, bg="#ef4444")
            inner.pack(fill="both", expand=True, padx=3, pady=3)
            try:                                    # click-through, and don't record it
                self.border.attributes("-transparentcolor", "#000001")
                inner.configure(bg="#000001")
            except Exception:
                pass
        except Exception:
            self.border = None

    # small floating control bar, placed just above the recorded area
    def _build_bar(self):
        r = self.app.root
        self.bar = tk.Toplevel(r)
        self.bar.overrideredirect(True)
        self.bar.attributes("-topmost", True)
        self.bar.configure(bg="#111827")
        bx = self.bbox[0] + self.vx
        by = max(self.bbox[1] + self.vy - 44, 0)
        self.bar.geometry(f"+{bx}+{by}")
        self.rec_lbl = tk.Label(self.bar, text="● READY", fg="#fbbf24", bg="#111827",
                                font=("Segoe UI", 11, "bold"))
        self.rec_lbl.pack(side="left", padx=(10, 4), pady=6)
        self.timer = tk.Label(self.bar, text="0:00", fg="#e5e7eb", bg="#111827",
                              font=("Consolas", 11))
        self.timer.pack(side="left", padx=4)

        # v7: ▶ Start (armed) → ⏹ Stop (running)
        self.start_btn = tk.Button(self.bar, text="▶ Start", command=self.begin,
                                   bg="#15803d", fg="white", relief="flat", padx=12,
                                   activebackground="#16a34a")
        self.start_btn.pack(side="left", padx=(10, 4), pady=4)

        self.stop_btn = tk.Button(self.bar, text="⏹ Stop", command=self.stop,
                                  bg="#ef4444", fg="white", relief="flat", padx=12,
                                  activebackground="#dc2626")

        tk.Button(self.bar, text="✕", command=self.cancel, bg="#334155", fg="white",
                  relief="flat", padx=8, activebackground="#475569"
                  ).pack(side="left", padx=(0, 8), pady=4)

    def _tick(self):
        if self.stop_flag.is_set() or not self.running:
            return
        t = int(time.time() - self.t0)
        self.timer.config(text=f"{t // 60}:{t % 60:02d}")
        self.bar.after(500, self._tick)

    def _cursor_pos(self):
        if not IS_WINDOWS:
            return None
        import ctypes
        from ctypes import wintypes
        pt = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        # window coords -> virtual-image coords
        return (pt.x - self.vx, pt.y - self.vy)

    def _loop(self):
        period = 1.0 / self.FPS
        nxt = time.time()
        # v6.1: self.bbox is in VIRTUAL-DESKTOP IMAGE coordinates (0-based).
        # ImageGrab.grab(all_screens=True) wants SCREEN coordinates, whose origin
        # is the virtual origin — and that is NEGATIVE when a monitor sits left of
        # or above the primary. Passing image coords straight through grabbed a
        # rectangle that was off-screen, so every frame came back BLACK.
        # __init__ already receives the offset for exactly this translation; it
        # simply was never applied here.
        grab_box = (self.bbox[0] + self.vx, self.bbox[1] + self.vy,
                    self.bbox[2] + self.vx, self.bbox[3] + self.vy)
        while not self.stop_flag.is_set():
            try:
                img = ImageGrab.grab(bbox=grab_box, all_screens=True) \
                    if IS_WINDOWS else ImageGrab.grab(bbox=grab_box)
                frame = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
                frame = frame[:self.size[1], :self.size[0]]
                cur = self._cursor_pos()               # screen coords
                if cur:
                    cx = cur[0] - grab_box[0]
                    cy = cur[1] - grab_box[1]
                    if 0 <= cx < self.size[0] and 0 <= cy < self.size[1]:
                        cv2.circle(frame, (cx, cy), 9, (0, 210, 255), 2)
                        cv2.circle(frame, (cx, cy), 2, (0, 210, 255), -1)
                if frame.shape[0] != self.size[1] or frame.shape[1] != self.size[0]:
                    # v6: cv2 writes NOTHING if the frame size differs from the
                    # writer's size — and says nothing about it. Force the match.
                    frame = cv2.resize(frame, self.size)
                if self.frames < 5 and not frame.any():   # v6.1: pure-black frame
                    self.black += 1
                self.writer.write(frame)
                self.frames += 1
            except Exception as e:                 # v6: was `pass` — silent death
                self.errors += 1
                self.last_error = f"{type(e).__name__}: {e}"
            nxt += period
            delay = nxt - time.time()
            if delay > 0:
                time.sleep(delay)
            else:
                nxt = time.time()          # fell behind; resync

    def stop(self):
        if not self.running:                       # v7: armed but never started
            self.cancel()
            return
        self.stop_flag.set()
        if self.thread:
            self.thread.join(timeout=5)
        self.writer.release()
        self.bar.destroy()
        if getattr(self, "border", None):          # v6
            try:
                self.border.destroy()
            except Exception:
                pass
        # v6: tell the truth about what happened, instead of handing over an
        # empty file as if it had worked.
        if self.frames == 0:
            msg = "Recording produced no frames."
            if self.last_error:
                msg += f"\n\nFirst error: {self.last_error}"
            msg += ("\n\nCommon causes:\n"
                    "  • the selected region has zero width or height\n"
                    "  • the screen-capture call was blocked\n"
                    "  • no usable codec (try: pip install --upgrade opencv-python)")
            try:
                os.remove(self.tmp)
            except Exception:
                pass
            self.app.recorder = None
            messagebox.showerror("Recording failed", msg)
            self.app.status.config(text="Recording failed — nothing was captured.")
            return
        if self.black >= 5:                        # v6.1: caught a black recording
            messagebox.showwarning(
                "Recording looks black",
                "Every captured frame was blank.\n\n"
                "This usually means the capture region fell outside the visible "
                "desktop, or the screen is protected (some players and DRM'd "
                "windows capture as black).\n\n"
                "Try recording a different area, or the whole screen.")
        if self.errors:
            self.app.status.config(
                text=f"Recorded {self.frames} frame(s); {self.errors} frame(s) failed "
                     f"({self.last_error}).")
        self.app.recording_done(self.tmp, self.frames, self.size)



SHAPE_MENU = [
    ("line",      "─  Line"),
    ("arrow",     "➤  Arrow"),
    ("darrow",    "↔  Double arrow"),
    ("rect",      "▭  Rectangle"),
    ("rrect",     "▢  Rounded rectangle"),
    ("ellipse",   "◯  Ellipse"),
    ("triangle",  "△  Triangle"),
    ("diamond",   "◇  Diamond"),
    ("pentagon",  "⬠  Pentagon"),
    ("hexagon",   "⬡  Hexagon"),
    ("star",      "★  Star"),
    ("callout",   "💬  Speech bubble"),
    ("check",     "✓  Tick"),
    ("cross",     "✗  Cross"),
]
POLY_SHAPES = {"triangle", "diamond", "pentagon", "hexagon", "star",
               "callout", "check", "cross", "rrect"}


def shape_points(t, x0, y0, x1, y1):
    """v9: outline points for a shape drawn inside the drag box."""
    import math
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    w, h = max(x1 - x0, 1), max(y1 - y0, 1)
    cx, cy = x0 + w / 2, y0 + h / 2

    if t == "triangle":
        return [(cx, y0), (x1, y1), (x0, y1)]
    if t == "diamond":
        return [(cx, y0), (x1, cy), (cx, y1), (x0, cy)]
    if t in ("pentagon", "hexagon"):
        n = 5 if t == "pentagon" else 6
        rot = -math.pi / 2
        return [(cx + (w / 2) * math.cos(rot + i * 2 * math.pi / n),
                 cy + (h / 2) * math.sin(rot + i * 2 * math.pi / n)) for i in range(n)]
    if t == "star":
        pts = []
        for i in range(10):
            r = 0.5 if i % 2 == 0 else 0.22
            a = -math.pi / 2 + i * math.pi / 5
            pts.append((cx + w * r * math.cos(a), cy + h * r * math.sin(a)))
        return pts
    if t == "rrect":
        r = min(w, h) * 0.18
        return [(x0 + r, y0), (x1 - r, y0), (x1, y0 + r), (x1, y1 - r),
                (x1 - r, y1), (x0 + r, y1), (x0, y1 - r), (x0, y0 + r)]
    if t == "callout":
        b = y1 - h * 0.25                       # body bottom; tail hangs below
        return [(x0, y0), (x1, y0), (x1, b), (x0 + w * 0.45, b),
                (x0 + w * 0.22, y1), (x0 + w * 0.28, b), (x0, b)]
    if t == "check":
        return [(x0, y0 + h * 0.55), (x0 + w * 0.35, y1), (x1, y0)]
    if t == "cross":
        return [(x0, y0), (x1, y1), (cx, cy), (x1, y0), (x0, y1)]
    return [(x0, y0), (x1, y1)]


class FlowBar(tk.Frame):
    """v8.9: a toolbar that WRAPS onto extra rows instead of running off the edge.

    The old toolbar packed everything with side="left". When the window was
    narrower than the row, Tk simply gave the trailing widgets ZERO width — so
    ⤢ Fit, ✄ Trim, ↩ Undo and 🗑 Clear rendered 1px wide at x=0 and were
    completely unclickable. They looked like they "did nothing" because they were
    never really there.
    """

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self._items = []
        self._last_w = 0
        self.bind("<Configure>", self._on_configure)

    def add(self, widget, padx=3, pady=4):
        self._items.append((widget, padx, pady))
        return widget

    def _on_configure(self, e):
        if abs(e.width - self._last_w) < 2:
            return
        self._last_w = e.width
        self.reflow(e.width)

    def reflow(self, width=None):
        width = width or self.winfo_width()
        if width <= 1:
            return
        x = y = row_h = 0
        for w, padx, pady in self._items:
            try:
                ww = w.winfo_reqwidth() + 2 * padx
                wh = w.winfo_reqheight() + 2 * pady
            except Exception:
                continue
            if x + ww > width and x > 0:          # wrap to the next row
                x = 0
                y += row_h
                row_h = 0
            w.place(x=x + padx, y=y + pady)
            x += ww
            row_h = max(row_h, wh)
        self.configure(height=max(y + row_h, 1))


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(f"Snip & Annotate  ·  hiTech  (v{__version__})")
        self.root.geometry("1100x750")
        self.zoom = 1.0                       # v3
        self.crop_box = None                  # v4
        self.ocr_words = None                 # v9.4: [{t,line,x0,y0,x1,y1}] or None
        self.ocr_sel = set()                  # v9.4: selected word indices
        self._ocr_anchor = None               # v9.4: drag anchor index
        self._ocr_bar = None                  # v9.4: floating [Copy all][Done] bar
        # v3: ALWAYS shut down cleanly. If the process dies while it still owns
        # the Windows clipboard (EmptyClipboard makes us the owner), other apps
        # can be left unable to copy/paste until they restart — which is exactly
        # what happened after running this tool.
        # v8.4: pythonw.exe has NO console, so an unhandled exception used to make
        # the app vanish with nothing to look at. Log it and SAY so instead.
        self.root.report_callback_exception = self._on_tk_error

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        # v4: crop confirm / cancel
        self.root.bind("<Return>", self._crop_confirm)
        self.root.bind("<Escape>", self._esc_pressed)
        import atexit
        atexit.register(self._release_clipboard_ownership)

        self.image: Image.Image | None = None      # base screenshot
        # ---- v5: MULTI-SNIP ----------------------------------------------
        # Each capture becomes its own Snip (image + its own annotation history)
        # instead of replacing the previous one. Thumbnails down the left switch
        # between them; the ➕ page button composes several onto one canvas.
        self.snips: list[dict] = []                # [{image, shapes, name, layers}]
        self.current: int = -1                     # index into self.snips
        # ---- v8: LAYERS ---------------------------------------------------
        # The canvas is a stack: the base image plus any snips you dragged onto
        # it. Each layer is movable and resizable with ↖ Select, and the canvas
        # AUTO-EXPANDS so a dropped snip is never clipped. The annotation tools
        # are unchanged — they draw on top of whatever the stack renders.
        self.layers: list[dict] = []               # [{orig, x, y, sc, name}]
        self.layer_sel: int | None = None
        self.layer_drag = None
        self.layer_resize = None
        self.layer_drag = None                     # v8
        self._text_entry = None                    # v8.3: inline text editor
        self._text_busy = False                    # v8.4: re-entrancy guard
        self._text_win = None
        self._text_at = None
        self._thumb_from = None                    # v8: drag-from-rail state
        self._thumb_moved = False
        self._ghost = None
        self.shapes: list[dict] = []               # annotation history (for undo/redraw)
        self.tool = tk.StringVar(value="pen")
        # v9.4: leaving OCR text-select mode by picking any other tool
        self.tool.trace_add("write", lambda *_: (
            self._ocr_exit(redraw=True) if (self.ocr_words is not None and
                                            self.tool.get() != "ocr") else None))
        self.selected: int | None = None           # index into self.shapes (text only)
        self.recorder = None                       # active Recorder or None
        self._move_from = None                     # drag-move origin
        self.color = "#ff2d2d"
        self.width = tk.IntVar(value=3)
        self.font_size = tk.IntVar(value=24)
        self.font_size.trace_add("write", lambda *a: self._apply_font_size())
        self._drag = None                          # in-progress shape
        self._tkimg = None

        self._build_toolbar()
        # v8.9: lay the toolbars out once the real window size is known
        self.root.after(50, lambda: [b.reflow() for b in getattr(self, "_bars", ())])

        wrap = tk.Frame(self.root)
        wrap.pack(fill="both", expand=True)

        # ---- v5: thumbnail rail — every snip is kept and switchable ----
        rail = tk.Frame(wrap, bg="#0f172a", width=118)
        rail.pack(side="left", fill="y")
        rail.pack_propagate(False)
        tk.Label(rail, text="SNIPS", bg="#0f172a", fg="#64748b",
                 font=("Segoe UI", 8, "bold")).pack(pady=(6, 2))
        tk.Button(rail, text="➕ page", command=self.new_page,
                  bg="#7c3aed", fg="white", relief="flat", padx=6, pady=3,
                  activebackground="#8b5cf6",
                  ).pack(fill="x", padx=6, pady=(0, 6))
        tk.Label(rail, text="drag a snip\nonto the canvas", bg="#0f172a", fg="#64748b",
                 font=("Segoe UI", 7), justify="center").pack(pady=(0, 4))
        self.thumb_frame = tk.Frame(rail, bg="#0f172a")
        self.thumb_frame.pack(fill="both", expand=True)

        self.hbar = tk.Scrollbar(wrap, orient="horizontal")
        self.vbar = tk.Scrollbar(wrap, orient="vertical")
        self.cv = tk.Canvas(wrap, bg="#2b2b2b",
                            xscrollcommand=self.hbar.set, yscrollcommand=self.vbar.set)
        self.hbar.config(command=self.cv.xview)
        self.vbar.config(command=self.cv.yview)
        self.vbar.pack(side="right", fill="y")
        self.hbar.pack(side="bottom", fill="x")
        self.cv.pack(side="left", fill="both", expand=True)

        self.cv.bind("<ButtonPress-1>", self.on_press)
        self.cv.bind("<B1-Motion>", self.on_drag)
        self.cv.bind("<ButtonRelease-1>", self.on_release)
        self.cv.bind("<Double-Button-1>", self.on_double)
        self.root.bind("<Delete>", lambda e: self._delete_key())
        self.root.bind("<Control-z>", lambda e: self.undo())
        self.root.bind("<Control-s>", lambda e: self.save())
        self.root.bind("<Control-c>", lambda e: self.copy_clipboard())

        self.status = tk.Label(self.root,
                               text=f"v{__version__}  ·  Snip (region) or Full Screen to start.",
                               anchor="w", bg="#1e293b", fg="#e2e8f0")
        self.status.pack(fill="x")

    # ---------------- toolbar ----------------
    def _build_toolbar(self):
        # v3: TWO rows. Everything used to sit in one pack(side="left") row, so on
        # a narrower window the right-hand buttons (Highlight, Save, Copy…) were
        # simply cut off and you had to maximize to reach them.
        wrap = tk.Frame(self.root, bg="#0f172a")
        wrap.pack(fill="x")
        # v8.9: FlowBars wrap onto extra rows, so a narrow window can never hide
        # a button again (⤢ Fit / ✄ Trim / ↩ Undo used to be clipped to 1px).
        bar = FlowBar(wrap, bg="#0f172a")       # row 1: capture + file + actions
        bar.pack(fill="x")
        bar2 = FlowBar(wrap, bg="#0f172a")      # row 2: tools + style + view
        bar2.pack(fill="x")
        self._bars = (bar, bar2)

        def btn(text, cmd, bg="#334155", parent=None):
            p = parent or bar
            b = tk.Button(p, text=text, command=cmd, bg=bg, fg="white",
                          activebackground="#475569", activeforeground="white",
                          relief="flat", padx=10, pady=4)
            p.add(b)
            return b

        # ---- v4: capture as a symbol + dropdown (Rectangle is the default) ----
        cap = tk.Frame(bar, bg="#0f172a")
        bar.add(cap, padx=3)
        tk.Button(cap, text="📷", command=self.new_snip, bg="#0e7c9e", fg="white",
                  activebackground="#0ea5e9", relief="flat", padx=10, pady=4,
                  font=("Segoe UI", 12)).pack(side="left")

        self.cap_menu = tk.Menu(self.root, tearoff=0, bg="#1e293b", fg="#e2e8f0",
                                activebackground="#0e7c9e", activeforeground="white")
        self.cap_menu.add_command(label="▭   Rectangle   (default)", command=self.new_snip)
        self.cap_menu.add_command(label="🗖   Window", command=self.snip_window)
        self.cap_menu.add_command(label="🖥   Full screen", command=self.full_screen)
        self.cap_menu.add_command(label="✎   Freeform", command=self.snip_freeform)

        def _drop(_e=None):
            x = cap.winfo_rootx()
            y = cap.winfo_rooty() + cap.winfo_height()
            self.cap_menu.tk_popup(x, y)

        tk.Button(cap, text="▾", command=_drop, bg="#0e7c9e", fg="white",
                  activebackground="#0ea5e9", relief="flat", padx=4, pady=4).pack(side="left")

        btn("⏺", self.record_start, "#b91c1c")
        btn("📂", self.open_file)

        bar.add(tk.Label(bar, text="│", bg="#0f172a", fg="#334155"), padx=2)

        # ---- v4: the actions people reach for constantly, on the TOP ribbon ----
        # v8.6: this was labelled "🔤 Text", right beside the "T Text" annotation
        # tool — so "choose Text" meant two different things. It is OCR; say so.
        btn("🔤 OCR", self.ocr_image, "#7c3aed")
        btn("⛶ Crop", self.crop_start)
        btn("🎨 Paint", self.edit_in_paint)
        btn("🖨 Print", self.print_image)
        btn("↗ Share", self.share)
        btn("💾 Save", self.save, "#15803d")
        btn("📋 Copy", self.copy_clipboard, "#15803d")
        btn("🩺", self.diagnostics)                 # v9: state report for bug hunts

        # ---- v4: hiTech wordmark, right-aligned in the space that already exists ----
        mark = tk.Frame(bar, bg="#0f172a")
        bar.add(mark, padx=8)
        tk.Label(mark, text="hi", bg="#0f172a", fg="#38bdf8",
                 font=("Segoe UI", 13, "bold")).pack(side="left")
        tk.Label(mark, text="Tech", bg="#0f172a", fg="#4ade80",
                 font=("Segoe UI", 13, "bold")).pack(side="left")

        # ---- row 2: drawing tools, style, actions ----
        for t, label in (("select", "↖ Select"),
                         ("pen", "✏ Pen"), ("highlight", "🖍 Highlight"),
                         ("text", "T Text")):
            bar2.add(tk.Radiobutton(bar2, text=label, value=t, variable=self.tool,
                           indicatoron=False, bg="#334155", fg="white",
                           selectcolor="#0e7c9e", relief="flat",
                           padx=8, pady=4))

        # v9: a full shape library behind one dropdown (was: 4 fixed buttons)
        shp = tk.Frame(bar2, bg="#0f172a")
        bar2.add(shp, padx=3)
        self.shape_btn = tk.Button(shp, text="◇ Shapes ▾", bg="#334155", fg="white",
                                   activebackground="#475569", relief="flat",
                                   padx=10, pady=4, command=lambda: self._shape_menu())
        self.shape_btn.pack(side="left")

        self.shape_m = tk.Menu(self.root, tearoff=0, bg="#1e293b", fg="#e2e8f0",
                               activebackground="#0e7c9e", activeforeground="white")
        for key, label in SHAPE_MENU:
            self.shape_m.add_command(
                label=label, command=lambda k=key: self._pick_shape(k))

        bar2.add(tk.Label(bar2, text=" | ", bg="#0f172a", fg="#64748b"), padx=2)

        self.color_btn = tk.Button(bar2, text="Color", bg=self.color, fg="white",
                                   relief="flat", padx=10, command=self.pick_color)
        bar2.add(self.color_btn)
        bar2.add(tk.Label(bar2, text="Width", bg="#0f172a", fg="#94a3b8"), padx=2)
        bar2.add(tk.Spinbox(bar2, from_=1, to=30, width=3, textvariable=self.width), padx=2)
        bar2.add(tk.Label(bar2, text="Font", bg="#0f172a", fg="#94a3b8"), padx=2)
        bar2.add(tk.Spinbox(bar2, from_=8, to=96, width=3, textvariable=self.font_size), padx=2)

        bar2.add(tk.Label(bar2, text=" | ", bg="#0f172a", fg="#64748b"), padx=2)
        btn("↩ Undo", self.undo, parent=bar2)
        btn("🗑 Clear", self.clear, parent=bar2)

        # zoom controls (v3)
        bar2.add(tk.Label(bar2, text=" | ", bg="#0f172a", fg="#64748b"), padx=2)
        btn("＋", self.zoom_in, parent=bar2)
        btn("－", self.zoom_out, parent=bar2)
        btn("⤢ Fit", self.zoom_fit, parent=bar2)
        btn("✄ Trim", self.trim_canvas, "#0e7c9e", parent=bar2)   # v8.2
        self.zoom_lbl = tk.Label(bar2, text="100%", bg="#0f172a", fg="#94a3b8", width=6)
        bar2.add(self.zoom_lbl, padx=2)

    # ---------------- capture actions ----------------
    def pick_color(self):
        c = colorchooser.askcolor(color=self.color, parent=self.root)
        if c and c[1]:
            self.color = c[1]
            self.color_btn.config(bg=self.color)
            if self.selected is not None:          # recolor the selected text live
                self.shapes[self.selected]["color"] = self.color
                self.redraw()

    def _apply_font_size(self, *_):
        if self.selected is not None:
            self.shapes[self.selected]["size"] = self.font_size.get()
            self.redraw()

    def _grab_all(self) -> tuple[Image.Image, dict | None]:
        """v9.2: Snipping-Tool style — grab the ENTIRE virtual desktop and
        return it with the spanning geometry, so one overlay covers every
        monitor and the drag can start/end/cross anywhere."""
        shot = grab_fullscreen()
        geom = virtual_geom()
        self._log(f"SNIP overlay: shot={shot.width}x{shot.height} geom={geom}")
        return shot, geom

    def new_snip(self):
        self.root.withdraw()
        self.root.update_idletasks()
        time.sleep(0.25)                      # let the window disappear
        try:
            shot, geom = self._grab_all()     # v9.2: no screen pre-choice
        except Exception as e:
            self.root.deiconify()
            messagebox.showerror("Capture failed", str(e))
            return
        self.root.deiconify()
        sel = RegionSelector(self.root, shot, geom)
        if sel.result:
            self.set_image(sel.result)

    # ================= v4: capture modes =================
    def snip_window(self):
        """Click a window; capture just that window."""
        if not IS_WINDOWS:
            messagebox.showinfo("Window capture", "Window capture is Windows-only.")
            return
        self.root.withdraw()
        self.root.update_idletasks()
        time.sleep(0.25)
        try:
            shot = grab_fullscreen()
            rects = _enum_window_rects()
            self.root.deiconify()
            if not rects:
                messagebox.showerror("Window capture", "No windows found.")
                return
            picked = WindowPicker(self.root, shot, rects).result
            if picked:
                self.set_image(picked)
        except Exception as e:
            self.root.deiconify()
            messagebox.showerror("Window capture failed", str(e))

    def snip_freeform(self):
        """Lasso an arbitrary shape; everything outside becomes transparent."""
        self.root.withdraw()
        self.root.update_idletasks()
        time.sleep(0.25)
        try:
            shot, geom = self._grab_all()     # v9.2: spans all monitors
        except Exception as e:
            self.root.deiconify()
            messagebox.showerror("Capture failed", str(e))
            return
        self.root.deiconify()
        sel = FreeformSelector(self.root, shot, geom)
        if sel.result:
            self.set_image(sel.result)

    def full_screen(self):
        """v9.3: grab the ENTIRE virtual desktop (all monitors) — same as the
        Snipping Tool's fullscreen mode. For one screen, drag a snip over it."""
        self.root.withdraw()
        self.root.update_idletasks()
        time.sleep(0.25)
        try:
            shot = grab_fullscreen()
            self._log(f"FULLSCREEN grab: {shot.width}x{shot.height}")
        finally:
            self.root.deiconify()
        self.set_image(shot)

    def record_start(self):
        if cv2 is None:
            messagebox.showinfo("Recording needs OpenCV",
                                "Install it with:\n\n  pip install opencv-python\n\n"
                                "then restart the app.")
            return
        if self.recorder:
            return
        # freeze ALL screens, let the user drag a region anywhere (Esc = everything)
        self.root.withdraw()
        self.root.update_idletasks()
        time.sleep(0.25)
        try:
            shot, geom = self._grab_all()     # v9.2: spans all monitors
        except Exception as e:
            self.root.deiconify()
            messagebox.showerror("Capture failed", str(e))
            return
        self.root.deiconify()
        sel = RegionSelector(self.root, shot, geom)
        vx, vy = virtual_origin()
        if sel.box:
            bbox = sel.box                    # already virtual-desktop image coords
        else:                                 # Esc / tiny drag -> everything
            bbox = (0, 0, shot.width, shot.height)
        # v6: sanity-check the region BEFORE we hide anything. A zero-width or
        # zero-height box produced a valid-looking but completely empty video.
        bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if bw < 16 or bh < 16:
            messagebox.showerror(
                "Recording",
                f"The selected area is too small ({bw}×{bh}).\n"
                "Drag a larger region, or press Esc to record the whole screen.")
            return

        # v7: do NOT start recording the instant the region is drawn. Show the
        # area, then wait for an explicit ▶ Start (with a 3-2-1 countdown), so
        # you can get the screen ready first.
        self.root.withdraw()
        try:
            self.recorder = Recorder(self, bbox, (vx, vy), autostart=False)
        except Exception as e:
            self.recorder = None
            self.root.deiconify()
            messagebox.showerror("Recording failed to start", str(e))
            return
        self.status.config(
            text=f"Armed {bw}×{bh} — press ▶ Start on the floating bar when ready.")

    def recording_done(self, tmp_path: str, frames: int, size: tuple):
        self.recorder = None
        self.root.deiconify()
        if frames == 0:
            messagebox.showerror("Recording", "No frames were captured.")
            return
        secs = frames / Recorder.FPS
        p = filedialog.asksaveasfilename(
            defaultextension=".mp4",
            initialfile=time.strftime("recording_%Y%m%d_%H%M%S.mp4"),
            filetypes=[("MP4 video", "*.mp4"), ("AVI video", "*.avi"),
                       ("Animated GIF", "*.gif")])
        if not p:
            os.unlink(tmp_path)
            self.status.config(text="Recording discarded.")
            return
        try:
            ext = os.path.splitext(p)[1].lower()
            if ext == ".mp4":
                os.replace(tmp_path, p)
            elif ext == ".avi":
                self._transcode(tmp_path, p, "XVID")
                os.unlink(tmp_path)
            elif ext == ".gif":
                self._to_gif(tmp_path, p)
                os.unlink(tmp_path)
            else:
                os.replace(tmp_path, p)
            self.status.config(text=f"Saved {secs:.0f}s recording: {p}")
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    @staticmethod
    def _transcode(src_path: str, dst: str, fourcc: str):
        cap = cv2.VideoCapture(src_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or Recorder.FPS
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        out = cv2.VideoWriter(dst, cv2.VideoWriter_fourcc(*fourcc), fps, (w, h))
        while True:
            ok, f = cap.read()
            if not ok:
                break
            out.write(f)
        cap.release()
        out.release()

    @staticmethod
    def _to_gif(src_path: str, dst: str, max_w: int = 800):
        cap = cv2.VideoCapture(src_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or Recorder.FPS
        frames = []
        while True:
            ok, f = cap.read()
            if not ok:
                break
            f = cv2.cvtColor(f, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(f)
            if img.width > max_w:
                img = img.resize((max_w, int(img.height * max_w / img.width)))
            frames.append(img)
        cap.release()
        if not frames:
            raise RuntimeError("no frames to convert")
        frames[0].save(dst, save_all=True, append_images=frames[1:],
                       duration=int(1000 / fps), loop=0, optimize=True)

    def open_file(self):
        p = filedialog.askopenfilename(filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp")])
        if p:
            self.set_image(Image.open(p).convert("RGB"))

    # ================= v5: snip list, thumbnails, compose =================
    def _commit_current(self):
        """Write the live image/shapes/layers back into the current snip record."""
        if 0 <= self.current < len(self.snips):
            self.snips[self.current]["image"] = self.image
            self.snips[self.current]["shapes"] = self.shapes
            self.snips[self.current]["layers"] = self.layers          # v8

    def select_snip(self, i: int):
        if not (0 <= i < len(self.snips)):
            return
        self._commit_current()
        self.current = i
        self.image = self.snips[i]["image"]
        self.shapes = self.snips[i]["shapes"]
        self.layers = self.snips[i].setdefault("layers", [])          # v8
        self.layer_sel = None
        self.selected = None
        self.crop_box = None
        self._refresh_thumbs()
        self.zoom_fit()
        self.status.config(
            text=f"{self.snips[i]['name']} — {self.image.width}×{self.image.height} "
                 f"({i + 1} of {len(self.snips)})")

    def delete_snip(self, i: int):
        if not (0 <= i < len(self.snips)):
            return
        del self.snips[i]
        if not self.snips:
            self.current = -1
            self.image = None
            self.shapes = []
            self.cv.delete("all")
            self._refresh_thumbs()
            self.status.config(text="No snips. Press 📷 to capture.")
            return
        self.current = min(i, len(self.snips) - 1)
        self.select_snip(self.current)

    def _refresh_thumbs(self):
        """Rebuild the thumbnail rail on the left."""
        if not hasattr(self, "thumb_frame"):
            return
        for w in self.thumb_frame.winfo_children():
            w.destroy()
        self._thumb_imgs = []                      # keep refs alive

        for i, snip in enumerate(self.snips):
            im = snip["image"].copy()
            im.thumbnail((96, 96))
            tkim = ImageTk.PhotoImage(im)
            self._thumb_imgs.append(tkim)

            cell = tk.Frame(self.thumb_frame,
                            bg="#0ea5e9" if i == self.current else "#1e293b",
                            padx=2, pady=2)
            cell.pack(fill="x", padx=4, pady=3)

            lbl = tk.Label(cell, image=tkim, bd=0, cursor="hand2")
            lbl.pack()
            # v8: click switches to the snip; DRAGGING it onto the canvas drops
            # it in as a movable/resizable layer.
            lbl.bind("<ButtonPress-1>", lambda e, k=i: self._thumb_press(e, k))
            lbl.bind("<B1-Motion>", self._thumb_motion)
            lbl.bind("<ButtonRelease-1>", self._thumb_release)

            row = tk.Frame(cell, bg=cell["bg"])
            row.pack(fill="x")
            tk.Label(row, text=snip["name"], bg=cell["bg"], fg="#e2e8f0",
                     font=("Segoe UI", 8)).pack(side="left")
            tk.Button(row, text="✕", command=lambda k=i: self.delete_snip(k),
                      bg=cell["bg"], fg="#94a3b8", relief="flat",
                      font=("Segoe UI", 8), padx=2, pady=0,
                      activebackground="#dc2626", activeforeground="white"
                      ).pack(side="right")

    # ================= v8: drag a thumbnail onto the canvas =================
    def _thumb_press(self, e, idx):
        """Remember which snip the press started on; a click still just switches."""
        self._thumb_from = idx
        self._thumb_moved = False
        self._ghost = None

    def _thumb_motion(self, e):
        if self._thumb_from is None:
            return
        self._thumb_moved = True
        # a small ghost that follows the pointer, so the drag is visible
        if self._ghost is None:
            try:
                src = self.snips[self._thumb_from]
                im = src["image"].copy()
                im.thumbnail((140, 140))
                self._ghost_img = ImageTk.PhotoImage(im)
                g = tk.Toplevel(self.root)
                g.overrideredirect(True)
                g.attributes("-topmost", True)
                try:
                    g.attributes("-alpha", 0.75)
                except Exception:
                    pass
                tk.Label(g, image=self._ghost_img, bd=2, relief="solid",
                         bg="#38bdf8").pack()
                self._ghost = g
            except Exception:
                self._ghost = None
        if self._ghost is not None:
            self._ghost.geometry(f"+{e.x_root + 12}+{e.y_root + 12}")

    def _thumb_release(self, e):
        idx = self._thumb_from
        moved = self._thumb_moved
        self._thumb_from = None
        self._thumb_moved = False
        if self._ghost is not None:
            try:
                self._ghost.destroy()
            except Exception:
                pass
            self._ghost = None
        if idx is None:
            return

        if not moved:                              # a plain click -> switch snip
            self.select_snip(idx)
            return

        # dropped over the canvas? convert the screen point to canvas coords
        cx = e.x_root - self.cv.winfo_rootx()
        cy = e.y_root - self.cv.winfo_rooty()
        if 0 <= cx <= self.cv.winfo_width() and 0 <= cy <= self.cv.winfo_height():
            at = (self.cv.canvasx(cx), self.cv.canvasy(cy))
            self.add_layer_from_snip(idx, at=at)
        else:
            self.select_snip(idx)                  # dropped elsewhere: just switch

    def _delete_key(self):
        """v8: Del removes the selected LAYER, else the selected text shape."""
        if self.layer_sel is not None:
            self.delete_layer()
            return
        self.delete_selected()

    # ================= v8: layers on the MAIN canvas =================
    def add_layer_from_snip(self, idx: int, at=None):
        """Drop snip #idx onto the current canvas as a movable/resizable layer.

        The canvas AUTO-EXPANDS so the dropped image is never clipped.
        """
        if not (0 <= idx < len(self.snips)):
            return
        if idx == self.current:
            messagebox.showinfo("Add snip", "That snip is the page you are editing.\n"
                                            "Drag a DIFFERENT snip onto it.")
            return
        src = self.snips[idx]
        img = self.render_snip(src)                 # annotations baked in

        if self.image is None:                      # empty page -> becomes the base
            self.set_image(img)
            return

        # v8.1: the FIRST image was the base bitmap, not a layer — so it had no
        # handles and could not be selected, moved or resized (only the snips you
        # dropped on top could). Promote it to layer 0 now, with a white page
        # behind it, so EVERY image on the canvas behaves the same way.
        self._promote_base_to_layer()

        z = getattr(self, "zoom", 1.0) or 1.0
        if at:
            x = max(int(at[0] / z), 0)
            y = max(int(at[1] / z), 0)
        else:                                       # no drop point: place to the right
            x = self.image.width + 12
            y = 12

        self.layers.append({"orig": img, "img": img, "x": x, "y": y,
                            "w": img.width, "h": img.height, "name": src["name"]})
        self.layer_sel = len(self.layers) - 1
        self._expand_canvas()
        self.tool.set("select")
        self.redraw()
        self.status.config(
            text=f"{src['name']} added as a layer — drag to move, drag a corner to "
                 f"resize (↖ Select). All annotation tools still work.")

    def _promote_base_to_layer(self):
        """v8.1: turn the page's base bitmap into layer 0 (once), so it is
        selectable / movable / resizable like every dropped snip."""
        cur = self.snips[self.current] if 0 <= self.current < len(self.snips) else None
        if self.layers:                             # already promoted
            return
        if self.image is None or (cur and cur.get("is_page")):
            return                                  # a blank page stays the backdrop
        base = self.image
        self.layers.append({"orig": base, "img": base, "x": 0, "y": 0,
                            "w": base.width, "h": base.height,
                            "name": cur["name"] if cur else "Base"})
        self.image = Image.new("RGB", (base.width, base.height), (255, 255, 255))
        if cur:
            cur["image"] = self.image
            cur["is_page"] = True                   # the backdrop is now a page

    MARGIN = 12

    def _content_extent(self):
        """Width/height needed to hold every layer (plus a small margin)."""
        if not self.layers:
            return None
        w = max(int(l["x"] + self._layer_img(l).width) for l in self.layers)
        h = max(int(l["y"] + self._layer_img(l).height) for l in self.layers)
        return w + self.MARGIN, h + self.MARGIN

    def _fit_canvas(self, shrink: bool = False):
        """v8.2: size the page to its content.

        The old `_expand_canvas` only ever GREW. Drag a snip far out and the page
        grew; drag it back and it stayed huge — which is why an export ended up
        ~90% empty white. Now it can shrink back too (on release / trim).
        """
        if self.image is None:
            return
        ext = self._content_extent()
        if not ext:
            return
        need_w, need_h = ext
        cur_w, cur_h = self.image.width, self.image.height

        if shrink:
            new_w, new_h = max(need_w, 40), max(need_h, 40)
        else:                                       # while dragging: never clip
            new_w, new_h = max(need_w, cur_w), max(need_h, cur_h)

        if (new_w, new_h) == (cur_w, cur_h):
            return
        bg = Image.new("RGB", (new_w, new_h), (255, 255, 255))
        bg.paste(self.image.crop((0, 0, min(cur_w, new_w), min(cur_h, new_h))), (0, 0))
        self.image = bg
        if 0 <= self.current < len(self.snips):
            self.snips[self.current]["image"] = bg

    # kept as an alias so nothing else breaks
    def _expand_canvas(self):
        self._fit_canvas(shrink=False)

    def trim_canvas(self):
        """v8.7: crop the page to its content on ALL FOUR sides.

        The old version only shrank the right/bottom edge — it computed
        max(x + width) and never moved the layers back toward the origin. So
        after you dragged things around, the white on the left and top stayed
        (40% of the page in testing) and Trim looked like it did nothing.
        Now the content is shifted flush to the corner first, then the page is
        sized to it.
        """
        self._log(f"TRIM clicked: page={self.image.size if self.image else None} "
                  f"layers={len(self.layers)} shapes={len(self.shapes)}")
        if self.image is None:
            self.status.config(text="Nothing to trim.")
            self._log("TRIM: no image -> abort")
            return

        if self.layers:
            before = self.image.size
            # bounding box of everything on the page
            min_x = min(int(l["x"]) for l in self.layers)
            min_y = min(int(l["y"]) for l in self.layers)

            # shift the layers (and any annotations) flush to the top-left
            if min_x or min_y:
                for l in self.layers:
                    l["x"] = int(l["x"]) - min_x
                    l["y"] = int(l["y"]) - min_y
                for sh in self.shapes:               # keep annotations aligned
                    if sh["t"] == "text":
                        sh["xy"] = (sh["xy"][0] - min_x, sh["xy"][1] - min_y)
                    elif "pts" in sh:
                        sh["pts"] = [(px - min_x, py - min_y) for px, py in sh["pts"]]
                    sh.pop("_bbox", None)

            self._fit_canvas(shrink=True)            # now size to the content
            self.selected = None
            self.zoom_fit()
            self.redraw()
            self.status.config(
                text=f"Trimmed {before[0]}×{before[1]} → "
                     f"{self.image.width}×{self.image.height}.")
            self._log(f"TRIM (layers): {before} -> {self.image.size}")
            return
        # no layers: trim the white border off the flattened page itself
        from PIL import ImageChops
        flat = self.render()
        before = flat.size
        bg = Image.new("RGB", flat.size, (255, 255, 255))
        box = ImageChops.difference(flat.convert("RGB"), bg).getbbox()
        if not box:
            self.status.config(text="Nothing to trim — the page is blank.")
            return
        if box == (0, 0, flat.width, flat.height):
            self.status.config(
                text="Nothing to trim — there is no white border on this image.")
            self._log(f"TRIM (flat): content fills the page {flat.size} -> no-op")
            return
        self.image = flat.crop(box)
        self.shapes.clear()
        self.selected = None
        if 0 <= self.current < len(self.snips):
            self.snips[self.current]["image"] = self.image
            self.snips[self.current]["shapes"] = self.shapes
        self.zoom_fit()
        self.redraw()
        self.status.config(
            text=f"Trimmed {before[0]}×{before[1]} → "
                 f"{self.image.width}×{self.image.height}.")

    def _layer_img(self, l):
        """v8.2: a layer now carries an explicit target w/h (not one scale), so it
        can be stretched horizontally or vertically on its own."""
        if "w" not in l:                            # migrate an old sc-based layer
            sc = l.get("sc", 1.0)
            l["w"] = max(int(l["orig"].width * sc), 20)
            l["h"] = max(int(l["orig"].height * sc), 20)
        w = max(int(l["w"]), 20)
        h = max(int(l["h"]), 20)
        if (w, h) != (l["img"].width, l["img"].height):
            l["img"] = l["orig"].resize((w, h), Image.LANCZOS)
        return l["img"]

    def _layer_at(self, ix, iy):
        """Topmost layer containing the IMAGE-space point, or None."""
        for i in range(len(self.layers) - 1, -1, -1):
            l = self.layers[i]
            im = self._layer_img(l)
            if l["x"] <= ix <= l["x"] + im.width and l["y"] <= iy <= l["y"] + im.height:
                return i
        return None

    def _layer_handles(self, l):
        """v8.2: EIGHT handles — 4 corners (keep aspect) + 4 edges (stretch one
        axis), so pieces can be fitted together horizontally or vertically."""
        im = self._layer_img(l)
        x, y, w, h = l["x"], l["y"], im.width, im.height
        return {
            "nw": (x, y),            "n": (x + w / 2, y),         "ne": (x + w, y),
            "w":  (x, y + h / 2),                                 "e":  (x + w, y + h / 2),
            "sw": (x, y + h),        "s": (x + w / 2, y + h),     "se": (x + w, y + h),
        }

    def _layer_handle(self, i, ix, iy):
        """Which handle of layer i is under the (image-space) point?"""
        if i is None or not (0 <= i < len(self.layers)):
            return None
        l = self.layers[i]
        z = getattr(self, "zoom", 1.0) or 1.0
        tol = 12 / z                                # generous in image space
        for name, (hx, hy) in self._layer_handles(l).items():
            if abs(ix - hx) <= tol and abs(iy - hy) <= tol:
                return name
        return None

    def delete_layer(self):
        if self.layer_sel is not None and 0 <= self.layer_sel < len(self.layers):
            name = self.layers[self.layer_sel]["name"]
            del self.layers[self.layer_sel]
            self.layer_sel = None
            self.redraw()
            self.status.config(text=f"Removed layer: {name}")

    def new_page(self):
        """v8: a blank white page to drop snips onto."""
        w, h = 1280, 800
        self._commit_current()
        self.snips.append({"image": Image.new("RGB", (w, h), (255, 255, 255)),
                           "shapes": [], "layers": [],
                           "is_page": True,                    # v8.1: a backdrop,
                           "name": f"Page {len(self.snips) + 1}"})   # never a layer
        self.current = len(self.snips) - 1
        self.image = self.snips[self.current]["image"]
        self.shapes = self.snips[self.current]["shapes"]
        self.layers = self.snips[self.current]["layers"]
        self.layer_sel = None
        self.selected = None
        self._refresh_thumbs()
        self.zoom_fit()
        self.status.config(
            text="Blank page — drag snips from the left onto it. "
                 "The page grows to fit; drag a corner to resize a snip.")

    # ---------------- compose several snips into one image ----------------
    def _fit_zoom(self) -> float:
        """Zoom that makes the whole image visible in the canvas."""
        if not self.image:
            return 1.0
        self.root.update_idletasks()
        cw = max(self.cv.winfo_width(), 200)
        ch = max(self.cv.winfo_height(), 200)
        z = min(cw / self.image.width, ch / self.image.height, 1.0)
        return max(z, 0.05)

    def _apply_zoom(self, z):
        self.zoom = max(0.05, min(z, 8.0))
        if hasattr(self, "zoom_lbl"):
            self.zoom_lbl.config(text=f"{int(self.zoom * 100)}%")
        self.redraw()

    def zoom_in(self):  self._apply_zoom(getattr(self, "zoom", 1.0) * 1.25)
    def zoom_out(self): self._apply_zoom(getattr(self, "zoom", 1.0) / 1.25)
    def zoom_fit(self): self._apply_zoom(self._fit_zoom())
    def zoom_100(self): self._apply_zoom(1.0)

    def set_image(self, img: Image.Image, replace: bool = False):
        """v5: every capture ADDS a snip. Nothing is ever silently replaced."""
        if replace and 0 <= self.current < len(self.snips):
            self.snips[self.current]["image"] = img.convert("RGB")
            self.snips[self.current]["shapes"] = []
        else:
            self._commit_current()                 # keep the one we are leaving
            self.snips.append({
                "image": img.convert("RGB"),
                "shapes": [],
                "layers": [],                      # v8
                "name": f"Snip {len(self.snips) + 1}",
            })
            self.current = len(self.snips) - 1
        self.image = self.snips[self.current]["image"]
        self.shapes = self.snips[self.current]["shapes"]
        self.layers = self.snips[self.current].setdefault("layers", [])   # v8
        self.layer_sel = None
        self.selected = None
        self._refresh_thumbs()
        # v3: like the Microsoft Snipping Tool — after a capture the window comes
        # forward, sizes itself to the shot, and the image is ZOOMED TO FIT so
        # every tool is reachable straight away (no maximizing, no scrolling).
        self._size_window_to(img)
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.root.after(60, self.zoom_fit)
        self.status.config(text=f"Image {img.width}×{img.height} — annotate, then Save or Copy.")

    def _size_window_to(self, img):
        """Fit the window around the capture, capped to the screen."""
        try:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            w = min(img.width + 40, int(sw * 0.9))
            h = min(img.height + 170, int(sh * 0.9))     # 170 = two toolbars + status
            w, h = max(w, 900), max(h, 520)
            x = max((sw - w) // 2, 0)
            y = max((sh - h) // 3, 0)
            self.root.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            pass

    # ---------------- drawing ----------------
    def canvas_xy(self, e):
        """Canvas event -> IMAGE coordinates (undo the zoom, so annotations land
        on the correct pixels no matter how the view is scaled)."""
        z = getattr(self, "zoom", 1.0) or 1.0
        return (self.cv.canvasx(e.x) / z, self.cv.canvasy(e.y) / z)

    def _shape_bbox(self, s):
        """v9: bounding box of ANY shape, in image coords."""
        if s["t"] == "text":
            return s.get("_bbox")
        pts = s.get("pts") or []
        if not pts:
            return None
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))

    def _hit_shape(self, x, y) -> int | None:
        """v9: topmost shape (of ANY kind) under the point.

        Previously only TEXT could be selected — so an arrow, rectangle or
        ellipse could never be moved once drawn.
        """
        for i in range(len(self.shapes) - 1, -1, -1):
            bb = self._shape_bbox(self.shapes[i])
            if bb and bb[0] - 6 <= x <= bb[2] + 6 and bb[1] - 6 <= y <= bb[3] + 6:
                return i
        return None

    def _hit_text(self, x, y) -> int | None:
        """Topmost TEXT shape under the point (used by double-click-to-edit)."""
        for i in range(len(self.shapes) - 1, -1, -1):
            s = self.shapes[i]
            if s["t"] != "text":
                continue
            bb = s.get("_bbox")
            if bb and bb[0] - 4 <= x <= bb[2] + 4 and bb[1] - 4 <= y <= bb[3] + 4:
                return i
        return None

    def on_press(self, e):
        if not self.image:
            return
        # v8.4: a click outside an open text box commits it (this is what the
        # removed <FocusOut> binding used to do, but safely — we are not inside
        # the widget's own focus event here).
        if self._text_entry is not None:
            self._commit_text()
            return
        x, y = self.canvas_xy(e)
        t = self.tool.get()

        if t == "crop":                            # v4
            self.crop_box = [x, y, x, y]
            return

        if t == "ocr":                             # v9.4: anchor a text selection
            # v9.5: nearest-word matching — a press on the whitespace beside a
            # word must still start a selection, like real text selection.
            i = self._ocr_word_near(x, y)
            self._ocr_anchor = i
            self._ocr_arming = True                # drag may still find an anchor
            self.ocr_sel = {i} if i is not None else set()
            self.redraw()
            return

        if t in ("select", "text"):
            # v9: ANY shape can be picked up, not just text
            hit = self._hit_shape(x, y) if t == "select" else self._hit_text(x, y)
            if hit is not None:                    # select (and arm move)
                self.selected = hit
                self._move_from = (x, y)
                sh = self.shapes[hit]
                if sh["t"] == "text":
                    self.font_size.set(sh["size"])
                self.redraw()
                kind = "Text" if sh["t"] == "text" else sh["t"].capitalize()
                self.status.config(text=f"{kind} selected — drag to move, "
                                        f"Del to delete.")
                return

            if t == "select":
                # v8: a corner handle of the SELECTED layer wins over everything
                hd = self._layer_handle(self.layer_sel, x, y)
                if hd:
                    l = self.layers[self.layer_sel]
                    im = self._layer_img(l)
                    self.layer_resize = (hd, x, y, im.width, im.height,
                                         l["x"], l["y"])
                    return
                # v8: click a layer -> select it and arm a move
                li = self._layer_at(x, y)
                if li is not None:
                    self.layer_sel = li
                    self.layers.append(self.layers.pop(li))    # bring to front
                    self.layer_sel = len(self.layers) - 1
                    l = self.layers[self.layer_sel]
                    self.layer_drag = (x - l["x"], y - l["y"])
                    self.selected = None
                    self.redraw()
                    self.status.config(
                        text=f"{l['name']} — drag to move, drag a corner to resize, "
                             f"Del to remove.")
                    return
                if self.layer_sel is not None or self.selected is not None:
                    self.layer_sel = None
                    self.selected = None
                    self.redraw()
                return
        elif self.selected is not None:            # drawing tool: drop selection
            self.selected = None
            self.redraw()

        if t == "text":
            # v8.3: type straight onto the canvas. The old code called
            # simpledialog.askstring(), which blocks inside wait_window() — with
            # a topmost overlay or a stale grab around it never returned, and the
            # whole app froze. An inline Entry cannot deadlock.
            self._begin_text(x, y)
            return

        self._drag = {"t": t, "pts": [(x, y)], "color": self.color,
                      "w": self.width.get()}

    def on_drag(self, e):
        # ---- v9.1: crop marquee — the v8 rewrite dropped this branch, so the
        # box was never updated during the drag and Enter always saw a
        # degenerate (x, y, x, y) box ("area too small").
        if self.tool.get() == "crop" and self.crop_box is not None:
            x, y = self.canvas_xy(e)
            self.crop_box[2], self.crop_box[3] = x, y
            self.redraw()
            return

        # v9.4: extend the text selection while dragging (reading order)
        if self.tool.get() == "ocr":
            if not getattr(self, "_ocr_arming", False) and self._ocr_anchor is None:
                return
            x, y = self.canvas_xy(e)
            i = self._ocr_word_near(x, y)
            if i is None:
                return
            if self._ocr_anchor is None:           # v9.5: press missed a word —
                self._ocr_anchor = i               # first word touched anchors
            self.ocr_sel = set(range(min(self._ocr_anchor, i),
                                     max(self._ocr_anchor, i) + 1))
            self.redraw()
            return

        # ---- v8: move / resize a dropped layer -------------------------------
        if self.layer_resize is not None and self.layer_sel is not None:
            x, y = self.canvas_xy(e)
            hd, x0, y0, w0, h0, lx0, ly0 = self.layer_resize
            l = self.layers[self.layer_sel]
            ow, oh = l["orig"].width, l["orig"].height
            dx, dy = x - x0, y - y0

            # which edges move with the cursor
            if hd in ("e", "ne", "se"):
                new_w = w0 + dx
            elif hd in ("w", "nw", "sw"):
                new_w = w0 - dx
            else:                                   # n / s: width unchanged
                new_w = w0

            if hd in ("s", "se", "sw"):
                new_h = h0 + dy
            elif hd in ("n", "ne", "nw"):
                new_h = h0 - dy
            else:                                   # e / w: height unchanged
                new_h = h0

            # v8.2: CORNERS keep the aspect ratio; EDGES stretch a single axis
            if hd in ("nw", "ne", "se", "sw") and oh:
                new_h = new_w * (oh / ow)

            new_w = int(max(20, min(new_w, ow * 8)))
            new_h = int(max(20, min(new_h, oh * 8)))
            l["w"], l["h"] = new_w, new_h

            # the opposite edge / corner stays anchored
            if hd in ("w", "nw", "sw"):
                l["x"] = max(int(lx0 + (w0 - new_w)), 0)
            if hd in ("n", "nw", "ne"):
                l["y"] = max(int(ly0 + (h0 - new_h)), 0)

            self._fit_canvas()
            self.redraw()
            pct_w = new_w / ow * 100
            pct_h = new_h / oh * 100
            self.status.config(
                text=f"{l['name']} — {new_w}×{new_h}  "
                     f"({pct_w:.0f}% × {pct_h:.0f}%)   "
                     f"corners keep the aspect ratio; edges stretch one way")
            return

        if self.layer_drag is not None and self.layer_sel is not None:
            x, y = self.canvas_xy(e)
            ox, oy = self.layer_drag
            l = self.layers[self.layer_sel]
            l["x"] = max(int(x - ox), 0)
            l["y"] = max(int(y - oy), 0)
            self._expand_canvas()
            self.redraw()
            return

        if self.selected is not None and self._move_from:
            x, y = self.canvas_xy(e)
            dx, dy = x - self._move_from[0], y - self._move_from[1]
            s = self.shapes[self.selected]
            if s["t"] == "text":
                s["xy"] = (s["xy"][0] + dx, s["xy"][1] + dy)
            else:                                  # v9: move lines/arrows/shapes
                s["pts"] = [(px + dx, py + dy) for px, py in s["pts"]]
                s.pop("_bbox", None)
            self._move_from = (x, y)
            self.redraw()
            return
        if not self._drag:
            return
        x, y = self.canvas_xy(e)
        d = self._drag
        if d["t"] in ("pen", "highlight"):
            d["pts"].append((x, y))
        else:
            d["pts"] = [d["pts"][0], (x, y)]
        self.redraw(temp=d)

    def on_release(self, e):
        # v9.4: releasing a text-selection drag copies it right away
        if self.tool.get() == "ocr":
            self._ocr_anchor = None
            self._ocr_arming = False
            txt = self._ocr_selection_text()
            if txt:
                self._ocr_copy(txt, f"{len(self.ocr_sel)} word(s)")
            return
        # v9.1: finishing a crop drag — report the selected size
        if self.tool.get() == "crop" and self.crop_box is not None:
            x0, y0, x1, y1 = self.crop_box
            w, h = abs(int(x1 - x0)), abs(int(y1 - y0))
            self.status.config(
                text=f"Crop: {w}×{h} selected — Enter to apply, Esc to cancel.")
            return
        if self.layer_drag is not None or self.layer_resize is not None:   # v8
            self.layer_drag = None
            self.layer_resize = None
            self._fit_canvas(shrink=True)          # v8.2: reclaim the white space
            self.redraw()
            return
        self._move_from = None
        if not self._drag:
            return
        d = self._drag
        self._drag = None
        if len(d["pts"]) >= 2:
            self.shapes.append(d)
        self.redraw()

    def on_double(self, e):
        x, y = self.canvas_xy(e)
        # v9.5: double-click a word in text-select mode -> its whole LINE
        if self.tool.get() == "ocr" and self.ocr_words:
            i = self._ocr_word_near(x, y)
            if i is not None:
                ln = self.ocr_words[i]["line"]
                self.ocr_sel = {j for j, w in enumerate(self.ocr_words)
                                if w["line"] == ln}
                self.redraw()
                self._ocr_copy(self._ocr_selection_text(),
                               f"line ({len(self.ocr_sel)} word(s))")
            return
        hit = self._hit_text(x, y)
        if hit is None:
            return
        self.selected = hit
        self.edit_text_dialog(hit)

    # ================= v8.3: inline text (no modal, cannot hang) =================
    def _shape_menu(self):
        """Drop the shape list under the button."""
        try:
            x = self.shape_btn.winfo_rootx()
            y = self.shape_btn.winfo_rooty() + self.shape_btn.winfo_height()
            self.shape_m.tk_popup(x, y)
        finally:
            self.shape_m.grab_release()

    def _pick_shape(self, key):
        self.tool.set(key)
        label = dict(SHAPE_MENU).get(key, key)
        self.shape_btn.config(text=f"{label.split()[0]} ▾")
        self.status.config(
            text=f"{label.strip()} — drag on the image to draw it. "
                 f"Then ↖ Select to move it, Del to remove it.")

    # ================= v9: diagnostics =================
    def _log(self, msg):
        """Append a timestamped line to the log file (and stdout if there is one)."""
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        try:
            print(line, flush=True)              # teed to the log by _install_crash_log
        except Exception:
            pass

    def _state_report(self) -> str:
        """Everything I need to diagnose a problem, in one block."""
        L = []
        L.append(f"SnipAnnotate v{__version__}")
        L.append(f"python {sys.version.split()[0]}  {platform.system()} "
                 f"{platform.release()}")
        try:
            L.append(f"window   : {self.root.winfo_width()}x{self.root.winfo_height()}")
            L.append(f"canvas   : {self.cv.winfo_width()}x{self.cv.winfo_height()}")
        except Exception:
            pass
        L.append(f"zoom     : {getattr(self, 'zoom', '?')}")
        L.append(f"tool     : {self.tool.get()}")
        L.append(f"snips    : {len(self.snips)}  (current={self.current})")
        if self.image is not None:
            L.append(f"page     : {self.image.width}x{self.image.height}")
        else:
            L.append("page     : None")
        L.append(f"layers   : {len(self.layers)}")
        for i, l in enumerate(self.layers):
            im = self._layer_img(l)
            L.append(f"   [{i}] {l['name']:<12} x={int(l['x']):<5} y={int(l['y']):<5} "
                     f"{im.width}x{im.height}")
        L.append(f"shapes   : {len(self.shapes)}")
        # toolbar visibility — the bug that hid ✄ Trim for several versions
        try:
            ww = self.root.winfo_width()
            hidden = []
            def walk(w):
                for c in w.winfo_children():
                    if isinstance(c, tk.Button):
                        x = c.winfo_rootx() - self.root.winfo_rootx()
                        if c.winfo_width() <= 2 or x + c.winfo_width() > ww:
                            hidden.append(c.cget("text"))
                    walk(c)
            walk(self.root)
            L.append(f"clipped buttons: {hidden if hidden else 'none'}")
        except Exception:
            pass
        return "\n".join(L)

    def diagnostics(self):
        """🩺 — dump the full state to the log AND the clipboard, and show it."""
        rep = self._state_report()
        self._log("===== DIAGNOSTICS =====\n" + rep)
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(rep + f"\n\nlog: {LOG_PATH}")
        except Exception:
            pass
        win = tk.Toplevel(self.root)
        win.title(f"Diagnostics — v{__version__}")
        win.geometry("620x520")
        win.configure(bg="#0f172a")
        tk.Label(win, text="Copied to the clipboard — paste this to report a problem.",
                 bg="#0f172a", fg="#4ade80", anchor="w").pack(fill="x", padx=10, pady=6)
        t = tk.Text(win, bg="#0f172a", fg="#e2e8f0", relief="flat",
                    font=("Consolas", 10), padx=10, pady=8)
        t.pack(fill="both", expand=True, padx=10)
        t.insert("1.0", rep + f"\n\nlog file:\n{LOG_PATH}")
        tk.Button(win, text="Close", command=win.destroy, bg="#334155", fg="white",
                  relief="flat", padx=14, pady=4).pack(pady=8)

    def _trace(self, msg):
        """v8.5: breadcrumbs -> the log file, so a hard crash shows how far we got."""
        try:
            print(f"[text] {msg}", flush=True)
        except Exception:
            pass

    def _begin_text(self, ix, iy):
        """Put an Entry right where you clicked; Enter commits, Esc cancels."""
        try:
            self._begin_text_inner(ix, iy)
        except Exception as e:                     # v8.4: never take the app down
            self._cancel_text()
            self.tool.set("select")
            messagebox.showerror("Text tool", f"Could not start the text box:\n{e}")

    def _begin_text_inner(self, ix, iy):
        self._trace(f"begin at ({ix:.0f},{iy:.0f})")
        self._cancel_text()                        # drop any half-finished one
        z = getattr(self, "zoom", 1.0) or 1.0
        self._trace(f"zoom={z}")

        # v8.4: read the size defensively — a Spinbox can hold a non-integer, and
        # IntVar.get() then raises TclError.
        try:
            base = int(self.font_size.get())
        except Exception:
            base = 24
            self.font_size.set(24)
        fs = max(min(int(base * z * 0.8), 72), 8)  # clamp: no absurd widget fonts

        colour = self.color if isinstance(self.color, str) and self.color.startswith("#") \
            else "#ff0000"

        self._trace(f"font size={fs} colour={colour}")
        ent = tk.Entry(self.cv, bd=1, relief="solid", width=24)
        self._trace("Entry created")
        # apply the styling separately: if a font or colour is rejected on this
        # machine we still get a usable (plain) text box instead of a crash.
        for opt in ({"font": ("Segoe UI", fs)}, {"fg": colour},
                    {"bg": "#ffffff"}, {"insertbackground": colour}):
            try:
                ent.config(**opt)
            except Exception:
                pass

        win = self.cv.create_window(ix * z, iy * z, window=ent, anchor="nw")
        self._trace("placed on canvas")
        ent.bind("<Return>", lambda _e: self._commit_text())
        ent.bind("<KP_Enter>", lambda _e: self._commit_text())
        ent.bind("<Escape>", lambda _e: self._cancel_text())
        # v8.4: NO <FocusOut> binding. On Windows, focus_force() inside a click
        # handler fires FocusOut straight away — which committed and DESTROYED the
        # Entry while the very event that created it was still being dispatched,
        # crashing the app. Clicking elsewhere commits via on_press instead.
        self._text_entry = ent
        self._text_win = win
        self._text_at = (ix, iy)
        # defer the focus to the next idle slot, so it never lands inside the
        # button-press dispatch that created the widget
        self.root.after_idle(lambda: self._focus_text_entry(ent))
        self.status.config(text="Type the text, then press Enter  (Esc to cancel).")
        self._trace("ready")

    def _focus_text_entry(self, ent):
        try:
            if ent is self._text_entry and ent.winfo_exists():
                ent.focus_set()
                self._trace("focused")
        except Exception as e:
            self._trace(f"focus failed: {e}")

    def _commit_text(self):
        ent = self._text_entry
        if ent is None or getattr(self, "_text_busy", False):
            return
        self._text_busy = True                     # v8.4: guard re-entry
        try:
            txt = ent.get().strip() if ent.winfo_exists() else ""
        except Exception:
            txt = ""
        at = self._text_at
        self._cancel_text()                        # tears the widget down first
        self._text_busy = False
        if not txt or at is None:
            self.status.config(text="Text cancelled.")
            return
        self.shapes.append({"t": "text", "xy": at, "text": txt,
                            "color": self.color, "size": self.font_size.get()})
        self.selected = len(self.shapes) - 1       # auto-select the new text
        self.tool.set("select")                    # so it can be dragged at once
        self.redraw()
        self.status.config(text="Text placed — drag to move, double-click to edit, "
                                "Del to delete.")

    def _cancel_text(self):
        ent, win = self._text_entry, self._text_win
        self._text_entry = None                    # null FIRST: the FocusOut that
        self._text_win = None                      # destroy() fires must be a no-op
        self._text_at = None
        if win is not None:
            try:
                self.cv.delete(win)
            except Exception:
                pass
        if ent is not None:
            try:
                ent.destroy()
            except Exception:
                pass

    def edit_text_dialog(self, idx: int):
        s = self.shapes[idx]
        dlg = tk.Toplevel(self.root)
        dlg.title("Edit text")
        dlg.transient(self.root)
        dlg.grab_set()
        tk.Label(dlg, text="Text").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        ent = tk.Entry(dlg, width=42)
        ent.insert(0, s["text"])
        ent.grid(row=0, column=1, columnspan=2, padx=8, pady=6)
        ent.focus_set()
        tk.Label(dlg, text="Size").grid(row=1, column=0, sticky="w", padx=8)
        size_v = tk.IntVar(value=s["size"])
        tk.Spinbox(dlg, from_=8, to=96, width=4, textvariable=size_v)\
            .grid(row=1, column=1, sticky="w", padx=8)
        col = {"v": s["color"]}
        cbtn = tk.Button(dlg, text="Color", bg=col["v"], fg="white", relief="flat")

        def pick():
            c = colorchooser.askcolor(color=col["v"], parent=dlg)
            if c and c[1]:
                col["v"] = c[1]
                cbtn.config(bg=col["v"])
        cbtn.config(command=pick)
        cbtn.grid(row=1, column=2, sticky="w", padx=8)

        def ok(_=None):
            s["text"] = ent.get()
            s["size"] = size_v.get()
            s["color"] = col["v"]
            dlg.destroy()
            self.redraw()

        def delete():
            dlg.destroy()
            self.delete_selected()

        row = tk.Frame(dlg)
        row.grid(row=2, column=0, columnspan=3, pady=8)
        tk.Button(row, text="OK", width=8, command=ok).pack(side="left", padx=4)
        tk.Button(row, text="Delete", width=8, command=delete).pack(side="left", padx=4)
        tk.Button(row, text="Cancel", width=8, command=dlg.destroy).pack(side="left", padx=4)
        dlg.bind("<Return>", ok)
        dlg.bind("<Escape>", lambda e: dlg.destroy())

    def delete_selected(self):
        if self.selected is not None and 0 <= self.selected < len(self.shapes):
            self.shapes.pop(self.selected)
            self.selected = None
            self.redraw()

    def undo(self):
        if self.shapes:
            self.shapes.pop()
            if self.selected is not None and self.selected >= len(self.shapes):
                self.selected = None
            self.redraw()

    def clear(self):
        if self.shapes and messagebox.askyesno("Clear", "Remove all annotations?"):
            self.shapes.clear()
            self.redraw()

    # draw a shape onto the tk canvas
    def _draw_canvas_shape(self, s):
        t = s["t"]
        if t == "text":
            f = tkfont.Font(family="Segoe UI" if IS_WINDOWS else "DejaVu Sans",
                            size=s["size"], weight="bold")
            iid = self.cv.create_text(*s["xy"], text=s["text"], fill=s["color"],
                                      anchor="nw", font=f)
            s["_bbox"] = self.cv.bbox(iid)         # for hit-testing / selection box
            return
        pts, c, w = s["pts"], s["color"], s["w"]
        if t == "pen":
            self.cv.create_line(*[v for p in pts for v in p], fill=c, width=w,
                                capstyle="round", joinstyle="round", smooth=True)
        elif t == "highlight":
            self.cv.create_line(*[v for p in pts for v in p], fill=c,
                                width=max(w * 4, 12), capstyle="round",
                                joinstyle="round", smooth=True, stipple="gray50")
        elif t == "line":
            self.cv.create_line(*pts[0], *pts[1], fill=c, width=w)
        elif t == "arrow":
            self.cv.create_line(*pts[0], *pts[1], fill=c, width=w,
                                arrow="last", arrowshape=(4 * w, 5 * w, 2 * w))
        elif t == "darrow":                        # v9
            self.cv.create_line(*pts[0], *pts[1], fill=c, width=w,
                                arrow="both", arrowshape=(4 * w, 5 * w, 2 * w))
        elif t == "rect":
            self.cv.create_rectangle(*pts[0], *pts[1], outline=c, width=w)
        elif t == "ellipse":
            self.cv.create_oval(*pts[0], *pts[1], outline=c, width=w)
        elif t in POLY_SHAPES:                     # v9: the whole shape library
            p = shape_points(t, pts[0][0], pts[0][1], pts[1][0], pts[1][1])
            flat = [v for pt in p for v in pt]
            if t in ("check", "cross"):            # open strokes
                self.cv.create_line(*flat, fill=c, width=w,
                                    capstyle="round", joinstyle="round")
            else:
                self.cv.create_polygon(*flat, outline=c, width=w, fill="")

    def redraw(self, temp=None):
        self.cv.delete("all")
        if not self.image:
            return
        z = getattr(self, "zoom", 1.0) or 1.0
        if abs(z - 1.0) < 0.005:
            disp = self.image
        else:
            w = max(1, int(self.image.width * z))
            h = max(1, int(self.image.height * z))
            disp = self.image.resize((w, h), Image.LANCZOS)
        self._tkimg = ImageTk.PhotoImage(disp)
        self.cv.create_image(0, 0, anchor="nw", image=self._tkimg)
        self.cv.config(scrollregion=(0, 0, disp.width, disp.height))

        # v8: layers on top of the base image
        self._layer_tk = []
        for i, l in enumerate(self.layers):
            im = self._layer_img(l)
            if abs(z - 1.0) >= 0.005:
                im = im.resize((max(int(im.width * z), 1),
                                max(int(im.height * z), 1)), Image.LANCZOS)
            tkim = ImageTk.PhotoImage(im)
            self._layer_tk.append(tkim)
            lx, ly = l["x"] * z, l["y"] * z
            self.cv.create_image(lx, ly, anchor="nw", image=tkim)
            sel = (i == self.layer_sel)
            # v9.6: these overlay items are ALREADY in final canvas coords
            # (lx = l.x * z, im already resized) — tag them so the zoom pass
            # at the end of redraw does NOT scale them a second time. The
            # double-scaling put the box and handles at z² of the true
            # position, so at any zoom != 100% the box sat off the picture
            # and the resize handles were unreachable.
            self.cv.create_rectangle(lx, ly, lx + im.width, ly + im.height,
                                     outline="#38bdf8" if sel else "#94a3b8",
                                     width=2 if sel else 1,
                                     dash=() if sel else (4, 3),
                                     tags="layerui")
            if sel:
                # v8.2: EIGHT handles — corners (aspect) + edge midpoints (stretch)
                hs = 5
                w, h = im.width, im.height
                for hx, hy in ((lx, ly), (lx + w / 2, ly), (lx + w, ly),
                               (lx, ly + h / 2), (lx + w, ly + h / 2),
                               (lx, ly + h), (lx + w / 2, ly + h), (lx + w, ly + h)):
                    self.cv.create_rectangle(hx - hs, hy - hs, hx + hs, hy + hs,
                                             fill="#38bdf8", outline="#0f172a",
                                             tags="layerui")
                self.cv.create_text(lx + 4, max(ly - 9, 6), anchor="w",
                                    text=f"{l['name']}  {im.width}×{im.height}",
                                    fill="#38bdf8", font=("Segoe UI", 8, "bold"),
                                    tags="layerui")
        self.cv.scale("all", 0, 0, 1, 1)          # no-op; shapes scaled below
        for s in self.shapes:
            self._draw_canvas_shape(s)
        if temp:
            self._draw_canvas_shape(temp)
        if self.selected is not None and self.selected < len(self.shapes):
            bb = self.shapes[self.selected].get("_bbox")
            if bb:
                self.cv.create_rectangle(bb[0] - 4, bb[1] - 4, bb[2] + 4, bb[3] + 4,
                                         outline="#00b0ff", dash=(4, 3), width=2)
        # v9.4: OCR text-select overlay (image coords — zoom scaling applies)
        if self.tool.get() == "ocr" and self.ocr_words:
            for i, wd in enumerate(self.ocr_words):
                if i in self.ocr_sel:
                    self.cv.create_rectangle(wd["x0"], wd["y0"], wd["x1"], wd["y1"],
                                             fill="#38bdf8", stipple="gray50",
                                             outline="#38bdf8", width=1)
                else:
                    self.cv.create_rectangle(wd["x0"], wd["y0"], wd["x1"], wd["y1"],
                                             outline="#94a3b8", width=1)

        # v9.1: crop marquee (image coords — the zoom scaling below applies)
        if self.tool.get() == "crop" and self.crop_box is not None:
            cx0, cy0, cx1, cy1 = self.crop_box
            self.cv.create_rectangle(cx0, cy0, cx1, cy1,
                                     outline="#f59e0b", width=2, dash=(6, 4))
        # v3: shapes are stored in IMAGE coordinates; scale the canvas items
        # (not the image, which is already resized) so they line up when zoomed.
        if abs(z - 1.0) >= 0.005:
            for item in self.cv.find_all():
                if (self.cv.type(item) != "image"
                        and "layerui" not in self.cv.gettags(item)):
                    self.cv.scale(item, 0, 0, z, z)

        # v9.4: the [Copy all][Done] bar rides on the canvas; delete("all")
        # removed its window item, so re-create it (screen coords, no zoom).
        if self._ocr_bar is not None and self.tool.get() == "ocr":
            self.cv.create_window(self.cv.canvasx(8), self.cv.canvasy(8),
                                  anchor="nw", window=self._ocr_bar)

    # ---------------- export ----------------
    def _pil_font(self, size):
        candidates = (["segoeuib.ttf", "arialbd.ttf"] if IS_WINDOWS
                      else ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                            "/System/Library/Fonts/Helvetica.ttc"])
        for c in candidates:
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                continue
        return ImageFont.load_default()

    def render_snip(self, snip: dict) -> Image.Image:
        """v5: bake a SPECIFIC snip's annotations into its image (used by Compose)."""
        keep_img, keep_shapes = self.image, self.shapes
        try:
            self.image, self.shapes = snip["image"], snip["shapes"]
            return self.render()
        finally:
            self.image, self.shapes = keep_img, keep_shapes

    def render(self) -> Image.Image:
        """Burn LAYERS, then annotations, into a copy of the base image."""
        out = self.image.convert("RGBA")
        for l in self.layers:                      # v8: dropped snips
            im = self._layer_img(l)
            im = im.convert("RGBA")
            out.paste(im, (int(l["x"]), int(l["y"])), im)
        overlay = Image.new("RGBA", out.size, (0, 0, 0, 0))
        dr = ImageDraw.Draw(overlay)
        for s in self.shapes:
            t = s["t"]
            if t == "text":
                dr.text(s["xy"], s["text"], fill=s["color"],
                        font=self._pil_font(s["size"]))
                continue
            pts, c, w = s["pts"], s["color"], s["w"]
            if t == "pen":
                dr.line(pts, fill=c, width=w, joint="curve")
                for p in (pts[0], pts[-1]):
                    r = w / 2
                    dr.ellipse([p[0]-r, p[1]-r, p[0]+r, p[1]+r], fill=c)
            elif t == "highlight":
                rgb = tuple(int(c.lstrip("#")[i:i+2], 16) for i in (0, 2, 4))
                dr.line(pts, fill=rgb + (96,), width=max(w * 4, 12), joint="curve")
            elif t == "line":
                dr.line([pts[0], pts[1]], fill=c, width=w)
            elif t == "arrow":
                dr.line([pts[0], pts[1]], fill=c, width=w)
                self._arrow_head(dr, pts[0], pts[1], c, w)
            elif t == "darrow":                     # v9
                dr.line([pts[0], pts[1]], fill=c, width=w)
                self._arrow_head(dr, pts[1], pts[0], c, w)
                self._arrow_head(dr, pts[0], pts[1], c, w)
            elif t in POLY_SHAPES:                  # v9: the shape library
                p = shape_points(t, pts[0][0], pts[0][1], pts[1][0], pts[1][1])
                if t in ("check", "cross"):
                    dr.line(p, fill=c, width=w, joint="curve")
                else:
                    dr.polygon(p, outline=c)
                    dr.line(p + [p[0]], fill=c, width=w, joint="curve")
            elif t == "rect":
                dr.rectangle([pts[0], pts[1]], outline=c, width=w)
            elif t == "ellipse":
                x0, y0 = pts[0]; x1, y1 = pts[1]
                dr.ellipse([min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)],
                           outline=c, width=w)
        return Image.alpha_composite(out, overlay).convert("RGB")

    @staticmethod
    def _arrow_head(dr, p0, p1, color, w):
        import math
        ang = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
        ln = max(5 * w, 12)
        for da in (math.radians(155), math.radians(-155)):
            dr.line([p1, (p1[0] + ln * math.cos(ang + da),
                          p1[1] + ln * math.sin(ang + da))], fill=color, width=w)

    def save(self):
        if not self.image:
            return
        p = filedialog.asksaveasfilename(defaultextension=".png",
                                         initialfile=time.strftime("snip_%Y%m%d_%H%M%S.png"),
                                         filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg")])
        if not p:
            return
        img = self.render()
        img.save(p, quality=95)
        self.status.config(text=f"Saved: {p}")

    # ---------------- clean shutdown (v3) ----------------
    def _release_clipboard_ownership(self):
        """Make sure we are not the clipboard owner when the process exits.

        On Windows, EmptyClipboard() makes the calling process the clipboard
        OWNER. If that process then exits while still owning it, other apps can
        be left with a broken copy/paste until they restart. Windows renders any
        format we already supplied (we always call SetClipboardData with real
        CF_DIB bytes, never delayed rendering), so simply making sure no session
        is left open — and that we are not the owner at exit — is enough.
        """
        if not IS_WINDOWS:
            return
        try:
            import ctypes
            u32 = ctypes.windll.user32
            # If a clipboard session somehow remained open, close it.
            try:
                u32.CloseClipboard()
            except Exception:
                pass
            # If we still own the clipboard, hand ownership back by opening with
            # a NULL owner and closing without emptying it: the data stays, the
            # ownership is dropped.
            try:
                if u32.GetClipboardOwner() == u32.GetActiveWindow():
                    if u32.OpenClipboard(None):
                        u32.CloseClipboard()
            except Exception:
                pass
        except Exception:
            pass

    def _on_tk_error(self, exc, val, tb):
        """v8.4: never die silently — write a log and tell the user where it is."""
        import traceback as _tb
        text = "".join(_tb.format_exception(exc, val, tb))
        path = os.path.join(tempfile.gettempdir(), "snipannotate_error.log")
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n{text}")
        except Exception:
            pass
        try:
            self.status.config(text=f"Error: {val}   (logged to {path})")
        except Exception:
            pass
        messagebox.showerror(
            "Something went wrong",
            f"{exc.__name__}: {val}\n\n"
            f"The app is still running.\n\nDetails were written to:\n{path}")

    def on_close(self):
        self._release_clipboard_ownership()
        try:
            self.root.destroy()
        except Exception:
            pass

    # ================= v4: OCR (Windows built-in) =================
    def ocr_image(self):
        """v9.4: Snipping-Tool-style text actions — recognize the words IN the
        picture and let the user drag across them to select + copy, instead of
        dumping everything into a popup window."""
        if not self.image:
            messagebox.showinfo("OCR", "Take a snip first.")
            return
        # v9.3: OCR the FLATTENED page. self.image is only the base — in the
        # layer workflow the snips live in self.layers and the base is a blank
        # white page, so OCR always came back empty ("No text found").
        page = self.render()
        self._log(f"OCR: page={page.width}x{page.height} "
                  f"layers={len(self.layers)} shapes={len(self.shapes)}")
        try:
            words = _windows_ocr(page)
        except (ImportError, ModuleNotFoundError):     # v8.6: catch BOTH
            messagebox.showwarning(
                "OCR needs one more package",
                "Text extraction uses the OCR engine built into Windows — but the\n"
                "Python bridge to it is missing a piece.\n\n"
                "Install all of them once (copy this whole line):\n\n"
                "  pip install winrt-Windows.Foundation "
                "winrt-Windows.Foundation.Collections "
                "winrt-Windows.Graphics.Imaging "
                "winrt-Windows.Media.Ocr "
                "winrt-Windows.Storage.Streams\n\n"
                "Then restart the app. Nothing else is downloaded — the OCR engine\n"
                "itself is already part of Windows.")
            return
        except Exception as e:
            self._log(f"OCR failed: {e}")
            messagebox.showerror("OCR failed", str(e))
            return

        self._log(f"OCR: {len(words)} word(s) recognized")
        if not words:
            messagebox.showinfo("OCR", "No text found in this image.")
            return

        self.ocr_words = words
        self.ocr_sel = set()
        self._ocr_anchor = None
        self.selected = None
        self.layer_sel = None
        self.tool.set("ocr")
        self._ocr_show_bar()
        self.redraw()
        self.status.config(
            text=f"OCR: {len(words)} word(s) found — drag to select (copied on "
                 f"release), double-click = whole line, 📄 All text = everything. "
                 f"Esc or Done to exit.")

    # ---- v9.4: OCR text-select helpers ----
    def _ocr_show_bar(self):
        self._ocr_hide_bar()
        bar = tk.Frame(self.cv, bg="#1e293b")
        tk.Label(bar, text="Text select", bg="#1e293b", fg="#94a3b8",
                 padx=6).pack(side="left")
        tk.Button(bar, text="📋 Copy all", command=self._ocr_copy_all,
                  bg="#15803d", fg="white", relief="flat", padx=10,
                  pady=2).pack(side="left", padx=4, pady=3)
        tk.Button(bar, text="📄 All text", command=self._ocr_text_window,
                  bg="#7c3aed", fg="white", relief="flat", padx=10,
                  pady=2).pack(side="left", padx=(0, 4), pady=3)
        tk.Button(bar, text="✕ Done", command=lambda: self._ocr_exit(redraw=True),
                  bg="#334155", fg="white", relief="flat", padx=10,
                  pady=2).pack(side="left", padx=(0, 4), pady=3)
        self._ocr_bar = bar

    def _ocr_hide_bar(self):
        if self._ocr_bar is not None:
            try:
                self._ocr_bar.destroy()
            except Exception:
                pass
            self._ocr_bar = None

    def _ocr_exit(self, redraw=False):
        self.ocr_words = None
        self.ocr_sel = set()
        self._ocr_anchor = None
        self._ocr_hide_bar()
        if self.tool.get() == "ocr":
            self.tool.set("select")
        if redraw:
            self.redraw()
        self.status.config(text="Text select closed.")

    def _ocr_word_at(self, x, y, pad=3):
        """Index of the word whose box contains (x, y) in image coords.
        Exact hit wins; the padded box is only a fallback — otherwise a point
        between two adjacent words always resolved to the earlier one."""
        if not self.ocr_words:
            return None
        for i, w in enumerate(self.ocr_words):
            if w["x0"] <= x <= w["x1"] and w["y0"] <= y <= w["y1"]:
                return i
        for i, w in enumerate(self.ocr_words):
            if (w["x0"] - pad <= x <= w["x1"] + pad and
                    w["y0"] - pad <= y <= w["y1"] + pad):
                return i
        return None

    def _ocr_word_near(self, x, y, maxdist=30):
        """v9.5: like _ocr_word_at, but falls back to the NEAREST word within
        maxdist px — text selection must tolerate a press on the whitespace
        beside a word, not demand a pixel-exact hit."""
        i = self._ocr_word_at(x, y)
        if i is not None or not self.ocr_words:
            return i
        best, best_d = None, maxdist
        for j, w in enumerate(self.ocr_words):
            cx = min(max(x, w["x0"]), w["x1"])   # closest point of the box
            cy = min(max(y, w["y0"]), w["y1"])
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            if d < best_d:
                best, best_d = j, d
        return best

    def _ocr_all_text(self):
        """v9.5: the entire recognized text, line by line."""
        if not self.ocr_words:
            return ""
        lines = {}
        for w in self.ocr_words:
            lines.setdefault(w["line"], []).append(w["t"])
        return "\n".join(" ".join(lines[k]) for k in sorted(lines))

    def _ocr_text_window(self):
        """v9.5: the classic popup — ALL recognized text, editable, with
        Copy / Save as .txt. Brought back by request alongside in-picture select."""
        text = self._ocr_all_text()
        if not text:
            return
        win = tk.Toplevel(self.root)
        win.title("Extracted text — hiTech Snip & Annotate")
        win.geometry("720x480")
        win.configure(bg="#0f172a")
        tk.Label(win, text="All recognized text. Edit it here if you like.",
                 bg="#0f172a", fg="#94a3b8", anchor="w").pack(fill="x", padx=10, pady=(8, 4))
        txt = tk.Text(win, wrap="word", bg="#0f172a", fg="#e2e8f0",
                      insertbackground="#e2e8f0", relief="flat", padx=10, pady=8)
        txt.pack(fill="both", expand=True, padx=10)
        txt.insert("1.0", text)

        row = tk.Frame(win, bg="#0f172a")
        row.pack(fill="x", pady=8)

        def recopy():
            self._ocr_copy(txt.get("1.0", "end-1c"), "the edited text")

        def save_txt():
            p = filedialog.asksaveasfilename(defaultextension=".txt",
                                             filetypes=[("Text", "*.txt")])
            if p:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(txt.get("1.0", "end-1c"))
                self.status.config(text=f"Saved {p}")

        for label, cmd, bg in (("📋 Copy", recopy, "#15803d"),
                               ("💾 Save as .txt", save_txt, "#334155"),
                               ("Close", win.destroy, "#334155")):
            tk.Button(row, text=label, command=cmd, bg=bg, fg="white",
                      relief="flat", padx=12, pady=4).pack(side="left", padx=4)

    def _ocr_selection_text(self):
        """Selected words in reading order; same line joined by spaces."""
        if not self.ocr_sel:
            return ""
        idxs = sorted(self.ocr_sel)
        out, cur_line, cur = [], None, []
        for i in idxs:
            w = self.ocr_words[i]
            if w["line"] != cur_line and cur:
                out.append(" ".join(cur))
                cur = []
            cur_line = w["line"]
            cur.append(w["t"])
        if cur:
            out.append(" ".join(cur))
        return "\n".join(out)

    def _ocr_copy(self, text, what):
        if not text:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except Exception:
            pass
        self._log(f"OCR copy ({what}): {len(text)} chars")
        self.status.config(text=f"Copied {what} to the clipboard.")

    def _ocr_copy_all(self):
        self.ocr_sel = set(range(len(self.ocr_words or [])))
        self.redraw()
        self._ocr_copy(self._ocr_selection_text(),
                       f"all {len(self.ocr_sel)} word(s)")

    # ================= v4: crop =================
    def crop_start(self):
        self._log("CROP armed")
        if not self.image:
            messagebox.showinfo("Crop", "Take a snip first.")
            return
        self.tool.set("crop")
        self.status.config(text="Crop: drag a rectangle, then press Enter to apply "
                                "(Esc to cancel).")

    def _crop_confirm(self, _e=None):
        if self.tool.get() == "crop" and self.crop_box:
            box = tuple(self.crop_box)
            self.crop_box = None
            self.crop_apply(box)

    def _esc_pressed(self, _e=None):
        if self.tool.get() == "ocr" and self.ocr_words is not None:
            self._ocr_exit(redraw=True)
            return
        self._crop_cancel()

    def _crop_cancel(self, _e=None):
        if self.tool.get() == "crop":
            self.crop_box = None
            self.tool.set("select")
            self.redraw()
            self.status.config(text="Crop cancelled.")

    def crop_apply(self, box):
        """box in IMAGE coordinates."""
        x0, y0, x1, y1 = box
        x0, x1 = sorted((max(int(x0), 0), min(int(x1), self.image.width)))
        y0, y1 = sorted((max(int(y0), 0), min(int(y1), self.image.height)))
        if x1 - x0 < 5 or y1 - y0 < 5:
            self._log(f"CROP rejected (too small): box=({x0},{y0},{x1},{y1})")
            self.status.config(text="Crop cancelled — area too small.")
            return
        self._log(f"CROP applied: box=({x0},{y0},{x1},{y1}) "
                  f"page=({self.image.width},{self.image.height}) "
                  f"layers={len(self.layers)} shapes={len(self.shapes)}")
        # v8.8: render() bakes the LAYERS and the annotations into one bitmap —
        # so after cropping we must CLEAR both. Previously the layers were left
        # behind: they were pasted on top of the flattened crop a second time,
        # and Trim then recomputed the page size from their stale coordinates and
        # blew the canvas back up with white. That is why Trim "did nothing".
        flat = self.render()
        self.image = flat.crop((x0, y0, x1, y1))
        self.layers.clear()                       # <-- the bug
        self.shapes.clear()
        self.layer_sel = None
        self.selected = None
        if 0 <= self.current < len(self.snips):
            snip = self.snips[self.current]
            snip["image"] = self.image
            snip["layers"] = self.layers
            snip["shapes"] = self.shapes
            snip["is_page"] = True                # it is a flattened page now
        self.tool.set("select")
        self.zoom_fit()
        self.status.config(text=f"Cropped to {self.image.width}×{self.image.height}.")

    # ================= v4: share / paint / print =================
    def _temp_png(self) -> str:
        path = os.path.join(tempfile.gettempdir(),
                            f"hitech_snip_{int(time.time())}.png")
        self.render().save(path, "PNG")
        return path

    def share(self):
        """Open the Windows share sheet for the current image."""
        if not self.image:
            messagebox.showinfo("Share", "Take a snip first.")
            return
        path = self._temp_png()
        if not IS_WINDOWS:
            messagebox.showinfo("Share", f"Saved to:\n{path}")
            return
        try:
            # PowerShell drives the Windows share UI; the file is a real PNG on disk
            ps = (
                "Add-Type -AssemblyName System.Runtime.WindowsRuntime;"
                "$f=[Windows.Storage.StorageFile]::GetFileFromPathAsync('%s');"
                "Start-Process 'shell:AppsFolder\\Microsoft.Windows.Photos_8wekyb3d8bbwe!App' '%s'"
                % (path, path)
            )
            # Simplest reliable path: hand the file to the shell's "Open with / Share"
            subprocess.Popen(["rundll32.exe", "shell32.dll,OpenAs_RunDLL", path])
            self.status.config(text="Share / Open-with dialog opened.")
        except Exception as e:
            messagebox.showerror("Share failed", f"{e}\n\nThe image is at:\n{path}")

    def edit_in_paint(self):
        if not self.image:
            messagebox.showinfo("Edit in Paint", "Take a snip first.")
            return
        path = self._temp_png()
        try:
            if IS_WINDOWS:
                subprocess.Popen(["mspaint.exe", path])
            elif IS_MAC:
                subprocess.Popen(["open", "-a", "Preview", path])
            else:
                subprocess.Popen(["xdg-open", path])
            self.status.config(text="Opened in the system image editor.")
        except Exception as e:
            messagebox.showerror("Edit in Paint failed", f"{e}\n\nThe image is at:\n{path}")

    def print_image(self):
        """Windows print dialog. 'Microsoft Print to PDF' is one of the printers."""
        if not self.image:
            messagebox.showinfo("Print", "Take a snip first.")
            return
        path = self._temp_png()
        try:
            if IS_WINDOWS:
                os.startfile(path, "print")          # noqa: S606 — Windows print verb
                self.status.config(
                    text="Print dialog opened — choose “Microsoft Print to PDF” for a PDF.")
            else:
                subprocess.Popen(["lp", path])
                self.status.config(text="Sent to the default printer.")
        except Exception as e:
            messagebox.showerror("Print failed", f"{e}\n\nThe image is at:\n{path}")

    def copy_clipboard(self):
        if not self.image:
            return
        self.copy_image_to_clipboard(self.render())

    def copy_image_to_clipboard(self, img: "Image.Image"):
        """v5: copy ANY image (the editor's, or a composition) to the clipboard."""
        try:
            if IS_WINDOWS:
                # CF_DIB via BMP bytes (strip the 14-byte BMP file header).
                # v3 fix: declare 64-bit argtypes/restypes — without them
                # GlobalAlloc's HGLOBAL is truncated to 32 bits on x64 and the
                # subsequent GlobalLock/memmove access-violates; and ALWAYS
                # CloseClipboard in finally, or the whole OS clipboard stays
                # locked by this process.
                import ctypes
                from ctypes import wintypes
                buf = io.BytesIO()
                img.save(buf, "BMP")
                data = buf.getvalue()[14:]
                CF_DIB, GMEM_MOVEABLE = 8, 0x0002
                k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
                k32.GlobalAlloc.restype = wintypes.HGLOBAL
                k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
                k32.GlobalLock.restype = wintypes.LPVOID
                k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
                k32.GlobalUnlock.restype = wintypes.BOOL
                k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
                k32.GlobalFree.argtypes = [wintypes.HGLOBAL]
                u32.OpenClipboard.argtypes = [wintypes.HWND]
                u32.SetClipboardData.restype = wintypes.HANDLE
                u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]

                u32.GetClipboardOwner.restype = wintypes.HWND
                u32.GetActiveWindow.restype = wintypes.HWND
                u32.EmptyClipboard.restype = wintypes.BOOL
                u32.CloseClipboard.restype = wintypes.BOOL

                # v3: retry a few times — another app can hold the clipboard for
                # a moment, and a failed OpenClipboard used to leave us in a bad
                # state.
                opened = False
                for _ in range(5):
                    if u32.OpenClipboard(None):
                        opened = True
                        break
                    time.sleep(0.08)
                if not opened:
                    raise RuntimeError("clipboard is busy — try again")
                try:
                    u32.EmptyClipboard()
                    h = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
                    if not h:
                        raise RuntimeError("GlobalAlloc failed")
                    p = k32.GlobalLock(h)
                    if not p:
                        k32.GlobalFree(h)
                        raise RuntimeError("GlobalLock failed")
                    ctypes.memmove(p, data, len(data))
                    k32.GlobalUnlock(h)
                    if not u32.SetClipboardData(CF_DIB, h):
                        k32.GlobalFree(h)               # ownership NOT transferred on failure
                        raise RuntimeError("SetClipboardData failed")
                    # on success the clipboard owns h — do not free it
                finally:
                    u32.CloseClipboard()
            else:
                buf = io.BytesIO()
                img.save(buf, "PNG")
                for cmd in (["wl-copy", "-t", "image/png"],
                            ["xclip", "-selection", "clipboard", "-t", "image/png"]):
                    try:
                        subprocess.run(cmd, input=buf.getvalue(), check=True, timeout=10)
                        break
                    except Exception:
                        continue
                else:
                    raise RuntimeError("install xclip (X11) or wl-clipboard (Wayland)")
            self.status.config(text="Copied to clipboard.")
        except Exception as e:
            messagebox.showerror("Copy failed", str(e))

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
