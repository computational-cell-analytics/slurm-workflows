#!/usr/bin/python
# -- coding: utf-8 --
"""Tests of `slurm_workflows/mobie.py`: the dataset list of the project.json, in the S3 bucket and local, and
the sources of the dataset.json in the S3 bucket."""
import json
import os
import tempfile
import unittest
from unittest import mock

from stubs import read_file, write_executable, write_file

from slurm_workflows import mobie

REMOTE = "remote:bucket"

# Maps 'remote:bucket/<path>' to '$STUB_BUCKET/<path>'. Supports 'copyto', 'cat', the 'lsf --files-only' of
# s3_datasets() and the 'lsf --max-depth 3' of s3_data(). The last argument of 'lsf' is the remote.
RCLONE_STUB = f"""#!/bin/bash
to_local() {{ echo "${{1/#{REMOTE}/$STUB_BUCKET}}"; }}
case "$1" in
    copyto) mkdir -p "$(dirname "$(to_local "$3")")" && cp "$(to_local "$2")" "$(to_local "$3")" ;;
    cat) cat "$(to_local "$2")" ;;
    lsf)
        cd "$(to_local "${{@: -1}}")" || exit 3
        if [ "$2" = "--max-depth" ] ; then
            find . -mindepth 1 -maxdepth 3 -type d | sed 's|^\\./||; s|$|/|'
            find . -mindepth 1 -maxdepth 3 -type f | sed 's|^\\./||'
        else
            ls -d */dataset.json 2>/dev/null
        fi
        exit 0 ;;
    *) exit 1 ;;
esac
"""

PROJECT = {"datasets": ["M_LR_000226_R", "G_EK_000049_L"], "defaultDataset": "G_EK_000049_L",
           "description": "cochlea-lightsheet", "specVersion": "0.3.0"}


def other_entries(project):
    return {key: value for key, value in project.items() if key != "datasets"}


class RcloneStubTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mobie_project = os.path.join(self.tmp.name, "mobie_project")
        self.bucket = os.path.join(self.tmp.name, "bucket")
        bin_dir = os.path.join(self.tmp.name, "stub_bin")
        os.makedirs(self.mobie_project)
        os.makedirs(bin_dir)
        write_executable(os.path.join(bin_dir, "rclone"), RCLONE_STUB)
        self.remote_file = write_file(os.path.join(self.bucket, "project.json"), json.dumps(PROJECT))

        env = {"PATH": os.pathsep.join([bin_dir, "/usr/bin", "/bin"]), "STUB_BUCKET": self.bucket}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()


class ProjectTest(RcloneStubTest):

    def add_dataset_folders(self, root, names):
        for name in names:
            write_file(os.path.join(root, name, "dataset.json"), "{}")

    def test_new_dataset_is_added_in_order(self):
        mobie.add_s3_dataset(self.mobie_project, REMOTE, "M_AMD_000058_L")

        remote = json.loads(read_file(self.remote_file))
        self.assertEqual(remote["datasets"], ["G_EK_000049_L", "M_AMD_000058_L", "M_LR_000226_R"])
        self.assertEqual(other_entries(remote), other_entries(PROJECT))
        self.assertEqual(os.listdir(self.mobie_project), [])

    def test_listed_dataset_gives_no_upload(self):
        before = read_file(self.remote_file)

        mobie.add_s3_dataset(self.mobie_project, REMOTE, "G_EK_000049_L")

        self.assertEqual(read_file(self.remote_file), before)
        self.assertEqual(os.listdir(self.mobie_project), [])

    def test_copy_of_a_slurm_job_holds_the_jobid(self):
        with mock.patch.dict(os.environ, {"SLURM_JOB_ID": "123"}):
            with mock.patch("slurm_workflows.mobie.rclone", wraps=mobie.rclone) as rclone_mock:
                mobie.add_s3_dataset(self.mobie_project, REMOTE, "M_AMD_000058_L")

        local_file = os.path.join(self.mobie_project, "tmp_123_project.json")
        self.assertEqual(rclone_mock.call_args_list[0][0][0], ["copyto", f"{REMOTE}/project.json", local_file])
        self.assertEqual(os.listdir(self.mobie_project), [])

    def test_change_of_the_remote_file_during_the_job_stops_the_upload(self):
        concurrent = json.dumps(dict(PROJECT, datasets=PROJECT["datasets"] + ["M_AMD_000059_R"]))
        real_rclone = mobie.rclone

        def rclone_with_concurrent_write(args):
            if args[0] == "cat":
                write_file(self.remote_file, concurrent)
            return real_rclone(args)

        with mock.patch("slurm_workflows.mobie.rclone", side_effect=rclone_with_concurrent_write):
            with self.assertRaises(ValueError):
                mobie.add_s3_dataset(self.mobie_project, REMOTE, "M_AMD_000058_L")

        self.assertEqual(read_file(self.remote_file), concurrent)
        self.assertEqual(os.listdir(self.mobie_project), [])

    def test_generate_s3_sets_the_dataset_folders_of_the_bucket(self):
        # M_LR_000226_R is listed, but has no folder.
        self.add_dataset_folders(self.bucket, ["M_AMD_000058_L", "G_EK_000049_L"])

        mobie.generate_s3_project(self.mobie_project, REMOTE)

        remote = json.loads(read_file(self.remote_file))
        self.assertEqual(remote["datasets"], ["G_EK_000049_L", "M_AMD_000058_L"])
        self.assertEqual(other_entries(remote), other_entries(PROJECT))
        self.assertEqual(os.listdir(self.mobie_project), [])

    def test_generate_keeps_the_default_dataset(self):
        self.add_dataset_folders(self.bucket, ["M_AMD_000058_L"])
        before = read_file(self.remote_file)

        with self.assertRaises(ValueError):
            mobie.generate_s3_project(self.mobie_project, REMOTE)

        self.assertEqual(read_file(self.remote_file), before)

    def test_generate_local_sets_the_local_dataset_folders(self):
        project_file = write_file(os.path.join(self.mobie_project, "project.json"), json.dumps(PROJECT))
        self.add_dataset_folders(self.mobie_project, ["M_AMD_000058_L", "G_EK_000049_L"])
        os.makedirs(os.path.join(self.mobie_project, "no_dataset"))
        bucket_before = read_file(self.remote_file)

        mobie.generate_local_project(self.mobie_project)

        local = json.loads(read_file(project_file))
        self.assertEqual(local["datasets"], ["G_EK_000049_L", "M_AMD_000058_L"])
        self.assertEqual(other_entries(local), other_entries(PROJECT))
        self.assertEqual(read_file(self.remote_file), bucket_before)


