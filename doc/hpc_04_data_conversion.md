# Convert raw data for the stitching

The Flamingo light-sheet microscope writes each tile and channel of a cochlea as a raw file.
The command `flamingo_tools.convert_data` of cochlea-net converts these raw files to one BDV n5 and
its XML. BigStitcher reads this format for the stitching.

The conversion runs on the cluster. On the LS workstation it often fails, because the connection to
UKON_spezial is unstable and many users share the workstation.

This page assumes that you did the steps of [Getting started](hpc_01_getting_started.md): the
micromamba environment, the clone of slurm-workflows, and the settings file. The deploy step is
explained in [Processing pipelines](hpc_03_processing_pipelines.md#run-a-pipeline).

## The folders

On UKON, the sample folder of a cochlea holds three folders:

| Folder | Content |
|---|---|
| `1_raw` | The raw files of the microscope, one subfolder per stain |
| `2_converted_stitching` | The n5 and the XML of the conversion. Later also the intermediate files of the stitching |
| `3_fused` | The fused result of the stitching |

On the cluster, the conversion uses the cochlea folder `<data_dir>/<cochlea>/`:

| Path | Content |
|---|---|
| `1_raw/` | The copy of the raw files |
| `2_converted_stitching/<converted_name>.n5` | The image data |
| `2_converted_stitching/<converted_name>.xml` | The BDV metadata, which BigStitcher reads |
| `2_converted_stitching/<converted_name>.n5.convert_state.json` | The progress of the conversion |
| `2_converted_stitching/<converted_name>.n5.convert_log.txt` | The reads and transfers that failed and were retried |

`<cochlea>` has the parts of a [cochlea name](hpc_03_processing_pipelines.md#the-cochlea-name), separated by `_`.
The deploy step checks only that the name has these parts. The name of a converted cochlea can differ from the
usual format, e.g. `WT2` as the index and `MWfLS` as the side.
The sample folder on UKON can have another name. For example, the sample folder `Re_AMD_000WT2_MWfLS` has the
cochlea folder `Re_AMD_WT2_MWfLS` on the cluster.

## Convert with a Slurm job

The procedure has three steps:

1. Copy the raw files from UKON to the cluster.
2. Convert them in a Slurm job.
3. Copy the result back to UKON.

Steps 1 and 3 run on the login node, in a tmux session, see [HPC 101](hpc_101.md#use-tmux-sessions).
They ask for your GWDG password, see [Why the transfers are not Slurm jobs](#why-the-transfers-are-not-slurm-jobs).
A failed login stops a command at once, without a retry, because repeated failed logins can lock the GWDG account.

Activate the micromamba environment of cochlea-net, and set these variables:

```bash
micromamba activate cochlea-net
UKON_DIR="UKON100\archiv\imaging\Lightsheet\Huiskengroup_CTLSM\<year>\<experimenter>\<sample folder>"
COCHLEA_DIR=<data_dir>/<cochlea>
```

> [!IMPORTANT]
> Put the UKON path in quotation marks. Otherwise, the shell removes the backslashes.

### 1. Copy the raw files to the cluster

```bash
mkdir -p "$COCHLEA_DIR"
flamingo_tools.transfer_data -u <GWDG-username> -p "$UKON_DIR" -d 1_raw -o "$COCHLEA_DIR"
```

The raw files land in `$COCHLEA_DIR/1_raw`. If the transfer stops, run the same command again.
See [Data transfer](hpc_02_data_transfer.md#larger-amounts-of-data-eg-n5-files) for the options.

Optional: check the raw files before the submission. `--dry_run` reports the pairs of raw files and
metadata files, and the shape of each tile. It needs a few seconds and writes nothing:

```bash
flamingo_tools.convert_data -i "$COCHLEA_DIR/1_raw" --out_path "$COCHLEA_DIR/2_converted_stitching/<cochlea>.n5" \
    --file_ext .raw --dry_run
```

### 2. Convert in a Slurm job

Write a parameter file `<cochlea>.json`:

```json
{
    "cochlea": "Re_AMD_WT2_MWfLS"
}
```

| Parameter | Purpose |
|---|---|
| `cochlea` | The name of the cochlea folder |
| `raw_folder` | Optional: the folder of the raw files in the cochlea folder. Default: `1_raw` |
| `converted_name` | Optional: the name of the n5 and the XML, without the extension. Default: the value of `cochlea` |
| `time`, `mem` | Optional: the resources of the job. Default: `20:00:00` and `96G` |

Run the deploy step with the template `convert_data`, first as a dry run, then with `--submit`:

```bash
cd /path/to/slurm_outputs
python /path/to/slurm-workflows/scripts/deploy.py --input /path/to/slurm-workflows/templates/cochlea-net/convert_data.template \
    --params /path/to/<cochlea>.json
python /path/to/slurm-workflows/scripts/deploy.py --input /path/to/slurm-workflows/templates/cochlea-net/convert_data.template \
    --params /path/to/<cochlea>.json --submit
```

The dry run gives a warning if `<data_dir>/<cochlea>/<raw_folder>` does not exist.
The job uses 4 cores. The default resources come from one mouse cochlea, which needed 12 h and 96G.
There is no run of a gerbil cochlea yet.

If the job stops before it is complete, e.g. at the time limit, submit it again.
The conversion continues with the first tile that is not complete.
To start again from the beginning, remove the n5, the XML and the `.convert_state.json` first.

### 3. Copy the result to UKON

When the job is complete, copy the n5 and the XML to `2_converted_stitching` on UKON:

```bash
flamingo_tools.transfer_data --ingest -u <GWDG-username> -p "$UKON_DIR\2_converted_stitching" \
    -d <converted_name>.n5 -o "$COCHLEA_DIR/2_converted_stitching"
flamingo_tools.transfer_data --ingest -u <GWDG-username> -p "$UKON_DIR\2_converted_stitching" \
    -d <converted_name>.xml -o "$COCHLEA_DIR/2_converted_stitching"
```

- Each command asks for the password.
- `-p` must exist on UKON. If `2_converted_stitching` does not exist, add `--create-parents`.
- The progress file and the log file stay on the cluster.

The stitching reads `2_converted_stitching/` on the cluster, so keep it.
`1_raw/` is a copy of the data on UKON. Remove it when the upload is complete.

## Alternative: convert in an interactive job, directly from UKON

`convert_data` can also read the raw files from UKON. It copies one raw file at a time to a stage
folder, converts it, and removes it. The cluster then never holds the full `1_raw`, which saves disk
space. The command asks for the password, so it runs in an interactive job and not in a batch job.

1. Start a tmux session on the login node, so that the job continues when the connection to the
   cluster ends.
2. Start an interactive job with the resources of the template. `--constraint=inet` selects a node
   with access to the network:
   ```bash
   tmux new -s convert
   srun -p standard96s:shared -c 4 --mem 96G -t 20:00:00 --constraint=inet --pty bash
   ```
3. In the job, activate the environment and set the variables of [Convert with a Slurm job](#convert-with-a-slurm-job), then run:
   ```bash
   flamingo_tools.convert_data -u <GWDG-username> -i "$UKON_DIR\1_raw" \
       --out_path "$COCHLEA_DIR/2_converted_stitching/<converted_name>.n5" --file_ext .raw \
       --stage_dir "$COCHLEA_DIR/stage" --n_threads "$SLURM_CPUS_PER_TASK"
   ```
4. Copy the result to UKON as in [step 3](#3-copy-the-result-to-ukon), in the same job or on the login node.

- The command asks for the password once and keeps it in memory for the whole run. A retry after a
  disconnect needs no new prompt.
- A failed login stops the command at once. Run it again with the correct password.
- `--stage_dir` needs free space for one raw file.
- `--n_threads` limits the threads to the cores of the job. The default is every core of the node.
- With `--dry_run`, the command checks the UKON path and the pairs of files first. It copies only the
  metadata files.
- If the job stops, start a new interactive job and run the same command. The conversion continues
  where it stopped, and asks for the password again.

| | Slurm job | Interactive job |
|---|---|---|
| Disk space on the cluster | `1_raw` and the n5 | The n5 and one raw file |
| Cores | Only for the conversion | Also while the transfer of each raw file runs |
| Archive | The deploy step archives the job | No archive |
| Your attention | Three commands. The job runs alone | The tmux session must stay open |

## Why the transfers are not Slurm jobs

The transfer commands ask for your GWDG password. A batch job has no terminal, so the prompt fails.
To give a batch job the password, the password must stay on the cluster until the job starts:

- in a file;
- in the environment of the job, which Slurm stores;
- in the sbatch script, which the archive copies.

The GWDG password also protects your mail and the other GWDG services. Thus no template contains a
transfer from or to UKON. The interactive job keeps the password only in the memory of the command.
