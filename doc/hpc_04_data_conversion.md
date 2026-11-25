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
| `convert_pipeline.json` | `slurm_wf.convert` only: the finished stages and the JobIDs |
| `transfer_log.txt` | `slurm_wf.convert` only: the files that a transfer could not copy |

`<cochlea>` has the parts of a [cochlea name](hpc_03_processing_pipelines.md#the-cochlea-name), separated by `_`.
The deploy step checks only that the name has these parts. The name of a converted cochlea can differ from the
usual format, e.g. `WT2` as the index and `MWfLS` as the side.
The sample folder on UKON can have another name. For example, the sample folder `Re_AMD_000WT2_MWfLS` has the
cochlea folder `Re_AMD_WT2_MWfLS` on the cluster.

## Convert with one command

`slurm_wf.convert` does the steps of [Convert step by step](#convert-step-by-step) for one cochlea:

1. It copies the raw files from UKON to the cluster.
2. It checks the raw files with the dry run of the conversion.
3. It submits the conversion with the deploy step and waits for the job.
4. It copies the n5 and the XML to UKON.
5. With `--remove-raw`, it removes the copy of `1_raw` on the cluster after the upload.

The command asks for your GWDG password once and keeps it only in memory, see
[Why the transfers are not Slurm jobs](#why-the-transfers-are-not-slurm-jobs). It runs on the login node for the
whole time, also while the job waits in the queue. Thus start it in a tmux session, see
[HPC 101](hpc_101.md#use-tmux-sessions). Outside tmux, screen or another terminal multiplexer, the command stops
before the password prompt. A tmux session on your own computer does not count: the command must run below a
multiplexer on the login node. `--allow-no-tmux` skips this check.

1. Note the name of the login node, e.g. `glogin10`. A tmux session exists only on its login node. The command
   prints the name too.
2. Start a tmux session and activate the micromamba environment of cochlea-net:
   ```bash
   tmux new -s convert
   micromamba activate cochlea-net
   ```
3. Write the parameter file `<cochlea>.json`. `ukon_dir` is the sample folder on UKON, which holds `1_raw`:
   ```json
   {
       "cochlea": "R_AMD_WT1_L",
       "ukon_dir": "UKON100/archiv/imaging/Lightsheet/Huiskengroup_CTLSM/<year>/<experimenter>/<sample folder>"
   }
   ```
   Use forward slashes. In JSON, a backslash needs a second backslash.
4. Go to the folder of the slurm output files. Check the stages with `--dry-run`, then start the command:
   ```bash
   cd /path/to/slurm_outputs
   python /path/to/slurm-workflows/scripts/convert_cochlea.py -j /path/to/<cochlea>.json --dry-run
   python /path/to/slurm-workflows/scripts/convert_cochlea.py -j /path/to/<cochlea>.json --remove-raw
   ```
5. Enter the password. The command logs in to UKON at once and stops if the password is wrong. Then detach from the
   session with `Ctrl`+`b`, `d`.
6. To look at the progress, connect to the same login node, e.g. `ssh glogin10.hpc.gwdg.de`, and attach to the
   session with `tmux attach -t convert`.

- The GWDG username is `academic_id` of the settings file. `-u` overrides it.
- `<data_dir>/<cochlea>/convert_pipeline.json` records each finished stage and the JobIDs.
- If the command stops, e.g. after a reboot of the login node, run the same command again. It continues with the
  first unfinished stage, and asks for the password again if a transfer remains.
  - A download continues with the missing files. A partly copied file continues where it stopped.
  - An upload of the n5 continues with the chunk directories that are incomplete on UKON.
  - A job of an earlier run which still waits or runs is not submitted again.
  - If the earlier run started on another login node, add `--takeover`.
- The command checks the state of the job every 5 minutes with `sacct`.
  - After a `TIMEOUT`, a node failure or a preemption, it submits the job again, at most twice
    (`--max-resubmits`). The conversion continues with the first tile that is not complete.
  - After any other failure of the job, the command stops. Fix the cause, then run the same command again. It
    submits the job again.
- A failed transfer stops the command. A failed login stops it at once, without a retry.
- The deploy step archives each job as usual. `--force` and `--allow-dirty` go to the deploy step.
- A tracked repository with uncommitted changes stops the command before the password prompt, and not only
  at the submission after the download. `--allow-dirty` skips this check.
- Without `--remove-raw`, `1_raw` stays on the cluster. A later run with `--remove-raw` only removes it.

## Convert step by step

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

The raw files land in `$COCHLEA_DIR/1_raw`. If the transfer stops, run the same command again with `--resume`.
It then copies only the missing files and the files with a different size, and continues a partly copied file.
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
| `time`, `mem` | Optional: the resources of the job. Default `mem`: `16G`. Default `time`: see below |
| `ukon_dir` | Only for `slurm_wf.convert`: the sample folder on UKON, which holds `1_raw` |

Run the deploy step with the template `convert_data`, first as a dry run, then with `--submit`:

```bash
cd /path/to/slurm_outputs
python /path/to/slurm-workflows/scripts/deploy.py --input /path/to/slurm-workflows/templates/cochlea-net/convert_data.template \
    --params /path/to/<cochlea>.json
python /path/to/slurm-workflows/scripts/deploy.py --input /path/to/slurm-workflows/templates/cochlea-net/convert_data.template \
    --params /path/to/<cochlea>.json --submit
```

The dry run gives a warning if `<data_dir>/<cochlea>/<raw_folder>` does not exist.
The job uses 4 cores and 16G. The conversion holds ~3 GB in memory, independent of the tile size.

The deploy step sets the time limit from the size of the raw folder:
- the time at 100 MB/s, times 1.5;
- rounded up to 30 min;
- at least 1 h, at most 48 h, the limit of the shared partitions.

For example, 720 GB of raw data get 3:30 h. The rule is `time_from_size` in the row of `convert_data` in
`templates/cochlea-net/steps.json`, see [the steps file](new_template.md#cochlea-net-a-row-in-the-steps-file).
To tune it, edit that row. The deploy step prints the limit and the size in a note.

- A `time` in the parameter file overrides the rule.
- Without the raw folder, e.g. in a dry run before the download, the limit is the `time` of the row, `08:00:00`.
  A note then names the missing folder.

The conversion reached 96 to 105 MB/s on 2026-11-24 and 2026-11-25: 720 GB in 1.9 h, 1.0 TB in 2.9 h. At a high
load of the file system, it slows down to ~65 MB/s. A short time limit lets the job start sooner: a job with an
8 h limit waited 13.8 h in the queue, a job with a 4 h limit 28 min.

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
- If the upload of the n5 stops, run the same command again with `--resume`. It then uploads only the chunk
  directories that are incomplete on UKON.
- The progress file and the log file stay on the cluster.

The stitching reads `2_converted_stitching/` on the cluster, so keep it.
`1_raw/` is a copy of the data on UKON. Remove it when the upload is complete.

## Why the transfers are not Slurm jobs

The transfer commands ask for your GWDG password. A batch job has no terminal, so the prompt fails.
To give a batch job the password, the password must stay on the cluster until the job starts:

- in a file;
- in the environment of the job, which Slurm stores;
- in the sbatch script, which the archive copies.

The GWDG password also protects your mail and the other GWDG services. Thus no template contains a
transfer from or to UKON.

A job also cannot reach UKON. The compute nodes cannot open port 445, the port of SMB, also not with
`--constraint=inet` (tested on 2026-11-25). `--constraint=inet` gives access to HTTP, HTTPS and FTP only.

The transfer commands and `slurm_wf.convert` run on the login node, and keep the password only in the memory of
their process. `slurm_wf.convert` keeps it for the whole run, which can take one or two days with the queue
wait. It gives the password to each transfer on stdin, never in a file, an argument or its environment. The
transfer command then gives it to `smbclient` in the environment variable `PASSWD`. Only you and root can read
the environment of your processes.
