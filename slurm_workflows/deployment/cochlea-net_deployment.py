#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Deployment of the jobs of the cochlea-net project: segmentation of SGNs and IHCs, detection of
ribbon synapses, and the export of the results to MoBIE and the S3 bucket.
"""
import json
import os
import shlex
import sys

from slurm_workflows.inputs import job_variables, path_exists
from slurm_workflows.mobie import DATASET_FILE, IMAGE_DIR, OME_ZARR, rclone, s3_datasets
from slurm_workflows.pipelines import missing_templates, step_template

PROJECT = "cochlea-net"

# Written by the apply step. It is the input of the segmentation step.
SEGMENTATION_INPUT = "predictions.zarr"

# Written by the mask step. cochlea-net keeps an existing mask, even an incomplete one.
MASK = "mask.zarr"

# Scale level of an OME-Zarr transferred from the S3 bucket.
OME_ZARR_KEY = "s0"

# Input key of an n5, filled with the setup index. An n5 which holds every stain has one setup per
# stain. An n5 which holds one stain uses setup 0.
N5_KEY = "setup{}/timepoint0/s0"

REQUIRED_SETTINGS = ("data_dir", "models")

# Steps of a pipeline which export the result to MoBIE. They need a MoBIE project to write into.
MOBIE_STEPS_KEY = "mobie_steps"

# Steps at the end of a pipeline which check its result and remove the intermediate data. The steps
# of the second key remove a local MoBIE copy, so they need the MoBIE steps.
CLEANUP_STEPS_KEY = "cleanup_steps"
MOBIE_CLEANUP_STEPS_KEY = "mobie_cleanup_steps"

# Key of the group which a step without a group in its template name belongs to.
GROUP_KEY = "group"

# First step of a pipeline with '--download'. It copies the OME-Zarr of the stain from the S3 bucket.
DOWNLOAD_STEP = "download_image"

# Last step of a pipeline with '--cleanup-input'. It removes the image data of the stain.
CLEANUP_INPUT_STEP = "cleanup_input"

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
DEFAULT_SYNAPSE_VERSION = "synapse_v3"

# Name of the prediction folder and the MoBIE source of the synapse jobs. The fields are the version
# parts of the model keys, e.g. 'v3' of 'synapse_v3'. cochlea-net reads the source by this name.
SYNAPSE_SOURCE = "synapse_{synapse}_ihc_{ihc}"

# rclone reads its remotes from this file, unless the environment variable RCLONE_CONFIG names another one.
RCLONE_CONFIG = os.path.join("~", ".config", "rclone", "rclone.conf")


def add_arguments(
    parser,
) -> None:
    """Add the options of the project to the parser of `slurm_wf.deploy`.

    Args:
        parser: `argparse.ArgumentParser` of `slurm_wf.deploy`.
    """
    parser.add_argument("--no-mobie", dest="no_mobie", action="store_true",
                        help="Leave the MoBIE steps of a pipeline out, to process a cochlea without "
                             "exporting the result. Needs -p.")
    parser.add_argument("--no-cleanup", dest="no_cleanup", action="store_true",
                        help="Leave the cleanup steps of a pipeline out, to keep the intermediate data, "
                             "such as predictions.zarr. Needs -p.")
    parser.add_argument("--group", type=str, default=None,
                        help="Group of a template which serves more than one group, such as a MoBIE "
                             "export. It overrides the group of the pipeline. "
                             f"Available: {sorted(GROUPS)}")
    parser.add_argument("--download", action="store_true",
                        help="Add a first step which downloads the OME-Zarr of the stain from the S3 bucket "
                             "to <data_dir>/<cochlea>. The other steps read it. Needs -p.")
    parser.add_argument("--cleanup-input", dest="cleanup_input", action="store_true",
                        help="Add a last step which removes the image data of the stain, if the S3 bucket "
                             "holds its OME-Zarr. From an n5, only the setup of the stain is removed. "
                             "Needs -p.")


def select_steps(
    definition: dict,
    settings: dict,
    args,
) -> list:
    """Return the steps of a pipeline to submit.

    The MoBIE steps need a project to write into. Without one they are dropped instead of failing,
    so a pipeline runs on an account which uses no MoBIE project. '--no-mobie' drops them for a
    single run, because the processing of a cochlea does not depend on the export.

    The cleanup steps come last, so that a failed step of the chain cancels them and its
    intermediate data stays for a rerun. A MoBIE cleanup step runs only with the MoBIE steps.
    '--no-cleanup' drops every cleanup step for a single run.

    '--download' adds the download step at the start. It needs a group, because it downloads the
    stain of one group. '--cleanup-input' adds the step which removes that image data at the end.
    It also needs a group, and '--no-cleanup' does not drop it.

    Args:
        definition: Output of `load_pipeline()`.
        settings: Output of `load_settings()`.
        args: Parsed command line options.

    Returns:
        list: The steps to submit, in order.
    """
    for key in (MOBIE_STEPS_KEY, CLEANUP_STEPS_KEY, MOBIE_CLEANUP_STEPS_KEY):
        if not isinstance(definition.get(key, []), list):
            raise ValueError(f"Pipeline {definition['name']} needs a list of steps for '{key}'.")

        missing = missing_templates(PROJECT, definition.get(key, []))
        if missing:
            raise ValueError(f"Pipeline {definition['name']} refers to steps without a template: {missing}.")

    for option, given in (("--download", args.download), ("--cleanup-input", args.cleanup_input)):
        if given and definition.get(GROUP_KEY) is None and args.group is None:
            raise ValueError(f"{option} needs a group. Pipeline {definition['name']} has none, "
                             "so it reads every stain from an n5.")

    steps = list(definition["steps"])
    if args.download:
        steps.insert(0, DOWNLOAD_STEP)

    mobie_steps = definition.get(MOBIE_STEPS_KEY, [])
    cleanup_steps = list(definition.get(CLEANUP_STEPS_KEY, []))
    mobie_cleanup_steps = definition.get(MOBIE_CLEANUP_STEPS_KEY, [])

    if settings.get("mobie_project") and not args.no_mobie:
        steps += mobie_steps
        cleanup_steps += mobie_cleanup_steps
    elif mobie_steps or mobie_cleanup_steps:
        reason = ("--no-mobie was given" if args.no_mobie
                  else "the settings file names no 'mobie_project'")
        print(f"The MoBIE steps are skipped, because {reason}: " +
              ", ".join(mobie_steps + mobie_cleanup_steps) + ".")

    if args.no_cleanup:
        if cleanup_steps:
            print("The cleanup steps are skipped, because --no-cleanup was given: " + ", ".join(cleanup_steps) + ".")
        cleanup_steps = []

    if args.cleanup_input:
        cleanup_steps.append(CLEANUP_INPUT_STEP)

    return steps + cleanup_steps


def species_template(
    project: str,
    step: str,
    species: str,
) -> str:
    """Return the template of a step in the folder of a species.

    Args:
        project: Name of the project.
        step: Name of the step.
        species: Species code of the cochlea, e.g. `G`.

    Returns:
        str: Path of the template, or None if the species folder has no template for the step.
    """
    template = step_template(project, os.path.join(species, step))
    if species and os.path.isfile(template):
        return template
    return None


def resolve_template(
    project: str,
    step: str,
    parameters: dict,
) -> str:
    """Return the template of a pipeline step for the species of the cochlea.

    The default templates fit mice. A species whose cochleae need other resources has a folder
    `templates/<project>/<species>/`, named after the species code of the cochlea, e.g. `G`. A step
    without a template in that folder uses the default template.

    Args:
        project: Name of the project.
        step: Name of the step.
        parameters: Job parameters, which name the cochlea.

    Returns:
        str: Path of the template.
    """
    species = parameters.get("cochlea", "").split("_")[0]
    template = species_template(project, step, species)
    if template is None:
        return step_template(project, step)

    print(f"Step {step} uses the template of species {species}: {template}")
    return template


def watershed_parameters(
    model_path: str,
    group: str,
) -> dict:
    """Read the watershed parameters of a trained model.

    Args:
        model_path: Path of the trained model.
        group: Model group, 'SGN' or 'IHC'.

    Returns:
        dict: Watershed parameters. The defaults of the group are used if the model has no `best_best_params.json`.
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
        prefix: Species and experimenter initials.
        number: Animal index of the cochlea, with leading zeros.
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


