#!/usr/bin/python
# -- coding: utf-8 --
"""Tests of `slurm_workflows/update.py`: the refresh of the efficiency report by JobID, the replaced
JobIDs, the backfill, and the mark of a pipeline step which never started. `reportseff` is a stub, see
`test/stubs.py`."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

from slurm_workflows.update import update_archive
from stubs import reportseff_calls, set_reportseff_output, stub_env, write_file

TODAY = str(date.today())
# Older than the window of `reportseff -u`, but still kept in the Slurm database.
WEEKS_AGO = str(date.today() - timedelta(days=100))
# Older than the purge time of the Slurm database.
OLD_DATE = "2025-01-01"


def report(jobid, state, elapsed="00:01:00"):
    return {"jobid": jobid, "state": state, "elapsed": elapsed, "time_eff": "1.0%", "cpu_eff": "1.0%",
            "mem_eff": "1.0%"}


def line(jobid, state, elapsed="01:00:00"):
    """Return a line of the `reportseff` output."""
    return f"{jobid} {state} {elapsed} 100.0% 50.0% 10.0%"


def not_started(jobid):
    """Return the `reportseff` line of a job which was cancelled before it started."""
    return f"{jobid} CANCELLED 00:00:00 0.0% --- 0.0%"


class ArchiveTestCase(unittest.TestCase):
    """An archive directory with the stub commands. The subclasses hold the tests."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archive_dir = os.path.join(self.tmp.name, "archive")
        os.makedirs(self.archive_dir)
        self.env = stub_env(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def job(self, task, jobid, reports, step=None, depends_on=None, job_date=TODAY, previous=False, log=None,
            slurm_output=None):
        """Write the archive folder of a job, or the folder of a replaced JobID with `previous=True`."""
        folder = os.path.join(self.archive_dir, f"{job_date}_{task}")
        metadict = {"date": job_date, "task": task, "jobid": jobid, "reportseff": reports}
        if step is not None:
            metadict["pipeline"] = {"name": "chain", "step": step, "of": 3}
            if depends_on is not None:
                metadict["pipeline"]["depends_on"] = depends_on
        if slurm_output is not None:
            metadict["slurm_output"] = slurm_output
        job_folder = os.path.join(folder, "previous", jobid) if previous else folder
        write_file(os.path.join(job_folder, "metadata.json"), json.dumps(metadict))
        if not previous:
            write_file(os.path.join(folder, "log.txt"), f"{jobid}\n" if log is None else log)
        return job_folder

    def chain(self, first_state, job_date=TODAY):
        self.job("first", "1001", [report("1001", first_state)], step=1, job_date=job_date)
        second = self.job("second", "1002", [], step=2, depends_on="1001", job_date=job_date)
        third = self.job("third", "1003", [], step=3, depends_on="1002", job_date=job_date)
        return second, third

    def update(self, report_lines, pattern=None, **options):
        set_reportseff_output(self.env, ["JobID State Elapsed TimeEff CPUEff MemEff"] + report_lines)
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, self.env), contextlib.redirect_stdout(stdout):
            update_archive(self.archive_dir, pattern=pattern, **options)
        return stdout.getvalue()

    def queried_jobids(self):
        """Return the JobIDs of each `reportseff` call."""
        return [sorted(call.split()) for call in reportseff_calls(self.env)]

    def metadata(self, job_folder):
        with open(os.path.join(job_folder, "metadata.json")) as myfile:
            return json.load(myfile)


