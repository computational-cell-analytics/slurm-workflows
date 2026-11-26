#!/usr/bin/python
# -- coding: utf-8 --
"""Tests of `slurm_wf.convert`: the stages, the state file, the resubmission and the handling of the password."""
import contextlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from slurm_workflows.projects.cochlea_net import conversion
from stubs import add_conversion_stubs, stub_calls, stub_env, write_executable, write_file

PASSWORD = "secret pass"
COCHLEA = "R_AMD_WT1_L"
UKON_DIR = "UKON100/archiv/imaging/Lightsheet/Huiskengroup_CTLSM/2026/AMD/R_AMD_000WT1_L"


class FakeDeploy:
    """Stand-in for `deploy_from_argv()`. Records the options and returns one new JobID per submission."""

    def __init__(self):
        self.calls = []
        self.next_jobid = 1000

    def __call__(self, argv):
        self.calls.append(list(argv))
        if "--submit" not in argv:
            return []
        self.next_jobid += 1
        return [str(self.next_jobid - 1)]


class ConvertCochleaTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self.tmp.name, "data")
        self.cochlea_dir = os.path.join(self.data_dir, COCHLEA)
        self.raw_dir = os.path.join(self.cochlea_dir, "1_raw")
        self.state_file = os.path.join(self.cochlea_dir, conversion.STATE_FILE)
        self.settings = write_file(os.path.join(self.tmp.name, "settings.json"),
                                   json.dumps({"data_dir": self.data_dir, "models": {}, "academic_id": "user1",
                                               "hpc_user": "u1"}))
        self.params = write_file(os.path.join(self.tmp.name, "params.json"),
                                 json.dumps({"cochlea": COCHLEA, "ukon_dir": UKON_DIR + "/"}))
        self.env = stub_env(self.tmp.name)
        self.deploy = FakeDeploy()

    def tearDown(self):
        self.tmp.cleanup()

    def convert(self, sacct_states=("COMPLETED",), transfer_exits=(), write_stubs=True, session="tmux",
                check_exits=(), dirty=(), **options):
        """Run the command with the stubs in the multiplexer `session`, with the dirty repositories `dirty`. Returns
        the getpass mock, and the exception or None."""
        if write_stubs:
            add_conversion_stubs(self.env, sacct_states, transfer_exits, check_exits)
        options = {"poll_interval": 0, **options}
        error = None
        with mock.patch.dict(os.environ, self.env), \
                mock.patch.object(conversion, "multiplexer", return_value=session), \
                mock.patch.object(conversion, "deploy_from_argv", self.deploy), \
                mock.patch.object(conversion, "check_dirty", return_value=list(dirty)) as self.check_dirty, \
                mock.patch.object(conversion.getpass, "getpass", return_value=PASSWORD) as prompt, \
                contextlib.redirect_stdout(io.StringIO()) as self.output:
            try:
                conversion.convert_cochlea(self.params, self.settings, **options)
            except ValueError as exc:
                error = exc
        return prompt, error

    def state(self):
        with open(self.state_file) as myfile:
            return json.load(myfile)

    def write_state(self, **state):
        os.makedirs(self.cochlea_dir, exist_ok=True)
        with open(self.state_file, "w") as myfile:
            json.dump({"done": {}, "jobs": [], **state}, myfile)

    def transfers(self):
        return stub_calls(self.env, "transfer_calls")

    def test_full_run(self):
        prompt, error = self.convert(["PENDING", "RUNNING", "COMPLETED"], remove_raw=True)

        self.assertIsNone(error)
        prompt.assert_called_once()
        log = os.path.join(self.cochlea_dir, "transfer_log.txt")
        common = f"-u user1 -l {log} --password-stdin"
        upload = f"--ingest -p {UKON_DIR}/2_converted_stitching -o {self.cochlea_dir}/2_converted_stitching " \
                 "--create-parents"
        self.assertEqual(self.transfers(), [
            f"-p {UKON_DIR} -d 1_raw -o {self.cochlea_dir} --resume {common}",
            f"{upload} -d {COCHLEA}.n5 --resume {common}",
            f"{upload} -d {COCHLEA}.xml {common}",
        ])
        self.assertEqual(stub_calls(self.env, "check_calls"),
                         [f"-p {UKON_DIR} -d 1_raw -o {self.cochlea_dir} --check {common}"])
        n5 = os.path.join(self.cochlea_dir, "2_converted_stitching", f"{COCHLEA}.n5")
        self.assertEqual(stub_calls(self.env, "convert_calls"),
                         [f"-i {self.raw_dir} --out_path {n5} --file_ext .raw --dry_run"])
        self.assertEqual(self.deploy.calls,
                         [["-i", conversion.CONVERT_TEMPLATE, "-j", self.params, "-s", self.settings, "--submit"]])

        state = self.state()
        self.assertEqual(list(state["done"]), list(conversion.STAGES))
        self.assertEqual(state["jobs"], ["1000"])
        self.assertNotIn("driver", state)
        self.assertFalse(os.path.exists(self.raw_dir))

    def test_password_only_on_stdin(self):
        self.convert()

        self.assertEqual(stub_calls(self.env, "transfer_passwords"), [PASSWORD] * 3)
        self.assertEqual(stub_calls(self.env, "check_passwords"), [PASSWORD])
        for name in ("transfer_calls", "check_calls", "transfer_env", "sacct_calls", "convert_calls"):
            self.assertNotIn(PASSWORD, "\n".join(stub_calls(self.env, name)), name)
        with open(self.state_file) as myfile:
            self.assertNotIn(PASSWORD, myfile.read())

    def test_raw_folder_stays_without_option(self):
        _, error = self.convert()

        self.assertIsNone(error)
        self.assertTrue(os.path.isdir(self.raw_dir))
        self.assertNotIn(conversion.REMOVE_RAW, self.state()["done"])

    def test_timeout_submits_again(self):
        _, error = self.convert(["RUNNING", "TIMEOUT", "PENDING", "COMPLETED"])

        self.assertIsNone(error)
        self.assertEqual(len(self.deploy.calls), 2)
        self.assertEqual(self.state()["jobs"], ["1000", "1001"])
        self.assertEqual(len(self.transfers()), 3)

    def test_resubmissions_are_limited(self):
        _, error = self.convert(["TIMEOUT"], max_resubmits=1)

        self.assertIn("after 1 resubmissions", str(error))
        self.assertEqual(len(self.deploy.calls), 2)
        self.assertEqual(len(self.transfers()), 1)
        self.assertNotIn("driver", self.state())

    def test_failed_job_stops_before_the_upload(self):
        _, error = self.convert(["RUNNING", "CANCELLED by 4711"])

        self.assertIn("slurm-1000.out", str(error))
        self.assertEqual(len(self.transfers()), 1)
        self.assertEqual(list(self.state()["done"]), [conversion.DOWNLOAD, conversion.CHECK])

    def test_failed_download_stops_before_the_submission(self):
        _, error = self.convert(transfer_exits=[1])

        self.assertIn("download failed with exit code 1", str(error))
        self.assertEqual(self.deploy.calls, [])
        self.assertEqual(stub_calls(self.env, "convert_calls"), [])
        self.assertEqual(self.state()["done"], {})

    def test_failed_check_stops_before_the_submission(self):
        self.env["CONVERT_EXIT"] = "1"
        _, error = self.convert()

        self.assertIn("dry run of the conversion failed", str(error))
        self.assertEqual(self.deploy.calls, [])

    def test_restart_waits_for_the_running_job(self):
        self.write_state(done={"download": "x", "check": "x"}, jobs=["1000"])

        prompt, error = self.convert(["RUNNING", "COMPLETED"])

        self.assertIsNone(error)
        prompt.assert_called_once()
        self.assertEqual(self.deploy.calls, [])
        self.assertEqual(stub_calls(self.env, "sacct_calls")[0], "-j 1000 -X -n -P -o State")
        self.assertEqual([call.split()[0] for call in self.transfers()], ["--ingest", "--ingest"])
        self.assertEqual(len(stub_calls(self.env, "check_calls")), 1)

    def test_failed_login_check_stops_before_the_wait(self):
        self.write_state(done={"download": "x", "check": "x"}, jobs=["1000"])

        _, error = self.convert(["RUNNING", "COMPLETED"], check_exits=[1])

        self.assertIn("login to UKON", str(error))
        self.assertEqual(stub_calls(self.env, "sacct_calls"), [])
        self.assertEqual(self.transfers(), [])
        self.assertNotIn("driver", self.state())

    def test_restart_submits_a_failed_job_again(self):
        self.write_state(done={"download": "x", "check": "x"}, jobs=["999"])

        _, error = self.convert(["FAILED", "COMPLETED"])

        self.assertIsNone(error)
        self.assertEqual(self.state()["jobs"], ["999", "1000"])

    def test_restart_without_smb_stage_asks_no_password(self):
        os.makedirs(self.raw_dir)
        done = {stage: "x" for stage in conversion.STAGES if stage != conversion.REMOVE_RAW}
        self.write_state(done=done, jobs=["1000"])

        prompt, error = self.convert(remove_raw=True)

        self.assertIsNone(error)
        prompt.assert_not_called()
        self.assertFalse(os.path.exists(self.raw_dir))

    def test_failed_upload_keeps_the_raw_folder(self):
        _, error = self.convert(transfer_exits=[0, 1], remove_raw=True)

        self.assertIn("upload_n5 failed", str(error))
        self.assertTrue(os.path.isdir(self.raw_dir))
        self.assertNotIn(conversion.UPLOAD_N5, self.state()["done"])

    def test_every_stage_done(self):
        self.write_state(done={stage: "x" for stage in conversion.STAGES})

        prompt, error = self.convert()

        self.assertIsNone(error)
        prompt.assert_not_called()
        self.assertEqual(self.transfers(), [])

    def sleeping_process(self, *argv):
        """Start a process that sleeps, with these extra words in its command line."""
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"] + list(argv))
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        return process.pid

    def test_driver_on_this_host_blocks(self):
        pid = self.sleeping_process("slurm_wf.convert")
        self.write_state(driver={"host": socket.gethostname(), "pid": pid, "started": "x"})

        prompt, error = self.convert()

        self.assertIn("tmux attach", str(error))
        prompt.assert_not_called()
        self.assertEqual(self.transfers(), [])

    @unittest.skipUnless(os.path.isdir("/proc/self"), "needs /proc")
    def test_reused_pid_on_this_host_does_not_block(self):
        pid = self.sleeping_process()
        self.write_state(driver={"host": socket.gethostname(), "pid": pid, "started": "x"})

        _, error = self.convert()

        self.assertIsNone(error)
        self.assertEqual(len(self.transfers()), 3)

    def test_driver_on_another_host_needs_takeover(self):
        self.write_state(driver={"host": "glogin99", "pid": 1, "started": "x"})

        _, error = self.convert()
        self.assertIn("ssh glogin99.hpc.gwdg.de", str(error))
        self.assertEqual(self.transfers(), [])

        _, error = self.convert(takeover=True)
        self.assertIsNone(error)
        self.assertNotIn("driver", self.state())

    def test_dry_run(self):
        prompt, error = self.convert(dry_run=True)

        self.assertIsNone(error)
        prompt.assert_not_called()
        self.assertEqual(self.transfers(), [])
        self.assertEqual(self.deploy.calls, [["-i", conversion.CONVERT_TEMPLATE, "-j", self.params, "-s",
                                              self.settings]])
        self.assertFalse(os.path.exists(self.state_file))

    def test_no_multiplexer_stops_before_the_prompt(self):
        prompt, error = self.convert(session=None)

        self.assertIn("--allow-no-tmux", str(error))
        prompt.assert_not_called()
        self.assertEqual(self.transfers(), [])
        self.assertFalse(os.path.exists(self.state_file))

    def test_allow_no_tmux_runs_with_a_warning(self):
        _, error = self.convert(session=None, allow_no_tmux=True)

        self.assertIsNone(error)
        self.assertIn("Warning: This command does not run in tmux", self.output.getvalue())
        self.assertEqual(len(self.transfers()), 3)

    def test_dry_run_without_multiplexer_warns(self):
        _, error = self.convert(session=None, dry_run=True)

        self.assertIsNone(error)
        self.assertIn("Warning: This command does not run in tmux", self.output.getvalue())

    def test_removal_of_the_raw_folder_needs_no_multiplexer(self):
        os.makedirs(self.raw_dir)
        done = {stage: "x" for stage in conversion.STAGES if stage != conversion.REMOVE_RAW}
        self.write_state(done=done, jobs=["1000"])

        _, error = self.convert(session=None, remove_raw=True)

        self.assertIsNone(error)
        self.assertFalse(os.path.exists(self.raw_dir))

    def test_dirty_repository_stops_before_the_prompt(self):
        prompt, error = self.convert(dirty=["slurm-workflows"])

        self.assertIn("Dirty repositories: slurm-workflows", str(error))
        prompt.assert_not_called()
        self.assertEqual(self.transfers(), [])
        self.assertEqual(self.deploy.calls, [])
        self.assertFalse(os.path.exists(self.state_file))

    def test_allow_dirty_skips_the_early_check(self):
        _, error = self.convert(dirty=["slurm-workflows"], allow_dirty=True)

        self.assertIsNone(error)
        self.check_dirty.assert_not_called()
        self.assertEqual(self.deploy.calls[-1][-2:], ["--allow-dirty", "--submit"])

    def test_dirty_repository_after_the_conversion_does_not_stop(self):
        done = {stage: "2026-11-25 10:00:00" for stage in (conversion.DOWNLOAD, conversion.CHECK, conversion.CONVERT)}
        self.write_state(done=done, jobs=["1000"])

        _, error = self.convert(dirty=["slurm-workflows"])

        self.assertIsNone(error)
        self.check_dirty.assert_not_called()

    def test_transfer_command_without_check_stops_before_the_prompt(self):
        add_conversion_stubs(self.env)
        bin_dir = self.env["PATH"].split(os.pathsep)[0]
        write_executable(os.path.join(bin_dir, "flamingo_tools.transfer_data"),
                         "#!/bin/bash\necho '  --password-stdin'\n")

        prompt, error = self.convert(write_stubs=False)

        self.assertIn("no option --check", str(error))
        prompt.assert_not_called()

    def test_old_transfer_command_stops_before_the_prompt(self):
        add_conversion_stubs(self.env)
        bin_dir = self.env["PATH"].split(os.pathsep)[0]
        write_executable(os.path.join(bin_dir, "flamingo_tools.transfer_data"), "#!/bin/bash\necho usage\n")

        prompt, error = self.convert(write_stubs=False)

        self.assertIn("--password-stdin", str(error))
        prompt.assert_not_called()

    def test_unknown_job_state_stops(self):
        _, error = self.convert([""])

        self.assertIn("sacct gave no state of job 1000", str(error))
        self.assertEqual(len(stub_calls(self.env, "sacct_calls")), conversion.UNKNOWN_STATE_LIMIT)

    def test_parameters_need_ukon_dir(self):
        write_file(self.params, json.dumps({"cochlea": COCHLEA}))

        _, error = self.convert()

        self.assertIn("'ukon_dir'", str(error))


