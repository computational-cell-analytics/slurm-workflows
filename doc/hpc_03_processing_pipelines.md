# Run processing pipelines

The templates of the project `cochlea-net` in the Git repository [slurm-workflows](https://github.com/computational-cell-analytics/slurm-workflows) cover:
1) the conversion to the MoBIE data format and the transfer to the S3 bucket,
2) the segmentation of IHCs,
3) the segmentation of SGNs, and
4) the detection of ribbon synapses.

## Default settings per user

Each user keeps the fixed settings in their own clone of the repository. Copy the blueprint `project_settings/cochlea-net.blueprint.json` to `project_settings/cochlea-net.json` and adapt the values:

```bash
cp project_settings/cochlea-net.blueprint.json project_settings/cochlea-net.json
```

The file contains:

```json
{
    "user_address": "yourmail@gwdg.de",
    "account_name": "your_account",
    "academic_id": "",
    "hpc_user": "",

    "cochlea-net_environment": "cochlea-net",
    "mobie_environment": "cochlea-net",

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

> [!WARNING]
> Git does not track `project_settings/cochlea-net.json`. New keys and model versions arrive only in the blueprint file.
> Compare the two files after every pull. An outdated settings file stops a job with a missing key or an unresolved placeholder.

## Parameter files per cochlea

The standard format of the image file name is:
`<species><experimenter-initials>_<animal-index><cochlea-side>_<stain1>_<stain2>_<stain3>_fused.n5`,
e.g. the file name `MAMD_137L_PV_Vglut3_CTBP2_fused.n5` refers to:
* species: `M`
* experimenter initials: `AMD`
* animal index: `137`
* cochlea side: `L`
* stains 1 to 3: `PV`, `Vglut3`, `CTBP2`

Each pipeline reads the settings file and one parameter file per cochlea, e.g. `M_AMD_000137_L.json`:
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
    "synapse_version": "synapses_v3",
    "stain_synapses": "CTBP2"
}
```

> [!NOTE]
> `stain_synapses` selects the stain of the synapse detection. The default is `CTBP2`. A different stain, e.g. `Homer1`, becomes part of the prediction folder, the MoBIE source and the S3 table, e.g. `Homer1_synapses_v3_IHC_v11`.

## Running the processing pipelines

The pipelines are `cochlea-net/mobie`, `cochlea-net/ihc`, `cochlea-net/sgn`, and `cochlea-net/synapses`.

> [!IMPORTANT]
> The `synapses` pipeline needs the IHC segmentation of the same cochlea. Run `ihc` first and set `ihc_version` in the parameter file.

Example commands:
```bash
# dry run: write the sbatch script, submit nothing
python slurm-workflows/scripts/deploy_process.py --pipeline cochlea-net/sgn --json parameter_file.json
# submit the pipeline as a chain of Slurm jobs
python slurm-workflows/scripts/deploy_process.py --pipeline cochlea-net/sgn --json parameter_file.json --deploy
```

> [!NOTE]
> The last step of each pipeline removes the intermediate data, such as `predictions.zarr` and the local MoBIE copies, after it checks the result. Add `--no-cleanup` to keep it, for example to run a new watershed on the same prediction.

## Next steps

The analysis of a segmentation runs with the console scripts of cochlea-net:
- [Analysis](https://github.com/computational-cell-analytics/cochlea-net/blob/master/doc/analysis.md): the component labeling, the tonotopic mapping, the object measures, and the upload of the tables to the S3 bucket.
- [Processing parameter files](https://github.com/computational-cell-analytics/cochlea-net/blob/master/reproducibility/README.md#the-processing-parameter-files): the parameters of these steps for each cochlea.
