#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main_gui.py — VRFB Launcher: guided workflow orchestrator for the complete
VRFB data processing pipeline.

Guides a user step-by-step from raw potentiostat files all the way to
efficiency plots. Each pipeline stage shows its completion status and
provides a single action button.

Run with:  python main_gui.py
"""
from __future__ import annotations

# ── stdlib ────────────────────────────────────────────────────────────────────
import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Literal

# ── project pipeline modules ──────────────────────────────────────────────────
from core import series_data_converter
from core.parsers import PARSERS
from core.yarst_parser import YarstParser

# ── constants ─────────────────────────────────────────────────────────────────
SCRIPT_DIR   = pathlib.Path(__file__).parent
PREFS_FILE   = pathlib.Path.home() / '.vrfb_launcher.json'
WIN_W, WIN_H = 1100, 800

BG      = '#252526'
BG_DARK = '#1e1e1e'
BG_HEAD = '#1a1a2e'
FG      = '#cccccc'
FG_DIM  = '#888888'
FG_OK   = '#4caf50'
FG_WARN = '#ff9800'
FG_ERR  = '#ef5350'

VERSION = '1.0.0'

STEPS: list[dict] = [
    {'id': 'config',
     'num': 1,
     'name': 'Experiment Config',
     'desc': 'Create or edit config.json — required for all calculations',
     'btn':  'Edit Config'},
    {'id': 'parse',
     'num': 2,
     'name': 'Parse Potentiostat Data',
     'desc': 'Convert raw .txt files → cycles.csv + step_metrics.csv',
     'btn':  'Run Parser'},
    {'id': 'ocv_export',
     'num': 3,
     'name': 'Export OCV Log',
     'desc': 'YARST only — export continuous OCV voltage log → ocv_log.csv',
     'btn':  'Export OCV'},
    {'id': 'calibrate',
     'num': 4,
     'name': 'Calibrate Frame Pipeline',
     'desc': 'Webcam volume detection — launch calibration wizard (optional)',
     'btn':  'Launch Wizard'},
    {'id': 'convert',
     'num': 5,
     'name': 'Convert Color / Volume Data',
     'desc': 'Merge pixel heights + color sensor → colors_volumes_merged.csv (optional)',
     'btn':  'Convert'},
    {'id': 'align',
     'num': 6,
     'name': 'Align Sensor Data',
     'desc': 'Sync OCV / color sensors onto potentiostat timeline',
     'btn':  'Launch Aligner'},
    {'id': 'postproc',
     'num': 7,
     'name': 'Post-Processing & Plots',
     'desc': 'Calculate efficiencies and generate plots in 04_Results/',
     'btn':  'Run'},
    {'id': 'results',
     'num': 8,
     'name': 'View Results',
     'desc': 'Open 04_Results/ in the file explorer',
     'btn':  'Open Folder'},
]

_STEP_BY_ID: dict[str, dict] = {s['id']: s for s in STEPS}


# ── StepRow ───────────────────────────────────────────────────────────────────

class StepRow:
    """Widget bundle representing one pipeline stage."""

    _ICON: dict[str, tuple[str, str]] = {
        'done':    ('✓', FG_OK),
        'ready':   ('▶', FG),
        'waiting': ('○', FG_DIM),
        'running': ('…', FG_WARN),
        'na':      ('◌', FG_DIM),
    }

    def __init__(self, parent: tk.Frame, num: int, name: str,
                 desc: str, btn_text: str, command) -> None:
        self.frame = tk.Frame(parent, bg=BG, pady=5)
        self.frame.pack(fill=tk.X, padx=20)

        tk.Label(self.frame, text=f'{num}.', bg=BG, fg=FG_DIM,
                 font=('Helvetica', 11), width=3, anchor='e',
                 ).pack(side=tk.LEFT)

        self._lbl_icon = tk.Label(self.frame, text='○', bg=BG, fg=FG_DIM,
                                  font=('Helvetica', 13), width=2)
        self._lbl_icon.pack(side=tk.LEFT, padx=(4, 10))

        text_frame = tk.Frame(self.frame, bg=BG)
        text_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(text_frame, text=name, bg=BG, fg=FG,
                 font=('Helvetica', 10, 'bold'), anchor='w',
                 ).pack(anchor='w')
        tk.Label(text_frame, text=desc, bg=BG, fg=FG_DIM,
                 font=('Helvetica', 8), anchor='w',
                 ).pack(anchor='w')

        self._btn = ttk.Button(self.frame, text=btn_text, width=18,
                               command=command, state=tk.DISABLED)
        self._btn.pack(side=tk.RIGHT, padx=(8, 0))

    def set_status(self, state: Literal['done', 'ready', 'waiting', 'running', 'na']) -> None:
        char, color = self._ICON.get(state, ('?', FG_DIM))
        self._lbl_icon.config(text=char, fg=color)

    def set_button_state(self, enabled: bool) -> None:
        self._btn.config(state=tk.NORMAL if enabled else tk.DISABLED)

    def set_button_text(self, text: str) -> None:
        self._btn.config(text=text)


# ── LogPanel ──────────────────────────────────────────────────────────────────

class LogPanel:
    """Scrolled text widget for inline operation output."""

    def __init__(self, parent: tk.Widget) -> None:
        outer = tk.LabelFrame(parent, text=' Output ', bg=BG, fg=FG_DIM,
                              font=('Helvetica', 8), relief='flat',
                              highlightbackground='#333333',
                              highlightthickness=1)
        outer.pack(fill=tk.X, padx=8, pady=4)

        self._text = tk.Text(outer, state='disabled', bg=BG_DARK, fg=FG_DIM,
                             font=('Courier', 8), height=8, wrap='word',
                             relief='flat', borderwidth=0,
                             insertbackground=FG)
        sb = ttk.Scrollbar(outer, command=self._text.yview)
        self._text.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._text.pack(fill=tk.X, expand=True, padx=2, pady=2)

        for tag, fg in [('ok', FG_OK), ('warn', FG_WARN),
                        ('err', FG_ERR), ('dim', FG_DIM)]:
            self._text.tag_configure(tag, foreground=fg)

    def append(self, text: str, tag: str = 'dim') -> None:
        """Must be called from the main thread only."""
        self._text.config(state='normal')
        self._text.insert('end', text, tag)
        self._text.see('end')
        self._text.config(state='disabled')

    def clear(self) -> None:
        self._text.config(state='normal')
        self._text.delete('1.0', 'end')
        self._text.config(state='disabled')


# ── ConfigEditorDialog ────────────────────────────────────────────────────────

class ConfigEditorDialog(tk.Toplevel):
    """Modal dialog for creating or editing config.json."""

    def __init__(self, parent: tk.Tk, experiment_folder: pathlib.Path) -> None:
        super().__init__(parent)
        self.experiment_folder = experiment_folder
        self.saved = False
        self.title('Experiment Configuration')
        self.geometry('600x800')
        self.configure(bg=BG)
        self.resizable(True, True)

        self._cfg: dict = {}
        cfg_path = experiment_folder / 'config.json'
        if cfg_path.exists():
            try:
                self._cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
            except Exception:
                pass

        self._build_ui()
        self.grab_set()
        self.focus_set()

    # ── build ─────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        hdr = tk.Frame(self, bg=BG_HEAD, height=52)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        tk.Label(hdr, text='Experiment Configuration', bg=BG_HEAD, fg=FG,
                 font=('Helvetica', 12, 'bold')).pack(side=tk.LEFT, padx=18, pady=14)

        nb = ttk.Notebook(self)
        nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)

        tab1 = tk.Frame(nb, bg=BG, padx=16, pady=12)
        tab2 = tk.Frame(nb, bg=BG, padx=16, pady=12)
        nb.add(tab1, text='  Required  ')
        nb.add(tab2, text='  Metadata  ')

        self._build_required(tab1)
        self._build_metadata(tab2)

        tk.Frame(self, bg='#333333', height=1).pack(fill=tk.X)
        btn_bar = tk.Frame(self, bg=BG, height=42)
        btn_bar.pack(fill=tk.X)
        btn_bar.pack_propagate(False)
        ttk.Button(btn_bar, text='Cancel', command=self.destroy
                   ).pack(side=tk.RIGHT, padx=8, pady=7)
        ttk.Button(btn_bar, text='Save', command=self._save
                   ).pack(side=tk.RIGHT, pady=7)

    def _lbl_entry(self, parent: tk.Frame, row: int,
                   label: str, val: str) -> tk.StringVar:
        tk.Label(parent, text=label, bg=BG, fg=FG, anchor='e',
                 font=('Helvetica', 9), width=28,
                 ).grid(row=row, column=0, padx=(0, 8), pady=5, sticky='e')
        var = tk.StringVar(value=val)
        ttk.Entry(parent, textvariable=var, width=24,
                  ).grid(row=row, column=1, pady=5, sticky='w')
        return var

    def _build_required(self, parent: tk.Frame) -> None:
        elec = self._cfg.get('electrolyte', {})
        tk.Label(parent,
                 text='Concentration and volume are required for all capacity calculations.',
                 bg=BG, fg=FG_DIM, font=('Helvetica', 8),
                 ).grid(row=0, column=0, columnspan=2, pady=(0, 12), sticky='w')
        self._v_conc = self._lbl_entry(parent, 1, 'Concentration (mol/L):',
                                       str(elec.get('concentration_M', '')))
        self._v_vol  = self._lbl_entry(parent, 2, 'Electrolyte volume (mL):',
                                       str(elec.get('volume_ml', '')))
        self._v_n    = self._lbl_entry(parent, 3, 'Electrons transferred, n:',
                                       str(elec.get('electrons_transferred', 1)))

    def _build_metadata(self, parent: tk.Frame) -> None:
        info = self._cfg.get('experiment_info', {})
        cond = self._cfg.get('experimental_conditions', {})
        comp = self._cfg.get('cell_components', {})

        r = 0
        self._v_id       = self._lbl_entry(parent, r, 'Experiment ID:',
                                           info.get('id', '')); r += 1
        self._v_date     = self._lbl_entry(parent, r, 'Date (DD-MM-YYYY):',
                                           info.get('date', '')); r += 1
        self._v_operator = self._lbl_entry(parent, r, 'Operator:',
                                           info.get('operator', '')); r += 1
        self._v_desc     = self._lbl_entry(parent, r, 'Description:',
                                           info.get('description', '')); r += 1

        tk.Label(parent, text='Potentiostat:', bg=BG, fg=FG, anchor='e',
                 font=('Helvetica', 9), width=28,
                 ).grid(row=r, column=0, padx=(0, 8), pady=5, sticky='e')
        self._v_potstat = tk.StringVar(value=info.get('potentiostat', 'Yarst'))
        ttk.Combobox(parent, textvariable=self._v_potstat,
                     values=list(PARSERS), state='readonly', width=22,
                     ).grid(row=r, column=1, pady=5, sticky='w')
        r += 1

        self._v_flow = self._lbl_entry(parent, r, 'Flow rate (mL/min):',
                                       str(cond.get('flow_rate_ml_min', ''))); r += 1
        self._v_mem  = self._lbl_entry(parent, r, 'Membrane:',
                                       comp.get('membrane', {}).get('name', '')); r += 1

    # ── save ──────────────────────────────────────────────────────────────────

    def _save(self) -> None:
        try:
            conc = float(self._v_conc.get())
            vol  = float(self._v_vol.get())
            n_e  = float(self._v_n.get())
            if conc <= 0 or vol <= 0 or n_e <= 0:
                raise ValueError
        except (ValueError, AttributeError):
            messagebox.showerror('Validation',
                                 'Concentration, volume and n must be positive numbers.',
                                 parent=self)
            return

        cfg = dict(self._cfg)
        cfg.setdefault('electrolyte', {})
        cfg['electrolyte']['concentration_M'] = conc
        cfg['electrolyte']['volume_ml']        = vol
        cfg['electrolyte']['electrons_transferred'] = int(n_e) if n_e.is_integer() else n_e

        info = cfg.setdefault('experiment_info', {})
        for attr, key in [('_v_id', 'id'), ('_v_date', 'date'),
                          ('_v_operator', 'operator'), ('_v_desc', 'description')]:
            val = getattr(self, attr).get().strip()
            if val:
                info[key] = val
        info['potentiostat'] = self._v_potstat.get()

        cond = cfg.setdefault('experimental_conditions', {})
        try:
            cond['flow_rate_ml_min'] = float(self._v_flow.get())
        except ValueError:
            pass

        mem = self._v_mem.get().strip()
        if mem:
            cfg.setdefault('cell_components', {}).setdefault('membrane', {})['name'] = mem

        (self.experiment_folder / 'config.json').write_text(
            json.dumps(cfg, indent=4, ensure_ascii=False), encoding='utf-8')
        self.saved = True
        self.destroy()


# ── LauncherApp ───────────────────────────────────────────────────────────────

class LauncherApp:
    """Main launcher window — orchestrates the full VRFB pipeline."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title('VRFB Report Maker')
        self.root.geometry(f'{WIN_W}x{WIN_H}')
        self.root.configure(bg=BG)
        self.root.resizable(True, True)

        self.experiment_folder: pathlib.Path | None = None
        self.workspace_root:    pathlib.Path | None = None
        self._subprocess:       subprocess.Popen | None = None
        self._worker_thread:    threading.Thread | None = None

        # populated in _build_workflow_panel
        self._rows: dict[str, StepRow] = {}
        self._log:  LogPanel

        self._build_ui()
        self._load_prefs()

    # ── UI assembly ───────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._build_header()
        tk.Frame(self.root, bg='#333333', height=1).pack(fill=tk.X)
        self._build_main_pane()
        tk.Frame(self.root, bg='#333333', height=1).pack(fill=tk.X)
        self._build_status_bar()

    def _build_header(self) -> None:
        bar = tk.Frame(self.root, bg=BG_HEAD, height=60)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)
        tk.Label(bar, text='VRFB Report Maker', bg=BG_HEAD, fg=FG,
                 font=('Helvetica', 14, 'bold'),
                 ).pack(side=tk.LEFT, padx=18, pady=16)
        tk.Label(bar, text='Workflow Launcher', bg=BG_HEAD, fg=FG_DIM,
                 font=('Helvetica', 9),
                 ).pack(side=tk.LEFT, pady=16)

    def _build_main_pane(self) -> None:
        pane = tk.Frame(self.root, bg=BG)
        pane.pack(fill=tk.BOTH, expand=True)

        self._build_left_panel(pane)
        tk.Frame(pane, bg='#333333', width=1).pack(side=tk.LEFT, fill=tk.Y)
        self._build_right_panel(pane)

    def _build_left_panel(self, parent: tk.Frame) -> None:
        left = tk.Frame(parent, bg=BG, width=270)
        left.pack(side=tk.LEFT, fill=tk.Y)
        left.pack_propagate(False)

        # workspace bar
        ws_bar = tk.Frame(left, bg=BG_DARK, padx=8, pady=6)
        ws_bar.pack(fill=tk.X)
        tk.Label(ws_bar, text='Workspace', bg=BG_DARK, fg=FG_DIM,
                 font=('Helvetica', 8)).pack(anchor='w')
        btn_row = tk.Frame(ws_bar, bg=BG_DARK)
        btn_row.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(btn_row, text='Browse…', width=10,
                   command=self._browse_workspace).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn_row, text='+ New', width=8,
                   command=self._new_experiment).pack(side=tk.LEFT)

        tk.Frame(left, bg='#333333', height=1).pack(fill=tk.X)

        # workspace path label
        self._lbl_ws = tk.Label(left, text='— no workspace —', bg=BG_DARK,
                                fg=FG_DIM, font=('Courier', 7),
                                anchor='w', wraplength=258, justify='left')
        self._lbl_ws.pack(fill=tk.X, padx=8, pady=3)

        tk.Frame(left, bg='#333333', height=1).pack(fill=tk.X)

        # experiment list
        list_frame = tk.Frame(left, bg=BG_DARK)
        list_frame.pack(fill=tk.BOTH, expand=True)

        self._exp_listbox = tk.Listbox(
            list_frame, bg=BG_DARK, fg=FG,
            selectbackground='#37373d', selectforeground=FG,
            font=('Helvetica', 9), relief='flat', borderwidth=0,
            activestyle='none',
        )
        sb = ttk.Scrollbar(list_frame, command=self._exp_listbox.yview)
        self._exp_listbox.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._exp_listbox.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        self._exp_listbox.bind('<<ListboxSelect>>', self._on_experiment_selected)

        tk.Frame(left, bg='#333333', height=1).pack(fill=tk.X)
        self._lbl_exp = tk.Label(left, text='— no experiment selected —',
                                 bg=BG_DARK, fg=FG_DIM,
                                 font=('Helvetica', 8), anchor='w',
                                 wraplength=258, justify='left')
        self._lbl_exp.pack(fill=tk.X, padx=8, pady=4)

    def _build_right_panel(self, parent: tk.Frame) -> None:
        right = tk.Frame(parent, bg=BG)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # scrollable workflow area
        canvas = tk.Canvas(right, bg=BG, highlightthickness=0)
        vsb = ttk.Scrollbar(right, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        workflow_frame = tk.Frame(canvas, bg=BG)
        canvas.create_window((0, 0), window=workflow_frame, anchor='nw')
        workflow_frame.bind('<Configure>',
                            lambda e: canvas.configure(
                                scrollregion=canvas.bbox('all')))

        self._build_workflow_panel(workflow_frame)

        tk.Frame(right, bg='#333333', height=1).pack(fill=tk.X, side=tk.BOTTOM)
        self._log = LogPanel(right)

    def _build_workflow_panel(self, parent: tk.Frame) -> None:
        tk.Label(parent, text='Pipeline steps', bg=BG, fg=FG_DIM,
                 font=('Helvetica', 8), anchor='w',
                 ).pack(fill=tk.X, padx=20, pady=(10, 4))

        actions = {
            'config':     self._action_config,
            'parse':      self._action_parse,
            'ocv_export': self._action_ocv_export,
            'calibrate':  self._action_calibrate,
            'convert':    self._action_convert,
            'align':      self._action_align,
            'postproc':   self._action_postproc,
            'results':    self._action_view_results,
        }

        for step in STEPS:
            tk.Frame(parent, bg='#333333', height=1).pack(fill=tk.X, padx=20)
            row = StepRow(parent,
                          num=step['num'],
                          name=step['name'],
                          desc=step['desc'],
                          btn_text=step['btn'],
                          command=actions[step['id']])
            self._rows[step['id']] = row

        tk.Frame(parent, bg='#333333', height=1).pack(fill=tk.X, padx=20)

    def _build_status_bar(self) -> None:
        bar = tk.Frame(self.root, bg=BG_DARK, height=28)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        bar.pack_propagate(False)
        self._lbl_status = tk.Label(bar, text='Select or create an experiment to begin.',
                                    bg=BG_DARK, fg=FG_DIM,
                                    font=('Courier', 8), anchor='w')
        self._lbl_status.pack(side=tk.LEFT, padx=10)
        tk.Label(bar, text=f'v{VERSION}', bg=BG_DARK, fg=FG_DIM,
                 font=('Helvetica', 7)).pack(side=tk.RIGHT, padx=10)

    # ── Prefs ─────────────────────────────────────────────────────────────────

    def _load_prefs(self) -> None:
        try:
            data = json.loads(PREFS_FILE.read_text(encoding='utf-8'))
            p = pathlib.Path(data.get('workspace_root', ''))
            if p.is_dir():
                self.workspace_root = p
                self._lbl_ws.config(text=str(p))
                self._refresh_experiment_list()
        except Exception:
            pass

    def _save_prefs(self) -> None:
        try:
            PREFS_FILE.write_text(
                json.dumps({'workspace_root': str(self.workspace_root)}, indent=2),
                encoding='utf-8')
        except Exception:
            pass

    # ── Experiment browser ────────────────────────────────────────────────────

    def _browse_workspace(self) -> None:
        folder = filedialog.askdirectory(title='Select workspace folder')
        if not folder:
            return
        self.workspace_root = pathlib.Path(folder)
        self._lbl_ws.config(text=str(self.workspace_root))
        self._save_prefs()
        self._refresh_experiment_list()

    def _refresh_experiment_list(self) -> None:
        self._exp_listbox.delete(0, tk.END)
        if not self.workspace_root or not self.workspace_root.is_dir():
            return
        experiments = sorted(
            d.name for d in self.workspace_root.iterdir()
            if d.is_dir() and (d / 'config.json').exists()
        )
        for name in experiments:
            self._exp_listbox.insert(tk.END, f'  {name}')

    def _on_experiment_selected(self, _event=None) -> None:
        sel = self._exp_listbox.curselection()
        if not sel or not self.workspace_root:
            return
        name = self._exp_listbox.get(sel[0]).strip()
        folder = self.workspace_root / name
        if not folder.is_dir():
            return
        self.experiment_folder = folder
        self._lbl_exp.config(text=name, fg=FG)
        self._set_status(f'Experiment: {name}')
        self._scan_and_update_steps()

    def _new_experiment(self) -> None:
        if not self.workspace_root:
            messagebox.showinfo('No workspace',
                                'Set a workspace folder first.', parent=self.root)
            return
        name = simpledialog.askstring('New Experiment',
                                      'Enter experiment folder name:',
                                      parent=self.root)
        if not name or not name.strip():
            return
        name = name.strip()
        folder = self.workspace_root / name
        try:
            for sub in ('01_Raw_data', '02_Parsed_data',
                        '03_Processed_data', '04_Results'):
                (folder / sub).mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            messagebox.showerror('Error', f'Could not create folders:\n{exc}',
                                 parent=self.root)
            return

        self._refresh_experiment_list()

        # Select the new experiment in the list
        for i in range(self._exp_listbox.size()):
            if self._exp_listbox.get(i).strip() == name:
                self._exp_listbox.selection_clear(0, tk.END)
                self._exp_listbox.selection_set(i)
                self._exp_listbox.see(i)
                break
        self.experiment_folder = folder
        self._lbl_exp.config(text=name, fg=FG)
        self._scan_and_update_steps()

        # Immediately open config editor
        self._action_config()

    # ── Status detection ──────────────────────────────────────────────────────

    def _read_config(self) -> dict:
        if not self.experiment_folder:
            return {}
        try:
            return json.loads(
                (self.experiment_folder / 'config.json').read_text(encoding='utf-8'))
        except Exception:
            return {}

    def _find_cycles_csv(self) -> pathlib.Path | None:
        if not self.experiment_folder:
            return None
        parsed = self.experiment_folder / '02_Parsed_data'
        for candidate in [parsed / 'cycles.csv',
                          parsed / 'potentiostat' / 'cycles.csv']:
            if candidate.exists():
                return candidate
        return None

    def _get_potentiostat_type(self) -> str:
        cfg = self._read_config()
        return cfg.get('experiment_info', {}).get('potentiostat', '')


    def _find_volumes_csv(self) -> pathlib.Path | None:
        if not self.experiment_folder:
            return None
        processed = self.experiment_folder / '03_Processed_data'
        for name in ('volumes_pixels.csv', 'pixel_heights.csv'):
            p = processed / name
            if p.exists():
                return p
        return None

    def _scan_and_update_steps(self) -> None:
        if not self.experiment_folder:
            for row in self._rows.values():
                row.set_status('waiting')
                row.set_button_state(False)
            return

        folder    = self.experiment_folder
        parsed    = folder / '02_Parsed_data'
        processed = folder / '03_Processed_data'
        results   = folder / '04_Results'
        raw       = folder / '01_Raw_data'

        # config
        cfg = self._read_config()
        config_ok = bool(
            cfg
            and 'electrolyte' in cfg
            and 'concentration_M' in cfg['electrolyte']
            and 'volume_ml' in cfg['electrolyte']
        )

        # parse
        cycles_path  = self._find_cycles_csv()
        metrics_path = processed / 'step_metrics.csv'
        parse_ok     = cycles_path is not None and metrics_path.exists()

        # ocv_export
        ptype         = self._get_potentiostat_type()
        ocv_raw_dir   = raw / 'OCV'
        ocv_log_path  = parsed / 'OCV' / 'ocv_log.csv'
        ocv_has_raw   = ocv_raw_dir.is_dir() and any(ocv_raw_dir.glob('*.txt'))
        ocv_applicable = (ptype == 'Yarst') and ocv_has_raw
        ocv_ok         = ocv_log_path.exists()

        # calibrate
        frames_dir        = raw / 'frames'
        frames_applicable = frames_dir.is_dir() and any(frames_dir.glob('*.jpg'))
        calibrate_ok      = self._find_volumes_csv() is not None

        # convert
        color_dir        = raw / 'color'
        color_applicable = color_dir.is_dir() and any(color_dir.glob('*.txt'))
        color_merged     = parsed / 'colors_volumes_merged.csv'
        convert_ok       = color_merged.exists()

        # align
        aligned_ok = (parsed / 'all_data_aligned.csv').exists()

        # postproc / results
        plots_ok = results.is_dir() and any(results.glob('*.png'))

        # build state map
        states: dict[str, str] = {}

        states['config'] = 'done' if config_ok else 'ready'

        if not config_ok:
            states['parse'] = 'waiting'
        elif parse_ok:
            states['parse'] = 'done'
        else:
            states['parse'] = 'ready'

        if not ocv_applicable:
            states['ocv_export'] = 'na'
        elif not parse_ok:
            states['ocv_export'] = 'waiting'
        elif ocv_ok:
            states['ocv_export'] = 'done'
        else:
            states['ocv_export'] = 'ready'

        if not frames_applicable:
            states['calibrate'] = 'na'
        elif not parse_ok:
            states['calibrate'] = 'waiting'
        elif calibrate_ok:
            states['calibrate'] = 'done'
        else:
            states['calibrate'] = 'ready'

        if not color_applicable:
            states['convert'] = 'na'
        elif not parse_ok:
            states['convert'] = 'waiting'
        elif convert_ok:
            states['convert'] = 'done'
        else:
            states['convert'] = 'ready'

        if not parse_ok:
            states['align'] = 'waiting'
        elif aligned_ok:
            states['align'] = 'done'
        else:
            states['align'] = 'ready'

        if not parse_ok:
            states['postproc'] = 'waiting'
        elif plots_ok:
            states['postproc'] = 'done'
        else:
            states['postproc'] = 'ready'

        states['results'] = 'ready' if plots_ok else 'waiting'

        self._apply_step_states(states)

    def _apply_step_states(self, states: dict[str, str]) -> None:
        for step in STEPS:
            sid   = step['id']
            row   = self._rows[sid]
            state = states.get(sid, 'waiting')
            row.set_status(state)
            if state == 'na':
                row.set_button_state(False)
                row.set_button_text('N/A')
            elif state in ('done', 'ready'):
                row.set_button_state(True)
                if state == 'done' and sid != 'results':
                    row.set_button_text('Re-run')
                else:
                    row.set_button_text(step['btn'])
            else:
                row.set_button_state(False)
                row.set_button_text(step['btn'])

    # ── Thread / subprocess helpers ───────────────────────────────────────────

    def _set_status(self, text: str, color: str = FG_DIM) -> None:
        self._lbl_status.config(text=text, fg=color)

    def _set_all_buttons_enabled(self, enabled: bool) -> None:
        for row in self._rows.values():
            row.set_button_state(enabled)

    def _run_in_thread(self, fn, on_done, on_error) -> None:
        """Run fn() in a daemon thread; call on_done/on_error on the main thread."""
        self._set_all_buttons_enabled(False)

        def wrapper():
            try:
                fn()
                self.root.after(0, on_done)
            except Exception as exc:
                self.root.after(0, lambda e=exc: on_error(e))

        self._worker_thread = threading.Thread(target=wrapper, daemon=True)
        self._worker_thread.start()

    def _poll_subprocess(self, step_id: str) -> None:
        if self._subprocess and self._subprocess.poll() is None:
            self.root.after(500, lambda: self._poll_subprocess(step_id))
        else:
            self._subprocess = None
            self._scan_and_update_steps()
            self._set_status('Ready.')

    def _log_line(self, text: str, tag: str = 'dim') -> None:
        """Thread-safe log append (wraps root.after)."""
        self.root.after(0, lambda: self._log.append(text, tag))

    # ── Step action handlers ──────────────────────────────────────────────────

    def _action_config(self) -> None:
        if not self.experiment_folder:
            return
        dialog = ConfigEditorDialog(self.root, self.experiment_folder)
        self.root.wait_window(dialog)
        if dialog.saved:
            self._scan_and_update_steps()
            self._set_status('Config saved.', FG_OK)

    def _action_parse(self) -> None:
        if not self.experiment_folder:
            return
        ptype = self._get_potentiostat_type()
        if ptype not in PARSERS:
            messagebox.showerror('Config error',
                                 f'Unknown potentiostat type {ptype!r}. Set one of '
                                 f'{", ".join(PARSERS)} in config (Metadata tab) first.',
                                 parent=self.root)
            return

        folder  = self.experiment_folder
        raw_dir = folder / '01_Raw_data' / 'potentiostat'
        if not raw_dir.is_dir():
            messagebox.showerror('Not found',
                                 f'Raw data folder not found:\n{raw_dir}',
                                 parent=self.root)
            return

        file_out = folder / '02_Parsed_data' / 'potentiostat' / 'cycles.csv'

        self._log.clear()
        self._log_line(f'Parsing {ptype} data from {raw_dir}\n', 'warn')
        self._rows['parse'].set_status('running')
        self._set_status('Parsing…', FG_WARN)

        def work():
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                parser = PARSERS[ptype](folder)
                parser.read_data(raw_dir)
                parser.process_data(parser.data)
                file_out.parent.mkdir(parents=True, exist_ok=True)
                parser.write_csv_data(parser.data, file_out)

            self._log_line(buf.getvalue() or 'Done.\n', 'dim')

        def done():
            self._log_line(f'✓ Saved → {file_out.name}\n', 'ok')
            self._scan_and_update_steps()
            self._set_status('Parsing complete.', FG_OK)

        def error(exc: Exception):
            self._log_line(f'ERROR: {exc}\n', 'err')
            self._scan_and_update_steps()
            self._set_status(f'Parser error: {exc}', FG_ERR)
            messagebox.showerror('Parse error', str(exc), parent=self.root)

        self._run_in_thread(work, done, error)

    def _action_ocv_export(self) -> None:
        if not self.experiment_folder:
            return
        folder       = self.experiment_folder
        ocv_raw_dir  = folder / '01_Raw_data' / 'OCV'
        ocv_log_out  = folder / '02_Parsed_data' / 'OCV' / 'ocv_log.csv'

        self._log.clear()
        self._log_line('Exporting OCV data…\n', 'warn')
        self._rows['ocv_export'].set_status('running')
        self._set_status('Exporting OCV…', FG_WARN)

        def work():
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                parser = YarstParser(folder)
                parser.export_ocv_data(ocv_raw_dir, ocv_log_out)
            self._log_line(buf.getvalue() or 'Done.\n', 'dim')

        def done():
            self._log_line(f'✓ Saved → {ocv_log_out.name}\n', 'ok')
            self._scan_and_update_steps()
            self._set_status('OCV export complete.', FG_OK)

        def error(exc: Exception):
            self._log_line(f'ERROR: {exc}\n', 'err')
            self._scan_and_update_steps()
            self._set_status(f'OCV export error: {exc}', FG_ERR)

        self._run_in_thread(work, done, error)

    def _action_calibrate(self) -> None:
        if self._subprocess and self._subprocess.poll() is None:
            messagebox.showinfo('Already running',
                                'Calibration wizard is already open.',
                                parent=self.root)
            return
        script = SCRIPT_DIR / 'gui' / 'calibration_gui.py'
        self._subprocess = subprocess.Popen([sys.executable, str(script)])
        self._rows['calibrate'].set_status('running')
        self._set_status('Calibration wizard open — close it when done.', FG_WARN)
        self.root.after(500, lambda: self._poll_subprocess('calibrate'))

    def _action_convert(self) -> None:
        if not self.experiment_folder:
            return
        folder   = self.experiment_folder
        vol_path = self._find_volumes_csv()
        if vol_path is None:
            messagebox.showerror('Not found',
                                 'No volumes CSV found in 03_Processed_data/.\n'
                                 'Run the frame calibration step first.',
                                 parent=self.root)
            return

        color_dir = folder / '01_Raw_data' / 'color'
        color_files = list(color_dir.glob('*.txt'))
        if not color_files:
            messagebox.showerror('Not found',
                                 f'No .txt file found in:\n{color_dir}',
                                 parent=self.root)
            return
        color_path = color_files[0]
        out_path   = folder / '02_Parsed_data' / 'colors_volumes_merged.csv'

        self._log.clear()
        self._log_line(f'Converting color/volume data…\n  volumes: {vol_path.name}'
                       f'\n  color:   {color_path.name}\n', 'warn')
        self._rows['convert'].set_status('running')
        self._set_status('Converting…', FG_WARN)

        def work():
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                series_data_converter.process_experiment_data(
                    vol_path, color_path, out_path)
            self._log_line(buf.getvalue() or 'Done.\n', 'dim')

        def done():
            self._log_line(f'✓ Saved → {out_path.name}\n', 'ok')
            self._scan_and_update_steps()
            self._set_status('Conversion complete.', FG_OK)

        def error(exc: Exception):
            self._log_line(f'ERROR: {exc}\n', 'err')
            self._scan_and_update_steps()
            self._set_status(f'Conversion error: {exc}', FG_ERR)
            messagebox.showerror('Conversion error', str(exc), parent=self.root)

        self._run_in_thread(work, done, error)

    def _action_align(self) -> None:
        if self._subprocess and self._subprocess.poll() is None:
            messagebox.showinfo('Already running',
                                'Alignment GUI is already open.',
                                parent=self.root)
            return
        script = SCRIPT_DIR / 'gui' / 'alignment_gui.py'
        self._subprocess = subprocess.Popen([sys.executable, str(script)])
        self._rows['align'].set_status('running')
        self._set_status('Alignment GUI open — export the CSV when done.', FG_WARN)
        self.root.after(500, lambda: self._poll_subprocess('align'))

    def _action_postproc(self) -> None:
        if not self.experiment_folder:
            return
        if self._subprocess and self._subprocess.poll() is None:
            messagebox.showinfo('Already running',
                                'Post-processing GUI is already open.',
                                parent=self.root)
            return
        script = SCRIPT_DIR / 'gui' / 'postprocessing_gui.py'
        self._subprocess = subprocess.Popen(
            [sys.executable, str(script), str(self.experiment_folder)])
        self._rows['postproc'].set_status('running')
        self._set_status('Post-processing GUI open.', FG_WARN)
        self.root.after(500, lambda: self._poll_subprocess('postproc'))

    def _action_view_results(self) -> None:
        if not self.experiment_folder:
            return
        results = self.experiment_folder / '04_Results'
        results.mkdir(exist_ok=True)
        try:
            if sys.platform == 'win32':
                os.startfile(str(results))
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', str(results)])
            else:
                subprocess.Popen(['xdg-open', str(results)])
        except Exception as exc:
            messagebox.showerror('Error', f'Could not open folder:\n{exc}',
                                 parent=self.root)


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

    root = tk.Tk()
    try:
        ttk.Style(root).theme_use('clam')
    except tk.TclError:
        pass

    LauncherApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
