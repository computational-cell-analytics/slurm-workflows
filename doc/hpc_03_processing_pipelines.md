# Run processing pipelines

The project `cochlea-net` of [slurm-workflows](https://github.com/computational-cell-analytics/slurm-workflows) has four pipelines:

| Pipeline | Group | Steps | MoBIE steps | Cleanup steps |
|---|---|---|---|---|
| `cochlea-net/mobie` | - | `mobie_add_image` -> `mobie_transfer_dataset` | - | `mobie_cleanup_dataset` |
| `cochlea-net/sgn` | `SGN` | `mask_SGN` -> `apply_SGN` -> `segment_SGN` | `mobie_add_segmentation` -> `mobie_transfer_segmentation` | `cleanup_SGN`, `mobie_cleanup_segmentation` |
| `cochlea-net/ihc` | `IHC` | `mask_IHC` -> `apply_IHC` -> `segment_IHC` | `mobie_add_segmentation` -> `mobie_transfer_segmentation` | `cleanup_IHC`, `mobie_cleanup_segmentation` |
| `cochlea-net/synapses` | `synapses` | `mask_synapses` -> `apply_synapses` -> `detect_synapses` | `mobie_add_detection` -> `mobie_transfer_detection` | `cleanup_synapses` |

- `mobie` converts the image data of a cochlea to the MoBIE data format and transfers it to the S3 bucket.
- `sgn` and `ihc` segment the spiral ganglion neurons (SGN) or the inner hair cells (IHC) with a U-Net.
- `synapses` detects the ribbon synapses and matches them to an existing IHC segmentation.

This page assumes that you did the steps of [Getting started](hpc_01_getting_started.md): the
micromamba environment, the clone of slurm-workflows, and the settings file.

## The settings file

Each user keeps the values of their account in `project_settings/cochlea-net.json` of their clone.
Copy the blueprint [`project_settings/cochlea-net.blueprint.json`](../project_settings/cochlea-net.blueprint.json)
once and adapt the values:

```bash
cp project_settings/cochlea-net.blueprint.json project_settings/cochlea-net.json
```

| Key | Purpose |
|---|---|
| `user_address` | Your mail address. Slurm sends a mail to it if a job fails |
| `account_name` | The Slurm account of the HPC project, for `#SBATCH -A` |
| `academic_id`, `hpc_user` | Your AcademicID and your HPC User, recorded in the archive. Both may be empty |
| `cochlea-net_environment` | The micromamba environment of the cochlea-net jobs |
| `mobie_environment` | The micromamba environment of the MoBIE jobs. It can be the same environment |
| `archive_dir` | The archive directory, see [The archive](archive.md) |
| `data_dir` | The root of the cochlea data, see [The data layout](#the-data-layout) |
| `mobie_project` | The root of your local MoBIE project. Without this key, the pipelines skip the MoBIE steps |
| `s3_remote` | The rclone remote and bucket, in the form `remote:bucket` |
| `service_endpoint` | The URL of the S3 service |
| `repositories` | The path of your cochlea-net clone, under the name `cochlea-net`. The archive records its git hash |
| `models` | The paths of the trained models, see below |

`models` holds one map per model group: `SGN`, `IHC` and `Synapses`.
Each map gives the path of a model for a version key, e.g. `"SGN_v3": "/path/to/.../v3_cochlea_distance_unet_SGN_supervised_2026-07-10"`.
The parameter `sgn_version` selects a key of `models.SGN`, `ihc_version` a key of `models.IHC`, and
`synapse_version` a key of `models.Synapses`.
An unknown key stops the deploy step with the list of the available keys.

> [!WARNING]
> Git does not track `project_settings/cochlea-net.json`. New keys and model versions arrive only in the blueprint file.
> Compare the two files after every pull. An outdated settings file stops a job with a missing key or an unresolved placeholder.

## The data layout

The image data of a cochlea must be in the cochlea folder `<data_dir>/<cochlea>/`.
`<cochlea>` is the cochlea name of the parameter file, e.g. `M_AMD_000137_L`.
The folder holds one of these files for each stain:

| File | Name | Origin |
|---|---|---|
| n5 with every stain | `MAMD_137L_PV_Vglut3_CTBP2_fused.n5` | The initial processing |
| n5 with one stain | `GLR_301L_PV_fused.n5` | A cochlea which was transferred stain by stain, e.g. of a gerbil |
| OME-Zarr | `PV.ome.zarr` | A copy from the S3 bucket, see [Image data from the S3 bucket](#image-data-from-the-s3-bucket) |

The deploy step selects the file before it writes the sbatch script.
An n5 wins if it still holds the stain, and the OME-Zarr is the fallback.
The input key follows the file, so a rerun from the S3 bucket needs no extra parameter.
The n5 with every stain counts only if `stains` lists the stain. The order of `stains` gives the
setup of each stain in this n5.

The results go to the prediction folder `<data_dir>/predictions/<cochlea>/<prediction_dir>/`, e.g.
`<data_dir>/predictions/M_AMD_000137_L/SGN_v3/`.
`segmentation.zarr` and the tables of a segmentation stay there.
The MoBIE steps write into `<mobie_project>/<cochlea>/`.

### The cochlea name

A cochlea name has the format `<species>_<initials>_<index>_<side>[_v<N>]`:

| Part | Example | Meaning |
|---|---|---|
| `<species>` | `M` | The species, e.g. `M` for mouse and `G` for gerbil |
| `<initials>` | `AMD` | The initials of the experimenter |
| `<index>` | `000137` | The animal index, with leading zeros |
| `<side>` | `L` | The cochlea side, `L` or `R` |
| `_v<N>` | `_v2` | Optional: the version of a second acquisition |

The name of the n5 joins species and initials, and removes the leading zeros of the index:
`M_AMD_000137_L` gives `MAMD_137L_<stains>_fused.n5`, and `M_AMD_000137_L_v2` gives
`MAMD_137L_<stains>_fused_v2.n5`.
The species also selects the resources of a step, see [Placeholders of the deployment](#placeholders-of-the-deployment).

## The parameter file

Each pipeline reads the settings file and one parameter file per cochlea.
Keep one parameter file per cochlea, named `<cochlea>.json`, in one folder of your choice.
Keep the file after the run, so that you can run the next pipeline of the same cochlea with it.
The archive records the values in each sbatch script anyway.

```json
{
    "cochlea": "M_AMD_000137_L",
    "stains": [
        "PV",
        "Vglut3",
        "CTBP2"
    ],
    "sgn_version": "SGN_v3",
    "sgn_masking_threshold": 150,
    "ihc_version": "IHC_v11",
    "ihc_masking_threshold": 200,
    "synapse_version": "synapse_v3",
    "stain_synapses": "CTBP2"
}
```

| Parameter | `mobie` | `sgn` | `ihc` | `synapses` | Purpose |
|---|---|---|---|---|---|
| `cochlea` | yes | yes | yes | yes | The cochlea name |
| `stains` | yes | see text | see text | see text | Every stain of the cochlea, in the order of the setups of the n5 with every stain |
| `sgn_version` | - | yes | - | - | A key of `models.SGN` |
| `sgn_masking_threshold` | - | yes | - | - | The absolute threshold of the mask step, `ABSOLUTE_THRESHOLD` |
| `ihc_version` | - | - | yes | yes | A key of `models.IHC`. The `synapses` pipeline reads this IHC segmentation |
| `ihc_masking_threshold` | - | - | yes | - | The absolute threshold of the mask step, `ABSOLUTE_THRESHOLD` |
| `synapse_version` | - | - | - | optional | A key of `models.Synapses`. Default: `synapse_v3` |
| `stain_SGN`, `stain_IHC`, `stain_synapses` | - | optional | optional | optional | The stain of the group. Defaults: `PV`, `Vglut3`, `CTBP2` |
| `time`, `mem` | optional | optional | optional | optional | The resources of every step of the pipeline |
| `sgn_center_distance_threshold`, `sgn_boundary_distance_threshold`, `sgn_distance_smoothing` | - | optional | - | - | The watershed values of `segment_SGN`, see [The watershed values of the segmentation](#the-watershed-values-of-the-segmentation) |
| `ihc_center_distance_threshold`, `ihc_boundary_distance_threshold`, `ihc_distance_smoothing` | - | - | optional | - | The watershed values of `segment_IHC`, see [The watershed values of the segmentation](#the-watershed-values-of-the-segmentation) |

`sgn`, `ihc` and `synapses` need `stains` only to read the n5 with every stain.
A job which reads an n5 with one stain, or an OME-Zarr, needs no `stains`.

Use `stain_SGN`, `stain_IHC` or `stain_synapses` to process a target with a different stain, for
example `Homer1` instead of `CTBP2` for synapses.
A stain which differs from the default becomes part of the prediction folder, of the MoBIE source and
of the S3 table, e.g. `Homer1_synapse_v3_ihc_v11`.
So a new stain never overwrites an older prediction.
The stain does not change the model. The model version alone selects it.

> [!IMPORTANT]
> The `synapses` pipeline needs the IHC segmentation of the same cochlea. Run `ihc` first, and set `ihc_version` in the parameter file.

### The watershed values of the segmentation

The segment steps `segment_SGN` and `segment_IHC` split the prediction into objects with a watershed.
Three values control the watershed.
Each group has its own parameter names, so the parameter file of a cochlea can hold the values of
both groups:

| Value | `sgn` parameter | `ihc` parameter | SGN default | IHC default |
|---|---|---|---|---|
| Center distance threshold | `sgn_center_distance_threshold` | `ihc_center_distance_threshold` | `0.4` | `0.5` |
| Boundary distance threshold | `sgn_boundary_distance_threshold` | `ihc_boundary_distance_threshold` | `0.5` | `0.6` |
| Distance smoothing | `sgn_distance_smoothing` | `ihc_distance_smoothing` | `0` | `0.6` |

The deploy step takes each value from the first of these sources:

1. The parameter file, e.g. `"sgn_center_distance_threshold": 0.3`.
2. `best_best_params.json` in the model folder of the version, e.g. of `models.SGN.SGN_v3`.
3. The default of the group in the table above.

The dry run shows the values in the line `Placeholder values of <sbatch script>`.
A new watershed needs the `predictions.zarr` of the apply step, see
[Cleanup of the intermediate data](#cleanup-of-the-intermediate-data).

## Run a pipeline

Run the deploy step in one fixed folder, e.g. `slurm_outputs/` in your work directory:

- The dry run writes the sbatch scripts into this folder.
- `--submit` moves them into the archive directory.
- Slurm writes the output `slurm-<jobid>.out` of each job into this folder.
  `update_metadata.py --slurm-dir` reads it later, see [The archive](archive.md#the-workflow).

`deploy.py` runs with the Python 3.6 of the login node, so no environment needs to be active.
First run a dry run, which writes the sbatch scripts and submits nothing.
Then submit the pipeline as a chain of Slurm jobs:

```bash
cd /path/to/slurm_outputs
# dry run: write the sbatch scripts, submit nothing
python /path/to/slurm-workflows/scripts/deploy.py --pipeline cochlea-net/sgn --params /path/to/M_AMD_000137_L.json
# submit the pipeline as a chain of Slurm jobs
python /path/to/slurm-workflows/scripts/deploy.py --pipeline cochlea-net/sgn --params /path/to/M_AMD_000137_L.json --submit
```

Each step is a separate job with its own resources.
A step starts only after the previous step completed successfully.
Each step also checks its own input when it runs.
A missing input makes the job fail, and Slurm cancels the rest of the chain.

### The output of the dry run

The dry run prints these lines:

| Line | Meaning |
|---|---|
| `Pipeline <name>: <description>` | The pipeline |
| `Placeholder values of <sbatch script>: {...}` | Every value which the deploy step filled into the template |
| `The input of <sbatch script> is verified when the job runs.` | A later step of the chain. Its input does not exist yet |
| `Warning: ...` | A problem, e.g. a missing input, an existing prediction or mask, or a missing rclone remote |
| `Note: ...` | Information, e.g. the S3 bucket holds a missing stain, see [Image data from the S3 bucket](#image-data-from-the-s3-bucket) |
| `Nothing is submitted. Use --submit to submit the jobs.` | The end of the dry run |

A warning stops `--submit`.
Fix the cause and run the command again.
Use `--force` only if you know that the input will exist when the job starts, or that the job may
overwrite the existing result.

### Options

| Option | Purpose |
|---|---|
| `--submit` | Submit the jobs. Without it, the deploy step only writes and checks the sbatch scripts |
| `--force` | Submit the jobs although a check gave a warning |
| `--start-at <step>` | Start the pipeline at this step, e.g. to resume a chain after a failed step |
| `--no-mobie` | Leave the MoBIE steps out for this run |
| `--no-cleanup` | Leave the cleanup steps out for this run, to keep the intermediate data |
| `--download` | Add a first step, which copies the OME-Zarr of the stain from the S3 bucket |
| `--cleanup-input` | Add a last step, which removes the image data of the stain |
| `--group <group>` | The group of a template which serves more than one group, for a single job of `--input` |

To run one step on its own, give its template with `--input` instead of `--pipeline`:

```bash
python /path/to/slurm-workflows/scripts/deploy.py --input /path/to/slurm-workflows/templates/cochlea-net/apply_SGN.template \
    --params /path/to/M_AMD_000137_L.json
```

## After the submission

- `squeue --me` lists your jobs, see [HPC 101](hpc_101.md#advanced-scriptsfunctions).
  The later steps of a chain wait with the reason `(Dependency)`.
- Slurm sends a mail to `user_address` if a job fails.
- Read the output `slurm-<jobid>.out` of a failed step in the folder where the deploy step ran.
  A missing input shows the line `Missing input of the job: <path>`.
- A failed mask or apply step can leave a partial `mask.zarr` or `predictions.zarr` in the prediction
  folder. Remove it before you resume. Otherwise the deploy step gives a warning, which stops `--submit`.
- Fix the cause, then resume the chain at the failed step:
  ```bash
  python /path/to/slurm-workflows/scripts/deploy.py --pipeline cochlea-net/sgn --params /path/to/M_AMD_000137_L.json \
      --start-at apply_SGN --submit
  ```
- Update the archive, see [The archive](archive.md#the-workflow).

## The result in MoBIE and in the S3 bucket

The `sgn`, `ihc` and `synapses` pipelines end with two more steps, which add the result to the MoBIE
project and transfer the new source to the S3 bucket.
The `sgn` and the `ihc` pipeline add the segmentation.
The `synapses` pipeline adds the detections which were matched to the IHCs.
Only the new source is transferred, so the step is short.

These steps run only if the settings file names a `mobie_project`.
Remove that key to end the chain with the last processing step.
Do not set it to an empty string: the `mobie` pipeline then writes into the root folder `/`.
Use the option `--no-mobie` to leave the steps out for a single run, without a change of the
settings file.
The processing of a cochlea does not depend on the export.
The deploy step names the skipped steps.

The two segmentation templates serve both groups.
The pipeline gives the group with the key `group`, which also enters the name of the sbatch script.
So an `sgn` run and an `ihc` run of one cochlea keep separate archive folders.
Use the option `--group` to deploy such a template on its own with `--input`.

A MoBIE source is built from scratch, so a second run drops every entry which was added to its table
afterwards, such as a tonotopic mapping.
The deploy step refuses to rebuild an existing table, unless `--force` is given.

A step can also need an input which no step of the chain produces.
The `synapses` pipeline needs an IHC segmentation, which the `ihc` pipeline produced earlier: the
prediction runs only on the region around the IHCs, and the detections are matched to them.
The steps read this segmentation from the S3 bucket, so the deploy step cannot check it.
A missing segmentation stops the first step when it runs.
The MoBIE step of the same pipeline needs the cochlea to be a dataset of the MoBIE project already,
because a detection carries no image data which could create it.
The deploy step checks this dataset before the submission.

## Cleanup of the intermediate data

Each pipeline ends with a CPU job, which checks the result of the pipeline and then removes the
intermediate data.
The intermediate data takes up a lot of space and a lot of inodes.

- `sgn`, `ihc`: `mask.zarr`, `predictions.zarr` and `seeds.zarr`, and the local MoBIE copy of the segmentation.
- `synapses`: `mask.zarr` and `predictions.zarr`.
- `mobie`: the local MoBIE copy of every stain.

`segmentation.zarr` and the tables stay.
A local MoBIE copy is removed only if `rclone check` finds every file of it in the S3 bucket.
The cleanup runs after the MoBIE steps, so a failed step cancels it and the data stays for a rerun.

Use the option `--no-cleanup` to keep the intermediate data for a single run.
A new watershed or a new detection threshold needs `predictions.zarr`, which only a new apply step
rebuilds.
Use `--start-at` with the cleanup step, e.g. `--start-at cleanup_SGN`, to clean up a cochlea of an
earlier run.

## Image data from the S3 bucket

The initial processing writes one n5 which holds every stain.
The n5 is deleted once the cochlea is processed.
A later job then reads the OME-Zarr which the `mobie` pipeline transferred to the S3 bucket.

If no file of the cochlea folder holds the stain, the dry run checks the S3 bucket.
If the `dataset.json` of the cochlea in the bucket lists the stain as a source, the dry run prints
the command with `--download`.
The transfer lists a source only after its data `<cochlea>/images/ome-zarr/<stain>.ome.zarr` is
complete.
The check needs `rclone`, so run `module load rclone` first.

`--download` adds a first step, which copies the OME-Zarr from the bucket to
`<data_dir>/<cochlea>/<stain>.ome.zarr`, and the other steps read it.
An interrupted download stays in `<stain>.ome.zarr.part`, so no step reads a partial copy.
A rerun completes it.
`--download` needs a group, so it does not work with the `mobie` pipeline.

`--cleanup-input` adds the step `cleanup_input` at the end of the chain, which removes the image data
of the stain, e.g. the OME-Zarr of `--download`.
`--no-cleanup` does not drop this step.
The step removes the image data only if the `dataset.json` of the cochlea in the S3 bucket lists the
stain as a source, so a later `--download` can restore it.

- From an OME-Zarr, it removes the whole folder.
- From an n5, it removes only the setup folder of the stain, e.g. `setup0`. It removes the n5
  together with its last setup.

> [!CAUTION]
> Do not run another chain on the same stain while a chain with `--cleanup-input` runs. The other chain can lose its input.

## Placeholders of the deployment

The cochlea-net deployment adds these placeholder values to the settings and the parameters:

| Placeholder | Value | A parameter overrides it |
|---|---|---|
| `cochlea_job_name` | `cochlea` with `-` instead of `_`, for `#SBATCH --job-name` | no |
| `stain_SGN`, `stain_IHC`, `stain_synapses` | `PV`, `Vglut3`, `CTBP2` | yes |
| `time`, `mem`, `instances` | The row of the template in `templates/cochlea-net/steps.json`, with the values of the species. `instances` is the number of tasks of an apply array | yes |
| `last_instance` | `instances` - 1, for `#SBATCH -a 0-<last_instance>` | no |
| `stain` | The stain of the group of the template | no |
| `cochlea_data`, `input_key` | The file of the cochlea folder which holds the stain, and the key of the stain in it | no |
| `cochlea_data_multi`, `input_key_multi`, `channel_multi` | MoBIE image templates only: the file, the key and the name of each stain of `stains`, separated by spaces | no |
| `masking` | `sgn` or `ihc`, for `mask_SGN` and `mask_IHC` | no |
| `synapse_version` | `synapse_v3` | yes |
| `model` | The path of `models.<group>.<version>` | no |
| `sgn_prediction`, `ihc_prediction`, `synapses_prediction` | The prediction folder of each group whose version is known: `<version>`, or `<stain>_<version>` for a stain which is not the default | no |
| `synapse_ihc_prediction` | The folder of the synapse jobs, from both versions, e.g. `synapse_v3_ihc_v11` | no |
| `raw_folder`, `converted_name` | `convert_data` only: `1_raw`, and the value of `cochlea`, see [Data conversion](hpc_04_data_conversion.md) | yes |
| `prediction_dir` | The prediction folder of the group of the template. For `synapses`, it is `synapse_ihc_prediction` | no |
| `sgn_center_distance_threshold`, `sgn_boundary_distance_threshold`, `sgn_distance_smoothing`, and the same with `ihc_` | `segment_SGN` and `segment_IHC` only: the watershed values of `best_best_params.json` in the model folder, else the defaults of the group, see [The watershed values of the segmentation](#the-watershed-values-of-the-segmentation) | yes |

The deployment also names each sbatch script with the cochlea, e.g.
`<date>_sbatch_mask_SGN_MAMD137L.sbatch`.

## Next steps

The analysis of a segmentation runs with the console scripts of cochlea-net:
- [Analysis](https://github.com/computational-cell-analytics/cochlea-net/blob/master/doc/analysis.md): the component labeling, the tonotopic mapping, the object measures, and the upload of the tables to the S3 bucket.
- [Processing parameter files](https://github.com/computational-cell-analytics/cochlea-net/blob/master/reproducibility/README.md#the-processing-parameter-files): the parameters of these steps for each cochlea.
