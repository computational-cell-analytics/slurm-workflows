#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Handling of the git repositories which are tracked with a job.
"""
import os
import subprocess


def write_repository_file(
    repositories: dict,
    output_file: str,
) -> None:
    """Write the repositories of the settings in the tab-separated format read by `repository_status_to_dict()`.

    Args:
        repositories: Dictionary mapping repository names to their local paths.
        output_file: Path to the output file.
    """
    with open(output_file, "w") as myfile:
        for name, path in repositories.items():
            myfile.write(f"{name}\t{path}\n")


def repository_status_to_dict(
    repository_file: str,
    metadict: dict,
) -> None:
    """Add information about git repositories to a dictionary containing metadata.

    Each line of the repository file contains <repository_name> and <repository_path>, separated by a tab.
    The hash of the git commit and the status of the repository ('clean' or 'dirty') are tracked.

    Args:
        repository_file: File containing name and file path of git repositories.
        metadict: Dictionary containing metadata.
    """
    repo_list = []

    if os.path.isfile(repository_file):
        with open(repository_file, 'rt', encoding="utf8", errors='ignore') as myfile:
            for line in myfile:
                content = line.strip().split()
                if len(content) == 0:
                    continue
                if len(content) != 2:
                    raise ValueError(f"Ensure that the file containing repositories has the correct format. "
                                     f"Check the line '{line.strip()}' of {repository_file}.")

                repo_name = content[0]
                repo_path = content[1]

                if os.path.isdir(repo_path):
                    repo_version = subprocess.run(["git", "rev-parse", "HEAD"],
                                                  cwd=repo_path, stdout=subprocess.PIPE).stdout.decode('utf-8')
                    git_status_out = subprocess.run(["git", "status", "--porcelain"],
                                                    cwd=repo_path, stdout=subprocess.PIPE).stdout.decode('utf-8')
                    if len(git_status_out) == 0:
                        git_status = "clean"
                    else:
                        git_status = "dirty"

                    repo_list.append({"repo_name": repo_name,
                                      "repo_version": repo_version.strip(),
                                      "status": git_status})
                else:
                    print("Repository path " + repo_path + " could not be resolved.")

    metadict["Repositories"] = repo_list
