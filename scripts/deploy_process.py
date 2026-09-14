#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Script for executing pre-defined Slurm jobs by giving a set of parameters which are filled into an sbatch script.
The sbatch script is executed and can be archived.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from utils.inputs import (SEGMENTATION_INPUT, check_external_input, check_job_input,  # noqa: E402
                          check_output_absent, job_variables, path_exists, resolve_input)
from utils.metadata import METADATA_FILE, read_metadata, write_metadata  # noqa: E402
from utils.pipelines import (GROUP_KEY, load_pipeline, pipeline_names, pipeline_steps,  # noqa: E402
                             step_template, steps_from)
from utils.repositories import write_repository_file  # noqa: E402
from utils.settings import DEFAULT_SETTINGS_FILE, get_model_path, load_settings, settings_to_replacements  # noqa: E402
from utils.templates import replace_substrings_in_file  # noqa: E402

SCRIPTS_DIR = os.path.dirname(os.path.realpath(__file__))

RUN_SBATCH_SCRIPT = os.path.join(SCRIPTS_DIR, "01_run_sbatch.sh")

# Printed by 01_run_sbatch.sh for the JobID of the submitted job.
JOBID_PREFIX = "SUBMITTED_JOBID="

# Watershed defaults, used if the model directory has no 'best_best_params.json'.
WATERSHED_DEFAULTS = {
    # default for SGN_v2
    "SGN": {"center_distance_threshold": "0.4",
            "boundary_distance_threshold": "0.5",
            "distance_smoothing": "0"},
    # default for IHC_v4b
    "IHC": {"center_distance_threshold": "0.5",
            "boundary_distance_threshold": "0.6",
            "distance_smoothing": "0.6"},
}

# One entry per group of jobs. 'stain' is the default of the 'stain_<group>' parameter, 'version'
# names the parameter which selects the model, and 'models' is the group of the settings file.
GROUPS = {
    "SGN": {"stain": "PV", "version": "sgn_version", "models": "SGN", "masking": "sgn"},
    "IHC": {"stain": "Vglut3", "version": "ihc_version", "models": "IHC", "masking": "ihc"},
    "synapses": {"stain": "CTBP2", "version": "synapse_version", "models": "Synapses", "masking": None},
}

# Model version which is used if 'synapse_version' is not a parameter of the job.
DEFAULT_SYNAPSE_VERSION = "synapses_v3"


def watershed_parameters(
    model_path: str,
    group: str,
) -> dict:
    """Read the watershed parameters of a trained model.

    Args:
        model_path: Path of the trained model.
        group: Model group, 'SGN' or 'IHC'.

    Returns:
        dict: Watershed parameters. The defaults of the group are used if the model has no parameter file.
    """
    parameter_file = os.path.join(model_path, "best_best_params.json")

    if not os.path.exists(parameter_file):
        return dict(WATERSHED_DEFAULTS.get(group, {}))

    with open(parameter_file, "r") as myfile:
        parameters = json.load(myfile)["params"]

    print(f"Loaded cached best params from {parameter_file}")

    return {key: str(parameters[key]) for key in
            ("center_distance_threshold", "boundary_distance_threshold", "distance_smoothing")}


def template_group(
    input_file: str,
) -> str:
    """Return the group of a template.

    Args:
        input_file: Path of the template.

    Returns:
        str: Key of `GROUPS`, or None for a template which reads every stain, such as a MoBIE step.
    """
    template_name = os.path.basename(input_file)

    if "SGN" in template_name:
        return "SGN"
    if "IHC" in template_name:
        return "IHC"
    if "synapse" in template_name:
        return "synapses"

    return None


