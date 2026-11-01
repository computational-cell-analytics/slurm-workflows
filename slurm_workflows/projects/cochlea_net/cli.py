#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Command line interface of `slurm_wf.add_s3_dataset`, which only the project cochlea-net uses.
"""
import argparse
import sys

from slurm_workflows.projects.cochlea_net.mobie import (add_s3_dataset, add_s3_sources, check_s3_datasets,
                                                        generate_local_project, generate_s3_project,
                                                        repair_s3_dataset)


def main_add_s3_dataset():
    parser = argparse.ArgumentParser(
        description="Add a dataset to the MoBIE project in the S3 bucket: its name to the project.json and, with "
        "--source, its sources to its dataset.json. The S3 files list the datasets and the sources of every local "
        "MoBIE project, so only the new entries are added and all other entries stay unchanged. Other modes "
        "rebuild the dataset list of a project.json, check the dataset.json files of the bucket, or repair one.")

    parser.add_argument("-p", "--mobie-project", type=str, required=True,
                        help="Root of the local MoBIE project. --source reads the local dataset.json from it. "
                             "Each upload edits a temporary copy of the S3 file in it: tmp_<JobID>_<file name>, "
                             "or tmp_<file name> outside of a Slurm job.")
    parser.add_argument("-r", "--s3-remote", type=str, default=None,
                        help="rclone remote and bucket, in the form remote:bucket. Not needed for --generate local.")

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
                        help="URL of the S3 service, e.g. https://s3.fs.gwdg.de. Needed for --repair.")
    parser.add_argument("--default-view", type=str, default=None,
                        help="With --repair: the source whose view becomes the default view of the dataset.")
    parser.add_argument("-o", "--output", type=str, default=None,
                        help="With --repair: write the repaired dataset.json to this file and upload nothing.")
    args = parser.parse_args()

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