class MultiplexerTest(unittest.TestCase):

    def detect(self, processes, parent=100):
        """Return multiplexer() for a process chain {pid: (parent pid, command name)}, starting at `parent`."""
        with mock.patch.object(conversion.os, "getppid", return_value=parent), \
                mock.patch.object(conversion, "parent_process", side_effect=processes.get):
            return conversion.multiplexer()

    @unittest.skipUnless(os.path.isdir("/proc/self"), "needs /proc")
    def test_tmux_server_above_the_shell(self):
        self.assertEqual(self.detect({100: (50, "bash"), 50: (1, "tmux: server (/")}), "tmux")

    @unittest.skipUnless(os.path.isdir("/proc/self"), "needs /proc")
    def test_other_multiplexers(self):
        self.assertEqual(self.detect({100: (50, "bash"), 50: (1, "SCREEN")}), "screen")
        self.assertEqual(self.detect({100: (1, "zellij")}), "zellij")

    @unittest.skipUnless(os.path.isdir("/proc/self"), "needs /proc")
    def test_no_multiplexer(self):
        self.assertIsNone(self.detect({100: (50, "bash"), 50: (1, "sshd")}))
        self.assertIsNone(self.detect({100: (1, "tmuxinator")}))
        # A hidden process, e.g. sshd of root with hidepid, ends the walk.
        self.assertIsNone(self.detect({100: (50, "bash")}))

    @unittest.skipUnless(os.path.isdir("/proc/self"), "needs /proc")
    def test_inherited_variable_is_not_enough(self):
        with mock.patch.dict(os.environ, {"TMUX": "/tmp/tmux-1000/default,1,0"}):
            self.assertIsNone(self.detect({100: (1, "bash")}))

    def test_variables_without_proc(self):
        with mock.patch.object(conversion.os.path, "isdir", return_value=False), \
                mock.patch.dict(os.environ, {"STY": "123.pts-0.glogin1"}):
            os.environ.pop("TMUX", None)
            self.assertEqual(conversion.multiplexer(), "screen")

    @unittest.skipUnless(os.path.isdir("/proc/self"), "needs /proc")
    def test_parent_process_of_this_process(self):
        parent, name = conversion.parent_process(os.getpid())
        self.assertEqual(parent, os.getppid())
        self.assertTrue(name)


class JobStateTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = stub_env(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def state(self, output, exit_code=0):
        """Return the state which `job_state()` reads from this output of sacct."""
        bin_dir = self.env["PATH"].split(os.pathsep)[0]
        write_executable(os.path.join(bin_dir, "sacct"), f"#!/bin/bash\nprintf '{output}'\nexit {exit_code}\n")
        with mock.patch.dict(os.environ, self.env):
            return conversion.job_state("1000")

    def test_first_word_of_the_last_line(self):
        self.assertEqual(self.state("CANCELLED by 4711\\n"), "CANCELLED")
        self.assertEqual(self.state("REQUEUED\\nRUNNING\\n"), "RUNNING")

    def test_no_state(self):
        self.assertIsNone(self.state(""))
        self.assertIsNone(self.state("\\n  \\n"))
        self.assertIsNone(self.state("RUNNING\\n", exit_code=1))


if __name__ == "__main__":
    unittest.main()
