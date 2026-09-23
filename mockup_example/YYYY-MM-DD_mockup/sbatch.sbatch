#!/bin/bash
#SBATCH --job-name=train-nn-gpu
#SBATCH -t 05:00:00                  # estimated time, adapt to your needs
#SBATCH --mail-user=yourmail@gwdg.de # change this to your mailaddress
#SBATCH --mail-type=all              # send mail when job begins and ends
 
 
#SBATCH -p grete:shared              # the partition
#SBATCH -G A100:2                    # For requesting 2 GPUs.
 
module load miniforge3
module load cuda
source activate dl-gpu # Or whatever you called your miniforge/conda environment.
 
# Print out some info.
echo "Submitting job with sbatch from directory: ${SLURM_SUBMIT_DIR}"
echo "Home directory: ${HOME}"
echo "Working directory: $PWD"
echo "Current node: ${SLURM_NODELIST}"
 
# For debugging purposes.
python --version
python -m torch.utils.collect_env
nvcc -V
 
# Run the script:
python -u train.py
