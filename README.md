# Scripts to improve reproducibility of HPC

This repository is a collection of scripts connected to the usage of HPC, specifically at the GWDG (Gesellschaft für wissenschaftliche Datenverarbeitung Goettingen).

The goal is to make the usage of HPC resources more reproducible by archiving metadata.

Feedback is appreciated.

## Setup

Each project has one settings file, `project_settings/<project>.json`, with the paths and names which are specific to a cluster account.
This file is not tracked by git, so that no absolute path enters the repository.
Copy the blueprint file of the project once and adapt the values to your account, e.g. for `cochlea-net`:

```
cp project_settings/cochlea-net.blueprint.json project_settings/cochlea-net.json
```

The settings file of `cochlea-net` contains the mail address and the Slurm account for the sbatch header, the identifiers of the HPC user, the directories of the data and of the job archive, the names of the micromamba environments, the local paths of the git repositories, and the paths of the trained models.
The keys `academic_id` and `hpc_user` name the person who runs a job, so that the archive records who spent the computing time of the HPC project. Both may stay blank.
Every settings file needs the keys `repositories`, `academic_id` and `hpc_user`. A project can require more keys.
`scripts/deploy_process.py` reads the settings file of the project and fills the values into the templates.
Use the option `-s` to select a different settings file.

## Repository structure

The directory `scripts` contains the entry points which are run from the command line.
The directory `utils` contains the utility functions which the scripts share, and one deployment module per project.
The directory `templates` contains one subfolder per project with one template per processing step.
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

## Projects
A project is a set of templates, pipelines, settings and deployment logic which share one name.
The current project is `cochlea-net`.
The folder of a template, or the first part of a pipeline name, selects the project:

```bash
python scripts/deploy_process.py -i templates/cochlea-net/apply_SGN.template -j <params.json>
python scripts/deploy_process.py -p cochlea-net/sgn -j <params.json>
```

To add a project `<project>`, add these four parts:

- `templates/<project>/` with one `.template` file per step.
- `pipelines/<project>/` with the pipeline definitions, if the project chains steps.
- `project_settings/<project>.blueprint.json` with the keys `repositories`, `academic_id`, `hpc_user` and the keys of the project.
- `utils/<project>_deployment.py` with the logic which is specific to the project.

`scripts/deploy_process.py` calls four functions of the deployment module:

| Function | Purpose |
|---|---|
| `add_arguments(parser)` | Add the command line options of the project. |
| `select_steps(definition, settings, args)` | Return the steps of a pipeline to submit. |
| `prepare_step(settings, replacements, template_file, args, definition)` | Add the placeholder values of one job and return them together with the name of the sbatch script. |
| `check_steps(template_files, sbatch_files)` | Return warnings which stop the deployment unless `--force` is given. |

The deploy step records the project as `Project` in the `metadata.json` of every job.

## Template concept
The templates of `cochlea-net` are located in `templates/cochlea-net`.
They include the application of trained neural networks for the segmentation of IHCs and SGNs, the detection of synapses, and the transformation of data into MoBIE format and its transfer to the S3 bucket.
The MoBIE templates cover the image data of a cochlea, a segmentation of SGNs or IHCs, and the ribbon synapse detections.
Using `scripts/deploy_process.py` a JSON dictionary with parameters can be given as an input to fill blanks in the templates and use the resulting scripts for job submission.
The templates contain no absolute path.
A blank which is specific to a cluster account is filled from the settings file of the project, a blank which is specific to a job is filled from the parameter dictionary.
A blank without a value raises an error, so that no incomplete sbatch script is written.

The input data of the job is checked before the job is deployed.
A missing input gives a warning, and the option `--deploy` stops before the submission.
Use the option `--force` to submit the job for data which does not exist yet.

For `cochlea-net`, the deploy step also decides which file the job reads.
The initial processing writes one n5 which holds every stain, and that n5 is deleted once the cochlea is processed.
A later job reads the OME-Zarr which was transferred back from the S3 bucket instead.
The n5 wins if it still exists, and the OME-Zarr `<data_dir>/<cochlea>/<stain>.ome.zarr` is the fallback.
The input key follows the file, so a rerun from the S3 bucket needs no extra parameter.

The stain of a job is a parameter.
Use `stain_SGN`, `stain_IHC` or `stain_synapses` to process a target with a different stain, for example `Homer1` instead of `CTBP2` for synapses.
The prediction folder carries the stain if it deviates from the default, so a new stain never overwrites an older prediction.
The stain does not change the model, which is selected by the model version alone.

## Pipelines

Several processing steps can be submitted as a chain of Slurm jobs:

