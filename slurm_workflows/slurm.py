#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Parsing of sbatch scripts, of slurm output files and of the efficiency report of a job.
"""
import glob
import os
import re
import shutil
import subprocess
from typing import List

# The columns of a `reportseff` line, in order.
REPORTSEFF_KEYS = ("jobid", "state", "elapsed", "time_eff", "cpu_eff", "mem_eff")

# The number of JobIDs of one `reportseff` call, which keeps the command line short.
REPORTSEFF_CHUNK = 100

SBATCH_PARAMETERS = [
    {"param": ["-A", "--account"],
     "descr": "account"},

    {"param": ["-a", "--array"],
     "descr": "job_array"},

    {"param": ["-c", "--cpus-per-task"],
     "descr": "cpus_per_task"},

    {"param": ["-G", "--gpus"],
     "descr": "gpu"},

    {"param": ["--job-name"],
     "descr": "job_name"},

    {"param": ["--mail-user"],
     "descr": "mail_user"},

    {"param": ["--mem"],
     "descr": "memory_per_node"},

    {"param": ["-t", "--time"],
     "descr": "runtime"},

    {"param": ["-p", "--partition"],
     "descr": "partition"},
]


def jobids_from_log(
    log_file: str,
) -> list:
    """Read the JobIDs of a log file, oldest first.

    Args:
        log_file: Text file with one JobID per line.

    Returns:
        list: The JobIDs of the log file. The list is empty if the file does not exist.
    """
    jobids = []

    if os.path.isfile(log_file):
        with open(log_file, "rt", encoding="utf8", errors="ignore") as myfile:
            for line in myfile:
                content = line.strip()
                if len(content) != 0:
                    jobids.append(content.split()[0])

    return jobids


def add_sbatch_parameters(
    metadict: dict,
    sbatch_file: str,
) -> None:
    """Add the #SBATCH parameters of an sbatch script to the metadata.

    Args:
        metadict: Metadata of the job.
        sbatch_file: Path of the sbatch script.
    """
    with open(sbatch_file, "rt", encoding="utf8", errors="ignore") as myfile:
        for line in myfile:
            # Slurm reads only a line which starts with '#SBATCH'. '##SBATCH' disables a directive.
            contents = line.split()
            if len(contents) >= 2 and contents[0] == "#SBATCH":
                for p in SBATCH_PARAMETERS:
                    if contents[1] in p["param"] and len(contents) >= 3:
                        metadict[p["descr"]] = contents[2]
                    elif "=" in contents[1] and contents[1].split("=")[0] in p["param"]:
                        metadict[p["descr"]] = contents[1].split("=", 1)[1]


def slurm_output_files(
    slurm_dir: str,
    jobid: str,
) -> list:
    """Find the slurm output files of a job and of its array tasks.

    Args:
        slurm_dir: Directory of the slurm output files.
        jobid: JobID of the job.

    Returns:
        list: Sorted paths of the matching slurm output files.
    """
    return sorted(
        glob.glob(os.path.join(slurm_dir, f"slurm-{jobid}.out")) +
        glob.glob(os.path.join(slurm_dir, f"slurm-{jobid}_*.out"))
    )


def slurm_output_to_dict(
    slurm_file: str,
) -> dict:
    """Read the 'Job Information' section of a slurm output file.

    Args:
        slurm_file: Path of a file `slurm-<jobid>.out`, or `slurm-<jobid>_<array_index>.out` of an array task.

    Returns:
        dict: The keys 'file', 'submitted', 'started', 'ended', 'elapsed_min', 'limit_min', 'cpus',
            'nodes' and 'core_hours', and 'array_index' for an array task.
    """
    filename = os.path.basename(slurm_file)
    job_info = {"file": filename}

    array_match = re.match(r'slurm-\d+_(\d+)\.out', filename)
    if array_match:
        job_info["array_index"] = int(array_match.group(1))

    in_job_info = False
    with open(slurm_file, 'rt', encoding='utf-8', errors='ignore') as f:
        for line in f:
            if 'Job Information' in line:
                in_job_info = True
                continue
            if in_job_info:
                if line.startswith('==='):
                    break
                line = line.strip()
                if not line:
                    continue
                if line.startswith('Submitted:'):
                    job_info['submitted'] = line.split(':', 1)[1].strip()
                elif line.startswith('Started:'):
                    job_info['started'] = line.split(':', 1)[1].strip()
                elif line.startswith('Ended:'):
                    job_info['ended'] = line.split(':', 1)[1].strip()
                elif line.startswith('Elapsed:'):
                    elapsed_match = re.search(r'Elapsed:\s*(\d+)\s*min', line)
                    limit_match = re.search(r'Limit:\s*(\d+)\s*min', line)
                    if elapsed_match:
                        job_info['elapsed_min'] = int(elapsed_match.group(1))
                    if limit_match:
                        job_info['limit_min'] = int(limit_match.group(1))
                elif line.startswith('CPUs:'):
                    cpu_match = re.search(r'CPUs:\s*(\d+)', line)
                    node_match = re.search(r'Nodes:\s*(\d+)', line)
                    if cpu_match:
                        job_info['cpus'] = int(cpu_match.group(1))
                    if node_match:
                        job_info['nodes'] = int(node_match.group(1))
                elif 'core-hours' in line.lower():
                    core_match = re.search(r'([\d.]+)\s*core-hours', line, re.IGNORECASE)
                    if core_match:
                        job_info['core_hours'] = float(core_match.group(1))

    return job_info


def matches_jobid(
    entry: str,
    jobid: str,
) -> bool:
    """Check if a JobID of `reportseff` belongs to a job.

    Args:
        entry: JobID of a report line, such as '1234', '1234_0' for an array task, or '1234.batch'.
        jobid: JobID of the job.

    Returns:
        bool: True for the job itself, one of its array tasks, or one of its steps.
    """
    return entry == jobid or entry.startswith(f"{jobid}_") or entry.startswith(f"{jobid}.")


def run_reportseff(
    jobids: List[str],
) -> str:
    """Run `reportseff <jobid> ...`, with at most `REPORTSEFF_CHUNK` JobIDs per call.

    A query by JobID has no time window and also lists a job which never started, e.g. a step whose
    dependency failed. A `reportseff` on PATH can still fail, e.g. if its Python module is not
    installed in the active environment.

    Args:
        jobids: JobIDs of the jobs.

    Returns:
        str: The standard output of all calls.
    """
    executable = shutil.which("reportseff")
    if executable is None:
        raise ValueError("reportseff was not found")

    outputs = []
    for start in range(0, len(jobids), REPORTSEFF_CHUNK):
        try:
            result = subprocess.run([executable] + jobids[start:start + REPORTSEFF_CHUNK], stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, universal_newlines=True)
        except OSError as exc:
            raise ValueError(f"reportseff could not be run: {exc}") from exc
        if result.returncode != 0:
            lines = result.stderr.strip().splitlines() or [f"exit code {result.returncode}"]
            raise ValueError(f"reportseff failed: {lines[-1]}")
        outputs.append(result.stdout)
    return "\n".join(outputs)


def parse_reportseff(
    output: str,
) -> List[dict]:
    """Read the entries of a `reportseff` output.

    Args:
        output: Standard output of `reportseff`.

    Returns:
        list: One dict per job or array task with 'jobid', 'state', 'elapsed', 'time_eff', 'cpu_eff' and
            'mem_eff'. A task of an array which never started has a JobID such as '1234_[9]'.
    """
    entries = []
    for line in output.splitlines():
        contents = line.split()
        if len(contents) >= 6 and contents[0] != "JobID":
            entries.append(dict(zip(REPORTSEFF_KEYS, contents)))
    return entries


def reportseff_entries(
    jobids: List[str],
) -> dict:
    """Query the efficiency reports of jobs.

    Args:
        jobids: JobIDs of the jobs.

    Returns:
        dict: The entries of each JobID, see `parse_reportseff()`. The list is empty if `reportseff`
            does not list the job.
    """
    reports = {jobid: [] for jobid in jobids}
    for entry in parse_reportseff(run_reportseff(list(reports))):
        # '1234', '1234_0', '1234_[9]' and '1234.batch' belong to the job '1234'.
        base = re.split(r"[_.]", entry["jobid"], maxsplit=1)[0]
        if base in reports:
            reports[base].append(entry)
    return reports


def reportseff_from_jobid(
    log_file: str,
    metadict: dict,
    jobid: int = None,
) -> None:
    """Add the efficiency report of a job to the metadata.

    If `reportseff` does not list the job, the metadata keeps only the entries of its old report which
    belong to the JobID.

    Args:
        log_file: Log file with one JobID per line. Its last JobID is used if `jobid` is None.
        metadict: Metadata of the job.
        jobid: JobID of the job, or None.
    """
    if jobid is None:
        jobids = jobids_from_log(log_file)
        if len(jobids) == 0:
            raise FileNotFoundError(f"Provide either a JobID or a log file containing a JobID. "
                                    f"No JobID was found in {log_file}.")
        jobid = jobids[-1]
    else:
        jobid = str(jobid)

    metadict["jobid"] = jobid

    try:
        entries = reportseff_entries([jobid])[jobid]
    except ValueError as exc:
        # A finished job must still be archived, so the report is left out instead.
        print(f"Warning: the efficiency report is not recorded: {exc}. Make reportseff available, then run "
              f"scripts/update_metadata.py <archive_dir>.")
        return

    if not entries:
        # The report of another JobID, e.g. of a replaced JobID, must not stay under this JobID.
        kept = metadict.get("reportseff", [])
        kept = kept if isinstance(kept, list) else [kept]
        entries = [entry for entry in kept if matches_jobid(entry.get("jobid", ""), jobid)]
    metadict["reportseff"] = entries
