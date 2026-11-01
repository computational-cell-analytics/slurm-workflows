#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Access to the `project.json` and the `dataset.json` files of a MoBIE project. The S3 files list the
datasets and the sources of every local MoBIE project, so a local file must not replace them.
"""
import csv
import io
import json
import os
import re
import subprocess

PROJECT_FILE = "project.json"

# A dataset is a top-level folder of the project which holds this file.
DATASET_FILE = "dataset.json"

# Data folders of a dataset, which mobie names after the source.
IMAGE_DIR = "images/ome-zarr"
TABLE_DIR = "tables"
OME_ZARR = ".ome.zarr"
TABLE_FILE = "default.tsv"

# Contrast limits of a rebuilt image source, by the byte size of its unsigned dtype. 1400 replaces
# the uint16 maximum 65535, as 'mobie_transfer_dataset' does.
CONTRAST_LIMITS = {1: [0, 255], 2: [0, 1400]}

# Display and menu of the view which mobie creates for a new source.
DISPLAYS = {"image": "imageDisplay", "segmentation": "segmentationDisplay", "spots": "spotDisplay"}
MENUS = {"image": "images", "segmentation": "segmentations", "spots": "spots"}

# Name of a synapse source, e.g. 'synapse_v3_ihc_v11' or 'CTBP2_synapse_v3'. cochlea-net reads a
# synapse source by its name, so a deviation such as 'synapses_v3_IHC_v11' causes a KeyError there.
# Keep it in line with SYNAPSE_SOURCE of the cochlea-net deployment. test_inputs.py checks this.
SYNAPSE_NAME = re.compile(r"([A-Za-z0-9]+_)?synapse(_[a-z0-9_]+)?")


def tmp_file(name: str) -> str:
    # Two jobs which run at the same time must not share the temporary copy.
    jobid = os.environ.get("SLURM_JOB_ID")
    return f"tmp_{jobid}_{name}" if jobid else f"tmp_{name}"


def rclone(
    args: list,
) -> str:
    """Run an rclone command.

    Args:
        args: Arguments of rclone, e.g. ['cat', 'remote:bucket/project.json'].

    Returns:
        str: The standard output.
    """
    result = subprocess.run(["rclone"] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=True)
    if result.returncode != 0:
        raise ValueError(f"rclone {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def read_json(
    path: str,
) -> dict:
    with open(path, "r") as myfile:
        return json.load(myfile)


def write_json(
    path: str,
    content: dict,
) -> None:
    # Same format as 'write_metadata' of mobie-utils-python, so that a file changes only by its content.
    with open(path, "w") as myfile:
        json.dump(content, myfile, indent=2, sort_keys=True)


def set_datasets(
    project: dict,
    datasets: list,
) -> dict:
    """Return a copy of the project metadata with a new dataset list.

    Args:
        project: Content of a `project.json`.
        datasets: The new dataset names.

    Returns:
        dict: The project metadata. Only the dataset list differs.
    """
    default = project.get("defaultDataset")
    if default is not None and default not in datasets:
        raise ValueError(f"The default dataset {default} is not in the new dataset list. "
                         f"Change 'defaultDataset' of {PROJECT_FILE} first.")
    return dict(project, datasets=datasets)


def describe_change(
    before: list,
    after: list,
) -> str:
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    return f"added: {', '.join(added) or 'none'}; removed: {', '.join(removed) or 'none'}"


def local_datasets(
    mobie_project: str,
) -> list:
    """Return the datasets of a local MoBIE project.

    Args:
        mobie_project: Root of the local MoBIE project.

    Returns:
        list: Sorted names of the folders which hold a `dataset.json`.
    """
    return sorted(name for name in os.listdir(mobie_project)
                  if os.path.isfile(os.path.join(mobie_project, name, DATASET_FILE)))


def s3_datasets(
    s3_remote: str,
) -> list:
    """Return the datasets of the MoBIE project in the S3 bucket.

    Args:
        s3_remote: rclone remote and bucket, in the form 'remote:bucket'.

    Returns:
        list: Sorted names of the folders which hold a `dataset.json`.
    """
    # Depth 2 reads only the top level of each dataset folder, not the chunks of the OME-Zarr data.
    output = rclone(["lsf", "--files-only", "--max-depth", "2", "--include", f"/*/{DATASET_FILE}", s3_remote])
    return sorted(line.split("/")[0] for line in output.splitlines() if line)


def update_s3_json(
    mobie_project: str,
    remote_file: str,
    change,
    exists: bool = True,
) -> tuple:
    """Change a JSON file in the S3 bucket.

    The S3 file is copied to `<mobie_project>/tmp_<JobID>_<file name>`, edited and copied back.
    Outside of a Slurm job, the copy is `tmp_<file name>`. The local copy is removed afterwards.

    Args:
        mobie_project: Root of the local MoBIE project.
        remote_file: The file in the bucket, in the form 'remote:bucket/path'.
        change: Function which returns the new content for the current one, or for None if the
            file does not exist. It must not change its argument.
        exists: Whether the S3 file exists.

    Returns:
        tuple: The content before and after the change. If they are equal, nothing was uploaded.
    """
    local_file = os.path.join(mobie_project, tmp_file(os.path.basename(remote_file)))

    try:
        before = None
        if exists:
            rclone(["copyto", remote_file, local_file])
            before = read_json(local_file)

        after = change(before)
        if after == before:
            return before, after

        write_json(local_file, after)

        # The S3 file can change during the job, e.g. by the transfer of another job. An upload
        # would drop that change. The change keeps all other entries, so the upload then differs
        # from the S3 file by the change alone.
        # ponytail: S3 has no lock. A write by another job between this check and the upload is
        # still lost. The window is about one second. A new file is not checked.
        if exists and json.loads(rclone(["cat", remote_file])) != before:
            raise ValueError(f"{remote_file} changed during the job. Nothing was uploaded.")

        rclone(["copyto", local_file, remote_file])
        return before, after

    finally:
        if os.path.exists(local_file):
            os.remove(local_file)


def update_s3_project(
    mobie_project: str,
    s3_remote: str,
    new_datasets,
) -> None:
    """Change the dataset list of the `project.json` in the S3 bucket.

    Args:
        mobie_project: Root of the local MoBIE project, which holds the temporary copy.
        s3_remote: rclone remote and bucket, in the form 'remote:bucket'.
        new_datasets: Function which returns the new dataset list for the current one.
    """
    def change(project):
        datasets = new_datasets(project["datasets"])
        return project if datasets == project["datasets"] else set_datasets(project, datasets)

    before, after = update_s3_json(mobie_project, f"{s3_remote}/{PROJECT_FILE}", change)
    if after == before:
        print(f"The {PROJECT_FILE} of the S3 bucket is up to date.")
    else:
        print(f"Updated the {PROJECT_FILE} of the S3 bucket, {describe_change(before['datasets'], after['datasets'])}.")


def add_s3_dataset(
    mobie_project: str,
    s3_remote: str,
    dataset: str,
) -> None:
    """Add a dataset to the `project.json` of the S3 bucket, if the file does not list it yet.

    Args:
        mobie_project: Root of the local MoBIE project.
        s3_remote: rclone remote and bucket, in the form 'remote:bucket'.
        dataset: Name of the dataset to add.
    """
    update_s3_project(mobie_project, s3_remote,
                      lambda datasets: datasets if dataset in datasets else sorted(datasets + [dataset]))


def generate_s3_project(
    mobie_project: str,
    s3_remote: str,
) -> None:
    """Set the dataset list of the `project.json` in the S3 bucket to the dataset folders of the bucket.

    Args:
        mobie_project: Root of the local MoBIE project.
        s3_remote: rclone remote and bucket, in the form 'remote:bucket'.
    """
    update_s3_project(mobie_project, s3_remote, lambda datasets: s3_datasets(s3_remote))


def generate_local_project(
    mobie_project: str,
) -> None:
    """Set the dataset list of the local `project.json` to the dataset folders of the local project.

    Args:
        mobie_project: Root of the local MoBIE project.
    """
    path = os.path.join(mobie_project, PROJECT_FILE)
    project = read_json(path)

    datasets = local_datasets(mobie_project)
    if datasets == project["datasets"]:
        print(f"{path} is up to date.")
        return

    write_json(path, set_datasets(project, datasets))
    print(f"Updated {path}, {describe_change(project['datasets'], datasets)}.")


def s3_data(
    s3_remote: str,
    dataset: str,
) -> set:
    """Return the data folders of a dataset in the S3 bucket.

    Args:
        s3_remote: rclone remote and bucket, in the form 'remote:bucket'.
        dataset: Name of the dataset.

    Returns:
        set: Paths relative to the dataset folder: each 'images/ome-zarr/<name>.ome.zarr', and each
            'tables/<name>' which holds a 'default.tsv'.
    """
    # Depth 3 reaches 'images/ome-zarr/<name>.ome.zarr' and 'tables/<name>/default.tsv', but not the chunks.
    output = rclone(["lsf", "--max-depth", "3", f"{s3_remote}/{dataset}"])
    paths = [line.rstrip("/") for line in output.splitlines() if line]
    images = {path for path in paths if os.path.dirname(path) == IMAGE_DIR and path.endswith(OME_ZARR)}
    tables = {os.path.dirname(path) for path in paths
              if os.path.dirname(os.path.dirname(path)) == TABLE_DIR and os.path.basename(path) == TABLE_FILE}
    return images | tables


def source_paths(
    entry: dict,
    dataset: str,
) -> list:
    """Return the data folders of a source in the S3 bucket, relative to the dataset folder.

    Args:
        entry: Entry of the source in the 'sources' of a `dataset.json`.
        dataset: Name of the dataset.

    Returns:
        list: The OME-Zarr folder and the table folder, if the source has them.
    """
    source = next(iter(entry.values()))
    image_data = source.get("imageData", {})
    paths = []
    if "ome.zarr.s3" in image_data:
        # MoBIE reads the image data of the S3 project from this address, not from 'relativePath'.
        paths.append(image_data["ome.zarr.s3"]["s3Address"].split(f"/{dataset}/", 1)[-1])
    elif "ome.zarr" in image_data:
        paths.append(image_data["ome.zarr"]["relativePath"])
    table = source.get("tableData", {}).get("tsv")
    if table:
        paths.append(table["relativePath"])
    return [path.rstrip("/") for path in paths]


def unlisted_data(
    metadata: dict,
    dataset: str,
    data: set,
) -> list:
    """Return the data folders of a dataset which no source of its `dataset.json` lists.

    Args:
        metadata: Content of the `dataset.json`.
        dataset: Name of the dataset.
        data: Output of `s3_data()`.

    Returns:
        list: Sorted paths relative to the dataset folder.
    """
    listed = {path for entry in metadata["sources"].values() for path in source_paths(entry, dataset)}
    return sorted(data - listed)


def check_dataset(
    metadata: dict,
    dataset: str,
    data: set,
) -> list:
    """Return a message for each mismatch between a `dataset.json` and the data folders of its dataset.

    Args:
        metadata: Content of the `dataset.json`.
        dataset: Name of the dataset.
        data: Output of `s3_data()`.

    Returns:
        list: The messages. The list is empty if the file and the data match.
    """
    messages = [f"no source lists the data {path}" for path in unlisted_data(metadata, dataset, data)]
    for name, entry in sorted(metadata["sources"].items()):
        messages += [f"the source {name} lists the missing data {path}"
                     for path in source_paths(entry, dataset) if path not in data]
        if "synapse" in name.lower() and not SYNAPSE_NAME.fullmatch(name):
            messages.append(f"the synapse source {name} has no name of the form "
                            "[<stain>_]synapse_<lowercase suffix>, e.g. synapse_v3_ihc_v11")

    # A region display maps regions to sources, so only a list of source names is checked.
    for name, view in sorted(metadata["views"].items()):
        shown = {source for display in view.get("sourceDisplays", []) for settings in display.values()
                 if isinstance(settings.get("sources"), list) for source in settings["sources"]}
        messages += [f"the view {name} shows the missing source {source}"
                     for source in sorted(shown - set(metadata["sources"]))]

    default = metadata["views"].get("default")
    if default is not None and not any(DISPLAYS["image"] in display for display in default.get("sourceDisplays", [])):
        messages.append("the default view shows no image")
    return messages


def check_s3_datasets(
    s3_remote: str,
) -> None:
    """Print the mismatches between the `dataset.json` and the data folders of each dataset in the S3 bucket.

    Args:
        s3_remote: rclone remote and bucket, in the form 'remote:bucket'.
    """
    datasets = s3_datasets(s3_remote)
    mismatches = 0
    for dataset in datasets:
        metadata = json.loads(rclone(["cat", f"{s3_remote}/{dataset}/{DATASET_FILE}"]))
        messages = check_dataset(metadata, dataset, s3_data(s3_remote, dataset))
        for message in messages:
            print(f"{dataset}: {message}")
        mismatches += bool(messages)
    print(f"{mismatches} of {len(datasets)} datasets have a mismatch.")


def add_s3_sources(
    mobie_project: str,
    s3_remote: str,
    dataset: str,
    names: list,
) -> None:
    """Copy sources of the local `dataset.json` into the `dataset.json` of the S3 bucket.

    Only the named sources and their views are copied. They replace a source of the same name. If
    the bucket holds no `dataset.json` yet, the new file also gets the default view of the local
    file. Afterwards, each mismatch between the S3 file and the data in the bucket is printed.

    Args:
        mobie_project: Root of the local MoBIE project.
        s3_remote: rclone remote and bucket, in the form 'remote:bucket'.
        dataset: Name of the dataset.
        names: Names of the sources to copy.
    """
    local = read_json(os.path.join(mobie_project, dataset, DATASET_FILE))
    missing = [name for name in names if name not in local["sources"]]
    if missing:
        raise ValueError(f"The local {DATASET_FILE} of {dataset} has no source {', '.join(missing)}.")

    def change(remote):
        if remote is None:
            default = {"default": local["views"]["default"]} if "default" in local["views"] else {}
            remote = dict(local, sources={}, views=default)
        sources = dict(remote["sources"])
        views = dict(remote["views"])
        for name in names:
            sources[name] = local["sources"][name]
            if name in local["views"]:
                views[name] = local["views"][name]
        return dict(remote, sources=sources, views=views)

    before, after = update_s3_json(mobie_project, f"{s3_remote}/{dataset}/{DATASET_FILE}", change,
                                   exists=dataset in s3_datasets(s3_remote))
    if after == before:
        print(f"The {DATASET_FILE} of {dataset} in the S3 bucket is up to date.")
    else:
        print(f"Updated the {DATASET_FILE} of {dataset} in the S3 bucket, sources: {', '.join(names)}.")

    for message in check_dataset(after, dataset, s3_data(s3_remote, dataset)):
        print(f"Warning: {message}")


def default_view(
    source_type: str,
    name: str,
    contrast_limits: list = None,
) -> dict:
    """Return the view which mobie creates for a new source.

    Args:
        source_type: 'image', 'segmentation' or 'spots'.
        name: Name of the source.
        contrast_limits: Contrast limits of an image source.

    Returns:
        dict: The view.
    """
    display = {"name": name, "sources": [name]}
    if source_type == "image":
        display.update(color="white", contrastLimits=contrast_limits, opacity=1.0)
    else:
        display.update(lut="glasbey", opacity=0.5)
    return {"isExclusive": False, "sourceDisplays": [{DISPLAYS[source_type]: display}],
            "uiSelectionGroup": MENUS[source_type]}


def rebuild_source(
    s3_remote: str,
    dataset: str,
    name: str,
    data: set,
    service_endpoint: str,
) -> tuple:
    """Build the `dataset.json` entry and the view of a source from its data in the S3 bucket.

    The type comes from the data, not from the name. A spot source can carry 'IHC' in its name.
    - OME-Zarr and a table with 'label_id': segmentation.
    - A table with 'spot_id' alone: spots. mobie always writes this column.
    - OME-Zarr alone: image for an 8 or 16 bit unsigned dtype, segmentation for an integer dtype
      of 32 bit or more.

    Args:
        s3_remote: rclone remote and bucket, in the form 'remote:bucket'.
        dataset: Name of the dataset.
        name: Name of the source, which names its data folders.
        data: Output of `s3_data()`.
        service_endpoint: URL of the S3 service, e.g. 'https://s3.fs.gwdg.de'.

    Returns:
        tuple: The entry and the view of the source.

    Raises:
        ValueError: If the data matches no source type.
    """
    dataset_remote = f"{s3_remote}/{dataset}"
    image_path = f"{IMAGE_DIR}/{name}{OME_ZARR}"
    table_path = f"{TABLE_DIR}/{name}"
    table_data = {"tsv": {"relativePath": table_path}}

    columns, rows = [], []
    if table_path in data:
        reader = csv.DictReader(io.StringIO(rclone(["cat", f"{dataset_remote}/{table_path}/{TABLE_FILE}"])),
                                delimiter="\t")
        rows = list(reader)
        columns = reader.fieldnames or []

    if image_path not in data:
        if not {"spot_id", "x", "y", "z"} <= set(columns) or not rows:
            raise ValueError(f"{table_path} holds no spot table, and there is no {image_path}")
        # mobie.add_spots without a bounding box and a reference source takes the range of the
        # coordinates, which 'mobie_add_detection' relies on.
        coordinates = [[float(row[axis]) for row in rows] for axis in "xyz"]
        entry = {"spots": {"boundingBoxMax": [max(values) for values in coordinates],
                           "boundingBoxMin": [min(values) for values in coordinates],
                           "tableData": table_data, "unit": "micrometer"}}
        return entry, default_view("spots", name)

    bucket_path = s3_remote.split(":", 1)[1]
    image_data = {"ome.zarr": {"relativePath": image_path},
                  "ome.zarr.s3": {"s3Address": f"{service_endpoint.rstrip('/')}/{bucket_path}/{dataset}/{image_path}"}}

    if columns:
        if "label_id" not in columns:
            raise ValueError(f"{table_path} holds no segmentation table")
        return {"segmentation": {"imageData": image_data, "tableData": table_data}}, default_view("segmentation", name)

    # The dtype has the form '<u2': byte order, kind and byte size.
    dtype = json.loads(rclone(["cat", f"{dataset_remote}/{image_path}/s0/.zarray"]))["dtype"]
    kind, size = dtype[1], int(dtype[2:])
    if kind == "u" and size in CONTRAST_LIMITS:
        return {"image": {"imageData": image_data}}, default_view("image", name, CONTRAST_LIMITS[size])
    if kind in "ui" and size >= 4:
        return {"segmentation": {"imageData": image_data}}, default_view("segmentation", name)
    raise ValueError(f"{image_path} has the dtype {dtype}, which matches no source type")


def source_name(
    path: str,
) -> str:
    name = os.path.basename(path)
    return name[:-len(OME_ZARR)] if name.endswith(OME_ZARR) else name


def repair_s3_dataset(
    mobie_project: str,
    s3_remote: str,
    dataset: str,
    service_endpoint: str,
    default_view_source: str = None,
    output: str = None,
) -> None:
    """Add a source to the `dataset.json` of the S3 bucket for the data which no source lists.

    The entries are built from the data in the bucket with `rebuild_source()`. An existing entry
    does not change. Data which matches no source type is reported and skipped.

    Args:
        mobie_project: Root of the local MoBIE project, which holds the temporary copy.
        s3_remote: rclone remote and bucket, in the form 'remote:bucket'.
        dataset: Name of the dataset.
        service_endpoint: URL of the S3 service, e.g. 'https://s3.fs.gwdg.de'.
        default_view_source: Source whose view becomes the default view of the dataset.
        output: Write the repaired file to this path, and upload nothing.
    """
    data = s3_data(s3_remote, dataset)
    added, skipped = [], []

    def change(remote):
        sources = dict(remote["sources"])
        views = dict(remote["views"])
        for name in sorted({source_name(path) for path in unlisted_data(remote, dataset, data)}):
            if name in sources:
                skipped.append(f"{name}: the source of this name lists other data")
                continue
            try:
                sources[name], views[name] = rebuild_source(s3_remote, dataset, name, data, service_endpoint)
            except ValueError as exc:
                skipped.append(f"{name}: {exc}")
                continue
            added.append(name)

        if default_view_source is not None:
            if default_view_source not in views:
                raise ValueError(f"{dataset} has no view {default_view_source}.")
            # mobie.add_* gives a new dataset the view of its first source as the default view.
            views["default"] = dict(views[default_view_source], uiSelectionGroup="bookmark")
        return dict(remote, sources=sources, views=views)

    remote_file = f"{s3_remote}/{dataset}/{DATASET_FILE}"
    if output is not None:
        after = change(json.loads(rclone(["cat", remote_file])))
        write_json(output, after)
        print(f"Wrote the repaired {DATASET_FILE} of {dataset} to {output}. Nothing was uploaded.")
    else:
        before, after = update_s3_json(mobie_project, remote_file, change)
        print(f"The {DATASET_FILE} of {dataset} in the S3 bucket is up to date." if after == before
              else f"Updated the {DATASET_FILE} of {dataset} in the S3 bucket.")

    print(f"Added sources: {', '.join(added) or 'none'}")
    for message in skipped:
        print(f"Skipped {message}")
    for message in check_dataset(after, dataset, data):
        print(f"Remaining mismatch: {message}")
