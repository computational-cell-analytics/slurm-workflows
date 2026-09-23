#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Deployment of the jobs of the cochlea-net project: segmentation of SGNs and IHCs, detection of
ribbon synapses, and the export of the results to MoBIE and the S3 bucket.
"""
import json
import os

from utils.inputs import job_variables, path_exists
from utils.pipelines import missing_templates

PROJECT = "cochlea-net"

# Written by the apply step. It is the input of the segmentation step.
SEGMENTATION_INPUT = "predictions.zarr"

# Scale level of an OME-Zarr transferred from the S3 bucket. An n5 of the initial processing uses
# 'setup<n>/timepoint0/s0' instead, because it holds every stain in one file.
OME_ZARR_KEY = "s0"

REQUIRED_SETTINGS = ("data_dir", "models")

# Steps of a pipeline which export the result to MoBIE. They need a MoBIE project to write into.
MOBIE_STEPS_KEY = "mobie_steps"

# Key of the group which a step without a group in its template name belongs to.
GROUP_KEY = "group"

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


def add_arguments(
    parser,
) -> None:
    """Add the options of the project to the parser of `deploy_process.py`.

    Args:
        parser: `argparse.ArgumentParser` of `deploy_process.py`.
    """
    parser.add_argument("--no-mobie", dest="no_mobie", action="store_true",
                        help="Leave the MoBIE steps of a pipeline out, to process a cochlea without "
                             "exporting the result. Needs -p.")
    parser.add_argument("--group", type=str, default=None,
                        help="Group of a template which serves more than one group, such as a MoBIE "
                             "export. It overrides the group of the pipeline. "
                             f"Available: {sorted(GROUPS)}")


def select_steps(
    definition: dict,
    settings: dict,
    args,
) -> list:
    """Return the steps of a pipeline to submit.

    The MoBIE steps need a project to write into. Without one they are dropped instead of failing,
    so a pipeline runs on an account which uses no MoBIE project. '--no-mobie' drops them for a
    single run, because the processing of a cochlea does not depend on the export.

    Args:
        definition: Output of `load_pipeline()`.
        settings: Output of `load_settings()`.
        args: Parsed command line options.

    Returns:
        list: The steps to submit, in order.
    """
    steps = list(definition["steps"])
    mobie_steps = definition.get(MOBIE_STEPS_KEY, [])

    if not isinstance(mobie_steps, list):
        raise ValueError(f"Pipeline {definition['name']} needs a list of steps for '{MOBIE_STEPS_KEY}'.")

    missing = missing_templates(PROJECT, mobie_steps)
    if missing:
        raise ValueError(f"Pipeline {definition['name']} refers to steps without a template: {missing}.")

    if settings.get("mobie_project") and not args.no_mobie:
        return steps + mobie_steps

    if mobie_steps:
        reason = ("--no-mobie was given" if args.no_mobie
                  else "the settings file names no 'mobie_project'")
        print(f"The MoBIE steps are skipped, because {reason}: " + ", ".join(mobie_steps) + ".")

    return steps


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
    """Return the name of the prediction folder of the synapse jobs.

    The prediction runs on the dilated IHC segmentation and the detections are matched to it, so the
    result depends on two model versions. The joined name keeps a run against another IHC
    segmentation from overwriting an earlier one.

    Args:
        synapses_prediction: Prediction folder of the synapse detection.
        ihc_prediction: Prediction folder of the IHC segmentation.

    Returns:
        str: Name of the prediction folder.
    """
    return f"{synapses_prediction}_{ihc_prediction}"


def resolve_input(
    cochlea_dir: str,
    n5_name: str,
    n5_key: str,
    stain: str,
) -> tuple:
    """Return the file name of the job input and the matching input key.

    The n5 of the initial processing wins. The OME-Zarr transferred from the S3 bucket is the
    fallback, because the n5 is deleted after a cochlea is processed. If neither exists, the n5 is
    reported, so that `check_job_input()` names the familiar path.

    Args:
        cochlea_dir: Directory of the cochlea.
        n5_name: File name of the n5, or None if the stain list is unknown.
        n5_key: Input key of the n5.
        stain: Stain of the job. It names the OME-Zarr file.

    Returns:
        tuple of str: the file name of the input, and its input key.
    """
    ome_zarr_name = f"{stain}.ome.zarr"

    if n5_name is None:
        return ome_zarr_name, OME_ZARR_KEY

    if path_exists(os.path.join(cochlea_dir, n5_name)):
        return n5_name, n5_key

    if path_exists(os.path.join(cochlea_dir, ome_zarr_name)):
        return ome_zarr_name, OME_ZARR_KEY

    return n5_name, n5_key


def get_model_path(
    settings: dict,
    group: str,
    version: str,
) -> str:
    """Look up the path of a trained model in the settings.

    Args:
        settings: Output of `load_settings()`.
        group: Model group, one of the keys of the 'models' entry.
        version: Model version within the group.

    Returns:
        str: Path of the trained model.
    """
    models = settings["models"].get(group, {})
    if version not in models:
        raise ValueError(f"Add missing model path. No match for {group} model: {version}. "
                         f"Available versions: {sorted(models)}.")
    return models[version]


def prepare_step(
    settings: dict,
    replacements: dict,
    input_file: str,
    args,
    definition: dict = None,
) -> tuple:
    """Add the placeholder replacements of one job, and name its sbatch script.

    Args:
        settings: Output of `load_settings()`.
        replacements: The settings and the job parameters as placeholder replacements. The dictionary
            is changed in place.
        input_file: Path of the template. Its name selects the watershed step, and the model
            group unless the pipeline or '--group' supplies it.
        args: Parsed command line options.
        definition: Output of `load_pipeline()`, or None for a single job.

    Returns:
        tuple of:
            dict — the placeholder replacements
            str  — the name of the sbatch script, without the date and the extension
    """
    missing = [key for key in REQUIRED_SETTINGS if key not in settings]
    if missing:
        raise ValueError(f"The settings file is missing the keys: {missing}.")

    # A template which serves more than one group, such as a MoBIE export, has no group in its
    # name. The pipeline or '--group' supplies it.
    pipeline_group = args.group
    if pipeline_group is None and definition is not None:
        pipeline_group = definition.get(GROUP_KEY)
    if pipeline_group is not None and pipeline_group not in GROUPS:
        raise ValueError(f"Unknown group '{pipeline_group}'. Available groups: {sorted(GROUPS)}.")

    replacement_dict = replacements

    # The stain of every group is known to a template of any group, because a job can read the
    # result of another group. The mask of the synapse jobs is the IHC segmentation.
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

    # The folder of the synapse jobs depends on two model versions. It is derived for every template,
    # so that a later step, such as the MoBIE export, can name their result.
    if "synapses_prediction" in replacement_dict and "ihc_prediction" in replacement_dict:
        replacement_dict["marker_prediction"] = marker_prediction_name(
            replacement_dict["synapses_prediction"], replacement_dict["ihc_prediction"])

    # The prediction is masked with the IHC segmentation, so every step of the group depends on the
    # IHC model version as well and writes into the joined folder.
    if group == "synapses":
        if "marker_prediction" not in replacement_dict:
            raise ValueError(f"The template {template_name} needs the 'ihc_version' parameter, "
                             "which selects the IHC segmentation the prediction is masked with.")
        replacement_dict["prediction_dir"] = replacement_dict["marker_prediction"]

    # 'mobie_add_segmentation' also contains 'segment', but it needs no model and no watershed.
    if template_name.startswith("segment"):
        replacement_dict.update(watershed_parameters(replacement_dict["model"], group))

    # The group enters the name of a template which serves more than one group, so that the runs
    # of two groups do not share an archive folder.
    script_str = template_name.split(".template")[0]
    if pipeline_group is not None and template_group(input_file) is None:
        script_str = f"{script_str}_{pipeline_group}"

    return replacement_dict, f"{script_str}_{prefix}{number.lstrip('0')}{side}"


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


def check_steps(
    input_files: list,
    output_files: list,
) -> list:
    """Return the warnings of the project for the steps of a chain.

    A job without an INPUT is a segmentation job. Its input is the prediction of the apply step
    inside OUTPUT_FOLDER. Only the first step is checked for it, because the input of a later step
    is produced by its predecessor.

    Args:
        input_files: Paths of the templates.
        output_files: Paths of the generated sbatch scripts, in the same order.

    Returns:
        list: Warning messages. The list is empty if every step may run.
    """
    messages = []

    variables = job_variables(output_files[0])
    output_folder = variables.get("OUTPUT_FOLDER")
    if "INPUT" not in variables and output_folder is not None:
        prediction = os.path.join(output_folder, SEGMENTATION_INPUT)
        if not path_exists(prediction):
            messages.append(f"the prediction of the apply step does not exist: {prediction}")

    for input_file, output_file in zip(input_files, output_files):
        messages += check_prediction_absent(input_file, output_file)

    return messages
