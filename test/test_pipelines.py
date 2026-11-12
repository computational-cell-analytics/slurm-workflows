#!/usr/bin/python
# -- coding: utf-8 --
"""Tests of the step selection of a pipeline: `select_steps()` and `step_row()` of cochlea-net,
and the `download_image` and `cleanup_input` jobs which `--download` and `--cleanup-input` add."""
import argparse
import contextlib
import glob
import io
import json
import os
import tempfile
import unittest

from slurm_workflows.pipelines import load_pipeline, resolve_pipeline
from slurm_workflows.projects.cochlea_net import deployment
from slurm_workflows.templates import TEMPLATE_SUFFIX, project_of_template, render_template, step_template
from stubs import TEMPLATE_DIR, read_file, run, write_executable, write_file
from test_mobie import RCLONE_STUB, REMOTE

SETTINGS = {"mobie_project": "/path/to/mobie_project"}


class SelectStepsTest(unittest.TestCase):

    def setUp(self):
        self.module = deployment
        _, pipeline_file = resolve_pipeline("cochlea-net/sgn")
        self.definition = load_pipeline(pipeline_file)

    def select(self, settings=SETTINGS, no_mobie=False, no_cleanup=False, download=False, cleanup_input=False):
        args = argparse.Namespace(no_mobie=no_mobie, no_cleanup=no_cleanup, download=download,
                                  cleanup_input=cleanup_input, group=None)
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

    def test_cleanup_input_runs_last(self):
        self.assertEqual(self.select(cleanup_input=True)[-2:], ["mobie_cleanup_segmentation", "cleanup_input"])
        self.assertEqual(self.select(no_cleanup=True, cleanup_input=True)[-2:],
                         ["mobie_transfer_segmentation", "cleanup_input"])

        del self.definition["group"]
        with self.assertRaises(ValueError):
            self.select(cleanup_input=True)

    def test_cleanup_steps_must_be_a_list(self):
        self.definition["cleanup_steps"] = "cleanup_SGN"

        with self.assertRaises(ValueError):
            self.select()


class S3TemplateTest(unittest.TestCase):
    """Run the rendered `download_image` and `cleanup_input` jobs against the rclone stub of `test_mobie.py`."""

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

    def run_step(self, step, stain, cochlea_data="", input_key=""):
        values = {"cochlea_job_name": "job", "user_address": "a", "account_name": "b",
                  "data_dir": self.data_dir, "cochlea": self.COCHLEA, "cochlea_data": cochlea_data,
                  "input_key": input_key, "s3_remote": REMOTE, "stain": stain, "time": "01:00:00", "mem": "1G"}
        sbatch_file = os.path.join(self.tmp.name, f"{step}.sbatch")
        with contextlib.redirect_stdout(io.StringIO()):
            render_template(step_template(TEMPLATE_DIR, step), sbatch_file, values)
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

    def test_n5_setup_is_removed_and_the_n5_with_its_last_setup(self):
        n5 = "M_LR_226R_PV_Vglut3_fused.n5"
        for setup in ("setup0", "setup1"):
            write_file(self.cochlea_path(n5, setup, "timepoint0", "s0", "attributes.json"))
        self.add_to_bucket("PV")
        self.add_to_bucket("Vglut3")

        self.assertEqual(self.run_step("cleanup_input", "PV", n5, "setup0/timepoint0/s0"), 0)
        self.assertEqual(os.listdir(self.cochlea_path(n5)), ["setup1"])

        self.assertEqual(self.run_step("cleanup_input", "Vglut3", n5, "setup1/timepoint0/s0"), 0)
        self.assertEqual(os.listdir(self.cochlea_path()), [])

    def test_ome_zarr_is_removed(self):
        write_file(self.cochlea_path("PV.ome.zarr", "s0", ".zarray"))
        self.add_to_bucket("PV")

        self.assertEqual(self.run_step("cleanup_input", "PV", "PV.ome.zarr", "s0"), 0)
        self.assertEqual(os.listdir(self.cochlea_path()), [])

    def test_input_stays_without_a_complete_copy_in_the_bucket(self):
        write_file(self.cochlea_path("PV.ome.zarr", "s0", ".zarray"))
        self.assertNotEqual(self.run_step("cleanup_input", "PV", "PV.ome.zarr", "s0"), 0)

        self.add_to_bucket("PV", listed=False)
        self.assertNotEqual(self.run_step("cleanup_input", "PV", "PV.ome.zarr", "s0"), 0)
        self.assertTrue(os.path.exists(self.cochlea_path("PV.ome.zarr", "s0", ".zarray")))


class StepRowTest(unittest.TestCase):
    """The steps file of cochlea-net gives the group and the resources of every template."""

    def test_every_template_has_a_row(self):
        steps = json.loads(read_file(os.path.join(TEMPLATE_DIR, deployment.STEPS_FILE)))
        templates = glob.glob(os.path.join(TEMPLATE_DIR, f"*{TEMPLATE_SUFFIX}"))

        self.assertEqual(sorted(steps), sorted(os.path.basename(path)[:-len(TEMPLATE_SUFFIX)] for path in templates))
        for template in templates:
            with self.subTest(template):
                group, resources = deployment.step_row(template, "M")
                self.assertIn(group, list(deployment.GROUPS) + [None])
                expected = ["mem", "time"]
                if f"<{deployment.INSTANCES_KEY}>" in read_file(template):
                    expected.append(deployment.INSTANCES_KEY)
                self.assertEqual(sorted(resources), sorted(expected))

    def test_species_deviation(self):
        sgn = step_template(TEMPLATE_DIR, "apply_SGN")

        self.assertEqual(deployment.step_row(sgn, "M"), ("SGN", {"time": "00:25:00", "mem": "96G", "instances": 10}))
        self.assertEqual(deployment.step_row(sgn, "G"), ("SGN", {"time": "01:30:00", "mem": "128G", "instances": 10}))

    def test_template_without_row_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_file(os.path.join(tmp, deployment.STEPS_FILE), json.dumps({"apply_SGN": {"group": "SGN"}}))
            template = write_file(os.path.join(tmp, "predict_SGN.template"))

            with self.assertRaises(ValueError):
                deployment.step_row(template, "M")

    def test_project_of_template(self):
        self.assertEqual(project_of_template(step_template(TEMPLATE_DIR, "apply_SGN")), "cochlea-net")


if __name__ == "__main__":
    unittest.main()
