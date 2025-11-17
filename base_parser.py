#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Base class for parsing experimental data written by potentiostats.

@author: Andrey Novikov, Nikita Buriak, Ilia Khristoforov
"""

import pathlib
import csv
from dataclasses import dataclass
from abc import ABC, abstractmethod
import json


@dataclass
class ExperStep:
    """
    Represents a single experimental step with its measured data.
    """
    index: tuple  #: block, cycle, step or cycle, step
    t: list[float]  #: time, s
    U: list[float]  #: voltage, V
    I: list[float]  #: current, A
    Q: list[float]  #: charge, C
    unified_cycle_id: int | None = None
    charge_discharge: str | None = None

    def val_names(self) -> list[str]:
        """
        Generates a list of column names for the values within this experimental step,
        prefixed with a unified cycle ID and charge/discharge status.

        Raises:
            ValueError: If 'unified_cycle_id' or 'charge_discharge' are not set.

        Returns:
            list[str]: A list of formatted column names.
        """
        if self.unified_cycle_id is None or self.charge_discharge is None:
            raise ValueError("unified_cycle_id and charge_discharge must be set before calling val_names")

        _name = f"{self.unified_cycle_id:03d}_{self.charge_discharge}"
        return [f'{_name}_{s}' for s in ('t_s', 'U_V', 'I_A', 'Q_C')]


class BaseParser(ABC):
    """
    Abstract base class for parsing electrochemical experimental data.

    Provides common functionalities for managing experiment folders, loading configuration,
    calculating nominal capacity, and writing parsed data to CSV files.
    """
    def __init__(self, experiment_folder: pathlib.Path | str):
        """
        Initializes the BaseParser with the experiment folder and loads the configuration.

        Args:
            experiment_folder (pathlib.Path | str): The path to the experiment folder.
        """
        self.experiment_folder = pathlib.Path(experiment_folder)
        self.raw_data_path = self.experiment_folder / '01_Raw_data'
        self.parsed_data_path = self.experiment_folder / '02_Parsed_data'
        self.processed_data_path = self.experiment_folder / '03_Processed_data'
        self.results_path = self.experiment_folder / '04_Results'
        self.config_path = self.experiment_folder / 'config.json'

        self.processed_data_path.mkdir(parents=True, exist_ok=True)
        self.parsed_data_path.mkdir(parents=True, exist_ok=True)

        with self.config_path.open('r', encoding='utf8') as file:
            self.config = json.load(file)

        self.data: list[ExperStep] = []
        self.ctotal = self.config['electrolyte']['concentration_M']  # Molar concentration
        self.volume = self.config['electrolyte']['volume_ml'] / 1000  # Volume in liters

    def calculate_nominal_capacity(self) -> tuple[float, float]:
        """
        Calculates the nominal capacity of the electrolyte.

        Returns:
            tuple[float, float]: A tuple containing the nominal capacity in Coulombs and Ah.
        """
        n = 1  # Number of electrons in the reaction
        F = 96485  # Faraday constant
        # self.ctotal and self.volume are already set in __init__

        self.C_nom = n * F * self.ctotal * self.volume
        return self.C_nom, self.C_nom / 3600

    @abstractmethod
    def read_data(self, *args, **kwargs) -> list[ExperStep]:
        """
        Abstract method to read raw experimental data. Must be implemented by subclasses.
        """
        pass

    def process_data(
            self,
            data: list[ExperStep]) -> None:
        """
        Processes the raw experimental data to calculate metrics and OCV measurements,
        then stores them in summary CSV files.

        Args:
            data (list[ExperStep]): A list of experimental steps to process.
        """
        values_filename = self.processed_data_path / 'step_metrics.csv'
        ocv_filename = self.processed_data_path / 'ocv_measurements.csv'

        data_upd: list[ExperStep] = []
        values_rows: list[list[float | str]] = []
        ocv_rows: list[list[float | str]] = []

        unified_cycle_counter = 0
        previous_charge_discharge = None

        for step in data:
            # Determine unified_cycle_id and charge_discharge
            charge_discharge = 'ch' if step.I[0] > 0.0 else 'dch'
            if previous_charge_discharge is None or \
               (previous_charge_discharge == 'dch' and charge_discharge == 'ch'):
                unified_cycle_counter += 1
            step.unified_cycle_id = unified_cycle_counter
            step.charge_discharge = charge_discharge
            previous_charge_discharge = charge_discharge

            if len(step.index) == 3:
                block_id, cycle_id, step_id = step.index
            elif len(step.index) == 2:
                cycle_id, step_id = step.index
                block_id = "N/A"  # Placeholder for YARST data

            # Skip steps if all current measurements are zero
            if all(i == 0.0 for i in step.I):
                print(f"Block {block_id}, Cycle {cycle_id}, Step {step_id} is skipped (I==0)")
                print(f"Volume = {self.volume} L, Concentration = {self.ctotal} M")
                OCV = float(step.U[-1])
                print(f"OCV = {OCV} V")
                ocv_rows.append([step.unified_cycle_id, OCV])
                continue

            q_coulomb = 0.0
            voltage_sum = 0.0
            counter = 0
            for i, (t_val, U_val, I_val) in enumerate(zip(step.t, step.U, step.I)):
                q_coulomb = t_val * I_val
                voltage_sum += U_val
                counter = i + 1
                step.Q[i] = q_coulomb

            if counter == 0:
                # No data points - skip to stay safe
                continue

            avg_voltage = voltage_sum / counter
            capacity_mAh = q_coulomb / 3.6
            energy_mWh = avg_voltage * capacity_mAh

            data_upd.append(step)

            print(f"Capacity = {capacity_mAh:.2f} mAh, "
                  f"Avg Voltage = {avg_voltage:.2f} V, "
                  f"Energy = {energy_mWh:.2f} mWh")

            values_rows.append([
                step.unified_cycle_id,
                step.charge_discharge,
                capacity_mAh,
                avg_voltage,
                energy_mWh,
            ])

        data[:] = data_upd

        with values_filename.open('w', newline='', encoding='utf8') as file:
            writer = csv.writer(file)
            writer.writerow(['cycle', 'step', 'capacity_mAh', 'avg_voltage_V', 'energy_mWh'])
            writer.writerows(values_rows)

        with ocv_filename.open('w', newline='', encoding='utf8') as file:
            writer = csv.writer(file)
            writer.writerow(['cycle', 'ocv_V'])
            writer.writerows(ocv_rows)

    def write_csv_data(self, data: list[ExperStep], file_out: pathlib.Path):
        """
        Writes the processed experimental data to a CSV file.

        The data is grouped by unified cycle ID and charge/discharge status,
        and then written in a wide format where each column corresponds to a specific
        measurement (time, voltage, current, charge) for a given cycle and step.

        Args:
            data (list[ExperStep]): A list of ExperStep objects containing the processed data.
            file_out (pathlib.Path): The path to the output CSV file.
        """
        col_names: list[str] = []
        data_len_max: int = 0
        grouped_data: dict[str, ExperStep] = {}

        for step in data:
            group_key = f"{step.unified_cycle_id}_{step.charge_discharge}"
            if group_key not in grouped_data:
                grouped_data[group_key] = ExperStep(
                    index=step.index, t=[], U=[], I=[], Q=[],
                    unified_cycle_id=step.unified_cycle_id,
                    charge_discharge=step.charge_discharge
                )
            grouped_data[group_key].t.extend(step.t)
            grouped_data[group_key].U.extend(step.U)
            grouped_data[group_key].I.extend(step.I)
            grouped_data[group_key].Q.extend(step.Q)

        sorted_grouped_data = sorted(grouped_data.items(), key=lambda item: int(item[0].split('_')[0]))

        for group_key, combined_step in sorted_grouped_data:
            col_names.extend(combined_step.val_names())
            data_len_max = max(data_len_max, len(combined_step.t))

        with file_out.open('w', encoding='utf8', newline='') as f:
            csv_file = csv.DictWriter(f, fieldnames=col_names)
            csv_file.writeheader()

            for n in range(data_len_max):
                row: dict[str, float] = dict()
                for group_key, combined_step in sorted_grouped_data:
                    if n >= len(combined_step.t):
                        continue

                    _val_names = combined_step.val_names()
                    _vals = (
                        combined_step.t[n],
                        combined_step.U[n],
                        combined_step.I[n],
                        combined_step.Q[n]
                    )
                    row.update(dict(zip(_val_names, _vals)))

                csv_file.writerow(row)
