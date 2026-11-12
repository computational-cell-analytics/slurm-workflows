#!/usr/bin/python
# -- coding: utf-8 --
"""Tests of `slurm_workflows/update.py`: the refresh of the efficiency report by JobID, and the
replaced JobIDs. `reportseff` is a stub, see `test/stubs.py`."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import date
from unittest import mock

from slurm_workflows.update import update_archive
from stubs import reportseff_calls, set_reportseff_output, stub_env, write_file

TODAY = str(date.today())
OLD_DATE = "2025-01-01"


def report(jobid, state, elapsed="00:01:00"):
    return {"jobid": jobid, "state": state, "elapsed": elapsed, "time_eff": "1.0%", "cpu_eff": "1.0%",
            "mem_eff": "1.0%"}


def line(jobid, state, elapsed="01:00:00"):
    """Return a line of the `reportseff` output."""
    return f"{jobid} {state} {elapsed} 100.0% 50.0% 10.0%"


class ArchiveTestCase(unittest.TestCase):
    """An archive directory with the stub commands. The subclasses hold the tests."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archive_dir = os.path.join(self.tmp.name, "archive")
        os.makedirs(self.archive_dir)
        self.env = stub_env(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def job(self, task, jobid, reports, job_date=TODAY, previous=False, log=None):
        """Write the archive folder of a job, or the folder of a replaced JobID with `previous=True`."""
        folder = os.path.join(self.archive_dir, f"{job_date}_{task}")
        metadict = {"date": job_date, "task": task, "jobid": jobid, "reportseff": reports}
        job_folder = os.path.join(folder, "previous", jobid) if previous else folder
        write_file(os.path.join(job_folder, "metadata.json"), json.dumps(metadict))
        if not previous:
            write_file(os.path.join(folder, "log.txt"), f"{jobid}\n" if log is None else log)
        return job_folder

    def update(self, report_lines):
        set_reportseff_output(self.env, ["JobID State Elapsed TimeEff CPUEff MemEff"] + report_lines)
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, self.env), contextlib.redirect_stdout(stdout):
            update_archive(self.archive_dir)
        return stdout.getvalue()

    def queried_jobids(self):
        """Return the JobIDs of each `reportseff` call."""
        return [sorted(call.split()) for call in reportseff_calls(self.env)]

    def metadata(self, job_folder):
        with open(os.path.join(job_folder, "metadata.json")) as myfile:
            return json.load(myfile)


class UpdateReportseffTest(ArchiveTestCase):

    def test_old_job_is_refreshed(self):
        folder = self.job("old", "1001", [report("1001", "PENDING")], job_date=OLD_DATE)

        output = self.update([line("1001", "COMPLETED")])

        self.assertEqual(self.metadata(folder)["reportseff"][0]["state"], "COMPLETED")
        self.assertIn(f"Updated the efficiency report of {folder}.", output)

    def test_not_final_state_is_refreshed(self):
        folder = self.job("completing", "1001", [report("1001", "COMPLETING")])

        self.update([line("1001", "COMPLETED")])

        self.assertEqual(self.metadata(folder)["reportseff"][0]["state"], "COMPLETED")

    def test_unlisted_report_is_untracked(self):
        folder = self.job("array", "1001", [report("1001_0", "RUNNING"), report("1001_1", "COMPLETED")],
                          job_date=OLD_DATE)

        output = self.update([])

        reports = self.metadata(folder)["reportseff"]
        self.assertEqual(reports[0]["state"], "UNTRACKED")
        self.assertEqual(reports[0]["last_state"], "RUNNING")
        self.assertEqual(reports[0]["elapsed"], "00:01:00")
        self.assertEqual(reports[1], report("1001_1", "COMPLETED"))
        self.assertIn(f"Marked the report of {folder} as UNTRACKED", output)

        self.assertNotIn("Marked", self.update([]))

    def test_untracked_report_is_replaced_when_listed(self):
        untracked = dict(report("1001", "UNTRACKED"), last_state="RUNNING")
        folder = self.job("untracked", "1001", [untracked], job_date=OLD_DATE)

        self.update([line("1001", "COMPLETED")])

        self.assertEqual(self.metadata(folder)["reportseff"], [{
            "jobid": "1001", "state": "COMPLETED", "elapsed": "01:00:00", "time_eff": "100.0%",
            "cpu_eff": "50.0%", "mem_eff": "10.0%"}])

    def test_unlisted_replaced_jobid_is_untracked(self):
        self.job("first", "1005", [report("1005", "COMPLETED")], job_date=OLD_DATE)
        previous = self.job("first", "1001", [report("1001", "PENDING")], job_date=OLD_DATE, previous=True)

        self.update([])

        self.assertEqual(self.metadata(previous)["reportseff"][0]["state"], "UNTRACKED")
        self.assertEqual(self.metadata(previous)["reportseff"][0]["last_state"], "PENDING")

    def test_replaced_jobid_is_refreshed(self):
        self.job("first", "1005", [report("1005", "COMPLETED")])
        previous = self.job("first", "1001", [report("1001", "PENDING")], previous=True)

        self.update([line("1001", "TIMEOUT")])

        self.assertEqual(self.metadata(previous)["jobid"], "1001")
        self.assertEqual(self.metadata(previous)["reportseff"][0]["state"], "TIMEOUT")

    def test_top_level_keeps_its_jobid(self):
        # log.txt lists a later JobID, e.g. after an archiving which failed before the metadata.
        folder = self.job("first", "1001", [], log="1001\n1002\n")

        self.update([line("1001", "COMPLETED"), line("1002", "FAILED")])

        self.assertEqual(self.metadata(folder)["jobid"], "1001")
        self.assertEqual(self.metadata(folder)["reportseff"][0]["jobid"], "1001")

    def test_one_reportseff_call_for_all_jobs(self):
        self.job("a", "1001", [])
        self.job("b", "1002", [report("1002", "PENDING")])
        self.job("c", "1003", [report("1003", "COMPLETED")])
        self.job("d", "", [])

        self.update([line("1001", "COMPLETED"), line("1002", "COMPLETED")])

        self.assertEqual(self.queried_jobids(), [["1001", "1002"]])

    def test_retention_lines(self):
        self.job("old", "1001", [], job_date=OLD_DATE)
        self.job("new", "1002", [report("1002", "PENDING")])

        output = self.update([line("1002", "COMPLETED")])

        self.assertIn("reportseff listed 1 of 2 queried JobIDs.", output)
        self.assertIn(f"  {OLD_DATE}  1001  old", output)
        self.assertIn(f"Newest unlisted job: {OLD_DATE}.", output)
        self.assertIn(f"Oldest listed job: {TODAY}.", output)


if __name__ == "__main__":
    unittest.main()
