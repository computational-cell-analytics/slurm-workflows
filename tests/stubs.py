#!/usr/bin/python
# -- coding: utf-8 --
"""Shared helpers of the tests: stub commands for the cluster tools, and access to the scripts.

The stubs replace `sbatch` and `reportseff`, so the tests run on any machine.
"""
import importlib
import importlib.util
import os
import subprocess
import sys

REPOSITORY_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
SCRIPTS_DIR = os.path.join(REPOSITORY_DIR, "scripts")
MOCKUP_DIR = os.path.join(REPOSITORY_DIR, "mockup_example")

RUN_SBATCH = os.path.join(SCRIPTS_DIR, "01_run_sbatch.sh")
ARCHIVE_SCRIPTS = os.path.join(SCRIPTS_DIR, "02_archive_scripts.sh")
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

# Prints the file 'reportseff' of the state directory, which a test fills with report lines.
REPORTSEFF_STUB = """#!/bin/bash
cat "$STUB_STATE"/reportseff 2>/dev/null
exit 0
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

    The PATH holds only the stubs and the system directories, so a real `sbatch` or `reportseff`
    of the cluster is never called.

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
    if reportseff:
        write_executable(os.path.join(bin_dir, "reportseff"), REPORTSEFF_STUB)
    # 01_run_sbatch.sh calls 'python', which is not on every PATH.
    os.symlink(sys.executable, os.path.join(bin_dir, "python"))

    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([bin_dir, "/usr/bin", "/bin"])
    env["STUB_STATE"] = state_dir
    env.pop("WORK", None)
    env.pop("JOB_ARCHIVE", None)

    return env


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


def load_script(
    name: str,
):
    """Import a script of `scripts/` as a module.

    Args:
        name: File name of the script without '.py'.

    Returns:
        module: The imported script. Its `__main__` block does not run.
    """
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS_DIR, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cochlea_net_module():
    """Import the deployment module of cochlea-net, whose name holds a hyphen.

    Returns:
        module: `utils/cochlea-net_deployment.py`.
    """
    return importlib.import_module("utils.cochlea-net_deployment")


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