def n5_name(
    prefix: str,
    number: str,
    side: str,
    stains: list,
    version: str,
) -> str:
    """Return the file name of the n5 written by the initial processing.

    Args:
        prefix: Animal and person of the cochlea name.
        number: Number of the cochlea, with leading zeros.
        side: Side of the cochlea.
        stains: Every stain of the n5, in the order of its setups.
        version: Version suffix of the cochlea, or an empty string.

    Returns:
        str: File name of the n5.
    """
    stains_str = "_".join(stains)

    return f"{prefix}_{number.lstrip('0')}{side}_{stains_str}_fused{version}.n5"


def prediction_name(
    group: str,
    stain: str,
    version: str,
) -> str:
    """Return the name of the prediction folder of one group.

    The stain is part of the name if it deviates from the default of the group. A prediction of a
    different stain must not overwrite the prediction of the default stain.

    Args:
        group: Key of `GROUPS`.
        stain: Stain of the job.
        version: Model version of the job.

    Returns:
        str: Name of the prediction folder.
    """
    if stain == GROUPS[group]["stain"]:
        return version

    return f"{stain}_{version}"


def marker_prediction_name(
    synapses_prediction: str,
    ihc_prediction: str,
) -> str:
    """Return the name of the prediction folder of the synapse marker step.

    The step matches the detections of 'synapse_detect' to one IHC segmentation, so its result
    depends on two model versions. Its own folder keeps it from overwriting the detection of the
    step before it, and keeps a run against another IHC segmentation from overwriting an earlier one.

    Args:
        synapses_prediction: Prediction folder of the synapse detection.
        ihc_prediction: Prediction folder of the IHC segmentation.

    Returns:
        str: Name of the prediction folder.
    """
    return f"{synapses_prediction}_{ihc_prediction}"


