#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2026

Logic of `slurm_wf.add_processing_metadata`: add the cochlea, the stains and the model keys of each job
to the metadata.json of an archive. The deploy step writes them for a new job. An older job gets them
from the names in its archived script, from the other jobs of its archive folder, or from the other
steps of its pipeline run.
"""
import os
import re

from slurm_workflows.metadata import METADATA_FILE, SBATCH_FILE, archive_folders, archived_jobs, write_metadata
from slurm_workflows.projects.cochlea_net.deployment import (GROUPS, cochlea_parts, parse_prediction_name,
                                                             short_cochlea_name, version_part)
from slurm_workflows.projects.cochlea_net.mobie import OME_ZARR

# Keys of metadata.json, in the order of the deploy step after 'project'.
PROCESSING_KEYS = ("cochlea", "stains", "models")

# Order of the model groups in metadata.json. The synapse jobs name their own model before the IHC
# model, as `job_models()` does.
MODEL_GROUPS = [GROUPS["synapses"]["models"]] + [spec["models"] for name, spec in GROUPS.items()
                                                 if name != "synapses"]

# A prediction folder without a stain prefix holds the default stain of its group.
DEFAULT_STAINS = {spec["models"]: spec["stain"] for spec in GROUPS.values()}

# A prediction folder or a cochlea is a part of a path, the value of an assignment, an argument, or a
# value of inline JSON. A quote inside a word stays, so that a word with a shell variable, e.g.
# 'IHC_v11-"$VERSION"', is no name.
TOKEN_SEPARATORS = re.compile(r"[/\s=,;:(){}\[\]]+")
QUOTES = "\"'"

# A run of leading zeros of a number, e.g. '000' of 'MAMD000137L'.
LEADING_ZEROS = re.compile(r"(?<![0-9])0+(?=[0-9])")


def script_tokens(
    sbatch_file: str,
) -> list:
    """Return the words of a script. Comment lines are skipped, so that the text of a template cannot add a name.

    Args:
        sbatch_file: Path of the archived sbatch script.

    Returns:
        list: The words, split at `TOKEN_SEPARATORS`, without the quotes at their ends.
    """
    with open(sbatch_file, "rt", encoding="utf8", errors="ignore") as myfile:
        lines = [line for line in myfile if not line.lstrip().startswith("#")]
    return [token.strip(QUOTES) for token in TOKEN_SEPARATORS.split("".join(lines))]


def script_processing(
    tokens: list,
) -> dict:
    """Collect the model versions and the stains which the prediction folders of a script name.

    Args:
        tokens: Output of `script_tokens()`.

    Returns:
        dict: Per model group, the dictionary {"parts": set, "keys": set, "stains": set} of the version
            parts, the full model keys and the stains.
    """
    found = {}
    for token in tokens:
        if token.endswith(OME_ZARR):
            token = token[:-len(OME_ZARR)]
        for group, fields in parse_prediction_name(token).items():
            entry = found.setdefault(group, {"parts": set(), "keys": set(), "stains": set()})
            entry["parts"].add(fields["part"])
            if fields["key"] is not None:
                entry["keys"].add(fields["key"])
            entry["stains"].add(fields["stain"] or DEFAULT_STAINS[group])
    return found


def script_cochleae(
    tokens: list,
    short_name: str,
) -> set:
    """Return the cochleae of a script whose short form is the cochlea part of the archive folder.

    The folder names from before 2026-11-20 have no version suffix of the cochlea, e.g. 'MAMDN163L' for
    'M_AMD_N163_L_v2'. A folder of a job which was submitted by hand can keep the leading zeros, e.g.
    'MAMD000137L'. Both forms match.

    Args:
        tokens: Output of `script_tokens()`.
        short_name: The last part of the name of the archive folder, e.g. 'MAMD137L'.

    Returns:
        set: The full names of the cochlea, e.g. {'M_AMD_000137_L'}.
    """
    short_name = LEADING_ZEROS.sub("", short_name)
    cochleae = set()
    for token in tokens:
        try:
            species, experimenter, number, side, _ = cochlea_parts(token)
        except ValueError:
            continue
        forms = (short_cochlea_name(token), f"{species}{experimenter}{number}{side}")
        if short_name in {LEADING_ZEROS.sub("", form) for form in forms}:
            cochleae.add(token)
    return cochleae


def pipeline_runs(
    jobs: list,
) -> dict:
    """Join the steps of each pipeline run.

    A step names the first step of its run in `pipeline.run`, and its previous step in
    `pipeline.depends_on`. Older metadata has only `depends_on`, so the chain of these links joins
    the run.

    Args:
        jobs: Output of `archived_jobs()`.

    Returns:
        dict: The JobID of each job, mapped to one JobID of its run.
    """
    parent = {}

    def root(jobid):
        parent.setdefault(jobid, jobid)
        while parent[jobid] != jobid:
            parent[jobid] = parent[parent[jobid]]
            jobid = parent[jobid]
        return jobid

    for _, _, metadict in jobs:
        jobid = metadict.get("jobid")
        if not jobid:
            continue
        root(jobid)
        pipeline = metadict.get("pipeline") or {}
        for key in ("run", "depends_on"):
            if pipeline.get(key):
                parent[root(jobid)] = root(pipeline[key])

    return {jobid: root(jobid) for jobid in list(parent)}


def run_processing(
    evidence: dict,
    settings: dict,
) -> tuple:
    """Return the stain and the model key of each model group of a run.

    A key from a script wins. A synapse prediction folder holds only the version part, so the key comes
    from the settings file: the one key of the model group with that version part. A synapse folder
    holds the IHC stain in lower case, so the stains are compared without case, and a spelling with
    capitals wins.

    Args:
        evidence: Output of `script_processing()`, merged over the steps of the run.
        settings: Settings of cochlea-net, with the key 'models'.

    Returns:
        tuple of dict: the stain and the model key of each model group, in the order of `MODEL_GROUPS`.

    Raises:
        ValueError: If a model group has two versions or two stains, or if the settings file gives no
            single key.
    """
    stains = {}
    models = {}
    for group in MODEL_GROUPS:
        if group not in evidence:
            continue
        parts, keys = evidence[group]["parts"], evidence[group]["keys"]
        if len(parts) > 1:
            raise ValueError(f"the scripts name more than one {group} model: {', '.join(sorted(parts))}")
        if len(keys) > 1:
            raise ValueError(f"the scripts name more than one {group} model key: {', '.join(sorted(keys))}")

        spellings = {}
        for stain in evidence[group]["stains"]:
            spellings.setdefault(stain.lower(), set()).add(stain)
        if len(spellings) > 1:
            raise ValueError(f"the scripts name more than one {group} stain: "
                             f"{', '.join(sorted(evidence[group]['stains']))}")
        [variants] = spellings.values()
        stains[group] = max(sorted(variants), key=lambda stain: stain != stain.lower())

        if keys:
            models[group] = next(iter(keys))
            continue
        part = next(iter(parts))
        candidates = sorted(key for key in settings.get("models", {}).get(group, {}) if version_part(key) == part)
        if len(candidates) != 1:
            raise ValueError(f"the settings file has {len(candidates)} {group} model keys with the version part "
                             f"'{part}', not one" + (f": {candidates}" if candidates else ""))
        models[group] = candidates[0]

    return stains, models


def with_entries(
    metadict: dict,
    entries: dict,
) -> dict:
    """Return the metadata with new entries after 'project', where the deploy step writes them.

    Args:
        metadict: Metadata of the job.
        entries: The new entries, in the order of `PROCESSING_KEYS`.

    Returns:
        dict: A copy of the metadata. Without 'project', the new entries come last.
    """
    if "project" not in metadict:
        return dict(metadict, **entries)

    result = {}
    for key, value in metadict.items():
        result[key] = value
        if key == "project":
            result.update(entries)
    return result


def add_processing_metadata(
    archive_dir: str,
    settings: dict,
    pattern: str = None,
    dry_run: bool = False,
):
    """Add the cochlea, the stains and the model keys to the metadata of each job which does not have them.

    The cochlea comes from the scripts of the archive folder. Every job of the folder gets it, also a
    backfilled job without a script. The stains and the model keys come from the scripts of the
    pipeline run. Every job of the archive contributes, also a job outside of `pattern`. A job keeps
    an existing entry. A run whose scripts name two versions or two stains of a model group, or whose
    version the settings file cannot resolve, gets no stains and no model keys.

    Args:
        archive_dir: Directory of the archive folders.
        settings: Settings of cochlea-net, with the key 'models'.
        pattern: Part of the name of the archive folders to update, or None for all.
        dry_run: Print the changes, but write nothing.
    """
    jobs = sorted(archived_jobs(archive_folders(archive_dir)), key=lambda job: job[1])
    selected = None if pattern is None else set(archive_folders(archive_dir, pattern))
    runs = pipeline_runs(jobs)

    def run_of(job_folder, metadict):
        return runs.get(metadict.get("jobid"), job_folder)

    evidence = {}
    cochleae = {}
    for folder, job_folder, metadict in jobs:
        sbatch_file = os.path.join(job_folder, SBATCH_FILE)
        if not os.path.isfile(sbatch_file):
            continue
        tokens = script_tokens(sbatch_file)
        run_evidence = evidence.setdefault(run_of(job_folder, metadict), {})
        for group, found in script_processing(tokens).items():
            entry = run_evidence.setdefault(group, {"parts": set(), "keys": set(), "stains": set()})
            for field, values in found.items():
                entry[field] |= values
        short_name = os.path.basename(folder).rsplit("_", 1)[-1]
        cochleae.setdefault(folder, set()).update(script_cochleae(tokens, short_name))

    changed = 0
    without_cochlea = []
    without_models = []
    unknown_keys = set()
    for folder, job_folder, metadict in jobs:
        if selected is not None and folder not in selected:
            continue

        entries = {}
        names = cochleae.get(folder, set())
        if len(names) == 1:
            entries["cochlea"] = next(iter(names))
        elif names:
            print(f"Warning: {job_folder} gets no cochlea, because its scripts name {', '.join(sorted(names))}.")
        elif "cochlea" not in metadict:
            without_cochlea.append(job_folder)

        run_evidence = evidence.get(run_of(job_folder, metadict))
        if run_evidence:
            try:
                entries["stains"], entries["models"] = run_processing(run_evidence, settings)
            except ValueError as exc:
                print(f"Warning: {job_folder} gets no stains and model keys, because {exc}.")
        elif "models" not in metadict:
            without_models.append(job_folder)

        added = {}
        for key in PROCESSING_KEYS:
            if key not in entries:
                continue
            if key not in metadict:
                added[key] = entries[key]
            elif metadict[key] != entries[key]:
                print(f"Warning: {job_folder} keeps its {key} {metadict[key]}. Its scripts give {entries[key]}.")
        if not added:
            continue

        unknown_keys.update(f"{group} {key}" for group, key in added.get("models", {}).items()
                            if key not in settings.get("models", {}).get(group, {}))
        if not dry_run:
            write_metadata(with_entries(metadict, added), os.path.join(job_folder, METADATA_FILE))
        print(f"{'Would add' if dry_run else 'Added'} {added} to {job_folder}.")
        changed += 1

    print(f"{'Would add' if dry_run else 'Added'} processing keys to {changed} job(s).")
    for label, job_folders in (("cochlea", without_cochlea), ("model version", without_models)):
        if job_folders:
            print(f"No {label} found for {len(job_folders)} job(s):")
            for job_folder in job_folders:
                print(f"  {job_folder}")
    if unknown_keys:
        print("Note: the settings file has no model " + ", ".join(sorted(unknown_keys)) +
              ". The key comes from an archived script.")
