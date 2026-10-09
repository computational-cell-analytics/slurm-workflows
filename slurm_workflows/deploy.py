#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Logic of `slurm_wf.deploy`: fill the parameters of a job into a template, submit the sbatch script,
and archive it.
"""
import importlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import date

from slurm_workflows.inputs import check_external_input, check_job_input, check_output_absent
from slurm_workflows.metadata import METADATA_FILE, read_metadata, write_metadata
from slurm_workflows.pipelines import REPOSITORY_DIR, load_pipeline, resolve_pipeline, step_template, steps_from
from slurm_workflows.repositories import write_repository_file
from slurm_workflows.settings import blueprint_settings_file, load_settings, settings_file, settings_to_replacements
from slurm_workflows.templates import replace_substrings_in_file

SCRIPTS_DIR = os.path.join(REPOSITORY_DIR, "scripts")

RUN_SBATCH_SCRIPT = os.path.join(SCRIPTS_DIR, "01_run_sbatch.sh")

# Printed by 01_run_sbatch.sh for the JobID of the submitted job.
JOBID_PREFIX = "SUBMITTED_JOBID="


def submit_step(
    output_file: str,
    archive_dir: str,
    repository_file: str,
    settings_path: str = None,
    dependency: str = None,
) -> str:
    """Submit one job through `01_run_sbatch.sh` and return its JobID.

    Args:
        output_file: Path of the generated sbatch script.
        archive_dir: Directory to archive the job in. No archive is written for an empty value.
        repository_file: File listing the git repositories to snapshot.
        settings_path: Settings file which names the HPC user in the metadata, or None.
        dependency: JobID which has to complete successfully before this job starts.

    Returns:
        str: JobID of the submitted job.
    """
    cmd = ["bash", RUN_SBATCH_SCRIPT, "-m"]
    if archive_dir:
        cmd += ["-a", archive_dir]
    else:
        print("Warning: no archive directory is set. The job is not archived.")
    if dependency is not None:
        cmd += ["-d", dependency]
    if settings_path is not None:
        cmd += ["-s", settings_path]
    cmd += ["-r", repository_file, output_file]

    print(" ".join(cmd))
    # 'capture_output' and 'text' need Python 3.7. The login node of the cluster has Python 3.6.
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)

    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)

    jobid = None
    for line in result.stdout.splitlines():
        if line.startswith(JOBID_PREFIX):
            jobid = line[len(JOBID_PREFIX):].strip()

    if jobid is None:
        if result.returncode != 0:
            raise ValueError(f"Submission of {output_file} failed with exit code {result.returncode}.")
        raise ValueError(f"No JobID was reported for {output_file}.")

    # The job is queued, so the chain goes on. Only the archive of the job is incomplete.
    if result.returncode != 0:
        print(f"Warning: job {jobid} was submitted, but its archiving failed with exit code {result.returncode}.")

    return jobid


def write_job_block(
    archive_dir: str,
    output_file: str,
    project: str,
    pipeline_name: str = None,
    step_index: int = None,
    step_count: int = None,
    dependency: str = None,
) -> None:
    """Add the project and the pipeline information to the archived metadata of one job.

    Args:
        archive_dir: Directory the job was archived in.
        output_file: Name of the generated sbatch script.
        project: Name of the project.
        pipeline_name: Name of the pipeline, or None for a single job.
        step_index: Position of the step in the pipeline, starting at 1.
        step_count: Number of steps of the pipeline.
        dependency: JobID of the previous step, if there is one.
    """
    suffix = os.path.basename(output_file).split(".sbatch")[0].replace("_sbatch_", "_", 1)
    metadata_file = os.path.join(archive_dir, suffix, METADATA_FILE)

    if not os.path.isfile(metadata_file):
        print(f"Warning: no metadata found in {metadata_file}. The project information is not stored.")
        return

    metadict = read_metadata(metadata_file)
    metadict["Project"] = project
    if pipeline_name is not None:
        metadict["Pipeline"] = {"name": pipeline_name, "step": step_index, "of": step_count}
        if dependency is not None:
            metadict["Pipeline"]["depends_on"] = dependency

    write_metadata(metadict, metadata_file)


def repository_file_of(
    settings: dict,
    repository_file: str,
) -> tuple:
    """Return the repository file to use, and the temporary file to remove afterwards.

    Args:
        settings: Output of `load_settings()`.
        repository_file: Repository file given on the command line, or None.

    Returns:
        tuple of:
            str — the repository file to use
            str — the temporary file to remove, or None
    """
    if repository_file is not None:
        return repository_file, None

    handle, repository_file = tempfile.mkstemp(prefix="repository_list_", suffix=".txt", text=True)
    os.close(handle)
    write_repository_file(settings.get("repositories", {}), repository_file)

    return repository_file, repository_file


def project_module(
    project: str,
):
    """Import the deployment module of a project.

    Args:
        project: Name of the project.

    Returns:
        module: `slurm_workflows/deployment/<project>_deployment.py`, or
            `slurm_workflows/deployment/default_deployment.py` if the project has none.
    """
    module_file = os.path.join(REPOSITORY_DIR, "slurm_workflows", "deployment", f"{project}_deployment.py")
    if not os.path.isfile(module_file):
        return importlib.import_module("slurm_workflows.deployment.default_deployment")

    # The module name contains the project name, which may hold a hyphen, so it needs importlib.
    return importlib.import_module(f"slurm_workflows.deployment.{project}_deployment")


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
            dict — the settings
            str  — the settings file, or None if it does not exist
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

    with open(args.json, "r") as myfile:
        parameters = json.load(myfile)

    if args.pipeline is None:
        steps = [args.input]
        definition = None
    else:
        _, pipeline_file = resolve_pipeline(args.pipeline)
        definition = load_pipeline(project, pipeline_file)
        if definition.get("description") is not None:
            print(f"Pipeline {definition['name']}: {definition['description']}")
        # The steps are selected before '--start-at', so that a step which the project adds can be resumed.
        step_names = module.select_steps(definition, settings, args)
        if args.start_at is not None:
            step_names = steps_from(step_names, args.start_at)
        steps = [step_template(project, step) for step in step_names]

    # Render every step before anything is submitted, so that an unresolved placeholder of a later
    # step cannot leave the earlier steps of the chain queued.
    output_files = []
    for step_file in steps:
        # A parameter of the job overrides the corresponding setting.
        replacement_dict = settings_to_replacements(settings)
        replacement_dict.update(parameters)
        replacement_dict, script_name = module.prepare_step(settings, replacement_dict, step_file, args, definition)

        output_file = f"{str(date.today())}_sbatch_{script_name}.sbatch"
        replace_substrings_in_file(step_file, output_file, replacement_dict, strict=not args.allow_missing)
        output_files.append(output_file)

    # Only the first step can be checked here. The input of a later step is produced by its
    # predecessor, so it is verified by the guard inside the job script.
    warnings = check_job_input(output_files[0])
    for output_file in output_files:
        warnings += check_external_input(output_file)
        warnings += check_output_absent(output_file)
    warnings += module.check_steps(steps, output_files)

    for message in warnings:
        print(f"Warning: {message}")

    if len(output_files) > 1:
        for output_file in output_files[1:]:
            print(f"The input of {output_file} is verified when the job runs.")

    if not args.deploy:
        if args.pipeline is None:
            print(replacement_dict)
        else:
            print("The pipeline is not deployed. Use --deploy to submit the chain.")
        return

    if warnings and not args.force:
        raise ValueError("The input data of the job does not exist, or a previous run would be overwritten. "
                         "Use --force to submit the job anyway.")

    archive_dir = args.archive_dir
    if archive_dir is None:
        archive_dir = settings.get("archive_dir")

    repository_file, temporary_repository_file = repository_file_of(settings, args.repository_file)

    try:
        dependency = None
        for step_index, output_file in enumerate(output_files, start=1):
            jobid = submit_step(output_file, archive_dir, repository_file, settings_path, dependency)
            print(f"Step {step_index} of {len(output_files)} submitted as JobID {jobid}.")

            if archive_dir:
                if definition is None:
                    write_job_block(archive_dir, output_file, project)
                else:
                    write_job_block(archive_dir, output_file, project, definition["name"],
                                    step_index, len(output_files), dependency)

            dependency = jobid
    finally:
        if temporary_repository_file is not None:
            os.remove(temporary_repository_file)
