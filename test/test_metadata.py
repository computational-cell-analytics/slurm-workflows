#!/usr/bin/python
# -- coding: utf-8 --
"""Tests of the metadata of an archive folder: `slurm_workflows/metadata.py`, `slurm_workflows/slurm.py`
and `scripts/write_metadata.py`. `reportseff` is a stub, see `test/stubs.py`."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

from slurm_workflows.metadata import init_metadict, read_metadata, write_metadata
from slurm_workflows.repositories import job_repositories
from slurm_workflows.slurm import jobids_from_log, reportseff_from_jobid, add_sbatch_parameters
from stubs import REPOSITORY_DIR, WRITE_METADATA, reportseff_calls, run, set_reportseff_output, stub_env
from stubs import write_file

REPORT_LINES = [
    "JobID State Elapsed TimeEff CPUEff MemEff",
    "91234 COMPLETED 00:01:00 1.0% 10.0% 1.0%",
    "12345 COMPLETED 00:02:00 2.0% 20.0% 2.0%",
    "1234_0 COMPLETED 00:03:00 3.0% 30.0% 3.0%",
    "1234_1 FAILED 00:04:00 4.0% 40.0% 4.0%",
    "1234_[2] CANCELLED 00:00:00 0.0% --- 0.0%",
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
        add_sbatch_parameters(metadict, sbatch)
        return metadict

    def test_formats(self):
        metadict = self.parameters(
            "#SBATCH -t 05:00:00                  # estimated time",
            "#SBATCH --mem=5G",
            "#SBATCH --job-name=train",
            "#SBATCH -p grete:shared",
        )

        self.assertEqual(metadict, {"runtime": "05:00:00", "memory_per_node": "5G",
                                    "job_name": "train", "partition": "grete:shared"})

    def test_option_with_space(self):
        self.assertEqual(self.parameters("#SBATCH --mem 64G"), {"memory_per_node": "64G"})

    def test_tab_and_double_space(self):
        self.assertEqual(self.parameters("#SBATCH\t-c 4", "#SBATCH  -t 01:00:00"),
                         {"cpus_per_task": "4", "runtime": "01:00:00"})

    def test_disabled_directive_is_ignored(self):
        self.assertEqual(self.parameters("##SBATCH -t 99:00:00", "#SBATCH -t 01:00:00", "#SBATCH",
                                         "#SBATCH -c"),
                         {"runtime": "01:00:00"})


class ReportseffTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def report(self, jobid, reportseff=True, metadict=None):
        self.env = stub_env(self.tmp.name, reportseff=reportseff)
        if reportseff:
            set_reportseff_output(self.env, REPORT_LINES)
        metadict = {} if metadict is None else metadict
        with mock.patch.dict(os.environ, self.env), contextlib.redirect_stdout(io.StringIO()):
            reportseff_from_jobid(None, metadict, jobid=jobid)
        return metadict

    def test_jobid_match_is_exact(self):
        # The report of 91234 and 12345 must not be taken for JobID 1234. '1234_[2]' is a task of the
        # array which never started.
        metadict = self.report("1234")

        self.assertEqual([report["jobid"] for report in metadict["reportseff"]], ["1234_0", "1234_1", "1234_[2]"])
        self.assertEqual(reportseff_calls(self.env), ["1234"])

    def test_single_job(self):
        metadict = self.report("91234")

        self.assertEqual(metadict["jobid"], "91234")
        self.assertEqual(metadict["reportseff"], [{"jobid": "91234", "state": "COMPLETED", "elapsed": "00:01:00",
                                                   "time_eff": "1.0%", "cpu_eff": "10.0%", "mem_eff": "1.0%"}])

    def test_unknown_jobid_keeps_only_its_own_report(self):
        # The entry of JobID 5 is the report of a replaced JobID.
        metadict = self.report("777", metadict={"reportseff": [{"jobid": "777_0"}, {"jobid": "5"}]})

        self.assertEqual(metadict["reportseff"], [{"jobid": "777_0"}])


class MetadataFileTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "metadata.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_write_keeps_the_mode(self):
        write_metadata({"jobid": "1"}, self.path)
        os.chmod(self.path, 0o640)

        write_metadata({"jobid": "2"}, self.path)

        self.assertEqual(read_metadata(self.path), {"jobid": "2"})
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o640)
        self.assertEqual(os.listdir(self.tmp.name), ["metadata.json"])

    def test_failed_write_keeps_the_old_file(self):
        write_metadata({"jobid": "1"}, self.path)

        with mock.patch("slurm_workflows.metadata.json.dump", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                write_metadata({"jobid": "2"}, self.path)

        self.assertEqual(read_metadata(self.path), {"jobid": "1"})

    def test_read_error_names_the_file(self):
        write_file(self.path, "")

        with self.assertRaisesRegex(ValueError, f"The metadata file {self.path} is not valid JSON"):
            read_metadata(self.path)


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
        self.assertNotIn("reportseff", self.metadata())

    def test_failing_reportseff(self):
        env = stub_env(self.tmp.name)
        env["REPORTSEFF_EXIT"] = "1"

        result = run([sys.executable, WRITE_METADATA, self.archived], env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ModuleNotFoundError", result.stdout)
        self.assertNotIn("reportseff", self.metadata())

    def test_folder_without_git_is_not_recorded(self):
        env = stub_env(self.tmp.name)
        env["GIT_CEILING_DIRECTORIES"] = self.tmp.name
        folder = os.path.join(self.tmp.name, "plain")
        os.makedirs(folder)
        repository_file = write_file(os.path.join(self.tmp.name, "repositories.txt"), f"plain\t{folder}\n")

        result = run([sys.executable, WRITE_METADATA, "-r", repository_file, self.archived], env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("git hash of plain", result.stdout)
        self.assertNotIn("plain", [entry["repo_name"] for entry in self.metadata()["repositories"]])

    def git(self, repository, *args):
        identity = ["-c", "user.name=test", "-c", "user.email=test@example.org"]
        result = run(["git", "-C", repository] + identity + list(args))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def repository(self):
        repository = os.path.join(self.tmp.name, "repository")
        os.makedirs(repository)
        self.git(repository, "init", "-q")
        self.git(repository, "commit", "-q", "--allow-empty", "-m", "first")
        return repository

    def test_resubmission_refreshes_git_hash(self):
        env = stub_env(self.tmp.name)
        repository = self.repository()
        repository_file = write_file(os.path.join(self.tmp.name, "repositories.txt"), f"tool\t{repository}\n")

        run([sys.executable, WRITE_METADATA, "-r", repository_file, self.archived], env=env)
        self.git(repository, "commit", "-q", "--allow-empty", "-m", "second")
        run([sys.executable, WRITE_METADATA, "-r", repository_file, self.archived], env=env)

        self.assertEqual(self.metadata()["repositories"][0]["repo_version"], self.git(repository, "rev-parse", "HEAD"))

    def test_missing_repository_file_keeps_git_hash(self):
        env = stub_env(self.tmp.name)
        repository_file = write_file(os.path.join(self.tmp.name, "repositories.txt"), f"tool\t{self.repository()}\n")
        run([sys.executable, WRITE_METADATA, "-r", repository_file, self.archived], env=env)

        result = run([sys.executable, WRITE_METADATA, "-r", os.path.join(self.tmp.name, "none.txt"), self.archived],
                     env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.metadata()["repositories"][0]["repo_name"], "tool")


class RepositoriesTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_job_repositories(self):
        settings = {"repositories": {"tool": "/path/to/tool"}}
        repository_file = write_file(os.path.join(self.tmp.name, "repositories.txt"), "extra\t/path/with space\n")

        self.assertEqual(job_repositories(settings, repository_file),
                         {"slurm-workflows": REPOSITORY_DIR, "tool": "/path/to/tool", "extra": "/path/with space"})

        with contextlib.redirect_stdout(io.StringIO()):
            repositories = job_repositories({}, os.path.join(self.tmp.name, "none.txt"))
        self.assertEqual(repositories, {"slurm-workflows": REPOSITORY_DIR})

        repositories = job_repositories({"repositories": {"slurm-workflows": "/other"}})
        self.assertEqual(repositories, {"slurm-workflows": REPOSITORY_DIR})


if __name__ == "__main__":
    unittest.main()
