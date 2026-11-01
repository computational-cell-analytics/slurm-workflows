# Scripts to improve reproducibility of HPC

This repository is a collection of scripts connected to the usage of HPC, specifically at the GWDG (Gesellschaft für wissenschaftliche Datenverarbeitung Goettingen).

The goal is to make the usage of HPC resources more reproducible by archiving metadata.

Feedback is appreciated.

## Setup

Each project has one settings file, `project_settings/<project>.json`, with the paths and names which are specific to a cluster account.
This file is not tracked by git, so that no absolute path enters the repository.
Copy the blueprint file of the project once and adapt the values to your account, e.g. for `cochlea-net`:

```bash
cp project_settings/cochlea-net.blueprint.json project_settings/cochlea-net.json
```

The settings file of `cochlea-net` contains the mail address and the Slurm account for the sbatch header, the identifiers of the HPC user, the directories of the data and of the job archive, the names of the micromamba environments, the local paths of the git repositories, and the paths of the trained models.
The keys `academic_id` and `hpc_user` name the person who runs a job, so that the archive records who spent the computing time of the HPC project. Both may stay empty.
Every settings file needs the keys `academic_id` and `hpc_user`. A project can require more keys.
The optional entry `repositories` lists the git repositories of the project. Their hashes are archived with each job, together with the hash of slurm-workflows.
`scripts/deploy.py` reads the settings file of the project and fills the values into the templates.
Use the option `-s` to select a different settings file.

The scripts need no installation.
To use the commands below, or to import `slurm_workflows` from other code, install the package in editable mode:

```bash
pip install -e .
```

A regular install does not work, because the package finds `project_settings`, `templates` and `pipelines` relative to its own folder.
Each command runs the same code as its script:

| Command | Script |
|---|---|
| `slurm_wf.deploy` | `scripts/deploy.py` |
| `slurm_wf.write_metadata` | `scripts/write_metadata.py` |
| `slurm_wf.update_metadata` | `scripts/update_metadata.py` |
| `slurm_wf.summary` | `scripts/summary.py` |
| `slurm_wf.submit` | `scripts/submit_job.py` |
| `slurm_wf.archive` | `scripts/archive_job.py` |
| `slurm_wf.add_s3_dataset` | `scripts/add_s3_dataset.py`, only for `cochlea-net` |

## Repository structure

The directory `scripts` contains the entry points which are run from the command line.
The package `slurm_workflows` contains the logic of the Python scripts and the utility functions which they share.
Its module `cli` contains the command line interface of the generic commands.
Its subpackage `projects` contains one package per project, with the deployment module and the code which only that project uses.
The directory `templates` contains one subfolder per project with one template per processing step.
The file `templates/cochlea-net/steps.json` holds the group and the resources of each cochlea-net template, with the deviations of a species such as the gerbil `G`.
The directory `pipelines` contains one subfolder per project with the definitions which chain those steps.
The directory `project_settings` contains the settings file of each project.
The directory `doc` contains the documentation of the work on the HPC:

- [Getting started](doc/hpc_01_getting_started.md) - the HPC project, the SSH connection, and the
  micromamba environment for the jobs.
- [Data transfer](doc/hpc_02_data_transfer.md) - the transfer from and to UKON100 and the S3 bucket.
- [Processing pipelines](doc/hpc_03_processing_pipelines.md) - the settings file, the parameter file
  per cochlea, and the commands which run a pipeline.
- [HPC 101](doc/hpc_101.md) - the bash terminal, the quota, the screens, and the file permissions.

## Current concept

Jobs are submitted to the cluster using a JobID. The JobID has the benefit of being inherently unique, so it could be used as the sole identifier of a script and related data.
However, the number itself is not very informative and a specific task might be started multiple times before achieving a satisfying result.
The current concept involves the usage of a single sbatch script for a specific purpose linked to a date.
It should contain the date and a suffix in the file format `<date>_sbatch_<suffix>` with the date in format `yyyy-mm-dd`, e.g. `2025-03-19_sbatch_apply_unet.sbatch`.
Further scripts might be connected to this script by using the same date and suffix format.

## Archiving metadata
The job information of an sbatch script is monitored and can be looked up using `reportseff -u <user_id>` for around one week after the initial submission.
This information, among other pieces of information from the sbatch script, are extracted using `scripts/write_metadata.py`.
The keys of `metadata.json` are lower case with underscores, e.g. `job_name` and `reportseff`.
An archive which was written before this rule still has keys such as `job-name` and `Reportseff`.
Rename them once, then `scripts/summary.py` reads the archive again:

