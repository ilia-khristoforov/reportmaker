# VRFB Cycling Data Analysis Tool

This project provides a suite of Python scripts for parsing, processing, and visualizing experimental data obtained from Vanadium Redox Flow Battery (VRFB) cycling experiments. It supports data from YARST and ES8 potentiostats, performs efficiency and OCV calculations, and includes functionality for image processing of webcam frames to measure electrolyte levels.

## Table of Contents

- [VRFB Cycling Data Analysis Tool](#vrfb-cycling-data-analysis-tool)
  - [Table of Contents](#table-of-contents)
  - [Project Description](#project-description)
  - [Installation](#installation)
  - [Configuration (`config.json`)](#configuration-configjson)
  - [Usage](#usage)
    - [Parsing Raw Data](#parsing-raw-data)
    - [Post-Processing and Visualization](#post-processing-and-visualization)
    - [Image Processing for Volume Measurement](#image-processing-for-volume-measurement)
  - [Codebase Overview](#codebase-overview)

## Project Description

The primary goal of this tool is to streamline the analysis of VRFB experimental data. It automates several steps:
*   **Raw Data Parsing**: Converts raw potentiostat output (YARST and ES8 formats) into a standardized, parsed format (`cycles.txt`).
*   **Data Processing**: Calculates key performance metrics such as coulombic, voltage, and energy efficiencies, and extracts open-circuit voltage (OCV) measurements.
*   **Visualization**: Generates plots for efficiencies, OCV trends, and charge-discharge curves for selected cycles.
*   **Image Analysis**: Processes webcam images to determine electrolyte volumes based on liquid column heights.

## Installation

To set up the project, follow these steps:

1.  **Clone the repository (if applicable) or download the files.**
2.  **Navigate to the project root directory:**
    ```bash
    cd /path/to/report_maker
    ```
3.  **Create a virtual environment (recommended):**
    ```bash
    python -m venv venv
    ```
4.  **Activate the virtual environment:**
    *   **Windows:**
        ```bash
        .\venv\Scripts\activate
        ```
    *   **macOS/Linux:**
        ```bash
        source venv/bin/activate
        ```
5.  **Install the required Python packages:**
    You will need to install `pandas`, `matplotlib`, `numpy`, and `opencv-python`. A `requirements.txt` file is not currently provided, so you can install them manually:
    ```bash
    pip install pandas matplotlib numpy opencv-python
    ```

## Configuration (`config.json`)

Each experiment folder (e.g., `mmk_2c`, `MMK_ST_ProjEltest21`) should contain a `config.json` file. This file specifies essential parameters for the electrolyte, such as concentration and volume, which are used in calculations.

Example `config.json`:

```json
{
  "electrolyte": {
    "concentration_M": 1.5,
    "volume_ml": 50.0
  },
  "image_processing_rois": {
    "catholyte": {
      "angle": -3.90,
      "thresholds": [40, 220],
      "roi": [185, 538, 257, 278]
    },
    "anolyte": {
      "angle": -7.20,
      "thresholds": [40, 100],
      "roi": [196, 581, 359, 382]
    }
  }
}
```

*   `electrolyte.concentration_M`: Molar concentration of the electrolyte.
*   `electrolyte.volume_ml`: Volume of the electrolyte in milliliters.
*   `image_processing_rois`: (Optional) Configuration for image processing. Each key (e.g., `catholyte`, `anolyte`) represents a Region of Interest (ROI) and contains:
    *   `angle`: Rotation angle for the image.
    *   `thresholds`: Tuple `(min_threshold, max_threshold)` for binary thresholding.
    *   `roi`: Tuple `(x1, x2, y1, y2)` defining the rectangular region of interest in pixels.

## Usage

The tool is designed to be used in conjunction with experiment-specific folders. Each folder typically follows a structure like this:

```
<experiment_name>/
├── 01_Raw_data/
│   ├── color/
│   ├── OCV/
│   └── potentiostat/
│       └── <raw_data_files> (e.g., potentiostat.txt, MMK_ST_ProjEltest21-00000001.txt)
├── 02_Parsed_data/
│   ├── color/
│   ├── OCV/
│   └── potentiostat/
│       └── cycles.txt
├── 03_Processed_data/
│   ├── efficiencies.csv
│   ├── ocv_measurements.csv
│   └── step_metrics.csv
├── 04_Results/
│   ├── efficiencies.png
│   └── <other_plots>.png
└── config.json
```

### Parsing Raw Data

You can use either `yarst_parser.py` or `elins_parser.py` depending on your potentiostat data format.

To parse data, run the respective script from the project root, providing the path to your experiment folder:

*   **For YARST data:**
    ```bash
    python yarst_parser.py <experiment_folder_path>
    ```
    Example: `python yarst_parser.py MMK_ST_ProjEltest21`

*   **For ES8 (Elins) data:**
    ```bash
    python elins_parser.py <experiment_folder_path>
    ```
    Example: `python elins_parser.py mmk_2c`

These scripts will:
1.  Read raw data from `<experiment_folder>/01_Raw_data/potentiostat/`.
2.  Process the data and calculate basic step metrics.
3.  Save the parsed data to `<experiment_folder>/02_Parsed_data/potentiostat/cycles.txt`.
4.  Save processed metrics to `<experiment_folder>/03_Processed_data/step_metrics.csv` and OCV measurements to `<experiment_folder>/03_Processed_data/ocv_measurements.csv`.

### Post-Processing and Visualization

The `post_processing.py` file contains functions for calculating efficiencies and generating various plots. These functions are typically called from a notebook or another script after the raw data has been parsed and processed.

Key functions:
*   `calculate_efficiencies(metrics_file: pathlib.Path) -> pd.DataFrame`: Calculates coulombic, voltage, and energy efficiencies.
*   `plot_efficiencies(df: pd.DataFrame, output_path: pathlib.Path, title: str)`: Plots and saves efficiency data.
*   `plot_ocv(df: pd.DataFrame, output_path: pathlib.Path, title: str)`: Plots and saves OCV data.
*   `plot_charge_discharge_cycles(cycles_file: pathlib.Path, capacity_coulombs: float, output_path: pathlib.Path, start_cycle: int, end_cycle: int, cycle_step: int = 1, plot_legend: bool = True, plot_title: str = 'Charge-Discharge curves', current_densities: Optional[List[int]] = None, plot_colors: Optional[List[str]] = None)`: Plots charge-discharge curves for a specified range of cycles with optional stepping, custom titles, legends, and colors.

### Image Processing for Volume Measurement

The `frames_to_volumes.py` script processes images (e.g., from a webcam) to measure liquid column heights.

To process images, you'll typically run `process_all_images` from within another script or notebook, passing the relevant configuration:

*   `process_all_images(input_folder: pathlib.Path, output_folder: pathlib.Path, results_file: pathlib.Path, rois_: dict, saving_steps: bool = False)`: Processes all images in `input_folder`, saves processed images to a unique `output_folder`, and writes pixel heights to `results_file`. The `rois_` dictionary should come from your `config.json`.

## Codebase Overview

*   `base_parser.py`:
    *   Defines the `ExperStep` dataclass for holding experimental step data.
    *   Provides `BaseParser` (an abstract base class) with common functionalities like managing experiment folders, loading `config.json`, calculating nominal capacity, and methods for processing and writing data.
*   `yarst_parser.py`:
    *   Implements `YarstParser`, a subclass of `BaseParser`, specifically for parsing data from YARST potentiostat files.
*   `elins_parser.py`:
    *   Implements `ElinsParser`, a subclass of `BaseParser`, for parsing data from ES8 (Elins) potentiostat files.
*   `post_processing.py`:
    *   Contains functions for calculating efficiencies, reshaping OCV data, and generating various plots (efficiencies, OCV, charge-discharge curves).
*   `frames_to_volumes.py`:
    *   Provides functions for image manipulation (rotate, grayscale, cut ROI, blur, threshold, edge/contour detection, bounding box drawing).
    *   Includes `pipeline` and `process_image` functions for extracting liquid column heights from images.
    *   Offers `process_all_images` to automate batch processing of images.
*   `report_maker.ipynb`:
    *   A Jupyter Notebook that likely orchestrates the usage of the parsing, processing, and visualization functions. This would be your main entry point for generating reports.