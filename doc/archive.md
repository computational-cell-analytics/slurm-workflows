# The archive and the efficiency analysis

Each submitted job gets an archive folder with its sbatch script, its JobIDs, its environment and its
metadata.
The archive makes a run reproducible.
It is also the data of the efficiency analysis: how well a job used the requested resources, and how
many core hours a project spent.

## Why an archive folder

Slurm gives each job a unique JobID.
But the number tells nothing about the job, and one task often runs several times before the result
is good.
So each job has a date and a suffix.
Every file of a job uses the format `<date>_<middle>_<suffix>.<ext>`, with the date as `YYYY-MM-DD`
and a middle part of one word, e.g. `2025-03-19_sbatch_apply_unet.sbatch` and
`2025-03-19_log_apply_unet.txt`.
The archive folder of the job is `<archive_dir>/<date>_<suffix>/`.

## The archive folder

The archive directory is the folder which holds the archive folders.
`scripts/deploy.py` takes it from `--archive-dir`, else from `archive_dir` of the settings file.
The archive directory must exist.
A missing archive directory gives a warning, which stops `--submit` unless `--force` is given.

```
<archive_dir>/
└── <date>_<suffix>/
    ├── sbatch.sbatch       # the submitted sbatch script
    ├── log.txt             # one JobID per line, the latest last
    ├── metadata.json       # see below
    ├── environment.yml     # the export of the micromamba environment of the job
    └── previous/
        └── <jobid>/        # the files of a replaced JobID, except log.txt
```

