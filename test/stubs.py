#!/usr/bin/python
# -- coding: utf-8 --
"""Shared helpers of the tests: stub commands for the cluster tools, and access to the scripts.

The stubs replace `sbatch`, `scontrol`, `scancel`, `reportseff` and `micromamba`, so the tests run on any machine.
`add_conversion_stubs()` adds `sacct` and the commands of cochlea-net which `slurm_wf.convert` calls.
"""
import os
import subprocess
import sys

from slurm_workflows import repositories

REPOSITORY_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
SCRIPTS_DIR = os.path.join(REPOSITORY_DIR, "scripts")
MOCKUP_DIR = os.path.join(REPOSITORY_DIR, "mockup_example")
TEMPLATE_DIR = os.path.join(REPOSITORY_DIR, "templates", "cochlea-net")
MOCKUP_TEMPLATE = os.path.join(MOCKUP_DIR, "templates", "mockup", "train.template")
MOCKUP_SETTINGS = os.path.join(MOCKUP_DIR, "project_settings", "mockup.json")
MOCKUP_PARAMS = os.path.join(MOCKUP_DIR, "params.json")
MOCKUP_PIPELINE = os.path.join(MOCKUP_DIR, "pipelines", "mockup", "train_evaluate.json")

SUBMIT_SCRIPT = os.path.join(SCRIPTS_DIR, "submit_job.py")
ARCHIVE_SCRIPT = os.path.join(SCRIPTS_DIR, "archive_job.py")
TEST_DIR = os.path.join(REPOSITORY_DIR, "test")

get_repository_status = repositories.get_repository_status
WRITE_METADATA = os.path.join(SCRIPTS_DIR, "write_metadata.py")

# JobIDs start here and count up by one per submission.
FIRST_JOBID = 1000

# The stub reads its state from the directory in STUB_STATE:
#   count         - number of the previous submissions
#   sbatch_calls  - one line with the arguments of each call
#   SBATCH_EXIT   - an environment variable, a non-zero value makes the submission fail
SBATCH_STUB = f"""#!/bin/bash
echo "$@" >> "$STUB_STATE"/sbatch_calls
if [ "${{SBATCH_EXIT:-0}}" != 0 ] ; then
    echo "sbatch: error: stub failure" >&2
    exit "$SBATCH_EXIT"
fi
count=$(cat "$STUB_STATE"/count 2>/dev/null || echo 0)
echo $((count + 1)) > "$STUB_STATE"/count
echo "$(({FIRST_JOBID} + count));cluster"
"""

# Record the arguments of each call in 'scontrol_calls' and 'scancel_calls' of the state directory.
# A non-zero SCONTROL_EXIT makes the release fail.
SCONTROL_STUB = """#!/bin/bash
echo "$@" >> "$STUB_STATE"/scontrol_calls
if [ "${SCONTROL_EXIT:-0}" != 0 ] ; then
    echo "scontrol: error: stub failure" >&2
    exit "$SCONTROL_EXIT"
fi
"""
SCANCEL_STUB = """#!/bin/bash
echo "$@" >> "$STUB_STATE"/scancel_calls
"""

# Prints the file 'reportseff' of the state directory, which a test fills with report lines. The real
# command prints only the requested JobIDs. The caller filters the lines in the same way.
# 'reportseff_calls' gets one line with the arguments of each call.
# A non-zero REPORTSEFF_EXIT fails like a wrapper whose Python module is missing.
REPORTSEFF_STUB = """#!/bin/bash
echo "$@" >> "$STUB_STATE"/reportseff_calls
if [ "${REPORTSEFF_EXIT:-0}" != 0 ] ; then
    echo "ModuleNotFoundError: No module named 'reportseff'" >&2
    exit "$REPORTSEFF_EXIT"
fi
cat "$STUB_STATE"/reportseff 2>/dev/null
exit 0
"""

# 'env export -n <name>' prints a minimal export of the environment.
# 'run -n <name> python -m pip freeze' prints a pip package and a conda package, which the freeze leaves out.
MICROMAMBA_STUB = """#!/bin/bash
if [ "$1" = run ] ; then
    echo "numpy==1.0"
    echo "skan @ file:///conda/work"
else
    echo "name: $4"
    echo "dependencies:"
    echo "  - python=3.12"
    echo "prefix: /envs/$4"
fi
"""


# Prints the first line of 'sacct_states' of the state directory, and removes it unless it is the last line.
# A query of the times prints 'sacct_times' instead. 'sacct_calls' gets one line with the arguments of each call.
SACCT_STUB = """#!/bin/bash
echo "$@" >> "$STUB_STATE"/sacct_calls
if [[ "$*" == *Submit* ]] ; then
    cat "$STUB_STATE"/sacct_times
    exit 0
fi
states="$STUB_STATE"/sacct_states
head -n 1 "$states"
if [ "$(wc -l < "$states")" -gt 1 ] ; then
    tail -n +2 "$states" > "$states.tmp" && mv "$states.tmp" "$states"
fi
"""

