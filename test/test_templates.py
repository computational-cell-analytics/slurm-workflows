#!/usr/bin/python
# -- coding: utf-8 --
"""Tests of the placeholder filling: `slurm_workflows/templates.py`, `slurm_workflows/settings.py`,
and the default deployment of `scripts/deploy.py` on the mockup."""
import contextlib
import glob
import io
import json
import os
import re
import sys
import tempfile
import types
import unittest
from unittest import mock

from slurm_workflows import deploy
from slurm_workflows.projects import default
from slurm_workflows.settings import load_settings, settings_to_placeholders
from slurm_workflows.templates import fill_placeholders, placeholders_in, render_template
from stubs import (MOCKUP_DIR, MOCKUP_PARAMS, MOCKUP_PIPELINE, MOCKUP_SETTINGS, MOCKUP_TEMPLATE, REPOSITORY_DIR,
                   patched_command, read_file, run, write_file)

MOCKUP_SBATCH = os.path.join(MOCKUP_DIR, "YYYY-MM-DD_sbatch_train.sbatch")
DEPLOY_SCRIPT = os.path.join(REPOSITORY_DIR, "scripts", "deploy.py")

# Every template which removes files has this block, see doc/new_template.md.
REMOVE_TREE_BLOCK = (
    "# The file system removes one file per round trip, so its latency limits a single 'rm -rf' to a few\n"
    "# hundred files per second. Parallel processes remove the files, then 'rm -rf' removes the folders.\n"
    "REMOVE_PROCESSES=8\n"
    "\n"
    "remove_tree() {\n"
    "\tlocal start=$SECONDS\n"
    "\tif [ ! -e \"$1\" ] ; then\n"
    "\t\treturn 0\n"
    "\tfi\n"
    "\tfind \"$1\" -type f -print0 | xargs -0 -r -P \"$REMOVE_PROCESSES\" -n 1000 rm -f || return 1\n"
    "\trm -rf \"$1\" || return 1\n"
    "\techo \"Removed $1 in $((SECONDS - start)) s\"\n"
    "}\n"
)
RM_COMMAND = re.compile(r"^[^#\n]*\brm\s", re.MULTILINE)

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
                                   "#SBATCH --mail-user=<user_address>\npython <repo:tool>/run.py <dataset>\n")
        self.output = os.path.join(self.tmp.name, "job.sbatch")

    def tearDown(self):
        self.tmp.cleanup()

    def test_placeholders_in(self):
        self.assertEqual(placeholders_in("a <b> c <d-e> f <repo:g>"), ["b", "d-e", "repo:g"])

    def test_shell_code_and_escape_are_no_placeholders(self):
        self.assertEqual(placeholders_in("cmd < in > out; cat <<EOF >> log; echo \\<b>"), [])

    def test_escape_and_value_with_brackets(self):
        lines, missing = fill_placeholders(["echo \\<b> <a>\n"], {"a": "<b>", "b": "x"})

        self.assertEqual(lines, ["echo <b> <b>\n"])
        self.assertEqual(missing, set())

    def test_all_placeholders_filled(self):
        values = {"user_address": "me@example.org", "repo:tool": "/opt/tool", "dataset": "cifar10"}

        with contextlib.redirect_stdout(io.StringIO()):
            render_template(self.template, self.output, values)

        self.assertEqual(read_file(self.output),
                         "#SBATCH --mail-user=me@example.org\npython /opt/tool/run.py cifar10\n")

    def test_missing_placeholder_raises_and_writes_nothing(self):
        with self.assertRaises(ValueError) as context:
            render_template(self.template, self.output, {"user_address": "me@example.org"})

        self.assertIn("dataset", str(context.exception))
        self.assertIn("tool", str(context.exception))
        self.assertFalse(os.path.exists(self.output))

    def test_missing_placeholder_not_strict(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            render_template(self.template, self.output, {"user_address": "a", "repo:tool": "b"}, strict=False)

        self.assertIn("<dataset>", read_file(self.output))
        self.assertIn("no value for placeholder <dataset>", stdout.getvalue())


class SettingsTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_settings_to_placeholders(self):
        values = settings_to_placeholders(SETTINGS)

        self.assertEqual(values["cores"], "4")
        self.assertEqual(values["repo:tool"], "/path/to/tool")
        self.assertNotIn("models", values)
        self.assertNotIn("repositories", values)

    def test_repository_and_setting_of_same_name(self):
        values = settings_to_placeholders({"tool": "x", "repositories": {"tool": "/path"}})

        self.assertEqual(values, {"tool": "x", "repo:tool": "/path", "repo:slurm-workflows": REPOSITORY_DIR})

    def test_load_settings_missing_keys(self):
        path = write_file(os.path.join(self.tmp.name, "s.json"), json.dumps({"hpc_user": "u1"}))

        with self.assertRaises(ValueError) as context:
            load_settings(path)

        self.assertIn("academic_id", str(context.exception))

    def test_load_settings_invalid_json(self):
        path = write_file(os.path.join(self.tmp.name, "s.json"), "{")

        with self.assertRaises(ValueError):
            load_settings(path)

    def test_load_settings_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            load_settings(os.path.join(self.tmp.name, "none.json"))


class MockupTest(unittest.TestCase):
    """The mockup has no deployment module, so `scripts/deploy.py` uses the default deployment."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def deploy(self, parameter_file):
        cmd = patched_command(DEPLOY_SCRIPT) + ["-i", MOCKUP_TEMPLATE, "-j", parameter_file, "-s", MOCKUP_SETTINGS]
        return run(cmd, cwd=self.tmp.name)

    def test_render_matches_committed_sbatch(self):
        values = settings_to_placeholders(load_settings(MOCKUP_SETTINGS))
        with open(MOCKUP_PARAMS) as myfile:
            values.update(json.load(myfile))
        output = os.path.join(self.tmp.name, "train.sbatch")

        with contextlib.redirect_stdout(io.StringIO()):
            render_template(MOCKUP_TEMPLATE, output, values)

        self.assertEqual(read_file(output), read_file(MOCKUP_SBATCH))

    def test_default_deployment(self):
        result = self.deploy(MOCKUP_PARAMS)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("has no deployment module", result.stdout)
        sbatch_files = [name for name in os.listdir(self.tmp.name) if name.endswith("_sbatch_train.sbatch")]
        self.assertEqual(len(sbatch_files), 1)
        self.assertEqual(read_file(os.path.join(self.tmp.name, sbatch_files[0])), read_file(MOCKUP_SBATCH))

    def test_pipeline_with_its_own_templates(self):
        cmd = patched_command(DEPLOY_SCRIPT) + ["-p", MOCKUP_PIPELINE, "-j", MOCKUP_PARAMS, "-s", MOCKUP_SETTINGS]
        result = run(cmd, cwd=self.tmp.name)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(name.split("_sbatch_")[1] for name in os.listdir(self.tmp.name)),
                         ["evaluate.sbatch", "train.sbatch"])
        self.assertEqual(result.stdout.count("Placeholder values of"), 2)

    def test_allow_missing_refuses_submit(self):
        cmd = [sys.executable, DEPLOY_SCRIPT, "-i", MOCKUP_TEMPLATE, "-j", MOCKUP_PARAMS, "-s", MOCKUP_SETTINGS,
               "--allow-missing", "--submit"]
        result = run(cmd, cwd=self.tmp.name)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--allow-missing", result.stderr)
        self.assertEqual(os.listdir(self.tmp.name), [])

    def test_default_deployment_missing_parameter(self):
        with open(MOCKUP_PARAMS) as myfile:
            parameters = json.load(myfile)
        del parameters["dataset"]
        parameter_file = write_file(os.path.join(self.tmp.name, "params.json"), json.dumps(parameters))

        result = self.deploy(parameter_file)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dataset", result.stderr)
        self.assertNotIn("has no deployment module", result.stdout)
        self.assertEqual(os.listdir(self.tmp.name), ["params.json"])


class ExitCodeTrapTest(unittest.TestCase):

    def test_every_template_prints_its_exit_code(self):
        templates = glob.glob(os.path.join(REPOSITORY_DIR, "templates", "*", "*.template"))
        templates += glob.glob(os.path.join(MOCKUP_DIR, "templates", "*", "*.template"))
        self.assertGreater(len(templates), 2)
        for template in templates:
            with self.subTest(template=os.path.basename(template)):
                content = read_file(template)
                self.assertIn("trap 'exit 143' TERM\n", content)
                self.assertIn("""trap 'echo "Exit code of the job: $?"' EXIT\n""", content)


class RemoveTreeTest(unittest.TestCase):

    def test_every_template_removes_files_in_parallel(self):
        templates = glob.glob(os.path.join(REPOSITORY_DIR, "templates", "*", "*.template"))
        templates += glob.glob(os.path.join(MOCKUP_DIR, "templates", "*", "*.template"))
        removing = [template for template in templates if RM_COMMAND.search(read_file(template))]
        self.assertGreater(len(removing), 2)
        for template in removing:
            with self.subTest(template=os.path.basename(template)):
                content = read_file(template)
                self.assertIn(REMOVE_TREE_BLOCK, content)
                self.assertIsNone(RM_COMMAND.search(content.replace(REMOVE_TREE_BLOCK, "")))


class MailTypeTest(unittest.TestCase):

    def test_every_array_template_mails_each_failed_task(self):
        # Without ARRAY_TASKS, Slurm sends one mail for the whole array, which ignores a task that fails
        # before it starts.
        templates = glob.glob(os.path.join(REPOSITORY_DIR, "templates", "*", "*.template"))
        templates += glob.glob(os.path.join(MOCKUP_DIR, "templates", "*", "*.template"))
        array_templates = [template for template in templates
                           if re.search(r"^#SBATCH (-a|--array)\b", read_file(template), re.MULTILINE)]
        self.assertGreater(len(array_templates), 2)
        for template in array_templates:
            with self.subTest(template=os.path.basename(template)):
                mail_type = re.search(r"^#SBATCH --mail-type=(\S+)", read_file(template), re.MULTILINE)
                self.assertIsNotNone(mail_type)
                self.assertIn("ARRAY_TASKS", mail_type.group(1).split(","))


class ProjectModuleTest(unittest.TestCase):

    def test_missing_function_comes_from_default_deployment(self):
        module = types.ModuleType("slurm_workflows.projects.partial.deployment")
        module.prepare_step = lambda *args: None

        with mock.patch.dict(sys.modules, {module.__name__: module}), \
                mock.patch.object(deploy.os.path, "isfile", return_value=True):
            functions = deploy.project_module("partial")

        self.assertIs(functions.prepare_step, module.prepare_step)
        self.assertIs(functions.check_steps, default.check_steps)


if __name__ == "__main__":
    unittest.main()
