#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2026

Logic of `slurm_wf.summary`: summarize the efficiency and the core hours of the archived Slurm jobs.
The command only reads the archive. `slurm_wf.update_metadata` refreshes the data.
"""
import csv
import statistics
from typing import List, Optional

try:
    import openpyxl
except ImportError:  # openpyxl is optional. The login node does not have it.
    openpyxl = None

from slurm_workflows.metadata import archive_folders, archived_jobs, as_list
from slurm_workflows.update import FINAL_STATES, NOT_STARTED_ELAPSED

METADATA_COLUMNS = ["date", "task", "jobid", "job_name", "partition", "gpu", "cpus_per_task", "memory_per_node",
                    "runtime"]
EFFICIENCIES = ["cpu_eff", "mem_eff", "time_eff"]
COLUMNS = (["date", "task", "jobid", "replaced", "job_name", "partition", "gpu", "cpus_per_task", "memory_per_node",
            "runtime", "project", "pipeline", "run", "tasks", "state", "slurm_state"] + EFFICIENCIES
           + ["core_hours"])


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


def has_result(report: dict) -> bool:
    """Check if a task of the efficiency report ran and ended.

    A task which is not final, e.g. RUNNING or UNTRACKED, has the values of a report while it ran. A task
    which was cancelled before it started has the values 0.0%.

    Args:
        report: Entry of the efficiency report.

    Returns:
        bool: True if the task is in a final state and its elapsed time is not 00:00:00.
    """
    return report.get("state") in FINAL_STATES and report.get("elapsed") != NOT_STARTED_ELAPSED


def efficiencies(metadict: dict, key: str) -> List[float]:
    """Return one efficiency of each task of a job that has a result, see `has_result()`.

    Args:
        metadict: Metadata of the job.
        key: 'cpu_eff', 'mem_eff' or 'time_eff'.

    Returns:
        list: The efficiencies in percent. A task without a value is left out.
    """
    values = [parse_percent(report.get(key)) for report in as_list(metadict.get("reportseff", []))
              if has_result(report)]
    return [value for value in values if value is not None]


def core_hours(metadict: dict) -> Optional[float]:
    """Return the core hours of a job, summed over the tasks of an array job.

    Args:
        metadict: Metadata of the job.

    Returns:
        float: The core hours of the 'slurm_output' entry, or None if it has no value.
    """
    hours = [output["core_hours"] for output in as_list(metadict.get("slurm_output", []))
             if output.get("core_hours") is not None]
    return sum(hours) if hours else None


def slurm_states(metadict: dict) -> List[str]:
    """Return the states which the slurm output files of a job show.

    Args:
        metadict: Metadata of the job.

    Returns:
        list: The different states, sorted.
    """
    return sorted({output["state"] for output in as_list(metadict.get("slurm_output", [])) if output.get("state")})


def select_jobs(
        input_dir: str,
        pattern: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        states: Optional[List[str]] = None,
        partitions: Optional[List[str]] = None,
        all_jobids: bool = False,
) -> List[dict]:
    """Read the metadata of the archived jobs which match all given filters.

    Args:
        input_dir: Directory of the archive folders.
        pattern: Part of the name of the archive folders, or None for all.
        since: First job date as YYYY-MM-DD, or None.
        until: Last job date as YYYY-MM-DD, or None.
        states: Keep a job if one of its tasks has one of these states, in the efficiency report or in
            the slurm output, or None.
        partitions: Keep a job of one of these partitions, or None.
        all_jobids: Also read the replaced JobIDs in `previous/<jobid>/`.

    Returns:
        list: A copy of the metadata of each job with the key 'replaced', ordered by date, task and JobID.
    """
    jobs = []
    for folder, job_folder, metadict in archived_jobs(archive_folders(input_dir, pattern), all_jobids):
        job_date = metadict.get("date", "")
        if since is not None and job_date < since:
            continue
        if until is not None and job_date > until:
            continue
        job_states = {report.get("state") for report in as_list(metadict.get("reportseff", []))}
        if states is not None and not (job_states | set(slurm_states(metadict))) & set(states):
            continue
        if partitions is not None and metadict.get("partition") not in partitions:
            continue
        jobs.append(dict(metadict, replaced=job_folder != folder))

    return sorted(jobs, key=lambda metadict: (metadict.get("date", ""), metadict.get("task", ""),
                                              str(metadict.get("jobid", ""))))


def job_row(metadict: dict) -> list:
    """Return the values of a job in the order of `COLUMNS`.

    Args:
        metadict: Metadata of the job.

    Returns:
        list: The values. A missing value is None.
    """
    reports = as_list(metadict.get("reportseff", []))
    pipeline = metadict.get("pipeline", {})
    values = {key: metadict.get(key) for key in METADATA_COLUMNS}
    values.update({
        "replaced": metadict.get("replaced", False),
        "project": metadict.get("project"),
        "pipeline": pipeline.get("name"),
        "run": pipeline.get("run"),
        "tasks": len(reports),
        "state": ",".join(sorted({report["state"] for report in reports if report.get("state")})),
        "slurm_state": ",".join(slurm_states(metadict)),
    })
    for key in EFFICIENCIES:
        means = efficiencies(metadict, key)
        values[key] = round(statistics.mean(means), 1) if means else None
    hours = core_hours(metadict)
    values["core_hours"] = None if hours is None else round(hours, 2)
    return [values[column] for column in COLUMNS]


def print_summary(jobs: List[dict]):
    """Print the average efficiency and the core hours of the jobs.

    An efficiency is averaged over the tasks that have a result, see `has_result()`. The core hours are
    summed per job and then averaged over the jobs.

    Args:
        jobs: Metadata of the jobs.
    """
    print(f"Efficiency summary for {len(jobs)} job(s):")

    for key, name in (("cpu_eff", "CPU"), ("mem_eff", "Memory"), ("time_eff", "Time")):
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
        all_jobids: bool = False,
):
    """Print the efficiency summary of the archived jobs, and optionally write one row per job.

    Args:
        input_dir: Directory of the archive folders.
        output: Path of the table, or None. An .xlsx file needs openpyxl. Any other suffix gives TSV.
        pattern: Part of the name of the archive folders, or None for all.
        since: First job date as YYYY-MM-DD, or None.
        until: Last job date as YYYY-MM-DD, or None.
        states: Keep a job if one of its tasks has one of these states, in the efficiency report or in
            the slurm output, or None.
        partitions: Keep a job of one of these partitions, or None.
        all_jobids: Also summarize the replaced JobIDs in `previous/<jobid>/`.
    """
    if output is not None and output.lower().endswith(".xlsx") and openpyxl is None:
        raise ValueError("An .xlsx file needs openpyxl. Install openpyxl, or write a .tsv file.")

    jobs = select_jobs(input_dir, pattern, since, until, states, partitions, all_jobids)
    print_summary(jobs)

    if output is not None:
        write_table(jobs, output)
