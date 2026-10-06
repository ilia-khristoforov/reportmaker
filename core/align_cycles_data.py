#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Aligns supplementary sensor data with the potentiostat timeline.

Converts wide-format cycles.csv to long format, builds a master timeline with rest periods,
and interpolates color/volume and OCV sensor data onto the master timeline.

Usually driven interactively from gui/alignment_gui.py; the command-line entry point
is for when the offsets are already known.
"""

import argparse
import pandas as pd
import numpy as np
from pathlib import Path
from scipy.interpolate import interp1d


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def parse_wide_cycles_column(column_name: str) -> tuple:
    """
    Parse a wide format column name like '001_ch_t_s' into components.
    
    Args:
        column_name: Column name in format '{NNN}_{step_type}_{param}'
        
    Returns:
        Tuple of (cycle_num, step_type, param) or None if format doesn't match
    """
    parts = column_name.split('_')
    if len(parts) >= 3:
        try:
            cycle_num = int(parts[0])
            step_type = parts[1]  # 'ch' or 'dch'
            param = '_'.join(parts[2:])  # Join remaining parts for params like 't_s'
            return (cycle_num, step_type, param)
        except ValueError:
            return None
    return None


def extract_step_data(df_wide: pd.DataFrame, cycle_num: int, step_type: str) -> pd.DataFrame:
    """
    Extract data for a specific cycle and step type from wide format DataFrame.
    
    Args:
        df_wide: Wide format DataFrame
        cycle_num: Cycle number (e.g., 1, 2, 3...)
        step_type: 'ch' (charge) or 'dch' (discharge)
        
    Returns:
        DataFrame with columns: t_s, U_V, I_A, etc. (only valid rows, NaNs dropped)
    """
    cycle_str = f"{cycle_num:03d}"
    prefix = f"{cycle_str}_{step_type}_"
    
    # Find all columns for this cycle and step
    relevant_cols = [col for col in df_wide.columns if col.startswith(prefix)]
    
    if not relevant_cols:
        return pd.DataFrame()
    
    # Extract the parameter names (remove prefix)
    params = {}
    for col in relevant_cols:
        param = col[len(prefix):]
        params[param] = col
    
    # Create a DataFrame with the extracted columns
    step_df = pd.DataFrame()
    for param, col_name in params.items():
        step_df[param] = df_wide[col_name]
    
    # Drop rows where all values are NaN
    step_df = step_df.dropna(how='all')
    
    # Drop rows where t_s is NaN (time is required)
    if 't_s' in step_df.columns:
        step_df = step_df.dropna(subset=['t_s'])
    
    return step_df


def create_rest_period(cycle_num: int, duration_s: float, step_num: int = 1) -> pd.DataFrame:
    """
    Create synthetic rows for a rest period.
    
    Args:
        cycle_num: Cycle number
        duration_s: Duration of rest period in seconds
        step_num: Step number within the cycle (for tracking)
        
    Returns:
        DataFrame with rest period data (1-second intervals)
    """
    # Generate time points at 1-second intervals
    n_points = max(1, int(np.ceil(duration_s)))
    time_points = np.linspace(0, duration_s, n_points)
    
    rest_data = {
        'Global_Time_s': time_points,  # Will be updated when added to master
        'Cycle_Index': cycle_num,
        'Step_Type': 'rest',
        'Step_Number': step_num,
        't_s': time_points,  # Relative time within rest period
        'SoC': np.nan,
        'U_V': np.nan,
        'I_A': np.nan,
    }
    
    return pd.DataFrame(rest_data)


# ============================================================================
# MAIN PROCESSING FUNCTIONS
# ============================================================================

def build_master_timeline(df_wide: pd.DataFrame, rest_duration_s: float = 30.0) -> pd.DataFrame:
    """
    Convert wide format cycles.csv to long format and build master timeline.
    
    This function:
    1. Extracts charge and discharge steps for each cycle
    2. Adds rest periods after discharge steps only
    3. Builds a continuous Global_Time_s timeline
    
    Args:
        df_wide: Wide format DataFrame from cycles.csv
        rest_duration_s: Duration of rest periods in seconds (after discharge only)
        
    Returns:
        DataFrame in long format with Global_Time_s column
    """
    print("Building master timeline from wide format cycles.csv...")
    
    master_rows = []
    current_global_time = 0.0
    
    # Find all unique cycle numbers from column names
    cycle_numbers = set()
    for col in df_wide.columns:
        parsed = parse_wide_cycles_column(col)
        if parsed:
            cycle_num, _, _ = parsed
            cycle_numbers.add(cycle_num)
    
    cycle_numbers = sorted(cycle_numbers)
    print(f"  Found {len(cycle_numbers)} cycles: {cycle_numbers}")
    
    # Process each cycle
    for cycle_num in cycle_numbers:
        print(f"  Processing cycle {cycle_num}...")
        
        # 1. CHARGE STEP
        ch_data = extract_step_data(df_wide, cycle_num, 'ch')
        if not ch_data.empty and 't_s' in ch_data.columns:
            # Add global time to relative time
            ch_data = ch_data.copy()
            ch_data['Global_Time_s'] = current_global_time + ch_data['t_s'].values
            ch_data['Cycle_Index'] = cycle_num
            ch_data['Step_Type'] = 'ch'
            ch_data['Step_Number'] = 1
            
            # Update current_global_time to end of charge step
            if len(ch_data) > 0:
                current_global_time = ch_data['Global_Time_s'].max()
                master_rows.append(ch_data)
                print(f"    Charge: {len(ch_data)} points, duration: {ch_data['t_s'].max():.1f}s")
        
        # 2. DISCHARGE STEP
        dch_data = extract_step_data(df_wide, cycle_num, 'dch')
        if not dch_data.empty and 't_s' in dch_data.columns:
            # Add global time to relative time
            dch_data = dch_data.copy()
            dch_data['Global_Time_s'] = current_global_time + dch_data['t_s'].values
            dch_data['Cycle_Index'] = cycle_num
            dch_data['Step_Type'] = 'dch'
            dch_data['Step_Number'] = 2
            
            # Update current_global_time to end of discharge step
            if len(dch_data) > 0:
                current_global_time = dch_data['Global_Time_s'].max()
                master_rows.append(dch_data)
                print(f"    Discharge: {len(dch_data)} points, duration: {dch_data['t_s'].max():.1f}s")
        
        # 3. REST AFTER DISCHARGE
        rest_after_dch = create_rest_period(cycle_num, rest_duration_s, step_num=3)
        rest_after_dch['Global_Time_s'] = current_global_time + rest_after_dch['t_s'].values
        current_global_time = rest_after_dch['Global_Time_s'].max()
        master_rows.append(rest_after_dch)
        print(f"    Rest after discharge: {rest_duration_s:.1f}s")
    
    # Combine all rows
    if not master_rows:
        raise ValueError("No data extracted from cycles.csv. Check column naming format.")
    
    df_master = pd.concat(master_rows, ignore_index=True)
    
    # Sort by Global_Time_s
    df_master = df_master.sort_values('Global_Time_s').reset_index(drop=True)
    
    print(f"  Master timeline complete: {len(df_master)} total points")
    print(f"  Total duration: {df_master['Global_Time_s'].max():.1f}s")
    
    return df_master


def interpolate_color_data(df_master: pd.DataFrame, df_color: pd.DataFrame, 
                          offset_s: float = 0.0) -> pd.DataFrame:
    """
    Interpolate color/volume sensor data onto master timeline.
    
    Args:
        df_master: Master timeline DataFrame with Global_Time_s
        df_color: Color/volume DataFrame with Seconds_from_start column
        offset_s: Time offset to apply to color data (seconds)
        
    Returns:
        Master DataFrame with interpolated color/volume columns added
    """
    print(f"Interpolating color/volume data (offset: {offset_s:.1f}s)...")
    
    if df_color.empty:
        print("  No color data provided, skipping...")
        return df_master
    
    # Calculate aligned time for color data
    if 'Seconds_from_start' not in df_color.columns:
        raise ValueError("color_volumes.csv must have 'Seconds_from_start' column")
    
    color_time = df_color['Seconds_from_start'].values + offset_s
    
    # Get numeric columns to interpolate (exclude DateTime and Seconds_from_start)
    numeric_cols = df_color.select_dtypes(include=[np.number]).columns.tolist()
    if 'Seconds_from_start' in numeric_cols:
        numeric_cols.remove('Seconds_from_start')
    
    if not numeric_cols:
        print("  No numeric columns found in color data, skipping...")
        return df_master
    
    print(f"  Interpolating {len(numeric_cols)} columns: {numeric_cols}")
    
    # Interpolate each column
    master_time = df_master['Global_Time_s'].values
    
    for col in numeric_cols:
        # Remove NaN values for interpolation
        valid_mask = ~np.isnan(color_time) & ~np.isnan(df_color[col].values)
        if not np.any(valid_mask):
            print(f"    Warning: No valid data for column {col}")
            df_master[col] = np.nan
            continue
        
        valid_time = color_time[valid_mask]
        valid_values = df_color[col].values[valid_mask]
        
        # Create interpolation function
        # Use linear interpolation, extrapolate with nearest value
        interp_func = interp1d(valid_time, valid_values, 
                              kind='linear', 
                              bounds_error=False, 
                              fill_value=(valid_values[0], valid_values[-1]))
        
        # Interpolate onto master timeline
        df_master[col] = interp_func(master_time)
    
    print(f"  Color/volume interpolation complete")
    return df_master


def interpolate_ocv_data(df_master: pd.DataFrame, df_ocv: pd.DataFrame, 
                         offset_s: float = 0.0) -> pd.DataFrame:
    """
    Interpolate OCV sensor data onto master timeline.
    
    Args:
        df_master: Master timeline DataFrame with Global_Time_s
        df_ocv: OCV DataFrame with time_s and voltage_V columns
        offset_s: Time offset to apply to OCV data (seconds)
        
    Returns:
        Master DataFrame with interpolated OCV voltage column added
    """
    print(f"Interpolating OCV data (offset: {offset_s:.1f}s)...")
    
    if df_ocv.empty:
        print("  No OCV data provided, skipping...")
        return df_master
    
    if 'time_s' not in df_ocv.columns or 'voltage_V' not in df_ocv.columns:
        raise ValueError("ocv_log.csv must have 'time_s' and 'voltage_V' columns")
    
    # Calculate aligned time for OCV data
    ocv_time = df_ocv['time_s'].values + offset_s
    ocv_voltage = df_ocv['voltage_V'].values
    
    # Remove NaN values for interpolation
    valid_mask = ~np.isnan(ocv_time) & ~np.isnan(ocv_voltage)
    if not np.any(valid_mask):
        print("  Warning: No valid OCV data found")
        df_master['OCV_voltage_V'] = np.nan
        return df_master
    
    valid_time = ocv_time[valid_mask]
    valid_voltage = ocv_voltage[valid_mask]
    
    # Create interpolation function
    interp_func = interp1d(valid_time, valid_voltage, 
                          kind='linear', 
                          bounds_error=False, 
                          fill_value=(valid_voltage[0], valid_voltage[-1]))
    
    # Interpolate onto master timeline
    master_time = df_master['Global_Time_s'].values
    df_master['OCV_voltage_V'] = interp_func(master_time)
    
    print(f"  OCV interpolation complete")
    return df_master


def save_cycle_files(df_aligned: pd.DataFrame, output_dir: Path):
    """
    Slice aligned data by cycle and save individual files.
    
    Args:
        df_aligned: Full aligned DataFrame with Cycle_Index column
        output_dir: Directory to save cycle files
    """
    print(f"Saving individual cycle files to {output_dir}...")
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    if 'Cycle_Index' not in df_aligned.columns:
        print("  Warning: No Cycle_Index column found, cannot separate cycles")
        return
    
    cycles = sorted(df_aligned['Cycle_Index'].dropna().unique())
    print(f"  Found {len(cycles)} cycles")
    
    for cycle_num in cycles:
        cycle_data = df_aligned[df_aligned['Cycle_Index'] == cycle_num].copy()
        cycle_data = cycle_data.sort_values('Global_Time_s').reset_index(drop=True)
        
        filename = output_dir / f"cycle_{int(cycle_num):03d}.csv"
        cycle_data.to_csv(filename, index=False)
        print(f"    Saved cycle {int(cycle_num)}: {len(cycle_data)} points")
    
    print(f"  All cycle files saved")


# ============================================================================
# MAIN EXECUTION
# ============================================================================

def main():
    """Command-line entry point: align sensors for one experiment folder."""
    parser = argparse.ArgumentParser(
        description="Align color/volume and OCV sensor data onto the potentiostat timeline")
    parser.add_argument('experiment_folder', metavar="EXPERIMENT_FOLDER")
    parser.add_argument('--color-offset', type=float, default=0.0,
                        help="color sensor time shift, s (positive = sensor started after "
                             "the potentiostat)")
    parser.add_argument('--ocv-offset', type=float, default=0.0,
                        help="OCV logger time shift, s (same sign convention)")
    parser.add_argument('--rest', type=float, default=30.0,
                        help="rest duration after each discharge, s")
    args = parser.parse_args()

    parsed = Path(args.experiment_folder) / '02_Parsed_data'
    path_cycles = parsed / 'cycles.csv'
    if not path_cycles.exists():
        path_cycles = parsed / 'potentiostat' / 'cycles.csv'
    path_color = parsed / 'colors_volumes_merged.csv'
    path_ocv = parsed / 'OCV' / 'ocv_log.csv'
    if not path_ocv.exists():
        path_ocv = parsed / 'ocv_log.csv'

    if not path_cycles.exists():
        raise FileNotFoundError(f"cycles.csv not found in {parsed}")
    df_master = build_master_timeline(pd.read_csv(path_cycles), rest_duration_s=args.rest)

    if path_color.exists():
        df_master = interpolate_color_data(df_master, pd.read_csv(path_color, low_memory=False),
                                           offset_s=args.color_offset)
    else:
        print(f"  {path_color.name} not found, skipping color data")

    if path_ocv.exists():
        df_master = interpolate_ocv_data(df_master, pd.read_csv(path_ocv),
                                         offset_s=args.ocv_offset)
    else:
        print(f"  {path_ocv.name} not found, skipping OCV data")

    output_aligned = parsed / 'all_data_aligned.csv'
    df_master.to_csv(output_aligned, index=False)
    print(f"Saved {len(df_master)} rows to {output_aligned}")
    save_cycle_files(df_master, parsed / 'processed_cycles')


if __name__ == '__main__':
    main()
