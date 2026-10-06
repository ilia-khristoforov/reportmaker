#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
postprocessing_gui.py — Post-Processing & Plots tool for VRFB experiments.

Calculates efficiencies and generates publication-ready plots from parsed
potentiostat data. Accepts an optional experiment folder path as a CLI
argument, or lets the user browse for one interactively.

Run with:  python gui/postprocessing_gui.py [experiment_folder]  (from project root)
"""
from __future__ import annotations

# ── matplotlib Agg backend — must be set before importing post_processing ─────
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as _plt
_plt.show = lambda: None      # suppress plt.show() calls from post_processing

# ── stdlib ────────────────────────────────────────────────────────────────────
import contextlib
import io
import json
import pathlib
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# Ensure project root is on sys.path so core.* imports work
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

# ── project modules ───────────────────────────────────────────────────────────
import core.post_processing as post_processing
from core.base_parser import nominal_capacity_C

# ── constants ─────────────────────────────────────────────────────────────────
WIN_W, WIN_H = 900, 680

BG      = '#252526'
BG_DARK = '#1e1e1e'
BG_HEAD = '#1a1a2e'
FG      = '#cccccc'
FG_DIM  = '#888888'
FG_OK   = '#4caf50'
FG_WARN = '#ff9800'
FG_ERR  = '#ef5350'


# ── App ───────────────────────────────────────────────────────────────────────

class PostProcessingApp:
    """Standalone post-processing and plot generation tool."""

    def __init__(self, root: tk.Tk,
                 initial_folder: pathlib.Path | None = None) -> None:
        self.root = root
        self.root.title('VRFB Post-Processing & Plots')
        self.root.geometry(f'{WIN_W}x{WIN_H}')
        self.root.resizable(True, True)
        self.root.configure(bg=BG)

        self.folder: pathlib.Path | None = None
        self._worker: threading.Thread | None = None

        self._build_ui()

        if initial_folder and initial_folder.is_dir():
            self._load_folder(initial_folder)

    # ── UI assembly ───────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._build_top_bar()
        tk.Frame(self.root, bg='#333333', height=1).pack(fill=tk.X)
        self._build_controls()
        tk.Frame(self.root, bg='#333333', height=1).pack(fill=tk.X)
        self._build_log()
        tk.Frame(self.root, bg='#333333', height=1).pack(fill=tk.X)
        self._build_bottom_bar()

    def _build_top_bar(self) -> None:
        bar = tk.Frame(self.root, bg=BG_HEAD, height=60)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)

        tk.Label(bar, text='Experiment folder:', bg=BG_HEAD, fg=FG,
                 font=('Helvetica', 10)).pack(side=tk.LEFT, padx=(18, 8), pady=18)

        self._folder_var = tk.StringVar()
        ttk.Entry(bar, textvariable=self._folder_var, width=72
                  ).pack(side=tk.LEFT, pady=18)
        ttk.Button(bar, text='Browse…', command=self._browse_folder
                   ).pack(side=tk.LEFT, padx=10, pady=18)

    def _build_controls(self) -> None:
        panel = tk.Frame(self.root, bg=BG_DARK, padx=18, pady=10)
        panel.pack(fill=tk.X)

        # ── cycle range row ───────────────────────────────────────────────────
        row0 = tk.Frame(panel, bg=BG_DARK)
        row0.pack(fill=tk.X, pady=3)

        tk.Label(row0, text='Cycles:', bg=BG_DARK, fg=FG,
                 font=('Helvetica', 9), width=14, anchor='e'
                 ).pack(side=tk.LEFT, padx=(0, 8))
        tk.Label(row0, text='from', bg=BG_DARK, fg=FG_DIM,
                 font=('Helvetica', 8)).pack(side=tk.LEFT, padx=(0, 4))
        self._v_start = tk.IntVar(value=1)
        self._sb_start = ttk.Spinbox(row0, from_=1, to=999,
                                     textvariable=self._v_start, width=6)
        self._sb_start.pack(side=tk.LEFT)
        tk.Label(row0, text='to', bg=BG_DARK, fg=FG_DIM,
                 font=('Helvetica', 8)).pack(side=tk.LEFT, padx=6)
        self._v_end = tk.IntVar(value=1)
        self._sb_end = ttk.Spinbox(row0, from_=1, to=999,
                                   textvariable=self._v_end, width=6)
        self._sb_end.pack(side=tk.LEFT)
        tk.Label(row0, text='step', bg=BG_DARK, fg=FG_DIM,
                 font=('Helvetica', 8)).pack(side=tk.LEFT, padx=(12, 4))
        self._v_step = tk.IntVar(value=1)
        ttk.Spinbox(row0, from_=1, to=999,
                    textvariable=self._v_step, width=6).pack(side=tk.LEFT)

        self._lbl_total = tk.Label(row0, text='', bg=BG_DARK, fg=FG_DIM,
                                   font=('Helvetica', 8))
        self._lbl_total.pack(side=tk.LEFT, padx=(16, 0))

        # ── plot selection row ────────────────────────────────────────────────
        row1 = tk.Frame(panel, bg=BG_DARK)
        row1.pack(fill=tk.X, pady=(6, 3))

        tk.Label(row1, text='Generate plots:', bg=BG_DARK, fg=FG,
                 font=('Helvetica', 9), width=14, anchor='e'
                 ).pack(side=tk.LEFT, padx=(0, 8))

        self._v_eff = tk.BooleanVar(value=True)
        self._v_ocv = tk.BooleanVar(value=True)
        self._v_chd = tk.BooleanVar(value=True)
        for text, var in [('Efficiency plots', self._v_eff),
                          ('OCV curves', self._v_ocv),
                          ('Charge-discharge', self._v_chd)]:
            tk.Checkbutton(row1, text=text, variable=var,
                           bg=BG_DARK, fg=FG, selectcolor=BG,
                           activebackground=BG_DARK, activeforeground=FG,
                           font=('Helvetica', 9)
                           ).pack(side=tk.LEFT, padx=(0, 16))

        # ── dat export row ────────────────────────────────────────────────────
        row2 = tk.Frame(panel, bg=BG_DARK)
        row2.pack(fill=tk.X, pady=(6, 3))

        tk.Label(row2, text='Export data:', bg=BG_DARK, fg=FG,
                 font=('Helvetica', 9), width=14, anchor='e'
                 ).pack(side=tk.LEFT, padx=(0, 8))

        self._v_dat = tk.BooleanVar(value=False)
        self._cb_dat = tk.Checkbutton(
            row2, text='Colleague .dat files  (color + volume, tab-separated)',
            variable=self._v_dat,
            bg=BG_DARK, fg=FG, selectcolor=BG,
            activebackground=BG_DARK, activeforeground=FG,
            font=('Helvetica', 9), state=tk.DISABLED)
        self._cb_dat.pack(side=tk.LEFT, padx=(0, 16))

    def _build_log(self) -> None:
        log_frame = tk.Frame(self.root, bg=BG_DARK)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self._log = tk.Text(log_frame, state='disabled', bg=BG_DARK, fg=FG_DIM,
                            font=('Courier', 9), wrap='word',
                            relief='flat', borderwidth=0,
                            insertbackground=FG)
        sb = ttk.Scrollbar(log_frame, command=self._log.yview)
        self._log.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._log.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        for tag, fg in [('ok', FG_OK), ('warn', FG_WARN),
                        ('err', FG_ERR), ('dim', FG_DIM)]:
            self._log.tag_configure(tag, foreground=fg)

    def _build_bottom_bar(self) -> None:
        bar = tk.Frame(self.root, bg=BG_DARK, height=42)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)

        self._btn_run = ttk.Button(bar, text='Run', width=14,
                                   command=self._run, state=tk.DISABLED)
        self._btn_run.pack(side=tk.RIGHT, padx=14, pady=7)

        self._lbl_status = tk.Label(bar, text='Select an experiment folder to begin.',
                                    bg=BG_DARK, fg=FG_DIM,
                                    font=('Courier', 9), anchor='w')
        self._lbl_status.pack(side=tk.LEFT, padx=14, pady=7)

    # ── folder loading ────────────────────────────────────────────────────────

    def _browse_folder(self) -> None:
        folder = filedialog.askdirectory(title='Select experiment folder')
        if folder:
            self._load_folder(pathlib.Path(folder))

    def _load_folder(self, folder: pathlib.Path) -> None:
        self.folder = folder
        self._folder_var.set(str(folder))

        cycles_path  = self._find_cycles_csv()
        metrics_path = folder / '03_Processed_data' / 'step_metrics.csv'

        if not metrics_path.exists():
            self._set_status('step_metrics.csv not found — run the parser first.',
                             FG_ERR)
            self._btn_run.config(state=tk.DISABLED)
            return

        total = 1
        if cycles_path:
            try:
                total = max(1, post_processing.get_total_cycles(cycles_path))
            except Exception:
                pass

        for sb, var, val in [(self._sb_start, self._v_start, 1),
                             (self._sb_end,   self._v_end,   total)]:
            sb.config(to=total)
            var.set(val)
        self._sb_start.config(to=total)
        self._sb_end.config(to=total)
        self._lbl_total.config(text=f'({total} cycles detected)')
        self._btn_run.config(state=tk.NORMAL)

        if self._find_aligned_csv():
            self._cb_dat.config(state=tk.NORMAL)
            self._v_dat.set(True)
        else:
            self._cb_dat.config(state=tk.DISABLED)
            self._v_dat.set(False)

        self._set_status(f'Loaded: {folder.name}  —  {total} cycles', FG_OK)

    def _find_aligned_csv(self) -> pathlib.Path | None:
        if not self.folder:
            return None
        candidate = self.folder / '02_Parsed_data' / 'all_data_aligned.csv'
        return candidate if candidate.exists() else None

    def _find_cycles_csv(self) -> pathlib.Path | None:
        if not self.folder:
            return None
        parsed = self.folder / '02_Parsed_data'
        for candidate in [parsed / 'cycles.csv',
                          parsed / 'potentiostat' / 'cycles.csv']:
            if candidate.exists():
                return candidate
        return None

    def _get_q_theory_mah(self) -> float | None:
        if not self.folder:
            return None
        try:
            cfg = json.loads(
                (self.folder / 'config.json').read_text(encoding='utf-8'))
            return nominal_capacity_C(cfg) / 3600.0 * 1000.0    # → mAh
        except Exception:
            return None

    def _get_voltage_limits(self) -> tuple[float, float] | None:
        """Optional plot window from config.json: plotting.voltage_limits_V = [min, max]."""
        try:
            cfg = json.loads(
                (self.folder / 'config.json').read_text(encoding='utf-8'))
            lo, hi = cfg['plotting']['voltage_limits_V']
            return float(lo), float(hi)
        except Exception:
            return None

    # ── run ───────────────────────────────────────────────────────────────────

    def _set_status(self, text: str, color: str = FG_DIM) -> None:
        self._lbl_status.config(text=text, fg=color)

    def _log_append(self, text: str, tag: str = 'dim') -> None:
        """Thread-safe log write."""
        self.root.after(0, lambda: self._log_append_main(text, tag))

    def _log_append_main(self, text: str, tag: str) -> None:
        self._log.config(state='normal')
        self._log.insert('end', text, tag)
        self._log.see('end')
        self._log.config(state='disabled')

    def _log_clear(self) -> None:
        self._log.config(state='normal')
        self._log.delete('1.0', 'end')
        self._log.config(state='disabled')

    def _run(self) -> None:
        if not self.folder:
            return
        if self._worker and self._worker.is_alive():
            return

        folder       = self.folder
        cycles_path  = self._find_cycles_csv()
        metrics_path = folder / '03_Processed_data' / 'step_metrics.csv'
        results      = folder / '04_Results'
        results.mkdir(exist_ok=True)

        exp_name = folder.name
        q_mah    = self._get_q_theory_mah()
        start    = self._v_start.get()
        end      = self._v_end.get()
        step     = self._v_step.get()
        do_eff      = self._v_eff.get()
        do_ocv      = self._v_ocv.get()
        do_chd      = self._v_chd.get()
        do_dat      = self._v_dat.get()
        aligned_path = self._find_aligned_csv()

        self._btn_run.config(state=tk.DISABLED)
        self._set_status('Running…', FG_WARN)
        self._log_clear()

        def work() -> None:
            self._log_append(f'Experiment: {exp_name}\n', 'dim')
            if q_mah:
                self._log_append(
                    f'Theoretical capacity: {q_mah:.1f} mAh\n\n', 'dim')
            else:
                self._log_append(
                    'Theoretical capacity: not available (check config.json)\n\n',
                    'warn')

            # ── efficiencies ──────────────────────────────────────────────────
            df_eff = post_processing.calculate_efficiencies(metrics_path, q_mah)
            eff_csv = folder / '03_Processed_data' / 'efficiencies.csv'
            df_eff.to_csv(eff_csv, index=False)
            self._log_append(f'✓ Calculated efficiencies ({len(df_eff)} cycles)\n',
                             'ok')
            self._log_append(f'✓ Saved → {eff_csv.name}\n', 'ok')

            if do_eff:
                out = results / 'efficiencies.png'
                post_processing.plot_efficiencies(
                    df_eff, out, title=f'{exp_name} — Efficiencies')
                _plt.close('all')
                self._log_append(f'✓ Saved → {out.name}\n', 'ok')

            # ── OCV curves ────────────────────────────────────────────────────
            ocv_meas = folder / '03_Processed_data' / 'ocv_measurements.csv'
            if do_ocv:
                if ocv_meas.exists():
                    df_ocv = post_processing.reshape_ocv_data(ocv_meas)
                    out = results / 'ocv_curves.png'
                    post_processing.plot_ocv(
                        df_ocv, out, title=f'{exp_name} — OCV')
                    _plt.close('all')
                    self._log_append(f'✓ Saved → {out.name}\n', 'ok')
                else:
                    self._log_append(
                        'OCV measurements not found — skipped.\n', 'warn')

            # ── charge-discharge curves ───────────────────────────────────────
            if do_chd:
                if cycles_path and cycles_path.exists():
                    c_nom = (q_mah / 1000.0 * 3600.0) if q_mah else 1.0
                    out   = results / 'charge_discharge.png'
                    post_processing.plot_charge_discharge_cycles(
                        cycles_path, c_nom, out,
                        start_cycle=start, end_cycle=end, cycle_step=step,
                        voltage_limits=self._get_voltage_limits())
                    _plt.close('all')
                    self._log_append(f'✓ Saved → {out.name}\n', 'ok')
                else:
                    self._log_append(
                        'cycles.csv not found — charge-discharge plot skipped.\n',
                        'warn')

            # ── colleague .dat export ─────────────────────────────────────────
            if do_dat:
                if aligned_path and aligned_path.exists():
                    import pandas as pd
                    df_al = pd.read_csv(aligned_path)
                    _DAT_COLS = ['Global_Time_s',
                                 'R_count2', 'B_count2',
                                 'R_count1', 'B_count1',
                                 'anolyte', 'catholyte']
                    missing = [c for c in _DAT_COLS if c not in df_al.columns]
                    if missing:
                        self._log_append(
                            f'WARNING: .dat export skipped — missing columns: '
                            f'{missing}\n', 'warn')
                    else:
                        df_export = df_al[_DAT_COLS].dropna()

                        out_all = results / 'AOS_all_data.dat'
                        df_export.to_csv(out_all, sep='\t', index=False,
                                         header=False)
                        self._log_append(
                            f'✓ Saved → {out_all.name}  ({len(df_export)} rows)\n',
                            'ok')

                        mask_ch1 = ((df_al['Cycle_Index'] == 1) &
                                    (df_al['Step_Type'] == 'ch'))
                        df_ch1 = df_al.loc[mask_ch1, _DAT_COLS].dropna()
                        out_ch1 = results / 'AOS_first_charge.dat'
                        df_ch1.to_csv(out_ch1, sep='\t', index=False,
                                      header=False)
                        self._log_append(
                            f'✓ Saved → {out_ch1.name}  ({len(df_ch1)} rows)\n',
                            'ok')
                else:
                    self._log_append(
                        'all_data_aligned.csv not found — .dat export skipped.\n',
                        'warn')

        def done() -> None:
            self._log_append(f'\nAll done. Results saved to {results}\n', 'ok')
            self._set_status('Complete — results saved to 04_Results/', FG_OK)
            self._btn_run.config(state=tk.NORMAL, text='Run again')

        def error(exc: Exception) -> None:
            self._log_append(f'\nERROR: {exc}\n', 'err')
            self._set_status(f'Error: {exc}', FG_ERR)
            self._btn_run.config(state=tk.NORMAL)

        self._btn_run.config(state=tk.DISABLED)

        def wrapper():
            try:
                work()
                self.root.after(0, done)
            except Exception as exc:
                self.root.after(0, lambda e=exc: error(e))

        self._worker = threading.Thread(target=wrapper, daemon=True)
        self._worker.start()


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    initial_folder: pathlib.Path | None = None
    if len(sys.argv) > 1:
        p = pathlib.Path(sys.argv[1])
        if p.is_dir():
            initial_folder = p

    root = tk.Tk()
    try:
        ttk.Style(root).theme_use('clam')
    except tk.TclError:
        pass

    PostProcessingApp(root, initial_folder)
    root.mainloop()


if __name__ == '__main__':
    main()
