#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Access to the pipeline definitions. A pipeline is an ordered list of templates which is submitted
as a chain of Slurm jobs.
"""
import glob
import json
import os

REPOSITORY_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

PIPELINE_DIR = os.path.join(REPOSITORY_DIR, "pipelines")
TEMPLATE_DIR = os.path.join(REPOSITORY_DIR, "templates")

TEMPLATE_SUFFIX = ".template"

# Steps of a pipeline, in the order in which they are submitted.
STEPS_KEY = "steps"

# Steps of a pipeline which export the result to MoBIE. They need a MoBIE project to write into.
MOBIE_STEPS_KEY = "mobie_steps"

# Key of the group which a step without a group in its template name belongs to.
GROUP_KEY = "group"


def pipeline_names() -> list:
    """List the names of the available pipelines.

    Returns:
        list: Sorted pipeline names, without the '.json' suffix.
    """
    return sorted(os.path.basename(path)[:-len(".json")]
                  for path in glob.glob(os.path.join(PIPELINE_DIR, "*.json")))


def step_template(
    step: str,
) -> str:
    """Return the template path of a pipeline step.

    Args:
        step: Name of the step, which is the template name without the suffix.

    Returns:
        str: Path of the template file.
    """
    return os.path.join(TEMPLATE_DIR, f"{step}{TEMPLATE_SUFFIX}")


def load_pipeline(
    name: str,
) -> dict:
    """Read and validate a pipeline definition.

    Every step is validated before the pipeline is used, so that an unknown step cannot leave the
    earlier steps of the chain submitted.

    Args:
        name: Pipeline name, or path to a pipeline file.

    Returns:
        dict: Content of the pipeline file, with the key 'name' added.
    """
    pipeline_file = name if os.path.isfile(name) else os.path.join(PIPELINE_DIR, f"{name}.json")

    if not os.path.isfile(pipeline_file):
        raise FileNotFoundError(f"Pipeline {name} not found. Available pipelines: {pipeline_names()}.")

    with open(pipeline_file, "r") as myfile:
        try:
            pipeline = json.load(myfile)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Pipeline file {pipeline_file} is not valid JSON: {exc}") from exc

    # The type is checked first. A non-empty string passes the check below and then breaks the
    # concatenation with a TypeError, which the caller does not turn into a message.
    for key in (STEPS_KEY, MOBIE_STEPS_KEY):
        value = pipeline.get(key)
        if value is not None and not isinstance(value, list):
            raise ValueError(f"Pipeline file {pipeline_file} needs a list of steps for '{key}'.")

    steps = pipeline.get(STEPS_KEY)
    if not steps:
        raise ValueError(f"Pipeline file {pipeline_file} has no steps.")

    missing = [step for step in steps + pipeline.get(MOBIE_STEPS_KEY, [])
               if not os.path.isfile(step_template(step))]
    if missing:
        raise ValueError(f"Pipeline file {pipeline_file} refers to steps without a template: {missing}.")

    pipeline["name"] = os.path.basename(pipeline_file)[:-len(".json")]

    return pipeline


def pipeline_steps(
    definition: dict,
    mobie_project: str,
) -> tuple:
    """Return the steps to submit, and the MoBIE steps which are skipped.

    The MoBIE steps need a project to write into. Without one they are dropped instead of failing,
    so a pipeline runs on an account which uses no MoBIE project.

    Args:
        definition: Output of `load_pipeline()`.
        mobie_project: Value of 'mobie_project' of the settings file, or None.

    Returns:
        tuple of:
            list - the steps to submit, in order
            list - the MoBIE steps which are skipped
    """
    steps = list(definition[STEPS_KEY])
    mobie_steps = list(definition.get(MOBIE_STEPS_KEY, []))

    if mobie_project:
        return steps + mobie_steps, []

    return steps, mobie_steps


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
