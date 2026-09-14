#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Check that the input data of a job exists before the job is submitted.
"""
import glob
import os
import re

# Written by the apply step. It is the input of the segmentation step.
SEGMENTATION_INPUT = "predictions.zarr"

# Scale level of an OME-Zarr transferred from the S3 bucket. An n5 of the initial processing uses
# 'setup<n>/timepoint0/s0' instead, because it holds every stain in one file.
OME_ZARR_KEY = "s0"

# Matches 'INPUT=<path>' and 'export OUTPUT_FOLDER=<path>' in a rendered sbatch script.
# 'EXTERNAL_INPUT' names an input which no step of the pipeline produces, see 'check_external_input()'.
ASSIGNMENT_PATTERN = re.compile(r"^\s*(?:export\s+)?(INPUT|OUTPUT_FOLDER|EXTERNAL_INPUT)=(\S+)")

WILDCARD_CHARACTERS = "*?["


def job_variables(
    sbatch_file: str,
) -> dict:
    """Read the INPUT, OUTPUT_FOLDER and EXTERNAL_INPUT assignments of an sbatch script.

    A variable which is assigned more than once keeps the value of the last assignment.

    Args:
        sbatch_file: Path to an sbatch script with all placeholders filled in.

    Returns:
        dict: Dictionary mapping the variable name to its value.
    """
    variables = {}

    with open(sbatch_file, "rt", encoding="utf8", errors="ignore") as myfile:
        for line in myfile:
            match = ASSIGNMENT_PATTERN.match(line)
            if match:
                variables[match.group(1)] = match.group(2).strip("\"'")

    return variables


def path_exists(
    path: str,
) -> bool:
    """Check a path which can contain a wildcard.

    Args:
        path: Path of a file or a directory.

    Returns:
        bool: True if the path exists, or if a wildcard in the path has at least one match.
    """
    if any(character in path for character in WILDCARD_CHARACTERS):
        return len(glob.glob(path)) > 0
    return os.path.exists(path)


def resolve_input(
    cochlea_dir: str,
    n5_name: str,
    n5_key: str,
    stain: str,
) -> tuple:
    """Return the file name of the job input and the matching input key.

    The n5 of the initial processing wins. The OME-Zarr transferred from the S3 bucket is the
    fallback, because the n5 is deleted after a cochlea is processed. If neither exists, the n5 is
    reported, so that `check_job_input()` names the familiar path.

    Args:
        cochlea_dir: Directory of the cochlea.
        n5_name: File name of the n5, or None if the stain list is unknown.
        n5_key: Input key of the n5.
        stain: Stain of the job. It names the OME-Zarr file.

    Returns:
        tuple of str: the file name of the input, and its input key.
    """
    ome_zarr_name = f"{stain}.ome.zarr"

    if n5_name is None:
        return ome_zarr_name, OME_ZARR_KEY

    if path_exists(os.path.join(cochlea_dir, n5_name)):
        return n5_name, n5_key

    if path_exists(os.path.join(cochlea_dir, ome_zarr_name)):
        return ome_zarr_name, OME_ZARR_KEY

    return n5_name, n5_key


def check_job_input(
    sbatch_file: str,
) -> list:
    """Return one message per input of the job which does not exist.

    An INPUT which is not an absolute path is an S3 object key, so it is not checked.
    A job without an INPUT is a segmentation job. Its input is the prediction of the apply
    step inside OUTPUT_FOLDER.

    Args:
        sbatch_file: Path to an sbatch script with all placeholders filled in.

    Returns:
        list: Warning messages. The list is empty if all inputs of the job exist.
    """
    variables = job_variables(sbatch_file)
    input_path = variables.get("INPUT")
    output_folder = variables.get("OUTPUT_FOLDER")

    messages = []

    if input_path is not None:
        if not os.path.isabs(input_path):
            print(f"The input {input_path} is no local path. The check of the input is skipped.")
        elif not path_exists(input_path):
            messages.append(f"the input of the job does not exist: {input_path}")

    elif output_folder is not None:
        prediction = os.path.join(output_folder, SEGMENTATION_INPUT)
        if not path_exists(prediction):
            messages.append(f"the prediction of the apply step does not exist: {prediction}")

    return messages


def check_external_input(
    sbatch_file: str,
) -> list:
    """Return a message if the external input of a job does not exist.

    An external input is not produced by any step of the pipeline, so it is checked for every step
    and not only for the first one. A value which is no absolute path is skipped, as in
    `check_job_input()`.

    Args:
        sbatch_file: Path to an sbatch script with all placeholders filled in.

    Returns:
        list: Warning messages. The list is empty if the external input of the job exists.
    """
    external_input = job_variables(sbatch_file).get("EXTERNAL_INPUT")

    if external_input is None or not os.path.isabs(external_input):
        return []

    if path_exists(external_input):
        return []

    return [f"the external input of the job does not exist: {external_input}"]
