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
from slurm_workflows.metadata import ordered_pipeline
from slurm_workflows.pipelines import load_pipeline, resolve_pipeline, steps_starting_at
from slurm_workflows.projects import default
from slurm_workflows.repositories import check_dirty, job_repositories
from slurm_workflows.settings import blueprint_settings_file, load_settings, project_settings_file
from slurm_workflows.settings import settings_to_placeholders
from slurm_workflows.submission import archive_submitted_job, cancel_jobs, release_job, submit
from slurm_workflows.templates import render_template, step_template

# The functions which `deploy_jobs()` calls on the deployment module of a project.
DEPLOYMENT_FUNCTIONS = ("add_arguments", "select_steps", "prepare_step", "check_steps")

# Entries of metadata.json which `submit_chain()` writes. An entry of `step_metadata` cannot replace them.
GENERIC_METADATA = ("project", "pipeline")


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
        settings_path = project_settings_file(project)

    if not os.path.isfile(settings_path):
        print(f"Warning: settings file {settings_path} not found. Copy {blueprint_settings_file(project)} "
              f"to {settings_path} and adapt the values to your account.")
        return {}, None

    return load_settings(settings_path), settings_path


def submit_chain(
    sbatch_files: list,
    archive_dir: str,
    repositories: dict,
    settings_file: str,
    project: str = None,
    pipeline: dict = None,
    step_names: list = None,
    step_metadata: list = None,
) -> list:
    """Submit sbatch scripts as a chain of jobs and archive each job.

    Each job starts only after its predecessor completed successfully. If `archive_dir` is set, the first job
    is held until all jobs are archived. A failed archiving or release cancels all submitted jobs.

    Args:
        sbatch_files: The sbatch scripts, in the order of submission.
        archive_dir: Directory of the archive folders, or None to not archive the jobs.
        repositories: Names and paths of the git repositories to track.
        settings_file: Settings file of the project, or None.
        project: Name of the project for the metadata, or None.
        pipeline: Pipeline entry of the metadata, e.g. `{"name": ...}`, or None.
        step_names: Name of each step, in the order of `sbatch_files`. Required for a `pipeline` metadata entry.
        step_metadata: Entries to add to the metadata of each step, in the order of `sbatch_files`, or None.
            An entry of `GENERIC_METADATA` is ignored.

    Returns:
        list: JobIDs of the submitted jobs.
    """
    if pipeline is not None and step_names is None:
        raise ValueError("A pipeline entry in the metadata needs the step names.")

    dependency = None

    # The JobID of the first step identifies the run of a pipeline in the archive.
    run = None
    jobids = []

    # Only step 1 is held, subsequent steps are submitted with dependency on the previous step.
    for step, sbatch_file in enumerate(sbatch_files, start=1):
        try:
            jobid = submit(sbatch_file, dependency, hold=bool(archive_dir) and run is None)
        except ValueError as submit_exc:
            # A failed scontrol release leaves all steps waiting, so they are cancelled.
            if archive_dir and run is not None:
                try:
                    release_job(run)
                except ValueError as release_exc:
                    cancel_jobs(jobids)
                    raise ValueError(
                        f"{submit_exc}\nRelease failed, so the jobs {', '.join(jobids)} were cancelled: {release_exc}"
                    ) from release_exc

            # Raises the submit error if the current step failed.
            raise
        jobids.append(jobid)
        print(f"Step {step} of {len(sbatch_files)} submitted as JobID {jobid}.")
        if run is None:
            run = jobid

        if archive_dir:
            metadata = {}
            if project is not None:
                metadata["project"] = project
            if step_metadata is not None:
                metadata.update((key, value) for key, value in step_metadata[step - 1].items()
                                if key not in GENERIC_METADATA)
            if pipeline is not None:
                entry = {**pipeline, "run": run, "step": step, "of": len(sbatch_files),
                         "step_name": step_names[step - 1]}
                if dependency is not None:
                    entry["depends_on"] = dependency
                metadata["pipeline"] = ordered_pipeline(entry)
            try:
                archive_submitted_job(sbatch_file, jobid, archive_dir, move=True, repositories=repositories,
                                      settings_file=settings_file, metadata=metadata)
            except (OSError, ValueError) as exc:
                cancel_jobs(jobids)
                raise ValueError(f"Archiving failed, so the jobs {', '.join(jobids)} were cancelled: {exc}") from exc

        dependency = jobid

    # A failed scontrol release leaves all steps waiting, so they are cancelled.
    if archive_dir:
        try:
            release_job(run)
        except ValueError as exc:
            cancel_jobs(jobids)
            raise ValueError(f"Release failed, so the jobs {', '.join(jobids)} were cancelled: {exc}") from exc

    return jobids


