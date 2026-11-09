# Contributing

## Naming convention

Sbatch scripts follow the format `<date>_sbatch_<suffix>.<ext>` with the date in format `yyyy-mm-dd`,
e.g. `2025-03-19_sbatch_apply_unet.sbatch`. All files connected to one job share the same date and
suffix. Archive folders use the shorter format `<date>_<suffix>` (without the `sbatch` infix).

The keys of `metadata.json` are lower case with underscores, e.g. `job_name` and `reportseff`.

## The `sys.path` shim

A Python script in `scripts/` reaches `slurm_workflows/` through a `sys.path` shim, so every
command works without an install step. The script only calls its function of
`slurm_workflows/cli.py`. Put new logic in the package, not in the script:

```python
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from slurm_workflows.cli import main_deploy  # noqa: E402

if __name__ == "__main__":
    main_deploy()
```

A new command needs a function `main_<command>` in `slurm_workflows/cli.py`, an entry in
`console_scripts` of `setup.py`, and a wrapper script. A command which only one project uses goes
into `slurm_workflows/projects/<project>/cli.py`. Use hyphens in long options, e.g. `--archive-dir`.

## Project code

The code which only one project uses lives in `slurm_workflows/projects/<project>/`, with `_` for a
`-` of the project name. Its `deployment.py` defines the functions of the contract which differ from
`slurm_workflows/projects/default.py`, see [Create a new project](doc/new_project.md).

## Python version

The login node of the cluster runs Python 3.6, so the scripts must stay compatible with it.
Do not use a later feature, for example:

- `subprocess.run(capture_output=..., text=...)`. Use `stdout=subprocess.PIPE`,
  `stderr=subprocess.PIPE` and `universal_newlines=True`, which work in every version.
- a builtin generic in an annotation, such as `list[str]`. Use a bare `list` or `typing.List`.
- the walrus operator, a dataclass, or `datetime.fromisoformat`.

## Imports

All imports at the top of the file, never inside functions. A script in `scripts/` puts the
`sys.path` shim between the stdlib imports and the `slurm_workflows` imports.

## Quotes

Double quotes `"` throughout, for both strings and f-strings.

## Docstrings

Google style with `Args:` and `Returns:` sections. One-line summary, blank line, then body.

```python
def read_repository_file(
    repository_file: str,
) -> dict:
    """Read a repository file.

    Each line holds a repository name and a path, separated by a tab. The path can contain spaces.

    Args:
        repository_file: Path of the repository file.

    Returns:
        dict: Dictionary mapping repository names to their local paths.
    """
```

## Linting

Flake8 with a maximum line length of 120. Run:

```bash
python -m flake8 --isolated --max-line-length=120 .
```

## Tests

The tests replace `sbatch`, `reportseff`, `micromamba` and `rclone` with stubs, so they run on any machine:

```bash
python -m unittest discover -s test
```

Run them with Python 3.6 too, before a change reaches the cluster.
