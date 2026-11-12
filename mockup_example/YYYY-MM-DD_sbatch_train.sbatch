#!/bin/bash
# Mockup: not a functioning sbatch script.
# Adapted from https://docs.hpc.gwdg.de/how_to_use/slurm/gpu_usage/index.html
#SBATCH --job-name=train-resnet18_lr0.01
#SBATCH -t 05:00:00
#SBATCH --mail-user=yourmail@gwdg.de
#SBATCH --mail-type=FAIL

#SBATCH -p grete:shared
#SBATCH -G A100:2

# Print the exit code for the archive, see doc/new_template.md.
trap 'exit 143' TERM
trap 'echo "Exit code of the job: $?"' EXIT

source ~/.bashrc
micromamba activate dl-gpu

export INPUT=/path/to/data/cifar10
export OUTPUT_FOLDER=/path/to/data/runs/resnet18_lr0.01

# Verify the input of the job. A missing input stops the rest of the pipeline.
for path in "$INPUT" ; do
	if [ ! -e "$path" ] ; then
		echo "Missing input of the job: $path"
		exit 1
	fi
done

python -u /path/to/my-tool/train.py --input "$INPUT" --output "$OUTPUT_FOLDER"