def deploy_jobs(
    args,
    project: str,
    module,
):
    """Render the templates of a job or a pipeline, check the input, and submit the jobs.

    The order is:
    1. Render the sbatch script of every step. An error removes the scripts which were written.
    2. Check the scripts with the generic checks and `check_steps()` of the project, and check the
       archive directory.
    3. Print the warnings, then the notes. A dry run stops here.
    4. Stop if a tracked repository is dirty, unless `--allow-dirty` is given.
    5. Stop if a check gave a warning, unless `--force` is given.
    6. Submit each step with a dependency on the previous step, and archive it. The first step is held
        until all steps are archived. A failed archive cancels all submitted steps.

    Args:
        args: Parsed command line options of `slurm_wf.deploy`, including the options of the project.
        project: Name of the project.
        module: Output of `project_module()`.
    """
    settings, settings_path = load_project_settings(project, args.settings)
    archive_dir = args.archive_dir if args.archive_dir is not None else settings.get("archive_dir")

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
    step_metadata = []
    notes = []
    try:
        for template_file in template_files:
            # A parameter of the job overrides the corresponding setting.
            placeholder_values = settings_to_placeholders(settings)
            placeholder_values.update(parameters)
            placeholder_values, script_name, step_notes, metadata = module.prepare_step(
                settings, placeholder_values, template_file, args, definition)

            sbatch_file = f"{str(date.today())}_sbatch_{script_name}.sbatch"
            render_template(template_file, sbatch_file, placeholder_values, strict=not args.allow_missing)
            sbatch_files.append(sbatch_file)
            step_values.append(placeholder_values)
            step_metadata.append(metadata)
            notes += step_notes

        if module is default:
            notes.append(f"project '{project}' has no deployment module. "
                         "The placeholders were filled from the settings and the parameters only.")

        # Only the first step can be checked here. The input of a later step is produced by its
        # predecessor, so it is verified by the guard inside the job script.
        warnings = check_job_input(sbatch_files[0])
        for sbatch_file in sbatch_files:
            warnings += check_external_input(sbatch_file)
            warnings += check_output_absent(sbatch_file)
        warnings += module.check_steps(template_files, sbatch_files)
        # The archiving runs after the submission, so a missing directory must stop the chain here.
        if archive_dir and not os.path.isdir(archive_dir):
            warnings.append(f"the archive directory {archive_dir} does not exist, so the jobs would not be archived")

        repositories = job_repositories(settings, args.repository_file)
        dirty = check_dirty(repositories, settings.get("untracked_files_dirty", False))
        dirty_message = None
        if dirty and not args.allow_dirty:
            dirty_message = f"Dirty repositories: {', '.join(dirty)}. Pass --allow-dirty to submit anyway."

        if not args.submit:
            for sbatch_file, placeholder_values in zip(sbatch_files, step_values):
                print(f"Placeholder values of {sbatch_file}: {placeholder_values}")
            for sbatch_file in sbatch_files[1:]:
                print(f"The input of {sbatch_file} is verified when the job runs.")

        # The warnings and the notes come last, so that the details of the dry run do not hide them.
        for message in warnings:
            print(f"Warning: {message}")
        if dirty_message is not None and not args.submit:
            print(f"Warning: {dirty_message}")
        for message in notes:
            print(f"Note: {message}")

        if not args.submit:
            print("Nothing is submitted. Use --submit to submit the jobs.")
            return

        if dirty_message is not None:
            raise ValueError(dirty_message)

        if warnings and not args.force:
            raise ValueError("The checks gave warnings, see above. Use --force to submit the jobs anyway.")
    except BaseException:
        # A script of an aborted deployment must not stay, because a later call could submit it.
        for sbatch_file in sbatch_files:
            os.remove(sbatch_file)
        raise

    if not archive_dir:
        print("Warning: no archive directory is set. The jobs are not archived.")

    pipeline = None
    if definition is None:
        step_names = None
    else:
        pipeline = {"name": definition["name"]}
        if args.start_at is not None:
            pipeline["start_at"] = args.start_at
    submit_chain(sbatch_files, archive_dir, repositories, settings_path, project=project, pipeline=pipeline,
                 step_names=step_names, step_metadata=step_metadata)
