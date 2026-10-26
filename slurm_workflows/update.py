#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Logic of `slurm_wf.update_metadata`: update the archived metadata of Slurm jobs.
Refresh the efficiency report of a running or pending job, and add the data of the slurm output files.
"""
import os
from datetime import date
from typing import List, Optional

from slurm_workflows.metadata import LOG_FILE, METADATA_FILE, SBATCH_FILE
from slurm_workflows.metadata import archive_folders, as_list, read_metadata, write_metadata
from slurm_workflows.slurm import jobids_from_log, matches_jobid, reportseff_from_jobid, sbatch_parameters_to_dict
from slurm_workflows.slurm import slurm_output_files, slurm_output_to_dict

# The efficiency report of a job is available for around one week after the submission.
REPORTSEFF_MAX_DAYS = 8

UNFINISHED_STATES = ("RUNNING", "PENDING")


def update_sbatch_data(subfolders: List[str]):
    """Copy the #SBATCH parameters into the metadata.

    Args:
        subfolders: Archive folders.
    """
    update_dir = []

    for folder in subfolders:
        metadata = os.path.join(folder, METADATA_FILE)
        if os.path.isfile(metadata):
            metadict = read_metadata(metadata)

            sbatch_file = os.path.join(folder, SBATCH_FILE)
            sbatch_dict = {}
            sbatch_parameters_to_dict(sbatch_file, sbatch_dict)

            for key, value in sbatch_dict.items():
                if key not in metadict:

                    update_dir.append(folder)
                    metadict[key] = value
                elif value != metadict[key]:
                    update_dir.append(folder)
                    metadict[key] = value

            if folder in update_dir:
                write_metadata(metadict, metadata)

    update_dir = list(set(update_dir))
    update_dir.sort()

    for folder in update_dir:
        print(f"Updated sbatch parameters of folder {folder}.")

    if len(update_dir) == 0:
        print("All directories contain updated sbatch information.")


def update_reporteff(subfolders: List[str]):
    """Refresh the efficiency report of a recent job that is pending, running, or has no report.

    Args:
        subfolders: Archive folders.
    """
    update_dir = []

    for folder in subfolders:
        metadata = os.path.join(folder, METADATA_FILE)
        if os.path.isfile(metadata):
            metadict = read_metadata(metadata)

            # check for dates in the last 8 days
            date_str = [int(i) for i in metadict["date"].split("-")]
            job_date = date(date_str[0], date_str[1], date_str[2])
            days_past = date.today() - job_date
            if days_past.days > REPORTSEFF_MAX_DAYS:
                continue

            reports = as_list(metadict.get("Reportseff", []))
            for report in reports:
                if report["State"] in UNFINISHED_STATES:
                    update_dir.append(folder)
                    break

            if len(reports) == 0:
                update_dir.append(folder)

    for folder in update_dir:
        print(f"Updating efficiency report of folder {folder}.")
        log_file = os.path.join(folder, LOG_FILE)
        metadata = os.path.join(folder, METADATA_FILE)

        metadict = read_metadata(metadata)
        reportseff_from_jobid(log_file, metadict=metadict, jobid=None)

        write_metadata(metadict, metadata)

    if len(update_dir) == 0:
        print("All directories contain updated efficiency reports.")


def check_metadata(subfolders: List[str]):
    """Correct the date and the task from the folder name, and the efficiency report from the JobID.

    A report of another JobID is deleted if `log.txt` does not list the JobID of the metadata.

    Args:
        subfolders: Archive folders.
    """
    update_metadata = []
    for folder in subfolders:
        folder_name = os.path.basename(folder)
        contents = folder_name.split("_")
        folder_date = contents[0]
        task = "_".join(contents[1:])

        log_file = os.path.join(folder, LOG_FILE)
        metadata = os.path.join(folder, METADATA_FILE)

        if os.path.isfile(metadata):
            metadict = read_metadata(metadata)

            if metadict["date"] != folder_date:
                update_metadata.append(folder)
                metadict["date"] = folder_date

            if metadict["task"] != task:
                metadict["task"] = task
                update_metadata.append(folder)

            # check if JobID of efficiency report is identical with JobID of log
            reports = as_list(metadict.get("Reportseff", []))
            for report in reports:
                if not matches_jobid(report["JobID"], metadict["jobid"]):
                    if metadict["jobid"] not in jobids_from_log(log_file):
                        metadict["Reportseff"] = []
                    reportseff_from_jobid(log_file, metadict, jobid=metadict["jobid"])
                    update_metadata.append(folder)

            write_metadata(metadict, metadata)

    update_metadata = list(set(update_metadata))
    update_metadata.sort()

    for folder in update_metadata:
        print(f"Updated essential metadata for {folder}.")


def update_slurm_output(subfolders: List[str], slurm_dir: str):
    """Add the data of the slurm output files of each job to its metadata.

    Each file `slurm-<jobid>.out` or `slurm-<jobid>_<array_index>.out` gives one entry of the list
    'SlurmOutput'.

    Args:
        subfolders: Archive folders.
        slurm_dir: Directory of the slurm output files.
    """
    update_dir = []

    for folder in subfolders:
        metadata = os.path.join(folder, METADATA_FILE)
        if not os.path.isfile(metadata):
            continue

        metadict = read_metadata(metadata)

        jobid = metadict.get("jobid")
        if not jobid:
            continue

        slurm_files = slurm_output_files(slurm_dir, jobid)

        if not slurm_files:
            continue

        new_slurm_output = [slurm_output_to_dict(sf) for sf in slurm_files]
        if new_slurm_output == metadict.get("SlurmOutput"):
            continue

        metadict["SlurmOutput"] = new_slurm_output
        update_dir.append(folder)

        write_metadata(metadict, metadata)

    update_dir = list(set(update_dir))
    update_dir.sort()

    for folder in update_dir:
        print(f"Updated slurm output information of folder {folder}.")

    if len(update_dir) == 0:
        print("No matching slurm output files found.")


def update_archive(
        input_dir: str,
        pattern: Optional[str] = None,
        check_general_info: bool = False,
        check_sbatch_info: bool = False,
        slurm_dir: Optional[str] = None,
):
    """Update the metadata of the archive folders in a directory.

    The efficiency report is always refreshed.

    Args:
        input_dir: Directory of the archive folders.
        pattern: Part of the name of the archive folders to update, or None for all.
        check_general_info: Run `check_metadata()`.
        check_sbatch_info: Run `update_sbatch_data()`.
        slurm_dir: Directory of the slurm output files, or None.
    """
    subfolders = archive_folders(input_dir, pattern)

    if check_sbatch_info:
        update_sbatch_data(subfolders)

    if check_general_info:
        check_metadata(subfolders)

    if slurm_dir is not None:
        update_slurm_output(subfolders, slurm_dir)

    update_reporteff(subfolders)
