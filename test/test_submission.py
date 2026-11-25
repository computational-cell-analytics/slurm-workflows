#!/usr/bin/python
# -- coding: utf-8 --
"""Tests of `scripts/submit_job.py` and of the submission step of `slurm_workflows/deploy.py`.

`sbatch`, `reportseff` and `micromamba` are stubs, see `test/stubs.py`.
"""
import argparse
import contextlib
import hashlib
import io
import json
import os
import tempfile
import types
import unittest
from unittest import mock

from slurm_workflows import cli, deploy
from slurm_workflows.projects import default
from slurm_workflows.submission import environment_export, freeze_pip_packages
from stubs import (FIRST_JOBID, MOCKUP_PARAMS, MOCKUP_PIPELINE, MOCKUP_SETTINGS, MOCKUP_TEMPLATE, SUBMIT_SCRIPT,
                   get_test_repository_status, patched_command, read_file, run, sbatch_calls, scancel_calls,
                   scontrol_calls, stub_env, write_file)

DATE = "2025-01-01"
SBATCH_NAME = f"{DATE}_sbatch_x.sbatch"
LOG_NAME = f"{DATE}_log_x.txt"
# The package list of the micromamba stub: the dependencies of the export and the pip freeze.
ENVIRONMENT_HASH = hashlib.sha256("  - python=3.12\nnumpy==1.0\n".encode("utf-8")).hexdigest()


class SubmitJobTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.job_dir = os.path.join(self.tmp.name, "jobs")
        self.archive_dir = os.path.join(self.tmp.name, "archive")
        os.makedirs(self.archive_dir)
        self.sbatch_file = write_file(os.path.join(self.job_dir, SBATCH_NAME),
                                      "#!/bin/bash\n#SBATCH -t 01:00:00\necho hi\n")
        self.log_file = os.path.join(self.job_dir, LOG_NAME)
        self.env = stub_env(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def submit(self, *options):
        if not os.path.isfile(self.sbatch_file):
            write_file(self.sbatch_file, "#!/bin/bash\n#SBATCH -t 01:00:00\n")
        return run(patched_command(SUBMIT_SCRIPT) + list(options) + [self.sbatch_file], env=self.env)

    def test_first_submission_writes_log(self):
        result = self.submit()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"Submitted batch job {FIRST_JOBID}", result.stdout)
        self.assertEqual(read_file(self.log_file), f"{FIRST_JOBID}\n")

    def test_second_submission_appends(self):
        self.submit()
        self.submit()

        self.assertEqual(read_file(self.log_file), f"{FIRST_JOBID}\n{FIRST_JOBID + 1}\n")

    def test_log_without_final_newline(self):
        write_file(self.log_file, "7")

        self.submit()

        self.assertEqual(read_file(self.log_file), f"7\n{FIRST_JOBID}\n")

    def test_job_without_archive_is_not_held(self):
        self.submit()

        self.assertNotIn("--hold", sbatch_calls(self.env)[0])
        self.assertEqual(scontrol_calls(self.env), [])

    def test_archived_job_is_held_and_released(self):
        result = self.submit("-a", self.archive_dir)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("--hold", sbatch_calls(self.env)[0])
        self.assertEqual(scontrol_calls(self.env), [f"release {FIRST_JOBID}"])
        self.assertEqual(scancel_calls(self.env), [])

    def test_dependency(self):
        self.submit("-d", "7")

        self.assertIn("--dependency=afterok:7 --kill-on-invalid-dep=yes", sbatch_calls(self.env)[0])

    def test_failed_submission(self):
        self.env["SBATCH_EXIT"] = "1"

        result = self.submit()

        self.assertEqual(result.returncode, 1)
        self.assertIn("stub failure", result.stderr)
        self.assertFalse(os.path.exists(self.log_file))

    def test_bad_file_name_is_not_submitted(self):
        sbatch_file = write_file(os.path.join(self.job_dir, "job.sbatch"), "#!/bin/bash\n")

        result = run(patched_command(SUBMIT_SCRIPT) + [sbatch_file], env=self.env)

        self.assertEqual(result.returncode, 1)
        self.assertEqual(sbatch_calls(self.env), [])

    def test_dirty_repository_is_not_submitted(self):
        repository = os.path.join(self.tmp.name, "repository")
        write_file(os.path.join(repository, "tracked.txt"), "first\n")
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.org"]
        for args in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "first"]):
            run(["git", "-C", repository] + identity + args)
        write_file(os.path.join(repository, "tracked.txt"), "second\n")
        repository_file = write_file(os.path.join(self.tmp.name, "repositories.txt"), f"tool\t{repository}\n")

        result = self.submit("-r", repository_file)

        self.assertEqual(result.returncode, 1)
        self.assertIn("Dirty repositories: tool", result.stderr)
        self.assertEqual(sbatch_calls(self.env), [])

        result = self.submit("-r", repository_file, "--allow-dirty")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(sbatch_calls(self.env)), 1)

    def test_two_moved_submissions_keep_both_jobids(self):
        first = self.submit("-m", "-a", self.archive_dir)
        second = self.submit("-m", "-a", self.archive_dir)

        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        archived = os.path.join(self.archive_dir, f"{DATE}_x")
        self.assertEqual(read_file(os.path.join(archived, "log.txt")), f"{FIRST_JOBID}\n{FIRST_JOBID + 1}\n")
        with open(os.path.join(archived, "metadata.json")) as myfile:
            self.assertEqual(json.load(myfile)["jobid"], str(FIRST_JOBID + 1))
        with open(os.path.join(archived, "previous", str(FIRST_JOBID), "metadata.json")) as myfile:
            self.assertEqual(json.load(myfile)["jobid"], str(FIRST_JOBID))

    def test_environment_is_archived(self):
        write_file(self.sbatch_file, "#!/bin/bash\nsource ~/.bashrc\nmicromamba activate tools\n")

        result = self.submit("-a", self.archive_dir)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        folder = os.path.join(self.archive_dir, f"{DATE}_x")
        self.assertEqual(read_file(os.path.join(folder, "environment.yml")),
                         "name: tools\ndependencies:\n  - python=3.12\nprefix: /envs/tools\n")
        self.assertEqual(read_file(os.path.join(folder, "pip_freeze.txt")), "numpy==1.0\n")
        with open(os.path.join(folder, "metadata.json")) as myfile:
            self.assertEqual(json.load(myfile)["environment_hash"], ENVIRONMENT_HASH)

    def test_failed_archive_cancels_the_job(self):
        result = self.submit("-a", os.path.join(self.tmp.name, "none"))

        self.assertEqual(result.returncode, 1)
        self.assertIn(f"job {FIRST_JOBID} failed, so the job was cancelled", result.stderr)
        self.assertEqual(scontrol_calls(self.env), [])
        self.assertEqual(scancel_calls(self.env), [str(FIRST_JOBID)])

    def test_failed_release_cancels_the_job(self):
        self.env["SCONTROL_EXIT"] = "1"

        result = self.submit("-a", self.archive_dir)

        self.assertEqual(result.returncode, 1)
        self.assertEqual(scontrol_calls(self.env), [f"release {FIRST_JOBID}"])
        self.assertEqual(scancel_calls(self.env), [str(FIRST_JOBID)])


