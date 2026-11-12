# Create a template

A template is an sbatch script with placeholders.
The deploy step fills the placeholders and writes the sbatch script of a job.
This guide shows how to turn a working sbatch script into a template.
[Create a new project](new_project.md) shows the other parts of a project.
The templates of `mockup_example/templates/mockup/` are the worked example.

## 1. Start from a working sbatch script

Start from an sbatch script which runs with `sbatch` on the cluster.
A template adds no function to the script. It only replaces values, so an error of the script stays
an error of the template.

## 2. Replace the values with placeholders

A placeholder has the format `<name>`.
Replace two kinds of values:

- A value of the cluster account, e.g. the mail address, the Slurm account, a data folder, the name of
  the environment, or the path of a git repository. The settings file holds these values.
- A value of the job, e.g. the dataset or the run name. The parameter file holds these values.

After this step, the template holds no absolute path and no personal value.
The first step of the mockup shows both kinds:

```bash
#SBATCH --job-name=train-<run_name>              # parameter
#SBATCH --mail-user=<user_address>               # setting
micromamba activate <environment>                # setting
export INPUT=<data_dir>/<dataset>                # setting and parameter
python -u <repo:my-tool>/train.py --input "$INPUT" --output "$OUTPUT_FOLDER"
```

The rules of the syntax:

- A name holds no whitespace, `<` or `>`. So the shell code `cmd < in > out` holds no placeholder.
- Write `\<name>` for the literal text `<name>`.
- The deploy step does not search a value for placeholders.
- `<repo:name>` is the path of the entry `name` of `repositories` in the settings file.
  `<repo:slurm-workflows>` is always the path of this repository.