class UpdateReportseffTest(ArchiveTestCase):

    def test_steps_after_a_failed_step_are_marked(self):
        second, third = self.chain("PENDING")

        output = self.update([line("1001", "TIMEOUT")])

        failure = {"jobid": "1001", "step": 1, "state": "TIMEOUT"}
        self.assertEqual(self.metadata(second)["pipeline"]["failed_at"], failure)
        self.assertEqual(self.metadata(third)["pipeline"]["failed_at"], failure)
        self.assertEqual(self.metadata(third)["reportseff"], [])
        self.assertIn("never started", output)

        output = self.update([line("1001", "TIMEOUT")])

        self.assertNotIn("_second", output)
        self.assertNotIn("_third", output)
        # The second run finds nothing to query: 1001 is final, 1002 and 1003 are marked.
        self.assertEqual(self.queried_jobids(), [["1001", "1002", "1003"]])

    def test_never_started_steps_name_the_root(self):
        # The JobID query lists a step which never started as CANCELLED with 00:00:00.
        second, third = self.chain("PENDING")

        self.update([line("1001", "TIMEOUT"), not_started("1002"), not_started("1003")])

        failure = {"jobid": "1001", "step": 1, "state": "TIMEOUT"}
        self.assertEqual(self.metadata(second)["pipeline"]["failed_at"], failure)
        self.assertEqual(self.metadata(third)["pipeline"]["failed_at"], failure)
        self.assertEqual(self.metadata(second)["reportseff"][0]["state"], "CANCELLED")

    def test_cancelled_pending_step_is_the_root(self):
        # A step which was cancelled by hand while its predecessor ran is the failed step itself.
        second, third = self.chain("RUNNING")

        self.update([line("1001", "COMPLETED"), not_started("1002")])

        self.assertNotIn("failed_at", self.metadata(second)["pipeline"])
        self.assertEqual(self.metadata(third)["pipeline"]["failed_at"],
                         {"jobid": "1002", "step": 2, "state": "CANCELLED"})

    def test_step_with_slurm_output_is_not_marked(self):
        # The slurm output shows that the step started, so the archived state of 1001 is outdated.
        self.job("first", "1001", [report("1001", "FAILED")], step=1)
        second = self.job("second", "1002", [], step=2, depends_on="1001",
                          slurm_output=[{"file": "slurm-1002.out", "started": "2026-01-01T10:00:00"}])

        self.update([])

        self.assertNotIn("failed_at", self.metadata(second)["pipeline"])

    def test_running_step_is_not_a_failure(self):
        second, _ = self.chain("PENDING")

        output = self.update([line("1001", "RUNNING", "00:10:00")])

        self.assertNotIn("failed_at", self.metadata(second)["pipeline"])
        self.assertIn("1002", self.queried_jobids()[0])
        self.assertNotIn("UNTRACKED", output)

    def test_unknown_state_is_not_a_failure(self):
        second, _ = self.chain("REQUEUED")

        self.update([])

        self.assertNotIn("failed_at", self.metadata(second)["pipeline"])

    def test_predecessor_outside_of_the_pattern(self):
        second, _ = self.chain("FAILED")

        self.update([], pattern="second")

        self.assertEqual(self.metadata(second)["pipeline"]["failed_at"],
                         {"jobid": "1001", "step": 1, "state": "FAILED"})

    def test_old_step_is_marked(self):
        second, _ = self.chain("OUT_OF_MEMORY", job_date=OLD_DATE)

        self.update([])

        self.assertEqual(self.metadata(second)["pipeline"]["failed_at"]["state"], "OUT_OF_MEMORY")

    def test_old_job_is_refreshed(self):
        folder = self.job("old", "1001", [report("1001", "PENDING")], job_date=WEEKS_AGO)

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
        folder = self.job("untracked", "1001", [untracked], job_date=WEEKS_AGO)

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

    def test_predecessor_is_a_replaced_jobid(self):
        self.job("first", "1005", [report("1005", "PENDING")], step=1)
        self.job("first", "1001", [report("1001", "CANCELLED")], step=1, previous=True)
        second = self.job("second", "1002", [], step=2, depends_on="1001")

        self.update([line("1005", "RUNNING", "00:10:00")])

        self.assertEqual(self.metadata(second)["pipeline"]["failed_at"]["jobid"], "1001")

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

    def test_purged_job_is_not_queried(self):
        folder = self.job("purged", "1001", [report("1001", "RUNNING")], job_date=OLD_DATE)
        self.job("empty", "1002", [], job_date=OLD_DATE)

        output = self.update([line("1001", "COMPLETED")])

        self.assertEqual(reportseff_calls(self.env), [])
        self.assertEqual(self.metadata(folder)["reportseff"][0]["state"], "UNTRACKED")
        self.assertNotIn("Warning", output)

    def test_bad_date_is_queried(self):
        folder = self.job("bad_date", "1001", [], job_date="iter03")

        self.update([line("1001", "COMPLETED")])

        self.assertEqual(self.metadata(folder)["reportseff"][0]["state"], "COMPLETED")

    def test_unlisted_young_jobs_are_reported(self):
        self.job("old", "1001", [], job_date=OLD_DATE)
        self.job("young", "1002", [], job_date=WEEKS_AGO)
        self.job("new", "1003", [report("1003", "PENDING")])

        output = self.update([line("1003", "COMPLETED")])

        self.assertIn("Warning: reportseff does not list 1 JobID(s) of the last 365 days.", output)
        self.assertIn(f"  {WEEKS_AGO}  1002  young", output)
        self.assertNotIn("1001", output)

    def test_slurm_output_state_of_untracked_predecessor(self):
        # The Slurm database purged 1001 before its report was final. Its slurm output shows the timeout.
        untracked = dict(report("1001", "UNTRACKED"), last_state="RUNNING")
        self.job("first", "1001", [untracked], step=1, job_date=OLD_DATE,
                 slurm_output=[{"file": "slurm-1001.out", "started": "2025-01-01T10:00:00", "state": "TIMEOUT"}])
        second = self.job("second", "1002", [], step=2, depends_on="1001", job_date=OLD_DATE)

        self.update([])

        self.assertEqual(self.metadata(second)["pipeline"]["failed_at"],
                         {"jobid": "1001", "step": 1, "state": "TIMEOUT"})

    def test_unreadable_metadata_is_skipped(self):
        empty = self.job("empty", "1001", [])
        write_file(os.path.join(empty, "metadata.json"), "")
        corrupt = self.job("corrupt", "1002", [], previous=True)
        write_file(os.path.join(corrupt, "metadata.json"), '{"jobid": ')
        folder = self.job("valid", "1003", [report("1003", "PENDING")])

        output = self.update([line("1003", "COMPLETED")])

        self.assertIn(f"Warning: The metadata file {os.path.join(empty, 'metadata.json')} is not valid JSON", output)
        self.assertIn(f"Warning: The metadata file {os.path.join(corrupt, 'metadata.json')} is not valid JSON",
                      output)
        self.assertEqual(self.metadata(folder)["reportseff"][0]["state"], "COMPLETED")

    def test_missing_reportseff_still_marks(self):
        self.env = stub_env(os.path.join(self.tmp.name, "no_reportseff"), reportseff=False)
        second, _ = self.chain("FAILED")

        output = self.update([])

        self.assertIn("Warning: the efficiency reports are not refreshed", output)
        self.assertEqual(self.metadata(second)["pipeline"]["failed_at"]["state"], "FAILED")


