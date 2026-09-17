#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Script for extracting metadata from an sbatch script and storing it in a JSON file for better accessibility.
A file containing git repositories can be used as an argument to archive the current git hash of the repository.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from utils.metadata import LOG_FILE, METADATA_FILE, SBATCH_FILE  # noqa: E402
from utils.metadata import init_metadict, read_metadata, write_metadata  # noqa: E402
from utils.repositories import repository_status_to_dict  # noqa: E402
from utils.settings import DEFAULT_SETTINGS_FILE, USER_SETTINGS, load_settings  # noqa: E402
from utils.slurm import reportseff_from_jobid, sbatch_parameters_to_dict  # noqa: E402


def user_to_metadict(
    metadict: dict,
) -> None:
    """Add the identifiers of the person who ran the job to a dictionary containing metadata.

    A job which finished must still be archived, so an unusable settings file is reported and
    skipped instead of failing the archiving step.

    Args:
        metadict: Dictionary containing metadata for a slurm job.
    """
    try:
        settings = load_settings(DEFAULT_SETTINGS_FILE)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Warning: the HPC user is not recorded. {exc}")
        return

    for key in USER_SETTINGS:
        if settings[key]:
            metadict[key] = settings[key]


def main(
    input_dir: str,
    output_file: str = None,
    jobid: int = None,
    repository_file: str = None,
    overwrite: bool = False,
):
    """Extract metadata from an sbatch script.

    Args:
        input_dir: Input directory containing sbatch script and log file.
        output_file: Output file for metadata. Default: <input_dir>/metadata.json
        jobid: JobID of SBATCH script
        repository_file: Optional file containing repository information
        overwrite: Flag for overwriting metadata information
    """
    if output_file is None:
        output_file = os.path.join(input_dir, METADATA_FILE)
    else:
        output_file = os.path.abspath(output_file)

    sbatch_file = os.path.join(input_dir, SBATCH_FILE)
    log_file = os.path.join(input_dir, LOG_FILE)

    if os.path.isfile(output_file) and not overwrite:
        metadict = read_metadata(output_file)
        user_to_metadict(metadict)
        sbatch_parameters_to_dict(sbatch_file, metadict=metadict)
        reportseff_from_jobid(log_file, metadict=metadict, jobid=jobid)

    else:
        metadict = init_metadict(input_dir)

        user_to_metadict(metadict)
        sbatch_parameters_to_dict(sbatch_file, metadict=metadict)
        reportseff_from_jobid(log_file, metadict=metadict, jobid=jobid)
        if repository_file is not None:
            repository_status_to_dict(repository_file, metadict=metadict)

    write_metadata(metadict, output_file)


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description="Extract metadata from an sbatch script.")

    parser.add_argument('input_dir', type=str, help="Input directory containing sbatch script.")

    parser.add_argument('-o', "--output", type=str, default=None,
                        help="Output file for metadata. Default: <input_dir>/metadata.json")
    parser.add_argument('-j', "--jobid", type=str, default=None, help="JobID")
    parser.add_argument('-r', "--repository_file",
                        type=str, default=None,
                        help="File with information about git repositories in format '<Name>\t<path-to-repository>\n'")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing JSON file.")
    args = parser.parse_args()

    try:
        main(args.input_dir, args.output, args.jobid, args.repository_file, args.overwrite)
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(str(exc))
