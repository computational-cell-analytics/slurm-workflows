#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Handling of the git repositories which are tracked with a job.
"""
import os
import subprocess


def read_repository_file(
    repository_file: str,
) -> dict:
    """Read a repository file.

    Each line holds a repository name and a path, separated by a tab. The path can contain spaces.

    Args:
        repository_file: Path of the repository file.

    Returns:
        dict: Dictionary mapping repository names to their local paths.
    """
    if not os.path.isfile(repository_file):
        raise FileNotFoundError(f"Repository file {repository_file} not found.")

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
    repo_list = []

    for repo_name, repo_path in repositories.items():
        if not os.path.isdir(repo_path):
            print("Repository path " + repo_path + " could not be resolved.")
            continue

        repo_version = subprocess.run(["git", "rev-parse", "HEAD"],
                                      cwd=repo_path, stdout=subprocess.PIPE).stdout.decode("utf-8")
        git_status_out = subprocess.run(["git", "status", "--porcelain"],
                                        cwd=repo_path, stdout=subprocess.PIPE).stdout.decode("utf-8")
        git_status = "clean" if len(git_status_out) == 0 else "dirty"

        repo_list.append({"repo_name": repo_name,
                          "repo_version": repo_version.strip(),
                          "status": git_status})

    metadict["Repositories"] = repo_list
