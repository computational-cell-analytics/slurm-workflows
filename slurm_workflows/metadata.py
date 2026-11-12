#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Access to the files of an archive folder, and creation of its `metadata.json`.
"""
import glob
import json
import os
import shutil
from typing import List, Optional

from slurm_workflows.repositories import add_repository_status
from slurm_workflows.settings import USER_SETTINGS, load_settings
from slurm_workflows.slurm import reportseff_from_jobid, add_sbatch_parameters

METADATA_FILE = "metadata.json"
SBATCH_FILE = "sbatch.sbatch"
LOG_FILE = "log.txt"
# Subfolder of an archive folder with one folder `<jobid>/` per replaced JobID.
PREVIOUS_DIR = "previous"


def read_metadata(
    metadata_file: str,
) -> dict:
    """Read a metadata file of an archive folder.

    Args:
        metadata_file: Path of the metadata file.

    Returns:
        dict: Content of the metadata file.
    """
    with open(metadata_file, "r") as myfile:
        try:
            return json.load(myfile)
        except ValueError as exc:
            raise ValueError(f"The metadata file {metadata_file} is not valid JSON: {exc}") from exc


def write_metadata(
    metadict: dict,
    metadata_file: str,
) -> None:
    """Write a metadata file of an archive folder.

    The key order and the tab indentation of the archived files are kept. The file is written to a
    temporary file first and then renamed, so an interrupted write cannot leave an empty or partial
    file.

    Args:
        metadict: Metadata of the job.
        metadata_file: Path of the metadata file.
    """
    temporary = f"{metadata_file}.tmp"
    with open(temporary, "w") as myfile:
        json.dump(metadict, myfile, sort_keys=False, indent="\t", separators=(",", ": "))
    if os.path.isfile(metadata_file):
        shutil.copymode(metadata_file, temporary)
    os.replace(temporary, metadata_file)


def as_list(
    value,
) -> list:
    """Return a metadata entry which can hold a single entry or a list of entries as a list.

    Args:
        value: Value of a metadata entry.

    Returns:
        list: The value itself if it is a list, otherwise a list with the value as its only entry.
    """
    return value if isinstance(value, list) else [value]


def archive_folders(
    input_dir: str,
    pattern: Optional[str] = None,
) -> List[str]:
    """List the archive folders in a directory.

    Args:
        input_dir: Directory of the archive folders.
        pattern: Part of the folder name, which can hold wildcards, or None for all folders.

    Returns:
        list: Paths of the archive folders.
    """
    if pattern is None:
        return [f.path for f in os.scandir(input_dir) if f.is_dir()]

    subfolders = sorted(glob.glob(os.path.join(input_dir, f"*{pattern}*"), recursive=False))
    if len(subfolders) == 0:
        raise ValueError(f"No subfolders match pattern {pattern}.")
    return subfolders


def job_folders(
    folder: str,
) -> List[str]:
    """List the folders of the jobs of an archive folder.

    Args:
        folder: Archive folder.

    Returns:
        list: The archive folder, then its folders `previous/<jobid>/` of the replaced JobIDs.
    """
    previous_dir = os.path.join(folder, PREVIOUS_DIR)
    if not os.path.isdir(previous_dir):
        return [folder]
    return [folder] + sorted(entry.path for entry in os.scandir(previous_dir) if entry.is_dir())


def archived_jobs(
    folders: List[str],
    include_previous: bool = True,
) -> List[tuple]:
    """Read the metadata of the jobs of archive folders.

    An unreadable `metadata.json` is reported and skipped, so that one broken file does not stop a
    command over the whole archive.

    Args:
        folders: Archive folders.
        include_previous: Also read the replaced JobIDs in `previous/<jobid>/`.

    Returns:
        list: One tuple per job: the archive folder, the folder of the job (the archive folder or
            `previous/<jobid>/`), and the metadata.
    """
    jobs = []
    for folder in folders:
        for job_folder in (job_folders(folder) if include_previous else [folder]):
            metadata = os.path.join(job_folder, METADATA_FILE)
            if not os.path.isfile(metadata):
                continue
            try:
                jobs.append((folder, job_folder, read_metadata(metadata)))
            except ValueError as exc:
                print(f"Warning: {exc} The job is skipped.")
    return jobs


def init_metadict(
    input_dir: str,
) -> dict:
    """Create the metadata of a job from the name of its archive folder.

    Args:
        input_dir: Archive folder, named `<date>_<suffix>` with the date as YYYY-MM-DD.

    Returns:
        dict: The keys 'date' and 'task'. 'task' holds the suffix.
    """
    folder_name = os.path.basename(os.path.abspath(input_dir))
    contents = folder_name.split("_")

    if len(contents) < 2:
        raise ValueError(f"Check correct format of input directory {folder_name}: 'yyyy-mm-dd_suffix'.")

    return {"date": contents[0], "task": "_".join(contents[1:])}


def add_user(
    metadict: dict,
    settings_file: str = None,
) -> None:
    """Add the identifiers of the person who ran the job to the metadata.

    A job which finished must still be archived, so an unusable settings file is reported and
    skipped instead of failing the archiving step.

    Args:
        metadict: Metadata of the job.
        settings_file: Settings file of the project, or None.
    """
    if settings_file is None:
        print("Warning: the HPC user is not recorded. No settings file is given.")
        return

    try:
        settings = load_settings(settings_file)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Warning: the HPC user is not recorded. {exc}")
        return

    for key in USER_SETTINGS:
        if settings[key]:
            metadict[key] = settings[key]


def write_job_metadata(
    input_dir: str,
    output_file: str = None,
    jobid: str = None,
    repositories: dict = None,
    overwrite: bool = False,
    settings_file: str = None,
    metadata: dict = None,
):
    """Create or update the `metadata.json` of an archive folder.

    Args:
        input_dir: Archive folder.
        output_file: Metadata file. Default: `<input_dir>/metadata.json`.
        jobid: JobID, or None for the last JobID of `log.txt`.
        repositories: Names and paths of the git repositories to track, or None.
        overwrite: Replace an existing `metadata.json`.
        settings_file: Settings file of the project, which names the HPC user.
        metadata: Entries to add, e.g. the project of the job, or None.
    """
    if output_file is None:
        output_file = os.path.join(input_dir, METADATA_FILE)
    else:
        output_file = os.path.abspath(output_file)

    sbatch_file = os.path.join(input_dir, SBATCH_FILE)
    log_file = os.path.join(input_dir, LOG_FILE)

    if os.path.isfile(output_file) and not overwrite:
        metadict = read_metadata(output_file)
    else:
        metadict = init_metadict(input_dir)

    add_user(metadict, settings_file)
    add_sbatch_parameters(metadict, sbatch_file)
    reportseff_from_jobid(log_file, metadict=metadict, jobid=jobid)
    # A resubmission can run on a newer commit, so an existing file gets the new hashes too.
    # Without repositories, e.g. of a missing repository file, the recorded hashes stay.
    if repositories:
        add_repository_status(metadict, repositories)
    if metadata is not None:
        metadict.update(metadata)

    write_metadata(metadict, output_file)
