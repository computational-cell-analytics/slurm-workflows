#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Parsing of sbatch scripts, of slurm output files and of the efficiency report of a job.
"""
import glob
import os
import re
import subprocess

from utils.metadata import jobids_from_log

SBATCH_PARAMETERS = [
    {"param": ["-A", "--account"],
     "descr": "account"},

    {"param": ["-a", "--array"],
     "descr": "Job array"},

    {"param": ["-c", "--cpus-per-task"],
     "descr": "cpus-per-task"},

    {"param": ["-G", "--gpus"],
     "descr": "gpu"},

    {"param": ["--job-name"],
     "descr": "job-name"},

    {"param": ["--mail-user"],
     "descr": "mail-user"},

    {"param": ["--mem"],
     "descr": "Memory-per-node"},

    {"param": ["-t", "--time"],
     "descr": "runtime"},

    {"param": ["-p", "--partition"],
     "descr": "partition"},
]


def sbatch_parameters_to_dict(
    sbatch_file: str,
    metadict: dict,
) -> None:
    """Add the parameters of an sbatch file to a dictionary containing metadata.

    Args:
        sbatch_file: A job script for slurm containing #SBATCH options.
        metadict: Dictionary containing metadata for a slurm job.
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
    """Find the slurm output files of a single job and of an array job.

    Args:
        slurm_dir: Directory containing slurm output files.
        jobid: JobID of the slurm job.

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
    """Parse the Job Information section from a slurm output file.

    Handles both single-job files (slurm-<job_id>.out) and array-job files
    (slurm-<job_id>_<array_index>.out).

    Args:
        slurm_file: Path to the slurm output file.

    Returns:
        Dictionary with parsed fields: file, array_index (if array job), submitted,
        started, ended, elapsed_min, limit_min, cpus, nodes, core_hours.
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


def reportseff_from_jobid(
    log_file: str,
    metadict: dict,
    jobid: int = None,
) -> None:
    """Add information about the efficiency of the submitted job to a dictionary containing metadata.
    The information is obtained through the command 'reportseff -u <user-id>',
    which is available for seven days after job submission.
    The dictionary is only updated, if the JobID was found within the output of the shell command.

    Args:
        log_file: Text file containing one or multiple lines with JobIDs. Only the last JobID is evaluated.
        metadict: Dictionary containing metadata for slurm job.
        jobid: JobID of slurm job.
    """
    if jobid is None:
        jobids = jobids_from_log(log_file)
        if len(jobids) == 0:
            raise FileNotFoundError(f"Provide either a JobID or a log file containing a JobID. "
                                    f"No JobID was found in {log_file}.")
        jobid = jobids[-1]
    else:
        jobid = str(jobid)
        print(f"Using manually provided JobID {jobid}")

    metadict["jobid"] = jobid
    user_id = subprocess.run(['whoami'], stdout=subprocess.PIPE).stdout.decode('utf-8').strip()

    try:
        result = subprocess.run(['reportseff', '-u', user_id], stdout=subprocess.PIPE).stdout.decode('utf-8')
    except OSError as exc:
        # A finished job must still be archived, so the report is left out instead.
        print(f"Warning: the efficiency report is not recorded. reportseff could not be run: {exc}")
        return

    lines = result.split("\n")
    reports_eff_list = []
    job_id_found = False

    for line in lines:
        contents = line.split()
        if len(contents) >= 6 and matches_jobid(contents[0], jobid):
            job_id_found = True
            reports_eff = {"JobID": contents[0]}
            reports_eff["State"] = contents[1]
            reports_eff["Elapsed"] = contents[2]
            reports_eff["TimeEff"] = contents[3]
            reports_eff["CPUEff"] = contents[4]
            reports_eff["MemEff"] = contents[5]
            reports_eff_list.append(reports_eff)

    # do not overwrite Reportseff if JobID is not found and entry already exists
    if job_id_found:
        metadict["Reportseff"] = reports_eff_list
    elif "Reportseff" not in metadict:
        metadict["Reportseff"] = []
