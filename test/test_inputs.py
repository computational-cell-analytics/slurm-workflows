#!/usr/bin/python
# -- coding: utf-8 --
"""Tests of the check of missing files: `slurm_workflows/inputs.py` and the input choice of cochlea-net."""
import argparse
import contextlib
import io
import os
import tempfile
import unittest

from slurm_workflows.inputs import check_external_input, check_job_input, check_output_absent, job_variables
from stubs import cochlea_net_module, write_file


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
        self.module = cochlea_net_module()

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, *names):
        for name in names:
            write_file(os.path.join(self.cochlea_dir, name, "attributes.json"))

    def resolve(self, stains):
        candidates = self.module.n5_candidates("GLR", "000301", "L", stains, "Vglut3", "")
        candidates.append((self.OME_ZARR, "s0"))
        return self.module.resolve_input(self.cochlea_dir, candidates)

    def mobie_replacements(self):
        replacements = {"data_dir": self.tmp.name, "cochlea": self.COCHLEA, "stains": self.STAINS}
        replacements, _ = self.module.prepare_step({"data_dir": self.tmp.name, "models": {}}, replacements,
                                                   "mobie_add_image.template", argparse.Namespace(group=None))
        return replacements

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

    def test_none_reports_first_candidate(self):
        self.assertEqual(self.resolve(self.STAINS), (self.MULTI, "setup1/timepoint0/s0"))
        self.assertEqual(self.resolve(None), (self.SINGLE, "setup0/timepoint0/s0"))

    def test_stain_not_in_stains(self):
        self.assertEqual(self.module.n5_candidates("GLR", "000301", "L", ["PV"], "Vglut3", ""),
                         [(self.SINGLE, "setup0/timepoint0/s0")])

    def test_mobie_image_multi_n5(self):
        self.add(self.MULTI)
        replacements = self.mobie_replacements()

        self.assertEqual(replacements["cochlea_data_multi"], " ".join([self.MULTI] * 3))
        self.assertEqual(replacements["input_key_multi"],
                         "setup0/timepoint0/s0 setup1/timepoint0/s0 setup2/timepoint0/s0")
        self.assertEqual(replacements["channel_multi"], "PV Vglut3 CTBP2")

    def test_mobie_image_n5_per_stain(self):
        files = [f"GLR_301L_{stain}_fused.n5" for stain in self.STAINS]
        self.add(*files)
        replacements = self.mobie_replacements()

        self.assertEqual(replacements["cochlea_data"], files[0])
        self.assertEqual(replacements["cochlea_data_multi"], " ".join(files))
        self.assertEqual(replacements["input_key_multi"], " ".join(["setup0/timepoint0/s0"] * 3))


class PreviousRunTest(unittest.TestCase):
    """A mask step keeps an existing mask, even an incomplete one, so its rerun gives a warning."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.module = cochlea_net_module()
        self.sbatch = write_file(os.path.join(self.tmp.name, "job.sbatch"),
                                 f"#!/bin/bash\nexport OUTPUT_FOLDER={self.tmp.name}\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_mask_of_previous_run(self):
        self.assertEqual(self.module.check_prediction_absent("mask_IHC.template", self.sbatch), [])

        write_file(os.path.join(self.tmp.name, "mask.zarr", ".zgroup"))
        self.assertEqual(len(self.module.check_prediction_absent("mask_IHC.template", self.sbatch)), 1)
        self.assertEqual(self.module.check_prediction_absent("apply_IHC.template", self.sbatch), [])


if __name__ == "__main__":
    unittest.main()
