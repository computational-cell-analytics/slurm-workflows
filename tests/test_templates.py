#!/usr/bin/python
# -- coding: utf-8 --
"""Tests of the placeholder replacement: `utils/templates.py`, `utils/settings.py`, and the default
deployment of `scripts/deploy_process.py` on the mockup."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

from tests.stubs import MOCKUP_DIR, REPOSITORY_DIR, read_file, run, write_file
from utils.settings import load_settings, settings_to_replacements
from utils.templates import extract_substrings, replace_substrings_in_file

MOCKUP_TEMPLATE = os.path.join(MOCKUP_DIR, "templates", "mockup", "mockup.template")
MOCKUP_SETTINGS = os.path.join(MOCKUP_DIR, "project_settings", "mockup.json")
MOCKUP_DATASET = os.path.join(MOCKUP_DIR, "dataset.json")
MOCKUP_SBATCH = os.path.join(MOCKUP_DIR, "YYYY-MM-DD_sbatch_mockup.sbatch")
DEPLOY_PROCESS = os.path.join(REPOSITORY_DIR, "scripts", "deploy_process.py")

SETTINGS = {
    "user_address": "me@example.org",
    "academic_id": "",
    "hpc_user": "u1",
    "cores": 4,
    "repositories": {"tool": "/path/to/tool"},
    "models": {"SGN": {"v1": "/path/to/model"}},
}


class TemplateTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.template = write_file(os.path.join(self.tmp.name, "job.template"),
                                   "#SBATCH --mail-user=<user_address>\npython <tool>/run.py <dataset>\n")
        self.output = os.path.join(self.tmp.name, "job.sbatch")

    def tearDown(self):
        self.tmp.cleanup()

    def test_extract_substrings(self):
        self.assertEqual(extract_substrings("a <b> c <d-e> f"), ["b", "d-e"])

    def test_all_placeholders_filled(self):
        replacements = {"user_address": "me@example.org", "tool": "/opt/tool", "dataset": "cifar10"}

        with contextlib.redirect_stdout(io.StringIO()):
            replace_substrings_in_file(self.template, self.output, replacements)

        self.assertEqual(read_file(self.output),
                         "#SBATCH --mail-user=me@example.org\npython /opt/tool/run.py cifar10\n")

    def test_missing_placeholder_raises_and_writes_nothing(self):
        with self.assertRaises(ValueError) as context:
            replace_substrings_in_file(self.template, self.output, {"user_address": "me@example.org"})

        self.assertIn("dataset", str(context.exception))
        self.assertIn("tool", str(context.exception))
        self.assertFalse(os.path.exists(self.output))

    def test_missing_placeholder_not_strict(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            replace_substrings_in_file(self.template, self.output, {"user_address": "a", "tool": "b"}, strict=False)

        self.assertIn("<dataset>", read_file(self.output))
        self.assertIn("no replacement for placeholder <dataset>", stdout.getvalue())


class SettingsTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_settings_to_replacements(self):
        replacements = settings_to_replacements(SETTINGS)

        self.assertEqual(replacements["cores"], "4")
        self.assertEqual(replacements["tool"], "/path/to/tool")
        self.assertNotIn("models", replacements)
        self.assertNotIn("repositories", replacements)

    def test_repository_name_collision(self):
        with self.assertRaises(ValueError):
            settings_to_replacements({"tool": "x", "repositories": {"tool": "/path"}})

    def test_load_settings_missing_keys(self):
        path = write_file(os.path.join(self.tmp.name, "s.json"), json.dumps({"hpc_user": "u1"}))

        with self.assertRaises(ValueError) as context:
            load_settings(path)

        self.assertIn("repositories", str(context.exception))

    def test_load_settings_invalid_json(self):
        path = write_file(os.path.join(self.tmp.name, "s.json"), "{")

        with self.assertRaises(ValueError):
            load_settings(path)

    def test_load_settings_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            load_settings(os.path.join(self.tmp.name, "none.json"))


class MockupTest(unittest.TestCase):
    """The mockup has no deployment module, so `deploy_process.py` uses the default deployment."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def deploy(self, parameter_file):
        cmd = [sys.executable, DEPLOY_PROCESS, "-i", MOCKUP_TEMPLATE, "-j", parameter_file, "-s", MOCKUP_SETTINGS]
        return run(cmd, cwd=self.tmp.name)

    def test_render_matches_committed_sbatch(self):
        replacements = settings_to_replacements(load_settings(MOCKUP_SETTINGS))
        with open(MOCKUP_DATASET) as myfile:
            replacements.update(json.load(myfile))
        output = os.path.join(self.tmp.name, "mockup.sbatch")

        with contextlib.redirect_stdout(io.StringIO()):
            replace_substrings_in_file(MOCKUP_TEMPLATE, output, replacements)

        self.assertEqual(read_file(output), read_file(MOCKUP_SBATCH))

    def test_default_deployment(self):
        result = self.deploy(MOCKUP_DATASET)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("has no deployment module", result.stdout)
        sbatch_files = [name for name in os.listdir(self.tmp.name) if name.endswith("_sbatch_mockup.sbatch")]
        self.assertEqual(len(sbatch_files), 1)
        self.assertEqual(read_file(os.path.join(self.tmp.name, sbatch_files[0])), read_file(MOCKUP_SBATCH))

    def test_default_deployment_missing_parameter(self):
        with open(MOCKUP_DATASET) as myfile:
            parameters = json.load(myfile)
        del parameters["dataset"]
        parameter_file = write_file(os.path.join(self.tmp.name, "params.json"), json.dumps(parameters))

        result = self.deploy(parameter_file)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dataset", result.stderr)
        self.assertNotIn("has no deployment module", result.stdout)
        self.assertEqual(os.listdir(self.tmp.name), ["params.json"])


if __name__ == "__main__":
    unittest.main()
