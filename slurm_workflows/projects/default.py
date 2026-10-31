#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Deployment of a project without its own module `slurm_workflows/projects/<project>/deployment.py`.
The placeholders are filled from the settings and the job parameters only. A project module takes
each function which it does not define from this module.
"""
import os

from slurm_workflows.pipelines import STEPS_KEY
from slurm_workflows.templates import TEMPLATE_SUFFIX, step_template


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


def resolve_template(
    template_dir: str,
    step: str,
    parameters: dict,
) -> str:
    """Return the template of the step."""
    return step_template(template_dir, step)


def prepare_step(
    settings: dict,
    placeholder_values: dict,
    template_file: str,
    args,
    definition: dict = None,
) -> tuple:
    """Keep the placeholder values, and name the sbatch script after the template."""
    return placeholder_values, os.path.basename(template_file)[:-len(TEMPLATE_SUFFIX)]


def check_steps(
    template_files: list,
    sbatch_files: list,
) -> list:
    """Return no warning, because the default deployment knows nothing about the data of a job."""
    return []
