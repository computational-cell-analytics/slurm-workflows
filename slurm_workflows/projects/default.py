#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Deployment of a project without its own module `slurm_workflows/projects/<project>/deployment.py`.
The placeholders are filled from the settings and the job parameters only. A project module takes
each function which it does not define from this module.
"""
import os

from slurm_workflows.pipelines import STEPS_KEY
from slurm_workflows.templates import TEMPLATE_SUFFIX


def add_arguments(
    parser,
) -> None:
    """Add the command line options of the project to `slurm_wf.deploy`.

    The default project adds no option.

    Args:
        parser: `argparse.ArgumentParser` of `slurm_wf.deploy`, with the generic options already added.
    """


def select_steps(
    definition: dict,
    settings: dict,
    args,
) -> list:
    """Select the steps of a pipeline to submit.

    `--start-at` applies after this function, so it can name a step which the project adds.

    Args:
        definition: Pipeline definition of `load_pipeline()`: `steps`, the optional `description`, the
            keys of the project, and the added keys `name` and `template_dir`.
        settings: Settings of the project, or an empty dictionary.
        args: `argparse.Namespace` with the generic options and the options of the project.

    Returns:
        list: Step names in the order of submission. Each step needs a template in `template_dir`.
            The default deployment returns all `steps`.
    """
    return definition[STEPS_KEY]


def prepare_step(
    settings: dict,
    placeholder_values: dict,
    template_file: str,
    args,
    definition: dict = None,
) -> tuple:
    """Prepare the placeholder values of one job.

    `deploy_jobs()` calls this function once per step, with new placeholder values, before it writes
    the sbatch script of the step.

    Args:
        settings: Settings of the project, or an empty dictionary.
        placeholder_values: The placeholders of the settings, with the parameters of the job on top. The
            function can change the dictionary in place.
        template_file: Path of the template.
        args: `argparse.Namespace` with the generic options and the options of the project.
        definition: Pipeline definition, see `select_steps()`, or None for a single job of `-i`.

    Returns:
        tuple of:
            dict - the placeholder values of the template
            str  - the name of the sbatch script without the date and the extension. The default is
                   the template name
            list - notes, which `deploy_jobs()` prints after the warnings. The default is empty
    """
    return placeholder_values, os.path.basename(template_file)[:-len(TEMPLATE_SUFFIX)], []


def check_steps(
    template_files: list,
    sbatch_files: list,
) -> list:
    """Check the rendered sbatch scripts before the submission.

    The generic checks of `slurm_workflows/inputs.py` run in addition to this function.

    Args:
        template_files: Templates of the steps, in the order of submission.
        sbatch_files: The sbatch scripts of the steps, in the same order.

    Returns:
        list: Warning messages. A warning stops `--submit`, unless `--force` is given. The default
            deployment returns no warning.
    """
    return []
