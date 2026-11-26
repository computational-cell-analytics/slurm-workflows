#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Command line interface of `slurm_wf.add_s3_dataset`, `slurm_wf.add_processing_metadata` and `slurm_wf.convert`,
which only the project cochlea-net uses.
"""
import argparse
import sys

from slurm_workflows.projects.cochlea_net.conversion import convert_cochlea
from slurm_workflows.projects.cochlea_net.mobie import (add_s3_dataset, add_s3_sources, check_s3_datasets,
                                                        generate_local_project, generate_s3_project,
                                                        repair_s3_dataset)
from slurm_workflows.projects.cochlea_net.processing_metadata import add_processing_metadata
from slurm_workflows.settings import load_settings, project_settings_file

PROJECT = "cochlea-net"


def main_add_s3_dataset():
    parser = argparse.ArgumentParser(
        description="Add a dataset to the MoBIE project in the S3 bucket: its name to the project.json and, with "
        "--source, its sources to its dataset.json. The S3 files list the datasets and the sources of every local "
        "MoBIE project, so only the new entries are added and all other entries stay unchanged. Other modes "
        "rebuild the dataset list of a project.json, check the dataset.json files of the bucket, or repair one.")

    parser.add_argument("-p", "--mobie-project", type=str, default=None,
                        help="Root of the local MoBIE project, the setting mobie_project. Needed for -d and "
                             "--generate. --source reads the local dataset.json from it. Each upload edits a "
                             "temporary copy of the S3 file in it: tmp_<JobID>_<file name>, or tmp_<file name> "
                             "outside of a Slurm job.")
    parser.add_argument("-r", "--s3-remote", type=str, default=None,
                        help="rclone remote and bucket, in the form remote:bucket, the setting s3_remote. "
                             "Not needed for --generate local.")

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("-d", "--dataset", type=str, default=None, help="Name of the dataset to add.")
    mode.add_argument("--generate", choices=["s3", "local"], default=None,
                      help="Set the dataset list to the dataset folders. 's3' updates the project.json of the "
                           "S3 bucket from the bucket. 'local' updates the project.json of the local project "
                           "from the local folders and uploads nothing.")
    mode.add_argument("--check", action="store_true",
                      help="Print each mismatch between the dataset.json and the data folders of every dataset "
                           "in the S3 bucket.")

    dataset_mode = parser.add_mutually_exclusive_group()
    dataset_mode.add_argument("-s", "--source", type=str, nargs="+", default=None,
                              help="Copy these sources and their views from the local dataset.json of -d into "
                                   "the dataset.json of the S3 bucket, before the dataset is added to the "
                                   "project.json. A source of the same name is replaced.")
    dataset_mode.add_argument("--repair", action="store_true",
                              help="Add a source to the S3 dataset.json of -d for each data folder which no "
                                   "source lists. The entry is built from the data in the bucket.")
    parser.add_argument("-e", "--service-endpoint", type=str, default=None,
                        help="URL of the S3 service, the setting service_endpoint, e.g. https://s3.fs.gwdg.de. "
                             "Needed for --repair.")
    parser.add_argument("--default-view", type=str, default=None,
                        help="With --repair: the source whose view becomes the default view of the dataset.")
    parser.add_argument("-o", "--output", type=str, default=None,
                        help="With --repair: write the repaired dataset.json to this file and upload nothing.")
    args = parser.parse_args()

    if args.mobie_project is None and not args.check:
        parser.error("-p is needed, unless --check is given.")
    if args.s3_remote is None and args.generate != "local":
        parser.error("-r is needed, unless --generate local is given.")
    if (args.source or args.repair) and args.dataset is None:
        parser.error("--source and --repair need -d.")
    if args.repair and args.service_endpoint is None:
        parser.error("--repair needs -e.")
    if not args.repair and (args.service_endpoint or args.default_view or args.output):
        parser.error("-e, --default-view and -o need --repair.")

    try:
        if args.check:
            check_s3_datasets(args.s3_remote)
        elif args.generate == "local":
            generate_local_project(args.mobie_project)
        elif args.generate == "s3":
            generate_s3_project(args.mobie_project, args.s3_remote)
        elif args.repair:
            repair_s3_dataset(args.mobie_project, args.s3_remote, args.dataset, args.service_endpoint,
                              args.default_view, args.output)
        else:
            # The dataset.json comes first, so that the project.json lists the dataset only with its sources.
            if args.source:
                add_s3_sources(args.mobie_project, args.s3_remote, args.dataset, args.source)
            add_s3_dataset(args.mobie_project, args.s3_remote, args.dataset)
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(str(exc))


def main_add_processing_metadata():
    parser = argparse.ArgumentParser(
        description="Add the cochlea, the stains and the model keys of each job, e.g. 'cochlea': 'G_EK_000233_L', "
        "'stains': {\"IHC\": \"Vglut3\"} and 'models': {\"IHC\": \"IHC_v11b\"}, to its metadata.json. The deploy "
        "step writes them for a new job. An older job gets them from the names in its archived sbatch.sbatch, from "
        "the other jobs of its archive folder, or from the other steps of its pipeline run. A synapse folder holds "
        "only the version part, e.g. v3, so its key comes from the settings file. A job keeps existing entries.")

    parser.add_argument("archive_dir", type=str, help="Archive directory, which holds the archive folders.")
    parser.add_argument("-s", "--settings", type=str, default=project_settings_file(PROJECT),
                        help="Settings file of cochlea-net. Its 'models' give the key of a version part. "
                             "Default: %(default)s")
    parser.add_argument("-p", "--pattern", type=str, default=None,
                        help="Update only the archive folders whose path relative to archive_dir contains this "
                             "text. The text can hold wildcards. The other folders still give the versions of "
                             "their runs.")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="Print the new entries of each job, but write nothing.")
    args = parser.parse_args()

    try:
        add_processing_metadata(args.archive_dir, load_settings(args.settings), args.pattern, args.dry_run)
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(str(exc))


def main_convert():
    parser = argparse.ArgumentParser(
        description="Convert the raw data of one cochlea for the stitching in one command: download the raw data "
        "from UKON, check it, convert it in a Slurm job, upload the n5 and the XML to UKON and, with --remove-raw, "
        "remove the raw data on the cluster. Run it in a tmux session on a login node; outside tmux or screen it "
        "stops. It asks for the GWDG password once, keeps it only in memory and checks it at once. The file "
        "convert_pipeline.json in the cochlea folder records the finished stages, so the same command continues "
        "after an interruption. Run it in the folder of the slurm output files, as the deploy step.")

    parser.add_argument("-j", "--params", type=str, required=True,
                        help="Parameter file of the template convert_data, with the key 'ukon_dir': the sample "
                             "folder on UKON, which holds the raw folder, e.g. "
                             "UKON100/archiv/imaging/Lightsheet/Huiskengroup_CTLSM/<year>/<experimenter>/<sample>.")
    parser.add_argument("-s", "--settings", type=str, default=None,
                        help="Settings file of cochlea-net. Default: project_settings/cochlea-net.json")
    parser.add_argument("-u", "--username", type=str, default=None,
                        help="GWDG username. Default: academic_id of the settings file.")
    parser.add_argument("--remove-raw", dest="remove_raw", action="store_true",
                        help="Remove the raw folder on the cluster after the upload of the n5 and the XML.")
    parser.add_argument("--poll-interval", dest="poll_interval", type=float, default=300,
                        help="Seconds between two queries of the job state. Default: %(default)s")
    parser.add_argument("--max-resubmits", dest="max_resubmits", type=int, default=2,
                        help="Number of resubmissions after a TIMEOUT, a node failure or a preemption. "
                             "Default: %(default)s")
    parser.add_argument("--force", action="store_true", help="Pass --force to the deploy step.")
    parser.add_argument("--allow-dirty", dest="allow_dirty", action="store_true",
                        help="Submit the conversion although a tracked repository has uncommitted changes. "
                             "Without it, the command stops before the password prompt.")
    parser.add_argument("--takeover", action="store_true",
                        help="Continue although the state file names a driver on another login node, e.g. after a "
                             "reboot of that node.")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="Print the stages and run the deploy step without --submit. Nothing is transferred "
                             "or submitted.")
    parser.add_argument("--allow-no-tmux", dest="allow_no_tmux", action="store_true",
                        help="Run although the command does not run in tmux, screen or another terminal "
                             "multiplexer. It then stops when the connection to the cluster ends.")
    args = parser.parse_args()

    try:
        convert_cochlea(args.params, settings_file=args.settings, username=args.username,
                        remove_raw=args.remove_raw, poll_interval=args.poll_interval,
                        max_resubmits=args.max_resubmits, force=args.force, allow_dirty=args.allow_dirty,
                        takeover=args.takeover, dry_run=args.dry_run, allow_no_tmux=args.allow_no_tmux)
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(str(exc))
