# Flow Battery Report Maker

Processing and plotting tools for **redox flow battery cycling experiments**, developed by the FlowBat team at Skoltech for vanadium RFBs (VRFBs), but chemistry-agnostic in the parts that matter.

You give it a folder of raw potentiostat files plus a short `config.json`. It returns:

- per-step capacity, average voltage and energy, and the rest-period OCV
- per-cycle coulombic, voltage and energy efficiency, and electrolyte utilisation
- charge–discharge curves vs. SoC, efficiency-vs-cycle and OCV-vs-cycle plots
- optionally, data from extra sensors (a webcam for tank levels, a color sensor, a separate OCV cell) aligned onto the same time axis

## Install

Requires Python 3.10+.

```bash
git clone <this repo>
cd report_maker
python -m venv .venv
.venv/Scripts/activate          # Windows;  source .venv/bin/activate on Linux/macOS
pip install -r requirements.txt
```

## Quick start

**GUI.** Run `python main_gui.py`, pick the folder that contains your experiments, select one, and work through the numbered steps. Each step shows whether its inputs and outputs already exist.

**Script.** This does the same core work without a GUI:

```bash
python -m examples.run_pipeline path/to/my_experiment
```

[examples/run_pipeline.py](examples/run_pipeline.py) is about 50 lines long. It's the best place to see how the library pieces fit together.

## Experiment folder

Each experiment is one self-contained folder. You only create `config.json` and `01_Raw_data/`; everything else is generated.

```
my_experiment/
├── config.json
├── 01_Raw_data/
│   ├── potentiostat/     # raw cycling files (*.txt)          — required
│   ├── OCV/              # separate OCV-cell logger            — optional
│   ├── color/            # color sensor log (colors.txt)       — optional
│   └── frames/           # webcam JPEGs, YYYYmmdd_HHMMSS.jpg   — optional
├── 02_Parsed_data/       # cycles.csv, ocv_log.csv, aligned data
├── 03_Processed_data/    # step_metrics.csv, ocv_measurements.csv, efficiencies.csv
└── 04_Results/           # plots
```

Keep experiment data **outside** this repository, or name local experiment folders `exp_*` so git ignores them.

## `config.json`

Copy [examples/config.example.json](examples/config.example.json). Only the `electrolyte` block is used in calculations:

| Key | Required | Meaning |
|---|---|---|
| `electrolyte.concentration_M` | yes | Active-species concentration in the capacity-limiting electrolyte, mol/L |
| `electrolyte.volume_ml` | yes | Volume of that electrolyte, mL |
| `electrolyte.electrons_transferred` | no (default 1) | Electrons per active molecule, *n* |
| `experiment_info.potentiostat` | for the GUI | Which parser to use: `Yarst` or `Elins` |
| `plotting.voltage_limits_V` | no | Y-axis range of the charge–discharge plot, e.g. `[0.8, 1.65]`; autoscale if absent |

Everything else (cell components, flow rate, operator…) is free-form metadata.

The theoretical capacity is computed as **Q = n·F·c·V**. For vanadium, n = 1. For other chemistries, set `electrons_transferred` and use the concentration and volume of whichever side limits capacity.

## What the numbers mean

- **Steps and cycles.** Each potentiostat step with non-zero current is labelled charge (`ch`) if its first current point is **positive**, and discharge (`dch`) otherwise. A new cycle starts at every discharge → charge transition. Zero-current steps count as rests, and their last voltage is stored as the OCV.
- **Capacity** is recomputed from current by trapezoidal integration (instrument-reported Ah is ignored). **Average voltage** is the mean of the sampled points, so it assumes roughly uniform sampling.
- **SoC** = |Q| / Q_theory, counted from the start of each step.
- **Efficiencies (per cycle):**
  - CE = Q_dch / Q_ch
  - VE = V̄_dch / V̄_ch
  - EE = CE · VE
  - utilisation = Q_dch / Q_theory

**Output column names.** `cycles.csv` is wide-format, with one column group per cycle and step: `001_ch_t_s, 001_ch_SoC, 001_ch_U_V, 001_ch_I_A, 001_ch_Q_C, 001_dch_t_s, …`. Time restarts at 0 for every step.

## Adapting it to your lab

### A different potentiostat

Supported out of the box are **YARST** and **Elins (ES8 software)**. Both export CP1251 text with Russian headers. For anything else, write a small parser (about 30 lines for a CSV export):

```python
# core/my_parser.py
import pathlib
import pandas as pd
from core.base_parser import BaseParser, ExperStep

class MyParser(BaseParser):
    def read_data(self, raw_dir: pathlib.Path) -> list[ExperStep]:
        self.data = []
        for fn in sorted(raw_dir.glob('*.csv')):
            df = pd.read_csv(fn)
            for (cycle, step), g in df.groupby(['cycle', 'step'], sort=False):
                self.data.append(ExperStep(
                    index=(cycle, step),
                    t=(g['time_s'] - g['time_s'].iloc[0]).tolist(),   # seconds from step start
                    U=g['voltage_V'].tolist(),
                    I=g['current_A'].tolist(),                        # charge must be positive
                    Q=[float('nan')] * len(g),                        # recomputed later
                ))
        return self.data
```

Then register it in [core/parsers.py](core/parsers.py):

```python
PARSERS = {'Yarst': YarstParser, 'Elins': ElinsParser, 'MyLab': MyParser}
```

After that, `"potentiostat": "MyLab"` in `config.json` works in both the GUI and the example script. Everything downstream (metrics, efficiencies, plots, alignment) operates on `ExperStep` lists, so it needs no changes.

### The optional sensors

Steps 3–6 in the launcher are tied to our hardware. Skip them if you only have potentiostat data; post-processing works without them.

| Step | Input | Tool |
|---|---|---|
| OCV log | YARST files from a second channel on an OCV cell | `YarstParser.export_ocv_data` |
| Tank levels | Webcam JPEGs of both tanks | `gui/calibration_gui.py`: rotate, crop, threshold, then batch-measure liquid height in pixels |
| Color + volume merge | Color sensor log + pixel heights | `core/series_data_converter.py` |
| Alignment | Everything above + `cycles.csv` | `gui/alignment_gui.py`: sliders for each sensor's time offset |

The sensors run on independent clocks, so alignment builds one master timeline from the potentiostat steps (plus rest periods) and shifts each sensor by a manually tuned offset.

## Code layout

```
main_gui.py                  launcher: runs the steps in order, shows status
core/                        library (no GUI code)
  base_parser.py             ExperStep, BaseParser: coulomb counting, metrics, cycles.csv
  parsers.py                 potentiostat registry — add new parsers here
  yarst_parser.py            YARST format
  elins_parser.py            Elins / ES8 format
  post_processing.py         efficiencies and static plots
  frames_to_volumes.py       OpenCV tank-level pipeline
  series_data_converter.py   merge webcam volumes with color sensor data
  align_cycles_data.py       master timeline + sensor interpolation
gui/                         standalone tool windows (calibration, alignment, post-processing)
examples/                    config template and a scripted pipeline
```

Each `core/*_parser.py`, `align_cycles_data.py` and `series_data_converter.py` also runs from the command line with an experiment folder argument, e.g. `python -m core.yarst_parser path/to/my_experiment`.

## Authors

Andrey Novikov, Nikita Buriak, Ilia Khristoforov (FlowBat team, Skoltech).
