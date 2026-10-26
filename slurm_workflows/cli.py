#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Command line interface of the `slurm_wf.*` commands. The scripts in `scripts/` call the same
functions, so they work without an installation.
"""
import argparse
import os
import sys
from datetime import datetime

from slurm_workflows.deploy import RUN_SBATCH_SCRIPT, SCRIPTS_DIR, deploy_jobs, project_module
from slurm_workflows.metadata import write_job_metadata
from slurm_workflows.mobie import add_s3_dataset as add_dataset_to_s3, generate_local_project, generate_s3_project
from slurm_workflows.pipelines import pipeline_names, project_of_template, resolve_pipeline
from slurm_workflows.summary import summarize
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
    args = parser.parse_args()

    try:
        update_archive(args.input_dir, args.pattern, args.general, args.sbatch, args.slurm_dir)
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(str(exc))


def iso_date(value: str) -> str:
    return datetime.strptime(value, "%Y-%m-%d").date().isoformat()


def summary():
    parser = argparse.ArgumentParser(
        description="Print the average efficiency and the core hours of the archived jobs, and optionally "
        "write one row per job, ordered by date. The command only reads the archive. "
        "Run slurm_wf.update_metadata first to refresh the data.")

    parser.add_argument("input_dir", type=str, help="Directory of the archive folders.")

    parser.add_argument("-o", "--output", type=str, default=None,
                        help="Table with one row per job. An .xlsx file needs openpyxl. "
                             "Any other suffix gives a TSV file.")
    parser.add_argument("-p", "--pattern", type=str, default=None,
                        help="Pattern to match folders in job archive. Supports wildcards.")
    parser.add_argument("--since", type=iso_date, default=None, help="First job date, YYYY-MM-DD.")
    parser.add_argument("--until", type=iso_date, default=None, help="Last job date, YYYY-MM-DD.")
    parser.add_argument("--state", type=lambda value: value.split(","), default=None,
                        help="Keep a job if one of its tasks has one of these states, e.g. COMPLETED,TIMEOUT.")
    parser.add_argument("--partition", type=lambda value: value.split(","), default=None,
                        help="Keep a job of one of these partitions, e.g. grete:shared,standard96.")
    args = parser.parse_args()

    try:
        summarize(args.input_dir, args.output, args.pattern, args.since, args.until, args.state, args.partition)
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(str(exc))


def add_s3_dataset():
    parser = argparse.ArgumentParser(
        description="Add a dataset to the project.json of the MoBIE project in the S3 bucket, or rebuild "
        "the dataset list of a project.json from the dataset folders. A dataset folder holds a dataset.json. "
        "The dataset names stay in alphabetical order, and the other entries stay unchanged.")

    parser.add_argument("-p", "--mobie_project", type=str, required=True,
                        help="Root of the local MoBIE project. It holds the temporary copy "
                             "tmp_<JobID>_project.json, or tmp_project.json outside of a Slurm job.")
    parser.add_argument("-r", "--s3_remote", type=str, default=None,
                        help="rclone remote and bucket, in the form remote:bucket. Not needed for --generate local.")

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("-d", "--dataset", type=str, default=None, help="Name of the dataset to add.")
    mode.add_argument("--generate", choices=["s3", "local"], default=None,
                      help="Set the dataset list to the dataset folders. 's3' updates the project.json of the "
                           "S3 bucket from the bucket. 'local' updates the project.json of the local project "
                           "from the local folders and uploads nothing.")
    args = parser.parse_args()

    if args.s3_remote is None and args.generate != "local":
        parser.error("-r is needed, unless --generate local is given.")

    try:
        if args.generate == "local":
            generate_local_project(args.mobie_project)
        elif args.generate == "s3":
            generate_s3_project(args.mobie_project, args.s3_remote)
        else:
            add_dataset_to_s3(args.mobie_project, args.s3_remote, args.dataset)
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(str(exc))


# The bash scripts stay standalone. exec hands them the process, so their options, output and exit
# code are unchanged.
def submit():
    os.execvp("bash", ["bash", RUN_SBATCH_SCRIPT] + sys.argv[1:])


def archive():
    os.execvp("bash", ["bash", ARCHIVE_SCRIPT] + sys.argv[1:])
