"""
Minimal scripted pipeline (no GUI): parse potentiostat data, compute
efficiencies, save the standard plots.

Usage (from the repository root):
    python -m examples.run_pipeline path/to/experiment_folder
"""

import argparse
import json
import pathlib

import matplotlib
matplotlib.use('Agg')  # save figures without opening windows

from core import post_processing as pp
from core.parsers import PARSERS


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('experiment_folder', type=pathlib.Path)
    args = ap.parse_args()
    folder: pathlib.Path = args.experiment_folder

    # 1. Parse: raw files -> step_metrics.csv, ocv_measurements.csv, cycles.csv
    config = json.loads((folder / 'config.json').read_text(encoding='utf-8'))
    parser = PARSERS[config['experiment_info']['potentiostat']](folder)
    parser.read_data(parser.raw_data_path / 'potentiostat')
    parser.process_data(parser.data)
    cycles_csv = parser.parsed_data_path / 'potentiostat' / 'cycles.csv'
    parser.write_csv_data(parser.data, cycles_csv)

    capacity_C, capacity_Ah = parser.calculate_nominal_capacity()
    print(f"Theoretical capacity: {capacity_Ah * 1000:.1f} mAh")

    # 2. Efficiencies per cycle
    results = folder / '04_Results'
    results.mkdir(exist_ok=True)
    eff = pp.calculate_efficiencies(parser.processed_data_path / 'step_metrics.csv',
                                    q_theory_mAh=capacity_Ah * 1000)
    eff.to_csv(parser.processed_data_path / 'efficiencies.csv', index=False)

    # 3. Plots
    pp.plot_efficiencies(eff, results / 'efficiencies.png')
    pp.plot_ocv(pp.reshape_ocv_data(parser.processed_data_path / 'ocv_measurements.csv'),
                results / 'ocv.png')
    n_cycles = pp.get_total_cycles(cycles_csv)
    pp.plot_charge_discharge_cycles(cycles_csv, capacity_C, results / 'charge_discharge.png',
                                    start_cycle=1, end_cycle=n_cycles,
                                    cycle_step=max(1, n_cycles // 10),
                                    voltage_limits=config.get('plotting', {}).get('voltage_limits_V'))


if __name__ == '__main__':
    main()
