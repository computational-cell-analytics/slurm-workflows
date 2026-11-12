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
from datetime import datetime
from typing import List, Optional

# The columns of a `reportseff` line, in order.
REPORTSEFF_KEYS = ("jobid", "state", "elapsed", "time_eff", "cpu_eff", "mem_eff")

# The number of JobIDs of one `reportseff` call, which keeps the command line short.
REPORTSEFF_CHUNK = 100

# The line of slurmstepd when Slurm cancels a job or a step, e.g.
# 'slurmstepd: error: *** JOB 123 ON ggpu202 CANCELLED AT 2026-11-03T12:00:00 DUE TO TIME LIMIT ***'.
# Without a reason, the job was cancelled with scancel.
CANCEL_PATTERN = re.compile(r"\*\*\* (?:JOB|STEP) \S+ ON \S+ CANCELLED AT \S+(?: DUE TO (.+?))? \*\*\*")
CANCEL_STATES = {"TIME LIMIT": "TIMEOUT", "PREEMPTION": "PREEMPTED", "NODE FAILURE": "NODE_FAIL"}

# 'slurmstepd: error: Detected 1 oom_kill event in StepId=123.batch.'
OOM_PATTERN = re.compile(r"oom[-_]kill", re.IGNORECASE)

# The line of the EXIT trap of the templates, see doc/new_template.md.
EXIT_CODE_PATTERN = re.compile(r"^Exit code of the job: (\d+)$")

# A last line of the job output which shows that the job script failed: the guard of a template, an
# error message, an error of bash, or a Python exception after a traceback.
ERROR_PREFIXES = ("Missing input of the job:", "ERROR:")
BASH_ERROR_PATTERN = re.compile(r"slurm_script: line \d+: ")
EXCEPTION_PATTERN = re.compile(r"^[A-Za-z_][\w.]*(?:Error|Exception|Interrupt)\b")
TRACEBACK_LINE = "Traceback (most recent call last):"

# The longest `error` line of a `slurm_output` entry.
MAX_ERROR_LENGTH = 300

# The format of the times of the 'Job Information' section.
TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"

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


def elapsed_seconds(
    job_info: dict,
) -> Optional[int]:
    """Return the run time of a job from the 'started' and 'ended' times of its slurm output.

    Args:
        job_info: Entry of `slurm_output`.

    Returns:
        int: The seconds from the start to the end, or None if a time is missing or has another format.
    """
    try:
        started = datetime.strptime(job_info["started"], TIME_FORMAT)
        ended = datetime.strptime(job_info["ended"], TIME_FORMAT)
    except (KeyError, ValueError):
        return None
    return int((ended - started).total_seconds())


def output_state(
    lines: List[str],
    job_info: dict,
) -> tuple:
    """Derive the state of a job from the lines of its output before the 'Job Information' section.

    The output holds no exit code unless the template prints it, so a state is given only if a marker
    shows it. The first rule that matches gives the state:
    1. a slurmstepd line which cancels the job: TIMEOUT, PREEMPTED, NODE_FAIL or CANCELLED
    2. an oom_kill event: OUT_OF_MEMORY
    3. an elapsed time of at least the time limit: TIMEOUT
    4. the exit code 0: COMPLETED
    5. a last line of the guard, of an 'ERROR:' message, of a bash error, or of a Python exception
       after a traceback: FAILED, with this line
    6. another exit code: FAILED

    Args:
        lines: Lines of the job output, without line ends.
        job_info: Entry of `slurm_output`, with 'exit_code', 'elapsed_sec' and 'limit_min' if known.

    Returns:
        tuple of:
            str - the state, or None
            str - the line which shows the state, or None
    """
    for line in lines:
        match = CANCEL_PATTERN.search(line)
        if match:
            return CANCEL_STATES.get(match.group(1), "CANCELLED"), line
    for line in lines:
        if OOM_PATTERN.search(line):
            return "OUT_OF_MEMORY", line

    # A job which Slurm kills before its EXIT trap runs prints no exit code.
    if "elapsed_sec" in job_info and "limit_min" in job_info and \
            job_info["elapsed_sec"] >= job_info["limit_min"] * 60:
        return "TIMEOUT", None
    exit_code = job_info.get("exit_code")
    if exit_code == 0:
        return "COMPLETED", None

    output = [line for line in lines if line.strip() and not EXIT_CODE_PATTERN.match(line.strip())]
    last = output[-1].strip() if output else ""
    if last.startswith(ERROR_PREFIXES) or BASH_ERROR_PATTERN.search(last) or \
            (EXCEPTION_PATTERN.match(last) and any(line.strip() == TRACEBACK_LINE for line in output)):
        return "FAILED", last
    if exit_code is not None:
        return "FAILED", None
    return None, None


def slurm_output_to_dict(
    slurm_file: str,
) -> dict:
    """Read a slurm output file: the 'Job Information' section, the exit code, and the state.

    Args:
        slurm_file: Path of a file `slurm-<jobid>.out`, or `slurm-<jobid>_<array_index>.out` of an array task.

    Returns:
        dict: The keys 'file', 'submitted', 'started', 'ended', 'elapsed_min', 'limit_min', 'cpus',
            'nodes' and 'core_hours', and 'array_index' for an array task. 'elapsed_sec' if both times
            are known, 'exit_code' if the job printed it, and 'state' and 'error' if the output shows
            them, see `output_state()`.
    """
    filename = os.path.basename(slurm_file)
    job_info = {"file": filename}

    array_match = re.match(r'slurm-\d+_(\d+)\.out', filename)
    if array_match:
        job_info["array_index"] = int(array_match.group(1))

    # The output of a job can be large, so only the lines which `output_state()` reads are kept: the
    # first cancel line, the first oom_kill line, a traceback line, and the last line.
    markers = {}
    last = None
    exit_code = None
    in_job_info = False
    with open(slurm_file, 'rt', encoding='utf-8', errors='ignore') as f:
        for line in f:
            if 'Job Information' in line:
                in_job_info = True
                continue
            if not in_job_info:
                stripped = line.strip()
                exit_match = EXIT_CODE_PATTERN.match(stripped)
                if exit_match:
                    exit_code = int(exit_match.group(1))
                elif stripped:
                    last = line.rstrip("\n")
                    if "cancel" not in markers and CANCEL_PATTERN.search(line):
                        markers["cancel"] = last
                    elif "oom" not in markers and OOM_PATTERN.search(line):
                        markers["oom"] = last
                    elif stripped == TRACEBACK_LINE:
                        markers["traceback"] = last
                continue
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

    elapsed = elapsed_seconds(job_info)
    if elapsed is not None:
        job_info["elapsed_sec"] = elapsed
    if exit_code is not None:
        job_info["exit_code"] = exit_code

    state, error = output_state(list(markers.values()) + ([last] if last is not None else []), job_info)
    if state is not None:
        job_info["state"] = state
    if error is not None:
        job_info["error"] = error.strip().encode("ascii", "replace").decode("ascii")[:MAX_ERROR_LENGTH]

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
