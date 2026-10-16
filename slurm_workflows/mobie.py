#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Access to the `project.json` of a MoBIE project. The S3 file lists the datasets of every local
MoBIE project, so a local `project.json` must not replace it.
"""
import json
import os
import subprocess

PROJECT_FILE = "project.json"

# A dataset is a top-level folder of the project which holds this file.
DATASET_FILE = "dataset.json"


def tmp_project_file() -> str:
    # Two jobs which run at the same time must not share the temporary copy.
    jobid = os.environ.get("SLURM_JOB_ID")
    return f"tmp_{jobid}_project.json" if jobid else "tmp_project.json"


def rclone(
    args: list,
) -> str:
    """Run an rclone command.

    Args:
        args: Arguments of rclone, e.g. ['cat', 'remote:bucket/project.json'].

    Returns:
        str: The standard output.
    """
    result = subprocess.run(["rclone"] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=True)
    if result.returncode != 0:
        raise ValueError(f"rclone {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def read_project(
    path: str,
) -> dict:
    with open(path, "r") as myfile:
        return json.load(myfile)


def write_project(
    path: str,
    project: dict,
) -> None:
    # Same format as 'write_metadata' of mobie-utils-python, so that a file changes only by its content.
    with open(path, "w") as myfile:
        json.dump(project, myfile, indent=2, sort_keys=True)


def set_datasets(
    project: dict,
    datasets: list,
) -> dict:
    """Return a copy of the project metadata with a new dataset list.

    Args:
        project: Content of a `project.json`.
        datasets: The new dataset names.

    Returns:
        dict: The project metadata. Only the dataset list differs.
    """
    default = project.get("defaultDataset")
    if default is not None and default not in datasets:
        raise ValueError(f"The default dataset {default} is not in the new dataset list. "
                         f"Change 'defaultDataset' of {PROJECT_FILE} first.")
    return dict(project, datasets=datasets)


def describe_change(
    before: list,
    after: list,
) -> str:
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    return f"added: {', '.join(added) or 'none'}; removed: {', '.join(removed) or 'none'}"


def local_datasets(
    mobie_project: str,
) -> list:
    """Return the datasets of a local MoBIE project.

    Args:
        mobie_project: Root of the local MoBIE project.

    Returns:
        list: Sorted names of the folders which hold a `dataset.json`.
    """
    return sorted(name for name in os.listdir(mobie_project)
                  if os.path.isfile(os.path.join(mobie_project, name, DATASET_FILE)))


def s3_datasets(
    s3_remote: str,
) -> list:
    """Return the datasets of the MoBIE project in the S3 bucket.

    Args:
        s3_remote: rclone remote and bucket, in the form 'remote:bucket'.

    Returns:
        list: Sorted names of the folders which hold a `dataset.json`.
    """
    # Depth 2 reads only the top level of each dataset folder, not the chunks of the OME-Zarr data.
    output = rclone(["lsf", "--files-only", "--max-depth", "2", "--include", f"/*/{DATASET_FILE}", s3_remote])
    return sorted(line.split("/")[0] for line in output.splitlines() if line)


def update_s3_project(
    mobie_project: str,
    s3_remote: str,
    new_datasets,
) -> None:
    """Change the dataset list of the `project.json` in the S3 bucket.

    The S3 file is copied to `<mobie_project>/tmp_<JobID>_project.json`, edited and copied back.
    Outside of a Slurm job, the copy is `tmp_project.json`. The local copy is removed afterwards.

    Args:
        mobie_project: Root of the local MoBIE project.
        s3_remote: rclone remote and bucket, in the form 'remote:bucket'.
        new_datasets: Function which returns the new dataset list for the current one.
    """
    local_file = os.path.join(mobie_project, tmp_project_file())
    remote_file = f"{s3_remote}/{PROJECT_FILE}"

    try:
        rclone(["copyto", remote_file, local_file])
        project = read_project(local_file)

        datasets = new_datasets(project["datasets"])
        if datasets == project["datasets"]:
            print(f"The {PROJECT_FILE} of the S3 bucket is up to date.")
            return

        write_project(local_file, set_datasets(project, datasets))

        # The S3 file can change during the job, e.g. by the transfer of another cochlea. An upload
        # would drop that change. set_datasets() keeps all other entries, so the upload then
        # differs from the S3 file by the dataset list alone.
        # ponytail: S3 has no lock. A write by another job between this check and the upload is
        # still lost. The window is about one second.
        if json.loads(rclone(["cat", remote_file])) != project:
            raise ValueError(f"The {PROJECT_FILE} of the S3 bucket changed during the job. Nothing was uploaded.")

        rclone(["copyto", local_file, remote_file])
        print(f"Updated the {PROJECT_FILE} of the S3 bucket, {describe_change(project['datasets'], datasets)}.")

    finally:
        if os.path.exists(local_file):
            os.remove(local_file)


def add_s3_dataset(
    mobie_project: str,
    s3_remote: str,
    dataset: str,
) -> None:
    """Add a dataset to the `project.json` of the S3 bucket, if the file does not list it yet.

    Args:
        mobie_project: Root of the local MoBIE project.
        s3_remote: rclone remote and bucket, in the form 'remote:bucket'.
        dataset: Name of the dataset to add.
    """
    update_s3_project(mobie_project, s3_remote,
                      lambda datasets: datasets if dataset in datasets else sorted(datasets + [dataset]))


def generate_s3_project(
    mobie_project: str,
    s3_remote: str,
) -> None:
    """Set the dataset list of the `project.json` in the S3 bucket to the dataset folders of the bucket.

    Args:
        mobie_project: Root of the local MoBIE project.
        s3_remote: rclone remote and bucket, in the form 'remote:bucket'.
    """
    update_s3_project(mobie_project, s3_remote, lambda datasets: s3_datasets(s3_remote))


def generate_local_project(
    mobie_project: str,
) -> None:
    """Set the dataset list of the local `project.json` to the dataset folders of the local project.

    Args:
        mobie_project: Root of the local MoBIE project.
    """
    path = os.path.join(mobie_project, PROJECT_FILE)
    project = read_project(path)

    datasets = local_datasets(mobie_project)
    if datasets == project["datasets"]:
        print(f"{path} is up to date.")
        return

    write_project(path, set_datasets(project, datasets))
    print(f"Updated {path}, {describe_change(project['datasets'], datasets)}.")
