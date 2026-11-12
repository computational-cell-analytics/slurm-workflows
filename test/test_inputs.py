#!/usr/bin/python
# -- coding: utf-8 --
"""Tests of the check of missing files: `slurm_workflows/inputs.py` and the input choice of cochlea-net."""
import argparse
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from slurm_workflows.projects.cochlea_net import deployment, mobie
from slurm_workflows.inputs import check_external_input, check_job_input, check_output_absent, job_variables
from slurm_workflows.templates import render_template, step_template
from stubs import MICROMAMBA_STUB, TEMPLATE_DIR, read_file, run, write_executable, write_file


def deploy_args(**options):
    return argparse.Namespace(**{"group": None, "no_mobie": False, "no_cleanup": False, "download": False,
                                 "cleanup_input": False, **options})


class InputCheckTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self.tmp.name, "data")
        write_file(os.path.join(self.data, "image.n5", "attributes.json"))

    def tearDown(self):
        self.tmp.cleanup()

    def sbatch(self, *lines):
        return write_file(os.path.join(self.tmp.name, "job.sbatch"), "#!/bin/bash\n" + "\n".join(lines) + "\n")

    def test_job_variables(self):
        sbatch = self.sbatch("INPUT=/first", "export OUTPUT_FOLDER='/out'", "  INPUT=\"/second\"",
                             "OTHER=/ignored", "# INPUT=/comment")

        self.assertEqual(job_variables(sbatch), {"INPUT": "/second", "OUTPUT_FOLDER": "/out"})

    def test_job_variables_like_the_shell(self):
        sbatch = self.sbatch('export INPUT="/a b/c.n5"  # the input', "OUTPUT_FOLDER=/out INPUT_KEY=s0",
                             "INPUT_DIR=/ignored", "EXTERNAL_INPUT='unbalanced")

        self.assertEqual(job_variables(sbatch), {"INPUT": "/a b/c.n5", "OUTPUT_FOLDER": "/out", "INPUT_KEY": "s0"})

    def test_no_input(self):
        self.assertEqual(check_job_input(self.sbatch("echo hi")), [])

    def test_existing_input(self):
        self.assertEqual(check_job_input(self.sbatch(f"INPUT={self.data}/image.n5")), [])

    def test_missing_input(self):
        warnings = check_job_input(self.sbatch(f"export INPUT={self.data}/missing.n5"))

        self.assertEqual(len(warnings), 1)
        self.assertIn("missing.n5", warnings[0])

    def test_relative_input_is_skipped(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(check_job_input(self.sbatch("INPUT=bucket/key.ome.zarr")), [])

    def test_wildcard_input(self):
        self.assertEqual(check_job_input(self.sbatch(f"INPUT={self.data}/*.n5")), [])
        self.assertEqual(len(check_job_input(self.sbatch(f"INPUT={self.data}/*.zarr"))), 1)

    def test_external_input(self):
        self.assertEqual(check_external_input(self.sbatch(f"export EXTERNAL_INPUT={self.data}/image.n5")), [])
        self.assertEqual(len(check_external_input(self.sbatch(f"export EXTERNAL_INPUT={self.data}/none"))), 1)
        self.assertEqual(check_external_input(self.sbatch("echo hi")), [])

    def test_output_absent(self):
        self.assertEqual(check_output_absent(self.sbatch(f"EXISTING_OUTPUT={self.data}/none.tsv")), [])
        self.assertEqual(len(check_output_absent(self.sbatch(f"EXISTING_OUTPUT={self.data}/image.n5"))), 1)


class ResolveInputTest(unittest.TestCase):
    """An n5 wins, the OME-Zarr from the S3 bucket is the fallback."""

    COCHLEA = "G_LR_000301_L"
    STAINS = ["PV", "Vglut3", "CTBP2"]
    MULTI = "GLR_301L_PV_Vglut3_CTBP2_fused.n5"
    SINGLE = "GLR_301L_Vglut3_fused.n5"
    OME_ZARR = "Vglut3.ome.zarr"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cochlea_dir = os.path.join(self.tmp.name, self.COCHLEA)
        self.module = deployment

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, *names):
        for name in names:
            if name.endswith(".n5"):
                for setup in range(len(self.STAINS)):
                    write_file(os.path.join(self.cochlea_dir, name, f"setup{setup}", "timepoint0", "s0",
                                            "attributes.json"))
            else:
                write_file(os.path.join(self.cochlea_dir, name, "s0", ".zarray"))

    def resolve(self, stains):
        candidates = self.module.n5_candidates("GLR", "000301", "L", stains, "Vglut3", "")
        candidates.append((self.OME_ZARR, "s0"))
        return self.module.resolve_input(self.cochlea_dir, candidates)

    def mobie_values(self):
        values = {"data_dir": self.tmp.name, "cochlea": self.COCHLEA, "stains": self.STAINS}
        values, _, _ = self.module.prepare_step({"data_dir": self.tmp.name, "models": {}}, values,
                                                step_template(TEMPLATE_DIR, "mobie_add_image"),
                                                deploy_args())
        return values

    def test_multi_n5_wins(self):
        self.add(self.MULTI, self.SINGLE, self.OME_ZARR)
        self.assertEqual(self.resolve(self.STAINS), (self.MULTI, "setup1/timepoint0/s0"))

    def test_single_n5_wins_over_ome_zarr(self):
        self.add(self.SINGLE, self.OME_ZARR)
        self.assertEqual(self.resolve(self.STAINS), (self.SINGLE, "setup0/timepoint0/s0"))
        self.assertEqual(self.resolve(None), (self.SINGLE, "setup0/timepoint0/s0"))

    def test_ome_zarr_fallback(self):
        self.add(self.OME_ZARR)
        self.assertEqual(self.resolve(self.STAINS), (self.OME_ZARR, "s0"))

    def test_n5_without_the_setup_of_the_stain(self):
        self.add(self.MULTI, self.OME_ZARR)
        shutil.rmtree(os.path.join(self.cochlea_dir, self.MULTI, "setup1"))
        self.assertEqual(self.resolve(self.STAINS), (self.OME_ZARR, "s0"))

        n5 = os.path.join(self.cochlea_dir, self.MULTI)
        for input_key, count in (("setup0/timepoint0/s0", 0), ("setup1/timepoint0/s0", 1), ("${KEYS[$i]}", 0)):
            sbatch = write_file(os.path.join(self.tmp.name, "job.sbatch"),
                                f"export INPUT={n5}\nexport INPUT_KEY={input_key}\n")
            self.assertEqual(len(self.module.check_steps(["mask_IHC.template"], [sbatch])), count, input_key)

    def test_none_reports_first_candidate(self):
        self.assertEqual(self.resolve(self.STAINS), (self.MULTI, "setup1/timepoint0/s0"))
        self.assertEqual(self.resolve(None), (self.SINGLE, "setup0/timepoint0/s0"))

    def test_stain_not_in_stains(self):
        self.assertEqual(self.module.n5_candidates("GLR", "000301", "L", ["PV"], "Vglut3", ""),
                         [(self.SINGLE, "setup0/timepoint0/s0")])

    def test_mobie_image_multi_n5(self):
        self.add(self.MULTI)
        values = self.mobie_values()

        self.assertEqual(values["cochlea_data_multi"], " ".join([self.MULTI] * 3))
        self.assertEqual(values["input_key_multi"],
                         "setup0/timepoint0/s0 setup1/timepoint0/s0 setup2/timepoint0/s0")
        self.assertEqual(values["channel_multi"], "PV Vglut3 CTBP2")

    def test_mobie_image_n5_per_stain(self):
        files = [f"GLR_301L_{stain}_fused.n5" for stain in self.STAINS]
        self.add(*files)
        values = self.mobie_values()

        self.assertEqual(values["cochlea_data"], files[0])
        self.assertEqual(values["cochlea_data_multi"], " ".join(files))
        self.assertEqual(values["input_key_multi"], " ".join(["setup0/timepoint0/s0"] * 3))

    def ihc_step(self, download=False, definition=None, step="mask_IHC", **parameters):
        values = {"data_dir": self.tmp.name, "cochlea": self.COCHLEA, "stains": self.STAINS,
                  "ihc_version": "IHC_v11", "s3_remote": "remote:bucket", **parameters}
        settings = {"data_dir": self.tmp.name, "models": {"IHC": {"IHC_v11": "/path/to/model"}}}
        values, _, notes = self.module.prepare_step(settings, values, step_template(TEMPLATE_DIR, step),
                                                    deploy_args(download=download), definition)
        return values, " ".join(notes)

    def test_resources_of_the_species_and_of_a_parameter(self):
        values, _ = self.ihc_step()
        self.assertEqual((values["time"], values["mem"]), ("01:10:00", "32G"))

        values, _ = self.ihc_step(time="09:00:00")
        self.assertEqual((values["time"], values["mem"]), ("09:00:00", "32G"))

    def test_array_size_of_the_species_and_of_a_parameter(self):
        self.add(self.MULTI)
        values, _ = self.ihc_step(step="apply_IHC")
        self.assertEqual((values["instances"], values["last_instance"]), (10, 9))

        values, _ = self.ihc_step(step="apply_IHC", instances="2")
        self.assertEqual((values["instances"], values["last_instance"]), ("2", 1))

        for instances in (0, "-1", "four"):
            with self.subTest(instances), self.assertRaises(ValueError):
                self.ihc_step(step="apply_IHC", instances=instances)

    def test_watershed_values_per_group(self):
        settings = {"data_dir": self.tmp.name,
                    "models": {"SGN": {"SGN_v3": "/path/to/model"}, "IHC": {"IHC_v11": "/path/to/model"}}}
        parameters = {"data_dir": self.tmp.name, "cochlea": self.COCHLEA, "sgn_version": "SGN_v3",
                      "ihc_version": "IHC_v11", "sgn_center_distance_threshold": "0.3"}
        sgn, _, _ = self.module.prepare_step(settings, dict(parameters), step_template(TEMPLATE_DIR, "segment_SGN"),
                                             deploy_args())
        ihc, _, _ = self.module.prepare_step(settings, dict(parameters), step_template(TEMPLATE_DIR, "segment_IHC"),
                                             deploy_args())

        defaults = self.module.WATERSHED_DEFAULTS
        self.assertEqual(sgn["sgn_center_distance_threshold"], "0.3")
        self.assertEqual(sgn["sgn_boundary_distance_threshold"], defaults["SGN"]["boundary_distance_threshold"])
        self.assertEqual(ihc["ihc_center_distance_threshold"], defaults["IHC"]["center_distance_threshold"])

    def test_download_reads_the_ome_zarr(self):
        self.add(self.MULTI)
        values, _ = self.ihc_step(download=True, definition={"name": "ihc", "steps": ["mask_IHC", "apply_IHC"]})

        self.assertEqual((values["cochlea_data"], values["input_key"]), (self.OME_ZARR, "s0"))
        self.assertEqual(values["stain"], "Vglut3")

    def test_pipeline_options_need_a_pipeline(self):
        with self.assertRaisesRegex(ValueError, "--download"):
            self.ihc_step(download=True)

    def test_s3_note(self):
        definition = {"name": "ihc", "steps": ["mask_IHC", "apply_IHC"]}
        dataset = json.dumps({"sources": {"Vglut3": {}}})
        with mock.patch.object(self.module, "s3_datasets", return_value=[self.COCHLEA]), \
                mock.patch.object(self.module, "rclone", return_value=dataset):
            self.assertIn("--download", self.ihc_step(definition=definition)[1])
            self.assertNotIn("S3 bucket", self.ihc_step(download=True, definition=definition)[1])

        # The transfer of a partial copy did not list the source.
        with mock.patch.object(self.module, "s3_datasets", return_value=[self.COCHLEA]), \
                mock.patch.object(self.module, "rclone", return_value=json.dumps({"sources": {}})):
            self.assertIn("either", self.ihc_step(definition=definition)[1])

        with mock.patch.object(self.module, "s3_datasets", return_value=[]):
            self.assertIn("either", self.ihc_step(definition=definition)[1])