def version_part(
    version: str,
) -> str:
    """Return the version part of a model key in lowercase, e.g. 'v11' of 'IHC_v11'.

    Args:
        version: Model key of the settings file.

    Returns:
        str: The text after the first '_', or the full key if it has no '_'.
    """
    return version.split("_", 1)[-1].lower()


def marker_prediction_name(
    stain_synapses: str,
    synapse_version: str,
    stain_ihc: str,
    ihc_version: str,
) -> str:
    """Return the name of the prediction folder of the synapse jobs.

    The prediction runs on the dilated IHC segmentation and the detections are matched to it, so the
    result depends on two model versions. The joined name keeps a run against another IHC
    segmentation from overwriting an earlier one.

    The name is also the MoBIE source of the detections, which cochlea-net reads by name.
    `SYNAPSE_SOURCE` sets the format. The spelling of the model keys does not change the name.
    A non-default synapse stain is a prefix of the name. A non-default IHC stain is part of the
    IHC field, e.g. 'synapse_v3_ihc_myo7a_v11'.

    Args:
        stain_synapses: Stain of the synapse jobs.
        synapse_version: Model key of the synapse detection.
        stain_ihc: Stain of the IHC segmentation.
        ihc_version: Model key of the IHC segmentation.

    Returns:
        str: Name of the prediction folder.
    """
    name = SYNAPSE_SOURCE.format(
        synapse=version_part(synapse_version),
        ihc=prediction_name("IHC", stain_ihc, version_part(ihc_version)).lower())
    return prediction_name("synapses", stain_synapses, name)


