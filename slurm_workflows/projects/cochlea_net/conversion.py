#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2026

Logic of `slurm_wf.convert`: the conversion of the raw data of one cochlea for the stitching, in one command.

The transfers from and to UKON need the GWDG password. A batch job has no terminal, and the compute
nodes cannot reach UKON on port 445, also not with `--constraint=inet` (tested on 2026-11-25). So the
command runs on a login node, in a tmux session. It stops outside a terminal multiplexer, unless
`allow_no_tmux` is set. It asks for the password once, keeps it only in memory, and checks it at once
with a login to UKON. It gives the password to each transfer on stdin, never in a file, an argument
or its environment. `flamingo_tools.transfer_data` then gives it to `smbclient` in the environment
variable PASSWD, which only the user and root can read.

The stages, in order:
1. download - copy `<ukon_dir>/<raw_folder>` to the cochlea folder.
2. check - the dry run of `flamingo_tools.convert_data`, which fails in seconds, before the queue wait.
3. convert - submit the template `convert_data` with the deploy step and wait for the job. A TIMEOUT, a
   node failure or a preemption submits the job again. The conversion continues from its progress file.
   The summary at the end splits the time of this stage into the queue wait and the run time of the job.
4. upload_n5, upload_xml - copy the n5, then the XML, to `<ukon_dir>/2_converted_stitching`. A new
   run of an interrupted upload uploads only the incomplete chunk directories.
5. remove_raw - only with `--remove-raw`: remove the copy of the raw data on the cluster.

