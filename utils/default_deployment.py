#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Deployment of a project without its own module `utils/<project>_deployment.py`. The placeholders
are filled from the settings and the job parameters only.
"""
import os

from utils.pipelines import STEPS_KEY, TEMPLATE_SUFFIX, project_of_template


def add_arguments(
    parser,
) -> None:
    """Add no options, because the default project has none."""


def select_steps(
    definition: dict,
    settings: dict,
    args,
) -> list:
    """Return all steps of the pipeline definition."""
    return definition[STEPS_KEY]


def prepare_step(
    settings: dict,
    replacements: dict,
    input_file: str,
    args,
    definition: dict = None,
) -> tuple:
    """Keep the replacements, and name the sbatch script after the template."""
    return replacements, os.path.basename(input_file)[:-len(TEMPLATE_SUFFIX)]


def check_steps(
    input_files: list,
    output_files: list,
) -> list:
    """Print a notice that the default deployment filled the templates, and return no warning.

    A warning would stop `--deploy` without `--force`, so the notice is printed instead.
    """
    print(f"Note: project '{project_of_template(input_files[0])}' has no deployment module. "
          "The placeholders were filled from the settings and the parameters only.")
    return []
