#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Submission of an sbatch script and archiving of the files of its job. The files of a job follow the
format `<date>_<middle>_<suffix>.<ext>`, with a middle part of one word, such as `sbatch` or `log`.
Its archive folder is `<archive_dir>/<date>_<suffix>/`, which holds each file as `<middle>.<ext>`.
"""
import functools
import glob
import os
import re
import shutil
import subprocess

from slurm_workflows.metadata import LOG_FILE, METADATA_FILE, PREVIOUS_DIR, SBATCH_FILE, read_metadata
from slurm_workflows.metadata import write_job_metadata
from slurm_workflows.slurm import jobids_from_log

# The environment of a job, e.g. 'micromamba activate cochlea-net' or 'source activate dl-gpu'.
ACTIVATE_PATTERN = re.compile(r"^\s*(?:micromamba|mamba|conda|source)\s+activate\s+(\S+)")

ENVIRONMENT_FILE = "environment.yml"


def job_name_parts(
    sbatch_file: str,
) -> tuple:
    """Return the date and the suffix of an sbatch script `<date>_sbatch_<suffix>.<ext>`.

    Args:
        sbatch_file: Path of the sbatch script.

    Returns:
        tuple of str: the date and the suffix.
    """
    parts = os.path.splitext(os.path.basename(sbatch_file))[0].split("_")
    if len(parts) < 3:
        raise ValueError(f"The sbatch script {sbatch_file} does not follow the format "
                         "<date>_sbatch_<suffix>.sbatch.")
    return parts[0], "_".join(parts[2:])


def submit(
    sbatch_file: str,
    dependency: str = None,
) -> str:
    """Submit an sbatch script and add its JobID to the log file `<date>_log_<suffix>.txt` beside it.

    Args:
        sbatch_file: Path of the sbatch script.
        dependency: JobID which has to complete successfully before this job starts, or None.

    Returns:
        str: JobID of the submitted job.
    """
    sbatch_file = os.path.realpath(sbatch_file)
    date, suffix = job_name_parts(sbatch_file)

    cmd = ["sbatch", "--parsable"]
    if dependency is not None:
        # Give up instead of waiting forever if the dependency fails.
        cmd += [f"--dependency=afterok:{dependency}", "--kill-on-invalid-dep=yes"]
    cmd.append(sbatch_file)

    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    except OSError as exc:
        raise ValueError(f"Submission of {sbatch_file} failed. sbatch could not be run: {exc}") from exc
    if result.returncode != 0:
        raise ValueError(f"Submission of {sbatch_file} failed: {result.stderr.strip()}")

    # '--parsable' prints the bare JobID, optionally followed by ';<cluster>'.
    jobid = result.stdout.strip().split(";")[0]
    if not jobid:
        raise ValueError(f"No JobID was returned for {sbatch_file}.")

    log_file = os.path.join(os.path.dirname(sbatch_file), f"{date}_log_{suffix}.txt")
    separator = ""
    if os.path.isfile(log_file):
        with open(log_file, "r") as myfile:
            content = myfile.read()
        if content and not content.endswith("\n"):
            separator = "\n"
    with open(log_file, "a") as myfile:
        myfile.write(f"{separator}{jobid}\n")

    return jobid


def move_replaced_job(
    folder: str,
    new_jobids: list,
) -> None:
    """Move the files of an archived JobID to `previous/<jobid>/` if a new JobID replaces it.

    A copy followed by a move archives the same JobID twice, so nothing moves if the archived JobID
    is the last new JobID. `log.txt` stays, because it lists every JobID of the job.

    Args:
        folder: Archive folder of the job.
        new_jobids: JobIDs of the log file which is archived next, oldest first.
    """
    metadata_file = os.path.join(folder, METADATA_FILE)
    if not new_jobids or not os.path.isfile(metadata_file):
        return

    jobid = read_metadata(metadata_file).get("jobid")
    if not jobid or jobid == new_jobids[-1]:
        return

    target = os.path.join(folder, PREVIOUS_DIR, jobid)
    if os.path.exists(target):
        raise FileExistsError(f"The files of the replaced JobID {jobid} cannot be moved, because {target} exists.")
    os.makedirs(target)

    for name in sorted(os.listdir(folder)):
        if name not in (LOG_FILE, PREVIOUS_DIR):
            shutil.move(os.path.join(folder, name), os.path.join(target, name))

    print(f"Moved the files of the replaced JobID {jobid} to {target}.")


def archive_job(
    input_dir: str,
    archive_dir: str,
    date: str,
    suffix: str,
    move: bool = False,
) -> str:
    """Copy or move the files of a job into its archive folder.

    A log file is merged into an archived one, because a moved log file holds only the new JobIDs.
    The files of a replaced JobID move to `previous/<jobid>/` first, see `move_replaced_job()`.

    Args:
        input_dir: Directory of the job files.
        archive_dir: Directory of the archive folders.
        date: Date of the job, as YYYY-MM-DD.
        suffix: Suffix of the job.
        move: Move the files instead of copying them.

    Returns:
        str: Path of the archive folder.
    """
    if not os.path.isdir(archive_dir):
        raise FileNotFoundError(f"Archive directory {archive_dir} does not exist.")
    if not os.path.isdir(input_dir):
        raise FileNotFoundError(f"Input directory {input_dir} does not exist.")

    pattern = os.path.join(glob.escape(input_dir), f"{glob.escape(date)}_*_{glob.escape(suffix)}.*")
    # The pattern also matches the files of a job whose suffix ends with '_<suffix>', such as 'y_x'
    # for 'x'. Their middle part then holds an underscore.
    job_files = []
    for path in sorted(glob.glob(pattern)):
        name = os.path.basename(path)
        middle = name[len(date) + 1:name.rindex(f"_{suffix}")]
        if "_" not in middle:
            job_files.append((path, f"{middle}.{name.rsplit('.', 1)[1]}"))

    if not job_files:
        raise FileNotFoundError(f"No files of the job {date}_{suffix} in {input_dir}.")

    folder = os.path.join(archive_dir, f"{date}_{suffix}")
    os.makedirs(folder, exist_ok=True)

    new_log = next((path for path, target_name in job_files if target_name.startswith("log.")), None)
    move_replaced_job(folder, jobids_from_log(new_log) if new_log is not None else [])

    for path, target_name in job_files:
        target = os.path.join(folder, target_name)
        if target_name.startswith("log.") and os.path.isfile(target):
            # JobIDs are unique, so a JobID which is already archived is dropped.
            lines = []
            for log_file in (target, path):
                with open(log_file, "r") as myfile:
                    lines += myfile.read().splitlines()
            with open(target, "w") as myfile:
                myfile.writelines(f"{line}\n" for line in dict.fromkeys(lines))
            if move:
                os.remove(path)
        elif move:
            shutil.move(path, target)
        else:
            shutil.copy(path, target)

    print(f"{'Moved' if move else 'Copied'} {len(job_files)} file(s) of the job to {folder}.")

    return folder


@functools.lru_cache(maxsize=None)
def environment_export(
    environment: str,
) -> str:
    """Export a micromamba environment. The result is cached, because every step of a chain uses it.

    Args:
        environment: Name or path of the environment.

    Returns:
        str: The YAML export, including the packages installed with pip.
    """
    executable = os.environ.get("MAMBA_EXE") or shutil.which("micromamba")
    if executable is None:
        raise ValueError("micromamba was not found.")

    option = "-p" if os.sep in environment else "-n"
    try:
        result = subprocess.run([executable, "env", "export", option, environment],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    except OSError as exc:
        raise ValueError(f"micromamba could not be run: {exc}") from exc
    if result.returncode != 0:
        raise ValueError(result.stderr.strip())

    return result.stdout


def export_environment(
    sbatch_file: str,
    folder: str,
) -> None:
    """Write the export of the environment which a job activates to `environment.yml` in its archive folder.

    A failed export gives a warning, because the job is already submitted.

    Args:
        sbatch_file: Path of the sbatch script.
        folder: Archive folder of the job.
    """
    # ponytail: only the first activated environment is archived. Export one file per environment if
    # a job activates more than one.
    with open(sbatch_file, "rt", encoding="utf8", errors="ignore") as myfile:
        matches = (ACTIVATE_PATTERN.match(line) for line in myfile)
        environment = next((match.group(1) for match in matches if match), None)

    if environment is None:
        return

    try:
        export = environment_export(environment)
    except ValueError as exc:
        print(f"Warning: the environment {environment} is not archived. {exc}")
        return

    with open(os.path.join(folder, ENVIRONMENT_FILE), "w") as myfile:
        myfile.write(export)


def archive_submitted_job(
    sbatch_file: str,
    jobid: str,
    archive_dir: str,
    move: bool = False,
    repositories: dict = None,
    settings_file: str = None,
    metadata: dict = None,
) -> str:
    """Archive the files of a submitted job, its environment and its `metadata.json`.

    Args:
        sbatch_file: Path of the sbatch script.
        jobid: JobID of the job.
        archive_dir: Directory of the archive folders.
        move: Move the job files instead of copying them.
        repositories: Names and paths of the git repositories to track, or None.
        settings_file: Settings file of the project, which names the HPC user, or None.
        metadata: Entries to add to the metadata, or None.

    Returns:
        str: Path of the archive folder.
    """
    sbatch_file = os.path.realpath(sbatch_file)
    date, suffix = job_name_parts(sbatch_file)

    folder = archive_job(os.path.dirname(sbatch_file), os.path.realpath(archive_dir), date, suffix, move)
    export_environment(os.path.join(folder, SBATCH_FILE), folder)
    write_job_metadata(folder, jobid=jobid, repositories=repositories, settings_file=settings_file,
                       metadata=metadata)

    return folder
