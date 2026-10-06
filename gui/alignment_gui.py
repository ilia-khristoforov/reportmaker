#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
alignment_gui.py — Interactive GUI for aligning sensor data onto the
potentiostat master timeline.

Two sliders (coarse + fine) per sensor let you shift OCV and color/volume
data in real time while plots update live. Export the aligned CSV when done.

Run with:  python gui/alignment_gui.py  (from project root)
"""

import io
import contextlib
import pathlib
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# Ensure project root is on sys.path so core.* imports work
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

from core.align_cycles_data import (
    build_master_timeline,
    interpolate_color_data,
    interpolate_ocv_data,
)

# ── Constants ──────────────────────────────────────────────────────────────────

WIN_W, WIN_H = 1300, 900

BG       = '#252526'
BG_DARK  = '#1e1e1e'
BG_HEAD  = '#1a1a2e'
FG       = '#cccccc'
FG_DIM   = '#888888'
FG_OK    = '#4caf50'
FG_WARN  = '#ff9800'
FG_ERR   = '#ef5350'

DEBOUNCE_MS  = 250
PLOT_STRIDE  = 10       # plot every Nth point for speed
REST_DURATION_S = 30.0

SENSORS = ['ocv', 'color']


# ── Application ────────────────────────────────────────────────────────────────

class AlignmentApp:

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("VRFB Data Alignment Tool")
        self.root.geometry(f"{WIN_W}x{WIN_H}")
        self.root.resizable(True, True)
        self.root.configure(bg=BG)

        # ── Data state ────────────────────────────────────────────────────────
        self.folder: pathlib.Path | None = None
        self.df_cycles: pd.DataFrame | None = None   # raw wide format — kept for rebuilds
        self.df_color: pd.DataFrame | None = None
        self.df_ocv:   pd.DataFrame | None = None
        self.df_master: pd.DataFrame | None = None   # rebuilt when rest duration changes
        self._last_df:  pd.DataFrame | None = None   # latest interpolated result

        # ── Control vars ──────────────────────────────────────────────────────
        self._coarse_var = {s: tk.DoubleVar(value=0.0) for s in SENSORS}
        self._fine_var   = {s: tk.DoubleVar(value=0.0) for s in SENSORS}
        self._entry_var  = {s: tk.StringVar(value='0.0') for s in SENSORS}
        self._rest_var       = tk.DoubleVar(value=REST_DURATION_S)
        self._rest_entry_var = tk.StringVar(value=f'{REST_DURATION_S:.1f}')
        self._updating_controls = False   # re-entrancy guard

        self._debounce_id: str | None = None
        self._rebuild_debounce_id: str | None = None
        self._gen = 0   # generation counter for stale result discard

        self._color_col: str | None = None   # currently selected color column

        # ── Build UI ──────────────────────────────────────────────────────────
        self._build_ui()

    # ── UI assembly ───────────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_top_bar()
        tk.Frame(self.root, bg='#333333', height=1).pack(fill=tk.X)
        self._build_controls()
        tk.Frame(self.root, bg='#333333', height=1).pack(fill=tk.X)
        self._build_plot_area()
        tk.Frame(self.root, bg='#333333', height=1).pack(fill=tk.X)
        self._build_bottom_bar()

    def _build_top_bar(self):
        bar = tk.Frame(self.root, bg=BG_HEAD, height=60)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)

        tk.Label(bar, text="Experiment folder:", bg=BG_HEAD, fg=FG,
                 font=('Helvetica', 10)).pack(side=tk.LEFT, padx=(18, 8), pady=18)

        self._folder_var = tk.StringVar(value="")
        ttk.Entry(bar, textvariable=self._folder_var, width=80).pack(
            side=tk.LEFT, pady=18)
        ttk.Button(bar, text="Browse…", command=self._browse_folder).pack(
            side=tk.LEFT, padx=10, pady=18)

    def _build_controls(self):
        panel = tk.Frame(self.root, bg=BG_DARK, padx=12, pady=6)
        panel.pack(fill=tk.X)

        labels = {'ocv': 'OCV offset (s):', 'color': 'Color offset (s):'}
        for sensor in SENSORS:
            self._build_sensor_row(panel, sensor, labels[sensor])

        self._build_rest_row(panel)

        # Color column picker
        col_row = tk.Frame(panel, bg=BG_DARK)
        col_row.pack(fill=tk.X, pady=(4, 0))
        tk.Label(col_row, text="Color column:", bg=BG_DARK, fg=FG,
                 width=16, anchor='e').pack(side=tk.LEFT, padx=(0, 6))
        self._col_var = tk.StringVar()
        self._col_combo = ttk.Combobox(col_row, textvariable=self._col_var,
                                       state='disabled', width=22)
        self._col_combo.pack(side=tk.LEFT)
        self._col_combo.bind('<<ComboboxSelected>>', self._on_col_changed)

    def _build_sensor_row(self, parent: tk.Frame, sensor: str, label: str):
        row = tk.Frame(parent, bg=BG_DARK)
        row.pack(fill=tk.X, pady=2)

        tk.Label(row, text=label, bg=BG_DARK, fg=FG,
                 width=16, anchor='e').pack(side=tk.LEFT, padx=(0, 6))

        # Entry
        entry = ttk.Entry(row, textvariable=self._entry_var[sensor], width=10)
        entry.pack(side=tk.LEFT, padx=(0, 8))
        entry.bind('<Return>',    lambda e, s=sensor: self._on_entry_commit(s))
        entry.bind('<FocusOut>',  lambda e, s=sensor: self._on_entry_commit(s))

        # Coarse slider
        tk.Label(row, text="Coarse:", bg=BG_DARK, fg=FG_DIM,
                 font=('Helvetica', 8)).pack(side=tk.LEFT)
        tk.Scale(row, from_=-10000, to=10000, variable=self._coarse_var[sensor],
                 orient=tk.HORIZONTAL, resolution=10, length=450,
                 bg=BG_DARK, fg=FG, troughcolor='#3a3a3a',
                 highlightthickness=0, showvalue=False,
                 command=lambda v, s=sensor: self._on_slider_change(s)
                 ).pack(side=tk.LEFT, padx=(2, 10))

        # Fine slider
        tk.Label(row, text="Fine:", bg=BG_DARK, fg=FG_DIM,
                 font=('Helvetica', 8)).pack(side=tk.LEFT)
        tk.Scale(row, from_=-50, to=50, variable=self._fine_var[sensor],
                 orient=tk.HORIZONTAL, resolution=0.5, length=220,
                 bg=BG_DARK, fg=FG, troughcolor='#3a3a3a',
                 highlightthickness=0, showvalue=False,
                 command=lambda v, s=sensor: self._on_slider_change(s)
                 ).pack(side=tk.LEFT, padx=(2, 10))

        # Reset
        ttk.Button(row, text="Reset", width=6,
                   command=lambda s=sensor: self._reset_sensor(s)
                   ).pack(side=tk.LEFT)

    def _build_rest_row(self, parent: tk.Frame):
        row = tk.Frame(parent, bg=BG_DARK)
        row.pack(fill=tk.X, pady=2)

        tk.Label(row, text="Rest duration (s):", bg=BG_DARK, fg=FG,
                 width=16, anchor='e').pack(side=tk.LEFT, padx=(0, 6))

        entry = ttk.Entry(row, textvariable=self._rest_entry_var, width=10)
        entry.pack(side=tk.LEFT, padx=(0, 8))
        entry.bind('<Return>',   lambda e: self._on_rest_entry_commit())
        entry.bind('<FocusOut>', lambda e: self._on_rest_entry_commit())

        tk.Scale(row, from_=1, to=300, variable=self._rest_var,
                 orient=tk.HORIZONTAL, resolution=0.5, length=400,
                 bg=BG_DARK, fg=FG, troughcolor='#3a3a3a',
                 highlightthickness=0, showvalue=False,
                 command=lambda v: self._on_rest_change()
                 ).pack(side=tk.LEFT, padx=(2, 10))

        tk.Label(row, text="(rebuilds master timeline)", bg=BG_DARK,
                 fg=FG_DIM, font=('Helvetica', 8)).pack(side=tk.LEFT)

    def _build_plot_area(self):
        plot_frame = tk.Frame(self.root, bg=BG_DARK)
        plot_frame.pack(fill=tk.BOTH, expand=True)

        self.fig = Figure(facecolor=BG_DARK)
        self.fig.subplots_adjust(hspace=0.38, left=0.07, right=0.93,
                                 top=0.96, bottom=0.07)

        self.ax_uv  = self.fig.add_subplot(2, 1, 1)
        self.ax_ocv = self.ax_uv.twinx()
        self.ax_uv2 = self.fig.add_subplot(2, 1, 2)
        self.ax_col = self.ax_uv2.twinx()

        for ax in (self.ax_uv, self.ax_ocv, self.ax_uv2, self.ax_col):
            ax.set_facecolor(BG_DARK)
            ax.tick_params(colors=FG_DIM, labelsize=8)
            for spine in ax.spines.values():
                spine.set_color('#444444')
            ax.xaxis.label.set_color(FG_DIM)
            ax.yaxis.label.set_color(FG_DIM)
            ax.grid(True, color='#333333', linewidth=0.5)

        self.ax_uv.set_ylabel('U_V  (potentiostat)', color=FG_DIM, fontsize=8)
        self.ax_ocv.set_ylabel('OCV sensor  (V)', color='#ff9800', fontsize=8)
        self.ax_uv.set_title('Voltage alignment  —  OCV slider', color=FG_DIM,
                             fontsize=9, loc='left')

        self.ax_uv2.set_ylabel('U_V  (potentiostat)', color=FG_DIM, fontsize=8)
        self.ax_col.set_ylabel('Color column  (norm.)', color='#4caf50', fontsize=8)
        self.ax_uv2.set_title('Color alignment  —  Color slider', color=FG_DIM,
                              fontsize=9, loc='left')
        self.ax_uv2.set_xlabel('Global_Time_s', color=FG_DIM, fontsize=8)

        self.ax_ocv.tick_params(colors='#ff9800', labelsize=8)
        self.ax_col.tick_params(colors='#4caf50', labelsize=8)

        # Placeholder line artists (data filled in after load)
        self.line_uv,  = self.ax_uv.plot([], [], color='#7777aa', lw=0.5,
                                          label='U_V')
        self.line_ocv, = self.ax_ocv.plot([], [], color='#ff9800', lw=0.8,
                                           label='OCV sensor')
        self.line_uv2, = self.ax_uv2.plot([], [], color='#7777aa', lw=0.5,
                                           label='U_V')
        self.line_col, = self.ax_col.plot([], [], color='#4caf50', lw=0.8,
                                           label='color col')

        self.ax_uv.legend(loc='upper left', fontsize=7,
                          facecolor=BG_DARK, labelcolor=FG_DIM)
        self.ax_ocv.legend(loc='upper right', fontsize=7,
                           facecolor=BG_DARK, labelcolor='#ff9800')
        self.ax_uv2.legend(loc='upper left', fontsize=7,
                           facecolor=BG_DARK, labelcolor=FG_DIM)
        self.ax_col.legend(loc='upper right', fontsize=7,
                           facecolor=BG_DARK, labelcolor='#4caf50')

        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        toolbar_frame = tk.Frame(plot_frame, bg=BG_DARK)
        toolbar_frame.pack(fill=tk.X)
        toolbar = NavigationToolbar2Tk(self.canvas, toolbar_frame)
        toolbar.config(background=BG_DARK)
        for w in toolbar.winfo_children():
            try:
                w.config(background=BG_DARK)
            except tk.TclError:
                pass
        toolbar.update()

    def _build_bottom_bar(self):
        bar = tk.Frame(self.root, bg=BG_DARK, height=36)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)

        self._btn_export = ttk.Button(bar, text="Export CSV", width=14,
                                      command=self._export, state=tk.DISABLED)
        self._btn_export.pack(side=tk.RIGHT, padx=14, pady=5)

        self._lbl_status = tk.Label(bar, text="Select an experiment folder to begin.",
                                    bg=BG_DARK, fg=FG_DIM,
                                    font=('Courier', 9), anchor='w')
        self._lbl_status.pack(side=tk.LEFT, padx=14, pady=5)

    # ── Folder / data loading ─────────────────────────────────────────────────

    def _browse_folder(self):
        folder = filedialog.askdirectory(title="Select experiment folder")
        if not folder:
            return
        self.folder = pathlib.Path(folder)
        self._folder_var.set(str(self.folder))

        parsed = self.folder / '02_Parsed_data'
        if not parsed.exists():
            messagebox.showerror("Not found",
                                 f"No '02_Parsed_data' subfolder found in:\n{self.folder}")
            self.folder = None
            return

        # Locate cycles.csv
        for candidate in [parsed / 'cycles.csv',
                          parsed / 'potentiostat' / 'cycles.csv']:
            if candidate.exists():
                self._path_cycles = candidate
                break
        else:
            messagebox.showerror("Not found",
                                 "Could not find cycles.csv inside 02_Parsed_data.")
            self.folder = None
            return

        self._path_color = parsed / 'colors_volumes_merged.csv'
        self._path_ocv   = parsed / 'OCV'/ 'ocv_log.csv'

        self._lbl_status.config(text="Loading data… (this may take a few seconds)",
                                fg=FG_WARN)
        self._btn_export.config(state=tk.DISABLED)
        self._col_combo.config(state='disabled')

        threading.Thread(target=self._load_data_thread, daemon=True).start()

    def _load_data_thread(self):
        try:
            df_cycles = pd.read_csv(self._path_cycles)

            df_color = pd.DataFrame()
            if self._path_color.exists():
                df_color = pd.read_csv(self._path_color, low_memory=False)
                # Coerce any object columns (firmware restart messages mixed in)
                for col in df_color.select_dtypes(include='object').columns:
                    if col != 'DateTime':
                        df_color[col] = pd.to_numeric(df_color[col], errors='coerce')

            df_ocv = pd.DataFrame()
            if self._path_ocv.exists():
                df_ocv = pd.read_csv(self._path_ocv)

            # Build master timeline (slow step — rebuilt when rest duration changes)
            rest = self._rest_var.get()
            with contextlib.redirect_stdout(io.StringIO()):
                df_master = build_master_timeline(df_cycles, rest_duration_s=rest)

            stats = (f"Loaded: {len(df_master):,} pts  |  "
                     f"{int(df_master['Cycle_Index'].nunique())} cycles  |  "
                     f"{df_master['Global_Time_s'].max():.0f} s total")

            self.root.after(0, lambda: self._on_data_loaded(
                df_cycles, df_master, df_color, df_ocv, stats))

        except Exception as exc:
            self.root.after(0, lambda: (
                self._lbl_status.config(text=f"Error: {exc}", fg=FG_ERR),
                messagebox.showerror("Load error", str(exc)),
            ))

    def _on_data_loaded(self, df_cycles: pd.DataFrame, df_master: pd.DataFrame,
                        df_color: pd.DataFrame, df_ocv: pd.DataFrame,
                        stats: str):
        self.df_cycles = df_cycles
        self.df_master = df_master
        self.df_color  = df_color if not df_color.empty else None
        self.df_ocv    = df_ocv   if not df_ocv.empty   else None

        # Detect color columns
        numeric_cols = []
        if self.df_color is not None:
            all_num = df_color.select_dtypes(include=[np.number]).columns.tolist()
            numeric_cols = [c for c in all_num if c != 'Seconds_from_start']

        if numeric_cols:
            self._col_combo['values'] = numeric_cols
            self._col_combo.config(state='readonly')
            for preferred in ('catholyte', 'anolyte'):
                if preferred in numeric_cols:
                    self._col_var.set(preferred)
                    break
            else:
                self._col_var.set(numeric_cols[0])
            self._color_col = self._col_var.get()

        self._lbl_status.config(text=stats, fg=FG_OK)
        self._btn_export.config(state=tk.NORMAL)

        # U_V lines + sensor interpolation drawn together via _do_update
        self._do_update()

    # ── Control callbacks ─────────────────────────────────────────────────────

    def _get_total_offset(self, sensor: str) -> float:
        return self._coarse_var[sensor].get() + self._fine_var[sensor].get()

    def _on_slider_change(self, sensor: str):
        if self._updating_controls:
            return
        self._updating_controls = True
        total = self._get_total_offset(sensor)
        self._entry_var[sensor].set(f'{total:.1f}')
        self._updating_controls = False
        self._schedule_update()

    def _on_entry_commit(self, sensor: str):
        if self._updating_controls:
            return
        try:
            val = float(self._entry_var[sensor].get())
        except ValueError:
            # Revert entry to current total
            self._entry_var[sensor].set(f'{self._get_total_offset(sensor):.1f}')
            return
        self._updating_controls = True
        # Distribute: nearest multiple of 10 to coarse, remainder to fine
        coarse = round(val / 10) * 10
        fine   = round(val - coarse, 1)
        # Clamp to slider ranges
        coarse = max(-10000, min(10000, coarse))
        fine   = max(-50,    min(50,    fine))
        self._coarse_var[sensor].set(coarse)
        self._fine_var[sensor].set(fine)
        self._entry_var[sensor].set(f'{coarse + fine:.1f}')
        self._updating_controls = False
        self._schedule_update()

    def _reset_sensor(self, sensor: str):
        self._updating_controls = True
        self._coarse_var[sensor].set(0.0)
        self._fine_var[sensor].set(0.0)
        self._entry_var[sensor].set('0.0')
        self._updating_controls = False
        self._schedule_update()

    def _on_col_changed(self, _event=None):
        self._color_col = self._col_var.get()
        self._schedule_update()

    def _on_rest_change(self):
        val = self._rest_var.get()
        self._rest_entry_var.set(f'{val:.1f}')
        self._schedule_rebuild()

    def _on_rest_entry_commit(self):
        try:
            val = float(self._rest_entry_var.get())
            val = max(1.0, min(300.0, val))
        except ValueError:
            val = self._rest_var.get()
        self._rest_var.set(val)
        self._rest_entry_var.set(f'{val:.1f}')
        self._schedule_rebuild()

    # ── Debounced update pipeline ─────────────────────────────────────────────

    def _schedule_update(self):
        if self._debounce_id is not None:
            self.root.after_cancel(self._debounce_id)
        self._debounce_id = self.root.after(DEBOUNCE_MS, self._do_update)

    def _schedule_rebuild(self):
        """Debounced rebuild of master timeline (rest duration changed)."""
        if self._rebuild_debounce_id is not None:
            self.root.after_cancel(self._rebuild_debounce_id)
        if self._debounce_id is not None:
            self.root.after_cancel(self._debounce_id)
            self._debounce_id = None
        self._rebuild_debounce_id = self.root.after(DEBOUNCE_MS, self._do_rebuild)

    def _do_rebuild(self):
        """Rebuild master timeline from raw cycles data, then re-interpolate."""
        self._rebuild_debounce_id = None
        if self.df_cycles is None:
            return

        self._gen += 1
        my_gen       = self._gen
        rest         = self._rest_var.get()
        df_cycles    = self.df_cycles
        df_color     = self.df_color
        df_ocv       = self.df_ocv
        offset_ocv   = self._get_total_offset('ocv')
        offset_color = self._get_total_offset('color')
        color_col    = self._color_col

        self._lbl_status.config(text=f"Rebuilding timeline (rest={rest:.1f} s)…",
                                fg=FG_WARN)

        def work():
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    df_master = build_master_timeline(df_cycles, rest_duration_s=rest)

                if my_gen != self._gen:
                    return

                df_work = df_master.copy()
                if df_ocv is not None:
                    with contextlib.redirect_stdout(io.StringIO()):
                        df_work = interpolate_ocv_data(df_work, df_ocv,
                                                       offset_s=offset_ocv)
                if df_color is not None:
                    with contextlib.redirect_stdout(io.StringIO()):
                        df_work = interpolate_color_data(df_work, df_color,
                                                         offset_s=offset_color)
            except Exception:
                return

            if my_gen != self._gen:
                return

            self.root.after(0, lambda: self._apply_rebuild(
                df_master, df_work, offset_ocv, offset_color, color_col, my_gen))

        threading.Thread(target=work, daemon=True).start()

    def _apply_rebuild(self, df_master: pd.DataFrame, df_work: pd.DataFrame,
                       offset_ocv: float, offset_color: float,
                       color_col: str | None, gen: int):
        if gen != self._gen:
            return
        self.df_master = df_master   # store updated master
        self._apply_to_plot(df_work, offset_ocv, offset_color, color_col, gen)

    def _do_update(self):
        self._debounce_id = None
        if self.df_master is None:
            return

        self._gen += 1
        my_gen       = self._gen
        offset_ocv   = self._get_total_offset('ocv')
        offset_color = self._get_total_offset('color')
        color_col    = self._color_col
        df_master    = self.df_master     # reference — never mutated
        df_color     = self.df_color
        df_ocv       = self.df_ocv

        def work():
            try:
                df_work = df_master.copy()

                if df_ocv is not None:
                    with contextlib.redirect_stdout(io.StringIO()):
                        df_work = interpolate_ocv_data(df_work, df_ocv,
                                                       offset_s=offset_ocv)
                if df_color is not None:
                    with contextlib.redirect_stdout(io.StringIO()):
                        df_work = interpolate_color_data(df_work, df_color,
                                                         offset_s=offset_color)
            except Exception:
                return   # silently skip on error; status label unchanged

            if my_gen != self._gen:
                return   # superseded by a newer slider move

            self.root.after(0, lambda: self._apply_to_plot(
                df_work, offset_ocv, offset_color, color_col, my_gen))

        threading.Thread(target=work, daemon=True).start()

    def _apply_to_plot(self, df_work: pd.DataFrame,
                       offset_ocv: float, offset_color: float,
                       color_col: str | None, gen: int):
        if gen != self._gen:
            return   # another update arrived while we were waiting

        t = np.asarray(df_work['Global_Time_s'])[::PLOT_STRIDE]

        # U_V lines (update always — master timeline shifts when rest changes)
        uv = np.asarray(df_work['U_V'])[::PLOT_STRIDE] if 'U_V' in df_work.columns else np.full_like(t, np.nan)
        self.line_uv.set_xdata(t);   self.line_uv.set_ydata(uv)
        self.line_uv2.set_xdata(t);  self.line_uv2.set_ydata(uv)
        self.ax_uv.relim();   self.ax_uv.autoscale_view()
        self.ax_uv2.relim();  self.ax_uv2.autoscale_view()

        # OCV line
        if 'OCV_voltage_V' in df_work.columns:
            ocv = df_work['OCV_voltage_V'].values[::PLOT_STRIDE]
            self.line_ocv.set_xdata(t)
            self.line_ocv.set_ydata(ocv)
            self.ax_ocv.relim()
            self.ax_ocv.autoscale_view()

        # Color line (normalized)
        if color_col and color_col in df_work.columns:
            raw = df_work[color_col].values[::PLOT_STRIDE].astype(float)
            lo, hi = np.nanmin(raw), np.nanmax(raw)
            norm = (raw - lo) / (hi - lo + 1e-9)
            self.line_col.set_xdata(t)
            self.line_col.set_ydata(norm)
            self.ax_col.set_ylabel(f'{color_col}  (norm.)', color='#4caf50', fontsize=8)
            self.ax_col.relim()
            self.ax_col.autoscale_view()

        self.canvas.draw_idle()
        self._last_df = df_work

        self._lbl_status.config(
            text=(f"OCV offset: {offset_ocv:+.1f} s   |   "
                  f"Color offset: {offset_color:+.1f} s"),
            fg=FG_OK)

    # ── Export ────────────────────────────────────────────────────────────────

    def _export(self):
        if self._last_df is None:
            messagebox.showwarning("No data", "Load data and adjust offsets first.")
            return

        default_dir  = str(self.folder / '02_Parsed_data') if self.folder else '.'
        default_name = 'all_data_aligned.csv'
        path = filedialog.asksaveasfilename(
            initialfile=default_name,
            initialdir=default_dir,
            defaultextension='.csv',
            filetypes=[('CSV files', '*.csv')],
            title='Save aligned data as…',
        )
        if not path:
            return

        self._lbl_status.config(text="Saving…", fg=FG_WARN)
        self.root.update_idletasks()

        df_to_save = self._last_df
        offset_ocv   = self._get_total_offset('ocv')
        offset_color = self._get_total_offset('color')

        def save():
            try:
                df_to_save.to_csv(path, index=False)
                name = pathlib.Path(path).name
                self.root.after(0, lambda: self._lbl_status.config(
                    text=(f"Saved → {name}   "
                          f"(OCV: {offset_ocv:+.1f} s,  Color: {offset_color:+.1f} s)"),
                    fg=FG_OK,
                ))
            except Exception as exc:
                self.root.after(0, lambda: (
                    self._lbl_status.config(text=f"Save failed: {exc}", fg=FG_ERR),
                    messagebox.showerror("Save error", str(exc)),
                ))

        threading.Thread(target=save, daemon=True).start()


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
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
    AlignmentApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
