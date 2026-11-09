# Create a new project

A project is a set of templates, pipelines, settings and deployment logic which share one name, for
example `cochlea-net`.
This guide shows how to add a project.
[Create a template](new_template.md) shows how to turn a working sbatch script into a template.
The project `mockup` in `mockup_example/` is the worked example of both guides.

## The four places of a project

| Place | Content | Required |
|---|---|---|
| `templates/<project>/` | One `<step>.template` file per step | yes |
| `project_settings/<project>.blueprint.json` | The keys of the settings file, with example values. Tracked by git | yes |
| `pipelines/<project>/` | One `<name>.json` file per pipeline | only to chain steps |
| `slurm_workflows/projects/<package>/deployment.py` | The logic which only the project needs | only if the settings and the parameters cannot fill every placeholder |

`<package>` is the project name with `_` for each `-`, e.g. `slurm_workflows/projects/cochlea_net/`.
The package also holds the other code which only the project uses, e.g. `mobie.py` of cochlea-net.

`scripts/deploy.py` finds the project from its input:

- `--input templates/<project>/<step>.template`: the folder of the template.
- `--pipeline <project>/<name>`: the first part of the name.
- `--pipeline <path>/pipelines/<project>/<name>.json`: the folder of the pipeline file.

The deploy step records the project as `project` in the `metadata.json` of every job.

## The settings file

Each person keeps the values of their cluster account in `project_settings/<project>.json`.
Git does not track this file, so no absolute path and no mail address enters the repository.
`.gitignore` holds two rules for this:

```
project_settings/*.json
!project_settings/*.blueprint.json
```

Commit the blueprint `project_settings/<project>.blueprint.json` with example values.
Each person copies it once and adapts the values:

```bash
cp project_settings/<project>.blueprint.json project_settings/<project>.json
```

A missing settings file gives a warning which names the blueprint to copy, and the deploy step
continues with empty settings.

Every settings file needs the keys `academic_id` and `hpc_user`.
They name the person who runs a job, so that the archive records who spent the computing time of the
HPC project.
Both keys may be empty, but a missing key stops the deploy step.
A project can use more keys:

| Key | Template placeholder | Purpose |
|---|---|---|
| A scalar key, e.g. `data_dir` | `<data_dir>` | Any value of the account |
| `archive_dir` | `<archive_dir>` | The archive directory, the default of `--archive-dir` |
| An entry of `repositories`, e.g. `"my-tool": "/path/to/my-tool"` | `<repo:my-tool>` | The path of a git repository. The archive records its git hash with each job |
| - | `<repo:slurm-workflows>` | The path of this repository, always available and always recorded |
| Any other nested key, e.g. `models` of cochlea-net | none | Only a deployment module can read it |

## Pipelines

A pipeline is an ordered list of steps, which the deploy step submits as a chain of Slurm jobs.
A step is the name of a template in `templates/<project>/`, without `.template`.
The file `pipelines/<project>/<name>.json` defines the pipeline:

| Key | Required | Purpose |
|---|---|---|
| `steps` | yes | The step names in the order of submission |
| `description` | no | One sentence, which the deploy step prints |
| `group` | cochlea-net | The group (`SGN`, `IHC`, `synapses`) of a step which serves more than one group |
| `mobie_steps` | cochlea-net | The steps which export the result to MoBIE and the S3 bucket |
| `cleanup_steps` | cochlea-net | The steps which remove the intermediate data |
| `mobie_cleanup_steps` | cochlea-net | The steps which remove the local MoBIE copy |

Every other key belongs to the deployment module of the project, which reads it in `select_steps()`
and `prepare_step()`.
A new pipeline needs no code change.
The deploy step checks that each step has a template before it submits anything.

