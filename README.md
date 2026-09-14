# Scripts to improve reproducibility of HPC

This repository is a collection of scripts connected to the usage of HPC, specifically at the GWDG (Gesellschaft für wissenschaftliche Datenverarbeitung Goettingen).

The goal is to make the usage of HPC resources more reproducible by archiving metadata.

Feedback is appreciated.

## Setup

The paths and names which are specific to a cluster account are collected in `utils/settings.json`.
This file is not tracked by git, so that no absolute path enters the repository.
Copy the example file once and adapt the values to your account:

```
cp utils/settings.example.json utils/settings.json
```

The file contains the mail address and the Slurm account for the sbatch header, the directories of the data and of the job archive, the names of the micromamba environments, the local paths of the git repositories, and the paths of the trained models.
`scripts/deploy_process.py` reads the file and fills the values into the templates.
Use the option `-s` to select a different settings file.

## Repository structure

The directory `scripts` contains the entry points which are run from the command line.
The directory `utils` contains the utility functions which the scripts share, together with the
settings file.
The directory `templates` contains one template per processing step, and the directory `pipelines`
contains the definitions which chain those steps.

## Current concept

Jobs are submitted to the cluster using a JobID. The JobID has the benefit of being inherently unique, so it could be used as the sole identifier of a script and related data.
However, the number itself is not very informative and a specific task might be started multiple times before achieving a satisfying result.
The current concept involves the usage of a single sbatch script for a specific purpose linked to a date.
It should contain the date and a suffix in the file format `<date>_sbatch_<suffix>` with the date in format `yyyy-mm-dd`, e.g. `2025-03-19_sbatch_apply_unet.sbatch`.
Further scripts might be connected to this script by using the same date and suffix format.

## Archiving metadata
The job information of an sbatch script is monitored and can be looked up using `reportseff -u <user_id>` for around one week after the initial submission.
This information, among other pieces of information from the sbatch script, are extracted using `scripts/write_metadata.py`.

## Template concept
Multiple templates for common sbatch scripts are located in `templates`.
This includes the application of trained neural networks for the segmentation of IHCs and SGNs, the detection of synapses, and the transformation of data into MoBIE format and its transfer to the S3 bucket.
The MoBIE templates cover the image data of a cochlea, a segmentation of SGNs or IHCs, and the ribbon synapse detections.
Using `scripts/deploy_process.py` a JSON dictionary with parameters can be given as an input to fill blanks in the templates and use the resulting scripts for job submission.
The templates contain no absolute path.
A blank which is specific to a cluster account is filled from `utils/settings.json`, a blank which is specific to a job is filled from the parameter dictionary.
A blank without a value raises an error, so that no incomplete sbatch script is written.

The input data of the job is checked before the job is deployed.
A missing input gives a warning, and the option `--deploy` stops before the submission.
Use the option `--force` to submit the job for data which does not exist yet.

The deploy step also decides which file the job reads.
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
python scripts/deploy_process.py -p mobie -j <params.json> --deploy      # add image data to MoBIE, transfer to S3
python scripts/deploy_process.py -p sgn -j <params.json> --deploy        # mean_std, apply, segment SGN
python scripts/deploy_process.py -p ihc -j <params.json> --deploy        # mean_std, apply, segment IHC
python scripts/deploy_process.py -p synapses -j <params.json> --deploy   # detect synapses, match to IHCs
```

The whole chain is submitted at once.
Each step is a separate job with its own resources, so the steps can differ in partition, cores,
memory and GPU.
A step starts only after its predecessor completed successfully, because it is submitted with
`--dependency=afterok`.
Each step also verifies its own input when it runs.
A missing input makes the job fail, so the remaining steps of the chain are cancelled.

A pipeline is defined by a JSON file in `pipelines`, which lists the templates in order.
Use the option `--start-at` to resume a chain after a failed step.

## The result in MoBIE and in the S3 bucket

The `sgn`, `ihc` and `synapses` pipelines end with two more steps, which add the result to the MoBIE
project and transfer the new source to the S3 bucket.
The `sgn` and the `ihc` pipeline add the segmentation, the `synapses` pipeline adds the detections
before and after the matching to the IHCs.
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
The second step of the `synapses` pipeline matches the detections to an IHC segmentation, which the `ihc` pipeline produced earlier.
The MoBIE step of the same pipeline needs the cochlea to be a dataset of the MoBIE project already, because a detection carries no image data which could create it.
Such a prerequisite is checked before the submission, for every step of the chain.

## Example

An example for a use case showing an sbatch script, a log file containing the JobID, and the corresponding archived metadata are located in the `example` directory.
The example script `example/YYYY-MM-DD_sbatch_example.sbatch` has been adapted from the [GWDG](https://docs.hpc.gwdg.de/how_to_use/slurm/gpu_usage/index.html "GPU Usage - Documentation for HPC").
The text file `example/YYYY-MM-DD_log_example.txt` contains the fictional JobID 1234567, with which the sbatch job has been submitted.
If the script would be started multiple times, the latest jobid would be appended in a new line, if `scripts/01_run_sbatch.sh` is used for submission.

The script and the log file can be archived using the command:
```
bash scripts/02_archive_scripts.sh -i example/ -a example/ YYYY-MM-DD example
```

A file containing information about git repositories can be given as an optional argument to archive the current git hash of the repository.
An example for such a file is `example/repository_list.txt`.
The information about the git repositories should be presented in the format `<Repository-name>	<Path-to-repository>`, where each line corresponds to a new git repository.

The metadata for the script is created with:
```
python scripts/write_metadata.py -r example/repository_list.txt example/YYYY-MM-DD_example/ -o example/YYYY-MM-DD_example/metadata.json
```

