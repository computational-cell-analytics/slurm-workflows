#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2026

Logic of `slurm_wf.summary`: summarize the efficiency and the core hours of the archived Slurm jobs.
The command only reads the archive. `slurm_wf.update_metadata` refreshes the data.
"""
import csv
import os
import statistics
from typing import List, Optional

try:
    import openpyxl
except ImportError:  # openpyxl is optional. The login node does not have it.
    openpyxl = None

from slurm_workflows.metadata import METADATA_FILE, archive_folders, as_list, read_metadata
from slurm_workflows.update import UNFINISHED_STATES

METADATA_COLUMNS = ["date", "task", "jobid", "job-name", "partition", "gpu", "cpus-per-task", "Memory-per-node",
                    "runtime"]
EFFICIENCIES = ["CPUEff", "MemEff", "TimeEff"]
COLUMNS = METADATA_COLUMNS + ["tasks", "State"] + EFFICIENCIES + ["core_hours"]


def parse_percent(value: str) -> Optional[float]:
    """Convert a percentage of the efficiency report into a number.

    Args:
        value: Value of the efficiency report, for example '85.3%'.

    Returns:
        float: The value without the percent sign, or None if the report has no value.
    """
    if value and value not in ("---", "N/A", ""):
        try:
            return float(value.rstrip("%"))
        except ValueError:
            return None
    return None


def efficiencies(metadict: dict, key: str) -> List[float]:
    """Return one efficiency of each task of a job that is not RUNNING or PENDING.

    Args:
        metadict: Metadata of the job.
        key: 'CPUEff', 'MemEff' or 'TimeEff'.

    Returns:
        list: The efficiencies in percent. A task without a value is left out.
    """
    values = [parse_percent(report.get(key)) for report in as_list(metadict.get("Reportseff", []))
              if report.get("State") not in UNFINISHED_STATES]
    return [value for value in values if value is not None]


def core_hours(metadict: dict) -> Optional[float]:
    """Return the core hours of a job, summed over the tasks of an array job.

    Args:
        metadict: Metadata of the job.

    Returns:
        float: The core hours of the 'SlurmOutput' entry, or None if it has no value.
    """
    hours = [output["core_hours"] for output in as_list(metadict.get("SlurmOutput", []))
             if output.get("core_hours") is not None]
    return sum(hours) if hours else None


def select_jobs(
        input_dir: str,
        pattern: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        states: Optional[List[str]] = None,
        partitions: Optional[List[str]] = None,
) -> List[dict]:
    """Read the metadata of the archived jobs which match all given filters.

    Args:
        input_dir: Directory of the archive folders.
        pattern: Part of the name of the archive folders, or None for all.
        since: First job date as YYYY-MM-DD, or None.
        until: Last job date as YYYY-MM-DD, or None.
        states: Keep a job if one of its tasks has one of these states, or None.
        partitions: Keep a job of one of these partitions, or None.

    Returns:
        list: The metadata of each job, ordered by date and task.
    """
    jobs = []
    for folder in archive_folders(input_dir, pattern):
        metadata = os.path.join(folder, METADATA_FILE)
        if not os.path.isfile(metadata):
            continue

        metadict = read_metadata(metadata)
        if since is not None and metadict["date"] < since:
            continue
        if until is not None and metadict["date"] > until:
            continue
        if states is not None and not any(report.get("State") in states
                                          for report in as_list(metadict.get("Reportseff", []))):
            continue
        if partitions is not None and metadict.get("partition") not in partitions:
            continue
        jobs.append(metadict)

    return sorted(jobs, key=lambda metadict: (metadict["date"], metadict["task"]))


def job_row(metadict: dict) -> list:
    """Return the values of a job in the order of `COLUMNS`.

    Args:
        metadict: Metadata of the job.

    Returns:
        list: The values. A missing value is None.
    """
    reports = as_list(metadict.get("Reportseff", []))
    states = ",".join(sorted({report["State"] for report in reports}))
    means = []
    for key in EFFICIENCIES:
        values = efficiencies(metadict, key)
        means.append(round(statistics.mean(values), 1) if values else None)
    hours = core_hours(metadict)
    hours = None if hours is None else round(hours, 2)
    return [metadict.get(key) for key in METADATA_COLUMNS] + [len(reports), states] + means + [hours]


def print_summary(jobs: List[dict]):
    """Print the average efficiency and the core hours of the jobs.

    An efficiency is averaged over the tasks that are not RUNNING or PENDING. The core hours are
    summed per job and then averaged over the jobs.

    Args:
        jobs: Metadata of the jobs.
    """
    print(f"Efficiency summary for {len(jobs)} job(s):")

    for key, name in (("CPUEff", "CPU"), ("MemEff", "Memory"), ("TimeEff", "Time")):
        values = [value for metadict in jobs for value in efficiencies(metadict, key)]
        label = f"Average {name} Efficiency:"
        if values:
            print(f"  {label:<27}{statistics.mean(values):.1f}% (n={len(values)})")
        else:
            print(f"  {label:<27}N/A")

    core_hours_list = [hours for hours in (core_hours(metadict) for metadict in jobs) if hours is not None]
    if core_hours_list:
        print(f"  Average Core Hours: {statistics.mean(core_hours_list):.2f} (n={len(core_hours_list)})")
        print(f"  Median Core Hours: {statistics.median(core_hours_list):.2f} (n={len(core_hours_list)})")
        print(f"  Total Core Hours:   {sum(core_hours_list):.2f}")
    else:
        print("  Core Hours:                N/A")


def write_table(jobs: List[dict], output: str):
    """Write one row per job to an .xlsx file, or to a TSV file for any other suffix.

    Args:
        jobs: Metadata of the jobs.
        output: Path of the table.
    """
    rows = [job_row(metadict) for metadict in jobs]

    if output.lower().endswith(".xlsx"):
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(COLUMNS)
        for row in rows:
            sheet.append(row)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        workbook.save(output)
    else:
        with open(output, "w", newline="") as table:
            writer = csv.writer(table, dialect="excel-tab")
            writer.writerow(COLUMNS)
            writer.writerows(rows)

    print(f"Wrote {len(rows)} job(s) to {output}.")


def summarize(
        input_dir: str,
        output: Optional[str] = None,
        pattern: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        states: Optional[List[str]] = None,
        partitions: Optional[List[str]] = None,
):
    """Print the efficiency summary of the archived jobs, and optionally write one row per job.

    Args:
        input_dir: Directory of the archive folders.
        output: Path of the table, or None. An .xlsx file needs openpyxl. Any other suffix gives TSV.
        pattern: Part of the name of the archive folders, or None for all.
        since: First job date as YYYY-MM-DD, or None.
        until: Last job date as YYYY-MM-DD, or None.
        states: Keep a job if one of its tasks has one of these states, or None.
        partitions: Keep a job of one of these partitions, or None.
    """
    if output is not None and output.lower().endswith(".xlsx") and openpyxl is None:
        raise ValueError("An .xlsx file needs openpyxl. Install openpyxl, or write a .tsv file.")

    jobs = select_jobs(input_dir, pattern, since, until, states, partitions)
    print_summary(jobs)

    if output is not None:
        write_table(jobs, output)
