#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Parsing potentiostat YARST [https://yarst.org/prod] data obtained
in vanadium flow battery experiments

@authors: Andrey Novikov, Nikita Buriak, Ilia Khristoforov
"""

import sys
import argparse
import pathlib
import re
from base_parser import ExperStep, BaseParser


class YarstParser(BaseParser):
    def read_data(self, dir_in: pathlib.Path) -> list[ExperStep]:
        """
        Reads experimental data from YARST potentiostat files.

        Args:
            dir_in (pathlib.Path): Path to the directory containing raw data files (*.txt).

        Returns:
            list[ExperStep]: A list of ExperStep objects containing the parsed data.
        """
        data: list[ExperStep] = []

        re_header = re.compile(r"   Цикл   Шаг  Время,s    U,V    I,A T,°C ESR,mR   Q,Ah   E,Wh") # Regex to match the header line

        for fn in sorted(dir_in.glob('*.txt')):
            with fn.open('r', encoding='cp1251') as file:
                nline: int = 0
                cycle = ""
                step = ""
                is_head: bool = True
    
                for line in file:
                    nline += 1
                    line = line.rstrip('\n')
                    if not line:
                        continue
                    
                    if is_head:
                        if re_header.fullmatch(line):
                            is_head = False
                        continue
                    
                    vals = line.split()  # cycle, step, t, U, I, ...
                    if vals[0] == "Прервано":
                        break
                    
                    if len(vals) != 9:
                        print(f"Data format error: Unexpected number of values '{line}' at {fn} line#{nline}")
                        sys.exit(1)
    
                    if vals[0] != cycle or vals[1] != step:
                        cycle = vals[0]
                        step = vals[1]
                        data.append(ExperStep(index=(cycle, step), t=[], U=[], I=[], Q=[]))
    
                    data[-1].t.append(float(vals[2]))
                    data[-1].U.append(float(vals[3]))
                    data[-1].I.append(float(vals[4]))
                    data[-1].Q.append(float('nan'))
                #
            #
            self.data = data
        return data


def main():
    """
    Main function to parse YARST experimental data.

    It reads raw data from a specified experiment folder, processes it,
    and writes the parsed data to a CSV file.
    """
    parser = argparse.ArgumentParser(
        prog="parse-vrfb-YARST-data",
        description="Parsing potentiostat YARST experimental data for vanadium flow batteries",
        epilog="(c) FlowBat team, Skoltech")
    parser.add_argument('experiment_folder', metavar="EXPERIMENT_FOLDER",
                        help='experiment folder containing raw data, config.json, etc.')
    args = parser.parse_args()

    experiment_folder = pathlib.Path(args.experiment_folder)
    dir_in = experiment_folder / '01_Raw_data' / 'potentiostat'
    file_out = experiment_folder / '02_Parsed_data' / 'potentiostat' / 'cycles.txt'

    yarst_parser = YarstParser(experiment_folder)

    # Read input file
    print(":::")
    print(f"::: Reading YARST experimental data files '{str(dir_in)}/*.txt'")
    print(":::")
    yarst_parser.read_data(dir_in)

    # Process file
    yarst_parser.process_data(yarst_parser.data)

    # Write output file
    print(":::")
    print(f"::: Writing processed data to '{str(file_out)}'")
    print(":::")
    yarst_parser.write_csv_data(yarst_parser.data, file_out)
# ---


if __name__ == '__main__':
    main()
    # test()
