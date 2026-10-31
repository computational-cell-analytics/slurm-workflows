#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2026

Temporary script: rename the keys of every `<archive_dir>/<folder>/metadata.json` to lower case with
underscores. Run it once on each job archive, then delete it. A second run changes nothing.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from slurm_workflows.metadata import METADATA_FILE, archive_folders, read_metadata, write_metadata  # noqa: E402

KEYS = {
    "job-name": "job_name",
    "cpus-per-task": "cpus_per_task",
    "Memory-per-node": "memory_per_node",
    "mail-user": "mail_user",
    "Job array": "job_array",
    "Reportseff": "reportseff",
    "SlurmOutput": "slurm_output",
    "Repositories": "repositories",
    "Project": "project",
    "Pipeline": "pipeline",
}

REPORTSEFF_KEYS = {
    "JobID": "jobid",
    "State": "state",
    "Elapsed": "elapsed",
    "TimeEff": "time_eff",
    "CPUEff": "cpu_eff",
    "MemEff": "mem_eff",
}


def rename(
    entry: dict,
    keys: dict,
) -> dict:
    # If an old and a new key are both present, the later one wins, which is the newer value.
    return {keys.get(key, key): value for key, value in entry.items()}


def migrate(
    metadict: dict,
) -> dict:
    """Return the metadata of one job with the new keys. Unknown keys stay.

    Args:
        metadict: Metadata of the job.

    Returns:
        dict: The metadata with the new keys, in the same order.
    """
    migrated = rename(metadict, KEYS)

    reports = migrated.get("reportseff")
    if isinstance(reports, dict):
        migrated["reportseff"] = rename(reports, REPORTSEFF_KEYS)
    elif isinstance(reports, list):
        migrated["reportseff"] = [rename(report, REPORTSEFF_KEYS) for report in reports]

    return migrated


def main():
    parser = argparse.ArgumentParser(
        description="Rename the keys of every metadata.json in the subfolders of a job archive to lower case "
        "with underscores.")
    parser.add_argument("archive_dir", type=str, help="Directory of the archive folders.")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="Print the files which would change, and change nothing.")
    args = parser.parse_args()

    changed = 0
    for folder in sorted(archive_folders(args.archive_dir)):
        metadata_file = os.path.join(folder, METADATA_FILE)
        if not os.path.isfile(metadata_file):
            continue

        metadict = read_metadata(metadata_file)
        migrated = migrate(metadict)
        if list(migrated.items()) == list(metadict.items()):
            continue

        changed += 1
        print(metadata_file)
        if not args.dry_run:
            # The new file replaces the old one only when it is complete.
            write_metadata(migrated, f"{metadata_file}.tmp")
            os.replace(f"{metadata_file}.tmp", metadata_file)

    print(f"{'Would change' if args.dry_run else 'Changed'} {changed} metadata file(s).")


if __name__ == "__main__":
    main()
