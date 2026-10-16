#!/usr/bin/python
# -- coding: utf-8 --
"""Tests of `slurm_workflows/mobie.py`: the dataset list of the project.json, in the S3 bucket and local."""
import json
import os
import tempfile
import unittest
from unittest import mock

from stubs import read_file, write_executable, write_file

from slurm_workflows import mobie

REMOTE = "remote:bucket"

# Maps 'remote:bucket/<path>' to '$STUB_BUCKET/<path>'. Supports 'copyto', 'cat' and the 'lsf' of
# s3_datasets(), whose last argument is the remote.
RCLONE_STUB = f"""#!/bin/bash
to_local() {{ echo "${{1/#{REMOTE}/$STUB_BUCKET}}"; }}
case "$1" in
    copyto) cp "$(to_local "$2")" "$(to_local "$3")" ;;
    cat) cat "$(to_local "$2")" ;;
    lsf) cd "$(to_local "${{@: -1}}")" && ls -d */dataset.json ;;
    *) exit 1 ;;
esac
"""

PROJECT = {"datasets": ["M_LR_000226_R", "G_EK_000049_L"], "defaultDataset": "G_EK_000049_L",
           "description": "cochlea-lightsheet", "specVersion": "0.3.0"}


def other_entries(project):
    return {key: value for key, value in project.items() if key != "datasets"}


class ProjectTest(unittest.TestCase):

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


if __name__ == "__main__":
    unittest.main()
