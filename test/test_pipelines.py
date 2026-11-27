#!/usr/bin/python
# -- coding: utf-8 --
"""Tests of the step selection of a pipeline: `select_steps()` and `step_row()` of cochlea-net,
the `download_image` and `cleanup_input` jobs which `--download` and `--cleanup-input` add, and the
Slurm job name of every template."""
import argparse
import contextlib
import glob
import io
import json
import os
import re
import tempfile
import unittest

from slurm_workflows.pipelines import load_pipeline, resolve_pipeline
from slurm_workflows.projects.cochlea_net import deployment
from slurm_workflows.templates import TEMPLATE_SUFFIX, project_of_template, render_template, step_template
from stubs import TEMPLATE_DIR, read_file, run, write_executable, write_file
from test_mobie import RCLONE_STUB, REMOTE

SETTINGS = {"mobie_project": "/path/to/mobie_project"}


def write_chunks(
    folder: str,
) -> None:
    """Write 2500 empty chunks in nested folders, more than one batch of 1000 files, so that several removal
    processes run.

    Args:
        folder: Folder of the zarr.
    """
    for z in range(5):
        for y in range(5):
            for x in range(100):
                write_file(os.path.join(folder, "s0", str(z), str(y), str(x)))


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

    def add_to_bucket(self, stain, listed=True, folder=None):
        """The transfer lists the source only after a complete copy, so an unlisted source is a partial copy.

        The OME-Zarr folder is the stain, or `folder` for a source whose folder has another name.
        """
        image_path = f"images/ome-zarr/{folder or stain}.ome.zarr"
        write_file(os.path.join(self.bucket, self.COCHLEA, image_path, "s0", ".zarray"))
        if listed:
            self.sources[stain] = {"image": {"imageData": {
                "ome.zarr": {"relativePath": image_path},
                "ome.zarr.s3": {"s3Address": f"https://s3.example.org/bucket/{self.COCHLEA}/{image_path}"}}}}
        self.write_dataset()

    def write_dataset(self):
        write_file(os.path.join(self.bucket, self.COCHLEA, "dataset.json"), json.dumps({"sources": self.sources}))

    def run_step(self, step, stain, cochlea_data="", input_key=""):
        values = {"job_name": "job", "user_address": "a", "account_name": "b",
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

    def test_download_reads_the_folder_of_the_source(self):
        # The source PV of M_LR_000169_R lists 'PV_resized.ome.zarr'.
        self.add_to_bucket("PV", folder="PV_resized")

        self.assertEqual(self.run_step("download_image", "PV"), 0)
        self.assertEqual(os.listdir(self.cochlea_path()), ["PV.ome.zarr"])
        self.assertTrue(os.path.exists(self.cochlea_path("PV.ome.zarr", "s0", ".zarray")))

        # The stain is the name of the source, not the name of the folder.
        self.assertNotEqual(self.run_step("download_image", "PV_resized"), 0)

    def test_download_of_a_source_without_image_data_fails(self):
        self.add_to_bucket("PV")
        self.sources["PV"] = {"segmentation": {"tableData": {"tsv": {"relativePath": "tables/PV"}}}}
        self.write_dataset()

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
        write_chunks(self.cochlea_path("PV.ome.zarr"))
        self.add_to_bucket("PV")

        self.assertEqual(self.run_step("cleanup_input", "PV", "PV.ome.zarr", "s0"), 0)
        self.assertEqual(os.listdir(self.cochlea_path()), [])

    def test_input_stays_without_a_complete_copy_in_the_bucket(self):
        write_file(self.cochlea_path("PV.ome.zarr", "s0", ".zarray"))
        self.assertNotEqual(self.run_step("cleanup_input", "PV", "PV.ome.zarr", "s0"), 0)

        self.add_to_bucket("PV", listed=False)
        self.assertNotEqual(self.run_step("cleanup_input", "PV", "PV.ome.zarr", "s0"), 0)

        # A source without image data gives nothing for 'download_image' to restore.
        self.sources["PV"] = {"segmentation": {"tableData": {"tsv": {"relativePath": "tables/PV"}}}}
        self.write_dataset()
        self.assertNotEqual(self.run_step("cleanup_input", "PV", "PV.ome.zarr", "s0"), 0)
        self.assertTrue(os.path.exists(self.cochlea_path("PV.ome.zarr", "s0", ".zarray")))

    def test_ome_zarr_of_a_source_with_another_folder_name_is_removed(self):
        write_file(self.cochlea_path("PV.ome.zarr", "s0", ".zarray"))
        self.add_to_bucket("PV", folder="PV_resized")

        self.assertEqual(self.run_step("cleanup_input", "PV", "PV.ome.zarr", "s0"), 0)
        self.assertEqual(os.listdir(self.cochlea_path()), [])


class CleanupTemplateTest(unittest.TestCase):
    """Run the rendered `cleanup_SGN` job. A test of `test_templates.py` keeps the removal the same in
    every cleanup template."""

    def test_intermediate_data_is_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "predictions", "M_LR_000226_R", "SGN_v3")
            for name in ("mask.zarr", "predictions.zarr", "seeds.zarr", "segmentation.zarr"):
                write_chunks(os.path.join(folder, name))
            values = {"job_name": "job", "user_address": "a", "account_name": "b", "data_dir": tmp,
                      "cochlea": "M_LR_000226_R", "prediction_dir": "SGN_v3", "time": "00:10:00", "mem": "1G"}
            sbatch_file = os.path.join(tmp, "cleanup_SGN.sbatch")
            with contextlib.redirect_stdout(io.StringIO()):
                render_template(step_template(TEMPLATE_DIR, "cleanup_SGN"), sbatch_file, values)

            result = run(["bash", sbatch_file], env={"PATH": "/usr/bin:/bin"})

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(os.listdir(folder), ["segmentation.zarr"])
            self.assertIn(f"Removed {folder}/predictions.zarr in ", result.stdout)