# '--help' prints the options --password-stdin and --check. Any other call reads the password from the first line
# of stdin and records its environment in 'transfer_env'. A call with --check records its arguments in
# 'check_calls' and the password in 'check_passwords'; the first line of 'check_exits' is its exit code. Any other
# call uses 'transfer_calls', 'transfer_passwords' and 'transfer_exits' in the same way. Without the exit file,
# the exit code is 0. A successful download writes a file into the folder of -d in -o.
TRANSFER_STUB = """#!/bin/bash
if [ "$1" = --help ] ; then
    echo "  --password-stdin  Read the password from the first line of stdin."
    echo "  --check           Log in and check that the remote data exists."
    exit 0
fi
IFS= read -r password
env >> "$STUB_STATE"/transfer_env
kind=transfer
for arg in "$@" ; do
    if [ "$arg" = --check ] ; then
        kind=check
    fi
done
echo "$@" >> "$STUB_STATE"/${kind}_calls
printf '%s\\n' "$password" >> "$STUB_STATE"/${kind}_passwords
code=0
exits="$STUB_STATE"/${kind}_exits
if [ -s "$exits" ] ; then
    code=$(head -n 1 "$exits")
    tail -n +2 "$exits" > "$exits.tmp" && mv "$exits.tmp" "$exits"
fi
if [ "$kind" = check ] ; then
    exit "$code"
fi
ingest=0
while [ $# -gt 0 ] ; do
    case "$1" in
        --ingest) ingest=1 ;;
        -d) name=$2 ; shift ;;
        -o) out=$2 ; shift ;;
    esac
    shift
done
if [ "$ingest" = 0 ] && [ "$code" = 0 ] ; then
    mkdir -p "$out/$name" && echo raw > "$out/$name/tile.raw"
fi
exit "$code"
"""

# Records the arguments of each call in 'convert_calls'. A non-zero CONVERT_EXIT makes the call fail.
CONVERT_STUB = """#!/bin/bash
echo "$@" >> "$STUB_STATE"/convert_calls
exit "${CONVERT_EXIT:-0}"
"""


def write_executable(
    path: str,
    content: str,
) -> None:
    """Write a script and make it executable.

    Args:
        path: Path of the script.
        content: Content of the script.
    """
    with open(path, "w") as myfile:
        myfile.write(content)
    os.chmod(path, 0o755)


def stub_env(
    tmp_dir: str,
    reportseff: bool = True,
) -> dict:
    """Create the stub commands and return an environment which finds them first.

    The PATH holds only the stubs and the system directories, so a real `sbatch`, `scontrol`, `scancel`,
    `reportseff` or `micromamba` is never called.

    Args:
        tmp_dir: Temporary directory of the test.
        reportseff: Add the `reportseff` stub. Without it, `reportseff` is missing.

    Returns:
        dict: Environment for `subprocess.run`, with STUB_STATE set.
    """
    bin_dir = os.path.join(tmp_dir, "stub_bin")
    state_dir = os.path.join(tmp_dir, "stub_state")
    os.makedirs(bin_dir, exist_ok=True)
    os.makedirs(state_dir, exist_ok=True)

    write_executable(os.path.join(bin_dir, "sbatch"), SBATCH_STUB)
    write_executable(os.path.join(bin_dir, "scontrol"), SCONTROL_STUB)
    write_executable(os.path.join(bin_dir, "scancel"), SCANCEL_STUB)
    if reportseff:
        write_executable(os.path.join(bin_dir, "reportseff"), REPORTSEFF_STUB)
    micromamba = os.path.join(bin_dir, "micromamba")
    write_executable(micromamba, MICROMAMBA_STUB)

    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([bin_dir, "/usr/bin", "/bin"])
    env["MAMBA_EXE"] = micromamba
    env["STUB_STATE"] = state_dir
    env.pop("WORK", None)
    env.pop("JOB_ARCHIVE", None)

    return env


def add_conversion_stubs(
    env: dict,
    sacct_states: list = (),
    transfer_exits: list = (),
    check_exits: list = (),
    sacct_times: list = (),
) -> None:
    """Add the stubs of `sacct`, `flamingo_tools.transfer_data` and `flamingo_tools.convert_data` to `stub_env()`.

    Args:
        env: Output of `stub_env()`.
        sacct_states: The states which `sacct` prints, one per call. The last state repeats.
        transfer_exits: The exit codes of the transfers, one per call. Further calls exit with 0.
        check_exits: The exit codes of the calls with --check, one per call. Further calls exit with 0.
        sacct_times: The lines `<submit>|<start>|<end>` which `sacct` prints for a query of the times.
    """
    bin_dir = env["PATH"].split(os.pathsep)[0]
    write_executable(os.path.join(bin_dir, "sacct"), SACCT_STUB)
    write_executable(os.path.join(bin_dir, "flamingo_tools.transfer_data"), TRANSFER_STUB)
    write_executable(os.path.join(bin_dir, "flamingo_tools.convert_data"), CONVERT_STUB)
    for name, lines in (("sacct_states", sacct_states), ("transfer_exits", transfer_exits),
                        ("check_exits", check_exits), ("sacct_times", sacct_times)):
        with open(os.path.join(env["STUB_STATE"], name), "w") as myfile:
            myfile.write("".join(f"{line}\n" for line in lines))


