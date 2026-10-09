#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Command line interface of the `slurm_wf.*` commands. The scripts in `scripts/` call the same
functions, so they work without an installation.
"""
import argparse
import os
import sys

from slurm_workflows.deploy import RUN_SBATCH_SCRIPT, SCRIPTS_DIR, deploy_jobs, project_module
from slurm_workflows.metadata import write_job_metadata
from slurm_workflows.pipelines import pipeline_names, project_of_template, resolve_pipeline
from slurm_workflows.update import update_archive

ARCHIVE_SCRIPT = os.path.join(SCRIPTS_DIR, "02_archive_scripts.sh")


def deploy():
    parser = argparse.ArgumentParser(
        description="Fill in a template for an sbatch script using a JSON dictionary. "
        "The new sbatch script is deployed to the cluster and an entry in the archive directory is created. "
        "A pipeline fills in one template per step and submits the steps as a chain of Slurm jobs, "
        "where each step starts only after its predecessor completed successfully. "
        "The folder of the template, or the first part of the pipeline name, selects the project.")

    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("-i", "--input", type=str, default=None,
                        help="Input template of a single job, e.g. templates/<project>/<step>.template.")
    source.add_argument("-p", "--pipeline", type=str, default=None,
                        help=f"Pipeline to submit as a chain of jobs, as <project>/<pipeline>. "
                             f"Available: {pipeline_names()}")

    parser.add_argument("-j", "--json", type=str, required=True, help="JSON dictionary.")

    parser.add_argument("-s", "--settings", type=str, default=None,
                        help="JSON file with the paths and names of your account. "
                             "Default: project_settings/<project>.json")
    parser.add_argument("-a", "--archive_dir", type=str, default=None,
                        help="Directory to archive scripts and metadata. Default: archive_dir of the settings file.")
    parser.add_argument("-r", "--repository_file", type=str, default=None,
                        help="File containing git repositories to track. "
                             "Default: derived from the repositories of the settings file.")
    parser.add_argument("--deploy", action="store_true", help="Run script.")
    parser.add_argument("--allow-missing", dest="allow_missing", action="store_true",
                        help="Print a warning instead of raising an error for an unresolved placeholder.")
    parser.add_argument("--start-at", dest="start_at", type=str, default=None,
                        help="Start a pipeline at this step, to resume a chain after a failure.")
    parser.add_argument("--force", action="store_true",
                        help="Deploy the job even if the input data does not exist, or if a previous "
                             "result would be overwritten.")

    # The project adds its own options, so it is resolved before the full parse. The pre-parser
    # has no help and no required option, so that '-h' lists the options of the project as well.
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("-i", "--input", type=str, default=None)
    pre_parser.add_argument("-p", "--pipeline", type=str, default=None)
    known_args, _ = pre_parser.parse_known_args()

    project = None
    try:
        if known_args.pipeline is not None:
            project, _ = resolve_pipeline(known_args.pipeline)
        elif known_args.input is not None:
            project = project_of_template(known_args.input)
        if project is not None:
            module = project_module(project)
            module.add_arguments(parser)
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(str(exc))

    args = parser.parse_args()

    if args.start_at is not None and args.pipeline is None:
        parser.error("--start-at needs a pipeline. Use it together with -p.")

    try:
        deploy_jobs(args, project, module)
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(str(exc))


def write_metadata():
    parser = argparse.ArgumentParser(
        description="Extract metadata from an sbatch script.")

    parser.add_argument('input_dir', type=str, help="Input directory containing sbatch script.")

    parser.add_argument('-o', "--output", type=str, default=None,
                        help="Output file for metadata. Default: <input_dir>/metadata.json")
    parser.add_argument('-j', "--jobid", type=str, default=None, help="JobID")
    parser.add_argument('-r', "--repository_file",
                        type=str, default=None,
                        help="File with information about git repositories in format '<Name>\t<path-to-repository>\n'")
    parser.add_argument("-s", "--settings", type=str, default=None,
                        help="Settings file of the project, which names the HPC user.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing JSON file.")
    args = parser.parse_args()

    try:
        write_job_metadata(args.input_dir, args.output, args.jobid, args.repository_file, args.overwrite,
                           args.settings)
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(str(exc))


def update_metadata():
    parser = argparse.ArgumentParser(
        description="Update report of job efficiency, if last status was 'PENDING'.")

    parser.add_argument('input_dir', type=str, help="Input directory containing sbatch script.")

    parser.add_argument('-p', "--pattern", type=str, default=None,
                        help="Pattern to match folders in job archive. Supports wildcards.")
    parser.add_argument('-s', "--slurm_dir", type=str, default=None,
                        help="Directory containing slurm output files (slurm-<job_id>.out or "
                             "slurm-<job_id>_<array_index>.out) to parse and store in metadata.")
    parser.add_argument("--general", action="store_true", help="Check general info.")
    parser.add_argument("--sbatch", action="store_true", help="Check sbatch information.")
    parser.add_argument("--average", action="store_true",
                        help="Print average efficiency and core-hour summary across matching jobs.")
    args = parser.parse_args()

    try:
        update_archive(args.input_dir, args.pattern, args.general, args.sbatch, args.slurm_dir, args.average)
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(str(exc))


# The bash scripts stay standalone. exec hands them the process, so their options, output and exit
# code are unchanged.
def submit():
    os.execvp("bash", ["bash", RUN_SBATCH_SCRIPT] + sys.argv[1:])


def archive():
    os.execvp("bash", ["bash", ARCHIVE_SCRIPT] + sys.argv[1:])
