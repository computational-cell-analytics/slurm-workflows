#!/usr/bin/python
# -- coding: utf-8 --
"""Tests of the metadata of an archive folder: `utils/metadata.py`, `utils/slurm.py` and
`scripts/write_metadata.py`. `reportseff` is a stub, see `tests/stubs.py`."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

from tests.stubs import WRITE_METADATA, run, set_reportseff_output, stub_env, write_file
from utils.metadata import init_metadict, jobids_from_log
from utils.slurm import reportseff_from_jobid, sbatch_parameters_to_dict

REPORT_LINES = [
    "JobID State Elapsed TimeEff CPUEff MemEff",
    "91234 COMPLETED 00:01:00 1.0% 10.0% 1.0%",
    "12345 COMPLETED 00:02:00 2.0% 20.0% 2.0%",
    "1234_0 COMPLETED 00:03:00 3.0% 30.0% 3.0%",
    "1234_1 FAILED 00:04:00 4.0% 40.0% 4.0%",
]


class LogFileTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_jobids_from_log(self):
        log_file = write_file(os.path.join(self.tmp.name, "log.txt"), "11\n\n 12 \n13")

        self.assertEqual(jobids_from_log(log_file), ["11", "12", "13"])

    def test_jobids_from_missing_log(self):
        self.assertEqual(jobids_from_log(os.path.join(self.tmp.name, "none.txt")), [])

    def test_init_metadict(self):
        self.assertEqual(init_metadict("/archive/2025-01-01_apply_SGN_M1L/"),
                         {"date": "2025-01-01", "task": "apply_SGN_M1L"})

    def test_init_metadict_bad_name(self):
        with self.assertRaises(ValueError):
            init_metadict("/archive/nodate")


class SbatchParameterTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def parameters(self, *lines):
        sbatch = write_file(os.path.join(self.tmp.name, "sbatch.sbatch"), "#!/bin/bash\n" + "\n".join(lines) + "\n")
        metadict = {}
        sbatch_parameters_to_dict(sbatch, metadict)
        return metadict

    def test_formats(self):
        metadict = self.parameters(
            "#SBATCH -t 05:00:00                  # estimated time",
            "#SBATCH --mem=5G",
            "#SBATCH --job-name=train",
            "#SBATCH -p grete:shared",
        )

        self.assertEqual(metadict, {"runtime": "05:00:00", "Memory-per-node": "5G",
                                    "job-name": "train", "partition": "grete:shared"})

    def test_option_with_space(self):
        self.assertEqual(self.parameters("#SBATCH --mem 64G"), {"Memory-per-node": "64G"})

    def test_tab_and_double_space(self):
        self.assertEqual(self.parameters("#SBATCH\t-c 4", "#SBATCH  -t 01:00:00"),
                         {"cpus-per-task": "4", "runtime": "01:00:00"})

    def test_disabled_directive_is_ignored(self):
        self.assertEqual(self.parameters("##SBATCH -t 99:00:00", "#SBATCH -t 01:00:00", "#SBATCH",
                                         "#SBATCH -c"),
                         {"runtime": "01:00:00"})


class ReportseffTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def report(self, jobid, reportseff=True):
        env = stub_env(self.tmp.name, reportseff=reportseff)
        if reportseff:
            set_reportseff_output(env, REPORT_LINES)
        metadict = {}
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()):
            reportseff_from_jobid(None, metadict, jobid=jobid)
        return metadict

    def test_jobid_match_is_exact(self):
        # The report of 91234 and 12345 must not be taken for JobID 1234.
        metadict = self.report("1234")

        self.assertEqual([report["JobID"] for report in metadict["Reportseff"]], ["1234_0", "1234_1"])

    def test_single_job(self):
        metadict = self.report("91234")

        self.assertEqual(metadict["jobid"], "91234")
        self.assertEqual(metadict["Reportseff"], [{"JobID": "91234", "State": "COMPLETED", "Elapsed": "00:01:00",
                                                   "TimeEff": "1.0%", "CPUEff": "10.0%", "MemEff": "1.0%"}])

    def test_unknown_jobid_keeps_report(self):
        env = stub_env(self.tmp.name)
        set_reportseff_output(env, REPORT_LINES)
        metadict = {"Reportseff": [{"JobID": "5"}]}
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()):
            reportseff_from_jobid(None, metadict, jobid="777")

        self.assertEqual(metadict["Reportseff"], [{"JobID": "5"}])


class WriteMetadataTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archived = os.path.join(self.tmp.name, "2025-01-01_x")
        write_file(os.path.join(self.archived, "sbatch.sbatch"), "#!/bin/bash\n#SBATCH -t 01:00:00\n")
        write_file(os.path.join(self.archived, "log.txt"), "1234\n")
        self.metadata_file = os.path.join(self.archived, "metadata.json")

    def tearDown(self):
        self.tmp.cleanup()

    def metadata(self):
        with open(self.metadata_file) as myfile:
            return json.load(myfile)

    def test_missing_reportseff(self):
        env = stub_env(self.tmp.name, reportseff=False)

        result = run([sys.executable, WRITE_METADATA, self.archived], env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Warning", result.stdout)
        self.assertEqual(self.metadata()["jobid"], "1234")
        self.assertNotIn("Reportseff", self.metadata())

    def git(self, repository, *args):
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.org"]
        result = run(["git", "-C", repository] + identity + list(args))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    @unittest.expectedFailure
    def test_resubmission_refreshes_git_hash(self):
        env = stub_env(self.tmp.name)
        repository = os.path.join(self.tmp.name, "repository")
        os.makedirs(repository)
        self.git(repository, "init", "-q")
        self.git(repository, "commit", "-q", "--allow-empty", "-m", "first")
        repository_file = write_file(os.path.join(self.tmp.name, "repositories.txt"), f"tool\t{repository}\n")

        run([sys.executable, WRITE_METADATA, "-r", repository_file, self.archived], env=env)
        self.git(repository, "commit", "-q", "--allow-empty", "-m", "second")
        run([sys.executable, WRITE_METADATA, "-r", repository_file, self.archived], env=env)

        self.assertEqual(self.metadata()["Repositories"][0]["repo_version"], self.git(repository, "rev-parse", "HEAD"))


if __name__ == "__main__":
    unittest.main()