def n5_candidates(
    prefix: str,
    number: str,
    side: str,
    stains: list,
    stain: str,
    version: str,
) -> list:
    """Return the n5 files which can hold one stain, with the input key of the stain.

    The initial processing writes one n5 which holds every stain. A cochlea which is transferred
    stain by stain has one n5 per stain instead, for example 'GLR_301L_PV_fused.n5'.

    Args:
        prefix: Species and experimenter initials.
        number: Animal index of the cochlea, with leading zeros.
        side: Side of the cochlea.
        stains: Every stain of the cochlea, in the order of the setups of the n5, or None.
        stain: The stain to read.
        version: Version suffix of the cochlea, or an empty string.

    Returns:
        list of tuple: the file name and the input key of each candidate. The n5 with every stain
            comes first, if 'stains' holds the stain.
    """
    candidates = []

    if stains and stain in stains:
        candidates.append((n5_name(prefix, number, side, stains, version), N5_KEY.format(stains.index(stain))))

    candidates.append((n5_name(prefix, number, side, [stain], version), N5_KEY.format(0)))

    return candidates


def resolve_input(
    cochlea_dir: str,
    candidates: list,
) -> tuple:
    """Return the file name of the job input and the matching input key.

    The first candidate whose input key exists wins. The file alone is not enough, because
    'cleanup_input' removes only the setup of one stain from an n5. If no candidate exists, the
    first one is reported, so that `check_job_input()` or `check_steps()` names the preferred path.

    Args:
        cochlea_dir: Directory of the cochlea.
        candidates: File name and input key of each candidate, in the order of preference.

    Returns:
        tuple of str: the file name of the input, and its input key.
    """
    for name, key in candidates:
        if path_exists(os.path.join(cochlea_dir, name, key)):
            return name, key

    return candidates[0]


