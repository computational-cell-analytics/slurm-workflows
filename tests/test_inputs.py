#!/usr/bin/python
# -- coding: utf-8 --
"""Tests of the check of missing files: `utils/inputs.py` and `resolve_input()` of cochlea-net."""
import contextlib
import io
import os
import tempfile
import unittest

from tests.stubs import cochlea_net_module, write_file
from utils.inputs import check_external_input, check_job_input, check_output_absent, job_variables


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
    """The n5 of the initial processing wins, the OME-Zarr from the S3 bucket is the fallback."""

    N5 = "M_LR_1L_PV_fused.n5"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.resolve_input = cochlea_net_module().resolve_input

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, name):
        write_file(os.path.join(self.tmp.name, name, "attributes.json"))

    def test_n5_wins(self):
        self.add(self.N5)
        self.add("PV.ome.zarr")

        self.assertEqual(self.resolve_input(self.tmp.name, self.N5, "setup0/timepoint0/s0", "PV"),
                         (self.N5, "setup0/timepoint0/s0"))

    def test_ome_zarr_fallback(self):
        self.add("PV.ome.zarr")

        self.assertEqual(self.resolve_input(self.tmp.name, self.N5, "setup0/timepoint0/s0", "PV"),
                         ("PV.ome.zarr", "s0"))

    def test_neither_reports_n5(self):
        self.assertEqual(self.resolve_input(self.tmp.name, self.N5, "setup0/timepoint0/s0", "PV"),
                         (self.N5, "setup0/timepoint0/s0"))

    def test_unknown_n5(self):
        self.assertEqual(self.resolve_input(self.tmp.name, None, None, "PV"), ("PV.ome.zarr", "s0"))


if __name__ == "__main__":
    unittest.main()
