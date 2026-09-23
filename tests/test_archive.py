#!/usr/bin/python
# -- coding: utf-8 --
"""Tests of `scripts/02_archive_scripts.sh`: the transfer of the job files and the log merge."""
import os
import tempfile
import unittest

from tests.stubs import ARCHIVE_SCRIPTS, read_file, run, stub_env, write_file

DATE = "2025-01-01"


class ArchiveTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.input_dir = os.path.join(self.tmp.name, "input")
        self.archive_dir = os.path.join(self.tmp.name, "archive")
        os.makedirs(self.input_dir)
        os.makedirs(self.archive_dir)
        self.env = stub_env(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def job_file(self, middle, suffix, extension, content=""):
        return write_file(os.path.join(self.input_dir, f"{DATE}_{middle}_{suffix}.{extension}"), content)

    def archive(self, suffix, move=False):
        cmd = ["bash", ARCHIVE_SCRIPTS] + (["-m"] if move else [])
        cmd += ["-i", self.input_dir, "-a", self.archive_dir, DATE, suffix]
        return run(cmd, env=self.env)

    def archived(self, suffix):
        return os.path.join(self.archive_dir, f"{DATE}_{suffix}")

    def submit(self, jobid, suffix="x"):
        """Add a JobID to the log file of the input directory, as `01_run_sbatch.sh` does."""
        self.job_file("sbatch", suffix, "sbatch", "#!/bin/bash\n")
        log_file = os.path.join(self.input_dir, f"{DATE}_log_{suffix}.txt")
        with open(log_file, "a") as myfile:
            myfile.write(f"{jobid}\n")

    def test_copy_keeps_sources_and_renames(self):
        sbatch = self.job_file("sbatch", "x", "sbatch", "script")
        log = self.job_file("log", "x", "txt", "1\n")

        result = self.archive("x")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(os.listdir(self.archived("x"))), ["log.txt", "sbatch.sbatch"])
        self.assertEqual(read_file(os.path.join(self.archived("x"), "sbatch.sbatch")), "script")
        self.assertTrue(os.path.isfile(sbatch))
        self.assertTrue(os.path.isfile(log))

    def test_move_removes_sources(self):
        self.job_file("sbatch", "x", "sbatch")
        self.job_file("log", "x", "txt", "1\n")

        result = self.archive("x", move=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(os.listdir(self.input_dir), [])
        self.assertEqual(sorted(os.listdir(self.archived("x"))), ["log.txt", "sbatch.sbatch"])

    def assert_log_after_two_submissions(self, first_move, second_move):
        self.submit(1)
        self.assertEqual(self.archive("x", move=first_move).returncode, 0)
        self.submit(2)
        self.assertEqual(self.archive("x", move=second_move).returncode, 0)

        self.assertEqual(read_file(os.path.join(self.archived("x"), "log.txt")), "1\n2\n")

    def test_log_merge_move_move(self):
        # A moved log file holds only the new JobID. It must not overwrite the archived log.
        self.assert_log_after_two_submissions(True, True)

    def test_log_merge_copy_copy(self):
        self.assert_log_after_two_submissions(False, False)

    def test_log_merge_copy_move(self):
        self.assert_log_after_two_submissions(False, True)

    def test_log_merge_without_final_newline(self):
        write_file(os.path.join(self.archived("x"), "log.txt"), "1")
        self.job_file("log", "x", "txt", "2\n")

        self.assertEqual(self.archive("x", move=True).returncode, 0)

        self.assertEqual(read_file(os.path.join(self.archived("x"), "log.txt")), "1\n2\n")

    @unittest.expectedFailure
    def test_other_suffix_is_not_archived(self):
        # 'y_x' ends with '_x', but it is a different job.
        self.submit(1, suffix="x")
        self.submit(2, suffix="y_x")

        self.assertEqual(self.archive("x", move=True).returncode, 0)

        self.assertEqual(sorted(os.listdir(self.archived("x"))), ["log.txt", "sbatch.sbatch"])
        self.assertEqual(sorted(os.listdir(self.input_dir)),
                         [f"{DATE}_log_y_x.txt", f"{DATE}_sbatch_y_x.sbatch"])

    def test_missing_archive_dir(self):
        self.job_file("sbatch", "x", "sbatch")
        cmd = ["bash", ARCHIVE_SCRIPTS, "-i", self.input_dir, "-a", os.path.join(self.tmp.name, "none"), DATE, "x"]

        result = run(cmd, env=self.env)

        self.assertEqual(result.returncode, 1)
        self.assertIn("does not exist", result.stdout)


if __name__ == "__main__":
    unittest.main()
