#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Efficiency calculations and static plots for flow battery cycling data.

@author: Andrey Novikov, Nikita Buriak, Ilia Khristoforov
"""

import matplotlib
import pandas as pd
from matplotlib import pyplot as plt
import numpy as np
import pathlib
from typing import Literal


def _save_and_show(output_path: pathlib.Path) -> None:
    """Saves the current figure; shows it only with an interactive backend."""
    try:
        plt.savefig(output_path)
        print(f"Graph successfully saved to file: {output_path}")
    except Exception as e:
        print(f"Failed to save file. Error: {e}")
    if matplotlib.get_backend().lower() != 'agg':
        plt.show()


def calculate_efficiencies(metrics_file: pathlib.Path, q_theory_mAh: float | None = None) -> pd.DataFrame:
    """
    Calculates coulombic, voltage, energy, and electrolyte utilization efficiencies per cycle from a metrics file.

    Args:
        metrics_file: Path to the step_metrics.csv file.
        q_theory_mAh: Maximal theoretical capacity in mAh. If provided, electrolyte utilization will be calculated.

    Returns:
        A pandas DataFrame with cycle, coulombic_efficiency, voltage_efficiency, energy_efficiency, 
        and optionally electrolyte_utilization.
    """
    metrics_df = pd.read_csv(metrics_file)

    # Initialize lists to store results
    cycle_efficiencies = []

    # Group by cycle to calculate efficiencies per cycle
    for cycle_id, cycle_group in metrics_df.groupby('cycle'):
        charge_steps = cycle_group[cycle_group['step'] == 'ch']
        discharge_steps = cycle_group[cycle_group['step'] == 'dch']

        if not charge_steps.empty and not discharge_steps.empty:
            # Coulombic Efficiency: (Discharge Capacity / Charge Capacity) * 100
            total_charge_capacity = charge_steps['capacity_mAh'].sum()
            total_discharge_capacity = abs(discharge_steps['capacity_mAh'].sum())
            coulombic_efficiency = (total_discharge_capacity / total_charge_capacity) * 100 if total_charge_capacity != 0 else 0

            # Voltage Efficiency: (Average Discharge Voltage / Average Charge Voltage) * 100
            avg_charge_voltage = charge_steps['avg_voltage_V'].mean()
            avg_discharge_voltage = discharge_steps['avg_voltage_V'].mean()
            voltage_efficiency = (avg_discharge_voltage / avg_charge_voltage) * 100 if avg_charge_voltage != 0 else 0

            # Energy Efficiency = CE * VE
            energy_efficiency = coulombic_efficiency * voltage_efficiency / 100

            # Electrolyte Utilization: (Q_actual / Q_theory) * 100
            # Q_actual is the actual capacity measured during discharge
            # Q_theory is the maximal theoretical capacity
            electrolyte_utilization = None
            if q_theory_mAh is not None and q_theory_mAh != 0:
                q_actual = total_discharge_capacity
                electrolyte_utilization = (q_actual / q_theory_mAh) * 100

            cycle_dict = {
                'cycle': cycle_id,
                'coulombic_efficiency': coulombic_efficiency,
                'voltage_efficiency': voltage_efficiency,
                'energy_efficiency': energy_efficiency,
            }
            
            if electrolyte_utilization is not None:
                cycle_dict['electrolyte_utilization'] = electrolyte_utilization
            
            cycle_efficiencies.append(cycle_dict)


    return pd.DataFrame(cycle_efficiencies)

def reshape_ocv_data(ocv_path: pathlib.Path) -> pd.DataFrame:
    """
    Reshapes the OCV data from a long format to a wide format.

    For each cycle, it creates a single row where the first and second
    OCV entries become separate columns (charge and discharge OCV).

    Args:
        ocv_path (pathlib.Path): Path to the OCV data file.

    Returns:
        pd.DataFrame: A DataFrame with reshaped OCV data, including 'cycle',
                      'ocv_charge_V', and 'ocv_discharge_V' columns.
    """

    df = pd.read_csv(ocv_path)
    reshaped_df = df.groupby('cycle')['ocv_V'].agg(['first', 'last'])
    reshaped_df = reshaped_df.reset_index()
    reshaped_df = reshaped_df.rename(columns={
        'first': 'ocv_charge_V', 
        'last': 'ocv_discharge_V'
    })
    return reshaped_df

def pick_cycle_step(num: int, cycles_path: pathlib.Path, step: Literal['charge', 'discharge'] = 'charge'):
    """
    Extracts data for a specific cycle and step (charge or discharge) from the cycles file.

    Args:
        num (int): The cycle number to extract.
        cycles_path (pathlib.Path): Path to the cycles data file.
        step (Literal['charge', 'discharge'], optional): The step type ('charge' or 'discharge').
                                                        Defaults to 'charge'.

    Returns:
        pd.DataFrame: A DataFrame containing the data for the specified cycle and step,
                      with renamed columns.
    """
    # Opens the cycles file
    cycles_df = pd.read_csv(cycles_path)
    
    # Filters columns for the desired cycle number and returns
    if step == 'charge':
        cycle_step_df = cycles_df.filter(like=f'{num:03d}_ch')
    else:
        cycle_step_df = cycles_df.filter(like=f'{num:03d}_dch')

    # Renames columns by removing variable parts of the names
    cycle_step_df.columns = ['_'.join(col.split('_')[2:]) for col in cycle_step_df.columns]
    return cycle_step_df

def save_full_cycles_to_file(
    cycles_path: pathlib.Path,
    output_path: pathlib.Path,
    cycle_numbers: list[int]
):
    """
    Extracts charge and discharge data for specified cycle numbers and saves them to separate CSV files.

    Args:
        cycles_path (pathlib.Path): Path to the main cycles data file.
        output_path (pathlib.Path): Directory where the individual cycle CSV files will be saved.
        cycle_numbers (list[int]): A list of cycle numbers to extract and save.
    """
    output_path.mkdir(parents=True, exist_ok=True) # Ensure the output directory exists

    for cycle_num in cycle_numbers:
        charge_df = pick_cycle_step(cycle_num, cycles_path, step='charge')
        discharge_df = pick_cycle_step(cycle_num, cycles_path, step='discharge')

        # Charge and discharge side by side under a two-level column header
        full_cycle_df = pd.concat([charge_df, discharge_df], axis=1, keys=['charge', 'discharge'])
        
        # Define the output file name
        file_name = f"cycle_{cycle_num:03d}_full.csv"
        full_file_path = output_path / file_name
        
        full_cycle_df.to_csv(full_file_path, index=False)
        print(f"Saved full cycle {cycle_num} to {full_file_path}")

def calc_SoC(step_df: pd.DataFrame, capacity_coulombs: float) -> pd.Series:
    """
    Calculates the State of Charge (SoC) for a given step DataFrame.

    Args:
        step_df (pd.DataFrame): DataFrame containing step data with a 'Q_C' column (charge in Coulombs).
        capacity_coulombs (float): The nominal capacity in Coulombs.

    Returns:
        pd.Series: A Series representing the SoC in percentage.
    """
    return abs(step_df['Q_C'] / capacity_coulombs) * 100

def get_total_cycles(cycles_path: pathlib.Path) -> int:
    """
    Gets the total number of unique cycles from the cycles data file.

    Args:
        cycles_path (pathlib.Path): Path to the cycles data file.

    Returns:
        int: The total number of unique cycles.
    """
    cycles_df = pd.read_csv(cycles_path)
    return cycles_df.columns.str.split('_').str[0].nunique()

def plot_efficiencies(df: pd.DataFrame, output_path: pathlib.Path, title: str = 'Efficiency Plot per Cycle'):
    """
    Plots and saves a graph of efficiencies based on the provided DataFrame.

    Args:
        df (pd.DataFrame): DataFrame containing data for plotting.
                           Expected columns: 'cycle', 'coulombic_efficiency',
                           'voltage_efficiency', 'energy_efficiency'.
                           Optional column: 'electrolyte_utilization'.
        output_path (pathlib.Path): Path to save the generated image (e.g., 'my_plot.png').
        title (str, optional): Title of the plot.
    """
    # Check for required columns
    required_columns = ['cycle', 'coulombic_efficiency', 'voltage_efficiency', 'energy_efficiency']
    if not all(col in df.columns for col in required_columns):
        raise ValueError(f"Required columns are missing in the DataFrame. Required: {required_columns}")

    # --- Plotting ---
    plt.figure(figsize=(12, 7))

    x_axis = df['cycle']

    # Plot three lines with markers
    plt.plot(x_axis, df['coulombic_efficiency'], marker='o', linestyle='-', label='Coulombic Efficiency')
    plt.plot(x_axis, df['voltage_efficiency'], marker='o', linestyle='-', label='Voltage Efficiency')
    plt.plot(x_axis, df['energy_efficiency'], marker='o', linestyle='-', label='Energy Efficiency')
    
    # Plot electrolyte utilization if available
    if 'electrolyte_utilization' in df.columns:
        plt.plot(x_axis, df['electrolyte_utilization'], marker='o', linestyle='-', label='Electrolyte Utilization')

    # --- Formatting ---
    plt.title(title)
    plt.xlabel('Cycle')
    plt.ylabel('Efficiency (%)')
    plt.legend()
    plt.grid(True)
    
    _save_and_show(output_path)


def plot_ocv(df: pd.DataFrame, output_path: pathlib.Path, title: str = 'OCV Plot per Cycle'):
    """
    Plots and saves a graph of OCV (Open Circuit Voltage) over cycles.

    Args:
        df (pd.DataFrame): DataFrame containing data for plotting.
                           Expected columns: 'cycle', 'ocv_charge_V', 'ocv_discharge_V'.
        output_path (pathlib.Path): Path to save the generated image (e.g., 'ocv_plot.png').
        title (str, optional): Title of the plot.
    """
    # Check for required columns
    required_columns = ['cycle', 'ocv_charge_V', 'ocv_discharge_V']
    if not all(col in df.columns for col in required_columns):
        raise ValueError(f"Required columns are missing in the DataFrame. Required: {required_columns}")

    # --- Plotting ---
    plt.figure(figsize=(12, 7))

    x_axis = df['cycle']

    # Plot two lines with markers: for charge and discharge
    plt.plot(x_axis, df['ocv_charge_V'], marker='o', linestyle='-', label='OCV on charge (V)')
    plt.plot(x_axis, df['ocv_discharge_V'], marker='o', linestyle='-', label='OCV on discharge (V)')

    # --- Formatting ---
    plt.title(title)
    plt.xlabel('Cycle')
    plt.ylabel('Voltage (V)')
    plt.legend()    
    plt.grid(True)
    
    _save_and_show(output_path)

def plot_charge_discharge_cycles(
    cycles_file: pathlib.Path,
    capacity_coulombs: float,
    output_path: pathlib.Path,
    start_cycle: int,
    end_cycle: int,
    cycle_step: int = 1,
    voltage_limits: tuple[float, float] | None = None,
    plot_legend: bool = True,
    plot_title: str = 'Charge-Discharge curves',
    current_densities: list[int] | None = None,
    plot_colors: list[str] | None = None,
):
    """
    Plots charge-discharge curves for specified cycles.

    Args:
        cycles_file (pathlib.Path): Path to the cycles data file.
        capacity_coulombs (float): The nominal capacity in Coulombs, used for SoC calculation.
        output_path (pathlib.Path): Path to save the generated image.
        start_cycle (int): The starting cycle number for plotting.
        end_cycle (int): The ending cycle number for plotting.
        cycle_step (int, optional): The step size between cycles to plot. Defaults to 1.
        voltage_limits (tuple[float, float] | None, optional): Y-axis range in V, e.g.
            (0.8, 1.65) for a single VRFB cell. Defaults to None (autoscale).
        plot_legend (bool, optional): If True, a legend will be displayed. Defaults to True.
        plot_title (str, optional): The title of the plot. Defaults to 'Charge-Discharge curves'.
        current_densities (list[int] | None, optional): List of current densities for legend labels.
                                                          If None and plot_legend is True, uses 'Cycle {num}'.
        plot_colors (list[str] | None, optional): List of colors to use for plotting. If None,
                                                    colors are generated from a 'Blues' colormap.
    """
    plt.figure(figsize=(12, 7))

    cycles_to_plot = list(range(start_cycle, end_cycle + 1, cycle_step)) # Generate cycles with step

    if plot_colors:
        colors = plot_colors
    else:
        cmap = plt.colormaps.get_cmap('Blues')
        colors = [cmap(i) for i in np.linspace(0.9, 0.3, num=len(cycles_to_plot))]

    for i, cycle_num in enumerate(cycles_to_plot):
        color_to_use = colors[i % len(colors)]

        charge_df = pick_cycle_step(cycle_num, cycles_file, step='charge').dropna()
        discharge_df = pick_cycle_step(cycle_num, cycles_file, step='discharge').dropna()
        if charge_df.empty or discharge_df.empty:
            continue  # e.g. last cycle interrupted before discharge
        charge_df['SoC_%'] = calc_SoC(charge_df, capacity_coulombs)

        label_text = None
        if plot_legend and current_densities and i < len(current_densities):
            label_text = f'{current_densities[i]} mA/cm²'
        elif plot_legend:
            label_text = f'Cycle {cycle_num}'

        plt.plot(charge_df['SoC_%'], charge_df['U_V'],
                 color=color_to_use, label=label_text)

        discharge_df['SoC_%'] = calc_SoC(discharge_df, capacity_coulombs)
        plt.plot(discharge_df['SoC_%'], discharge_df['U_V'],
                 color=color_to_use)

    plt.title(plot_title)
    plt.xlim((0, 100))
    if voltage_limits:
        plt.ylim(voltage_limits)
    plt.xlabel('SoC (%)')
    plt.ylabel('Voltage (V)')
    if plot_legend:
        plt.legend()
    plt.grid()

    _save_and_show(output_path)
