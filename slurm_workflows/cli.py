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

from slurm_workflows.deploy import deploy_jobs, project_module
from slurm_workflows.metadata import write_job_metadata
from slurm_workflows.pipelines import pipeline_names, resolve_pipeline
from slurm_workflows.repositories import job_repositories, read_repository_file
from slurm_workflows.settings import load_settings
from slurm_workflows.submission import archive_job, archive_submitted_job, submit
from slurm_workflows.summary import summarize
from slurm_workflows.templates import project_of_template
from slurm_workflows.update import update_archive


def main_deploy():
    parser = argparse.ArgumentParser(
        description="Fill in a template for an sbatch script with the values of a parameter file. "
        "With --submit, the sbatch script is submitted and archived. "
        "A pipeline fills in one template per step and submits the steps as a chain of Slurm jobs, "
        "where each step starts only after its predecessor completed successfully. "
        "The folder of the template, or the first part of the pipeline name, selects the project. "
        "A project can add its own options. Give -h together with -p or -i to list them, "
        "e.g. -p cochlea-net/sgn -h.")

    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("-i", "--input", type=str, default=None,
                        help="Input template of a single job, e.g. templates/<project>/<step>.template.")
    source.add_argument("-p", "--pipeline", type=str, default=None,
                        help=f"Pipeline to submit as a chain of jobs, as <project>/<pipeline>, or the path of a "
                             f"pipeline file <root>/pipelines/<project>/<pipeline>.json. Available: {pipeline_names()}")

    parser.add_argument("-j", "--params", type=str, required=True,
                        help="Parameter file of the job. A parameter overrides the setting of the same key.")

    parser.add_argument("-s", "--settings", type=str, default=None,
                        help="Settings file of the project, with the paths and names of your account. "
                             "Default: project_settings/<project>.json")
    parser.add_argument("-a", "--archive-dir", type=str, default=None,
                        help="Archive directory, which holds the archive folders. "
                             "Default: archive_dir of the settings file.")
    parser.add_argument("-r", "--repository-file", type=str, default=None,
                        help="File of additional git repositories to track. Each line holds a name and a path, "
                             "separated by whitespace, e.g. a tab. The job also tracks slurm-workflows and the "
                             "repositories of the settings file.")
    parser.add_argument("--submit", action="store_true",
                        help="Submit the jobs. Without this option, the sbatch scripts are only written and checked.")
    parser.add_argument("--allow-missing", dest="allow_missing", action="store_true",
                        help="Print a warning instead of an error for a placeholder without a value, e.g. for a "
                             "draft template. The sbatch script keeps the text <name>, so the option works only "
                             "without --submit.")
    parser.add_argument("--start-at", dest="start_at", type=str, default=None,
                        help="Start a pipeline at this step, to resume a chain after a failure. A step is the "
                             "name of a template without .template, also a step which the project adds, e.g. "
                             "cleanup_SGN or download_image. Needs -p.")
    parser.add_argument("--force", action="store_true",
                        help="Submit the jobs even if a check gives a warning, e.g. because the input data "
                             "does not exist or a previous result exists.")

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
    if args.allow_missing and args.submit:
        parser.error("--allow-missing keeps the text <name> in the sbatch script. Use it without --submit.")

    try:
        deploy_jobs(args, project, module)
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(str(exc))


def main_write_metadata():
    parser = argparse.ArgumentParser(
        description="Create or update the metadata.json of an archive folder from its sbatch.sbatch and log.txt.")

    parser.add_argument("archive_folder", type=str, help="Archive folder of the job, with sbatch.sbatch and log.txt.")

    parser.add_argument("-o", "--output", type=str, default=None,
                        help="Output file for metadata. Default: <archive_folder>/metadata.json")
    parser.add_argument("-j", "--jobid", type=str, default=None,
                        help="JobID of the efficiency report. Default: the last JobID of log.txt.")
    parser.add_argument("-r", "--repository-file", type=str, default=None,
                        help="File of the git repositories to track. Each line holds a name and a path, "
                             "separated by whitespace, e.g. a tab. Without it, the recorded hashes stay.")
    parser.add_argument("-s", "--settings", type=str, default=None,
                        help="Settings file of the project, which names the HPC user.")
    parser.add_argument("--overwrite", action="store_true",
                        help="Create the metadata anew, instead of updating an existing file.")
    args = parser.parse_args()

    try:
        repositories = None if args.repository_file is None else read_repository_file(args.repository_file)
        write_job_metadata(args.archive_folder, args.output, args.jobid, repositories, args.overwrite, args.settings)
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(str(exc))


def main_update_metadata():
    parser = argparse.ArgumentParser(
        description="Update the metadata.json of each archive folder. The efficiency report of a job is "
        "refreshed with one reportseff call by JobID if it is missing or not final, e.g. PENDING or RUNNING. The "
        "command lists the JobIDs which reportseff no longer knows. With -s, the core hours are added from the "
        "slurm output files. --general corrects the date and the task from the folder name, and replaces an "
        "efficiency report of another JobID. --sbatch copies the #SBATCH parameters of sbatch.sbatch.")

    parser.add_argument("archive_dir", type=str, help="Archive directory, which holds the archive folders.")

    parser.add_argument("-p", "--pattern", type=str, default=None,
                        help="Part of the archive folder name, matched as *<pattern>*. Supports wildcards.")
    parser.add_argument("-s", "--slurm-dir", type=str, default=None,
                        help="Folder of the slurm output files slurm-<jobid>.out or slurm-<jobid>_<array_index>.out, "
                             "usually the folder where deploy ran. Adds slurm_output with the core hours.")
    parser.add_argument("--general", action="store_true",
                        help="Correct the date and the task from the folder name, and replace an efficiency "
                             "report of another JobID.")
    parser.add_argument("--sbatch", action="store_true",
                        help="Copy the #SBATCH parameters of sbatch.sbatch into the metadata.")
    parser.add_argument("--backfill", action="store_true",
                        help="Create previous/<jobid>/metadata.json for the earlier JobIDs of log.txt, which have "
                             "no metadata.")
    args = parser.parse_args()

    try:
        update_archive(args.archive_dir, args.pattern, args.general, args.sbatch, args.slurm_dir, args.backfill)
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(str(exc))


