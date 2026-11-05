#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Handling of the git repositories which are tracked with a job.
"""
import os
import shutil
import subprocess

from slurm_workflows import REPOSITORY_DIR


def read_repository_file(
    repository_file: str,
) -> dict:
    """Read a repository file.

    Each line holds a repository name and a path, separated by a tab. The path can contain spaces.
    A missing file gives a warning and an empty dictionary.

    Args:
        repository_file: Path of the repository file.

    Returns:
        dict: Dictionary mapping repository names to their local paths.
    """
    if not os.path.isfile(repository_file):
        print(f"Warning: repository file {repository_file} not found. It is ignored.")
        return {}

    repositories = {}
    with open(repository_file, "rt", encoding="utf8", errors="ignore") as myfile:
        for line in myfile:
            content = line.strip().split(None, 1)
            if len(content) == 0:
                continue
            if len(content) != 2:
                raise ValueError(f"Ensure that the file containing repositories has the correct format. "
                                 f"Check the line '{line.strip()}' of {repository_file}.")
            repositories[content[0]] = content[1]

    return repositories


def job_repositories(
    settings: dict,
    repository_file: str = None,
) -> dict:
    """Return the git repositories which a job tracks.

    The settings entry 'repositories' and the repository file give the repositories of the project.
    A repository of the file overrides one of the same name in the settings. slurm-workflows is always
    the clone of this package.

    Args:
        settings: Settings of the project, or an empty dictionary.
        repository_file: Path of a repository file, or None.

    Returns:
        dict: Dictionary mapping repository names to their local paths.
    """
    repositories = dict(settings.get("repositories", {}))
    if repository_file is not None:
        repositories.update(read_repository_file(repository_file))
    repositories["slurm-workflows"] = REPOSITORY_DIR

    return repositories


def add_repository_status(
    metadict: dict,
    repositories: dict,
) -> None:
    """Add the git hash and the status of each repository to the metadata.

    The status is 'clean' or 'dirty'.

    Args:
        metadict: Metadata of the job.
        repositories: Dictionary mapping repository names to their local paths.
    """
    if shutil.which("git") is None:
        print("Warning: git was not found, so the git hashes of the repositories are not recorded.")
        return

    repo_list = []

    for repo_name, repo_path in repositories.items():
        if not os.path.isdir(repo_path):
            print("Repository path " + repo_path + " could not be resolved.")
            continue

        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_path, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, universal_newlines=True)
        if result.returncode != 0:
            print(f"Warning: the git hash of {repo_name} is not recorded: {result.stderr.strip()}")
            continue
        repo_version = result.stdout
        git_status_out = subprocess.run(["git", "status", "--porcelain"],
                                        cwd=repo_path, stdout=subprocess.PIPE).stdout.decode("utf-8")
        git_status = "clean" if len(git_status_out) == 0 else "dirty"

        repo_list.append({"repo_name": repo_name,
                          "repo_version": repo_version.strip(),
                          "status": git_status})

    metadict["repositories"] = repo_list
