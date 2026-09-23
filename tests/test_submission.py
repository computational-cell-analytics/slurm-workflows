#!/usr/bin/python
# -- coding: utf-8 --
"""Tests of `scripts/01_run_sbatch.sh` and of the submission step of `scripts/deploy_process.py`.

`sbatch` and `reportseff` are stubs, see `tests/stubs.py`.
"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from tests.stubs import FIRST_JOBID, RUN_SBATCH, load_script, read_file, run, sbatch_calls, stub_env, write_file

DATE = "2025-01-01"
SBATCH_NAME = f"{DATE}_sbatch_x.sbatch"
LOG_NAME = f"{DATE}_log_x.txt"


class RunSbatchTest(unittest.TestCase):

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
        return run(["bash", RUN_SBATCH] + list(options) + [self.sbatch_file], env=self.env)

    def test_first_submission_writes_log(self):
        result = self.submit()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"SUBMITTED_JOBID={FIRST_JOBID}", result.stdout)
        self.assertEqual(read_file(self.log_file), f"{FIRST_JOBID}\n")

    def test_second_submission_appends(self):
        self.submit()
        self.submit()

        self.assertEqual(read_file(self.log_file), f"{FIRST_JOBID}\n{FIRST_JOBID + 1}\n")

    def test_empty_log_gets_jobid(self):
        write_file(self.log_file, "")

        self.submit()

        self.assertEqual(read_file(self.log_file), f"{FIRST_JOBID}\n")

    def test_dependency(self):
        self.submit("-d", "7")

        self.assertIn("--dependency=afterok:7 --kill-on-invalid-dep=yes", sbatch_calls(self.env)[0])

    def test_failed_submission(self):
        self.env["SBATCH_EXIT"] = "1"

        result = self.submit()

        self.assertEqual(result.returncode, 1)
        self.assertFalse(os.path.exists(self.log_file))

    def test_two_moved_submissions_keep_both_jobids(self):
        first = self.submit("-m", "-a", self.archive_dir)
        second = self.submit("-m", "-a", self.archive_dir)

        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        archived = os.path.join(self.archive_dir, f"{DATE}_x")
        self.assertEqual(read_file(os.path.join(archived, "log.txt")), f"{FIRST_JOBID}\n{FIRST_JOBID + 1}\n")
        with open(os.path.join(archived, "metadata.json")) as myfile:
            self.assertEqual(json.load(myfile)["jobid"], str(FIRST_JOBID + 1))


class SubmitStepTest(unittest.TestCase):
    """The chain of `deploy_process.py` continues when only the archiving of a job fails."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.deploy_process = load_script("deploy_process")

    def tearDown(self):
        self.tmp.cleanup()

    def submit_step(self, stub):
        with mock.patch.object(self.deploy_process, "RUN_SBATCH_SCRIPT", stub), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return self.deploy_process.submit_step("job.sbatch", "", "repositories.txt")

    def test_jobid_is_returned_if_archiving_fails(self):
        # The job is submitted, then the metadata step exits with an error.
        stub = write_file(os.path.join(self.tmp.name, "run_sbatch.sh"),
                          "#!/bin/bash\necho SUBMITTED_JOBID=42\necho 'archiving failed' >&2\nexit 1\n")

        self.assertEqual(self.submit_step(stub), "42")

    def test_failed_submission_raises(self):
        stub = write_file(os.path.join(self.tmp.name, "run_sbatch.sh"),
                          "#!/bin/bash\necho 'Submission failed.' >&2\nexit 1\n")

        with self.assertRaises(ValueError):
            self.submit_step(stub)


if __name__ == "__main__":
    unittest.main()
