#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Logic of `slurm_wf.update_metadata`: update the archived metadata of Slurm jobs.
Refresh the efficiency report of a running or pending job, and add the data of the slurm output files.
"""
import os
from typing import List, Optional

from slurm_workflows.metadata import LOG_FILE, METADATA_FILE, SBATCH_FILE
from slurm_workflows.metadata import archive_folders, as_list, job_folders, read_metadata, write_metadata
from slurm_workflows.slurm import jobids_from_log, matches_jobid, reportseff_entries, reportseff_from_jobid
from slurm_workflows.slurm import add_sbatch_parameters, slurm_output_files, slurm_output_to_dict

# The state of a task which is not final and which `reportseff` no longer lists, because the Slurm
# database purged the job. `last_state` keeps the state of the last report.
UNTRACKED_STATE = "UNTRACKED"

# A job which ends in one of these states never satisfies `afterok`, so the later steps of its chain
# never start.
FAILED_STATES = ("BOOT_FAIL", "CANCELLED", "DEADLINE", "FAILED", "NODE_FAIL", "OUT_OF_MEMORY", "PREEMPTED",
                 "TIMEOUT")

# The states which do not change any more. A report with another state, such as RUNNING, PENDING,
# COMPLETING or UNTRACKED, is refreshed.
FINAL_STATES = ("COMPLETED",) + FAILED_STATES

# The elapsed time of a task which was cancelled before it started.
NOT_STARTED_ELAPSED = "00:00:00"


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
            add_sbatch_parameters(sbatch_dict, sbatch_file)

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


def archived_jobs(
    folders: List[str],
) -> List[tuple]:
    """Read the metadata of the jobs of archive folders, including the replaced JobIDs.

    Args:
        folders: Archive folders.

    Returns:
        list: One tuple per job: the archive folder, the folder of the job (the archive folder or
            `previous/<jobid>/`), and the metadata.
    """
    jobs = []
    for folder in folders:
        for job_folder in job_folders(folder):
            metadata = os.path.join(job_folder, METADATA_FILE)
            if os.path.isfile(metadata):
                jobs.append((folder, job_folder, read_metadata(metadata)))
    return jobs


def needs_refresh(
    reports: List[dict],
) -> bool:
    """Check if the efficiency report of a job can still change.

    Args:
        reports: Entries of the efficiency report.

    Returns:
        bool: True if the report is empty or has a task which is not in a final state.
    """
    return not reports or any(report.get("state") not in FINAL_STATES for report in reports)


def mark_untracked(
    reports: List[dict],
) -> bool:
    """Replace the state of each task which is not final with `UNTRACKED_STATE`.

    Args:
        reports: Entries of the efficiency report. They are changed in place.

    Returns:
        bool: True if an entry changed.
    """
    changed = False
    for report in reports:
        state = report.get("state")
        if state not in FINAL_STATES and state != UNTRACKED_STATE:
            report["last_state"] = state
            report["state"] = UNTRACKED_STATE
            changed = True
    return changed


def print_retention(
    listed: List[dict],
    unlisted: List[dict],
):
    """Print the JobIDs which `reportseff` no longer lists, to show how long the Slurm database keeps a job.

    Args:
        listed: Metadata of the queried jobs which `reportseff` lists.
        unlisted: Metadata of the queried jobs which `reportseff` does not list.
    """
    print(f"reportseff listed {len(listed)} of {len(listed) + len(unlisted)} queried JobIDs.")
    if not unlisted:
        return

    print("reportseff no longer lists these JobIDs (date, JobID, task):")
    for metadict in sorted(unlisted, key=lambda metadict: (metadict.get("date", ""), metadict["jobid"])):
        print(f"  {metadict.get('date')}  {metadict['jobid']}  {metadict.get('task')}")

    print(f"Newest unlisted job: {max(metadict.get('date', '') for metadict in unlisted)}.")
    if listed:
        print(f"Oldest listed job: {min(metadict.get('date', '') for metadict in listed)}.")


def update_reportseff(
    subfolders: List[str],
):
    """Refresh the efficiency reports which can still change.

    `reportseff` is queried once, with the JobIDs of all jobs whose report is empty or not final,
    including the replaced JobIDs in `previous/<jobid>/`. A job of any age is listed while the Slurm
    database keeps it. A job which `reportseff` no longer lists gets `UNTRACKED_STATE` for each task
    which is not final.

    Args:
        subfolders: Archive folders to update.
    """
    jobs = archived_jobs(subfolders)

    # A JobID which is not a number would make `reportseff` fail for every JobID of the call.
    queried = [(job_folder, metadict) for _, job_folder, metadict in jobs
               if str(metadict.get("jobid", "")).isdigit()
               and needs_refresh(as_list(metadict.get("reportseff", [])))]
    try:
        listed = reportseff_entries([str(metadict["jobid"]) for _, metadict in queried]) if queried else {}
    except ValueError as exc:
        print(f"Warning: the efficiency reports are not refreshed: {exc}.")
        queried = []

    updated = 0
    listed_jobs = []
    unlisted = []
    for job_folder, metadict in queried:
        entries = listed[str(metadict["jobid"])]
        if entries:
            listed_jobs.append(metadict)
            changed = entries != metadict.get("reportseff")
            metadict["reportseff"] = entries
            message = f"Updated the efficiency report of {job_folder}."
        else:
            unlisted.append(metadict)
            changed = mark_untracked(as_list(metadict.get("reportseff", [])))
            message = (f"Marked the report of {job_folder} as {UNTRACKED_STATE}, because reportseff no longer "
                       "lists it.")
        if changed:
            write_metadata(metadict, os.path.join(job_folder, METADATA_FILE))
            print(message)
            updated += 1

    if queried:
        print_retention(listed_jobs, unlisted)
    if updated == 0:
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
            reports = as_list(metadict.get("reportseff", []))
            for report in reports:
                if not matches_jobid(report["jobid"], metadict["jobid"]):
                    if metadict["jobid"] not in jobids_from_log(log_file):
                        metadict["reportseff"] = []
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
    'slurm_output'. The replaced JobIDs in `previous/<jobid>/` get their entries too.

    Args:
        subfolders: Archive folders.
        slurm_dir: Directory of the slurm output files.
    """
    update_dir = []

    for _, job_folder, metadict in archived_jobs(subfolders):
        jobid = metadict.get("jobid")
        if not jobid:
            continue

        slurm_files = slurm_output_files(slurm_dir, jobid)

        if not slurm_files:
            continue

        new_slurm_output = [slurm_output_to_dict(sf) for sf in slurm_files]
        if new_slurm_output == metadict.get("slurm_output"):
            continue

        metadict["slurm_output"] = new_slurm_output
        update_dir.append(job_folder)

        write_metadata(metadict, os.path.join(job_folder, METADATA_FILE))

    update_dir = list(set(update_dir))
    update_dir.sort()

    for folder in update_dir:
        print(f"Updated slurm output information of folder {folder}.")

    if len(update_dir) == 0:
        print("No matching slurm output files found.")


def update_archive(
        input_dir: str,
        pattern: Optional[str] = None,
        general: bool = False,
        sbatch: bool = False,
        slurm_dir: Optional[str] = None,
):
    """Update the metadata of the archive folders in a directory.

    The efficiency report is always refreshed.

    Args:
        input_dir: Directory of the archive folders.
        pattern: Part of the name of the archive folders to update, or None for all.
        general: Run `check_metadata()`, the option `--general`.
        sbatch: Run `update_sbatch_data()`, the option `--sbatch`.
        slurm_dir: Directory of the slurm output files, or None.
    """
    subfolders = archive_folders(input_dir, pattern)

    if sbatch:
        update_sbatch_data(subfolders)

    if general:
        check_metadata(subfolders)

    if slurm_dir is not None:
        update_slurm_output(subfolders, slurm_dir)

    update_reportseff(subfolders)
