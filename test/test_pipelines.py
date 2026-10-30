#!/usr/bin/python
# -- coding: utf-8 --
"""Tests of the step selection of a pipeline: `select_steps()` and `resolve_template()` of cochlea-net,
and the `download_image` job which `--download` adds."""
import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest

from slurm_workflows.pipelines import load_pipeline, project_of_template, resolve_pipeline, step_template
from slurm_workflows.templates import replace_substrings_in_file
from stubs import cochlea_net_module, run, write_executable, write_file
from test_mobie import RCLONE_STUB, REMOTE

SETTINGS = {"mobie_project": "/path/to/mobie_project"}


class SelectStepsTest(unittest.TestCase):

    def setUp(self):
        self.module = cochlea_net_module()
        project, pipeline_file = resolve_pipeline("cochlea-net/sgn")
        self.definition = load_pipeline(project, pipeline_file)

    def select(self, settings=SETTINGS, no_mobie=False, no_cleanup=False, download=False):
        args = argparse.Namespace(no_mobie=no_mobie, no_cleanup=no_cleanup, download=download, group=None)
        with contextlib.redirect_stdout(io.StringIO()):
            return self.module.select_steps(self.definition, settings, args)

    def test_cleanup_runs_last(self):
        self.assertEqual(self.select()[-3:],
                         ["mobie_transfer_segmentation", "cleanup_SGN", "mobie_cleanup_segmentation"])

    def test_no_mobie_drops_mobie_cleanup(self):
        self.assertEqual(self.select(no_mobie=True), ["mask_SGN", "apply_SGN", "segment_SGN", "cleanup_SGN"])
        self.assertEqual(self.select(settings={})[-1], "cleanup_SGN")

    def test_no_cleanup(self):
        steps = self.select(no_cleanup=True)

        self.assertEqual(steps[-1], "mobie_transfer_segmentation")
        self.assertFalse([step for step in steps if "cleanup" in step])

    def test_download_runs_first(self):
        self.assertEqual(self.select(download=True)[:2], ["download_image", "mask_SGN"])

        del self.definition["group"]
        with self.assertRaises(ValueError):
            self.select(download=True)

    def test_cleanup_steps_must_be_a_list(self):
        self.definition["cleanup_steps"] = "cleanup_SGN"

        with self.assertRaises(ValueError):
            self.select()


class S3TemplateTest(unittest.TestCase):
    """Run the rendered `download_image` job against the rclone stub of `test_mobie.py`."""

    COCHLEA = "M_LR_000226_R"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self.tmp.name, "data")
        self.bucket = os.path.join(self.tmp.name, "bucket")
        self.sources = {}
        bin_dir = os.path.join(self.tmp.name, "stub_bin")
        os.makedirs(bin_dir)
        write_executable(os.path.join(bin_dir, "rclone"), RCLONE_STUB)
        self.env = {"PATH": os.pathsep.join([bin_dir, "/usr/bin", "/bin"]), "STUB_BUCKET": self.bucket,
                    "HOME": self.tmp.name}

    def tearDown(self):
        self.tmp.cleanup()

    def cochlea_path(self, *parts):
        return os.path.join(self.data_dir, self.COCHLEA, *parts)

    def add_to_bucket(self, stain, listed=True):
        """The transfer lists the source only after a complete copy, so an unlisted source is a partial copy."""
        write_file(os.path.join(self.bucket, self.COCHLEA, "images", "ome-zarr", f"{stain}.ome.zarr", "s0", ".zarray"))
        if listed:
            self.sources[stain] = {}
        write_file(os.path.join(self.bucket, self.COCHLEA, "dataset.json"), json.dumps({"sources": self.sources}))

    def run_step(self, step, stain):
        replacements = {"cochlea_job_name": "job", "user_address": "a", "account_name": "b",
                        "data_dir": self.data_dir, "cochlea": self.COCHLEA, "s3_remote": REMOTE, "stain": stain}
        sbatch_file = os.path.join(self.tmp.name, f"{step}.sbatch")
        with contextlib.redirect_stdout(io.StringIO()):
            replace_substrings_in_file(step_template("cochlea-net", step), sbatch_file, replacements)
        return run(["bash", sbatch_file], env=self.env).returncode

    def test_download(self):
        self.add_to_bucket("PV")

        self.assertEqual(self.run_step("download_image", "PV"), 0)
        self.assertEqual(os.listdir(self.cochlea_path()), ["PV.ome.zarr"])

        # An existing download is completed in place.
        os.remove(self.cochlea_path("PV.ome.zarr", "s0", ".zarray"))
        self.assertEqual(self.run_step("download_image", "PV"), 0)
        self.assertTrue(os.path.exists(self.cochlea_path("PV.ome.zarr", "s0", ".zarray")))

    def test_download_of_a_partial_copy_fails(self):
        self.add_to_bucket("PV", listed=False)

        self.assertNotEqual(self.run_step("download_image", "PV"), 0)
        self.assertFalse(os.path.exists(self.cochlea_path()))


class ResolveTemplateTest(unittest.TestCase):

    def setUp(self):
        self.module = cochlea_net_module()

    def resolve(self, cochlea, step):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.module.resolve_template("cochlea-net", step, {"cochlea": cochlea})

    def test_species_template(self):
        self.assertEqual(self.resolve("G_EK_000071_L", "apply_SGN"),
                         step_template("cochlea-net", os.path.join("G", "apply_SGN")))

    def test_default_template(self):
        self.assertEqual(self.resolve("M_AMD_000137_L", "apply_SGN"), step_template("cochlea-net", "apply_SGN"))
        self.assertEqual(self.resolve("G_EK_000071_L", "cleanup_IHC"), step_template("cochlea-net", "cleanup_IHC"))

    def test_project_of_species_template(self):
        species_template = step_template("cochlea-net", os.path.join("G", "apply_SGN"))

        self.assertEqual(project_of_template(species_template), "cochlea-net")


if __name__ == "__main__":
    unittest.main()