class BackfillTest(ArchiveTestCase):

    def test_earlier_jobids_get_metadata(self):
        folder = self.job("first", "1003", [report("1003", "COMPLETED")], log="1001\n1002\n1003\n")

        output = self.update([line("1001", "TIMEOUT"), not_started("1002")], backfill=True)

        self.assertIn("Backfilled the metadata of 2 earlier JobID(s).", output)
        first = self.metadata(os.path.join(folder, "previous", "1001"))
        self.assertEqual({key: first[key] for key in ("date", "task", "jobid", "backfilled")},
                         {"date": TODAY, "task": "first", "jobid": "1001", "backfilled": True})
        self.assertEqual(first["reportseff"][0]["state"], "TIMEOUT")
        self.assertEqual(self.metadata(os.path.join(folder, "previous", "1002"))["reportseff"][0]["state"],
                         "CANCELLED")

        output = self.update([line("1001", "TIMEOUT"), not_started("1002")], backfill=True)

        self.assertIn("Backfilled the metadata of 0 earlier JobID(s).", output)

    def test_empty_metadata_is_repaired(self):
        folder = self.job("first", "1002", [report("1002", "COMPLETED")], log="1001\n1002\n")
        write_file(os.path.join(folder, "previous", "1001", "metadata.json"), "")

        output = self.update([line("1001", "TIMEOUT")], backfill=True)

        self.assertIn("Backfilled the metadata of 1 earlier JobID(s).", output)
        repaired = self.metadata(os.path.join(folder, "previous", "1001"))
        self.assertEqual((repaired["backfilled"], repaired["reportseff"][0]["state"]), (True, "TIMEOUT"))

    def test_existing_previous_folder_is_kept(self):
        folder = self.job("first", "1002", [report("1002", "COMPLETED")], log="1001\n1002\n")
        self.job("first", "1001", [report("1001", "FAILED")], previous=True)

        self.update([], backfill=True)

        self.assertNotIn("backfilled", self.metadata(os.path.join(folder, "previous", "1001")))

    def test_jobid_missing_from_log(self):
        folder = self.job("first", "1002", [report("1002", "COMPLETED")], log="1001\n")

        output = self.update([], backfill=True)

        self.assertIn(f"Warning: {folder} is not backfilled, because log.txt does not list its JobID '1002'.",
                      output)
        self.assertFalse(os.path.exists(os.path.join(folder, "previous")))


class OptionTest(ArchiveTestCase):
    """The options --sbatch and --general also cover the replaced JobIDs."""

    def test_sbatch_covers_previous(self):
        folder = self.job("first", "1002", [report("1002", "COMPLETED")])
        write_file(os.path.join(folder, "sbatch.sbatch"), "#!/bin/bash\n#SBATCH -t 01:00:00\n")
        previous = self.job("first", "1001", [report("1001", "TIMEOUT")], previous=True)
        write_file(os.path.join(previous, "sbatch.sbatch"), "#!/bin/bash\n#SBATCH -t 02:00:00\n")
        backfilled = self.job("first", "1000", [report("1000", "FAILED")], previous=True)

        output = self.update([], sbatch=True)

        self.assertEqual(self.metadata(folder)["runtime"], "01:00:00")
        self.assertEqual(self.metadata(previous)["runtime"], "02:00:00")
        self.assertNotIn("runtime", self.metadata(backfilled))
        self.assertIn(f"Updated sbatch parameters of folder {previous}.", output)

    def test_general_covers_previous(self):
        self.job("first", "1002", [report("1002", "COMPLETED")])
        previous = self.job("first", "1001", [report("999", "COMPLETED")], previous=True)
        with open(os.path.join(previous, "metadata.json")) as myfile:
            metadict = json.load(myfile)
        metadict.update({"date": "2020-01-01", "task": "other"})
        write_file(os.path.join(previous, "metadata.json"), json.dumps(metadict))

        output = self.update([line("1001", "TIMEOUT")], general=True)

        corrected = self.metadata(previous)
        self.assertEqual((corrected["date"], corrected["task"]), (TODAY, "first"))
        self.assertEqual([entry["jobid"] for entry in corrected["reportseff"]], ["1001"])
        self.assertIn(f"Updated essential metadata for {previous}.", output)


if __name__ == "__main__":
    unittest.main()
