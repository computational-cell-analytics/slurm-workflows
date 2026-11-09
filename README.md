# slurm-workflows

This repository is a collection of scripts to run Slurm jobs on the HPC of the GWDG (Gesellschaft für wissenschaftliche Datenverarbeitung Goettingen).
The deploy step fills a template, submits the job, and archives it together with its metadata: the `#SBATCH` values, the JobIDs, the efficiency report of `reportseff`, and the git hash of each repository which the job used.
The archive makes a run reproducible, and it is the data of the efficiency analysis.

The repository holds the templates and pipelines of several projects.
At the moment, most jobs process light-sheet images of cochleae with [cochlea-net](https://github.com/computational-cell-analytics/cochlea-net).

Feedback is appreciated.

## Reading order

| Reader | Pages |
|---|---|
| A cochlea-net user who runs the pipelines | [Getting started](doc/hpc_01_getting_started.md) -> [Data transfer](doc/hpc_02_data_transfer.md) -> [Processing pipelines](doc/hpc_03_processing_pipelines.md). [HPC 101](doc/hpc_101.md) collects basic commands |
| An author of a new project or a new template | [Create a new project](doc/new_project.md) -> [Create a template](doc/new_template.md) -> the worked example `mockup_example/` |
| An analyst of the archive | [The archive and the efficiency analysis](doc/archive.md) |

## Setup

Each project has one settings file, `project_settings/<project>.json`, with the paths and names of a cluster account.
Git does not track this file, so no absolute path enters the repository.
Copy the blueprint of the project once and adapt the values, e.g. for `cochlea-net`:

```bash
cp project_settings/cochlea-net.blueprint.json project_settings/cochlea-net.json
```

The scripts need no installation, and they run with the Python 3.6 of the login node.
To use the commands of the table below, or to import `slurm_workflows` from other code, install the package in editable mode:

```bash
pip install -e .
```

A regular install does not work, because the package finds `project_settings`, `templates` and `pipelines` relative to its own folder.

## Commands

Each command runs the same code as its script.

| Command | Script | Purpose |
|---|---|---|
| `slurm_wf.deploy` | `scripts/deploy.py` | Fill a template, check the input, write the sbatch script, and submit a job or a chain of jobs |
| `slurm_wf.submit` | `scripts/submit_job.py` | Submit an sbatch script, log the JobID, and archive the job, its environment and its metadata |
| `slurm_wf.archive` | `scripts/archive_job.py` | Copy or move the files of one job into its archive folder |
| `slurm_wf.write_metadata` | `scripts/write_metadata.py` | Create or update the `metadata.json` of an archive folder |
| `slurm_wf.update_metadata` | `scripts/update_metadata.py` | Refresh the efficiency reports, and add the data of the slurm output files |
| `slurm_wf.summary` | `scripts/summary.py` | Print the efficiency of the archived jobs, and write one row per job to a table |
| `slurm_wf.add_s3_dataset` | `scripts/add_s3_dataset.py` | Only for `cochlea-net`: add a dataset and its sources to the MoBIE project in the S3 bucket, and check or repair the bucket |

Each command prints its options with `-h`.

## Repository structure

| Folder | Content |
|---|---|
| `scripts/` | The entry points of the command line |
| `slurm_workflows/` | The package with the logic of the scripts. Its module `cli` holds the command line interface |
| `slurm_workflows/projects/` | One package per project, with the deployment module and the code which only that project uses |
| `templates/` | One subfolder per project, with one template per step |
| `pipelines/` | One subfolder per project, with the pipeline definitions |
| `project_settings/` | The blueprint and the settings file of each project |
| `doc/` | The guides, see [Reading order](#reading-order) |
| `mockup_example/` | Every file of a mockup project, from the template to the archive |
| `test/` | The unit tests |

## Glossary

| Term | Meaning |
|---|---|
| project | A set of templates, pipelines, settings and deployment logic which share one name, e.g. `cochlea-net` |
| HPC project | A project of the GWDG HPC Project Portal, with members and computing time |
| MoBIE project | A folder of image data and tables in the MoBIE format, local or in the S3 bucket |
| settings file | `project_settings/<project>.json`, the values of one cluster account |
| blueprint | `project_settings/<project>.blueprint.json`, the committed example of a settings file |
| parameter file | The JSON file of `--params`, with the values of one job, e.g. one cochlea |
| template | A file `templates/<project>/<step>.template`, an sbatch script with placeholders |
| placeholder | A text `<name>` in a template, which the deploy step fills |
| sbatch script | A filled template, `<date>_sbatch_<suffix>.sbatch` |
| step | One entry of a pipeline, named after its template |
| pipeline | An ordered list of steps, which the deploy step submits as a chain of jobs |
| job | One submission of an sbatch script to Slurm, with one JobID |
| deployment module | `slurm_workflows/projects/<package>/deployment.py`, the logic of one project |
| archive directory | The folder which holds the archive folders, `archive_dir` of the settings file |
| archive folder | `<archive_dir>/<date>_<suffix>/`, the archive of one job |
| group | cochlea-net only: `SGN`, `IHC` or `synapses`, the target of a segmentation or detection |

## Tests

The directory `test` contains unit tests for the transfer of the job files, the log files, the filling of the placeholders, and the check of missing files.
Stubs replace `sbatch`, `reportseff`, `micromamba` and `rclone`, so the tests run on every machine and submit nothing.
The tests use only the standard library, so they also run with the Python 3.6 of the login node.
Run them from the root of the repository:

```bash
python -m unittest discover -s test
```

GitHub Actions runs flake8 and the tests with Python 3.6 and 3.14 for every pull request and every push to `main`.