```bash
python scripts/deploy_process.py -p cochlea-net/mobie -j <params.json> --deploy      # add image data to MoBIE, transfer to S3
python scripts/deploy_process.py -p cochlea-net/sgn -j <params.json> --deploy        # mean_std, apply, segment SGN
python scripts/deploy_process.py -p cochlea-net/ihc -j <params.json> --deploy        # mean_std, apply, segment IHC
python scripts/deploy_process.py -p cochlea-net/synapses -j <params.json> --deploy   # mean_std, apply, detect synapses
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

## Mockup example

The directory `mockup_example` shows every file of one job, from the template to the archived metadata.
The job is a mockup: it does not run, and its paths and names are placeholders.
The sbatch script was adapted from the [GWDG](https://docs.hpc.gwdg.de/how_to_use/slurm/gpu_usage/index.html "GPU Usage - Documentation for HPC").

| File | Role |
|---|---|
| `project_settings/mockup.json` | Settings of the project: mail address, environment, HPC user, git repositories. |
| `templates/mockup.template` | Template of the job, with the placeholders `<job_name>`, `<user_address>`, `<environment>`, `<module>` and `<dataset>`. |
| `dataset.json` | Parameters of the job: job name, debug module, dataset name. |
| `YYYY-MM-DD_sbatch_mockup.sbatch` | The sbatch script which is generated from the template. |
| `YYYY-MM-DD_log_mockup.txt` | The log file with the fictional JobID 1234567. |
| `repository_list.txt` | The git repositories whose hash is archived, one per line as `<Repository-name>	<Path-to-repository>`. |
| `YYYY-MM-DD_mockup/` | The archive folder with the sbatch script, the log file and `metadata.json`. |

The mockup files are kept in `mockup_example`, and not in the top-level directories `templates` and `project_settings`.
Every folder in `templates` is a project, and `scripts/deploy_process.py` requires a deployment module for it.
The top-level settings files are also not tracked by git.

### From the template to the archive

1. The settings file and the parameter file fill the placeholders of the template.
   A parameter overrides a setting with the same key.
   For a real project, `scripts/deploy_process.py` does this step.
   The mockup has no deployment module, so fill its template directly:
   ```bash
   python -c '
   import json, sys
   sys.path.insert(0, ".")
   from utils.settings import load_settings, settings_to_replacements
   from utils.templates import replace_substrings_in_file
   replacements = settings_to_replacements(load_settings("mockup_example/project_settings/mockup.json"))
   replacements.update(json.load(open("mockup_example/dataset.json")))
   replace_substrings_in_file("mockup_example/templates/mockup.template",
                              "mockup_example/YYYY-MM-DD_sbatch_mockup.sbatch", replacements)
   '
   ```
2. `scripts/01_run_sbatch.sh` submits the sbatch script and writes the JobID into the log file.
   If the script is submitted again, the new JobID is appended in a new line.
3. `scripts/02_archive_scripts.sh` copies the script and the log file into the archive folder:
   ```bash
   bash scripts/02_archive_scripts.sh -i mockup_example/ -a mockup_example/ YYYY-MM-DD mockup
   ```
4. `scripts/write_metadata.py` creates `metadata.json` from the `#SBATCH` parameters, the JobID, the efficiency report of `reportseff`, the HPC user of the settings file, and the git hash of each repository:
   ```bash
   python scripts/write_metadata.py -r mockup_example/repository_list.txt -s mockup_example/project_settings/mockup.json \
       mockup_example/YYYY-MM-DD_mockup/ -o mockup_example/YYYY-MM-DD_mockup/metadata.json
   ```
   `reportseff` is available only on the cluster.
   `scripts/deploy_process.py` passes the settings file on by itself and adds the key `Project`.

### Create your own project

1. Copy the mockup template to `templates/<project>/<step>.template`.
   Replace every value which is specific to a cluster account or to a job with a placeholder `<key>`.
2. Copy the mockup settings to `project_settings/<project>.blueprint.json` and commit it.
   Keep the keys `repositories`, `academic_id` and `hpc_user`, and add a key for every account-specific placeholder.
   Copy the blueprint to `project_settings/<project>.json` and fill in the values of your account.
3. Add `utils/<project>_deployment.py` with the four functions of the table in [Projects](#projects).
4. To chain several steps, add `pipelines/<project>/<name>.json` with the ordered template names under the key `steps`:
   ```json
   {
       "description": "Train and evaluate the network.",
       "steps": ["train", "evaluate"]
   }
   ```
5. Write the parameters of a job into a JSON file such as `dataset.json`, and deploy:
   ```bash
   python scripts/deploy_process.py -i templates/<project>/<step>.template -j dataset.json --deploy
   python scripts/deploy_process.py -p <project>/<name> -j dataset.json --deploy
   ```