A parameter overrides the setting of the same key.
The deployment module of a project can add more values, see
[the order of precedence](new_project.md#order-of-precedence-of-the-placeholder-values).

## 3. Keep the #SBATCH lines which the archive reads

The archive copies some `#SBATCH` values into `metadata.json`.
Write each value in one of these forms:

| Form | Key in `metadata.json` |
|---|---|
| `-A`, `--account` | `account` |
| `-a`, `--array` | `job_array` |
| `-c`, `--cpus-per-task` | `cpus_per_task` |
| `-G`, `--gpus` | `gpu` |
| `--job-name` | `job_name` |
| `--mail-user` | `mail_user` |
| `--mem` | `memory_per_node` |
| `-t`, `--time` | `runtime` |
| `-p`, `--partition` | `partition` |

Both `#SBATCH -t 05:00:00` and `#SBATCH --time=05:00:00` work.
The archive does not record other forms, e.g. `-J`, `--gres`, `-N` or `--constraint`.
Use `--job-name` instead of `-J`, and `-G` instead of `--gres`, if the archive must record the value.
`##SBATCH` disables a line for Slurm and for the archive.

## 4. Activate a micromamba environment

The archive exports the environment of the job to `environment.yml`.
It reads the first line in the format `micromamba|mamba|conda|source activate <env>`.
The export always uses micromamba, from `$MAMBA_EXE` or from the `PATH`.
A conda or miniforge environment therefore gives a warning and no `environment.yml`.
Activate the environment like this:

```bash
source ~/.bashrc
micromamba activate <environment>
```

## 5. Print the exit code of the job

The slurm output file of the cluster holds no exit code.
Put these lines directly after the `#SBATCH` lines, so that the archive can read the result of the
job, see [The archive](archive.md#the-format-of-a-slurm-output-file):

```bash
# Print the exit code for the archive, see doc/new_template.md.
trap 'exit 143' TERM
trap 'echo "Exit code of the job: $?"' EXIT
```

The EXIT trap prints the exit code of the job.
After a SIGTERM, e.g. at the time limit, bash prints 0, so the TERM trap exits with 143 first.
A test checks that every template has both trap lines.

## 6. Name the input and the output of the job

The deploy step reads these shell variables of the sbatch script before the submission:

| Variable | Check |
|---|---|
| `INPUT` | The input of the job exists. The deploy step checks only the first step of a pipeline |
| `EXTERNAL_INPUT` | An input which no step of the pipeline produces exists. The deploy step checks every step |
| `EXISTING_OUTPUT` | A result which the job builds again does not exist yet. The deploy step checks every step |
| `OUTPUT_FOLDER`, `INPUT_KEY`, `S3_REMOTE` | The deployment module of a project can check them. cochlea-net checks for an earlier prediction and for the rclone remote |

Write a variable as `VAR=value` or `export VAR=value`, as in the shell.
A quoted value can contain a space, and one line can hold several assignments.
The deploy step does not expand a variable in a value, and does not check a value which is not an
absolute path.
A path with a wildcard counts as existing if it has at least one match.
A template without these variables gets no check before the submission.

### Warnings and notes

Each check gives a list of warnings.
The deploy step prints every warning, then the notes of the deployment module.
A warning stops `--submit`, unless `--force` is given.
A note never stops the submission.
A deploy step which stops with an error removes the sbatch scripts which it wrote.
A dry run keeps them.

### The guard of a step

Before the submission, the input of a later step does not exist yet, so the deploy step cannot check
it.
Thus each template checks its own input when the job runs.
A missing input ends the job with an error, and Slurm cancels the rest of the chain:

```bash
# Verify the input of the job. A missing input stops the rest of the pipeline.
for path in "$INPUT" "$OUTPUT_FOLDER"/mask.zarr ; do
	if [ ! -e "$path" ] ; then
		echo "Missing input of the job: $path"
		exit 1
	fi
done
```

A step starts only after the previous step completed successfully, i.e. with exit code 0.
The exit code of a job is the status of its last command.
So a command whose failure must stop the chain needs `|| exit 1` if other commands follow it.

## 7. Name the template

The template name gives the names of the sbatch script and of the archive folder:

| File | Name |
|---|---|
| Template | `templates/<project>/<step>.template` |
| sbatch script, in the current directory | `<date>_sbatch_<step>.sbatch` |
| Log file with the JobID, in the current directory | `<date>_log_<step>.txt` |
| Archive folder, after `--submit` | `<archive_dir>/<date>_<step>/` |
| Slurm output, in the current directory | `slurm-<jobid>.out` |

The deployment module of a project can give the sbatch script another name, e.g. with the cochlea
name.
`--submit` moves the sbatch script and the log file into the archive folder.
Slurm writes `slurm-<jobid>.out` into the directory where the deploy step ran, unless `#SBATCH -o`
names another file.
A second submission on the same day uses the same archive folder, and its log file gets the new
JobID.

## 8. Test the template

Run the deploy step without `--submit`.
This dry run writes the sbatch script into the current directory and submits nothing:

```bash
python scripts/deploy.py --input templates/<project>/<step>.template --params params.json
```

Check the output:

- `Placeholder values of <sbatch script>: {...}` lists every value which the deploy step knows.
- `Warning: ...` names a problem of the input or of a placeholder.
- `Note: ...` gives information of the deployment module.

Then read the sbatch script.
A placeholder without a value stops the dry run, and no sbatch script is written.
For a draft, `--allow-missing` changes this error into a warning.
The sbatch script then keeps the text `<name>`, so the deploy step refuses `--allow-missing` together
with `--submit`.

## cochlea-net: a row in the steps file

A cochlea-net template needs a row in `templates/cochlea-net/steps.json`:

```json
"segment_SGN": {"group": "SGN", "time": "05:30:00", "mem": "400G", "G": {"time": "16:00:00"}}
```

- `group`: `SGN`, `IHC`, `synapses`, or `null` for a template which serves more than one group.
- `time`, `mem`: the values of `#SBATCH -t <time>` and `#SBATCH --mem <mem>` for a mouse cochlea.
- A species code, e.g. `G` for gerbil: the values which differ for this species.

The deploy step stops for a template without a row, and `test/test_pipelines.py` fails.
[Processing pipelines](hpc_03_processing_pipelines.md#placeholders-of-the-deployment) lists the
placeholders which the cochlea-net deployment adds.