def note_s3_image(
    s3_remote: str,
    cochlea: str,
    stain: str,
) -> None:
    """Print whether the S3 bucket holds the image data of a stain which is missing locally.

    If the bucket holds it, the note gives the command with '--download'. The note never stops the
    deployment, so a failed check is printed and not raised.

    Args:
        s3_remote: rclone remote and bucket, in the form 'remote:bucket', or None.
        cochlea: Name of the cochlea, which is the dataset in the bucket.
        stain: The stain to read.
    """
    image = f"{IMAGE_DIR}/{stain}{OME_ZARR}"
    if s3_remote is None:
        print(f"Note: the settings file names no 's3_remote', so the S3 bucket was not checked for {image}.")
        return

    # rclone gives the same error for a missing dataset folder and for a failed listing, so the
    # dataset list comes first. The transfer lists a source only after its data is complete, so a
    # data folder alone can be a partial copy.
    try:
        found = (cochlea in s3_datasets(s3_remote) and
                 stain in json.loads(rclone(["cat", f"{s3_remote}/{cochlea}/{DATASET_FILE}"]))["sources"])
    except FileNotFoundError:
        print("Note: rclone was not found, so the S3 bucket was not checked. Run 'module load rclone' first.")
        return
    except ValueError as exc:
        print(f"Note: the S3 bucket was not checked: {exc}")
        return

    if not found:
        print(f"Note: the {DATASET_FILE} of {cochlea} in the S3 bucket lists no source {stain}, so the bucket "
              f"holds no complete {image} either.")
        return

    command = " ".join(shlex.quote(arg) for arg in ["python"] + sys.argv + ["--download"])
    print(f"Note: the S3 bucket holds {s3_remote}/{cochlea}/{image}. To download it in a first step "
          f"of the chain, run:\n    {command}")


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
            dict - the placeholder replacements
            str  - the name of the sbatch script, without the date and the extension
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

    species = cochlea_content[0]
    experimenter = cochlea_content[1]
    number = cochlea_content[2]
    side = cochlea_content[3]
    stains = replacement_dict.get("stains")

    prefix = "".join([species, experimenter])
    template_name = os.path.basename(input_file)

    # A single job uses the given template as it is. A pipeline selects the species template.
    if definition is None:
        template = species_template(PROJECT, template_name.split(".template")[0], species)
        if template is not None and os.path.realpath(template) != os.path.realpath(input_file):
            print(f"Note: {template} fits species {species}. Pass it with -i to use its resources.")
    cochlea_dir = os.path.join(replacement_dict["data_dir"], cochlea)
    group = template_group(input_file)
    if group is None:
        group = pipeline_group

    if group is None:
        # The MoBIE templates convert every stain from an n5. The OME-Zarr of the S3 bucket is
        # their result, so it is no candidate.
        if not stains:
            raise ValueError(f"The template {template_name} needs the 'stains' parameter, which lists every "
                             "stain of the n5 data.")
        inputs = [resolve_input(cochlea_dir, n5_candidates(prefix, number, side, stains, stain, version))
                  for stain in stains]
        # The lists become bash arrays, so they are separated by spaces. A file name contains '_'.
        replacement_dict["cochlea_data"] = inputs[0][0]
        replacement_dict["cochlea_data_multi"] = " ".join(name for name, _ in inputs)
        replacement_dict["input_key_multi"] = " ".join(key for _, key in inputs)
        replacement_dict["channel_multi"] = " ".join(stains)

    else:
        group_spec = GROUPS[group]
        stain = replacement_dict[f"stain_{group}"]
        replacement_dict["stain"] = stain

        # The OME-Zarr of the S3 bucket is the fallback, because the n5 is deleted after a cochlea
        # is processed. The download step writes it only when the chain runs, so with '--download'
        # it is the only candidate.
        candidates = n5_candidates(prefix, number, side, stains, stain, version)
        candidates.append((f"{stain}.ome.zarr", OME_ZARR_KEY))
        if args.download:
            candidates = candidates[-1:]
        replacement_dict["cochlea_data"], replacement_dict["input_key"] = resolve_input(cochlea_dir, candidates)

        # The first step of a pipeline reads the image data and is rendered once per chain.
        if (definition is not None and not args.download and
                template_name.split(".template")[0] == definition["steps"][0] and
                not path_exists(os.path.join(cochlea_dir, replacement_dict["cochlea_data"],
                                             replacement_dict["input_key"]))):
            note_s3_image(replacement_dict.get("s3_remote"), cochlea, stain)

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
    if "synapse_version" in replacement_dict and "ihc_version" in replacement_dict:
        replacement_dict["marker_prediction"] = marker_prediction_name(
            replacement_dict["stain_synapses"], replacement_dict["synapse_version"],
            replacement_dict["stain_IHC"], replacement_dict["ihc_version"])

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
    """Return a message if a mask or an apply step finds the result of a previous run.

    The blocks of the prediction are split over the tasks of the job array, so a second run over an
    existing 'predictions.zarr' leaves a mix of two runs which no later step can detect. A mask step
    keeps an existing 'mask.zarr', so a mask which a timeout left incomplete is never rebuilt.

    Args:
        input_file: Path of the template.
        output_file: Path of the generated sbatch script.

    Returns:
        list: Warning messages. The list is empty if the step may run.
    """
    step = os.path.basename(input_file).split("_")[0]
    if step == "apply":
        result, message = SEGMENTATION_INPUT, "the prediction of a previous run exists: {}"
    elif step == "mask":
        result, message = MASK, ("the mask of a previous run exists and is reused as it is: {}. "
                                 "Remove it if its job did not complete.")
    else:
        return []

    output_folder = job_variables(output_file).get("OUTPUT_FOLDER")
    if output_folder is None:
        return []

    path = os.path.join(output_folder, result)
    if not path_exists(path):
        return []

    return [message.format(path)]


