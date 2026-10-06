#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
calibration_gui.py — Interactive wizard for calibrating the VRFB frame
processing pipeline.

Guides the user through:
  1. Folder selection (auto-picks calibration image ~30 min in)
  2. Rotation tuning for catholyte and anolyte tanks
  3. Interactive ROI drawing for each tank
  4. Threshold tuning with live three-panel preview
  5. Configuration summary with copyable rois dict
  6. Test batch on 6 evenly-distributed sample images
  7. Full batch processing with progress bar

Run with:  python gui/calibration_gui.py  (from project root)
"""

import csv
import datetime
import pathlib
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# Ensure project root is on sys.path so core.* imports work
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import cv2
from PIL import Image, ImageTk

from core.frames_to_volumes import (
    blur,
    contours_detection,
    cut_roi,
    edges_detection,
    find_pixel_length,
    rotate,
    threshold as apply_threshold,
)

# ── Constants ─────────────────────────────────────────────────────────────────

TANKS = ['catholyte', 'anolyte']
WIN_W, WIN_H = 1300, 830

# Fixed canvas dimensions
IMG_CW, IMG_CH   = 1220, 430   # rotation page: full-width image canvas
ROI_IMG_CW       = 840         # ROI page: left (image) canvas width
ROI_IMG_CH       = 410         # ROI page: left canvas height
ROI_PRV_CW       = 340         # ROI page: right (preview) canvas width
ROI_PRV_CH       = 410         # ROI page: right canvas height
PNL_W,  PNL_H   = 1200, 120   # threshold page: each panel (stacked vertically)

CALIB_MINUTES = 30

TANK_COLORS_BGR = {'catholyte': (0, 200, 0),   'anolyte': (0, 100, 255)}
TANK_COLORS_HEX = {'catholyte': '#00c853',      'anolyte': '#ff6d00'}

BG       = '#252526'
BG_DARK  = '#1e1e1e'
BG_HEAD  = '#1a1a2e'
FG       = '#cccccc'
FG_DIM   = '#888888'
FG_OK    = '#4caf50'
FG_WARN  = '#ff9800'
FG_ERR   = '#ef5350'
GRID     = "#e830d5"
AXIS     = "#ff0059"

# ── Module-level helpers ───────────────────────────────────────────────────────

def cv_to_photo(img, max_w: int, max_h: int):
    """
    Convert a cv2 image to a tkinter PhotoImage, scaled to fit inside
    max_w × max_h while preserving aspect ratio (centred).

    Returns (PhotoImage, scale, x_offset, y_offset).
    x_offset / y_offset are where the image's top-left sits inside the canvas.
    """
    if img is None:
        return None, 1.0, 0, 0
    if img.ndim == 2:
        rgb = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
    else:
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    scale = min(max_w / w, max_h / h, 1.0)
    nw = max(1, int(w * scale))
    nh = max(1, int(h * scale))
    if scale < 1.0:
        rgb = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_AREA)
    x_off = (max_w - nw) // 2
    y_off = (max_h - nh) // 2
    photo = ImageTk.PhotoImage(Image.fromarray(rgb))
    return photo, scale, x_off, y_off


def pick_calibration_image(images: list, minutes: int = CALIB_MINUTES) -> pathlib.Path:
    """Return the image closest to `minutes` from the first image's timestamp."""
    timed = []
    for p in images:
        try:
            t = datetime.datetime.strptime(p.stem, "%Y%m%d_%H%M%S")
            timed.append((p, t))
        except ValueError:
            pass
    if timed:
        timed.sort(key=lambda x: x[1])
        target = timed[0][1] + datetime.timedelta(minutes=minutes)
        return min(timed, key=lambda x: abs((x[1] - target).total_seconds()))[0]
    return images[len(images) // 2]  # fallback: middle image


def run_pipeline(img, angle, roi, thresh) -> dict:
    """
    Run the full detection pipeline on one image.
    Returns a dict with intermediate results and an 'error' key (None if OK).
    """
    r = dict(blurred=None, threshed=None, edges=None, bbox=None, height=None, error=None)
    try:
        rotated = rotate(img, angle)
        gray = cv2.cvtColor(rotated, cv2.COLOR_BGR2GRAY) if rotated.ndim == 3 else rotated
        roi_img = cut_roi(gray, roi)
        if roi_img.size == 0:
            r['error'] = 'ROI is empty'
            return r
        r['blurred']  = blur(roi_img)
        r['threshed'] = apply_threshold(r['blurred'], thresh)
        r['edges']    = edges_detection(r['threshed'])
        contours = contours_detection(r['edges'])
        if not contours:
            r['error'] = 'No contours detected'
            return r
        r['bbox'], r['height'] = find_pixel_length(contours)
    except Exception as exc:
        r['error'] = str(exc)
    return r


# ── Application ───────────────────────────────────────────────────────────────

class CalibrationApp:

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("VRFB Frame Calibration Tool")
        self.root.geometry(f"{WIN_W}x{WIN_H}")
        self.root.resizable(False, False)
        self.root.configure(bg=BG)

        # ── Experiment state ──────────────────────────────────────────────────
        self.folder: pathlib.Path | None = None
        self.images: list[pathlib.Path]  = []
        self.calib_img                   = None       # cv2 BGR ndarray

        self.angles     = {t: 0.0        for t in TANKS}
        self.roi_coords = {t: None       for t in TANKS}   # (x1, x2, y1, y2)
        self.thresholds = {t: (50, 255)  for t in TANKS}

        # PhotoImage refs — must stay alive to prevent GC blanking canvases
        self._refs: dict = {}

        # Cancel flag for batch thread
        self._cancel = threading.Event()

        # ── Wizard page sequence ──────────────────────────────────────────────
        self._pages = [('_pg_folder', None)]
        for tank in TANKS:
            self._pages += [
                ('_pg_rotation',  tank),
                ('_pg_roi',       tank),
                ('_pg_threshold', tank),
            ]
        self._pages += [('_pg_summary', None), ('_pg_test', None), ('_pg_run', None)]
        self._idx = 0

        self._build_chrome()
        self._show()

    # ── Chrome (header + content area + nav footer) ───────────────────────────

    def _build_chrome(self):
        # Header bar
        hdr = tk.Frame(self.root, bg=BG_HEAD, height=44)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        self._lbl_title = tk.Label(hdr, text="", bg=BG_HEAD, fg='white',
                                   font=('Helvetica', 12, 'bold'), anchor='w', padx=14)
        self._lbl_title.pack(side=tk.LEFT, fill=tk.Y)
        self._lbl_stepnum = tk.Label(hdr, text="", bg=BG_HEAD, fg=FG_DIM,
                                     font=('Helvetica', 10), anchor='e', padx=14)
        self._lbl_stepnum.pack(side=tk.RIGHT, fill=tk.Y)

        tk.Frame(self.root, bg='#333333', height=1).pack(fill=tk.X)

        # Scrollable content area
        self.content = tk.Frame(self.root, bg=BG)
        self.content.pack(fill=tk.BOTH, expand=True)

        tk.Frame(self.root, bg='#333333', height=1).pack(fill=tk.X)

        # Nav footer
        nav = tk.Frame(self.root, bg='#1e1e1e', height=100)
        nav.pack(fill=tk.X)
        nav.pack_propagate(False)
        self._btn_back = ttk.Button(nav, text="← Back", command=self._back, width=16)
        self._btn_back.pack(side=tk.LEFT, padx=20, pady=30)
        self._btn_next = ttk.Button(nav, text="Next →", command=self._next, width=16)
        self._btn_next.pack(side=tk.RIGHT, padx=20, pady=30)

    def _clear(self):
        self._refs.clear()
        for w in self.content.winfo_children():
            w.destroy()

    def _show(self):
        self._clear()
        name, arg = self._pages[self._idx]
        n = len(self._pages)

        step_labels = {
            '_pg_folder':    "Select Experiment Folder",
            '_pg_rotation':  f"{(arg or '').capitalize()} — Rotation",
            '_pg_roi':       f"{(arg or '').capitalize()} — Draw ROI",
            '_pg_threshold': f"{(arg or '').capitalize()} — Threshold Tuning",
            '_pg_summary':   "Configuration Summary",
            '_pg_test':      "Test on Sample Images",
            '_pg_run':       "Run Full Batch",
        }
        self._lbl_title.config(text=step_labels.get(name, name))
        self._lbl_stepnum.config(text=f"Step {self._idx + 1} / {n}")
        self._btn_back.config(state=tk.NORMAL if self._idx > 0 else tk.DISABLED)
        self._btn_next.config(text="Finish" if self._idx == n - 1 else "Next →")

        if arg is not None:
            getattr(self, name)(arg)
        else:
            getattr(self, name)()

    def _next(self):
        if self._validate():
            if self._idx < len(self._pages) - 1:
                self._idx += 1
                self._show()
            else:
                self.root.destroy()

    def _back(self):
        if self._idx > 0:
            self._idx -= 1
            self._show()

    def _validate(self) -> bool:
        name, arg = self._pages[self._idx]
        if name == '_pg_folder' and self.folder is None:
            messagebox.showwarning("No folder", "Please select an experiment folder first.")
            return False
        if name == '_pg_roi' and self.roi_coords[arg] is None:
            messagebox.showwarning("No ROI", "Please draw an ROI by clicking and dragging on the image.")
            return False
        return True

    # ── Shared helper ─────────────────────────────────────────────────────────

    def _put(self, canvas, img, key, cw, ch):
        """Display a cv2 image on a canvas; keep PhotoImage ref; return (scale, xo, yo)."""
        photo, scale, xo, yo = cv_to_photo(img, cw, ch)
        canvas.delete('all')
        canvas.create_image(xo, yo, anchor=tk.NW, image=photo)
        self._refs[key] = photo
        return scale, xo, yo

    # ── Page 0: Folder selection ───────────────────────────────────────────────

    def _pg_folder(self):
        pad = tk.Frame(self.content, bg=BG, padx=20, pady=14)
        pad.pack(fill=tk.BOTH, expand=True)

        # Folder picker row
        row = tk.Frame(pad, bg=BG)
        row.pack(fill=tk.X, pady=(0, 10))
        self._folder_var = tk.StringVar(value=str(self.folder) if self.folder else "")
        ttk.Entry(row, textvariable=self._folder_var, width=90).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(row, text="Browse…", command=self._browse_folder).pack(side=tk.LEFT)

        # Preview canvas
        c = tk.Canvas(pad, bg=BG_DARK, width=IMG_CW, height=IMG_CH, highlightthickness=0)
        c.pack()

        info_text = f"Will load the calibration image ~{CALIB_MINUTES} min into the experiment."
        tk.Label(pad, text=info_text, bg=BG, fg=FG_DIM, font=('Helvetica', 9)).pack(pady=4)

        if self.calib_img is not None:
            self._put(c, self.calib_img, 'folder_img', IMG_CW, IMG_CH)
            calib_name = pick_calibration_image(self.images).name
            info = f"{len(self.images)} images found   |   Calibration image: {calib_name}"
            tk.Label(pad, text=info, bg=BG, fg=FG_OK).pack()

    def _browse_folder(self):
        folder = filedialog.askdirectory(title="Select folder with experiment images")
        if not folder:
            return
        self.folder = pathlib.Path(folder)
        imgs = sorted(self.folder.glob("*.jpg")) + sorted(self.folder.glob("*.jpeg"))
        if not imgs:
            messagebox.showerror("No images", "No JPEG images found in the selected folder.")
            self.folder = None
            return
        self.images = imgs
        calib_path = pick_calibration_image(imgs)
        self.calib_img = cv2.imread(str(calib_path))
        if self.calib_img is None:
            messagebox.showerror("Load error", f"Could not open:\n{calib_path}")
            self.folder = None
            return
        self._show()  # refresh with preview

    # ── Page: Rotation ────────────────────────────────────────────────────────

    def _pg_rotation(self, tank: str):
        color = TANK_COLORS_HEX[tank]

        canvas = tk.Canvas(self.content, bg=BG_DARK, width=IMG_CW, height=IMG_CH,
                           highlightthickness=0)
        canvas.pack(pady=(8, 2))

        # Controls row
        ctrl = tk.Frame(self.content, bg=BG)
        ctrl.pack(fill=tk.X, padx=12, pady=4)

        tk.Label(ctrl, text="Rotation angle:", bg=BG, fg=FG).pack(side=tk.LEFT, padx=(0, 6))
        angle_var = tk.DoubleVar(value=self.angles[tank])
        lbl_angle = tk.Label(ctrl, text=f"{self.angles[tank]:+.2f}°",
                             bg=BG, fg=color, font=('Courier', 11, 'bold'), width=8)
        lbl_angle.pack(side=tk.LEFT)

        def nudge(delta):
            angle_var.set(round(angle_var.get() + delta, 2))

        ttk.Button(ctrl, text="−0.1°", width=6, command=lambda: nudge(-0.1)).pack(side=tk.LEFT, padx=2)
        ttk.Button(ctrl, text="+0.1°", width=6, command=lambda: nudge(+0.1)).pack(side=tk.LEFT, padx=2)

        slider = tk.Scale(ctrl, from_=-20, to=20, variable=angle_var,
                          orient=tk.HORIZONTAL, resolution=0.1, length=700,
                          bg=BG, fg=FG, troughcolor='#3a3a3a',
                          highlightthickness=0, showvalue=False)
        slider.pack(side=tk.LEFT, padx=8)
        ttk.Button(ctrl, text="Reset", command=lambda: angle_var.set(0.0)).pack(side=tk.RIGHT, padx=4)

        def redraw(*_):
            angle = angle_var.get()
            self.angles[tank] = angle
            lbl_angle.config(text=f"{angle:+.2f}°")
            img = rotate(self.calib_img, angle)
            photo, _, xo, yo = cv_to_photo(img, IMG_CW, IMG_CH)
            canvas.delete('all')
            canvas.create_image(xo, yo, anchor=tk.NW, image=photo)
            self._refs['rot_img'] = photo
            # Dim grid
            STEP = 80
            for x in range(0, IMG_CW + 1, STEP):
                canvas.create_line(x, 0, x, IMG_CH, fill=GRID, dash=(3, 9), width=1)
            for y in range(0, IMG_CH + 1, STEP):
                canvas.create_line(0, y, IMG_CW, y, fill=GRID, dash=(3, 9), width=1)
            # Bright centre cross
            cx, cy = IMG_CW // 2, IMG_CH // 2
            canvas.create_line(cx, 0, cx, IMG_CH, fill=AXIS, dash=(8, 5), width=2)
            canvas.create_line(0, cy, IMG_CW, cy, fill=AXIS, dash=(8, 5), width=2)

        angle_var.trace_add('write', redraw)
        self.content.after(50, redraw)

    # ── Page: ROI ────────────────────────────────────────────────────────────

    def _pg_roi(self, tank: str):
        color_hex = TANK_COLORS_HEX[tank]
        color_bgr = TANK_COLORS_BGR[tank]

        body = tk.Frame(self.content, bg=BG)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # Left: image canvas
        lf = ttk.LabelFrame(body, text=f"Rotated image (angle={self.angles[tank]:+.2f}°) — drag to draw ROI")
        lf.pack(side=tk.LEFT, fill=tk.BOTH)
        canvas = tk.Canvas(lf, bg=BG_DARK, width=ROI_IMG_CW, height=ROI_IMG_CH,
                           cursor='crosshair', highlightthickness=0)
        canvas.pack()

        # Right: preview panel
        rf = ttk.LabelFrame(body, text="ROI preview")
        rf.pack(side=tk.RIGHT, fill=tk.Y, padx=(8, 0))
        roi_canvas = tk.Canvas(rf, bg=BG_DARK, width=ROI_PRV_CW, height=ROI_PRV_CH,
                               highlightthickness=0)
        roi_canvas.pack()
        lbl_coords = tk.Label(rf, text="No ROI selected", bg=BG, fg=FG_DIM,
                              font=('Courier', 9))
        lbl_coords.pack(pady=4)
        ttk.Button(rf, text="Clear ROI", command=lambda: _clear_roi()).pack(pady=2)

        # Prepare the rotated image once
        rotated = rotate(self.calib_img, self.angles[tank])

        # Mutable state for scale/offset (needed in mouse callbacks)
        state = {'scale': 1.0, 'xo': 0, 'yo': 0}

        def draw_main():
            img_vis = rotated.copy()
            rc = self.roi_coords[tank]
            if rc:
                x1, x2, y1, y2 = rc
                cv2.rectangle(img_vis, (x1, y1), (x2, y2), color_bgr, 2)
                cv2.putText(img_vis, tank, (x1, max(y1 - 6, 14)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, color_bgr, 2)
            photo, s, xo, yo = cv_to_photo(img_vis, ROI_IMG_CW, ROI_IMG_CH)
            canvas.delete('img')
            canvas.create_image(xo, yo, anchor=tk.NW, image=photo, tags='img')
            self._refs['roi_main'] = photo
            state['scale'], state['xo'], state['yo'] = s, xo, yo

        def draw_preview():
            rc = self.roi_coords[tank]
            if rc is None:
                return
            x1, x2, y1, y2 = rc
            gray = cv2.cvtColor(rotated, cv2.COLOR_BGR2GRAY)
            roi_img = cut_roi(gray, rc)
            if roi_img.size == 0:
                return
            photo, _, _, _ = cv_to_photo(roi_img, ROI_PRV_CW, ROI_PRV_CH - 40)
            roi_canvas.delete('all')
            roi_canvas.create_image(ROI_PRV_CW // 2, (ROI_PRV_CH - 40) // 2,
                                    anchor=tk.CENTER, image=photo)
            self._refs['roi_prev'] = photo
            lbl_coords.config(
                text=f"x: {x1}–{x2}  ({x2 - x1} px)\ny: {y1}–{y2}  ({y2 - y1} px)",
                fg=color_hex,
            )

        def _clear_roi():
            self.roi_coords[tank] = None
            canvas.delete('roi_rect')
            roi_canvas.delete('all')
            lbl_coords.config(text="No ROI selected", fg=FG_DIM)
            draw_main()

        def canvas_to_img(cx, cy):
            s, xo, yo = state['scale'], state['xo'], state['yo']
            ih, iw = rotated.shape[:2]
            ix = int(max(0, min((cx - xo) / s, iw - 1)))
            iy = int(max(0, min((cy - yo) / s, ih - 1)))
            return ix, iy

        # Mouse drag
        drag = {'start': None}

        def on_press(e):
            drag['start'] = (e.x, e.y)
            canvas.delete('roi_rect')

        def on_drag(e):
            if drag['start'] is None:
                return
            x0, y0 = drag['start']
            canvas.delete('roi_rect')
            canvas.create_rectangle(x0, y0, e.x, e.y,
                                    outline=color_hex, width=2, tags='roi_rect')

        def on_release(e):
            if drag['start'] is None:
                return
            ix0, iy0 = canvas_to_img(*drag['start'])
            ix1, iy1 = canvas_to_img(e.x, e.y)
            x1, x2 = min(ix0, ix1), max(ix0, ix1)
            y1, y2 = min(iy0, iy1), max(iy0, iy1)
            if x2 - x1 < 5 or y2 - y1 < 5:
                return
            self.roi_coords[tank] = (x1, x2, y1, y2)
            draw_main()
            draw_preview()

        canvas.bind('<ButtonPress-1>',   on_press)
        canvas.bind('<B1-Motion>',        on_drag)
        canvas.bind('<ButtonRelease-1>', on_release)

        self.content.after(50, draw_main)
        if self.roi_coords[tank]:
            self.content.after(60, draw_preview)

    # ── Page: Threshold ───────────────────────────────────────────────────────

    def _pg_threshold(self, tank: str):
        color_hex = TANK_COLORS_HEX[tank]

        tk.Label(self.content, bg=BG, fg=FG_DIM,
                 text="Adjust thresholds until the liquid surface is clearly detected.") \
            .pack(anchor=tk.W, padx=12, pady=(6, 2))

        # Three panels stacked vertically
        panels = tk.Frame(self.content, bg=BG)
        panels.pack(fill=tk.X, padx=8, pady=2)

        def make_panel(label):
            f = ttk.LabelFrame(panels, text=label)
            f.pack(fill=tk.X, pady=2)
            c = tk.Canvas(f, bg=BG_DARK, width=PNL_W, height=PNL_H, highlightthickness=0)
            c.pack()
            return c

        c_blur   = make_panel("ROI (blurred)")
        c_thresh = make_panel("Thresholded")
        c_edges  = make_panel("Edges + detection")

        # Sliders
        sliders_f = ttk.LabelFrame(self.content, text="Threshold values  (only the min value matters for THRESH_BINARY)", padding=6)
        sliders_f.pack(fill=tk.X, padx=12, pady=4)

        mn0, mx0 = self.thresholds[tank]
        var_min = tk.IntVar(value=mn0)
        var_max = tk.IntVar(value=mx0)

        def make_row(parent, label, var):
            row = tk.Frame(parent, bg=BG)
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=label, bg=BG, fg=FG, width=5).pack(side=tk.LEFT)
            tk.Label(row, textvariable=var, bg=BG, fg=color_hex,
                     font=('Courier', 10), width=4).pack(side=tk.LEFT, padx=(0, 4))
            tk.Scale(row, from_=0, to=255, variable=var, orient=tk.HORIZONTAL,
                     resolution=1, length=800, bg=BG, fg=FG,
                     troughcolor='#3a3a3a', highlightthickness=0, showvalue=False) \
                .pack(side=tk.LEFT, fill=tk.X, expand=True)

        make_row(sliders_f, "Min:", var_min)
        make_row(sliders_f, "Max:", var_max)

        lbl_status = tk.Label(self.content, bg=BG, fg=FG_DIM, font=('Courier', 10))
        lbl_status.pack()

        def update(*_):
            mn, mx = var_min.get(), var_max.get()
            self.thresholds[tank] = (mn, mx)
            rc = self.roi_coords[tank]
            res = run_pipeline(self.calib_img, self.angles[tank], rc, (mn, mx))

            if res['blurred'] is not None:
                # Show blurred ROI with detected bbox overlaid in cyan
                blur_vis = cv2.cvtColor(res['blurred'], cv2.COLOR_GRAY2BGR)
                if res['bbox']:
                    bx, by, bw, bh = res['bbox']
                    cv2.rectangle(blur_vis, (bx, by), (bx + bw, by + bh), (0, 0, 255), 2)
                    cv2.putText(blur_vis, f"{res['height']} px", (bx, max(by - 5, 12)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
                self._put(c_blur, blur_vis, 'thr_blur', PNL_W, PNL_H)
            if res['threshed'] is not None:
                self._put(c_thresh, res['threshed'], 'thr_bin', PNL_W, PNL_H)

            if res['edges'] is not None:
                edge_vis = cv2.cvtColor(res['edges'], cv2.COLOR_GRAY2BGR)
                if res['bbox']:
                    x, y, w, h = res['bbox']
                    cv2.rectangle(edge_vis, (x, y), (x + w, y + h), (0, 255, 0), 2)
                    lbl_status.config(text=f"✓ Detected height: {res['height']} px", fg=FG_OK)
                elif res['error']:
                    lbl_status.config(text=f"✗ {res['error']}", fg=FG_ERR)
                self._put(c_edges, edge_vis, 'thr_edges', PNL_W, PNL_H)
            else:
                if res['error']:
                    lbl_status.config(text=f"✗ {res['error']}", fg=FG_ERR)

            if mn >= mx:
                lbl_status.config(text="⚠ Min must be less than Max", fg=FG_WARN)

        var_min.trace_add('write', update)
        var_max.trace_add('write', update)
        self.content.after(60, update)

    # ── Page: Summary ─────────────────────────────────────────────────────────

    def _pg_summary(self):
        rois = self._build_rois()

        tk.Label(self.content, bg=BG, fg=FG_DIM,
                 text="Copy the rois dict and paste it where needed, or proceed to test the parameters.") \
            .pack(anchor=tk.W, padx=12, pady=(6, 2))

        # Dict text box
        txt = tk.Text(self.content, font=('Courier', 10), height=11,
                      bg=BG_DARK, fg='#d4d4d4', insertbackground='white',
                      relief=tk.FLAT, padx=8, pady=6)
        txt.pack(fill=tk.X, padx=12, pady=(0, 2))

        rois_str = "rois = {\n"
        for tank in TANKS:
            if tank in rois:
                p = rois[tank]
                rois_str += f"    '{tank}': {{\n"
                rois_str += f"        'angle':      {p['angle']:.2f},\n"
                rois_str += f"        'roi':        {p['roi']},\n"
                rois_str += f"        'thresholds': {p['thresholds']},\n"
                rois_str += f"    }},\n"
        rois_str += "}"
        txt.insert('1.0', rois_str)
        txt.config(state=tk.DISABLED)

        def copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(rois_str)
            messagebox.showinfo("Copied", "rois dict copied to clipboard.")

        ttk.Button(self.content, text="Copy to Clipboard", command=copy) \
            .pack(anchor=tk.W, padx=12, pady=(0, 6))

        # Preview thumbnails with ROI rectangles
        prev = tk.Frame(self.content, bg=BG)
        prev.pack(fill=tk.X, padx=12)
        PW, PH = (WIN_W - 60) // 2, 200

        for tank in TANKS:
            if tank not in rois:
                continue
            p = rois[tank]
            img_vis = rotate(self.calib_img, p['angle']).copy()
            x1, x2, y1, y2 = p['roi']
            clr = TANK_COLORS_BGR[tank]
            cv2.rectangle(img_vis, (x1, y1), (x2, y2), clr, 2)
            cv2.putText(img_vis, tank, (x1, max(y1 - 6, 14)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, clr, 2)
            frame = ttk.LabelFrame(prev, text=tank.capitalize())
            frame.pack(side=tk.LEFT, padx=6)
            photo, _, _, _ = cv_to_photo(img_vis, PW, PH)
            tk.Label(frame, image=photo, bg=BG_DARK).pack()
            self._refs[f'sum_{tank}'] = photo

    def _build_rois(self) -> dict:
        return {
            tank: {
                'angle':      self.angles[tank],
                'roi':        self.roi_coords[tank],
                'thresholds': self.thresholds[tank],
            }
            for tank in TANKS
            if self.roi_coords[tank] is not None
        }

    # ── Page: Test batch ──────────────────────────────────────────────────────

    def _pg_test(self):
        N = 6
        n = min(N, len(self.images))
        indices = [int(i * (len(self.images) - 1) / max(n - 1, 1)) for i in range(n)]
        samples = [self.images[i] for i in indices]
        rois = self._build_rois()

        tk.Label(self.content, bg=BG, fg=FG_DIM,
                 text=f"Running pipeline on {n} evenly-distributed sample images…") \
            .pack(anchor=tk.W, padx=12, pady=(6, 2))

        # Scrollable grid
        outer = tk.Frame(self.content, bg=BG)
        outer.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        vsb = ttk.Scrollbar(outer, orient=tk.VERTICAL)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb = ttk.Scrollbar(outer, orient=tk.HORIZONTAL)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        scr = tk.Canvas(outer, bg=BG_DARK, yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        scr.pack(fill=tk.BOTH, expand=True)
        vsb.config(command=scr.yview)
        hsb.config(command=scr.xview)
        inner = tk.Frame(scr, bg=BG_DARK)
        scr.create_window((0, 0), window=inner, anchor=tk.NW)

        lbl_status = tk.Label(self.content, bg=BG, fg=FG_DIM, text="Processing…")
        lbl_status.pack()

        TW, TH = 380, 260

        def process():
            for col, img_path in enumerate(samples):
                frame = cv2.imread(str(img_path))
                if frame is None:
                    continue
                out = frame.copy()
                heights = []
                for tank, params in rois.items():
                    res = run_pipeline(frame, params['angle'], params['roi'], params['thresholds'])
                    clr = TANK_COLORS_BGR[tank]
                    if res['bbox']:
                        x, y, w, h = res['bbox']
                        x1, x2, y1, y2 = params['roi']
                        rot = rotate(out, params['angle'])
                        cv2.rectangle(rot, (x + x1, y + y1), (x + x1 + w, y + y1 + h), clr, 2)
                        out = rotate(rot, -params['angle'])
                        heights.append(f"{tank[:3]}:{res['height']}px")
                photo, _, _, _ = cv_to_photo(out, TW, TH)
                caption = f"{img_path.name}\n{' | '.join(heights) or '— no detection —'}"

                def add_cell(photo=photo, caption=caption, col=col):
                    cell = tk.Frame(inner, bg=BG, padx=4, pady=4)
                    cell.grid(row=0, column=col, padx=4, pady=4)
                    tk.Label(cell, image=photo, bg=BG_DARK).pack()
                    tk.Label(cell, text=caption, bg=BG, fg='#aaaaaa',
                             font=('Courier', 8), justify=tk.CENTER).pack()
                    self._refs[f'test_{col}'] = photo
                    inner.update_idletasks()
                    scr.config(scrollregion=scr.bbox('all'))

                self.root.after(0, add_cell)

            self.root.after(0, lambda: lbl_status.config(
                text="Done. Review the detections above.", fg=FG_OK))

        threading.Thread(target=process, daemon=True).start()

    # ── Page: Full batch ──────────────────────────────────────────────────────

    def _pg_run(self):
        rois = self._build_rois()
        pad = tk.Frame(self.content, bg=BG, padx=20, pady=12)
        pad.pack(fill=tk.BOTH, expand=True)

        # Config info
        info = ttk.LabelFrame(pad, text="Configuration", padding=8)
        info.pack(fill=tk.X, pady=(0, 8))
        for line in [
            f"Input folder:  {self.folder}",
            f"Total images:  {len(self.images)}",
        ] + [
            f"  {tank}: angle={p['angle']:.2f}°  roi={p['roi']}  thresh={p['thresholds']}"
            for tank, p in rois.items()
        ]:
            tk.Label(info, text=line, anchor='w', font=('Courier', 9)).pack(fill=tk.X)

        # Output paths
        default_out = self.folder.parent / (self.folder.name + "_processed")
        default_csv = self.folder.parent / "pixel_heights.csv"

        def path_row(label, default, is_dir=False):
            row = tk.Frame(pad, bg=BG)
            row.pack(fill=tk.X, pady=3)
            tk.Label(row, text=label, bg=BG, fg=FG, width=14).pack(side=tk.LEFT)
            var = tk.StringVar(value=str(default))
            ttk.Entry(row, textvariable=var, width=70).pack(side=tk.LEFT, padx=4)
            if is_dir:
                browse = lambda: var.set(filedialog.askdirectory() or var.get())
            else:
                browse = lambda: var.set(
                    filedialog.asksaveasfilename(defaultextension=".csv",
                                                filetypes=[("CSV", "*.csv")]) or var.get())
            ttk.Button(row, text="…", width=3, command=browse).pack(side=tk.LEFT)
            return var

        var_out = path_row("Output folder:", default_out, is_dir=True)
        var_csv = path_row("Results CSV:",   default_csv, is_dir=False)

        # Progress
        prog_var = tk.DoubleVar(value=0)
        ttk.Progressbar(pad, variable=prog_var, maximum=100, length=700) \
            .pack(pady=8)
        lbl_prog = tk.Label(pad, text="Ready.", bg=BG, fg=FG_DIM, font=('Courier', 10))
        lbl_prog.pack()

        btn_row = tk.Frame(pad, bg=BG)
        btn_row.pack(pady=8)
        btn_run    = ttk.Button(btn_row, text="▶  Start Batch Processing", width=26)
        btn_run.pack(side=tk.LEFT, padx=8)
        btn_cancel = ttk.Button(btn_row, text="✕ Cancel", width=10, state=tk.DISABLED)
        btn_cancel.pack(side=tk.LEFT)

        self._cancel.clear()

        def run():
            btn_run.config(state=tk.DISABLED)
            btn_cancel.config(state=tk.NORMAL)
            out_path = pathlib.Path(var_out.get())
            csv_path = pathlib.Path(var_csv.get())

            def task():
                try:
                    out_path.mkdir(parents=True, exist_ok=True)
                    total = len(self.images)
                    rows = []
                    for i, img_path in enumerate(self.images):
                        if self._cancel.is_set():
                            break
                        frame = cv2.imread(str(img_path))
                        row = [img_path.name]
                        for tank in TANKS:
                            if frame is not None and tank in rois:
                                p = rois[tank]
                                res = run_pipeline(frame, p['angle'], p['roi'], p['thresholds'])
                                row.append(res['height'] if res['height'] is not None else '')
                            else:
                                row.append('')
                        rows.append(row)
                        pct  = (i + 1) / total * 100
                        name = img_path.name
                        self.root.after(0, lambda p=pct, n=name: (
                            prog_var.set(p),
                            lbl_prog.config(text=f"{int(p)}%  —  {n}"),
                        ))

                    # Normalize volume columns by first valid value
                    for col_idx, tank in enumerate(TANKS, start=1):
                        first = next(
                            (r[col_idx] for r in rows if r[col_idx] != ''),
                            None,
                        )
                        if first:
                            for r in rows:
                                if r[col_idx] != '':
                                    r[col_idx] = r[col_idx] / first

                    with open(csv_path, 'w', newline='') as f:
                        writer = csv.writer(f)
                        writer.writerow(["filename", "catholyte", "anolyte"])
                        writer.writerows(rows)

                    if self._cancel.is_set():
                        self.root.after(0, lambda: lbl_prog.config(
                            text="Cancelled.", fg=FG_WARN))
                    else:
                        self.root.after(0, lambda: (
                            lbl_prog.config(text=f"Done! → {csv_path}", fg=FG_OK),
                            messagebox.showinfo("Complete",
                                                f"Batch processing complete.\n\nResults saved to:\n{csv_path}"),
                        ))
                except Exception as exc:
                    self.root.after(0, lambda: (
                        lbl_prog.config(text=f"Error: {exc}", fg=FG_ERR),
                        messagebox.showerror("Error", str(exc)),
                    ))
                finally:
                    self.root.after(0, lambda: (
                        btn_run.config(state=tk.NORMAL),
                        btn_cancel.config(state=tk.DISABLED),
                    ))

            threading.Thread(target=task, daemon=True).start()

        btn_run.config(command=run)
        btn_cancel.config(command=lambda: self._cancel.set())


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    # Tell Windows this process is DPI-aware so it renders crisp on high-DPI screens
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    root = tk.Tk()
    try:
        ttk.Style(root).theme_use('clam')
    except tk.TclError:
        pass
    CalibrationApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