class DeploySubmissionTest(unittest.TestCase):
    """`deploy_jobs()` submits and archives in one process. A failed archive or release cancels all steps."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archive_dir = os.path.join(self.tmp.name, "archive")
        os.makedirs(self.archive_dir)
        self.env = stub_env(self.tmp.name)
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        environment_export.cache_clear()
        freeze_pip_packages.cache_clear()

        # Patch get_repository_status so that mockup /path/to/my-tool returns clean.
        dirty_patch = mock.patch("slurm_workflows.repositories.get_repository_status", get_test_repository_status)
        dirty_patch.start()
        self.addCleanup(dirty_patch.stop)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def deploy(self, archive_dir, pipeline=None, force=True, start_at=None, module=default):
        # The input of the mockup does not exist, so only force=True submits it.
        args = argparse.Namespace(input=MOCKUP_TEMPLATE if pipeline is None else None, pipeline=pipeline,
                                  params=MOCKUP_PARAMS, settings=MOCKUP_SETTINGS, archive_dir=archive_dir,
                                  repository_file=None, submit=True, allow_missing=False, start_at=start_at,
                                  force=force, allow_dirty=False)
        with mock.patch.dict(os.environ, self.env), contextlib.redirect_stdout(io.StringIO()):
            deploy.deploy_jobs(args, "mockup", module)

    def archived_metadata(self, step="train"):
        [folder] = [name for name in os.listdir(self.archive_dir) if name.endswith(f"_{step}")]
        with open(os.path.join(self.archive_dir, folder, "metadata.json")) as myfile:
            return json.load(myfile)

    def submit_chain(self, sbatch_names, archive_dir, **options):
        sbatch_files = [write_file(os.path.join(self.tmp.name, name), "#!/bin/bash\n#SBATCH -t 01:00:00\n")
                        for name in sbatch_names]
        with mock.patch.dict(os.environ, self.env), contextlib.redirect_stdout(io.StringIO()):
            return deploy.submit_chain(sbatch_files, archive_dir, {}, None, **options)

    def test_deploy_from_argv_returns_the_jobids(self):
        argv = ["-p", MOCKUP_PIPELINE, "-j", MOCKUP_PARAMS, "-s", MOCKUP_SETTINGS, "-a", self.archive_dir, "--force"]
        with mock.patch.dict(os.environ, self.env), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.deploy_from_argv(argv), [])
            self.assertEqual(sbatch_calls(self.env), [])

            self.assertEqual(cli.deploy_from_argv(argv + ["--submit"]), [str(FIRST_JOBID), str(FIRST_JOBID + 1)])

    def test_project_and_environment_are_archived(self):
        self.deploy(self.archive_dir)

        metadata = self.archived_metadata()
        self.assertEqual(metadata["project"], "mockup")
        self.assertEqual(metadata["jobid"], str(FIRST_JOBID))
        self.assertEqual(metadata["environment_hash"], ENVIRONMENT_HASH)
        [folder] = os.listdir(self.archive_dir)
        self.assertEqual(read_file(os.path.join(self.archive_dir, folder, "environment.yml")),
                         "name: dl-gpu\ndependencies:\n  - python=3.12\nprefix: /envs/dl-gpu\n")
        self.assertEqual(read_file(os.path.join(self.archive_dir, folder, "pip_freeze.txt")), "numpy==1.0\n")

    def test_pipeline_is_a_chain(self):
        self.deploy(self.archive_dir, MOCKUP_PIPELINE)

        self.assertIn("--hold", sbatch_calls(self.env)[0])
        self.assertNotIn("--hold", sbatch_calls(self.env)[1])
        self.assertIn(f"--dependency=afterok:{FIRST_JOBID}", sbatch_calls(self.env)[1])
        self.assertEqual(scontrol_calls(self.env), [f"release {FIRST_JOBID}"])
        self.assertEqual(scancel_calls(self.env), [])
        run = str(FIRST_JOBID)
        self.assertEqual(self.archived_metadata("train")["pipeline"],
                         {"name": "train_evaluate", "run": run, "step": 1, "of": 2, "step_name": "train"})
        self.assertEqual(self.archived_metadata("evaluate")["pipeline"],
                         {"name": "train_evaluate", "run": run, "step": 2, "of": 2, "step_name": "evaluate",
                          "depends_on": run})

    def test_metadata_of_the_project_is_archived(self):
        def prepare_step(*args):
            values, script_name, notes, _ = default.prepare_step(*args)
            return values, script_name, notes, {"models": {"IHC": "IHC_v11b"}, "project": "other",
                                                "pipeline": {"name": "other"}}

        module = types.SimpleNamespace(**{name: getattr(default, name) for name in deploy.DEPLOYMENT_FUNCTIONS})
        module.prepare_step = prepare_step
        self.deploy(self.archive_dir, MOCKUP_PIPELINE, module=module)

        for step in ("train", "evaluate"):
            with self.subTest(step):
                metadata = self.archived_metadata(step)
                # The project cannot replace the generic entries.
                self.assertEqual((metadata["project"], metadata["models"]), ("mockup", {"IHC": "IHC_v11b"}))
                self.assertEqual(metadata["pipeline"]["name"], "train_evaluate")
                keys = list(metadata)
                self.assertEqual(keys[keys.index("project") + 1:keys.index("project") + 3], ["models", "pipeline"])

    def test_metadata_of_a_step_is_not_archived_with_the_next_step(self):
        def prepare_step(settings, values, template_file, *args):
            values, script_name, notes, _ = default.prepare_step(settings, values, template_file, *args)
            metadata = {"models": {"IHC": "IHC_v11b"}} if script_name == "train" else {}
            return values, script_name, notes, metadata

        module = types.SimpleNamespace(**{name: getattr(default, name) for name in deploy.DEPLOYMENT_FUNCTIONS})
        module.prepare_step = prepare_step
        self.deploy(self.archive_dir, MOCKUP_PIPELINE, module=module)

        self.assertEqual(self.archived_metadata("train")["models"], {"IHC": "IHC_v11b"})
        self.assertNotIn("models", self.archived_metadata("evaluate"))

    def test_single_job_gets_no_pipeline_of_the_project(self):
        def prepare_step(*args):
            values, script_name, notes, _ = default.prepare_step(*args)
            return values, script_name, notes, {"models": {"IHC": "IHC_v11b"}, "pipeline": {"name": "other"}}

        module = types.SimpleNamespace(**{name: getattr(default, name) for name in deploy.DEPLOYMENT_FUNCTIONS})
        module.prepare_step = prepare_step
        self.deploy(self.archive_dir, module=module)

        metadata = self.archived_metadata()
        self.assertEqual(metadata["models"], {"IHC": "IHC_v11b"})
        self.assertNotIn("pipeline", metadata)

    def test_resumed_pipeline_names_its_start(self):
        self.deploy(self.archive_dir, MOCKUP_PIPELINE, start_at="evaluate")

        # The order of the keys counts, so the items are compared as a list.
        self.assertEqual(list(self.archived_metadata("evaluate")["pipeline"].items()),
                         [("name", "train_evaluate"), ("run", str(FIRST_JOBID)), ("step", 1), ("of", 1),
                          ("step_name", "evaluate"), ("start_at", "evaluate")])

    def test_failed_submission_of_step_2_releases_step_1(self):
        submit = deploy.submit

        def submit_step_1_only(sbatch_file, dependency, hold):
            if dependency is not None:
                raise ValueError("stub failure")
            return submit(sbatch_file, dependency, hold)

        with mock.patch.object(deploy, "submit", side_effect=submit_step_1_only):
            with self.assertRaisesRegex(ValueError, "stub failure"):
                self.deploy(self.archive_dir, MOCKUP_PIPELINE)

        self.assertEqual(len(sbatch_calls(self.env)), 1)
        self.assertEqual(scontrol_calls(self.env), [f"release {FIRST_JOBID}"])
        self.assertEqual(scancel_calls(self.env), [])

    def test_failed_submission_and_release_cancels_step_1(self):
        self.env["SCONTROL_EXIT"] = "1"
        submit = deploy.submit

        def submit_step_1_only(sbatch_file, dependency, hold):
            if dependency is not None:
                raise ValueError("stub failure")
            return submit(sbatch_file, dependency, hold)

        with mock.patch.object(deploy, "submit", side_effect=submit_step_1_only):
            with self.assertRaisesRegex(ValueError, "stub failure\nRelease failed"):
                self.deploy(self.archive_dir, MOCKUP_PIPELINE)

        self.assertEqual(len(sbatch_calls(self.env)), 1)
        self.assertEqual(scontrol_calls(self.env), [f"release {FIRST_JOBID}"])
        self.assertEqual(scancel_calls(self.env), [str(FIRST_JOBID)])

    def test_failed_archiving_of_step_2_cancels_both_steps(self):
        with mock.patch.object(deploy, "archive_submitted_job", side_effect=[None, ValueError("stub failure")]):
            with self.assertRaisesRegex(ValueError, "Archiving failed"):
                self.deploy(self.archive_dir, MOCKUP_PIPELINE)

        self.assertEqual(scontrol_calls(self.env), [])
        self.assertEqual(scancel_calls(self.env), [f"{FIRST_JOBID} {FIRST_JOBID + 1}"])

    def test_failed_release_cancels_all_steps(self):
        self.env["SCONTROL_EXIT"] = "1"

        with self.assertRaisesRegex(ValueError, "Release failed"):
            self.deploy(self.archive_dir, MOCKUP_PIPELINE)

        self.assertEqual(scontrol_calls(self.env), [f"release {FIRST_JOBID}"])
        self.assertEqual(scancel_calls(self.env), [f"{FIRST_JOBID} {FIRST_JOBID + 1}"])

    def test_missing_archive_dir_stops_the_chain(self):
        with mock.patch.object(deploy, "check_job_input", return_value=[]):
            with self.assertRaisesRegex(ValueError, "--force"):
                self.deploy(os.path.join(self.tmp.name, "none"), MOCKUP_PIPELINE, force=False)

        self.assertEqual(sbatch_calls(self.env), [])

    def test_warning_stops_the_chain_and_removes_the_scripts(self):
        with mock.patch.object(deploy, "check_job_input", return_value=["the input of the job does not exist"]):
            with self.assertRaisesRegex(ValueError, "--force"):
                self.deploy(self.archive_dir, MOCKUP_PIPELINE, force=False)

        self.assertEqual(sbatch_calls(self.env), [])
        self.assertEqual([name for name in os.listdir(self.tmp.name) if name.endswith(".sbatch")], [])

    def test_dirty_repository_stops_the_chain(self):
        with mock.patch.object(deploy, "check_dirty", return_value=["tool"]):
            with self.assertRaisesRegex(ValueError, "Dirty repositories: tool"):
                self.deploy(self.archive_dir, MOCKUP_PIPELINE)

        self.assertEqual(sbatch_calls(self.env), [])
        self.assertEqual([name for name in os.listdir(self.tmp.name) if name.endswith(".sbatch")], [])

    def test_failed_submission_raises(self):
        self.env["SBATCH_EXIT"] = "1"

        with self.assertRaises(ValueError):
            self.deploy(self.archive_dir)

    def test_chain_with_defaults_has_no_generic_metadata(self):
        jobids = self.submit_chain([SBATCH_NAME], self.archive_dir)

        self.assertEqual(jobids, [str(FIRST_JOBID)])
        self.assertIn("--hold", sbatch_calls(self.env)[0])
        self.assertEqual(scontrol_calls(self.env), [f"release {FIRST_JOBID}"])
        metadata = self.archived_metadata("x")
        self.assertNotIn("project", metadata)
        self.assertNotIn("pipeline", metadata)

    def test_chain_without_archive_is_not_held(self):
        self.submit_chain([SBATCH_NAME], None)

        self.assertNotIn("--hold", sbatch_calls(self.env)[0])
        self.assertEqual(scontrol_calls(self.env), [])
        self.assertEqual(os.listdir(self.archive_dir), [])

    def test_wrong_number_of_step_entries_submits_nothing(self):
        sbatch_names = [SBATCH_NAME, f"{DATE}_sbatch_y.sbatch"]
        for options in ({"step_metadata": [{}]}, {"step_metadata": [{}, {}, {}]},
                        {"pipeline": {"name": "chain"}, "step_names": ["x"]}):
            with self.subTest(str(options)):
                with self.assertRaisesRegex(ValueError, "entries for 2 sbatch scripts"):
                    self.submit_chain(sbatch_names, self.archive_dir, **options)

        self.assertEqual(sbatch_calls(self.env), [])

    def test_pipeline_without_step_names_submits_nothing(self):
        with self.assertRaisesRegex(ValueError, "needs the step names"):
            self.submit_chain([SBATCH_NAME], self.archive_dir, pipeline={"name": "chain"})

        self.assertEqual(sbatch_calls(self.env), [])


if __name__ == "__main__":
    unittest.main()
