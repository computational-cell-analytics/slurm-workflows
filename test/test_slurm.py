#!/usr/bin/python
# -- coding: utf-8 --
"""Tests of the slurm output files: `slurm_output_to_dict()` of `slurm_workflows/slurm.py`. The
outputs are shortened copies of real slurm output files of the cluster."""
import os
import tempfile
import unittest

from slurm_workflows.slurm import slurm_output_to_dict

JOB_INFORMATION = """============ Job Information ===================================================
Submitted: 2026-11-03T16:17:35
Started: 2026-11-03T23:59:59
Ended: 2026-11-04T00:00:32
Elapsed: 1 min, Limit: {limit} min, Difference: 179 min
CPUs: 8, Nodes: 1
Estimated credit Consumption: 2.50 core-hours
================================================================================
"""

TRACEBACK = """Current node: ggpu202
Traceback (most recent call last):
  File "/user/u15000/flamingo-tools/scripts/training/micro_sam_box_prompts.py", line 24, in <module>
    from micro_sam.prompt_based_segmentation import segment_from_box
ModuleNotFoundError: No module named 'micro_sam.prompt_based_segmentation'"""

CANCEL_LINE = "slurmstepd: error: *** JOB 123 ON ggpu202 CANCELLED AT 2026-11-04T00:00:32{reason} ***"


class SlurmOutputTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def parse(self, output, limit=180, name="slurm-123.out"):
        """Write a slurm output file with the job output and the section 'Job Information', and read it."""
        path = os.path.join(self.tmp.name, name)
        # The login node can run with an ASCII locale, so the encoding is explicit.
        with open(path, "w", encoding="utf-8") as myfile:
            myfile.write(f"{output}\n{JOB_INFORMATION.format(limit=limit)}")
        return slurm_output_to_dict(path)

    def test_job_information(self):
        job_info = self.parse("Current node: ggpu202\ndone")

        self.assertEqual(job_info, {"file": "slurm-123.out", "submitted": "2026-11-03T16:17:35",
                                    "started": "2026-11-03T23:59:59", "ended": "2026-11-04T00:00:32",
                                    "elapsed_min": 1, "limit_min": 180, "cpus": 8, "nodes": 1, "core_hours": 2.5,
                                    "elapsed_sec": 33})

    def test_array_task(self):
        self.assertEqual(self.parse("done", name="slurm-123_4.out")["array_index"], 4)

    def test_python_exception_after_traceback(self):
        job_info = self.parse(TRACEBACK)

        self.assertEqual(job_info["state"], "FAILED")
        self.assertEqual(job_info["error"], TRACEBACK.splitlines()[-1])

    def test_exception_line_without_traceback_is_no_failure(self):
        self.assertNotIn("state", self.parse("ValueError: the threshold is printed as a hint"))

    def test_last_line_of_a_failure(self):
        lines = ["Missing input of the job: /data/M_AMD_000137_L/mask.zarr",
                 "ERROR: the input /data/M_AMD_000117_L/4_spark/per_channel is missing",
                 "/var/spool/slurmd/job16543667/slurm_script: line 13: /user/common.sh: No such file or directory"]
        for line in lines:
            with self.subTest(line=line):
                job_info = self.parse(f"Current node: ggpu202\n{line}")

                self.assertEqual((job_info["state"], job_info["error"]), ("FAILED", line))

    def test_error_before_the_last_line_is_no_failure(self):
        output = "/var/spool/slurmd/job1/slurm_script: line 13: module: command not found\ndone"

        self.assertNotIn("state", self.parse(output))

    def test_cancel_lines(self):
        reasons = {" DUE TO TIME LIMIT": "TIMEOUT", " DUE TO PREEMPTION": "PREEMPTED",
                   " DUE TO NODE FAILURE": "NODE_FAIL", "": "CANCELLED"}
        for reason, state in reasons.items():
            with self.subTest(state=state):
                line = CANCEL_LINE.format(reason=reason)
                job_info = self.parse(f"running\n{line}\nExit code of the job: 143")

                self.assertEqual((job_info["state"], job_info["error"]), (state, line))

    def test_oom_kill(self):
        line = "slurmstepd: error: Detected 1 oom_kill event in StepId=123.batch. Some of the step tasks have been " \
               "OOM Killed."

        job_info = self.parse(f"running\n{line}\nExit code of the job: 0")

        self.assertEqual((job_info["state"], job_info["error"]), ("OUT_OF_MEMORY", line))

    def test_elapsed_time_at_the_limit(self):
        # The job ran 33 seconds with a limit of 0 minutes. A terminated job prints 143.
        job_info = self.parse("running\nExit code of the job: 143", limit=0)

        self.assertEqual(job_info["state"], "TIMEOUT")
        self.assertNotIn("error", job_info)

    def test_exit_code_zero(self):
        job_info = self.parse(f"{TRACEBACK}\nrecovered\nExit code of the job: 0")

        self.assertEqual((job_info["exit_code"], job_info["state"]), (0, "COMPLETED"))

    def test_exit_code_after_traceback(self):
        job_info = self.parse(f"{TRACEBACK}\nExit code of the job: 1")

        self.assertEqual((job_info["exit_code"], job_info["state"]), (1, "FAILED"))
        self.assertTrue(job_info["error"].startswith("ModuleNotFoundError"))

    def test_exit_code_without_marker(self):
        job_info = self.parse("done\nExit code of the job: 2")

        self.assertEqual((job_info["exit_code"], job_info["state"]), (2, "FAILED"))
        self.assertNotIn("error", job_info)

    def test_error_is_short_ascii(self):
        line = "Missing input of the job: /data/Größe/" + "x" * 400

        error = self.parse(line)["error"]

        self.assertEqual(len(error), 300)
        self.assertTrue(error.startswith("Missing input of the job: /data/Gr??e/"))


if __name__ == "__main__":
    unittest.main()
