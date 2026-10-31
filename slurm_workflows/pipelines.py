#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Access to the pipeline definitions. A pipeline is an ordered list of templates which is submitted as
a chain of Slurm jobs.
"""
import glob
import json
import os

from slurm_workflows import REPOSITORY_DIR
from slurm_workflows.templates import step_template

# Each project has one subfolder of the same name in this directory.
PIPELINE_ROOT = os.path.join(REPOSITORY_DIR, "pipelines")

# Steps of a pipeline, in the order in which they are submitted.
STEPS_KEY = "steps"


def pipeline_names() -> list:
    """List the available pipelines of every project.

    Returns:
        list: Sorted pipeline names in the format '<project>/<pipeline>'.
    """
    return sorted(os.path.relpath(path, PIPELINE_ROOT)[:-len(".json")]
                  for path in glob.glob(os.path.join(PIPELINE_ROOT, "*", "*.json")))


def resolve_pipeline(
    name: str,
) -> tuple:
    """Return the project and the file of a pipeline.

    Args:
        name: Pipeline name in the format '<project>/<pipeline>', or path to a pipeline file.

    Returns:
        tuple of str: the project, and the path of the pipeline file.
    """
    if os.path.isfile(name):
        return os.path.basename(os.path.dirname(os.path.realpath(name))), name

    project, _, pipeline = name.partition("/")
    pipeline_file = os.path.join(PIPELINE_ROOT, project, f"{pipeline}.json")

    if not pipeline or not os.path.isfile(pipeline_file):
        raise FileNotFoundError(f"Pipeline {name} not found. Available pipelines: {pipeline_names()}.")

    return project, pipeline_file


def missing_templates(
    template_dir: str,
    steps: list,
) -> list:
    """Return the steps which have no template.

    Args:
        template_dir: Template folder of the project.
        steps: Names of the steps.

    Returns:
        list: Steps without a template file.
    """
    return [step for step in steps if not os.path.isfile(step_template(template_dir, step))]


def pipeline_template_dir(
    pipeline_file: str,
) -> str:
    """Return the template folder of a pipeline.

    A pipeline `<root>/pipelines/<project>/<name>.json` reads its templates from
    `<root>/templates/<project>/`, so a project outside of this repository can bring its own templates.

    Args:
        pipeline_file: Path of the pipeline file.

    Returns:
        str: Path of the template folder.
    """
    project_dir = os.path.dirname(os.path.realpath(pipeline_file))
    root = os.path.dirname(os.path.dirname(project_dir))
    return os.path.join(root, "templates", os.path.basename(project_dir))


def load_pipeline(
    pipeline_file: str,
) -> dict:
    """Read and validate a pipeline definition.

    Every step is validated before the pipeline is used, so that an unknown step cannot leave the
    earlier steps of the chain submitted. A project can add its own keys to the definition.

    Args:
        pipeline_file: Path of the pipeline file.

    Returns:
        dict: Content of the pipeline file, with the keys 'name' and 'template_dir' added.
    """
    with open(pipeline_file, "r") as myfile:
        try:
            pipeline = json.load(myfile)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Pipeline file {pipeline_file} is not valid JSON: {exc}") from exc

    # The type is checked first. A non-empty string passes the check below and then breaks the
    # template check with a TypeError, which the caller does not turn into a message.
    steps = pipeline.get(STEPS_KEY)
    if steps is not None and not isinstance(steps, list):
        raise ValueError(f"Pipeline file {pipeline_file} needs a list of steps for '{STEPS_KEY}'.")
    if not steps:
        raise ValueError(f"Pipeline file {pipeline_file} has no steps.")

    template_dir = pipeline_template_dir(pipeline_file)
    missing = missing_templates(template_dir, steps)
    if missing:
        raise ValueError(f"Pipeline file {pipeline_file} refers to steps without a template in {template_dir}: "
                         f"{missing}.")

    pipeline["name"] = os.path.basename(pipeline_file)[:-len(".json")]
    pipeline["template_dir"] = template_dir

    return pipeline


def steps_starting_at(
    steps: list,
    start_at: str,
) -> list:
    """Drop the steps before a given step, to resume a pipeline after a failure.

    Args:
        steps: Steps of the pipeline.
        start_at: Name of the step to start at.

    Returns:
        list: The remaining steps.
    """
    if start_at not in steps:
        raise ValueError(f"Step {start_at} is no step of the pipeline. Available steps: {steps}.")

    return steps[steps.index(start_at):]
