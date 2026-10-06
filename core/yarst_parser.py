#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Parser for YARST potentiostat [https://yarst.org/prod] text exports.

@authors: Andrey Novikov, Nikita Buriak, Ilia Khristoforov
"""

import argparse
import pathlib
import re
import csv
from collections.abc import Iterator
from core.base_parser import ExperStep, BaseParser

# YARST exports are CP1251 text with a Russian column header:
# cycle, step, time (s), U (V), I (A), T (°C), ESR (mOhm), Q (Ah), E (Wh)
ENCODING = 'cp1251'
RE_HEADER = re.compile(r"   Цикл   Шаг  Время,s    U,V    I,A T,°C ESR,mR   Q,Ah   E,Wh")
INTERRUPTED_MARKER = "Прервано"  # "Interrupted" — written when a run is stopped manually
N_COLUMNS = 9


def _iter_rows(fn: pathlib.Path) -> Iterator[tuple[int, list[str]]]:
    """
    Yields (line number, split values) for every data row of a YARST file.

    Skips everything up to and including the column header, and stops at the
    "interrupted" marker.
    """
    with fn.open('r', encoding=ENCODING) as file:
        is_head = True
        for nline, line in enumerate(file, start=1):
            line = line.rstrip('\n')
            if not line:
                continue
            if is_head:
                is_head = not RE_HEADER.fullmatch(line)
                continue
            vals = line.split()
            if vals[0] == INTERRUPTED_MARKER:
                return
            yield nline, vals


class YarstParser(BaseParser):
    def read_data(self, raw_dir: pathlib.Path) -> list[ExperStep]:
        """
        Reads experimental data from YARST potentiostat files.

        Args:
            raw_dir (pathlib.Path): Directory containing the raw *.txt files,
                read in alphabetical order.

        Returns:
            list[ExperStep]: A list of ExperStep objects containing the parsed data.

        Raises:
            ValueError: If a data row does not have the expected number of columns.
        """
        data: list[ExperStep] = []

        for fn in sorted(raw_dir.glob('*.txt')):
            cycle = ""
            step = ""
            for nline, vals in _iter_rows(fn):
                if len(vals) != N_COLUMNS:
                    raise ValueError(f"Unexpected number of values {vals} at {fn} line #{nline}")

                if vals[0] != cycle or vals[1] != step:
                    cycle, step = vals[0], vals[1]
                    data.append(ExperStep(index=(cycle, step), t=[], U=[], I=[], Q=[]))

                data[-1].t.append(float(vals[2]))
                data[-1].U.append(float(vals[3]))
                data[-1].I.append(float(vals[4]))
                data[-1].Q.append(float('nan'))

        self.data = data
        return data

    def export_ocv_data(self, dir_in: pathlib.Path, file_out: pathlib.Path) -> None:
        """
        Concatenates YARST files from a separate OCV channel into one continuous
        time/voltage log. Time restarts at zero in every file, so each file is
        shifted by the accumulated duration of the previous ones.

        Args:
            dir_in (pathlib.Path): Folder with raw OCV files (usually 01_Raw_data/OCV).
            file_out (pathlib.Path): Output CSV with columns time_s, voltage_V.
        """
        if not dir_in.exists():
            print(f"::: WARNING: OCV folder '{dir_in}' does not exist. Skipping OCV export.")
            return

        print(f"::: Exporting OCV data from '{dir_in}' to '{file_out}'...")
        file_out.parent.mkdir(parents=True, exist_ok=True)

        time_offset = 0.0
        with file_out.open('w', encoding='utf-8', newline='') as csv_out:
            writer = csv.writer(csv_out)
            writer.writerow(['time_s', 'voltage_V'])

            for fn in sorted(dir_in.glob('*.txt')):
                file_max_time = 0.0
                for _, vals in _iter_rows(fn):
                    if len(vals) != N_COLUMNS:
                        continue
                    try:
                        rel_time = float(vals[2])
                        voltage = float(vals[3])
                    except ValueError:
                        continue
                    writer.writerow([rel_time + time_offset, voltage])
                    file_max_time = rel_time
                time_offset += file_max_time

        print(f"::: OCV export finished. Total duration parsed: {time_offset:.2f} s")


def main():
    """
    Command-line entry point: parse a YARST experiment folder.

    Reads 01_Raw_data/potentiostat/*.txt, writes step_metrics.csv,
    ocv_measurements.csv and cycles.csv, plus ocv_log.csv if 01_Raw_data/OCV exists.
    """
    parser = argparse.ArgumentParser(
        prog="yarst_parser",
        description="Parse YARST potentiostat data for redox flow battery cycling experiments",
        epilog="(c) FlowBat team, Skoltech")
    parser.add_argument('experiment_folder', metavar="EXPERIMENT_FOLDER",
                        help='experiment folder containing raw data, config.json, etc.')
    args = parser.parse_args()

    experiment_folder = pathlib.Path(args.experiment_folder)
    dir_in = experiment_folder / '01_Raw_data' / 'potentiostat'
    file_out = experiment_folder / '02_Parsed_data' / 'potentiostat' / 'cycles.csv'
    dir_in_ocv = experiment_folder / '01_Raw_data' / 'OCV'
    file_out_ocv = experiment_folder / '02_Parsed_data' / 'OCV' / 'ocv_log.csv'

    yarst_parser = YarstParser(experiment_folder)

    print(f"::: Reading YARST experimental data files '{dir_in}/*.txt'")
    yarst_parser.read_data(dir_in)
    yarst_parser.process_data(yarst_parser.data)

    print(f"::: Writing processed data to '{file_out}'")
    yarst_parser.write_csv_data(yarst_parser.data, file_out)

    yarst_parser.export_ocv_data(dir_in_ocv, file_out_ocv)


if __name__ == '__main__':
    main()
