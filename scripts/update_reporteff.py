#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Script for updating the archived metadata of Slurm jobs.
The efficiency report of a running or pending job is refreshed, the information of a slurm output file
can be added, and the efficiency across the archived jobs can be summarised.
"""
import argparse
import glob
import os
import statistics
import sys
from datetime import date
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from utils.metadata import LOG_FILE, METADATA_FILE, SBATCH_FILE  # noqa: E402
from utils.metadata import as_list, jobids_from_log, read_metadata, write_metadata  # noqa: E402
from utils.slurm import matches_jobid, reportseff_from_jobid, sbatch_parameters_to_dict  # noqa: E402
from utils.slurm import slurm_output_files, slurm_output_to_dict  # noqa: E402

# The efficiency report of a job is available for around one week after the submission.
REPORTSEFF_MAX_DAYS = 8

UNFINISHED_STATES = ("RUNNING", "PENDING")


def update_sbatch_data(subfolders: List[str]):
    """Update sbatch_data.

    Args:
        subfolders: List of subfolders containing metadata in the format 'metadata.json'
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
    """Update efficiency report of previously pending or running tasks.

    Args:
        subfolders: List of subfolders containing metadata in the format 'metadata.json'
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
    """Check metadata of date, task, and JobID.
    If the JobID the last sbatch job (or a previous sbatch job) is not identical to
    the jobID of the efficiency report, the report is deleted.

    Args:
        subfolders: List of subfolders containing metadata in the format 'metadata.json'
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
    """Update metadata with job information parsed from slurm output files.

    For each subfolder, the jobid stored in metadata.json is used to locate matching
    slurm-<jobid>.out or slurm-<jobid>_<array_index>.out files in slurm_dir.
    Matched files are parsed and stored as a list under the 'SlurmOutput' key.

    Args:
        subfolders: List of subfolders containing metadata in the format 'metadata.json'
        slurm_dir: Directory containing slurm output files.
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


def parse_percent(value: str) -> Optional[float]:
    """Convert a percentage of the efficiency report into a number.

    Args:
        value: Value of the efficiency report, for example '85.3%'.

    Returns:
        float: The value without the percent sign, or None if the report has no value.
    """
    if value and value not in ('---', 'N/A', ''):
        try:
            return float(value.rstrip('%'))
        except ValueError:
            return None
    return None


def average_efficiency(subfolders: List[str], slurm_dir: Optional[str] = None):
    """Compute and print average efficiency and core-hour consumption across jobs.

    Efficiency metrics (CPUEff, MemEff, TimeEff) are read from the 'Reportseff'
    section of each metadata.json. Core hours are read from the 'SlurmOutput' section
    if present, otherwise from slurm output files in slurm_dir if provided.
    Only completed jobs (not RUNNING or PENDING) contribute to efficiency averages.

    Args:
        subfolders: List of subfolders containing metadata in the format 'metadata.json'
        slurm_dir: Optional directory containing slurm output files.
    """
    cpu_effs = []
    mem_effs = []
    time_effs = []
    core_hours_list = []

    for folder in subfolders:
        metadata = os.path.join(folder, METADATA_FILE)
        if not os.path.isfile(metadata):
            continue

        metadict = read_metadata(metadata)

        for report in as_list(metadict.get("Reportseff", [])):
            if report.get("State") in UNFINISHED_STATES:
                continue
            cpu = parse_percent(report.get("CPUEff"))
            mem = parse_percent(report.get("MemEff"))
            time_eff = parse_percent(report.get("TimeEff"))
            if cpu is not None:
                cpu_effs.append(cpu)
            if mem is not None:
                mem_effs.append(mem)
            if time_eff is not None:
                time_effs.append(time_eff)

        slurm_outputs = as_list(metadict.get("SlurmOutput", []))

        if slurm_outputs:
            job_core_hours = [so["core_hours"] for so in slurm_outputs if so.get("core_hours") is not None]
            if job_core_hours:
                core_hours_list.append(sum(job_core_hours))
        elif slurm_dir:
            jobid = metadict.get("jobid")
            if jobid:
                job_core_hours = [core_hours for core_hours in
                                  (slurm_output_to_dict(sf).get("core_hours")
                                   for sf in slurm_output_files(slurm_dir, jobid))
                                  if core_hours is not None]
                if job_core_hours:
                    core_hours_list.append(sum(job_core_hours))

    print(f"Efficiency summary for {len(subfolders)} job(s):")

    if cpu_effs:
        print(f"  Average CPU Efficiency:    {statistics.mean(cpu_effs):.1f}% (n={len(cpu_effs)})")
    else:
        print("  Average CPU Efficiency:    N/A")

    if mem_effs:
        print(f"  Average Memory Efficiency: {statistics.mean(mem_effs):.1f}% (n={len(mem_effs)})")
    else:
        print("  Average Memory Efficiency: N/A")

    if time_effs:
        print(f"  Average Time Efficiency:   {statistics.mean(time_effs):.1f}% (n={len(time_effs)})")
    else:
        print("  Average Time Efficiency:   N/A")

    if core_hours_list:
        print(f"  Average Core Hours: {statistics.mean(core_hours_list):.2f} (n={len(core_hours_list)})")
        print(f"  Median Core Hours: {statistics.median(core_hours_list):.2f} (n={len(core_hours_list)})")
        print(f"  Total Core Hours:   {sum(core_hours_list):.2f}")
    else:
        print("  Core Hours:                N/A")


def main(
        input_dir: str,
        pattern: Optional[str] = None,
        check_general_info: bool = False,
        check_sbatch_info: bool = False,
        slurm_dir: Optional[str] = None,
        average: bool = False,
):
    """Update report of job efficiency, if last status was 'PENDING'.

    Args:
        input_dir: Input directory containing archived scripts.
        pattern: Pattern to match folders in job archive.
        check_general_info: Check and fix general metadata (date, task, jobid).
        check_sbatch_info: Check and update sbatch parameters.
        slurm_dir: Optional directory containing slurm output files to parse and store.
        average: Print average efficiency and core-hour summary across matching jobs.
    """
    if pattern is None:
        subfolders = [f.path for f in os.scandir(input_dir) if f.is_dir()]
    else:
        subfolders = sorted(glob.glob(os.path.join(input_dir, f"*{pattern}*"), recursive=False))
        if len(subfolders) == 0:
            raise ValueError(f"No subfolders match pattern {pattern}.")

    if check_sbatch_info:
        update_sbatch_data(subfolders)

    if check_general_info:
        check_metadata(subfolders)

    if slurm_dir is not None:
        update_slurm_output(subfolders, slurm_dir)

    update_reporteff(subfolders)

    if average:
        average_efficiency(subfolders, slurm_dir)


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description="Update report of job efficiency, if last status was 'PENDING'.")

    parser.add_argument('input_dir', type=str, help="Input directory containing sbatch script.")

    parser.add_argument('-p', "--pattern", type=str, default=None,
                        help="Pattern to match folders in job archive. Supports wildcards.")
    parser.add_argument('-s', "--slurm_dir", type=str, default=None,
                        help="Directory containing slurm output files (slurm-<job_id>.out or "
                             "slurm-<job_id>_<array_index>.out) to parse and store in metadata.")
    parser.add_argument("--general", action="store_true", help="Check general info.")
    parser.add_argument("--sbatch", action="store_true", help="Check sbatch information.")
    parser.add_argument("--average", action="store_true",
                        help="Print average efficiency and core-hour summary across matching jobs.")
    args = parser.parse_args()

    try:
        main(args.input_dir, args.pattern, args.general, args.sbatch, args.slurm_dir, args.average)
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(str(exc))