The deploy step renders every step before it submits the first one, so an error in a later step
leaves no job of the chain in the queue.
Each step starts only after the previous step completed successfully (`--dependency=afterok`).
A failed step cancels the rest of the chain.
Each template checks its own input when the job runs, see
[the guard of a step](new_template.md#the-guard-of-a-step).
Use `--start-at <step>` to resume a chain at a failed step.

## When a project needs a deployment module

A project without a deployment module uses `slurm_workflows/projects/default.py`.
The default deployment:

- adds no command line option;
- submits all `steps` of a pipeline;
- fills the placeholders only from the settings file and the parameter file;
- names the sbatch script after the template;
- gives no warning of its own.

The deploy step then prints the note "project '<project>' has no deployment module".

Add a deployment module if the project needs one of these:

- a placeholder value which the deploy step must compute, e.g. a folder name from two parameters, or
  a path from a nested setting;
- a command line option of the project;
- a check of the data before the submission;
- an sbatch script name which differs from the template name;
- a pipeline key of the project, e.g. a list of optional steps.

### The contract of the four functions

`slurm_workflows/deploy.py` calls four functions of the deployment module.
A module defines only the functions which differ from `slurm_workflows/projects/default.py`.
The default deployment supplies the other functions.
The docstrings of `default.py` give the full contract.

| Function | Returns | Purpose |
|---|---|---|
| `add_arguments(parser)` | nothing | Add the options of the project to the `argparse` parser of `deploy.py` |
| `select_steps(definition, settings, args)` | list of step names | Select the steps of a pipeline to submit |
| `prepare_step(settings, placeholder_values, template_file, args, definition)` | `(placeholder_values, script_name, notes)` | Add the placeholder values of one job, and name its sbatch script |
| `check_steps(template_files, sbatch_files)` | list of warnings | Check the sbatch scripts before the submission |

The arguments:

- `args` is the `argparse.Namespace` of `deploy.py`. It holds the generic options, e.g. `args.submit`
  and `args.force`, and the options of the project.
- `definition` is the content of the pipeline file, plus the keys `name` (the file name without
  `.json`) and `template_dir`. It is None for a single job of `--input`.
- `settings` is the content of the settings file, or an empty dictionary.
- `placeholder_values` holds the placeholders of the settings, with the parameters on top.
  `prepare_step()` can change it in place.
- `script_name` is the name of the sbatch script without the date and `.sbatch`. The deploy step
  writes `<date>_sbatch_<script_name>.sbatch`.
- `notes` are messages, which the deploy step prints after the warnings. A note never stops the
  submission.
- A warning of `check_steps()` stops `--submit`, unless `--force` is given.

`deploy.py` calls `select_steps()` before it applies `--start-at`, so `--start-at` can name a step
which the project adds.

### A minimal deployment module

This module computes the placeholder `<run_name>` from two parameters.
It also adds the run name to the name of the sbatch script, so two runs on the same day get two
archive folders:

```python
# slurm_workflows/projects/my_project/deployment.py
import os

from slurm_workflows.templates import TEMPLATE_SUFFIX


def prepare_step(
    settings: dict,
    placeholder_values: dict,
    template_file: str,
    args,
    definition: dict = None,
) -> tuple:
    """Add the run name, and add it to the name of the sbatch script."""
    run_name = f"{placeholder_values['dataset']}_lr{placeholder_values['learning_rate']}"
    placeholder_values["run_name"] = run_name
    step = os.path.basename(template_file)[:-len(TEMPLATE_SUFFIX)]
    return placeholder_values, f"{step}_{run_name}", []
```

Add an empty `slurm_workflows/projects/my_project/__init__.py` beside it.
The only full example is `slurm_workflows/projects/cochlea_net/deployment.py`.

## Order of precedence of the placeholder values

The deploy step collects the values of one job in this order. A later source overrides an earlier one:

1. The settings file: each scalar key, and `<repo:name>` for each entry of `repositories`.
2. The parameter file. A parameter overrides the setting of the same key.
3. `prepare_step()` of the deployment module.

A deployment module decides for each value whether a parameter can override it.
`placeholder_values[key] = value` always sets the value.
`placeholder_values.setdefault(key, value)` keeps a parameter of the same key.
[Processing pipelines](hpc_03_processing_pipelines.md#placeholders-of-the-deployment) lists the
values of cochlea-net and which of them a parameter can override.

A placeholder without a value stops the deploy step, and no sbatch script is written.

## A project outside the repository

The deploy step reads the templates of a pipeline from the folder `templates/<project>/` beside the
folder `pipelines/<project>/` of the pipeline file.
So a pipeline outside this repository brings its own templates, e.g. the mockup:

```bash
python scripts/deploy.py --pipeline mockup_example/pipelines/mockup/train_evaluate.json \
    --params mockup_example/params.json --settings mockup_example/project_settings/mockup.json
```

The deploy step looks for the other two places only inside this repository:

- The settings file: only `project_settings/<project>.json`. Give the settings file with `--settings`.
- The deployment module: only `slurm_workflows/projects/<package>/deployment.py`. A project outside
  the repository uses the default deployment.

## Test a project

A test is optional, but it finds a broken template before a job fails on the cluster.
`test_render_matches_committed_sbatch` in `test/test_templates.py` is a good start.
It renders the mockup template with the mockup settings and parameters, and compares the result with
the committed sbatch script.
A project with a deployment module can also test `prepare_step()`, as `test/test_inputs.py` does for
cochlea-net.
Run the tests from the root of the repository:

```bash
python -m unittest discover -s test
```

## The worked example: mockup

The folder `mockup_example/` holds every file of a project with a pipeline of two steps, from the
templates to the archive.
The jobs do not run, and the paths and names are placeholders.
The folder is outside the top-level folders `templates/` and `project_settings/`, so the mockup is
not listed as a project, and git tracks its settings file.

| File | Role |
|---|---|
| `project_settings/mockup.json` | The settings file: mail address, HPC user, environment, archive directory, data directory, and the repository `my-tool` |
| `templates/mockup/train.template` | The first step. It trains a network on `<data_dir>/<dataset>` and writes into `<data_dir>/runs/<run_name>` |
| `templates/mockup/evaluate.template` | The second step. Its guard checks the model of the first step |
| `pipelines/mockup/train_evaluate.json` | The pipeline which chains `train` and `evaluate` |
| `params.json` | The parameter file: `dataset` and `run_name` |
| `YYYY-MM-DD_sbatch_train.sbatch` | The sbatch script of the first step |
| `YYYY-MM-DD_log_train.txt` | The log file with the fictional JobID 1234567 |
| `repository_list.txt` | A repository file for `--repository-file`. Each line holds a name and a path |
| `YYYY-MM-DD_train/` | The archive folder of the first step, see [The archive](archive.md) |

The two steps share the folder `<data_dir>/runs/<run_name>`, because both templates build it from the
same placeholders.
The dry run writes one sbatch script per step into the current directory and submits nothing:

```bash
python scripts/deploy.py --pipeline mockup_example/pipelines/mockup/train_evaluate.json \
    --params mockup_example/params.json --settings mockup_example/project_settings/mockup.json
```

It ends with these lines:

```
The input of YYYY-MM-DD_sbatch_evaluate.sbatch is verified when the job runs.
Warning: the input of the job does not exist: /path/to/data/cifar10
Warning: the archive directory /path/to/job_archive does not exist, so the jobs would not be archived
Note: project 'mockup' has no deployment module. The placeholders were filled from the settings and the parameters only.
Nothing is submitted. Use --submit to submit the jobs.
```

The warnings are correct, because the data and the archive directory of the mockup do not exist.
With `--submit`, the warnings stop the deploy step.

## Checklist for a new project

1. Create `templates/<project>/` and turn each working sbatch script into a template, see
   [Create a template](new_template.md).
2. Create `project_settings/<project>.blueprint.json` with `academic_id`, `hpc_user` and a key for
   each placeholder of the account. Add `repositories` if the jobs use other git repositories.
   Commit the blueprint.
3. Copy the blueprint to `project_settings/<project>.json` and fill in the values of your account.
4. To chain steps, add `pipelines/<project>/<name>.json`.
5. If the settings and the parameters cannot fill every placeholder, add
   `slurm_workflows/projects/<package>/deployment.py`.
6. Write a parameter file, run the dry run, and check the sbatch scripts:
   ```bash
   python scripts/deploy.py --pipeline <project>/<name> --params params.json
   ```
7. Submit the jobs:
   ```bash
   python scripts/deploy.py --pipeline <project>/<name> --params params.json --submit
   ```