def stub_calls(
    env: dict,
    name: str,
) -> list:
    """Return the lines of a record file of the stubs, e.g. 'transfer_calls', or an empty list without the file."""
    path = os.path.join(env["STUB_STATE"], name)
    if not os.path.isfile(path):
        return []
    with open(path, "r") as myfile:
        return myfile.read().splitlines()


def sbatch_calls(
    env: dict,
) -> list:
    """Return the arguments of each call of the `sbatch` stub.

    Args:
        env: Output of `stub_env()`.

    Returns:
        list: One string per call.
    """
    path = os.path.join(env["STUB_STATE"], "sbatch_calls")
    if not os.path.isfile(path):
        return []
    with open(path, "r") as myfile:
        return myfile.read().splitlines()


def scontrol_calls(
    env: dict,
) -> list:
    """Return the arguments of each call of the `scontrol` stub.

    Args:
        env: Output of `stub_env()`.

    Returns:
        list: One string per call.
    """
    path = os.path.join(env["STUB_STATE"], "scontrol_calls")
    if not os.path.isfile(path):
        return []
    with open(path, "r") as myfile:
        return myfile.read().splitlines()


def scancel_calls(
    env: dict,
) -> list:
    """Return the arguments of each call of the `scancel` stub.

    Args:
        env: Output of `stub_env()`.

    Returns:
        list: One string per call.
    """
    path = os.path.join(env["STUB_STATE"], "scancel_calls")
    if not os.path.isfile(path):
        return []
    with open(path, "r") as myfile:
        return myfile.read().splitlines()


def reportseff_calls(
    env: dict,
) -> list:
    """Return the arguments of each call of the `reportseff` stub.

    Args:
        env: Output of `stub_env()`.

    Returns:
        list: One string per call.
    """
    path = os.path.join(env["STUB_STATE"], "reportseff_calls")
    if not os.path.isfile(path):
        return []
    with open(path, "r") as myfile:
        return myfile.read().splitlines()


def set_reportseff_output(
    env: dict,
    lines: list,
) -> None:
    """Set the lines which the `reportseff` stub prints.

    Args:
        env: Output of `stub_env()`.
        lines: Report lines in the format 'JobID State Elapsed TimeEff CPUEff MemEff'.
    """
    with open(os.path.join(env["STUB_STATE"], "reportseff"), "w") as myfile:
        myfile.write("\n".join(lines) + "\n")


def run(
    cmd: list,
    env: dict = None,
    cwd: str = None,
) -> subprocess.CompletedProcess:
    """Run a command and capture its output.

    'capture_output' and 'text' need Python 3.7. The login node of the cluster has Python 3.6.

    Args:
        cmd: Command and its arguments.
        env: Environment, or None for the current one.
        cwd: Working directory, or None for the current one.

    Returns:
        subprocess.CompletedProcess: Result with stdout and stderr as strings.
    """
    return subprocess.run(cmd, env=env, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True)


def write_file(
    path: str,
    content: str = "",
) -> str:
    """Write a text file and create its folder.

    Args:
        path: Path of the file.
        content: Content of the file.

    Returns:
        str: The path.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as myfile:
        myfile.write(content)
    return path


def read_file(
    path: str,
) -> str:
    """Read a text file.

    Args:
        path: Path of the file.

    Returns:
        str: Content of the file.
    """
    with open(path, "r") as myfile:
        return myfile.read()


def get_test_repository_status(
    repo_path: str,
) -> str:
    """Return the git status of a repository which a test made.

    The clone of slurm-workflows and a path which is no directory, e.g. '/path/to/my-tool' of the mockup,
    are 'clean', so that the tests do not depend on the machine.

    Args:
        repo_path: Local path of the repository.

    Returns:
        str: 'clean', 'untracked' or 'dirty'.
    """
    if repo_path == REPOSITORY_DIR or not os.path.isdir(repo_path):
        return "clean"
    return get_repository_status(repo_path)


def patched_command(
    script: str,
) -> list:
    """Return a command which runs a script with `get_test_repository_status()` as the git status.

    Args:
        script: Path of the script.

    Returns:
        list: Command for `run()`.
    """
    code = (f"import runpy, sys; sys.path[:0] = [{REPOSITORY_DIR!r}, {TEST_DIR!r}]; "
            "from unittest import mock; import stubs; "
            "mock.patch('slurm_workflows.repositories.get_repository_status', "
            "stubs.get_test_repository_status).start(); "
            f"runpy.run_path({script!r}, run_name='__main__')")
    return [sys.executable, "-c", code]
