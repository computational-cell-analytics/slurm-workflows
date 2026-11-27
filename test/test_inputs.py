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
from slurm_workflows.templates import fill_placeholders, render_template, step_template
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
        values, _, _, _ = self.module.prepare_step({"data_dir": self.tmp.name, "models": {}}, values,
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

    def ihc_step(self, download=False, definition=None, step="mask_IHC", models=None, **parameters):
        values = {"data_dir": self.tmp.name, "cochlea": self.COCHLEA, "stains": self.STAINS,
                  "ihc_version": "IHC_v11", "s3_remote": "remote:bucket", **parameters}
        settings = {"data_dir": self.tmp.name, "models": models or {"IHC": {"IHC_v11": "/path/to/model"}}}
        values, _, notes, _ = self.module.prepare_step(settings, values, step_template(TEMPLATE_DIR, step),
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
        sgn, _, _, _ = self.module.prepare_step(settings, dict(parameters), step_template(TEMPLATE_DIR, "segment_SGN"),
                                                deploy_args())
        ihc, _, _, _ = self.module.prepare_step(settings, dict(parameters), step_template(TEMPLATE_DIR, "segment_IHC"),
                                                deploy_args())

        defaults = self.module.WATERSHED_DEFAULTS
        self.assertEqual(sgn["sgn_center_distance_threshold"], "0.3")
        self.assertEqual(sgn["sgn_boundary_distance_threshold"], defaults["SGN"]["boundary_distance_threshold"])
        self.assertEqual(ihc["ihc_center_distance_threshold"], defaults["IHC"]["center_distance_threshold"])

    def test_mask_defaults_and_parameters(self):
        values, _ = self.ihc_step()
        self.assertEqual((values["ihc_masking_threshold"], values["ihc_legacy_mask"]), ("None", "false"))

        values, _ = self.ihc_step(ihc_masking_threshold="150", ihc_legacy_mask="true")
        self.assertEqual((values["ihc_masking_threshold"], values["ihc_legacy_mask"]), ("150", "true"))

        # The values of a JSON parameter file.
        values, _ = self.ihc_step(ihc_masking_threshold=150, ihc_legacy_mask=True)
        self.assertEqual((values["ihc_masking_threshold"], values["ihc_legacy_mask"]), ("150", "true"))
        values, _ = self.ihc_step(ihc_masking_threshold=None)
        self.assertEqual(values["ihc_masking_threshold"], "None")

    def test_mask_threshold_needs_the_legacy_rule(self):
        with self.assertRaisesRegex(ValueError, "ihc_legacy_mask"):
            self.ihc_step(ihc_masking_threshold=200)
        with self.assertRaisesRegex(ValueError, "true or false"):
            self.ihc_step(ihc_legacy_mask="on")

        # Only the mask step reads the threshold, so a later step accepts an older parameter file.
        self.add(self.MULTI)
        values, _ = self.ihc_step(step="apply_IHC", ihc_masking_threshold=200)
        self.assertNotIn("ihc_legacy_mask", values)

    def test_alias_key_names_the_prediction(self):
        models = {"IHC": {"IHC_v11": "/path/to/v11", "IHC_v11b": "/path/to/v11"}}
        values, _ = self.ihc_step(models=models, ihc_version="IHC_v11b", synapse_version="synapse_v3")
        self.assertEqual(values["model"], "/path/to/v11")
        self.assertEqual(values["prediction_dir"], "IHC_v11b")
        self.assertEqual(values["synapse_ihc_prediction"], "synapse_v3_ihc_v11b")

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
        # The OME-Zarr folder of a source can have another name than the source.
        image = {"image": {"imageData": {"ome.zarr": {"relativePath": "images/ome-zarr/Vglut3_resized.ome.zarr"}}}}
        dataset = json.dumps({"sources": {"Vglut3": image}})
        with mock.patch.object(self.module, "s3_datasets", return_value=[self.COCHLEA]), \
                mock.patch.object(self.module, "rclone", return_value=dataset):
            note = self.ihc_step(definition=definition)[1]
            self.assertIn("--download", note)
            self.assertIn(f"{self.COCHLEA}/images/ome-zarr/Vglut3_resized.ome.zarr", note)
            self.assertNotIn("S3 bucket", self.ihc_step(download=True, definition=definition)[1])

        # The transfer of a partial copy did not list the source.
        with mock.patch.object(self.module, "s3_datasets", return_value=[self.COCHLEA]), \
                mock.patch.object(self.module, "rclone", return_value=json.dumps({"sources": {}})):
            self.assertIn("either", self.ihc_step(definition=definition)[1])

        # A source without image data, e.g. a table only, gives nothing to download.
        table = {"segmentation": {"tableData": {"tsv": {"relativePath": "tables/Vglut3"}}}}
        with mock.patch.object(self.module, "s3_datasets", return_value=[self.COCHLEA]), \
                mock.patch.object(self.module, "rclone", return_value=json.dumps({"sources": {"Vglut3": table}})):
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
        values, script_name, self.notes, _ = deployment.prepare_step({"data_dir": self.tmp.name, "models": {}},
                                                                     values, self.template, args or deploy_args())
        return values, script_name

    def raw_files(self, *sizes, raw_folder="1_raw"):
        """Write sparse raw files of these sizes in bytes, so that a size of terabytes costs no disk space."""
        for index, size in enumerate(sizes):
            path = os.path.join(self.tmp.name, self.COCHLEA, raw_folder, f"stain_{index % 2}", f"tile_{index}.raw")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as myfile:
                myfile.truncate(size)

    def test_defaults(self):
        values, script_name = self.prepare()

        self.assertEqual((values["raw_folder"], values["converted_name"]), ("1_raw", self.COCHLEA))
        self.assertEqual((values["time"], values["mem"]), ("08:00:00", "16G"))
        self.assertNotIn("stain", values)
        self.assertEqual(script_name, "convert_data_ReAMDWT2MWfLS")

    def test_time_of_the_steps_file_without_the_raw_folder(self):
        raw_dir = os.path.join(self.tmp.name, self.COCHLEA, "1_raw")

        values, _ = self.prepare()

        self.assertEqual(values["time"], "08:00:00")
        self.assertEqual(self.notes, [f"convert_data: time limit 08:00:00 of steps.json, because {raw_dir} does "
                                      "not exist."])

    def test_parameters_override_the_defaults(self):
        values, _ = self.prepare(raw_folder="1_raw_reimage", converted_name="MAMD_N235L_CTBP2", time="30:00:00")

        self.assertEqual((values["raw_folder"], values["converted_name"]), ("1_raw_reimage", "MAMD_N235L_CTBP2"))
        self.assertEqual(values["time"], "30:00:00")

    def test_no_group(self):
        with self.assertRaisesRegex(ValueError, "no group"):
            self.prepare(deploy_args(group="SGN"))

    def test_time_rule_of_the_steps_file(self):
        _, _, rule = deployment.step_row(self.template, "M")
        gb = 10 ** 9
        cases = [(0, "01:00:00"), (240 * gb, "01:00:00"), (241 * gb, "01:30:00"), (720 * gb, "03:00:00"),
                 (1010 * gb, "04:30:00"), (1955 * gb, "08:30:00"), (20000 * gb, "48:00:00")]
        for raw_bytes, expected in cases:
            self.assertEqual(deployment.time_from_size(raw_bytes, rule), expected, raw_bytes)

    def test_time_from_the_size_of_the_raw_folder(self):
        # The 27 tiles of R_AMD_WT1_L, 720.6 GB, which converted in 1.9 h on 2026-11-25.
        self.raw_files(*[4242 * 2048 * 1536 * 2] * 27)
        raw_dir = os.path.join(self.tmp.name, self.COCHLEA, "1_raw")

        values, _ = self.prepare()

        self.assertEqual(values["time"], "03:30:00")
        self.assertEqual(self.notes, [f"convert_data: time limit 03:30:00 for 721 GB in {raw_dir}, at 100 MB/s with a "
                                      "margin of 1.5. Set 'time' in the parameter file to override it."])

    def test_gerbil_gets_the_time_from_the_size(self):
        self.COCHLEA = "G_EK_000233_L"
        self.raw_files(1306 * 10 ** 9)

        self.assertEqual(self.prepare()[0]["time"], "05:30:00")

    def test_time_of_another_raw_folder(self):
        self.raw_files(500 * 10 ** 9, raw_folder="1_raw_reimage")

        self.assertEqual(self.prepare(raw_folder="1_raw_reimage")[0]["time"], "02:30:00")

    def test_time_parameter_wins_over_the_size(self):
        self.raw_files(2000 * 10 ** 9)

        values, _ = self.prepare(time="05:00:00")

        self.assertEqual(values["time"], "05:00:00")
        self.assertEqual(self.notes, [])

    def test_job(self):
        bin_dir = os.path.join(self.tmp.name, "stub_bin")
        os.makedirs(bin_dir)
        write_executable(os.path.join(bin_dir, "micromamba"), MICROMAMBA_STUB)
        args_file = os.path.join(self.tmp.name, "convert_args")
        write_executable(os.path.join(bin_dir, "flamingo_tools.convert_data"),
                         f'#!/bin/bash\nprintf "%s\\n" "$@" > {args_file}\n')
        env = {"PATH": os.pathsep.join([bin_dir, "/usr/bin", "/bin"]), "HOME": self.tmp.name}

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
                         ["-i", raw_dir, "--out_path", output, "--file_ext", ".raw"])
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

    def test_parse_prediction_name_reads_the_names_back(self):
        """The backfill of `add_processing_metadata` reads the model versions and the stains of a job from its
        prediction folders."""
        for stain_ihc in ("Vglut3", "Myo7a"):
            for stain_synapses in ("CTBP2", "Homer1"):
                values = {"stain_IHC": stain_ihc, "stain_synapses": stain_synapses, "stain_SGN": "PV",
                          "ihc_version": "IHC_v11b", "synapse_version": "synapses_v3", "sgn_version": "SGN_v3"}
                with self.subTest(stain_ihc=stain_ihc, stain_synapses=stain_synapses):
                    for group in ("SGN", "IHC"):
                        [(models_group, key)] = deployment.job_models(group, values).items()
                        stain = deployment.job_stains(group, values)[models_group]
                        name = deployment.prediction_name(group, stain, key)
                        prefix = None if stain == deployment.GROUPS[group]["stain"] else stain
                        self.assertEqual(deployment.parse_prediction_name(name),
                                         {models_group: {"part": deployment.version_part(key), "key": key,
                                                         "stain": prefix}})

                    name = deployment.synapse_ihc_prediction_name(stain_synapses, "synapses_v3", stain_ihc, "IHC_v11b")
                    fields = deployment.parse_prediction_name(name)
                    self.assertEqual({group: entry["part"] for group, entry in fields.items()},
                                     {group: deployment.version_part(key)
                                      for group, key in deployment.job_models("synapses", values).items()})
                    self.assertEqual(fields["Synapses"]["stain"], None if stain_synapses == "CTBP2" else stain_synapses)
                    self.assertEqual(fields["IHC"]["stain"], None if stain_ihc == "Vglut3" else stain_ihc.lower())

        for name in ("PV", "Vglut3", "predictions.zarr", "G_LR_000301_L", "GLR_301L_PV_Vglut3_fused.n5",
                     "synapse_detection_model_v3.pt", "mask_synapses", "setup2", "IHC_v11.json",
                     'IHC_v11-"$VERSION', 'synapse_v3_ihc_v11-"$VERSION'):
            with self.subTest(name):
                self.assertEqual(deployment.parse_prediction_name(name), {})

    def test_short_cochlea_name_keeps_the_version_suffix(self):
        self.assertEqual(deployment.short_cochlea_name("M_AMD_000137_L"), "MAMD137L")
        self.assertEqual(deployment.short_cochlea_name("M_AMD_000137_L_v2"), "MAMD137Lv2")
        self.assertEqual(deployment.cochlea_parts("M_AMD_000137_L_v2"), ("M", "AMD", "000137", "L", "_v2"))
        for cochlea in ("M_AMD_000137", "M_AMD_000137_L_x2", "M_AMD_000137_L_"):
            with self.subTest(cochlea), self.assertRaisesRegex(ValueError, "does not have the format"):
                deployment.cochlea_parts(cochlea)


class JobNameTest(unittest.TestCase):
    """A stain which is not the default of the group and the model version enter the names, so two stains or two
    model versions never share an archive folder. metadata.json records the full model keys."""

    COCHLEA = "G_LR_000301_L"
    MODELS = {"IHC": {"IHC_v11": "/path/to/model", "IHC_v11b": "/path/to/model"},
              "Synapses": {"synapse_v3": "/path/to/model", "synapses_v3": "/path/to/model"}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def prepare(self, step, definition=None, **parameters):
        """Return the name of the sbatch script, the rendered Slurm job name and the metadata of the job."""
        values = {"data_dir": self.tmp.name, "cochlea": self.COCHLEA, "ihc_version": "IHC_v11", **parameters}
        template = step_template(TEMPLATE_DIR, step)
        values, script_name, _, metadata = deployment.prepare_step(
            {"data_dir": self.tmp.name, "models": self.MODELS}, values, template, deploy_args(), definition)
        job_name_line = read_file(template).splitlines()[1]
        return script_name, fill_placeholders([job_name_line], values)[0][0].split("=")[1], metadata

    def names(self, step, definition=None, **parameters):
        """Return the name of the sbatch script and the rendered Slurm job name."""
        return self.prepare(step, definition, **parameters)[:2]

    def test_default_stain_keeps_the_stain_out_of_the_names(self):
        for parameters in ({}, {"stain_synapses": "CTBP2"}):
            with self.subTest(parameters):
                self.assertEqual(self.names("apply_synapses", **parameters),
                                 ("apply_synapses_v3_ihc_v11_GLR301L", "apply-synapses-v3-ihc-v11_G-LR-000301-L"))

    def test_other_stain_enters_the_names(self):
        self.assertEqual(self.names("apply_synapses", stain_synapses="Homer1"),
                         ("apply_synapses_Homer1_v3_ihc_v11_GLR301L", "apply-synapses-Homer1-v3-ihc-v11_G-LR-000301-L"))
        self.assertEqual(self.names("mask_IHC", stain_IHC="Myo7a"),
                         ("mask_IHC_Myo7a_v11_GLR301L", "mask-IHC-Myo7a-v11_G-LR-000301-L"))

        # The stain of another group does not change the names, except the IHC stain of a synapse job.
        self.assertEqual(self.names("mask_IHC", stain_synapses="Homer1"),
                         ("mask_IHC_v11_GLR301L", "mask-IHC-v11_G-LR-000301-L"))
        self.assertEqual(self.names("apply_synapses", stain_IHC="Myo7a"),
                         ("apply_synapses_v3_ihc_myo7a_v11_GLR301L", "apply-synapses-v3-ihc-myo7a-v11_G-LR-000301-L"))

    def test_model_version_enters_the_names(self):
        definition = {"name": "ihc", "group": "IHC", "steps": ["mask_IHC"]}
        self.assertEqual(self.names("download_image", definition),
                         ("download_image_IHC_v11_GLR301L", "download-image-IHC-v11_G-LR-000301-L"))
        self.assertEqual(self.names("download_image", definition, ihc_version="IHC_v11b"),
                         ("download_image_IHC_v11b_GLR301L", "download-image-IHC-v11b_G-LR-000301-L"))

        # The spelling of the model key does not change the names.
        self.assertEqual(self.names("apply_synapses", synapse_version="synapses_v3"),
                         self.names("apply_synapses", synapse_version="synapse_v3"))

    def test_template_of_every_group_names_the_group_and_the_stain(self):
        definition = {"name": "synapses", "group": "synapses", "steps": ["mask_synapses"]}
        self.assertEqual(self.names("mobie_add_detection", definition, stain_synapses="Homer1"),
                         ("mobie_add_detection_synapses_Homer1_v3_ihc_v11_GLR301L",
                          "mobie-add-detection-synapses-Homer1-v3-ihc-v11_G-LR-000301-L"))

    def test_job_name_is_the_script_name(self):
        definition = {"name": "ihc", "group": "IHC", "steps": ["mask_IHC"]}
        cases = [("apply_IHC", None, {"stain_IHC": "Myo7a"}), ("segment_IHC", None, {}),
                 ("mobie_transfer_segmentation", definition, {}), ("cleanup_input", definition, {}),
                 ("mobie_add_image", None, {"stains": ["PV", "Vglut3"]}), ("convert_data", None, {})]
        for step, step_definition, parameters in cases:
            with self.subTest(step):
                script_name, job_name = self.names(step, step_definition, **parameters)
                self.assertEqual(job_name, script_name.rsplit("_", 1)[0].replace("_", "-") + "_G-LR-000301-L")

        self.assertEqual(self.names("apply_IHC")[1], "apply-IHC-v11_G-LR-000301-L")
        self.assertEqual(self.names("mobie_add_image", stains=["PV"])[1], "mobie-add-image_G-LR-000301-L")

    def test_metadata_holds_the_full_names(self):
        definition = {"name": "ihc", "group": "IHC", "steps": ["mask_IHC"]}
        cochlea = {"cochlea": self.COCHLEA}
        cases = [(("mask_IHC", None, {"stain_IHC": "Myo7a"}),
                  dict(cochlea, stains={"IHC": "Myo7a"}, models={"IHC": "IHC_v11"})),
                 (("download_image", definition, {"ihc_version": "IHC_v11b"}),
                  dict(cochlea, stains={"IHC": "Vglut3"}, models={"IHC": "IHC_v11b"})),
                 (("apply_synapses", None, {"synapse_version": "synapses_v3", "stain_IHC": "Myo7a"}),
                  dict(cochlea, stains={"Synapses": "CTBP2", "IHC": "Myo7a"},
                       models={"Synapses": "synapses_v3", "IHC": "IHC_v11"})),
                 (("mobie_add_image", None, {"stains": ["PV", "Vglut3"]}), cochlea),
                 (("convert_data", None, {}), cochlea)]
        for (step, step_definition, parameters), metadata in cases:
            with self.subTest(step):
                self.assertEqual(self.prepare(step, step_definition, **parameters)[2], metadata)

        script_name, _, metadata = self.prepare("apply_synapses")
        self.assertEqual(list(metadata), ["cochlea", "stains", "models"])
        self.assertEqual(list(metadata["models"]), ["Synapses", "IHC"])
        self.assertEqual(script_name, "apply_synapses_v3_ihc_v11_GLR301L")

    def test_cochlea_version_suffix_enters_the_names(self):
        self.assertEqual(self.names("mask_IHC", cochlea="M_AMD_000137_L_v2"),
                         ("mask_IHC_v11_MAMD137Lv2", "mask-IHC-v11_M-AMD-000137-L-v2"))


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
