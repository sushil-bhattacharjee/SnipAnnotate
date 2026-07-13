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

IS_WINDOWS = platform.system() == "Windows"
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


def get_monitors() -> list[dict]:
    """[{name, x, y, w, h, primary}] in virtual-screen coordinates."""
    if not IS_WINDOWS:
        return []
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    monitors = []

    MonitorEnumProc = ctypes.WINFUNCTYPE(
        ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
        ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def _cb(hmon, hdc, lprect, lparam):
        r = lprect.contents
        monitors.append({"x": r.left, "y": r.top,
                         "w": r.right - r.left, "h": r.bottom - r.top,
                         "primary": (r.left == 0 and r.top == 0)})
        return 1

    user32.EnumDisplayMonitors(0, 0, MonitorEnumProc(_cb), 0)
    # primary first, then left-to-right
    monitors.sort(key=lambda m: (not m["primary"], m["x"], m["y"]))
    for i, m in enumerate(monitors):
        m["name"] = f"Screen {i + 1}" + (" (primary)" if m["primary"] else "")
    return monitors


def virtual_origin() -> tuple[int, int]:
    """Top-left of the Windows virtual desktop (can be negative)."""
    if not IS_WINDOWS:
        return (0, 0)
    import ctypes
    SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
    u = ctypes.windll.user32
    return (u.GetSystemMetrics(SM_XVIRTUALSCREEN),
            u.GetSystemMetrics(SM_YVIRTUALSCREEN))


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

        disp = self.shot.resize((sw, sh))
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

TOOLS = ("pen", "highlight", "line", "arrow", "rect", "ellipse", "text")


class Recorder:
    """Threaded screen recorder -> temp MP4; converted at save time if needed."""

    FPS = 12

    def __init__(self, app, bbox_virtual: tuple, win_offset: tuple):
        """bbox_virtual: (x0,y0,x1,y1) in virtual-desktop *image* coords.
        win_offset: virtual origin (vx, vy) to translate to window coords."""
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
        self.writer = cv2.VideoWriter(
            self.tmp, cv2.VideoWriter_fourcc(*"mp4v"), self.FPS, self.size)
        self.frames = 0
        self._build_bar()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        self._tick()

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
        tk.Label(self.bar, text="⏺ REC", fg="#ef4444", bg="#111827",
                 font=("Segoe UI", 11, "bold")).pack(side="left", padx=(10, 4), pady=6)
        self.timer = tk.Label(self.bar, text="0:00", fg="#e5e7eb", bg="#111827",
                              font=("Consolas", 11))
        self.timer.pack(side="left", padx=4)
        tk.Button(self.bar, text="⏹ Stop", command=self.stop, bg="#ef4444",
                  fg="white", relief="flat", padx=12,
                  activebackground="#dc2626").pack(side="left", padx=10, pady=4)

    def _tick(self):
        if self.stop_flag.is_set():
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
        grab_box = self.bbox
        while not self.stop_flag.is_set():
            try:
                img = ImageGrab.grab(bbox=grab_box, all_screens=True) \
                    if IS_WINDOWS else ImageGrab.grab(bbox=grab_box)
                frame = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
                frame = frame[:self.size[1], :self.size[0]]
                cur = self._cursor_pos()
                if cur:
                    cx, cy = cur[0] - self.bbox[0], cur[1] - self.bbox[1]
                    if 0 <= cx < self.size[0] and 0 <= cy < self.size[1]:
                        cv2.circle(frame, (cx, cy), 9, (0, 210, 255), 2)
                        cv2.circle(frame, (cx, cy), 2, (0, 210, 255), -1)
                self.writer.write(frame)
                self.frames += 1
            except Exception:
                pass
            nxt += period
            delay = nxt - time.time()
            if delay > 0:
                time.sleep(delay)
            else:
                nxt = time.time()          # fell behind; resync

    def stop(self):
        self.stop_flag.set()
        self.thread.join(timeout=5)
        self.writer.release()
        self.bar.destroy()
        self.app.recording_done(self.tmp, self.frames, self.size)



class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Snip & Annotate")
        self.root.geometry("1100x750")
        self.zoom = 1.0                       # v3
        # v3: ALWAYS shut down cleanly. If the process dies while it still owns
        # the Windows clipboard (EmptyClipboard makes us the owner), other apps
        # can be left unable to copy/paste until they restart — which is exactly
        # what happened after running this tool.
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        import atexit
        atexit.register(self._release_clipboard_ownership)

        self.monitors = get_monitors()             # v2: multi-monitor support
        names = [m["name"] for m in self.monitors] + ["All screens"]
        self.screen_var = tk.StringVar(value=names[0] if self.monitors else "All screens")
        self._screen_names = names

        self.image: Image.Image | None = None      # base screenshot
        self.shapes: list[dict] = []               # annotation history (for undo/redraw)
        self.tool = tk.StringVar(value="pen")
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

        wrap = tk.Frame(self.root)
        wrap.pack(fill="both", expand=True)
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
        self.root.bind("<Delete>", lambda e: self.delete_selected())
        self.root.bind("<Control-z>", lambda e: self.undo())
        self.root.bind("<Control-s>", lambda e: self.save())
        self.root.bind("<Control-c>", lambda e: self.copy_clipboard())

        self.status = tk.Label(self.root, text="New Snip (region) or Full Screen to start.",
                               anchor="w", bg="#1e293b", fg="#e2e8f0")
        self.status.pack(fill="x")

    # ---------------- toolbar ----------------
    def _build_toolbar(self):
        # v3: TWO rows. Everything used to sit in one pack(side="left") row, so on
        # a narrower window the right-hand buttons (Highlight, Save, Copy…) were
        # simply cut off and you had to maximize to reach them.
        wrap = tk.Frame(self.root, bg="#0f172a")
        wrap.pack(fill="x")
        bar = tk.Frame(wrap, bg="#0f172a")      # row 1: capture + file
        bar.pack(fill="x")
        bar2 = tk.Frame(wrap, bg="#0f172a")     # row 2: tools + style + actions
        bar2.pack(fill="x")

        def btn(text, cmd, bg="#334155", parent=None):
            b = tk.Button(parent or bar, text=text, command=cmd, bg=bg, fg="white",
                          activebackground="#475569", activeforeground="white",
                          relief="flat", padx=10, pady=4)
            b.pack(side="left", padx=3, pady=4)
            return b

        btn("⬛ New Snip", self.new_snip, "#0e7c9e")
        btn("🖥 Full Screen", self.full_screen, "#0e7c9e")
        btn("⏺ Record", self.record_start, "#b91c1c")
        tk.Label(bar, text="Screen", bg="#0f172a", fg="#94a3b8").pack(side="left", padx=(6, 0))
        om = tk.OptionMenu(bar, self.screen_var, *self._screen_names)
        om.config(bg="#334155", fg="white", relief="flat",
                  activebackground="#475569", activeforeground="white",
                  highlightthickness=0)
        om.pack(side="left", padx=3, pady=4)
        btn("📂 Open…", self.open_file)

        # ---- row 2: drawing tools, style, actions ----
        for t, label in (("select", "↖ Select"),
                         ("pen", "✏ Pen"), ("highlight", "🖍 Highlight"),
                         ("line", "─ Line"), ("arrow", "➤ Arrow"),
                         ("rect", "▭ Rect"), ("ellipse", "◯ Ellipse"),
                         ("text", "T Text")):
            tk.Radiobutton(bar2, text=label, value=t, variable=self.tool,
                           indicatoron=False, bg="#334155", fg="white",
                           selectcolor="#0e7c9e", relief="flat",
                           padx=8, pady=4).pack(side="left", padx=2, pady=4)

        tk.Label(bar2, text=" | ", bg="#0f172a", fg="#64748b").pack(side="left")

        self.color_btn = tk.Button(bar2, text="Color", bg=self.color, fg="white",
                                   relief="flat", padx=10, command=self.pick_color)
        self.color_btn.pack(side="left", padx=3)
        tk.Label(bar2, text="Width", bg="#0f172a", fg="#94a3b8").pack(side="left")
        tk.Spinbox(bar2, from_=1, to=30, width=3, textvariable=self.width).pack(side="left", padx=2)
        tk.Label(bar2, text="Font", bg="#0f172a", fg="#94a3b8").pack(side="left")
        tk.Spinbox(bar2, from_=8, to=96, width=3, textvariable=self.font_size).pack(side="left", padx=2)

        tk.Label(bar2, text=" | ", bg="#0f172a", fg="#64748b").pack(side="left")
        btn("↩ Undo", self.undo, parent=bar2)
        btn("🗑 Clear", self.clear, parent=bar2)
        btn("💾 Save", self.save, "#15803d", parent=bar2)
        btn("📋 Copy", self.copy_clipboard, "#15803d", parent=bar2)

        # zoom controls (v3)
        tk.Label(bar2, text=" | ", bg="#0f172a", fg="#64748b").pack(side="left")
        btn("＋", self.zoom_in, parent=bar2)
        btn("－", self.zoom_out, parent=bar2)
        btn("⤢ Fit", self.zoom_fit, parent=bar2)
        self.zoom_lbl = tk.Label(bar2, text="100%", bg="#0f172a", fg="#94a3b8", width=6)
        self.zoom_lbl.pack(side="left")

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

    def _chosen_monitor(self) -> dict | None:
        """The selected monitor's geometry, or None for 'All screens'."""
        name = self.screen_var.get()
        for m in self.monitors:
            if m["name"] == name:
                return m
        return None

    def _grab_selected(self) -> tuple[Image.Image, dict | None]:
        """Grab the chosen screen (cropped from the virtual desktop) or all."""
        shot = grab_fullscreen()
        mon = self._chosen_monitor()
        if mon:
            vx, vy = virtual_origin()
            box = (mon["x"] - vx, mon["y"] - vy,
                   mon["x"] - vx + mon["w"], mon["y"] - vy + mon["h"])
            shot = shot.crop(box)
        return shot, mon

    def new_snip(self):
        self.root.withdraw()
        self.root.update_idletasks()
        time.sleep(0.25)                      # let the window disappear
        try:
            shot, mon = self._grab_selected()
        except Exception as e:
            self.root.deiconify()
            messagebox.showerror("Capture failed", str(e))
            return
        self.root.deiconify()
        geom = {"x": mon["x"], "y": mon["y"], "w": mon["w"], "h": mon["h"]} if mon else None
        sel = RegionSelector(self.root, shot, geom)
        if sel.result:
            self.set_image(sel.result)

    def full_screen(self):
        self.root.withdraw()
        self.root.update_idletasks()
        time.sleep(0.25)
        try:
            shot, _ = self._grab_selected()
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
        # freeze the chosen screen, let the user drag a region (Esc = whole screen)
        self.root.withdraw()
        self.root.update_idletasks()
        time.sleep(0.25)
        try:
            shot, mon = self._grab_selected()
        except Exception as e:
            self.root.deiconify()
            messagebox.showerror("Capture failed", str(e))
            return
        self.root.deiconify()
        geom = {"x": mon["x"], "y": mon["y"], "w": mon["w"], "h": mon["h"]} if mon else None
        sel = RegionSelector(self.root, shot, geom)
        vx, vy = virtual_origin()
        off = (mon["x"] - vx, mon["y"] - vy) if mon else (0, 0)
        if sel.box:
            b = sel.box
            bbox = (off[0] + b[0], off[1] + b[1], off[0] + b[2], off[1] + b[3])
        else:                                   # Esc / tiny drag -> whole screen
            bbox = (off[0], off[1], off[0] + shot.width, off[1] + shot.height)
        self.root.withdraw()                    # stay out of the recording
        self.recorder = Recorder(self, bbox, (vx, vy))
        self.status.config(text="Recording… use the floating ⏹ Stop bar.")

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

    # ---------------- zoom (v3) ----------------
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

    def set_image(self, img: Image.Image):
        self.image = img.convert("RGB")
        self.shapes.clear()
        self.selected = None
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

    def _hit_text(self, x, y) -> int | None:
        """Topmost text shape whose bbox contains (x, y), else None."""
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
        x, y = self.canvas_xy(e)
        t = self.tool.get()

        if t in ("select", "text"):
            hit = self._hit_text(x, y)
            if hit is not None:                    # select (and arm move)
                self.selected = hit
                self._move_from = (x, y)
                self.font_size.set(self.shapes[hit]["size"])
                self.redraw()
                self.status.config(text="Text selected — drag to move, "
                                        "double-click to edit, Del to delete.")
                return
            if t == "select":                      # clicked empty space
                if self.selected is not None:
                    self.selected = None
                    self.redraw()
                return
        elif self.selected is not None:            # drawing tool: drop selection
            self.selected = None
            self.redraw()

        if t == "text":
            txt = simpledialog.askstring("Text", "Text to place:", parent=self.root)
            if txt:
                self.shapes.append({"t": "text", "xy": (x, y), "text": txt,
                                    "color": self.color, "size": self.font_size.get()})
                self.selected = len(self.shapes) - 1   # auto-select the new text
                self.tool.set("select")                # so it can be dragged at once
                self.redraw()
                self.status.config(text="Text placed — drag to move, "
                                        "double-click to edit.")
            return
        self._drag = {"t": t, "pts": [(x, y)], "color": self.color,
                      "w": self.width.get()}

    def on_drag(self, e):
        if self.selected is not None and self._move_from:
            x, y = self.canvas_xy(e)
            dx, dy = x - self._move_from[0], y - self._move_from[1]
            s = self.shapes[self.selected]
            s["xy"] = (s["xy"][0] + dx, s["xy"][1] + dy)
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
        hit = self._hit_text(x, y)
        if hit is None:
            return
        self.selected = hit
        self.edit_text_dialog(hit)

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
        elif t == "rect":
            self.cv.create_rectangle(*pts[0], *pts[1], outline=c, width=w)
        elif t == "ellipse":
            self.cv.create_oval(*pts[0], *pts[1], outline=c, width=w)

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
        # v3: shapes are stored in IMAGE coordinates; scale the canvas items
        # (not the image, which is already resized) so they line up when zoomed.
        if abs(z - 1.0) >= 0.005:
            for item in self.cv.find_all():
                if self.cv.type(item) != "image":
                    self.cv.scale(item, 0, 0, z, z)

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

    def render(self) -> Image.Image:
        """Burn annotations into a copy of the base image (RGBA for highlighter)."""
        out = self.image.convert("RGBA")
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

    def on_close(self):
        self._release_clipboard_ownership()
        try:
            self.root.destroy()
        except Exception:
            pass

    def copy_clipboard(self):
        if not self.image:
            return
        img = self.render()
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
