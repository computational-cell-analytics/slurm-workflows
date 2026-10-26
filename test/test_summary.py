#!/usr/bin/python
# -- coding: utf-8 --
"""Tests of the efficiency summary of an archive: `slurm_workflows/summary.py`."""
import contextlib
import csv
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from slurm_workflows import summary
from slurm_workflows.summary import COLUMNS, select_jobs, summarize
from stubs import write_file


def report(state, cpu):
    return {"JobID": "1", "State": state, "Elapsed": "00:01:00", "TimeEff": "10.0%", "CPUEff": cpu,
            "MemEff": "20.0%"}


# Folder name -> metadata. The dates are not in the order of the folder names.
JOBS = {
    "b_array": {"date": "2026-02-01", "task": "array", "partition": "grete:shared",
                "Reportseff": [report("COMPLETED", "40.0%"), report("FAILED", "20.0%")],
                "SlurmOutput": [{"core_hours": 1.5}, {"core_hours": 2.5}]},
    "a_late": {"date": "2026-03-01", "task": "late", "partition": "standard96",
               "Reportseff": [report("COMPLETED", "90.0%")]},
    "c_early": {"date": "2026-01-01", "task": "early", "partition": "grete:shared",
                "Reportseff": [report("RUNNING", "1.0%")], "SlurmOutput": [{"core_hours": 6.0}]},
}


class SummaryTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archive = os.path.join(self.tmp.name, "archive")
        for folder, metadict in JOBS.items():
            write_file(os.path.join(self.archive, folder, "metadata.json"), json.dumps(metadict))
        os.makedirs(os.path.join(self.archive, "no_metadata"))

    def tearDown(self):
        self.tmp.cleanup()

    def tasks(self, **filters):
        return [metadict["task"] for metadict in select_jobs(self.archive, **filters)]

    def run_summary(self, **kwargs):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            summarize(self.archive, **kwargs)
        return stdout.getvalue()

    def test_order_and_filters(self):
        self.assertEqual(self.tasks(), ["early", "array", "late"])
        self.assertEqual(self.tasks(since="2026-02-01", until="2026-02-01"), ["array"])
        self.assertEqual(self.tasks(states=["FAILED", "RUNNING"]), ["early", "array"])
        self.assertEqual(self.tasks(partitions=["standard96"]), ["late"])

    def test_terminal_summary_skips_unfinished_tasks(self):
        output = self.run_summary()

        self.assertIn("Efficiency summary for 3 job(s):", output)
        self.assertIn("Average CPU Efficiency:    50.0% (n=3)", output)
        self.assertIn("Average Core Hours: 5.00 (n=2)", output)

    def test_tsv_rows(self):
        table = os.path.join(self.tmp.name, "jobs.tsv")
        self.run_summary(output=table)

        with open(table, newline="") as myfile:
            rows = list(csv.DictReader(myfile, dialect="excel-tab"))

        self.assertEqual(list(rows[0]), COLUMNS)
        self.assertEqual([row["task"] for row in rows], ["early", "array", "late"])
        self.assertEqual((rows[0]["State"], rows[0]["CPUEff"], rows[0]["core_hours"]), ("RUNNING", "", "6.0"))
        self.assertEqual((rows[1]["tasks"], rows[1]["State"], rows[1]["CPUEff"], rows[1]["core_hours"]),
                         ("2", "COMPLETED,FAILED", "30.0", "4.0"))
        self.assertEqual(rows[2]["core_hours"], "")

    @unittest.skipIf(summary.openpyxl is None, "openpyxl is not installed")
    def test_xlsx_rows(self):
        table = os.path.join(self.tmp.name, "jobs.xlsx")
        self.run_summary(output=table)

        rows = list(summary.openpyxl.load_workbook(table).active.iter_rows(values_only=True))

        self.assertEqual(list(rows[0]), COLUMNS)
        self.assertEqual(rows[2][COLUMNS.index("CPUEff")], 30.0)

    def test_xlsx_without_openpyxl(self):
        with mock.patch.object(summary, "openpyxl", None):
            with self.assertRaises(ValueError):
                summarize(self.archive, output=os.path.join(self.tmp.name, "jobs.xlsx"))


if __name__ == "__main__":
    unittest.main()
