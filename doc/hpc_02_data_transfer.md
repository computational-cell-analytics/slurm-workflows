# Data transfer from/to UKON and to the S3 bucket

## Data transfer from/to UKON100

### Single/Few files

Use `smbclient` to browse UKON100 and to transfer single files. Connect to UKON with:
```bash
# connect to UKON
smbclient //wfs-medizin.top.gwdg.de/ukon-all$/ukon100 -U GWDG/gwdg_username
# e.g. smbclient //wfs-medizin.top.gwdg.de/ukon-all$/ukon100 -U GWDG/schilling40

# connect to UKON_spezial
smbclient //wfs-medizin-spezial.top.gwdg.de/ukon-all$ -U GWDG/gwdg_username
# smbclient //wfs-medizin-spezial.top.gwdg.de/ukon-all$ -U GWDG/schilling40
```

In the `smbclient` session, use these commands:
```bash
# toggle the recursive copy, default: off
recurse
# toggle the prompt before each file transfer, default: on
prompt
# copy a file or directory from UKON to the current directory on the cluster
mget UKON_file/directory
# copy a file or directory from the current directory on the cluster to UKON
mput local_file_name/directory
```
This example copies the file `UKON100\archiv\imaging\Lightsheet\Huiskengroup_CTLSM\2026\Aleyna\M_AMD_00C202_L\3_fused\MAMD_C202_PV_CR_Lypd1_fused.xml` to the cluster:
```bash
smbclient //wfs-medizin-spezial.top.gwdg.de/ukon-all$ -U GWDG/schilling40
# enter the password
# go to the directory on UKON
cd UKON100\archiv\imaging\Lightsheet\Huiskengroup_CTLSM\2026\Aleyna\M_AMD_00C202_L\3_fused
recurse
prompt
mget MAMD_C202_PV_CR_Lypd1_fused.xml
# close the connection with `exit` or `Ctrl`+`c`
exit
```

### Larger amounts of data, e.g. n5 files

The connection, above all to UKON_spezial, is unstable. For large data, use the script `scripts/data_transfer/smb_transfer_resilient.py` of cochlea-net. The script retries the connection and tracks the files that it did not transfer.

> [!IMPORTANT]
> Put the UKON path in quotation marks. Otherwise, the shell removes the backslashes.

```bash
python ~/cochlea-net/scripts/data_transfer/smb_transfer_resilient.py --username gwdg_username --remote_parent_dir "UKON100\archiv\imaging\Lightsheet\Huiskengroup_CTLSM\2026\Aleyna\M_AMD_00C202_L\3_fused" --remote_data MAMD_C202_PV_CR_Lypd1_fused.n5 -o .
```
The script asks for your GWDG password and then starts the transfer.

## Data transfer from/to the S3 bucket

Access to the S3 bucket needs an `aws_access_key_id` and an `aws_secret_access_key`. The S3 bucket stores the data in the MoBIE data format, which the MoBIE utils CLI creates.

> [!CAUTION]
> Keep the access keys secret. Do not put them in a shared file or in a git repository.

### Required files for `rclone`

`rclone` copies data between the cluster and the S3 bucket. Load it with:
```bash
module load rclone
```

Add the access keys to these two files:
1) `~/.aws/credentials`:
```bash
mkdir -p ~/.aws
vim ~/.aws/credentials
```
The `credentials` file contains:
```
[default]
aws_access_key_id = <aws_access_key_id>
aws_secret_access_key = <aws_secret_access_key>
```

2) `~/.config/rclone/rclone.conf`:
```bash
mkdir -p ~/.config/rclone
vim ~/.config/rclone/rclone.conf
```
The `rclone.conf` file contains:
```
[cochlea-lightsheet]
type = s3
provider = Ceph
access_key_id = <aws_access_key_id>
secret_access_key = <aws_secret_access_key>
endpoint = https://s3.fs.gwdg.de
```
`deploy_process.py` warns if `rclone.conf` has no section for the remote of `s3_remote` in the settings file.

### Copy data from/to the S3 bucket

