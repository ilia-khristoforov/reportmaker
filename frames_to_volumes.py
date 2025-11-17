#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Functions and pipeline for processing of the frames obtained
from the webcam on VRFB experiment. As the output, user gets a
CSV file with electrolyte level heights in pixels.

@author: Ilia Khristoforov
"""

import cv2
import os
import csv
import datetime
import pathlib
from typing import Optional

def rotate(img, angle):
    """
    Rotates an image by a specified angle.

    Args:
        img (numpy.ndarray): The input image.
        angle (float): The rotation angle in degrees.

    Returns:
        numpy.ndarray: The rotated image.
    """
    (h, w) = img.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR)
    return rotated

def convert_to_gray(img):
    return  cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

def convert_to_color(img):
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

def save_image(folder: pathlib.Path | str, name: str, img):
    """
    Saves an image to a specified folder with a given name.

    Args:
        folder (pathlib.Path | str): The target folder to save the image. If empty or a string,
                                      saves to the current directory.
        name (str): The filename for the saved image.
        img (numpy.ndarray): The image to save.
    """
    if folder:
        if isinstance(folder, str):
            path = os.path.join(folder, name)
        else:
            path = folder / name
    else:
        path = name
    cv2.imwrite(str(path), img)
    print(f'{name} saved to {folder if folder else "current directory"}')

def cut_roi(img, roi_coordinates):
    """
    Cuts a Region of Interest (ROI) from an image.

    Args:
        img (numpy.ndarray): The input image.
        roi_coordinates (tuple): A tuple (x1, x2, y1, y2) defining the ROI.

    Returns:
        numpy.ndarray: The image cropped to the specified ROI.
    """
    x1, x2, y1, y2 = roi_coordinates
    return img[y1:y2, x1:x2]

def blur(img):  
    """
    Applies a Gaussian blur to an image to smooth noise.

    Args:
        img (numpy.ndarray): The input image.

    Returns:
        numpy.ndarray: The blurred image.

    Raises:
        ValueError: If the input image is empty or None.
    """
    if img is None or img.size == 0:
        raise ValueError("Input image to blur() is empty or None.")
    blurred = cv2.GaussianBlur(img, (5, 5), 0)  # Smooths noise
    return blurred

def threshold(img, thresholds=(50,255)):
    """
    Applies binary thresholding to a grayscale image.

    Args:
        img (numpy.ndarray): The input grayscale image.
        thresholds (tuple, optional): A tuple (thresh_min, thresh_max) for thresholding.
                                      Defaults to (50, 255).

    Returns:
        numpy.ndarray: The thresholded binary image.
    """
    _, thresh = cv2.threshold(img, int(thresholds[0]), int(thresholds[1]), cv2.THRESH_BINARY)
    return thresh

def edges_detection(img):
    """
    Performs Canny edge detection on an image.

    Args:
        img (numpy.ndarray): The input image (preferably grayscale).

    Returns:
        numpy.ndarray: The image with detected edges.
    """
    return cv2.Canny(img, 50, 150)

def contours_detection(img):
    """
    Detects contours in a binary image.

    Args:
        img (numpy.ndarray): The input binary image.

    Returns:
        list: A list of detected contours.
    """
    contours, _ = cv2.findContours(img.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return contours

def find_pixel_length(contours):
    """
    Finds the bounding box with the maximum width among detected contours,
    representing the pixel length of a liquid column.

    Args:
        contours (list): A list of contours found in an image.

    Returns:
        tuple: A tuple containing the bounding box (x, y, w, h) and its width (w).
    """
    max_box = None
    max_width = 0
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        if w > max_width:
            max_width = w
            max_box = (x, y, w, h)
    x, y, w, h = max_box
    print(f"Liquid column length: {w} pixels")
    return max_box, w

def draw_bbox(img, box, roi):
    """
    Draws a bounding box on an image.

    Args:
        img (numpy.ndarray): The image to draw on.
        box (tuple): The bounding box coordinates (x, y, w, h).
        roi (tuple): The ROI coordinates (x1, x2, y1, y2) relative to the original image,
                     used to adjust bounding box position for drawing.

    Returns:
        numpy.ndarray: The image with the bounding box drawn.
    """
    x, y, w, h = box
    x1, x2, y1, y2 = roi
    cv2.rectangle(img, (x+x1, y+y1), (x+x1 + w, y+y1 + h), (0, 255, 0), 2)
    return img

def pipeline(img, angle, roi, thresholds, index, saving_steps: bool = True, steps_path: Optional[pathlib.Path] = None):
    """
    Processes an image through a series of steps to find the pixel length of a liquid column.

    Args:
        img (numpy.ndarray): The input image.
        angle (float): Rotation angle for the image.
        roi (tuple): Region of Interest coordinates (x1, x2, y1, y2).
        thresholds (tuple): Threshold values for binary filtering.
        index (str): Identifier for the current processing (e.g., ROI key).
        saving_steps (bool, optional): If True, saves intermediate processing steps. Defaults to True.
        steps_path (Optional[pathlib.Path], optional): Directory to save intermediate steps. Required if saving_steps is True.

    Returns:
        tuple: Bounding box (x, y, w, h) and pixel height of the detected liquid column.
    """
    rotated = rotate(img, angle) # Rotate image
    gray = convert_to_gray(rotated) # Convert to grayscale
    roi_img = cut_roi(gray, roi)    # Crop region of interest
    blurred = blur(roi_img)    # Blur
    thresh = threshold(blurred, thresholds)     # Thresholding
    edges = edges_detection(thresh)     # Edge detection
    contours = contours_detection(edges)   # Find contours
    bbox, height = find_pixel_length(contours)  # Find bounding box

    if saving_steps and steps_path:
        save_image(steps_path, f'1_rotated_{index}.jpg', rotated)
        save_image(steps_path, f'2_gray_{index}.jpg', gray)
        save_image(steps_path, f'3_roi_{index}.jpg', roi_img)
        save_image(steps_path, f'4_blur_{index}.jpg', blurred)
        save_image(steps_path, f'5_thresh_{index}.jpg', thresh)
        save_image(steps_path, f'6_edges_{index}.jpg', edges)

    return bbox, height

def process_image(img_name: pathlib.Path, rois_: dict, output_folder: Optional[pathlib.Path] = None, saving_steps: bool = False, steps_path: Optional[pathlib.Path] = None):
    """
    Processes a single image by applying a pipeline for each defined ROI.

    Args:
        img_name (pathlib.Path): Path to the input image file.
        rois_ (dict): A dictionary of ROIs, where each key is an ROI identifier
                      and each value is a dictionary containing 'angle', 'roi',
                      and 'thresholds' for that ROI.
        output_folder (Optional[pathlib.Path], optional): Folder to save the final processed image. If None,
                                                          overwrites the input image. Defaults to None.
        saving_steps (bool, optional): If True, saves intermediate processing steps for each ROI.
                                       Defaults to False.
        steps_path (Optional[pathlib.Path], optional): Directory to save intermediate steps. Required if saving_steps is True.

    Returns:
        list: A list of pixel lengths (heights) for each processed ROI.
    """
    frame = cv2.imread(str(img_name)) # Load image
    if frame is None:
        raise FileNotFoundError(f"Image file '{img_name}' could not be loaded. Check the path and file existence.")
    lengths = []
    final_img = frame.copy()
    # Process for each ROI
    for roi_key, roi_value in rois_.items():
        bbox, height = pipeline(frame, roi_value['angle'], roi_value['roi'], roi_value['thresholds'], roi_key, saving_steps=saving_steps, steps_path=steps_path)
        final_img = rotate(final_img, roi_value['angle'])
        final_img = draw_bbox(final_img, bbox, roi_value['roi'])
        final_img = rotate(final_img, -roi_value['angle'])
        lengths.append(height)

    # Save the final image to the output folder, preserving the original filename
    if output_folder:
        os.makedirs(output_folder, exist_ok=True)
        base_name = img_name.name
        save_image(output_folder, base_name, final_img)
    else:
        save_image('', img_name.name, final_img)
    return lengths

def get_unique_output_folder(base_folder: pathlib.Path) -> pathlib.Path:
    """
    Creates a unique output folder by appending a timestamp if a folder with the given name already exists.

    Args:
        base_folder (pathlib.Path): The desired base name for the output folder.

    Returns:
        pathlib.Path: A unique folder path.
    """
    if not base_folder.exists():
        return base_folder
    else:
        # Append timestamp to folder name
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        new_folder = base_folder.with_name(f"{base_folder.name}_{timestamp}")
        while new_folder.exists():
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            new_folder = base_folder.with_name(f"{base_folder.name}_{timestamp}")
        return new_folder

def process_all_images(input_folder: pathlib.Path, output_folder: pathlib.Path, results_file: pathlib.Path, rois_: dict, saving_steps: bool = False):
    """
    Processes all images in an input folder, extracts liquid column lengths for specified ROIs,
    and saves the results to a CSV file.

    Args:
        input_folder (pathlib.Path): Path to the folder containing input images.
        output_folder (pathlib.Path): Base path for the output folder where processed images will be saved.
                                     A unique subfolder will be created with a timestamp.
        results_file (pathlib.Path): Path to the CSV file where pixel heights will be recorded.
        rois_ (dict): A dictionary of ROIs, similar to the 'rois' parameter in process_image.
        saving_steps (bool, optional): If True, saves intermediate processing steps for each ROI and image.
                                       Defaults to False.
    """
    # Ensure a new unique output folder is created
    unique_output_folder = get_unique_output_folder(output_folder)
    unique_output_folder.mkdir(parents=True, exist_ok=True)

    # Also create a unique steps folder for saving intermediate steps if needed
    steps_path = unique_output_folder / "steps"
    steps_path.mkdir(parents=True, exist_ok=True)

    # Open CSV for writing results
    with open(results_file, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["filename", "catholyte", "anolyte"])  # Headers

        for filename in os.listdir(input_folder):
            if filename.lower().endswith(('.jpg', '.jpeg')):
                input_path = input_folder / filename
                
                # Process image
                try:
                    result = process_image(
                        input_path, 
                        rois_, 
                        output_folder=unique_output_folder, 
                        saving_steps=saving_steps, 
                        steps_path=steps_path
                    )  # Returns list

                    # Write result to CSV
                    writer.writerow([filename, result[0], result[1]])

                except Exception as e:
                    print(f"Error processing {filename}: {e}")

    print(f"Processing complete. Results saved in {unique_output_folder} and {results_file}")


# --- User configuration ---

folder = pathlib.Path('C:/MyFolder/origin docs/17.08/')

STEPS_PATH = folder / 'steps'
INPUT_FOLDER = folder / 'raw_'
OUTPUT_FOLDER = folder / 'processed'
RES_CSV = folder / 'pixel_heights.txt'

rois = {
    'catholyte': {'angle': -3.90, 'thresholds': (40,220), 'roi':(185, 538, 257, 278)},
    'anolyte': {'angle': -7.20, 'thresholds': (40,100),'roi': (196, 581, 359, 382)}
}

process_all_images(INPUT_FOLDER, OUTPUT_FOLDER, RES_CSV, rois, saving_steps=False)

# process_image('D:/SOC color sensor/imba   lances/imbalance_less_oxidation/17 Jun 16_06_04.jpg', rois, './', True, 'D:/SOC color sensor/imbalances/imbalance_less_oxidation/processed/steps')
# img = cv2.imread('D:/SOC color sensor/imbalances/imbalance_less_oxidation/raw/18 Jun 03_27_17.jpg')
# rotated = rotate(img, rois['catholyte']['angle'])
# roi_img = cut_roi(rotated, rois['catholyte']['roi'])
# save_image('', 'roi_img.jpg', roi_img)