A second submission on the same day uses the same archive folder.
Its JobID is added to `log.txt`, and the top level holds the files of the latest JobID.
Before the new files are archived, the files of the replaced JobID move to `previous/<jobid>/`.
So each JobID keeps its script, its environment, and its metadata with the git hashes, the
`pipeline` entry and the final state, e.g. `TIMEOUT`.
`scripts/summary.py` reads only the latest JobID of each archive folder.
`environment.yml` is missing if the job activates no micromamba environment, see
[Create a template](new_template.md#4-activate-a-micromamba-environment).
`mockup_example/YYYY-MM-DD_train/` is a complete example.

## The keys of metadata.json

The keys are lower case with underscores.

| Key | Source | Content |
|---|---|---|
| `date`, `task` | Name of the archive folder | The date and the suffix of the job |
| `academic_id`, `hpc_user` | Settings file | The person who ran the job. Left out if empty |
| `job_name`, `runtime`, `partition`, `gpu`, ... | `#SBATCH` lines | See [the #SBATCH forms](new_template.md#3-keep-the-sbatch-lines-which-the-archive-reads) |
| `jobid` | `log.txt` | The latest JobID |
| `reportseff` | `reportseff` | One entry per job or array task: `jobid`, `state`, `elapsed`, `time_eff`, `cpu_eff`, `mem_eff`. A task of an array which never started has a JobID such as `<jobid>_[9]`. An entry with the state `UNTRACKED` also has `last_state`, see update_metadata.py |
| `repositories` | `git` | One entry per repository: `repo_name`, `repo_version` (the git hash), `status` (`clean` or `dirty`) |
| `project` | Deploy step | The project, e.g. `cochlea-net` |
| `pipeline` | Deploy step | Only for a step of a pipeline: `name` of the pipeline, `step` number, number of steps `of`, and `depends_on`, the JobID of the previous step. `update_metadata.py` adds `failed_at` to a step which never started, see below |
| `slurm_output` | Slurm output file | One entry per output file: `file`, `array_index` of an array task, `submitted`, `started`, `ended`, `elapsed_min`, `limit_min`, `cpus`, `nodes`, `core_hours` |
| `backfilled` | `update_metadata.py --backfill` | Only in `previous/<jobid>/` of an earlier JobID of `log.txt`, which was archived without its own files. The metadata holds only `date`, `task`, `jobid`, the report and `slurm_output` |

The archive records the git hash of slurm-workflows, of each entry of `repositories` in the
settings file, and of each repository of `--repository-file`.
A repository with uncommitted changes has the status `dirty`, so its hash does not give the exact
code.

## The workflow

1. `scripts/deploy.py --submit` submits the jobs and writes the archive folders.
   At this time, the efficiency report shows the state `PENDING`.
2. After the jobs ended, update the archive:
   ```bash
   python scripts/update_metadata.py --slurm-dir <folder of slurm-<jobid>.out> <archive_dir>
   ```
   `reportseff` lists a job for 365 days after its end.
   Update the archive before the slurm output files are deleted.
3. Print the efficiency summary, and write one row per job to a table:
   ```bash
   python scripts/summary.py --output jobs.xlsx --since 2026-01-01 <archive_dir>
   ```

`reportseff` lists only the jobs of the user who runs it, and only of the cluster of the login node.
So run the update as the HPC user who submitted the jobs, on the same cluster.
If `reportseff` is missing, run `module load py-reportseff`.
Without `reportseff`, the commands print a warning and record no report.

`--slurm-dir` is the folder of the files `slurm-<jobid>.out`.
Slurm writes them into the directory where the deploy step ran, unless `#SBATCH -o` names another
file.
The core hours come only from these files.

## update_metadata.py

```bash
python scripts/update_metadata.py [--pattern <text>] [--slurm-dir <dir>] [--general] [--sbatch] [--backfill] \
    <archive_dir>
```

The command always refreshes the efficiency report of a job if the report is empty or has a task
which is not final, e.g. `PENDING`, `RUNNING` or `UNTRACKED`.
The final states are `COMPLETED` and the failed states, e.g. `FAILED`, `TIMEOUT` or `CANCELLED`.
All updates also cover the replaced JobIDs in `previous/<jobid>/`.

The Slurm database of GWDG purges a job 365 days after its end.
So the command does not query a job whose `date` is more than 372 days old.
The 7 extra days cover the time from the submission to the end of a job.

If `reportseff` does not list a job, e.g. after the purge, the job keeps its report.
Each task which is not final gets the state `UNTRACKED`, and `last_state` keeps its old state.
The command prints a warning with each JobID of the last 365 days which `reportseff` does not list.

A step of a pipeline waits for its previous step with `afterok`.
If the previous step fails, Slurm cancels the step before it starts.
`reportseff` lists such a step as `CANCELLED` with the elapsed time `00:00:00`.
A step without a report, or with only such tasks, and without a `slurm_output` entry with `started`,
never started.
For such a step, the command follows `depends_on` back through the archive.
If a previous step ended in a failed state, the step gets `failed_at` in its `pipeline` entry: the
`jobid`, the `step` and the `state` of the first step which failed.
The command prints this once, and does not refresh the step again.
This needs no `reportseff`.
A step whose previous step still shows `RUNNING` or `PENDING` gets no entry.
A step which was cancelled by hand before it started gets no entry, because it is itself the step
which failed.

The options add more updates:

| Option | Update |
|---|---|
| `-p`, `--pattern` | Update only the archive folders whose name contains this text. The text can hold wildcards |
| `-s`, `--slurm-dir` | Add `slurm_output` from the files `slurm-<jobid>.out` and `slurm-<jobid>_<array_index>.out` of this folder |
| `--general` | Correct `date` and `task` from the name of the archive folder. Replace an efficiency report of another JobID |
| `--sbatch` | Copy the `#SBATCH` values of `sbatch.sbatch` into the metadata again. A folder without `sbatch.sbatch` is skipped |
| `--backfill` | Create `previous/<jobid>/metadata.json` for each earlier JobID of `log.txt` without metadata, e.g. in an archive folder from before `previous/<jobid>/`. The refresh adds the efficiency report, and `--slurm-dir` the core hours |

The command skips an unreadable `metadata.json` with a warning.

### The format of a slurm output file

The cluster writes a section `Job Information` at the end of each slurm output file.
`--slurm-dir` reads this section:

```
============ Job Information ===================================================
Submitted: 2026-06-11T15:40:09
Started:   2026-06-11T15:57:46
Ended:     2026-06-11T17:42:12
Elapsed: 105 min, Limit: 120 min, Difference: 15 min
CPUs: 2, Nodes: 1
Estimated credit Consumption: 262.50 core-hours
================================================================================
```

## summary.py

```bash
python scripts/summary.py [--output <file>] [--pattern <text>] [--since <date>] [--until <date>] \
    [--state <s1,s2>] [--partition <p1,p2>] <archive_dir>
```

The command only reads the archive.

| Option | Purpose |
|---|---|
| `-o`, `--output` | Write one row per job to this file, ordered by date and task. A `.xlsx` file needs openpyxl. Any other suffix gives a TSV file |
| `-p`, `--pattern` | Use only the archive folders whose name contains this text |
| `--since`, `--until` | First and last `date`, `YYYY-MM-DD`. Both are inclusive |
| `--state` | Keep a job if one of its tasks has one of these states, e.g. `COMPLETED,TIMEOUT` |
| `--partition` | Keep a job of one of these partitions, e.g. `grete:shared,standard96` |

The terminal summary gives the average CPU, memory and time efficiency over the finished tasks of
all jobs.
An array job with 10 tasks counts 10 times.
Only a task in a final state counts, see update_metadata.py.
A task which was cancelled before it started, with the elapsed time `00:00:00`, does not count.
The core hours are summed per job, then averaged over the jobs.
A folder without `metadata.json` does not count.

The table has these columns: `date`, `task`, `jobid`, `job_name`, `partition`, `gpu`,
`cpus_per_task`, `memory_per_node`, `runtime`, `tasks` (the number of report entries), `state` (the
different states), the mean `cpu_eff`, `mem_eff` and `time_eff` of the finished tasks as numbers
without `%`, and the sum of `core_hours`.
A missing value gives an empty cell.

## Archive a job by hand

`scripts/deploy.py --submit` does these steps for every job.
Use the scripts to archive a job which was submitted without the deploy step.
The examples use the mockup and a new archive directory `my_archive/`.
Create it first, because the scripts need an existing archive directory:

```bash
mkdir my_archive
```

1. `scripts/submit_job.py` submits an sbatch script and adds the JobID to the log file
   `<date>_log_<suffix>.txt` beside it.
   With `--archive-dir`, it also does the next two steps and writes `environment.yml`:
   ```bash
   python scripts/submit_job.py --archive-dir my_archive/ --settings <settings file> <date>_sbatch_<suffix>.sbatch
   ```
2. `scripts/archive_job.py` copies the files of one job into its archive folder.
   With `--move`, it moves them.
   If `log.txt` exists in the archive folder, the new JobIDs are added to it.
   If the last new JobID replaces the JobID of `metadata.json`, the old files move to
   `previous/<jobid>/` first:
   ```bash
   python scripts/archive_job.py --input-dir mockup_example/ --archive-dir my_archive/ YYYY-MM-DD train
   ```
   Without `--input-dir`, the command uses `$WORK`. Without `--archive-dir`, it uses `$JOB_ARCHIVE`.
3. `scripts/write_metadata.py` creates or updates `metadata.json` in an archive folder:
   ```bash
   python scripts/write_metadata.py --repository-file mockup_example/repository_list.txt \
       --settings mockup_example/project_settings/mockup.json my_archive/YYYY-MM-DD_train/
   ```
   The settings file gives the HPC user.
   The repository file gives the repositories whose git hash the archive records.
   Each line holds a name and a path, separated by whitespace, e.g. a tab.

## Older archives

An older archive can have keys in another format, e.g. `job-name` and `Reportseff`.
`scripts/summary.py` cannot read them.
Rename the keys once:

```bash
python scripts/migrate_metadata_keys.py --dry-run <archive_dir>   # list the files which change
python scripts/migrate_metadata_keys.py <archive_dir>
```

`scripts/migrate_metadata_keys.py` is temporary and will be removed after the migration of the
cluster archive.