> [!TIP]
> Run a long transfer in a `tmux` screen, see [HPC 101](hpc_101.md#use-screens).
> The transfer of image data takes hours. The transfer of segmentation data and tables takes minutes.

```bash
# copy from the S3 bucket to the cluster
rclone --progress copyto cochlea-lightsheet:cochlea-lightsheet/path/to/file_or_folder /path/to/local/file_or_folder
# copy from the cluster to the S3 bucket
rclone --progress copyto /path/to/local/file_or_folder cochlea-lightsheet:cochlea-lightsheet/path/to/file_or_folder
```

### Add a dataset to the S3 bucket
MoBIE lists the datasets of a project in the file `project.json`, and the sources of a dataset in the file `<dataset>/dataset.json`. A local file lists only the datasets and sources of the local MoBIE project. The S3 files list the datasets and sources of all local MoBIE projects, i.e. of all users.

> [!CAUTION]
> Do not overwrite the `project.json` or a `dataset.json` of the S3 bucket with a local copy. Add your dataset or your sources to the S3 version instead.
> A local copy removes the sources of all other users from the S3 file. Their data stays in the bucket, but MoBIE in Fiji does not show it any more.

The transfer jobs add only their own entries to the S3 files:
- `mobie_transfer_dataset` adds the stains to the `dataset.json` and the cochlea to the `project.json`.
- `mobie_transfer_segmentation` adds the segmentation to the `dataset.json`.
- `mobie_transfer_detection` adds the spot source to the `dataset.json`.

The script copies the S3 file to `<mobie_project>/tmp_<JobID>_<file name>` and adds the entries. Outside of a Slurm job, the copy is `tmp_<file name>`. Before the upload, it reads the S3 file again. If another job changed the S3 file in the meantime, it uploads nothing. Then it removes the copy. The local MoBIE project does not change. After the upload, the script prints a warning for each mismatch between the S3 `dataset.json` and the data in the bucket.

For a dataset which you transfer without the pipeline, run the same script. `--source` names the sources which you transferred:
```bash
module load rclone
python /path/to/slurm-workflows/scripts/add_s3_dataset.py -p /path/to/mobie_project -r cochlea-lightsheet:cochlea-lightsheet -d <dataset> --source <source> [<source> ...]
```

A `project.json` can miss a dataset, for example after a transfer by hand. `--generate` sets the dataset list to the dataset folders, i.e. the folders which hold a `dataset.json`. It adds each missing name and removes each name without a folder. It prints the added and the removed names. Every other entry stays unchanged.
```bash
# set the list of the S3 project.json to the dataset folders of the S3 bucket
python /path/to/slurm-workflows/scripts/add_s3_dataset.py -p /path/to/mobie_project -r cochlea-lightsheet:cochlea-lightsheet --generate s3
# set the list of the local project.json to the local dataset folders, without an upload
python /path/to/slurm-workflows/scripts/add_s3_dataset.py -p /path/to/mobie_project --generate local
```

### Check and repair the dataset.json files of the S3 bucket
`--check` compares the `dataset.json` of each dataset in the S3 bucket with its data folders `images/ome-zarr/<source>.ome.zarr` and `tables/<source>`. A table folder counts only if it holds a `default.tsv`. MoBIE reads the image data of a source from its `s3Address`, so the check uses the `s3Address` and not the `relativePath`. The check reads only, and prints:
- data which no source lists, e.g. the sources of other users after an upload of a local `dataset.json`;
- a source whose data is missing, e.g. after a rename by hand;
- a view which shows a missing source;
- a default view which shows no image;
- a synapse source whose name is not `[<stain>_]synapse_<lowercase suffix>`, e.g. `synapses_v3_IHC_v11` instead of `synapse_v3_ihc_v11`. cochlea-net reads a synapse source by its name.

`--repair` fixes the first case, and with `--default_view` the last case. Fix the other cases by hand: point the source to the renamed data, or remove a source whose data is missing.
```bash
python /path/to/slurm-workflows/scripts/add_s3_dataset.py -p /path/to/mobie_project -r cochlea-lightsheet:cochlea-lightsheet --check
```

`--repair` adds a source to the S3 `dataset.json` for each data folder which no source lists. It builds the entry from the data in the bucket, so it needs no local MoBIE project. The data, not the name, gives the source type:

| data in the bucket | source |
|---|---|
| `<source>.ome.zarr` and a table with `label_id` | segmentation |
| a table with `spot_id`, no OME-Zarr | spots. The bounding box is the range of `x`, `y` and `z` in the table, as `mobie.add_spots` computes it |
| `<source>.ome.zarr` with an 8 or 16 bit dtype, no table | image, with the contrast limits `[0, 255]` or `[0, 1400]` |
| `<source>.ome.zarr` with an integer dtype of 32 bit or more, no table | segmentation without a table |

The repair does not change an existing source. It skips data which matches no row, e.g. a segmentation table without its OME-Zarr, and prints it. `--default_view <source>` makes the view of that source the default view of the dataset. Write the result to a file with `-o` first, check it, then run the command again without `-o` to upload it.
```bash
python /path/to/slurm-workflows/scripts/add_s3_dataset.py -p /path/to/mobie_project -r cochlea-lightsheet:cochlea-lightsheet \
    -d <dataset> --repair -e https://s3.fs.gwdg.de --default_view PV -o repaired_dataset.json
```