def iso_date(value: str) -> str:
    return datetime.strptime(value, "%Y-%m-%d").date().isoformat()


def main_summary():
    parser = argparse.ArgumentParser(
        description="Print the average efficiency and the core hours of the archived jobs, and optionally "
        "write one row per job, ordered by date. The command only reads the archive. "
        "Run slurm_wf.update_metadata first to refresh the data.")

    parser.add_argument("archive_dir", type=str, help="Archive directory, which holds the archive folders.")

    parser.add_argument("-o", "--output", type=str, default=None,
                        help="Table with one row per job. An .xlsx file needs openpyxl. "
                             "Any other suffix gives a TSV file.")
    parser.add_argument("-p", "--pattern", type=str, default=None,
                        help="Part of the archive folder name, matched as *<pattern>*. Supports wildcards.")
    parser.add_argument("--since", type=iso_date, default=None, help="First job date, YYYY-MM-DD.")
    parser.add_argument("--until", type=iso_date, default=None, help="Last job date, YYYY-MM-DD.")
    parser.add_argument("--state", type=lambda value: value.split(","), default=None,
                        help="Keep a job if one of its tasks has one of these states, e.g. COMPLETED,TIMEOUT.")
    parser.add_argument("--partition", type=lambda value: value.split(","), default=None,
                        help="Keep a job of one of these partitions, e.g. grete:shared,standard96.")
    args = parser.parse_args()

    try:
        summarize(args.archive_dir, args.output, args.pattern, args.since, args.until, args.state, args.partition)
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(str(exc))


def main_submit():
    parser = argparse.ArgumentParser(
        description="Submit an sbatch script and add its JobID to the log file <date>_log_<suffix>.txt beside it. "
        "With -a, the files of the job, its environment and its metadata are archived in "
        "<archive_dir>/<date>_<suffix>/. The sbatch script follows the format <date>_sbatch_<suffix>.sbatch.")

    parser.add_argument("sbatch_file", type=str, help="The sbatch script.")

    parser.add_argument("-m", "--move", action="store_true",
                        help="Move the job files into the archive instead of copying them.")
    parser.add_argument("-a", "--archive-dir", type=str, default=None,
                        help="Archive directory, which holds the archive folders. Without it, the job is not "
                             "archived.")
    parser.add_argument("-r", "--repository-file", type=str, default=None,
                        help="File of additional git repositories to track. Each line holds a name and a path, "
                             "separated by whitespace, e.g. a tab. The job also tracks slurm-workflows and the "
                             "repositories of the -s settings file.")
    parser.add_argument("-s", "--settings", type=str, default=None,
                        help="Settings file of the project. It names the HPC user and the git repositories to track.")
    parser.add_argument("-d", "--dependency", type=str, default=None,
                        help="Start the job only after this JobID completed successfully.")
    args = parser.parse_args()

    try:
        settings = {} if args.settings is None else load_settings(args.settings)
        repositories = job_repositories(settings, args.repository_file)
        jobid = submit(args.sbatch_file, args.dependency)
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(str(exc))

    print(f"Submitted batch job {jobid}")

    if args.archive_dir is None:
        return

    try:
        archive_submitted_job(args.sbatch_file, jobid, args.archive_dir, args.move, repositories, args.settings)
    except (OSError, ValueError) as exc:
        sys.exit(f"Job {jobid} was submitted, but its archiving failed: {exc}")


def main_archive():
    parser = argparse.ArgumentParser(
        description="Copy or move the files of one job into its archive folder <archive_dir>/<date>_<suffix>/. "
        "Every file of the job follows the format <date>_<middle>_<suffix>.<ext>, with a middle part of one word, "
        "such as 'sbatch' or 'log', and is archived as <middle>.<ext>.")

    parser.add_argument("date", type=str, help="Date of the job, YYYY-MM-DD.")
    parser.add_argument("suffix", type=str, help="Suffix of the job.")

    parser.add_argument("-m", "--move", action="store_true", help="Move the files instead of copying them.")
    parser.add_argument("-i", "--input-dir", type=str, default=os.environ.get("WORK"),
                        help="Directory of the job files. Default: $WORK")
    parser.add_argument("-a", "--archive-dir", type=str, default=os.environ.get("JOB_ARCHIVE"),
                        help="Archive directory, which holds the archive folders. Default: $JOB_ARCHIVE")
    args = parser.parse_args()

    if args.input_dir is None or args.archive_dir is None:
        parser.error("-i and -a are needed, unless WORK and JOB_ARCHIVE are set.")

    try:
        archive_job(os.path.realpath(args.input_dir), os.path.realpath(args.archive_dir), args.date, args.suffix,
                    args.move)
    except (OSError, ValueError) as exc:
        sys.exit(str(exc))
