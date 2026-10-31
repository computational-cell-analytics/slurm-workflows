#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Logic of `slurm_wf.deploy`: fill the parameters of a job into a template, submit the sbatch script,
and archive it.
"""
import importlib
import json
import os
import types
from datetime import date

from slurm_workflows import REPOSITORY_DIR
from slurm_workflows.inputs import check_external_input, check_job_input, check_output_absent
from slurm_workflows.pipelines import load_pipeline, resolve_pipeline, steps_starting_at
from slurm_workflows.projects import default
from slurm_workflows.repositories import read_repository_file
from slurm_workflows.settings import blueprint_settings_file, load_settings, settings_file, settings_to_placeholders
from slurm_workflows.submission import archive_submitted_job, submit
from slurm_workflows.templates import render_template, step_template

# The functions which `deploy_jobs()` calls on the deployment module of a project.
DEPLOYMENT_FUNCTIONS = ("add_arguments", "select_steps", "prepare_step", "check_steps")


def project_module(
    project: str,
):
    """Import the deployment module of a project.

    A project module defines only the functions which differ from the default deployment. The
    default deployment supplies the other functions of `DEPLOYMENT_FUNCTIONS`.

    Args:
        project: Name of the project. A hyphen becomes an underscore in the package name.

    Returns:
        `slurm_workflows/projects/default.py` if the project has no module, else a namespace with the
        functions of `DEPLOYMENT_FUNCTIONS`.
    """
    package = project.replace("-", "_")
    module_file = os.path.join(REPOSITORY_DIR, "slurm_workflows", "projects", package, "deployment.py")
    if not os.path.isfile(module_file):
        return default

    module = importlib.import_module(f"slurm_workflows.projects.{package}.deployment")
    return types.SimpleNamespace(**{name: getattr(module, name, getattr(default, name))
                                    for name in DEPLOYMENT_FUNCTIONS})


def load_project_settings(
    project: str,
    settings_path: str = None,
) -> tuple:
    """Read the settings of a project.

    A missing settings file gives a warning and no settings. The project decides if it can run
    without them.

    Args:
        project: Name of the project.
        settings_path: Settings file given on the command line, or None for the default of the project.

    Returns:
        tuple of:
            dict - the settings
            str  - the settings file, or None if it does not exist
    """
    if settings_path is None:
        settings_path = settings_file(project)

    if not os.path.isfile(settings_path):
        print(f"Warning: settings file {settings_path} not found. Copy {blueprint_settings_file(project)} "
              f"to {settings_path} and adapt the values to your account.")
        return {}, None

    return load_settings(settings_path), settings_path


def deploy_jobs(
    args,
    project: str,
    module,
):
    """Render the templates of a job or a pipeline, check the input, and submit the jobs.

    Args:
        args: Parsed command line options of `slurm_wf.deploy`, including the options of the project.
        project: Name of the project.
        module: Output of `project_module()`.
    """
    settings, settings_path = load_project_settings(project, args.settings)

    with open(args.params, "r") as myfile:
        parameters = json.load(myfile)

    if args.pipeline is None:
        template_files = [args.input]
        definition = None
    else:
        _, pipeline_file = resolve_pipeline(args.pipeline)
        definition = load_pipeline(pipeline_file)
        if definition.get("description") is not None:
            print(f"Pipeline {definition['name']}: {definition['description']}")
        # The steps are selected before '--start-at', so that a step which the project adds can be resumed.
        step_names = module.select_steps(definition, settings, args)
        if args.start_at is not None:
            step_names = steps_starting_at(step_names, args.start_at)
        template_files = [step_template(definition["template_dir"], step) for step in step_names]

    # Render every step before anything is submitted, so that an unresolved placeholder of a later
    # step cannot leave the earlier steps of the chain queued.
    sbatch_files = []
    step_values = []
    for template_file in template_files:
        # A parameter of the job overrides the corresponding setting.
        placeholder_values = settings_to_placeholders(settings)
        placeholder_values.update(parameters)
        placeholder_values, script_name = module.prepare_step(settings, placeholder_values, template_file, args,
                                                              definition)

        sbatch_file = f"{str(date.today())}_sbatch_{script_name}.sbatch"
        render_template(template_file, sbatch_file, placeholder_values, strict=not args.allow_missing)
        sbatch_files.append(sbatch_file)
        step_values.append(placeholder_values)

    if module is default:
        print(f"Note: project '{project}' has no deployment module. "
              "The placeholders were filled from the settings and the parameters only.")

    # Only the first step can be checked here. The input of a later step is produced by its
    # predecessor, so it is verified by the guard inside the job script.
    warnings = check_job_input(sbatch_files[0])
    for sbatch_file in sbatch_files:
        warnings += check_external_input(sbatch_file)
        warnings += check_output_absent(sbatch_file)
    warnings += module.check_steps(template_files, sbatch_files)

    for message in warnings:
        print(f"Warning: {message}")

    if len(sbatch_files) > 1:
        for sbatch_file in sbatch_files[1:]:
            print(f"The input of {sbatch_file} is verified when the job runs.")

    if not args.submit:
        for sbatch_file, placeholder_values in zip(sbatch_files, step_values):
            print(f"Placeholder values of {sbatch_file}: {placeholder_values}")
        print("Nothing is submitted. Use --submit to submit the jobs.")
        return

    if warnings and not args.force:
        raise ValueError("The input data of the job does not exist, or a previous run would be overwritten. "
                         "Use --force to submit the job anyway.")

    archive_dir = args.archive_dir
    if archive_dir is None:
        archive_dir = settings.get("archive_dir")
    if not archive_dir:
        print("Warning: no archive directory is set. The jobs are not archived.")

    if args.repository_file is not None:
        repositories = read_repository_file(args.repository_file)
    else:
        repositories = settings.get("repositories", {})

    dependency = None
    for step_index, sbatch_file in enumerate(sbatch_files, start=1):
        jobid = submit(sbatch_file, dependency)
        print(f"Step {step_index} of {len(sbatch_files)} submitted as JobID {jobid}.")

        if archive_dir:
            metadata = {"Project": project}
            if definition is not None:
                metadata["Pipeline"] = {"name": definition["name"], "step": step_index, "of": len(sbatch_files)}
                if dependency is not None:
                    metadata["Pipeline"]["depends_on"] = dependency
            try:
                archive_submitted_job(sbatch_file, jobid, archive_dir, move=True, repositories=repositories,
                                      settings_file=settings_path, metadata=metadata)
            except (OSError, ValueError) as exc:
                # The job is queued, so the chain goes on. Only the archive of the job is incomplete.
                print(f"Warning: job {jobid} was submitted, but its archiving failed: {exc}")

        dependency = jobid
