# Data transfer from/to UKON and to the S3 bucket

## Data transfer from/to UKON100

### Single/Few files

It is helpful to look around the UKON100 and transfer single files. You can connect to UKON using:
```bash
# connect to UKON
smbclient //wfs-medizin.top.gwdg.de/ukon-all$/ukon100 -U GWDG/gwdg_username
# e.g. smbclient //wfs-medizin.top.gwdg.de/ukon-all$/ukon100 -U GWDG/schilling40

# connect to UKON_spezial
smbclient //wfs-medizin-spezial.top.gwdg.de/ukon-all$ -U GWDG/gwdg_username
# smbclient //wfs-medizin-spezial.top.gwdg.de/ukon-all$ -U GWDG/schilling40
```

Once there, you can use
```bash
# toggle recursive copy of files, default: no recursive copying
recurse
# toggle prompt for file transfer, default: manual check for every file transfer
prompt
# navigate to a certain directory and copy it to your working directory on the cluster
mget UKON_file/directory
# upload file from the working directory on the cluster to UKON100
mput local_file_name/directory
```
An example for copying the file `UKON100\archiv\imaging\Lightsheet\Huiskengroup_CTLSM\2026\Aleyna\M_AMD_00C202_L\3_fused\MAMD_C202_PV_CR_Lypd1_fused.xml` to a directory on the cluster would be:
```bash
smbclient //wfs-medizin-spezial.top.gwdg.de/ukon-all$ -U GWDG/schilling40
# inserting password
# navigating to the directory on UKON
cd UKON100\archiv\imaging\Lightsheet\Huiskengroup_CTLSM\2026\Aleyna\M_AMD_00C202_L\3_fused
recurse
prompt
mget MAMD_C202_PV_CR_Lypd1_fused.xml
# exit the connection using `Ctrl`+`c` or `exit`
exit
```

### Larger amounts of data, e.g. n5 files

Because the connection, particularly to UKON_spezial, is unstable, larger amounts of data should be transferred with the script `scripts/data_transfer/smb_transfer_resilient.py`.
The script tracks files, which were not successfully transferred and does retries for the connection. Notice that you have to use quotation marks around the UKON path to ensure a successful parsing.
Example:
```bash
python ~/cochlea-net/scripts/data_transfer/smb_transfer_resilient.py --username gwdg_username --remote_parent_dir "UKON100\archiv\imaging\Lightsheet\Huiskengroup_CTLSM\2026\Aleyna\M_AMD_00C202_L\3_fused" --remote_data MAMD_C202_PV_CR_Lypd1_fused.n5 -o .
```
You will be prompted to enter your GWDG password. Afterwards, the transfer process should start.

## Data transfer from/to the S3 bucket

For accessing data on the S3 bucket, an `aws_access_key_id` and an `aws_secret_access_key` are required.
Data on the S3 bucket are in MoBIE data format. The data is created by using th MoBIE utils CLI functions.

### TODO: Implement mobie utils function

### Required files for `rclone`

The interaction between the server and the S3 bucket is performed using `rclone` which has been loaded on the cluster using:
```bash
module load rclone
```

To make the interaction of clone with the S3 cluster possible, the access keys have to be added to the two following files:
1) `.aws/credentials`:
```bash
# go to your root directory
cd ~
# create the `.aws` directory
mkdir .aws
cd .aws
vim credentials
```
The `credentials` file should contain:
```
[default]
aws_access_key_id = <aws_access_key_id>
aws_secret_access_key = <aws_secret_access_key>
```
The keys are not written here explicitly to ensure data safety.

2) `.config/rclone/rclone.conf`
```bash
# go to your root directory
cd ~
# create the `.aws` directory
mkdir -p .config/rclone
cd .config/rclone
vim rclone.conf
```
The `rclone.conf` file should contain:
```
[cochlea-lightsheet]
type = s3
provider = Ceph
access_key_id = <aws_access_key_id>
secret_access_key = <aws_secret_access_key>
endpoint = https://s3.fs.gwdg.de
```
The keys are not written here explicitly to ensure data safety.

### Copy data from/to the S3 bucket

The best way to copy large amounts of data is the use of a `tmux` screen. The transfer of staining data is in the order of hours, the transfer of segmentation data and tables in the order of minutes.

```bash
# copy file from S3 bucket to local directory
rclone --progress copyto cochlea-lightsheet:cochlea-lightsheet/path/to/file_or_folder /path/to/local/file_or_folder
# copy file from local directory to S3 cluster
rclone --progress copyto /path/to/local/file_or_folder cochlea-lightsheet:cochlea-lightsheet/path/to/file_or_folder
```

### Add a dataset to the S3 bucket
MoBIE manages the project with a `project.json` file. However, the local version only has the datasets, which were added to this MoBIE instance while the version of the S3 buckets is a collection of all datasets, which may come from different local MoBIE projects. Therefore, it is essential to not overwrite the external S3 bucket `project.json` but instead add the datasets to it and update it.
```bash
# copy the external project.json
rclone copyto cochlea-lightsheet:cochlea-lightsheet/project.json project_remote.json
# add the dataset to project_remote.json, e.g.
vim project_remote.json
# upload the edited file
rclone copyto project_remote.json cochlea-lightsheet:cochlea-lightsheet/project.json
```