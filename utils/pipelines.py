#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Access to the projects, their templates and their pipeline definitions. A pipeline is an ordered
list of templates which is submitted as a chain of Slurm jobs.
"""
import glob
import json
import os

REPOSITORY_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

# Each project has one subfolder of the same name in both directories.
PIPELINE_ROOT = os.path.join(REPOSITORY_DIR, "pipelines")
TEMPLATE_ROOT = os.path.join(REPOSITORY_DIR, "templates")

TEMPLATE_SUFFIX = ".template"

# Steps of a pipeline, in the order in which they are submitted.
STEPS_KEY = "steps"


def projects() -> list:
    """List the names of the projects.

    Returns:
        list: Sorted names of the subfolders of the template directory.
    """
    return sorted(name for name in os.listdir(TEMPLATE_ROOT)
                  if os.path.isdir(os.path.join(TEMPLATE_ROOT, name)))


def project_of_template(
    template_file: str,
) -> str:
    """Return the project of a template, which is the name of its folder.

    Args:
        template_file: Path of the template.

    Returns:
        str: Name of the project.
    """
    return os.path.basename(os.path.dirname(os.path.realpath(template_file)))


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
        return project_of_template(name), name

    project, _, pipeline = name.partition("/")
    pipeline_file = os.path.join(PIPELINE_ROOT, project, f"{pipeline}.json")

    if not pipeline or not os.path.isfile(pipeline_file):
        raise FileNotFoundError(f"Pipeline {name} not found. Available pipelines: {pipeline_names()}.")

    return project, pipeline_file


def step_template(
    project: str,
    step: str,
) -> str:
    """Return the template path of a pipeline step.

    Args:
        project: Name of the project.
        step: Name of the step, which is the template name without the suffix.

    Returns:
        str: Path of the template file.
    """
    return os.path.join(TEMPLATE_ROOT, project, f"{step}{TEMPLATE_SUFFIX}")


def missing_templates(
    project: str,
    steps: list,
) -> list:
    """Return the steps which have no template.

    Args:
        project: Name of the project.
        steps: Names of the steps.

    Returns:
        list: Steps without a template file.
    """
    return [step for step in steps if not os.path.isfile(step_template(project, step))]


def load_pipeline(
    project: str,
    pipeline_file: str,
) -> dict:
    """Read and validate a pipeline definition.

    Every step is validated before the pipeline is used, so that an unknown step cannot leave the
    earlier steps of the chain submitted. A project can add its own keys to the definition.

    Args:
        project: Name of the project.
        pipeline_file: Path of the pipeline file.

    Returns:
        dict: Content of the pipeline file, with the key 'name' added.
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

    missing = missing_templates(project, steps)
    if missing:
        raise ValueError(f"Pipeline file {pipeline_file} refers to steps without a template: {missing}.")

    pipeline["name"] = os.path.basename(pipeline_file)[:-len(".json")]

    return pipeline


def steps_from(
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