DATASET = "M_AMD_000127_L"

SPOT_TABLE = "spot_id\tx\ty\tz\n1.0000\t583.6800\t291.4600\t23.9400\n2.0000\t6.0800\t1527.2200\t1341.0200\n"
LABEL_TABLE = "label_id\tanchor_x\tanchor_y\tanchor_z\n1\t891.2\t818.8\t41.7\n"


def segmentation(name):
    return {"segmentation": {"imageData": {"ome.zarr": {"relativePath": f"images/ome-zarr/{name}.ome.zarr"}},
                             "tableData": {"tsv": {"relativePath": f"tables/{name}"}}}}


def dataset_json(names, default):
    return {"is2D": False, "sources": {name: segmentation(name) for name in names},
            "views": dict({name: mobie.default_view("segmentation", name) for name in names},
                          default=dict(mobie.default_view("segmentation", default), uiSelectionGroup="bookmark"))}


class DatasetTest(RcloneStubTest):

    def remote_path(self, *parts):
        return os.path.join(self.bucket, DATASET, *parts)

    def add_segmentation_data(self, name, dtype="<u8"):
        write_file(self.remote_path("images", "ome-zarr", f"{name}.ome.zarr", "s0", ".zarray"),
                   json.dumps({"dtype": dtype}))
        write_file(self.remote_path("tables", name, "default.tsv"), LABEL_TABLE)

    def test_named_sources_are_added_to_the_s3_dataset(self):
        write_file(self.remote_path("dataset.json"), json.dumps(dataset_json(["IHC_v11", "SGN_v2"], "IHC_v11")))
        local = dataset_json(["SGN_v2", "SGN_v3", "IHC_v4"], "SGN_v2")
        local["sources"]["SGN_v2"]["segmentation"]["description"] = "new"
        local_file = write_file(os.path.join(self.mobie_project, DATASET, "dataset.json"), json.dumps(local))

        mobie.add_s3_sources(self.mobie_project, REMOTE, DATASET, ["SGN_v2", "SGN_v3"])

        remote = json.loads(read_file(self.remote_path("dataset.json")))
        self.assertEqual(sorted(remote["sources"]), ["IHC_v11", "SGN_v2", "SGN_v3"])
        self.assertEqual(remote["sources"]["SGN_v2"], local["sources"]["SGN_v2"])
        self.assertEqual(remote["views"]["default"], dataset_json([], "IHC_v11")["views"]["default"])
        self.assertEqual(json.loads(read_file(local_file)), local)
        self.assertEqual(os.listdir(self.mobie_project), [DATASET])

    def test_new_s3_dataset_gets_the_named_sources_and_the_local_default_view(self):
        local = dataset_json(["PV", "SGN_v3"], "PV")
        write_file(os.path.join(self.mobie_project, DATASET, "dataset.json"), json.dumps(local))

        mobie.add_s3_sources(self.mobie_project, REMOTE, DATASET, ["PV"])

        remote = json.loads(read_file(self.remote_path("dataset.json")))
        self.assertEqual(sorted(remote["sources"]), ["PV"])
        self.assertEqual(remote["views"], {"PV": local["views"]["PV"], "default": local["views"]["default"]})

    def test_repair_builds_the_unlisted_sources_from_the_data(self):
        before = dataset_json(["IHC_v11"], "IHC_v11")
        write_file(self.remote_path("dataset.json"), json.dumps(before))
        self.add_segmentation_data("IHC_v11")
        self.add_segmentation_data("SGN_v3")
        write_file(self.remote_path("images", "ome-zarr", "PV.ome.zarr", "s0", ".zarray"), json.dumps({"dtype": "<u2"}))
        write_file(self.remote_path("tables", "synapse_v3_ihc_v9", "default.tsv"), SPOT_TABLE)
        # A segmentation table without image data is no spot source.
        write_file(self.remote_path("tables", "IHC", "default.tsv"), LABEL_TABLE)
        output = os.path.join(self.tmp.name, "repaired.json")

        mobie.repair_s3_dataset(self.mobie_project, REMOTE, DATASET, "https://s3.example.org/",
                                default_view_source="PV", output=output)
        self.assertEqual(json.loads(read_file(self.remote_path("dataset.json"))), before)
        mobie.repair_s3_dataset(self.mobie_project, REMOTE, DATASET, "https://s3.example.org/",
                                default_view_source="PV")

        remote = json.loads(read_file(self.remote_path("dataset.json")))
        self.assertEqual(json.loads(read_file(output)), remote)
        self.assertEqual(sorted(remote["sources"]), ["IHC_v11", "PV", "SGN_v3", "synapse_v3_ihc_v9"])
        self.assertEqual(remote["sources"]["IHC_v11"], before["sources"]["IHC_v11"])
        self.assertEqual(remote["sources"]["PV"]["image"]["imageData"]["ome.zarr.s3"]["s3Address"],
                         f"https://s3.example.org/bucket/{DATASET}/images/ome-zarr/PV.ome.zarr")
        self.assertEqual(remote["views"]["PV"]["sourceDisplays"][0]["imageDisplay"]["contrastLimits"], [0, 1400])
        self.assertEqual(remote["sources"]["SGN_v3"]["segmentation"]["tableData"],
                         {"tsv": {"relativePath": "tables/SGN_v3"}})
        spots = remote["sources"]["synapse_v3_ihc_v9"]["spots"]
        self.assertEqual(spots["boundingBoxMin"], [6.08, 291.46, 23.94])
        self.assertEqual(spots["boundingBoxMax"], [583.68, 1527.22, 1341.02])
        self.assertEqual(remote["views"]["default"], dict(remote["views"]["PV"], uiSelectionGroup="bookmark"))
        self.assertEqual(os.listdir(self.mobie_project), [])

    def test_check_dataset_reports_each_mismatch(self):
        write_file(self.remote_path("tables", "IHC_v11", "default.tsv"), LABEL_TABLE)
        write_file(self.remote_path("tables", "synapse_v3", "default.tsv"), SPOT_TABLE)
        write_file(self.remote_path("images", "ome-zarr", "SGN.ome.zarr", "s0", ".zarray"), "{}")
        # A table folder without default.tsv is no source table.
        write_file(self.remote_path("tables", "PV_SGN_v2", "subtype_ratio.tsv"), "")
        metadata = dataset_json(["IHC_v11"], "IHC_v11")
        # MoBIE reads the image data from the s3Address, so a wrong relativePath does no harm.
        metadata["sources"]["SGN"] = {"segmentation": {"imageData": {
            "ome.zarr": {"relativePath": "images/ome.zarr/SGN.ome.zarr"},
            "ome.zarr.s3": {"s3Address": f"https://s3.example.org/bucket/{DATASET}/images/ome-zarr/SGN.ome.zarr"}}}}
        metadata["views"]["synapses_v3"] = mobie.default_view("spots", "synapses_v3")
        # A view without sourceDisplays is valid, e.g. a bookmark of a position.
        metadata["views"]["apex"] = {"isExclusive": False, "uiSelectionGroup": "bookmark",
                                     "viewerTransform": {"position": [0.0, 0.0, 0.0]}}

        data = mobie.s3_data(REMOTE, DATASET)

        self.assertEqual(data, {"images/ome-zarr/SGN.ome.zarr", "tables/IHC_v11", "tables/synapse_v3"})
        self.assertEqual(mobie.check_dataset(metadata, DATASET, data), [
            "no source lists the data tables/synapse_v3",
            "the source IHC_v11 lists the missing data images/ome-zarr/IHC_v11.ome.zarr",
            "the view synapses_v3 shows the missing source synapses_v3",
            "the default view shows no image",
        ])

    def test_check_dataset_reports_a_bad_synapse_name(self):
        names = ["synapse_v3_ihc_v11", "CTBP2_synapse_v3", "RibB_synapse", "synapses_v3_IHC_v11", "synapse_v3_IHC_v9"]
        metadata = {"sources": {name: {"spots": {"tableData": {"tsv": {"relativePath": f"tables/{name}"}}}}
                                for name in names}, "views": {}}

        messages = mobie.check_dataset(metadata, DATASET, {f"tables/{name}" for name in names})

        self.assertEqual([message.split()[3] for message in messages], ["synapse_v3_IHC_v9", "synapses_v3_IHC_v11"])


if __name__ == "__main__":
    unittest.main()