class ConversionTest(unittest.TestCase):
    """The conversion reads the raw tiles of every stain, so it needs no group, no stain and no model."""

    COCHLEA = "Re_AMD_WT2_MWfLS"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.template = step_template(TEMPLATE_DIR, deployment.CONVERSION_STEP)

    def tearDown(self):
        self.tmp.cleanup()

    def prepare(self, args=None, **parameters):
        values = {"data_dir": self.tmp.name, "cochlea": self.COCHLEA, **parameters}
        values, script_name, _ = deployment.prepare_step({"data_dir": self.tmp.name, "models": {}}, values,
                                                         self.template, args or deploy_args())
        return values, script_name

    def test_defaults(self):
        values, script_name = self.prepare()

        self.assertEqual((values["raw_folder"], values["converted_name"]), ("1_raw", self.COCHLEA))
        self.assertEqual((values["time"], values["mem"]), ("20:00:00", "96G"))
        self.assertNotIn("stain", values)
        self.assertEqual(script_name, "convert_data_ReAMDWT2MWfLS")

    def test_parameters_override_the_defaults(self):
        values, _ = self.prepare(raw_folder="1_raw_reimage", converted_name="MAMD_N235L_CTBP2", time="30:00:00")

        self.assertEqual((values["raw_folder"], values["converted_name"]), ("1_raw_reimage", "MAMD_N235L_CTBP2"))
        self.assertEqual(values["time"], "30:00:00")

    def test_no_group(self):
        with self.assertRaisesRegex(ValueError, "no group"):
            self.prepare(deploy_args(group="SGN"))

    def test_job(self):
        bin_dir = os.path.join(self.tmp.name, "stub_bin")
        os.makedirs(bin_dir)
        write_executable(os.path.join(bin_dir, "micromamba"), MICROMAMBA_STUB)
        args_file = os.path.join(self.tmp.name, "convert_args")
        write_executable(os.path.join(bin_dir, "flamingo_tools.convert_data"),
                         f'#!/bin/bash\nprintf "%s\\n" "$@" > {args_file}\n')
        env = {"PATH": os.pathsep.join([bin_dir, "/usr/bin", "/bin"]), "HOME": self.tmp.name,
               "SLURM_CPUS_PER_TASK": "4"}

        values, _ = self.prepare(user_address="a", account_name="b", **{"cochlea-net_environment": "env"})
        sbatch_file = os.path.join(self.tmp.name, "job.sbatch")
        with contextlib.redirect_stdout(io.StringIO()):
            render_template(self.template, sbatch_file, values)

        raw_dir = os.path.join(self.tmp.name, self.COCHLEA, "1_raw")
        self.assertEqual(len(check_job_input(sbatch_file)), 1)
        self.assertNotEqual(run(["bash", sbatch_file], env=env).returncode, 0)
        self.assertFalse(os.path.exists(args_file))

        os.makedirs(raw_dir)
        self.assertEqual(check_job_input(sbatch_file), [])
        self.assertEqual(run(["bash", sbatch_file], env=env).returncode, 0)
        output = os.path.join(self.tmp.name, self.COCHLEA, "2_converted_stitching", f"{self.COCHLEA}.n5")
        self.assertEqual(read_file(args_file).splitlines(),
                         ["-i", raw_dir, "--out_path", output, "--file_ext", ".raw", "--n_threads", "4"])
        self.assertTrue(os.path.isdir(os.path.dirname(output)))