The file `convert_pipeline.json` in the cochlea folder records the finished stages and the JobIDs. The
same command continues at the first unfinished stage, e.g. after a reboot of the login node.
"""
import getpass
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import time
from datetime import datetime

from slurm_workflows import REPOSITORY_DIR
from slurm_workflows.cli import deploy_from_argv
from slurm_workflows.projects.cochlea_net.deployment import CONVERSION_STEP, RAW_FOLDER
from slurm_workflows.repositories import check_dirty, job_repositories
from slurm_workflows.settings import load_settings, project_settings_file

PROJECT = "cochlea-net"
CONVERT_TEMPLATE = os.path.join(REPOSITORY_DIR, "templates", PROJECT, f"{CONVERSION_STEP}.template")

TRANSFER_COMMAND = "flamingo_tools.transfer_data"
CONVERT_COMMAND = "flamingo_tools.convert_data"

# Files and folders in the cochlea folder. UKON uses the same name for the converted data.
STATE_FILE = "convert_pipeline.json"
TRANSFER_LOG = "transfer_log.txt"
CONVERTED_FOLDER = "2_converted_stitching"

DOWNLOAD = "download"
CHECK = "check"
CONVERT = "convert"
UPLOAD_N5 = "upload_n5"
UPLOAD_XML = "upload_xml"
REMOVE_RAW = "remove_raw"
STAGES = (DOWNLOAD, CHECK, CONVERT, UPLOAD_N5, UPLOAD_XML, REMOVE_RAW)
SMB_STAGES = (DOWNLOAD, UPLOAD_N5, UPLOAD_XML)

# Slurm states which end a job. A state which is in neither list, e.g. PENDING or RUNNING, means "wait".
RESUBMIT_STATES = ("TIMEOUT", "NODE_FAIL", "PREEMPTED", "BOOT_FAIL")
FAILURE_STATES = ("FAILED", "OUT_OF_MEMORY", "CANCELLED", "DEADLINE", "REVOKED", "SPECIAL_EXIT")

# Number of queries in a row without a state of the job, e.g. while slurmdbd is down, before the command stops.
UNKNOWN_STATE_LIMIT = 12

# The times of sacct, e.g. 2026-11-26T09:10:03.
SACCT_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"

# Command names of the server processes of terminal multiplexers, e.g. 'tmux: server' or 'screen'. A process
# below such a server keeps running when the SSH connection ends.
MULTIPLEXER_PATTERN = re.compile(r"^(tmux|screen|zellij|dtach|abduco)\b")
# Variables that a multiplexer sets in its sessions. Used only without /proc.
MULTIPLEXER_VARIABLES = (("TMUX", "tmux"), ("STY", "screen"), ("ZELLIJ", "zellij"))

# Words in the command line of a running `slurm_wf.convert`.
DRIVER_COMMANDS = ("slurm_wf.convert", "convert_cochlea")


class Conversion:
    """Names and paths of the conversion of one cochlea. A parameter overrides the setting of the same key."""

    def __init__(
        self,
        parameters: dict,
        settings: dict,
    ):
        for key in ("cochlea", "ukon_dir"):
            if not parameters.get(key):
                raise ValueError(f"The parameter file needs the key '{key}'.")
        data_dir = parameters.get("data_dir", settings.get("data_dir"))
        if not data_dir:
            raise ValueError("The settings file needs the key 'data_dir'.")

        self.cochlea = parameters["cochlea"]
        self.ukon_dir = parameters["ukon_dir"].rstrip("/\\")
        self.raw_folder = parameters.get("raw_folder", RAW_FOLDER)
        self.converted_name = parameters.get("converted_name", self.cochlea)

        self.data_dir = data_dir
        self.cochlea_dir = os.path.join(data_dir, self.cochlea)
        self.raw_dir = os.path.join(self.cochlea_dir, self.raw_folder)
        self.converted_dir = os.path.join(self.cochlea_dir, CONVERTED_FOLDER)
        self.state_file = os.path.join(self.cochlea_dir, STATE_FILE)
        self.log_file = os.path.join(self.cochlea_dir, TRANSFER_LOG)

    def transfer_commands(
        self,
        username: str,
    ) -> dict:
        """Return the transfer command of each SMB stage."""
        common = ["-u", username, "-l", self.log_file, "--password-stdin"]
        upload = ["--ingest", "-p", f"{self.ukon_dir}/{CONVERTED_FOLDER}", "-o", self.converted_dir,
                  "--create-parents"]
        return {
            DOWNLOAD: [TRANSFER_COMMAND, "-p", self.ukon_dir, "-d", self.raw_folder, "-o", self.cochlea_dir,
                       "--resume"] + common,
            UPLOAD_N5: [TRANSFER_COMMAND] + upload + ["-d", f"{self.converted_name}.n5", "--resume"] + common,
            UPLOAD_XML: [TRANSFER_COMMAND] + upload + ["-d", f"{self.converted_name}.xml"] + common,
        }

    def login_check_command(
        self,
        username: str,
    ) -> list:
        """Return the command that logs in to UKON and looks up the raw folder, without a transfer."""
        return [TRANSFER_COMMAND, "-p", self.ukon_dir, "-d", self.raw_folder, "-o", self.cochlea_dir, "--check",
                "-u", username, "-l", self.log_file, "--password-stdin"]

    def check_command(self) -> list:
        n5 = os.path.join(self.converted_dir, f"{self.converted_name}.n5")
        return [CONVERT_COMMAND, "-i", self.raw_dir, "--out_path", n5, "--file_ext", ".raw", "--dry_run"]


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def read_state(
    path: str,
) -> dict:
    """Return the content of the state file, or an empty state if the file does not exist."""
    state = {"done": {}, "jobs": []}
    if os.path.isfile(path):
        with open(path, "r") as myfile:
            state.update(json.load(myfile))
    return state


def write_state(
    path: str,
    state: dict,
) -> None:
    """Write the state file. The rename replaces the old file in one step, so an interruption leaves a whole file."""
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as myfile:
        json.dump(state, myfile, indent=4)
        myfile.write("\n")
    os.replace(tmp_path, path)


def reattach_host(
    host: str,
) -> str:
    return host if "." in host else f"{host}.hpc.gwdg.de"


def process_alive(
    pid: int,
) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def driver_alive(
    pid: int,
) -> bool:
    """Return True if the process `pid` runs and is a `slurm_wf.convert`.

    A driver that ends without cleanup, e.g. at a reboot, leaves its PID in the state file. With /proc, a
    new process with the same PID does not count, because its command line differs.
    """
    if not os.path.isdir("/proc/self"):
        return process_alive(pid)
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as myfile:
            cmdline = myfile.read().decode(errors="replace")
    except OSError:
        return False
    return any(command in cmdline for command in DRIVER_COMMANDS)


def parent_process(
    pid: int,
):
    """Return the parent PID and the command name of a process from /proc/<pid>/stat, or None."""
    try:
        with open(f"/proc/{pid}/stat", "r") as myfile:
            stat = myfile.read()
    except OSError:
        return None
    # The name is in parentheses and can contain spaces and parentheses, e.g. 'tmux: server'.
    start, end = stat.find("("), stat.rfind(")")
    fields = stat[end + 1:].split()
    if start < 0 or end < start or len(fields) < 2:
        return None
    return int(fields[1]), stat[start + 1:end]


def multiplexer():
    """Return the name of the terminal multiplexer that runs this process, e.g. 'tmux', or None.

    With /proc, the parent processes are checked, because an environment variable such as TMUX can be
    inherited by a process that does not run in the multiplexer.
    """
    if not os.path.isdir("/proc/self"):
        for variable, name in MULTIPLEXER_VARIABLES:
            if os.environ.get(variable):
                return name
        return None
    pid = os.getppid()
    while pid > 1:
        entry = parent_process(pid)
        if entry is None:
            return None
        pid, name = entry[0], entry[1]
        match = MULTIPLEXER_PATTERN.match(name.lower())
        if match:
            return match.group(1)
    return None


def claim_driver(
    state: dict,
    takeover: bool,
) -> None:
    """Record this process as the driver of the cochlea, unless another driver still runs.

    A driver on another login node cannot be checked from here. Its entry stays after a reboot of that
    node, so `takeover` replaces it.
    """
    host = socket.gethostname()
    driver = state.get("driver")
    if driver:
        if driver["host"] == host:
            if driver["pid"] != os.getpid() and driver_alive(driver["pid"]):
                raise ValueError(f"Another slurm_wf.convert (PID {driver['pid']}) runs for this cochlea on {host}. "
                                 "Attach to its tmux session with 'tmux attach'.")
        elif not takeover:
            raise ValueError(f"A slurm_wf.convert (PID {driver['pid']}) for this cochlea started on {driver['host']} "
                             f"on {driver['started']}. If it still runs, connect with "
                             f"'ssh {reattach_host(driver['host'])}' and 'tmux attach'. If that node was rebooted, "
                             "run this command again with --takeover.")
    state["driver"] = {"host": host, "pid": os.getpid(), "started": now()}


def check_commands() -> None:
    """Stop before the password prompt if the commands of cochlea-net are missing or too old."""
    for command in (TRANSFER_COMMAND, CONVERT_COMMAND):
        if shutil.which(command) is None:
            raise ValueError(f"{command} was not found. Activate the micromamba environment of cochlea-net first.")
    result = subprocess.run([TRANSFER_COMMAND, "--help"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, universal_newlines=True)
    for option in ("--password-stdin", "--check"):
        if option not in result.stdout:
            raise ValueError(f"{TRANSFER_COMMAND} has no option {option}. Update the clone of cochlea-net.")


def run_command(
    cmd: list,
    password: str = None,
) -> int:
    """Run a command with its output on the terminal. The password is the first line of its stdin."""
    print(f"[{now()}] $ {' '.join(shlex.quote(part) for part in cmd)}", flush=True)
    if password is None:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL).returncode
    return subprocess.run(cmd, input=f"{password}\n", universal_newlines=True).returncode


def job_state(
    jobid: str,
):
    """Return the Slurm state of a job, e.g. 'RUNNING', or None if sacct gives no state."""
    try:
        result = subprocess.run(["sacct", "-j", jobid, "-X", "-n", "-P", "-o", "State"], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, universal_newlines=True)
    except OSError:
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if result.returncode != 0 or not lines:
        return None
    # A cancelled job reads e.g. 'CANCELLED by 12345'.
    return lines[-1].split()[0]


def wait_for_job(
    jobid: str,
    poll_interval: float,
) -> str:
    """Query the state of a job every `poll_interval` seconds, and return its final state."""
    last_state = None
    unknown = 0
    while True:
        state = job_state(jobid)
        if state is None:
            unknown += 1
            if unknown >= UNKNOWN_STATE_LIMIT:
                raise ValueError(f"sacct gave no state of job {jobid} in {unknown} queries. Check the job with "
                                 "'squeue --me', then run the same command again.")
        else:
            unknown = 0
            if state != last_state:
                print(f"[{now()}] Job {jobid}: {state}", flush=True)
                last_state = state
            if state == "COMPLETED" or state in RESUBMIT_STATES or state in FAILURE_STATES:
                return state
        time.sleep(poll_interval)


def submit_conversion(
    deploy_argv: list,
) -> str:
    """Submit the conversion with the deploy step, which writes, submits and archives the job."""
    jobids = deploy_from_argv(deploy_argv)
    if len(jobids) != 1:
        raise ValueError(f"The deploy step submitted {len(jobids)} jobs instead of one.")
    return jobids[0]


def job_times(
    jobids: list,
):
    """Return the queue wait and the run time of jobs from sacct.

    Args:
        jobids: JobIDs of the jobs.

    Returns:
        tuple of (queue seconds, run seconds, number of jobs), summed over the jobs with a start and an end
        time, or None if sacct gives no such job.
    """
    try:
        result = subprocess.run(["sacct", "-j", ",".join(jobids), "-X", "-n", "-P", "-o", "Submit,Start,End"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    except OSError:
        return None
    if result.returncode != 0:
        return None

    queue = run = 0.0
    jobs = 0
    for line in result.stdout.splitlines():
        try:
            submit, start, end = (datetime.strptime(value.strip(), SACCT_TIME_FORMAT) for value in line.split("|"))
        except ValueError:
            # A job which did not start or end gives 'Unknown' or 'None'.
            continue
        queue += (start - submit).total_seconds()
        run += (end - start).total_seconds()
        jobs += 1
    return (queue, run, jobs) if jobs else None


def run_conversion(
    state: dict,
    save,
    deploy_argv: list,
    poll_interval: float,
    max_resubmits: int,
) -> list:
    """Submit the conversion, or wait for the job of an earlier run, until a job completes.

    A failed job of an earlier run is submitted again, because a new run of the command is the decision
    to try again. A failed job of this run stops the command.

    Returns:
        list: JobIDs of the jobs that the command waited for, oldest first.
    """
    jobid = state["jobs"][-1] if state["jobs"] else None
    earlier_job = jobid is not None
    resubmits = 0
    jobids = []
    while True:
        if jobid is None:
            jobid = submit_conversion(deploy_argv)
            state["jobs"].append(jobid)
            save()
        elif earlier_job:
            print(f"Job {jobid} of an earlier run.")

        jobids.append(jobid)
        final_state = wait_for_job(jobid, poll_interval)
        if final_state == "COMPLETED":
            return jobids
        if final_state in FAILURE_STATES and not earlier_job:
            raise ValueError(f"Job {jobid} ended with {final_state}. See slurm-{jobid}.out. Run the same command "
                             "again to submit the conversion again.")
        if final_state in RESUBMIT_STATES:
            if resubmits >= max_resubmits:
                raise ValueError(f"Job {jobid} ended with {final_state} after {resubmits} resubmissions. See "
                                 f"slurm-{jobid}.out. Run the same command again to submit the conversion again.")
            resubmits += 1
        print(f"Job {jobid} ended with {final_state}. The conversion is submitted again and continues where it "
              "stopped.")
        jobid = None
        earlier_job = False


def remove_raw_folder(
    conversion: Conversion,
) -> None:
    """Remove the copy of the raw data. Only `<data_dir>/<cochlea>/<raw_folder>` can be removed."""
    raw_dir = os.path.realpath(conversion.raw_dir)
    if os.path.dirname(os.path.dirname(raw_dir)) != os.path.realpath(conversion.data_dir):
        raise ValueError(f"The raw folder {raw_dir} is not a folder <data_dir>/<cochlea>/<raw_folder>, so it is not "
                         "removed.")
    if not os.path.isdir(raw_dir):
        print(f"The raw folder {raw_dir} does not exist.")
        return
    print(f"[{now()}] Removing {raw_dir}", flush=True)
    shutil.rmtree(raw_dir)


def convert_cochlea(
    params_file: str,
    settings_file: str = None,
    username: str = None,
    remove_raw: bool = False,
    poll_interval: float = 300,
    max_resubmits: int = 2,
    force: bool = False,
    allow_dirty: bool = False,
    takeover: bool = False,
    dry_run: bool = False,
    allow_no_tmux: bool = False,
) -> None:
    """Convert the raw data of one cochlea: download, check, convert, upload and, optionally, remove the raw data.

    Args:
        params_file: The parameter file of the deploy step, with the key 'ukon_dir' for the sample folder on UKON.
        settings_file: Settings file of cochlea-net, or None for project_settings/cochlea-net.json.
        username: GWDG username, or None for 'academic_id' of the settings file.
        remove_raw: Remove the raw folder on the cluster after the upload.
        poll_interval: Seconds between two queries of the job state.
        max_resubmits: Number of resubmissions after a TIMEOUT, a node failure or a preemption.
        force: Pass --force to the deploy step.
        allow_dirty: Submit although a tracked repository is dirty. Without it, a dirty repository stops the
            command before the password prompt.
        takeover: Replace the entry of a driver on another login node in the state file.
        dry_run: Print the stages and run the deploy step without --submit. Nothing is transferred or submitted.
        allow_no_tmux: Run although no terminal multiplexer such as tmux runs the command.
    """
    if settings_file is None:
        settings_file = project_settings_file(PROJECT)
    settings = load_settings(settings_file)
    with open(params_file, "r") as myfile:
        parameters = json.load(myfile)
    conversion = Conversion(parameters, settings)
    username = username or settings.get("academic_id")
    if not username:
        raise ValueError("No GWDG username: set 'academic_id' in the settings file, or pass --username.")

    state = read_state(conversion.state_file)
    stages = [stage for stage in STAGES if stage not in state["done"] and (stage != REMOVE_RAW or remove_raw)]

    deploy_argv = ["-i", CONVERT_TEMPLATE, "-j", params_file, "-s", settings_file]
    if force:
        deploy_argv.append("--force")
    if allow_dirty:
        deploy_argv.append("--allow-dirty")

    host = socket.gethostname()
    print(f"Cochlea {conversion.cochlea} on {host}. To reattach after a lost connection: "
          f"ssh {reattach_host(host)}, then 'tmux attach'.")
    if not stages:
        print(f"Every stage is done, see {conversion.state_file}.")
        return
    print(f"Stages to run: {', '.join(stages)}.")
    if REMOVE_RAW not in stages and REMOVE_RAW not in state["done"]:
        print(f"{conversion.raw_dir} stays on the cluster. Pass --remove-raw to remove it after the upload.")

    # Only the removal of the raw folder ends in seconds.
    long_run = any(stage != REMOVE_RAW for stage in stages)
    if long_run and multiplexer() is None:
        message = ("This command does not run in tmux or screen. It runs for hours, and it stops when the "
                   "connection to the cluster ends.")
        if dry_run or allow_no_tmux:
            print(f"Warning: {message}")
        else:
            raise ValueError(f"{message} Start it in a tmux session, see doc/hpc_04_data_conversion.md. "
                             "--allow-no-tmux skips this check.")

    if dry_run:
        deploy_from_argv(deploy_argv)
        return

    # The deploy step checks this again at the submission, but that is hours after the start of the download.
    if CONVERT in stages and not allow_dirty:
        dirty = check_dirty(job_repositories(settings), settings.get("untracked_files_dirty", False))
        if dirty:
            raise ValueError(f"Dirty repositories: {', '.join(dirty)}. Pass --allow-dirty to submit anyway.")

    if long_run:
        check_commands()
    os.makedirs(conversion.cochlea_dir, exist_ok=True)

    def save():
        write_state(conversion.state_file, state)

    claim_driver(state, takeover)
    save()

    password = None
    durations = []
    try:
        if any(stage in SMB_STAGES for stage in stages):
            password = getpass.getpass(f"GWDG password of {username}: ")
            # A mistyped password would otherwise show only at the upload, after the job.
            returncode = run_command(conversion.login_check_command(username), password)
            if returncode != 0:
                raise ValueError(f"The login to UKON or the lookup of {conversion.ukon_dir}/{conversion.raw_folder} "
                                 "failed, see the output above. Check the password and 'ukon_dir', then run the same "
                                 "command again.")
        transfers = conversion.transfer_commands(username)

        for stage in stages:
            print(f"\n[{now()}] === {stage} ===", flush=True)
            start = time.time()
            if stage in SMB_STAGES:
                returncode = run_command(transfers[stage], password)
                if returncode != 0:
                    raise ValueError(f"The stage {stage} failed with exit code {returncode}. See the output above "
                                     f"and {conversion.log_file}. Run the same command again: a download continues "
                                     "with the missing files.")
            elif stage == CHECK:
                # The dry run of a started conversion adds nothing.
                if not state["jobs"]:
                    returncode = run_command(conversion.check_command())
                    if returncode != 0:
                        raise ValueError(f"The dry run of the conversion failed with exit code {returncode}. Check "
                                         f"the raw data in {conversion.raw_dir}.")
            elif stage == CONVERT:
                convert_jobids = run_conversion(state, save, deploy_argv + ["--submit"], poll_interval,
                                                max_resubmits)
            elif stage == REMOVE_RAW:
                if UPLOAD_N5 not in state["done"] or UPLOAD_XML not in state["done"]:
                    raise ValueError("The raw folder is removed only after the upload of the n5 and the XML.")
                remove_raw_folder(conversion)
            state["done"][stage] = now()
            save()
            durations.append((stage, time.time() - start))
    finally:
        password = None
        state.pop("driver", None)
        save()

    print(f"\n[{now()}] All stages are done.")
    for stage, seconds in durations:
        print(f"  {stage}: {seconds / 3600:.1f} h")
        if stage == CONVERT:
            # The time of the stage also holds the deploy step and up to one poll interval per job.
            times = job_times(convert_jobids)
            if times is not None:
                queue, run, jobs = times
                count = f" ({jobs} jobs)" if jobs > 1 else ""
                print(f"    queue wait: {queue / 3600:.1f} h")
                print(f"    conversion job: {run / 3600:.1f} h{count}")
