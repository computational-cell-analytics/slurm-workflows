#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Check the data of a job before the job is submitted: every input exists, and no result which the
job would rebuild exists already.
"""
import glob
import os
import re

# Matches 'INPUT=<path>' and 'export OUTPUT_FOLDER=<path>' in a rendered sbatch script.
# 'EXTERNAL_INPUT' names an input which no step of the pipeline produces, see 'check_external_input()'.
# 'EXISTING_OUTPUT' names a result which the job would rebuild, see 'check_output_absent()'.
ASSIGNMENT_PATTERN = re.compile(
    r"^\s*(?:export\s+)?(INPUT|OUTPUT_FOLDER|EXTERNAL_INPUT|EXISTING_OUTPUT)=(\S+)")

WILDCARD_CHARACTERS = "*?["


def job_variables(
    sbatch_file: str,
) -> dict:
    """Read the INPUT, OUTPUT_FOLDER, EXTERNAL_INPUT and EXISTING_OUTPUT assignments of an sbatch script.

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


def check_job_input(
    sbatch_file: str,
) -> list:
    """Return a message if the INPUT of the job does not exist.

    An INPUT which is not an absolute path is an S3 object key, so it is not checked.

    Args:
        sbatch_file: Path to an sbatch script with all placeholders filled in.

    Returns:
        list: Warning messages. The list is empty if the input of the job exists.
    """
    input_path = job_variables(sbatch_file).get("INPUT")

    if input_path is None:
        return []

    if not os.path.isabs(input_path):
        print(f"The input {input_path} is no local path. The check of the input is skipped.")
        return []

    if not path_exists(input_path):
        return [f"the input of the job does not exist: {input_path}"]

    return []


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


def check_output_absent(
    sbatch_file: str,
) -> list:
    """Return a message if the job would rebuild a result which already exists.

    An example is a MoBIE source, which is built from scratch, so a second run drops every entry
    which was added to its table afterwards. The check runs before the submission and not in the
    job, so that `--force` can relax it.

    Args:
        sbatch_file: Path to an sbatch script with all placeholders filled in.

    Returns:
        list: Warning messages. The list is empty if the job rebuilds no existing result.
    """
    existing_output = job_variables(sbatch_file).get("EXISTING_OUTPUT")

    if existing_output is None or not os.path.isabs(existing_output):
        return []

    if not path_exists(existing_output):
        return []

    return [f"the result of the job exists and would be rebuilt: {existing_output}"]