def build_replacements(
    settings: dict,
    parameters: dict,
    input_file: str,
    pipeline_group: str = None,
) -> tuple:
    """Build the placeholder replacements of one job.

    Args:
        settings: Output of `load_settings()`.
        parameters: Parameter dictionary of the job.
        input_file: Path of the template. Its name selects the watershed step, and the model
            group unless `pipeline_group` supplies it.
        pipeline_group: Group of the pipeline. It is used for a template which serves more than one
            group, such as a MoBIE export, and therefore has no group in its name.

    Returns:
        tuple of:
            dict — the placeholder replacements
            str  — the short cochlea name used in the output file name
    """
    # A parameter of the job overrides the corresponding setting.
    replacement_dict = settings_to_replacements(settings)
    replacement_dict.update(parameters)

    # The stain of every group is known to a template of any group, because a job can read the
    # result of another group. The mask of 'synapse_marker' is the IHC segmentation.
    for group_name, group_spec in GROUPS.items():
        replacement_dict.setdefault(f"stain_{group_name}", group_spec["stain"])

    cochlea = replacement_dict["cochlea"]
    replacement_dict["cochlea_job_name"] = "-".join(cochlea.split("_"))

    cochlea_content = cochlea.split("_")
    version = ""
    if len(cochlea_content) < 4:
        raise ValueError("Cochlea parameter does not have the correct format.")
    if len(cochlea_content) > 4:
        if cochlea_content[4][0] != "v":
            raise ValueError("Cochlea parameter does not have the correct format.")
        version = "_" + cochlea_content[4]
        print(f"Processing version {cochlea_content[4]} of cochlea {''.join(cochlea_content[:3])}.")

    animal = cochlea_content[0]
    person = cochlea_content[1]
    number = cochlea_content[2]
    side = cochlea_content[3]
    stains = replacement_dict.get("stains")

    prefix = "".join([animal, person])
    template_name = os.path.basename(input_file)
    group = template_group(input_file)
    if group is None:
        group = pipeline_group

    if group is None:
        # The MoBIE templates read every stain, so they always need the n5.
        if not stains:
            raise ValueError(f"The template {template_name} needs the 'stains' parameter, which lists every "
                             "stain of the n5 data.")
        replacement_dict["cochlea_data"] = n5_name(prefix, number, side, stains, version)
        replacement_dict["channel_multi"] = "_".join(stains)
        replacement_dict["input_key_multi"] = "_".join(
            [f"setup{index}/timepoint0/s0" for index in range(len(stains))])

    else:
        group_spec = GROUPS[group]
        stain = replacement_dict[f"stain_{group}"]

        # The n5 holds every stain, so its key selects the channel. A stain which the n5 does not
        # hold leaves the OME-Zarr of the S3 bucket as the only candidate.
        if stains and stain in stains:
            n5_candidate = n5_name(prefix, number, side, stains, version)
            n5_key = f"setup{stains.index(stain)}/timepoint0/s0"
        else:
            n5_candidate = None
            n5_key = None

        cochlea_dir = os.path.join(replacement_dict["data_dir"], cochlea)
        replacement_dict["cochlea_data"], replacement_dict["input_key"] = resolve_input(
            cochlea_dir, n5_candidate, n5_key, stain)

        if group_spec["masking"] is not None:
            replacement_dict["masking"] = group_spec["masking"]

        if group == "synapses":
            replacement_dict.setdefault(group_spec["version"], DEFAULT_SYNAPSE_VERSION)

        if group_spec["version"] not in replacement_dict:
            raise ValueError(f"The template {template_name} needs the '{group_spec['version']}' parameter.")

        replacement_dict["model"] = get_model_path(settings, group_spec["models"],
                                                   replacement_dict[group_spec["version"]])

    # The prediction folder of every group whose model version is known, so that a template can
    # name the result of another group.
    for group_name, group_spec in GROUPS.items():
        model_version = replacement_dict.get(group_spec["version"])
        if model_version is not None:
            replacement_dict[f"{group_name.lower()}_prediction"] = prediction_name(
                group_name, replacement_dict[f"stain_{group_name}"], model_version)

    if group is not None:
        replacement_dict["prediction_dir"] = replacement_dict[f"{group.lower()}_prediction"]

    # The folder of the marker step depends on two model versions. It is derived for every template,
    # so that a later step, such as the MoBIE export, can name the result of the marker step.
    if "synapses_prediction" in replacement_dict and "ihc_prediction" in replacement_dict:
        replacement_dict["marker_prediction"] = marker_prediction_name(
            replacement_dict["synapses_prediction"], replacement_dict["ihc_prediction"])

    if "marker" in template_name:
        if "marker_prediction" not in replacement_dict:
            raise ValueError(f"The template {template_name} needs the 'ihc_version' parameter, "
                             "which selects the IHC segmentation the detections are matched to.")
        replacement_dict["prediction_dir"] = replacement_dict["marker_prediction"]

    # 'mobie_add_segmentation' also contains 'segment', but it needs no model and no watershed.
    if template_name.startswith("segment"):
        replacement_dict.update(watershed_parameters(replacement_dict["model"], group))

    return replacement_dict, f"{prefix}{number.lstrip('0')}{side}"


def render_step(
    input_file: str,
    replacement_dict: dict,
    cochlea_short: str,
    group: str = None,
    allow_missing: bool = False,
) -> str:
    """Fill a template and write the sbatch script of one job.

    A template which serves more than one group has no group in its name. The group is added to the
    file name, so that the runs of two groups do not share an archive folder.

    Args:
        input_file: Path of the template.
        replacement_dict: Output of `build_replacements()`.
        cochlea_short: Output of `build_replacements()`.
        group: Group of the job, or None.
        allow_missing: Print a warning instead of raising for an unresolved placeholder.

    Returns:
        str: Name of the generated sbatch script.
    """
    script_str = os.path.basename(input_file).split(".template")[0]
    if group is not None and template_group(input_file) is None:
        script_str = f"{script_str}_{group}"

    output_file = f"{str(date.today())}_sbatch_{script_str}_{cochlea_short}.sbatch"

    replace_substrings_in_file(input_file, output_file, replacement_dict, strict=not allow_missing)

    return output_file


