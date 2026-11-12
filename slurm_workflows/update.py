#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Logic of `slurm_wf.update_metadata`: update the archived metadata of Slurm jobs.
Refresh the efficiency report of a running or pending job, and add the data of the slurm output files.
"""
import os
from typing import List, Optional

from slurm_workflows.metadata import LOG_FILE, METADATA_FILE, PREVIOUS_DIR, SBATCH_FILE
from slurm_workflows.metadata import archive_folders, archived_jobs, as_list, init_metadict, write_metadata
from slurm_workflows.slurm import jobids_from_log, matches_jobid, reportseff_entries, reportseff_from_jobid
from slurm_workflows.slurm import add_sbatch_parameters, slurm_output_files, slurm_output_to_dict

# The state of a task which is not final and which `reportseff` no longer lists, because the Slurm
# database purged the job. `last_state` keeps the state of the last report.
UNTRACKED_STATE = "UNTRACKED"

# A job which ends in one of these states never satisfies `afterok`, so the later steps of its chain
# never start. An unknown state does not count, so that a step is not marked by mistake.
FAILED_STATES = ("BOOT_FAIL", "CANCELLED", "DEADLINE", "FAILED", "NODE_FAIL", "OUT_OF_MEMORY", "PREEMPTED",
                 "TIMEOUT")

# The states which do not change any more. A report with another state, such as RUNNING, PENDING,
# COMPLETING or UNTRACKED, is refreshed.
FINAL_STATES = ("COMPLETED",) + FAILED_STATES

# The elapsed time of a task which was cancelled before it started.
NOT_STARTED_ELAPSED = "00:00:00"


def update_sbatch_data(subfolders: List[str]):
    """Copy the #SBATCH parameters into the metadata, also of the replaced JobIDs.

    A job folder without `sbatch.sbatch`, such as a backfilled one, is skipped.

    Args:
        subfolders: Archive folders.
    """
    updated = []
    for _, job_folder, metadict in archived_jobs(subfolders):
        sbatch_file = os.path.join(job_folder, SBATCH_FILE)
        if not os.path.isfile(sbatch_file):
            continue

        sbatch_dict = {}
        add_sbatch_parameters(sbatch_dict, sbatch_file)
        if all(metadict.get(key) == value for key, value in sbatch_dict.items()):
            continue

        metadict.update(sbatch_dict)
        write_metadata(metadict, os.path.join(job_folder, METADATA_FILE))
        updated.append(job_folder)

    for job_folder in sorted(updated):
        print(f"Updated sbatch parameters of folder {job_folder}.")
    if not updated:
        print("All directories contain updated sbatch information.")


def never_started(
    metadict: dict,
) -> bool:
    """Check if a job never started.

    `reportseff` lists a job which was cancelled before it started, e.g. because its dependency failed,
    as CANCELLED with the elapsed time 00:00:00. A slurm output file exists only for a job which started.

    Args:
        metadict: Metadata of the job.

    Returns:
        bool: True if the job has no report or only tasks which were cancelled before they started, and
            no slurm output.
    """
    if any(output.get("started") for output in as_list(metadict.get("slurm_output", []))):
        return False
    return all(report.get("state") == "CANCELLED" and report.get("elapsed") == NOT_STARTED_ELAPSED
               for report in as_list(metadict.get("reportseff", [])))


def chain_failure(
    jobid: str,
    index: dict,
) -> Optional[dict]:
    """Find the step of a chain which failed at or before a job.

    Args:
        jobid: JobID of the job.
        index: Metadata of the archived jobs, by JobID.

    Returns:
        dict: 'jobid', 'step' and 'state' of the failed step, or None if the job is not archived, has
            not ended, or completed.
    """
    metadict = index.get(jobid)
    if metadict is None:
        return None

    pipeline = metadict.get("pipeline", {})
    if "failed_at" in pipeline:
        return pipeline["failed_at"]

    # A job which never started was cancelled because an earlier step failed, or by hand. Only in the
    # second case is the job itself the failed step.
    if never_started(metadict) and "depends_on" in pipeline:
        failure = chain_failure(pipeline["depends_on"], index)
        if failure is not None:
            return failure

    states = [report.get("state") for report in as_list(metadict.get("reportseff", []))]
    # One failed task of an array is enough, because `afterok` needs every task to complete.
    failed = [state for state in states if state in FAILED_STATES]
    if failed:
        return {"jobid": jobid, "step": pipeline.get("step"), "state": failed[0]}

    return None


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
    all_folders: List[str],
):
    """Refresh the efficiency reports which can still change, and mark the steps of a failed chain.

    `reportseff` is queried once, with the JobIDs of all jobs whose report is empty or not final,
    including the replaced JobIDs in `previous/<jobid>/`. A job of any age is listed while the Slurm
    database keeps it. A job which `reportseff` no longer lists gets `UNTRACKED_STATE` for each task
    which is not final. A step of a pipeline which never started because an earlier step failed gets
    `failed_at` in its `pipeline` entry, and is not queried again.

    Args:
        subfolders: Archive folders to update.
        all_folders: All archive folders of the archive directory, which hold the earlier steps.
    """
    jobs = archived_jobs(all_folders)
    # The index and the jobs share the metadata, so a refreshed report is visible to the chain check.
    index = {metadict.get("jobid"): metadict for _, _, metadict in jobs}

    selected = {os.path.realpath(folder) for folder in subfolders}
    jobs = [job for job in jobs if os.path.realpath(job[0]) in selected]

    # A JobID which is not a number would make `reportseff` fail for every JobID of the call.
    queried = [(job_folder, metadict) for _, job_folder, metadict in jobs
               if str(metadict.get("jobid", "")).isdigit() and "failed_at" not in metadict.get("pipeline", {})
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

    # The mark needs no `reportseff`, so it uses the archived reports if `reportseff` is missing.
    for _, job_folder, metadict in jobs:
        pipeline = metadict.get("pipeline", {})
        if "failed_at" in pipeline or "depends_on" not in pipeline or not never_started(metadict):
            continue
        failure = chain_failure(pipeline["depends_on"], index)
        if failure is None:
            continue
        pipeline["failed_at"] = failure
        write_metadata(metadict, os.path.join(job_folder, METADATA_FILE))
        print(f"Step {pipeline.get('step')} of pipeline {pipeline.get('name')} in {job_folder} never "
              f"started: step {failure['step']} (JobID {failure['jobid']}) ended with {failure['state']}.")
        updated += 1

    if queried:
        print_retention(listed_jobs, unlisted)
    if updated == 0:
        print("All directories contain updated efficiency reports.")


def check_metadata(subfolders: List[str]):
    """Correct the date and the task from the folder name, and the efficiency report from the JobID.

    The replaced JobIDs in `previous/<jobid>/` take the date and the task from the name of their
    archive folder too. A report with an entry of another JobID is queried again, see
    `reportseff_from_jobid()`.

    Args:
        subfolders: Archive folders.
    """
    updated = []
    for folder, job_folder, metadict in archived_jobs(subfolders):
        contents = os.path.basename(folder).split("_")
        changed = False
        for key, value in (("date", contents[0]), ("task", "_".join(contents[1:]))):
            if metadict.get(key) != value:
                metadict[key] = value
                changed = True

        jobid = metadict.get("jobid")
        reports = as_list(metadict.get("reportseff", []))
        if jobid and any(not matches_jobid(report.get("jobid", ""), jobid) for report in reports):
            reportseff_from_jobid(os.path.join(folder, LOG_FILE), metadict, jobid=jobid)
            changed = True

        if changed:
            write_metadata(metadict, os.path.join(job_folder, METADATA_FILE))
            updated.append(job_folder)

    for job_folder in sorted(updated):
        print(f"Updated essential metadata for {job_folder}.")


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


def backfill_replaced_jobs(
    subfolders: List[str],
):
    """Create `previous/<jobid>/metadata.json` for each earlier JobID of `log.txt` which has no metadata.

    An archive folder from before `previous/<jobid>/` keeps only the files of its latest JobID. The new
    metadata holds 'date', 'task', 'jobid' and 'backfilled'. The refresh of the efficiency report and
    the option `--slurm-dir` add the data of the job. An empty `metadata.json` counts as missing.

    Args:
        subfolders: Archive folders.
    """
    created = 0
    for folder, _, top_level in archived_jobs(subfolders, include_previous=False):
        log_file = os.path.join(folder, LOG_FILE)
        if not os.path.isfile(log_file):
            continue

        jobid = top_level.get("jobid")
        jobids = jobids_from_log(log_file)
        if jobid not in jobids:
            print(f"Warning: {folder} is not backfilled, because log.txt does not list its JobID '{jobid}'.")
            continue

        for earlier in jobids[:jobids.index(jobid)]:
            job_folder = os.path.join(folder, PREVIOUS_DIR, earlier)
            metadata = os.path.join(job_folder, METADATA_FILE)
            if os.path.isfile(metadata) and os.path.getsize(metadata) > 0:
                continue
            metadict = init_metadict(folder)
            metadict.update({"jobid": earlier, "reportseff": [], "backfilled": True})
            os.makedirs(job_folder, exist_ok=True)
            write_metadata(metadict, metadata)
            created += 1

    print(f"Backfilled the metadata of {created} earlier JobID(s).")


def update_archive(
        input_dir: str,
        pattern: Optional[str] = None,
        general: bool = False,
        sbatch: bool = False,
        slurm_dir: Optional[str] = None,
        backfill: bool = False,
):
    """Update the metadata of the archive folders in a directory.

    The efficiency report is always refreshed, and a step of a failed chain is marked.

    Args:
        input_dir: Directory of the archive folders.
        pattern: Part of the name of the archive folders to update, or None for all.
        general: Run `check_metadata()`, the option `--general`.
        sbatch: Run `update_sbatch_data()`, the option `--sbatch`.
        slurm_dir: Directory of the slurm output files, or None.
        backfill: Run `backfill_replaced_jobs()` first, the option `--backfill`.
    """
    subfolders = archive_folders(input_dir, pattern)

    if backfill:
        backfill_replaced_jobs(subfolders)

    if sbatch:
        update_sbatch_data(subfolders)

    if general:
        check_metadata(subfolders)

    if slurm_dir is not None:
        update_slurm_output(subfolders, slurm_dir)

    all_folders = subfolders if pattern is None else archive_folders(input_dir)
    update_reportseff(subfolders, all_folders)
