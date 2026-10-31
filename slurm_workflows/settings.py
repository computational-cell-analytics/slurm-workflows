#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Access to the settings of a project: the paths and names which are specific to a cluster account.
"""
import json
import os

from slurm_workflows import REPOSITORY_DIR

SETTINGS_DIR = os.path.join(REPOSITORY_DIR, "project_settings")

# Identifiers of the person who ran a job, recorded in the metadata of the archive.
# Both keys must be present, and both may be blank.
USER_SETTINGS = ("academic_id", "hpc_user")

REQUIRED_SETTINGS = ("repositories",) + USER_SETTINGS


def settings_file(
    project: str,
) -> str:
    """Return the default settings file of a project.

    Args:
        project: Name of the project.

    Returns:
        str: Path of the settings file.
    """
    return os.path.join(SETTINGS_DIR, f"{project}.json")


def blueprint_settings_file(
    project: str,
) -> str:
    """Return the blueprint settings file of a project, which is tracked by git.

    Args:
        project: Name of the project.

    Returns:
        str: Path of the blueprint settings file.
    """
    return os.path.join(SETTINGS_DIR, f"{project}.blueprint.json")


def load_settings(
    settings_file: str,
) -> dict:
    """Read the JSON file with the paths and names which are specific to a cluster account.

    Args:
        settings_file: Path of the settings file.

    Returns:
        dict: Content of the settings file.
    """
    if not os.path.isfile(settings_file):
        raise FileNotFoundError(f"Settings file {settings_file} not found.")

    with open(settings_file, "r") as myfile:
        try:
            settings = json.load(myfile)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Settings file {settings_file} is not valid JSON: {exc}") from exc

    missing = [key for key in REQUIRED_SETTINGS if key not in settings]
    if missing:
        raise ValueError(f"Settings file {settings_file} is missing the keys: {missing}.")

    return settings


def settings_to_placeholders(
    settings: dict,
) -> dict:
    """Flatten the settings into template placeholders.

    Every scalar entry becomes a placeholder under its own key. Each repository of the 'repositories'
    entry becomes the placeholder `repo:<name>` with its path. All other nested entries, such as
    'models', give no placeholder.

    Args:
        settings: Output of `load_settings()`.

    Returns:
        dict: Dictionary mapping placeholder names to their values.
    """
    placeholder_values = {key: str(value) for key, value in settings.items()
                          if isinstance(value, (str, int, float))}

    for name, path in settings.get("repositories", {}).items():
        placeholder_values[f"repo:{name}"] = str(path)

    return placeholder_values