```bash
python scripts/migrate_metadata_keys.py --dry-run <archive_dir>   # list the files which change
python scripts/migrate_metadata_keys.py <archive_dir>
```

## Projects
A project is a set of templates, pipelines, settings and deployment logic which share one name.
The current project is `cochlea-net`.
The folder of a template, or the first part of a pipeline name, selects the project:

```bash
python scripts/deploy.py -i templates/cochlea-net/apply_SGN.template -j <params.json>
python scripts/deploy.py -p cochlea-net/sgn -j <params.json>
```

To add a project `<project>`, add these four parts:

- `templates/<project>/` with one `.template` file per step.
- `pipelines/<project>/` with the pipeline definitions, if the project chains steps.
- `project_settings/<project>.blueprint.json` with the keys `academic_id`, `hpc_user` and the keys of the project. Add `repositories` if the jobs use other git repositories.
- `slurm_workflows/projects/<project>/deployment.py` with the logic which is specific to the project, if the project needs it.
  A hyphen of the project name becomes an underscore in the package name, e.g. `slurm_workflows/projects/cochlea_net/`.

`slurm_workflows/deploy.py` calls four functions of the deployment module:

| Function | Purpose |
|---|---|
| `add_arguments(parser)` | Add the command line options of the project. |
| `select_steps(definition, settings, args)` | Return the steps of a pipeline to submit. |
| `prepare_step(settings, placeholder_values, template_file, args, definition)` | Add the placeholder values of one job and return them together with the name of the sbatch script. |
| `check_steps(template_files, sbatch_files)` | Return warnings, see [Warnings](#warnings). |

A deployment module defines only the functions which differ from `slurm_workflows/projects/default.py`.
The default deployment supplies the other functions.
A project without a deployment module uses `slurm_workflows/projects/default.py`.
It fills the placeholders only from the settings file and the parameter file, and names the sbatch script after the template.
A placeholder without a value raises an error.
If all placeholders are filled, a note tells that the default deployment was used.

The deploy step records the project as `project` in the `metadata.json` of every job.

## Template contract

A template is an sbatch script with placeholders in the format `<name>`.
The deploy step takes the value of a placeholder from these sources:

1. A scalar key of the settings file, e.g. `<data_dir>`.
2. An entry of `repositories` in the settings file, as `<repo:name>`. The value is the path of the repository, e.g. `<repo:cochlea-net>`.
   `<repo:slurm-workflows>` is always the path of this repository.
3. A key of the parameter file. A parameter overrides the setting of the same key.
4. A value which the deployment module of the project adds, e.g. `<prediction_dir>` of `cochlea-net`.

A placeholder without a value raises an error, so that no incomplete sbatch script is written.
The option `--allow-missing` changes the error into a warning.
A placeholder name holds no space, `<` or `>`, so shell code such as `cmd < in > out` is no placeholder.
Write `\<name>` for the literal text `<name>`.

The deploy step writes the sbatch script `<date>_sbatch_<name>.sbatch` into the current directory.
`<name>` is the name of the template, or the name which the deployment module gives.
With `--submit`, the deploy step submits the sbatch script and moves it into the archive folder `<archive_dir>/<date>_<name>/`.
The archive folder also gets `metadata.json` and `environment.yml`, the export of the micromamba environment which the job activates.

### Variables which the deploy step checks

The deploy step reads these shell variables from the sbatch script before the submission:

| Variable | Check |
|---|---|
| `INPUT` | The input of the job exists. The deploy step checks only the first step of a pipeline. |
| `EXTERNAL_INPUT` | An input which no step of the pipeline produces exists. The deploy step checks every step. |
| `EXISTING_OUTPUT` | A result which the job builds again does not exist yet. |
| `OUTPUT_FOLDER`, `INPUT_KEY`, `S3_REMOTE` | The deployment module can check them. `cochlea-net` checks for an earlier prediction and for the rclone remote. |

Write a variable as `VAR=value` or `export VAR=value`, as in the shell.
A quoted value can contain a space, and one line can hold several assignments.
The deploy step does not expand a variable in a value, and does not check a value which is not an absolute path.
A template without these variables gets no check before the submission.

### Warnings

Every `check_*` function of `slurm_workflows/inputs.py` and of a deployment module returns a list of warning messages.
An empty list means that the check found no problem.
The deploy step prints every warning.
A warning stops `--submit`, unless `--force` is given.

### The guard of a step

The steps of a pipeline form a linear chain.
A step starts only after the previous step completed successfully (`--dependency=afterok`).
The steps share data only through the file system.
Two steps use the same path if their templates build it from the same placeholders, e.g. `<data_dir>/predictions/<cochlea>/<prediction_dir>`.

Before the submission, the input of a later step does not exist yet, so the deploy step cannot check it.
Thus each template checks its own input when the job runs.
A missing input ends the job with an error, and Slurm cancels the rest of the chain:

```bash
# Verify the input of the job. A missing input stops the rest of the pipeline.
for path in "$INPUT" "$OUTPUT_FOLDER"/mask.zarr ; do
	if [ ! -e "$path" ] ; then
		echo "Missing input of the job: $path"
		exit 1
	fi
done
```

## Template concept
The templates of `cochlea-net` are located in `templates/cochlea-net`.
They include the application of trained neural networks for the segmentation of IHCs and SGNs, the detection of synapses, and the conversion of the image data to the MoBIE data format and its transfer to the S3 bucket.
The MoBIE templates cover the image data of a cochlea, a segmentation of SGNs or IHCs, and the ribbon synapse detections.
The templates contain no absolute path.
A placeholder which is specific to a cluster account is filled from the settings file of the project, a placeholder which is specific to a job is filled from the parameter file.

The input data of the job is checked before the job is submitted.
A missing input gives a warning, and the option `--submit` stops before the submission.
Use the option `--force` to submit the job for data which does not exist yet.

For `cochlea-net`, the deploy step also decides which file the job reads.
The initial processing writes one n5 which holds every stain, and that n5 is deleted once the cochlea is processed.
Some cochleae have one n5 per stain instead, e.g. `GLR_301L_PV_fused.n5`.
A later job reads the OME-Zarr which was transferred back from the S3 bucket instead.
An n5 wins if it still holds the stain, and the OME-Zarr `<data_dir>/<cochlea>/<stain>.ome.zarr` is the fallback.
The input key follows the file, so a rerun from the S3 bucket needs no extra parameter.
If no file holds the stain, the deploy step of a pipeline checks whether the `dataset.json` of the cochlea in the S3 bucket lists the stain as a source.
The transfer lists a source only after its data `<cochlea>/images/ome-zarr/<stain>.ome.zarr` is complete.
If the bucket lists the stain, the deploy step prints the command with the option `--download`.
That option adds a first step, which copies the OME-Zarr from the bucket to `<data_dir>/<cochlea>/<stain>.ome.zarr`, and the other steps read it.
An interrupted download stays in `<stain>.ome.zarr.part`, so no step reads a partial copy, and a rerun completes it.
The check needs `rclone`, so run `module load rclone` first on the cluster.

The stain of a job is a parameter.
Use `stain_SGN`, `stain_IHC` or `stain_synapses` to process a target with a different stain, for example `Homer1` instead of `CTBP2` for synapses.
The prediction folder carries the stain if it deviates from the default, so a new stain never overwrites an older prediction.
The stain does not change the model, which is selected by the model version alone.

## Pipelines

Several processing steps can be submitted as a chain of Slurm jobs:

```bash
python scripts/deploy.py -p cochlea-net/mobie -j <params.json> --submit      # add image data to MoBIE, transfer to the S3 bucket
python scripts/deploy.py -p cochlea-net/sgn -j <params.json> --submit        # mask, apply, segment SGN
python scripts/deploy.py -p cochlea-net/ihc -j <params.json> --submit        # mask, apply, segment IHC
python scripts/deploy.py -p cochlea-net/synapses -j <params.json> --submit   # mask, apply, detect synapses
```

The whole chain is submitted at once.
Each step is a separate job with its own resources, so the steps can differ in partition, cores,
memory and GPU.
A step starts only after its predecessor completed successfully, because it is submitted with
`--dependency=afterok`.
Each step also verifies its own input when it runs.
A missing input makes the job fail, so the remaining steps of the chain are cancelled.

A pipeline is defined by a JSON file in `pipelines/<project>`, which lists the templates in order under the key `steps`.
A project can add its own keys to the definition, such as `mobie_steps` and `group` of `cochlea-net`.
Use the option `--start-at` to resume a chain after a failed step.

## The result in MoBIE and in the S3 bucket

The `sgn`, `ihc` and `synapses` pipelines of `cochlea-net` end with two more steps, which add the result to the MoBIE
project and transfer the new source to the S3 bucket.
The `sgn` and the `ihc` pipeline add the segmentation, the `synapses` pipeline adds the detections
which were matched to the IHCs.
Only the new source is transferred, so the step is short.

These steps run only if the settings file names a `mobie_project`.
Remove that key, or set it to an empty string, to end the chain with the last processing step.
Use the option `--no-mobie` to leave the steps out for a single run, without touching the settings
file, because the processing of a cochlea does not depend on the export.
The skipped steps are named when the pipeline is deployed.

The two segmentation templates serve both groups.
The pipeline declares its group with the key `group`, which also enters the file name of the
generated script, so an `sgn` run and an `ihc` run of one cochlea keep separate archive folders.
Use the option `--group` to deploy such a template on its own with `-i`.

A MoBIE source is built from scratch, so a second run drops every entry which was added to its table
afterwards, such as a tonotopic mapping.
The deploy step refuses to rebuild an existing table, unless `--force` is given.

A step can also need an input which no step of the chain produces.
The `synapses` pipeline needs an IHC segmentation, which the `ihc` pipeline produced earlier: the prediction runs only on the region around the IHCs, and the detections are matched to them.
The MoBIE step of the same pipeline needs the cochlea to be a dataset of the MoBIE project already, because a detection carries no image data which could create it.
Such a prerequisite is checked before the submission, for every step of the chain.

## Cleanup of the intermediate data

Each pipeline of `cochlea-net` ends with a CPU job, which checks the result of the pipeline and then removes the intermediate data.
The intermediate data takes up a lot of space and a lot of inodes.

- `sgn`, `ihc`: `mask.zarr`, `predictions.zarr` and `seeds.zarr`, and the local MoBIE copy of the segmentation.
- `synapses`: `mask.zarr` and `predictions.zarr`.
- `mobie`: the local MoBIE copy of every stain.

`segmentation.zarr` and the tables stay.
A local MoBIE copy is removed only if `rclone check` finds every file of it in the S3 bucket.
The cleanup runs after the MoBIE steps, so a failed step cancels it and the data stays for a rerun.

Use the option `--no-cleanup` to keep the intermediate data for a single run.
A new watershed or a new detection threshold needs `predictions.zarr`, which only a new apply step rebuilds.
Use `--start-at` with the cleanup step, e.g. `--start-at cleanup_SGN`, to clean up a cochlea from an earlier run.

Use the option `--cleanup-input` to also remove the image data of the stain, for example the OME-Zarr of `--download`.
It adds the step `cleanup_input` at the end of the chain, and `--no-cleanup` does not drop it.
The step removes the image data only if the `dataset.json` of the cochlea in the S3 bucket lists the stain as a source, so a later `--download` can restore it.
It removes an OME-Zarr completely.
From an n5, it removes only the setup folder of the stain, e.g. `setup0`, and it removes the n5 together with its last setup.
Do not run another chain on the same stain while a chain with `--cleanup-input` runs, because the other chain can lose its input.

## Mockup example

The directory `mockup_example` shows every file of one job, from the template to the archived metadata.
The job is a mockup: it does not run, and its paths and names are placeholders.
The sbatch script was adapted from the [GWDG](https://docs.hpc.gwdg.de/how_to_use/slurm/gpu_usage/index.html "GPU Usage - Documentation for HPC").

| File | Role |
|---|---|
| `project_settings/mockup.json` | Settings of the project: mail address, environment, HPC user, git repositories. |
| `templates/mockup/mockup.template` | Template of the job, with the placeholders `<job_name>`, `<user_address>`, `<environment>`, `<module>` and `<dataset>`. |
| `templates/mockup/evaluate.template` | Template of the second step of the mockup pipeline. |
| `pipelines/mockup/train_evaluate.json` | Pipeline which chains `mockup` and `evaluate`. |
| `params.json` | Parameter file of the job: job name, debug module, dataset name. |
| `YYYY-MM-DD_sbatch_mockup.sbatch` | The sbatch script which is generated from the template. |
| `YYYY-MM-DD_log_mockup.txt` | The log file with the fictional JobID 1234567. |
| `repository_list.txt` | The git repositories whose hash is archived. Each line holds a name and a path, separated by a tab. |
| `YYYY-MM-DD_mockup/` | The archive folder with the sbatch script, the log file and `metadata.json`. |

The mockup files are kept in `mockup_example`, and not in the top-level directories `templates` and `project_settings`.
So the mockup is not listed as a project, and its settings file can be tracked by git.

A pipeline reads its templates from the folder `templates/<project>/` beside its folder `pipelines/<project>/`.
So the mockup pipeline runs from its own folder.
The dry run writes one sbatch script per step into the working directory and submits nothing:

```bash
python scripts/deploy.py -p mockup_example/pipelines/mockup/train_evaluate.json \
    -j mockup_example/params.json -s mockup_example/project_settings/mockup.json
```

### From the template to the archive

1. The settings file and the parameter file fill the placeholders of the template.
   A parameter overrides a setting with the same key.
   The mockup has no deployment module, so `scripts/deploy.py` uses the default deployment:
   ```bash
   python scripts/deploy.py -i mockup_example/templates/mockup/mockup.template \
       -j mockup_example/params.json -s mockup_example/project_settings/mockup.json
   ```
   Without `--submit`, the command writes `<date>_sbatch_mockup.sbatch` with the current date into the working directory and submits nothing.
2. `scripts/submit_job.py` submits the sbatch script and writes the JobID into the log file.
   If the script is submitted again, the new JobID is appended in a new line.
   If the log file was moved into the archive before, the archive merges the new JobID into the archived log file.
   With `-a`, it also runs the next two steps and writes `environment.yml`, the export of the micromamba environment of the job.
3. `scripts/archive_job.py` copies the script and the log file into the archive folder:
   ```bash
   python scripts/archive_job.py -i mockup_example/ -a mockup_example/ YYYY-MM-DD mockup
   ```
4. `scripts/write_metadata.py` creates `metadata.json` from the `#SBATCH` parameters, the JobID, the efficiency report of `reportseff`, the HPC user of the settings file, and the git hash of each repository:
   ```bash
   python scripts/write_metadata.py -r mockup_example/repository_list.txt -s mockup_example/project_settings/mockup.json \
       mockup_example/YYYY-MM-DD_mockup/ -o mockup_example/YYYY-MM-DD_mockup/metadata.json
   ```
   `reportseff` is available only on the cluster.
   `scripts/deploy.py` passes the settings file on by itself and adds the key `project`.

### Create your own project

1. Copy the mockup template to `templates/<project>/<step>.template`.
   Replace every value which is specific to a cluster account or to a job with a placeholder `<key>`.
2. Copy the mockup settings to `project_settings/<project>.blueprint.json` and commit it.
   Keep the keys `academic_id` and `hpc_user`, and add a key for every account-specific placeholder.
   List the other git repositories of the project under `repositories`.
   Copy the blueprint to `project_settings/<project>.json` and fill in the values of your account.
3. If the settings and the parameters cannot fill every placeholder, add `slurm_workflows/projects/<project>/deployment.py` with the functions of the table in [Projects](#projects) which you need.
4. To chain several steps, add `pipelines/<project>/<name>.json` with the ordered template names under the key `steps`:
   ```json
   {
       "description": "Train and evaluate the network.",
       "steps": ["train", "evaluate"]
   }
   ```
   The two steps share the run folder through the same placeholders.
   The second step checks its input with the guard of the [Template contract](#the-guard-of-a-step).
   These are the bodies of the two templates, without the `#SBATCH` header:
   ```bash
   # templates/<project>/train.template
   export INPUT=<data_dir>/<dataset>
   export OUTPUT_FOLDER=<data_dir>/runs/<run_name>
   python -u <repo:my-tool>/train.py --input "$INPUT" --output "$OUTPUT_FOLDER"

   # templates/<project>/evaluate.template
   export INPUT=<data_dir>/runs/<run_name>/model.pt
   for path in "$INPUT" ; do
   	if [ ! -e "$path" ] ; then
   		echo "Missing input of the job: $path"
   		exit 1
   	fi
   done
   python -u <repo:my-tool>/evaluate.py --model "$INPUT"
   ```
5. Write the parameters of a job into a parameter file such as `params.json`, and submit:
   ```bash
   python scripts/deploy.py -i templates/<project>/<step>.template -j params.json --submit
   python scripts/deploy.py -p <project>/<name> -j params.json --submit
   ```

## Tests

The directory `test` contains unit tests for the transfer of the job files, the log files, the filling of the placeholders, and the check of missing files.
Stub commands replace `sbatch` and `reportseff`, so the tests run on every machine and submit nothing.
The tests use only the standard library, so they also run with the Python 3.6 of the login node.
Run them from the root of the repository:

```bash
python -m unittest discover -s test
```

GitHub Actions runs flake8 and the tests with Python 3.6 and 3.14 for every pull request and every push to `main`.
