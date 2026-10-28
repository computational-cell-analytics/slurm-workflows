#!/usr/bin/python
# -- coding: utf-8 --
"""Tests of the step selection of a pipeline: `select_steps()` and `resolve_template()` of cochlea-net."""
import argparse
import contextlib
import io
import os
import unittest

from slurm_workflows.pipelines import load_pipeline, project_of_template, resolve_pipeline, step_template
from stubs import cochlea_net_module

SETTINGS = {"mobie_project": "/path/to/mobie_project"}


class SelectStepsTest(unittest.TestCase):

    def setUp(self):
        self.module = cochlea_net_module()
        project, pipeline_file = resolve_pipeline("cochlea-net/sgn")
        self.definition = load_pipeline(project, pipeline_file)

    def select(self, settings=SETTINGS, no_mobie=False, no_cleanup=False):
        args = argparse.Namespace(no_mobie=no_mobie, no_cleanup=no_cleanup)
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

    def test_cleanup_steps_must_be_a_list(self):
        self.definition["cleanup_steps"] = "cleanup_SGN"

        with self.assertRaises(ValueError):
            self.select()


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
