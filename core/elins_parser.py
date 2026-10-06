#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Parser for Elins potentiostat files exported by the ES8 software (www.potentiostat.ru).

@author: Andrey Novikov, Nikita Buriak, Ilia Khristoforov
"""

import argparse
import pathlib
import re
from enum import Enum
from core.base_parser import ExperStep, BaseParser

# ES8 exports are CP1251 text with Russian section headers:
#   "Блок N..."             -> block N
#   "Цикл C, Шаг S"         -> cycle C, step S
#   "Время, с., Потенциал, В, Ток, А" -> columns: time (s), potential (V), current (A)
# Decimal separator is a comma; a line containing only "u" ends a block.
ENCODING = 'cp1251'
RE_BLOCK = re.compile(r'Блок ([0-9]+)\w+')
RE_STEP = re.compile(r'Цикл ([0-9]+), Шаг ([0-9]+)')
RE_VALUES_HEADER = re.compile(r'Время, с.,[ ]+Потенциал, В,[ ]+Ток, А')


class State(Enum):
    BLOCK_START = 1
    BLOCK_HEADER_START = 2
    STEP_START = 3
    DATA_START = 4
    VALUES = 5


class ElinsParser(BaseParser):
    def read_data(self, raw_dir: pathlib.Path) -> list[ExperStep]:
        """
        Reads electrochemical data from ES8 export files.

        Args:
            raw_dir (pathlib.Path): Directory with the *.txt export(s), read in
                alphabetical order, or a path to a single file.

        Returns:
            list[ExperStep]: A list of ExperStep objects containing the parsed data.

        Raises:
            FileNotFoundError: If no *.txt file is found.
            ValueError: If the file layout does not match the ES8 format.
        """
        files = sorted(raw_dir.glob('*.txt')) if raw_dir.is_dir() else [raw_dir]
        if not files:
            raise FileNotFoundError(f'No .txt file found in {raw_dir}')

        data: list[ExperStep] = []
        for fn in files:
            data.extend(self._read_file(fn))

        self.data = data
        return data

    @staticmethod
    def _read_file(fn: pathlib.Path) -> list[ExperStep]:
        data: list[ExperStep] = []
        state = State.BLOCK_START
        nblock = ncycle = nstep = -1

        with fn.open('r', encoding=ENCODING) as file:
            file.readline()  # Skip first line
            for nline, line in enumerate(file, start=2):
                line = line.rstrip('\n')

                if state == State.BLOCK_START:
                    if not line:
                        state = State.BLOCK_HEADER_START
                    continue

                if state == State.BLOCK_HEADER_START:
                    if line:
                        r = RE_BLOCK.fullmatch(line)
                        if not r:
                            raise ValueError(f"Unexpected block header '{line}' at {fn} line #{nline}")
                        nblock = int(r.group(1))
                        state = State.STEP_START
                    continue

                if state == State.STEP_START:
                    r = RE_STEP.fullmatch(line)
                    if r:
                        ncycle = int(r.group(1))
                        nstep = int(r.group(2))
                        state = State.DATA_START
                    continue

                if state == State.DATA_START:
                    if not RE_VALUES_HEADER.fullmatch(line):
                        raise ValueError(f"Unexpected values header at {fn} line #{nline}")
                    data.append(ExperStep(index=(nblock, ncycle, nstep), t=[], U=[], I=[], Q=[]))
                    state = State.VALUES
                    continue

                if state == State.VALUES:
                    if not line:
                        state = State.STEP_START
                        continue
                    if line == 'u':
                        state = State.BLOCK_START
                        continue

                    vals = [float(s.replace(',', '.')) for s in line.split()]
                    data[-1].t.append(vals[0])
                    data[-1].U.append(vals[1])
                    data[-1].I.append(vals[2])
                    data[-1].Q.append(float('nan'))

        return data


def main():
    """
    Command-line entry point: parse an Elins (ES8) experiment folder.

    Reads 01_Raw_data/potentiostat/*.txt and writes step_metrics.csv,
    ocv_measurements.csv and cycles.csv.
    """
    parser = argparse.ArgumentParser(
        prog="elins_parser",
        description="Parse Elins (ES8) potentiostat data for redox flow battery cycling experiments",
        epilog="(c) FlowBat team, Skoltech")
    parser.add_argument('experiment_folder', metavar="EXPERIMENT_FOLDER",
                        help='experiment folder containing raw data, config.json, etc.')
    args = parser.parse_args()

    experiment_folder = pathlib.Path(args.experiment_folder)
    dir_in = experiment_folder / '01_Raw_data' / 'potentiostat'
    file_out = experiment_folder / '02_Parsed_data' / 'potentiostat' / 'cycles.csv'

    elins_parser = ElinsParser(experiment_folder)

    print(f"::: Reading ES8 experimental data from '{dir_in}'")
    elins_parser.read_data(dir_in)
    elins_parser.process_data(elins_parser.data)

    print(f"::: Writing processed data to '{file_out}'")
    elins_parser.write_csv_data(elins_parser.data, file_out)


if __name__ == '__main__':
    main()
