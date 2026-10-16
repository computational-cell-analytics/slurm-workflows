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
MoBIE lists the datasets of a project in the file `project.json`. A local `project.json` lists only the datasets of the local MoBIE project. The `project.json` of the S3 bucket lists the datasets of all local MoBIE projects.

> [!CAUTION]
> Do not overwrite the `project.json` of the S3 bucket with a local copy. Add your dataset to the S3 version instead.

The job `mobie_transfer_dataset` adds the cochlea to the `project.json` of the S3 bucket. The script copies the file to `<mobie_project>/tmp_<JobID>_project.json`, adds the dataset, and sorts the dataset names. Outside of a Slurm job, the copy is `tmp_project.json`. Before the upload, it reads the S3 file again. If another job changed the S3 file in the meantime, it uploads nothing. Then it removes the copy.

For a dataset which you transfer without the pipeline, run the same script:
```bash
module load rclone
python /path/to/slurm-workflows/scripts/add_s3_dataset.py -p /path/to/mobie_project -r cochlea-lightsheet:cochlea-lightsheet -d <dataset>
```

A `project.json` can miss a dataset, for example after a transfer by hand. `--generate` sets the dataset list to the dataset folders, i.e. the folders which hold a `dataset.json`. It adds each missing name and removes each name without a folder. It prints the added and the removed names. Every other entry stays unchanged.
```bash
# set the list of the S3 project.json to the dataset folders of the S3 bucket
python /path/to/slurm-workflows/scripts/add_s3_dataset.py -p /path/to/mobie_project -r cochlea-lightsheet:cochlea-lightsheet --generate s3
# set the list of the local project.json to the local dataset folders, without an upload
python /path/to/slurm-workflows/scripts/add_s3_dataset.py -p /path/to/mobie_project --generate local
```
