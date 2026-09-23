# Run processing pipelines

Processing pipelines are implemented in the extra Git repository [slurm-workflows](https://github.com/computational-cell-analytics/slurm-workflows).
The templates of the project `cochlea-net` cover:
1) the transform into MoBIE data format and the transfer to the S3 bucket,
2) the segmentation of IHCs,
3) the segmentation of SGNs, and
4) the detection of ribbon synapses.

## Default settings per user

The fixed settings should be set per user, specifically per repository. They are located in `project_settings/cochlea-net.example.json`. Copy these settings to `project_settings/cochlea-net.json`:

```bash
cp project_settings/cochlea-net.example.json project_settings/cochlea-net.json
```

The file contains:

```json
{
    "user_address": "yourmail@gwdg.de",
    "account_name": "your_account",
    "academic_id": "",
    "hpc_user": "",

    "cochlea-net_environment": "cochlea-net",
    "mobie_environment": "mobie",

    "archive_dir": "/path/to/job_archive",
    "data_dir": "/path/to/cochlea-lightsheet",
    "mobie_project": "/path/to/cochlea-lightsheet/mobie_project/cochlea-lightsheet",
    "s3_remote": "cochlea-lightsheet:cochlea-lightsheet",
    "service_endpoint": "https://s3.fs.gwdg.de",

    "repositories": {
        "cochlea-net": "/path/to/cochlea-net",
        "mobie-utils-python": "/path/to/mobie-utils-python",
        "slurm-workflows": "/path/to/slurm-workflows"
    },

    "models": {
        "SGN": {
            "SGN_v2": "/path/to/cochlea-lightsheet/trained_models/SGN/v2_cochlea_distance_unet_SGN_supervised_2025-05-27",
            "SGN_v3": "/path/to/cochlea-lightsheet/trained_models/SGN/v3_cochlea_distance_unet_SGN_supervised_2026-07-10"
        },
        "IHC": {
            "IHC_v4b": "/path/to/cochlea-lightsheet/trained_models/IHC/v4_cochlea_distance_unet_IHC_supervised_2025-07-14",
            "IHC_v5": "/path/to/cochlea-lightsheet/trained_models/IHC/v5_cochlea_distance_unet_IHC_supervised_2025-08-20",
            "IHC_v6": "/path/to/cochlea-lightsheet/trained_models/IHC/v6_cochlea_distance_unet_IHC_supervised_2025-09-02",
            "IHC_v7": "/path/to/cochlea-lightsheet/trained_models/IHC/v7_cochlea_distance_unet_IHC_supervised_2025-09-08",
            "IHC_v9": "/path/to/cochlea-lightsheet/trained_models/IHC/v9_cochlea_distance_unet_IHC_supervised_2026-06-12",
            "IHC_v10": "/path/to/cochlea-lightsheet/trained_models/IHC/v10_cochlea_distance_unet_IHC_supervised_2026-06-12",
            "IHC_v11": "/path/to/cochlea-lightsheet/trained_models/IHC/v11_cochlea_distance_unet_IHC_supervised_2026-07-20"
        },
        "Synapses": {
            "synapses_v3": "/path/to/cochlea-lightsheet/trained_models/Synapses/synapse_detection_model_v3.pt",
            "synapses_v5": "/path/to/cochlea-lightsheet/trained_models/Synapses/synapse_detection_model_v5.pt"
        }
    }
}
```

Compare `project_settings/cochlea-net.json` with `project_settings/cochlea-net.example.json` after every pull. New keys and new model versions arrive in the example file only. An outdated `project_settings/cochlea-net.json` stops a job, either with a missing key or with an unresolved placeholder of a template.

## Parameter files per cochlea

The pipelines are based on the use of `params.json` files.

The current standard format for the file name is:
`<species><annotator-index>_<animal-index><cochlea-side>_<stain1>_<stain2>_<stain3>_fused.n5`,
e.g. the file name `MAMD_137L_PV_Vglut3_CTBP2_fused.n5` refers to:
* animal: `M`
* annotator: `AMD`
* animal index: `137`
* cochlea side: `L`
* stain 1: `PV`
* stain 2: `Vglut3`
* stain 3: `CTBP2`

The `parameter_file.json`, e.g. `M_AMD_000137_L.json` could contain:
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
    "synapse_version": "synapses_v3"
}
```

## Running the processing pipelines

The pipelines are `cochlea-net/mobie`, `cochlea-net/ihc`, `cochlea-net/sgn`, and `cochlea-net/synapses`. The `synapses` pipeline needs an IHC segmentation of the same cochlea, so run `ihc` before it and set `ihc_version` in the parameter file.
When the pipelines are run, the program reads the local variables from `project_settings/cochlea-net.json`.

Example commands are:
```bash
# for a dry run
python slurm-workflows/scripts/deploy_process.py --pipeline cochlea-net/sgn --json parameter_file.json -a /path/to/job_archive_directory
# for submitting the pipeline as a slurm job
python slurm-workflows/scripts/deploy_process.py --pipeline cochlea-net/sgn --json parameter_file.json -a /path/to/job_archive_directory --deploy
```