def check_prediction_absent(
    input_file: str,
    output_file: str,
) -> list:
    """Return a message if an apply step would write into an existing prediction.

    The blocks of the prediction are split over the tasks of the job array, so a second run over an
    existing 'predictions.zarr' leaves a mix of two runs which no later step can detect.

    Args:
        input_file: Path of the template.
        output_file: Path of the generated sbatch script.

    Returns:
        list: Warning messages. The list is empty if the step may run.
    """
    if "apply" not in os.path.basename(input_file):
        return []

    output_folder = job_variables(output_file).get("OUTPUT_FOLDER")
    if output_folder is None:
        return []

    prediction = os.path.join(output_folder, SEGMENTATION_INPUT)
    if not path_exists(prediction):
        return []

    return [f"the prediction of a previous run exists: {prediction}"]


def submit_step(
    output_file: str,
    archive_dir: str,
    repository_file: str,
    dependency: str = None,
) -> str:
    """Submit one job through `01_run_sbatch.sh` and return its JobID.

    Args:
        output_file: Path of the generated sbatch script.
        archive_dir: Directory to archive the job in. No archive is written for an empty value.
        repository_file: File listing the git repositories to snapshot.
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
    cmd += ["-r", repository_file, output_file]

    print(" ".join(cmd))
    # 'capture_output' and 'text' need Python 3.7. The login node of the cluster has Python 3.6.
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)

    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)

    if result.returncode != 0:
        raise ValueError(f"Submission of {output_file} failed with exit code {result.returncode}.")

    for line in result.stdout.splitlines():
        if line.startswith(JOBID_PREFIX):
            return line[len(JOBID_PREFIX):].strip()

    raise ValueError(f"No JobID was reported for {output_file}.")


def write_pipeline_block(
    archive_dir: str,
    output_file: str,
    pipeline_name: str,
    step_index: int,
    step_count: int,
    dependency: str = None,
) -> None:
    """Add the pipeline information to the archived metadata of one step.

    Args:
        archive_dir: Directory the job was archived in.
        output_file: Name of the generated sbatch script.
        pipeline_name: Name of the pipeline.
        step_index: Position of the step in the pipeline, starting at 1.
        step_count: Number of steps of the pipeline.
        dependency: JobID of the previous step, if there is one.
    """
    suffix = os.path.basename(output_file).split(".sbatch")[0].replace("_sbatch_", "_", 1)
    metadata_file = os.path.join(archive_dir, suffix, METADATA_FILE)

    if not os.path.isfile(metadata_file):
        print(f"Warning: no metadata found in {metadata_file}. The pipeline information is not stored.")
        return

    metadict = read_metadata(metadata_file)
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
    write_repository_file(settings["repositories"], repository_file)

    return repository_file, repository_file


def main(
    json_file: str,
    settings_file: str,
    archive_dir: str,
    repository_file: str,
    run_script: bool,
    input_file: str = None,
    pipeline: str = None,
    start_at: str = None,
    allow_missing: bool = False,
    force: bool = False,
    group: str = None,
    no_mobie: bool = False,
):
    settings = load_settings(settings_file)

    with open(json_file, "r") as myfile:
        parameters = json.load(myfile)

    if pipeline is None:
        steps = [input_file]
        pipeline_name = None
        description = None
        skipped = []
    else:
        definition = load_pipeline(pipeline)
        pipeline_name = definition["name"]
        description = definition.get("description")
        if group is None:
            group = definition.get(GROUP_KEY)
        # The steps are resolved before '--start-at', so that a MoBIE step can be resumed.
        step_names, skipped = pipeline_steps(definition, settings.get("mobie_project"), no_mobie)
        if start_at is not None:
            step_names = steps_from(step_names, start_at)
        steps = [step_template(step) for step in step_names]

    if group is not None and group not in GROUPS:
        raise ValueError(f"Unknown group '{group}'. Available groups: {sorted(GROUPS)}.")

    if description is not None:
        print(f"Pipeline {pipeline_name}: {description}")

    if skipped:
        reason = ("--no-mobie was given" if no_mobie
                  else "the settings file names no 'mobie_project'")
        print(f"The MoBIE steps are skipped, because {reason}: " + ", ".join(skipped) + ".")

    # Render every step before anything is submitted, so that an unresolved placeholder of a later
    # step cannot leave the earlier steps of the chain queued.
    output_files = []
    for step_file in steps:
        replacement_dict, cochlea_short = build_replacements(settings, parameters, step_file, group)
        output_files.append(render_step(step_file, replacement_dict, cochlea_short, group, allow_missing))

    # Only the first step can be checked here. The input of a later step is produced by its
    # predecessor, so it is verified by the guard inside the job script.
    warnings = check_job_input(output_files[0])
    for step_file, output_file in zip(steps, output_files):
        warnings += check_external_input(output_file)
        warnings += check_output_absent(output_file)
        warnings += check_prediction_absent(step_file, output_file)

    for message in warnings:
        print(f"Warning: {message}")

    if len(output_files) > 1:
        for output_file in output_files[1:]:
            print(f"The input of {output_file} is verified when the job runs.")

    if not run_script:
        if pipeline is None:
            print(replacement_dict)
        else:
            print("The pipeline is not deployed. Use --deploy to submit the chain.")
        return

    if warnings and not force:
        raise ValueError("The input data of the job does not exist, or a previous run would be overwritten. "
                         "Use --force to submit the job anyway.")

    if archive_dir is None:
        archive_dir = settings.get("archive_dir")

    repository_file, temporary_repository_file = repository_file_of(settings, repository_file)

    try:
        dependency = None
        for step_index, output_file in enumerate(output_files, start=1):
            jobid = submit_step(output_file, archive_dir, repository_file, dependency)
            print(f"Step {step_index} of {len(output_files)} submitted as JobID {jobid}.")

            if pipeline_name is not None and archive_dir:
                write_pipeline_block(archive_dir, output_file, pipeline_name,
                                     step_index, len(output_files), dependency)

            dependency = jobid
    finally:
        if temporary_repository_file is not None:
            os.remove(temporary_repository_file)


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description="Fill in a template for an sbatch script using a JSON dictionary. "
        "The new sbatch script is deployed to the cluster and an entry in the archive directory is created. "
        "A pipeline fills in one template per step and submits the steps as a chain of Slurm jobs, "
        "where each step starts only after its predecessor completed successfully.")

    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("-i", "--input", type=str, default=None, help="Input template of a single job.")
    source.add_argument("-p", "--pipeline", type=str, default=None,
                        help=f"Name of a pipeline to submit as a chain of jobs. Available: {pipeline_names()}")

    parser.add_argument("-j", "--json", type=str, required=True, help="JSON dictionary.")

    parser.add_argument("-s", "--settings", type=str, default=DEFAULT_SETTINGS_FILE,
                        help="JSON file with the paths and names of your account. Default: utils/settings.json")
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
                        help="Deploy the job even if the input data does not exist, if a previous "
                             "prediction would be overwritten, or if a MoBIE table would be rebuilt.")
    parser.add_argument("--no-mobie", dest="no_mobie", action="store_true",
                        help="Leave the MoBIE steps of a pipeline out, to process a cochlea without "
                             "exporting the result. Needs -p.")
    parser.add_argument("--group", type=str, default=None,
                        help="Group of a template which serves more than one group, such as a MoBIE "
                             "export. It overrides the group of the pipeline. "
                             f"Available: {sorted(GROUPS)}")

    args = parser.parse_args()

    if args.start_at is not None and args.pipeline is None:
        parser.error("--start-at needs a pipeline. Use it together with -p.")

    try:
        main(args.json, args.settings, args.archive_dir, args.repository_file, args.deploy,
             args.input, args.pipeline, args.start_at, args.allow_missing, args.force, args.group,
             args.no_mobie)
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(str(exc))