def check_rclone_remotes(
    output_files: list,
) -> list:
    """Return a message for each rclone remote of a transfer job which the rclone config does not hold.

    Without the remote, the transfer fails only after the steps before it have run.

    Args:
        output_files: Paths of the generated sbatch scripts.

    Returns:
        list: Warning messages. The list is empty if the rclone config holds every remote.
    """
    remotes = {job_variables(output_file).get("S3_REMOTE", "").split(":")[0] for output_file in output_files}
    remotes.discard("")
    if not remotes:
        return []

    config_file = os.environ.get("RCLONE_CONFIG", os.path.expanduser(RCLONE_CONFIG))
    sections = set()
    if os.path.isfile(config_file):
        with open(config_file, "r") as myfile:
            sections = {line.strip() for line in myfile}

    return [f"the rclone config {config_file} has no remote '{remote}', which the transfer to the S3 bucket "
            "needs. See doc/hpc_02_data_transfer.md." for remote in sorted(remotes) if f"[{remote}]" not in sections]


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

    # 'cleanup_input' removes only the setup of one stain from an n5, so the n5 can exist without the
    # input key. A key with '$' is set in a loop of the job, as in 'mobie_add_image'.
    input_path, input_key = variables.get("INPUT"), variables.get("INPUT_KEY")
    if (input_path is not None and input_key is not None and "$" not in input_key and
            path_exists(input_path) and not path_exists(os.path.join(input_path, input_key))):
        messages.append(f"the input of the job holds no {input_key}: {input_path}")

    for input_file, output_file in zip(input_files, output_files):
        messages += check_prediction_absent(input_file, output_file)

    messages += check_rclone_remotes(output_files)

    return messages