class SynapseNameTest(unittest.TestCase):
    """The name of the synapse jobs is also the MoBIE source, so `add_s3_dataset --check` must accept it."""

    def test_synapse_ihc_prediction_name(self):
        module = deployment
        cases = [(("CTBP2", "synapses_v3", "Vglut3", "IHC_v11"), "synapse_v3_ihc_v11"),
                 (("Homer1", "synapse_v3", "Vglut3", "IHC_v4b"), "Homer1_synapse_v3_ihc_v4b"),
                 (("CTBP2", "synapse_v3", "Myo7a", "IHC_v11"), "synapse_v3_ihc_myo7a_v11")]
        for args, name in cases:
            self.assertEqual(module.synapse_ihc_prediction_name(*args), name)
            self.assertTrue(mobie.SYNAPSE_NAME.fullmatch(name), name)


class PreviousRunTest(unittest.TestCase):
    """A mask step keeps an existing mask, even an incomplete one, so its rerun gives a warning."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.module = deployment
        self.sbatch = write_file(os.path.join(self.tmp.name, "job.sbatch"),
                                 f"#!/bin/bash\nexport OUTPUT_FOLDER={self.tmp.name}\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_mask_of_previous_run(self):
        self.assertEqual(self.module.check_prediction_absent("mask_IHC.template", self.sbatch), [])

        write_file(os.path.join(self.tmp.name, "mask.zarr", ".zgroup"))
        self.assertEqual(len(self.module.check_prediction_absent("mask_IHC.template", self.sbatch)), 1)
        self.assertEqual(self.module.check_prediction_absent("apply_IHC.template", self.sbatch), [])


class RcloneRemoteTest(unittest.TestCase):
    """A transfer job needs its rclone remote in the rclone config."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = os.path.join(self.tmp.name, "rclone.conf")
        self.module = deployment

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, *lines):
        sbatch = write_file(os.path.join(self.tmp.name, "job.sbatch"), "#!/bin/bash\n" + "\n".join(lines) + "\n")
        with mock.patch.dict(os.environ, {"RCLONE_CONFIG": self.config}):
            return self.module.check_rclone_remotes([sbatch, sbatch])

    def test_remote_in_config(self):
        write_file(self.config, "[other]\ntype = s3\n\n[cochlea-lightsheet]\ntype = s3\n")
        self.assertEqual(self.check("S3_REMOTE=cochlea-lightsheet:cochlea-lightsheet"), [])

    def test_remote_missing(self):
        self.assertEqual(len(self.check("S3_REMOTE=cochlea-lightsheet:cochlea-lightsheet")), 1)

        write_file(self.config, "[other]\ntype = s3\n")
        warnings = self.check("S3_REMOTE=cochlea-lightsheet:cochlea-lightsheet")
        self.assertEqual(len(warnings), 1)
        self.assertIn("'cochlea-lightsheet'", warnings[0])

    def test_no_transfer(self):
        self.assertEqual(self.check("echo hi"), [])


if __name__ == "__main__":
    unittest.main()
