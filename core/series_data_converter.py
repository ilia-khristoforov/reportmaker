"""
Merges webcam electrolyte-volume data with color-sensor data onto one time axis.

Inputs:
    - volume CSV (from frames_to_volumes / calibration_gui) with columns
      filename, catholyte, anolyte; filenames encode the capture time as
      'YYYYmmdd_HHMMSS.jpg'.
    - color sensor log with a 'Time' column like '14 Nov 15:35:33' (no year)
      followed by numeric count columns.

Output: color rows with volumes linearly interpolated onto their timestamps,
plus a Seconds_from_start column used later by align_cycles_data.
"""

import argparse
import pathlib
from datetime import datetime

import pandas as pd

VOLUME_COLUMNS = ['catholyte', 'anolyte']
FRAME_TIME_FORMAT = '%Y%m%d_%H%M%S'
COLOR_TIME_FORMAT = '%Y %d %b %H:%M:%S'


def load_volume_data(filepath: pathlib.Path | str) -> pd.DataFrame:
    """
    Loads the volume CSV and indexes it by the capture time parsed from filenames.

    Volumes are normalised to the first valid value of each tank, so 1.0 means
    the initial electrolyte volume.

    Args:
        filepath: CSV with columns filename, catholyte, anolyte.

    Returns:
        DataFrame indexed by DateTime with relative catholyte/anolyte volumes.
    """
    df = pd.read_csv(filepath)

    def parse_filename(fname: str) -> datetime:
        return datetime.strptime(fname.split('.')[0], FRAME_TIME_FORMAT)

    df['DateTime'] = df['filename'].apply(parse_filename)
    df = df.set_index('DateTime').sort_index()
    df = df[VOLUME_COLUMNS]

    for col in VOLUME_COLUMNS:
        first_val = df[col].dropna().iloc[0]
        df[col] = df[col] / first_val

    return df


def load_color_data(filepath: pathlib.Path | str, year: int) -> pd.DataFrame:
    """
    Loads the color sensor log. Its timestamps carry no year, so it is passed in
    (taken from the volume data).

    Args:
        filepath: Color sensor CSV with a 'Time' column and numeric count columns.
        year: Year of the experiment.

    Returns:
        DataFrame indexed by DateTime with the numeric count columns.
    """
    # skipinitialspace strips spaces after commas (e.g. ", 4410")
    df = pd.read_csv(filepath, skipinitialspace=True, low_memory=False)

    # Drop rows where the sensor firmware wrote restart/error messages into data
    # columns (MicroPython crash output mixed into CSV). Coerce count columns to
    # numeric and discard any row where conversion fails.
    count_cols = [c for c in df.columns if c != 'Time']
    for col in count_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    n_bad = df[count_cols].isna().any(axis=1).sum()
    if n_bad:
        print(f"  Warning: dropped {n_bad} non-numeric rows from color data "
              f"(firmware restart messages)")
    df = df.dropna(subset=count_cols)

    def parse_colortime(timestr: str) -> datetime:
        return datetime.strptime(f"{year} {timestr}", COLOR_TIME_FORMAT)

    df['DateTime'] = df['Time'].apply(parse_colortime)
    df = df.set_index('DateTime').sort_index()
    return df.drop(columns=['Time'])


def merge_and_interpolate(df_vol: pd.DataFrame, df_col: pd.DataFrame) -> pd.DataFrame:
    """
    Interpolates volumes (time-weighted, linear) onto the color sensor timestamps.

    Args:
        df_vol: Output of load_volume_data.
        df_col: Output of load_color_data.

    Returns:
        Color DataFrame with interpolated volume columns joined on.
    """
    combined_index = df_col.index.union(df_vol.index).sort_values()
    # limit_direction='both' also fills the edges if color logging started before the first frame
    df_vol_interpolated = (df_vol.reindex(combined_index)
                           .interpolate(method='time', limit_direction='both'))
    return df_col.join(df_vol_interpolated, how='left')


def add_seconds_column(df: pd.DataFrame) -> pd.DataFrame:
    """
    Prepends a Seconds_from_start column (relative to the first timestamp).

    Args:
        df: DataFrame indexed by DateTime.

    Returns:
        DataFrame with Seconds_from_start as the first column.
    """
    df['Seconds_from_start'] = (df.index - df.index.min()).total_seconds()
    cols = ['Seconds_from_start'] + [c for c in df.columns if c != 'Seconds_from_start']
    return df[cols]


def process_experiment_data(volume_path: pathlib.Path | str,
                            color_path: pathlib.Path | str,
                            output_path: pathlib.Path | str) -> None:
    """
    Loads, merges and saves volume + color data.

    Args:
        volume_path: Volume CSV (see load_volume_data).
        color_path: Color sensor log (see load_color_data).
        output_path: Output CSV path.
    """
    print("1. Loading volume data...")
    try:
        df_vol = load_volume_data(volume_path)
        experiment_year = df_vol.index[0].year
        print(f"   Experiment year: {experiment_year}")
    except Exception as e:
        print(f"Error reading volume file: {e}")
        return

    print("2. Loading color data...")
    try:
        df_col = load_color_data(color_path, experiment_year)
    except Exception as e:
        print(f"Error reading color file: {e}")
        return

    print("3. Interpolating volumes onto color timestamps...")
    final_df = add_seconds_column(merge_and_interpolate(df_vol, df_col))

    print(f"4. Saving result to {output_path}...")
    final_df.to_csv(output_path, index=True, index_label='DateTime')
    print("Done!")


def main():
    """Command-line entry point using the standard experiment folder layout."""
    parser = argparse.ArgumentParser(
        description="Merge webcam volume data and color sensor data onto one timeline")
    parser.add_argument('experiment_folder', metavar="EXPERIMENT_FOLDER")
    args = parser.parse_args()

    experiment_folder = pathlib.Path(args.experiment_folder)
    process_experiment_data(
        experiment_folder / '03_Processed_data' / 'volumes_pixels.csv',
        experiment_folder / '01_Raw_data' / 'color' / 'colors.txt',
        experiment_folder / '02_Parsed_data' / 'colors_volumes_merged.csv',
    )


if __name__ == "__main__":
    main()