class StepRowTest(unittest.TestCase):
    """The steps file of cochlea-net gives the group and the resources of every template."""

    def test_every_template_has_a_row(self):
        steps = json.loads(read_file(os.path.join(TEMPLATE_DIR, deployment.STEPS_FILE)))
        templates = glob.glob(os.path.join(TEMPLATE_DIR, f"*{TEMPLATE_SUFFIX}"))

        self.assertEqual(sorted(steps), sorted(os.path.basename(path)[:-len(TEMPLATE_SUFFIX)] for path in templates))
        for template in templates:
            with self.subTest(template):
                group, resources, time_rule = deployment.step_row(template, "M")
                self.assertIn(group, list(deployment.GROUPS) + [None])
                expected = ["mem", "time"]
                if f"<{deployment.INSTANCES_KEY}>" in read_file(template):
                    expected.append(deployment.INSTANCES_KEY)
                self.assertEqual(sorted(resources), sorted(expected))
                if time_rule is not None:
                    deployment.time_from_size(10 ** 12, time_rule)

    def test_every_species_of_the_steps_file_is_valid(self):
        steps = json.loads(read_file(os.path.join(TEMPLATE_DIR, deployment.STEPS_FILE)))
        for step, row in steps.items():
            for species in set(row) - {deployment.GROUP_KEY, deployment.TIME_RULE_KEY, *deployment.RESOURCE_KEYS}:
                with self.subTest(step=step, species=species):
                    _, _, time_rule = deployment.step_row(step_template(TEMPLATE_DIR, step), species)
                    if time_rule is not None:
                        deployment.time_from_size(10 ** 12, time_rule)

    def test_species_deviation(self):
        sgn = step_template(TEMPLATE_DIR, "apply_SGN")
        ihc = step_template(TEMPLATE_DIR, "apply_IHC")

        self.assertEqual(deployment.step_row(sgn, "M"),
                         ("SGN", {"time": "00:25:00", "mem": "96G", "instances": 10}, None))
        self.assertEqual(deployment.step_row(sgn, "G"),
                         ("SGN", {"time": "01:30:00", "mem": "128G", "instances": 10}, None))
        self.assertEqual(deployment.step_row(ihc, "M")[1]["instances"], 6)
        self.assertEqual(deployment.step_row(ihc, "G")[1]["instances"], 10)

    def time_rule_row(self, tmp, without=(), **row):
        rule = {"input": "<data_dir>/<cochlea>/1_raw", "mb_per_s": 100, "margin": 1.5, "round": "00:30:00",
                "min": "01:00:00", "max": "48:00:00"}
        rule = {key: value for key, value in rule.items() if key not in without}
        write_file(os.path.join(tmp, deployment.STEPS_FILE), json.dumps({"convert_data": {
            "group": None, "time": "08:00:00", "mem": "16G", deployment.TIME_RULE_KEY: rule, **row}}))
        return write_file(os.path.join(tmp, "convert_data.template"))

    def test_species_changes_one_value_of_the_time_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            template = self.time_rule_row(tmp, G={deployment.TIME_RULE_KEY: {"mb_per_s": 80}, "time": "12:00:00"})

            _, mouse, mouse_rule = deployment.step_row(template, "M")
            _, gerbil, gerbil_rule = deployment.step_row(template, "G")

        self.assertEqual((mouse["time"], gerbil["time"]), ("08:00:00", "12:00:00"))
        self.assertEqual((mouse_rule["mb_per_s"], gerbil_rule["mb_per_s"]), (100, 80))
        self.assertEqual({**gerbil_rule, "mb_per_s": 100}, mouse_rule)

    def test_time_rule_with_a_missing_or_unknown_key_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            template = self.time_rule_row(tmp, without=("min",))
            with self.assertRaisesRegex(ValueError, r"Missing: \['min'\]"):
                deployment.step_row(template, "M")

            template = self.time_rule_row(tmp, G={deployment.TIME_RULE_KEY: {"rate": 100}})
            deployment.step_row(template, "M")
            with self.assertRaisesRegex(ValueError, r"Unknown: \['rate'\]"):
                deployment.step_row(template, "G")

    def test_species_rule_without_a_row_rule_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_file(os.path.join(tmp, deployment.STEPS_FILE), json.dumps({"convert_data": {
                "group": None, "time": "08:00:00", "mem": "16G", "G": {deployment.TIME_RULE_KEY: {"mb_per_s": 80}}}}))
            template = write_file(os.path.join(tmp, "convert_data.template"))

            self.assertIsNone(deployment.step_row(template, "M")[2])
            with self.assertRaisesRegex(ValueError, "has no such rule"):
                deployment.step_row(template, "G")

    def test_time_from_size(self):
        rule = {"mb_per_s": 100, "margin": 1.5, "round": "00:30:00", "min": "01:00:00", "max": "48:00:00"}

        self.assertEqual(deployment.time_from_size(720 * 10 ** 9, rule), "03:00:00")
        self.assertEqual(deployment.time_from_size(720 * 10 ** 9, {**rule, "round": "00:10:00"}), "03:00:00")
        self.assertEqual(deployment.time_from_size(721 * 10 ** 9, {**rule, "round": "00:10:00"}), "03:10:00")
        self.assertEqual(deployment.time_from_size(10 ** 9, {**rule, "min": "00:15:00"}), "00:30:00")
        with self.assertRaisesRegex(ValueError, "HH:MM:SS"):
            deployment.time_from_size(10 ** 9, {**rule, "max": "2-00:00:00"})
        with self.assertRaisesRegex(ValueError, "positive"):
            deployment.time_from_size(10 ** 9, {**rule, "mb_per_s": 0})

    def test_template_without_row_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_file(os.path.join(tmp, deployment.STEPS_FILE), json.dumps({"apply_SGN": {"group": "SGN"}}))
            template = write_file(os.path.join(tmp, "predict_SGN.template"))

            with self.assertRaises(ValueError):
                deployment.step_row(template, "M")

    def test_project_of_template(self):
        self.assertEqual(project_of_template(step_template(TEMPLATE_DIR, "apply_SGN")), "cochlea-net")


class JobNameTest(unittest.TestCase):
    """The deployment builds the Slurm job name from the name of the sbatch script, so it names the step, the group,
    a non-default stain and the model version."""

    def test_every_template_takes_the_job_name_of_the_deployment(self):
        rows = json.loads(read_file(os.path.join(TEMPLATE_DIR, deployment.STEPS_FILE)))

        self.assertGreater(len(rows), 20)
        for step in sorted(rows):
            with self.subTest(step):
                job_names = re.findall(r"^#SBATCH --job-name=(\S+)", read_file(step_template(TEMPLATE_DIR, step)),
                                       re.MULTILINE)
                self.assertEqual(job_names, ["<job_name>"])


if __name__ == "__main__":
    unittest.main()
