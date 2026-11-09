# Getting started

## The HPC project

The GWDG organizes the work on the HPC in HPC projects. Each HPC project has a run time and members.
The [GWDG HPC Project Portal](https://hpcproject.gwdg.de/projects/views/) shows your HPC projects and the `Name`, `AcademicID`, `Nationality` and `HPC User` of each member.
The `AcademicID` is your ID for all services of the GWDG. The `HPC User` is your ID in one HPC project only.

> [!IMPORTANT]
> Set your `Nationality` before you work in an HPC project. Without it, the admin of an HPC project cannot add you as a member. You only get a notification.
> Use the `HPC User` of the HPC project to log in to the cluster for that HPC project.

> [!TIP]
> The [GWDG HPC Documentation](https://docs.hpc.gwdg.de/index.html) answers questions beyond the first steps of this guide. [HPC 101](hpc_101.md) collects basic commands and tips.

## Connect to the cluster

Connect to the cluster with SSH. Follow the instructions in the [GWDG HPC docs](https://docs.hpc.gwdg.de/start_here/connecting/index.html).

> [!TIP]
> On Windows, the file `C:\Users\your_username\.ssh\config` must have no extension. The name `config.txt` does not work.
> The `IdentityFile` entry of the config must contain the full path to the private key, e.g. `C:\...\id_ed25519`.

## Install micromamba

> [!TIP]
> Use `micromamba` instead of `conda`. It resolves dependencies and installs packages faster.
> The archive of a job exports only a micromamba environment.

Follow the [installation instructions](https://mamba.readthedocs.io/en/latest/installation/micromamba-installation.html).
These commands install the latest version into `~/bin/micromamba`, and add micromamba to your `~/.bashrc`:

```bash
cd ~
curl -Ls https://micro.mamba.pm/api/micromamba/$(uname)-$(uname -m)/latest | tar -xvj bin/micromamba
./bin/micromamba shell init -s bash -r ~/micromamba
source ~/.bashrc
```

The shell init is necessary.
Each job runs `source ~/.bashrc` and `micromamba activate`, and the archive finds micromamba through the variable `MAMBA_EXE`, which the shell init sets.

## Create the micromamba environment

> [!IMPORTANT]
> Create the environment in an interactive session with a GPU. Then `micromamba` selects the packages that support the GPU.

1. Start an interactive session.
   `--constraint=inet` selects a node with internet access, which the download of the packages needs.
   ```bash
   # an interactive node (preferred)
   srun -p grete:interactive -G 1g.10gb -c 4 --constraint=inet -t 01:00:00 --pty bash
   # a shared GPU
   srun -p grete:shared -G A100:1 --constraint=inet --pty -n 1 -c 4 -t 01:00:00 bash
   ```
2. Clone the [cochlea-net](https://github.com/computational-cell-analytics/cochlea-net) package into a folder of your choice, and install it.
   This guide writes `/path/to/cochlea-net` for the clone.
   ```bash
   cd /path/to
   git clone https://github.com/computational-cell-analytics/cochlea-net.git
   cd cochlea-net
   # create and activate the environment
   micromamba create -f environment.yaml -y
   micromamba activate cochlea-net
   # install the CLI commands
   pip install -e .
   ```

`environment.yaml` creates the environment `cochlea-net`, which also holds the [MoBIE utils](https://github.com/mobie/mobie-utils-python) of the MoBIE jobs.
The processing jobs call the commands of cochlea-net, e.g. `flamingo_tools.run_unet_prediction_slurm`.
The editable install runs the code of the clone, so the git hash in the archive matches it.
Run `pip install -e .` again after a pull which adds a command.

## Clone slurm-workflows and create the settings file

Clone [slurm-workflows](https://github.com/computational-cell-analytics/slurm-workflows) into a folder of your choice.
This guide writes `/path/to/slurm-workflows` for the clone.
Copy the blueprint of the settings file:

```bash
cd /path/to
git clone https://github.com/computational-cell-analytics/slurm-workflows.git
cd slurm-workflows
cp project_settings/cochlea-net.blueprint.json project_settings/cochlea-net.json
```

Adapt the values of `project_settings/cochlea-net.json` to your account, see [The settings file](hpc_03_processing_pipelines.md#the-settings-file).
Two values must match the steps above:

- `cochlea-net_environment` and `mobie_environment` must be the name of the environment, i.e. `cochlea-net`.
- `repositories.cochlea-net` must be the path of your cochlea-net clone, because the archive reads the git hash there.

`scripts/deploy.py` runs with the Python 3.6 of the login node, so it needs no environment.
It also runs with the Python of an active environment.
The archive calls `reportseff` for the efficiency report of a job.
`reportseff` is often available on the cluster. If it is missing, run `module load py-reportseff`.
