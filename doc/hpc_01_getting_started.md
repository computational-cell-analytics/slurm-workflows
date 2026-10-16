# Getting started

## What is an HPC project?
The GWDG organizes the work on the HPC in HPC projects. Each project has a run time and members.
The [GWDG HPC Project Portal](https://hpcproject.gwdg.de/projects/views/) shows your projects and the `Name`, `AcademicID`, `Nationality` and `HPC User` of each member.
The `AcademicID` is your ID for all services of the GWDG. The `HPC User` is your ID in one HPC project only.

> [!IMPORTANT]
> Set your `Nationality` before you work in an HPC project. Until you set it, you get a notification when someone adds you to a project.
> Use the `HPC User` of the project to log in to the cluster for that project.

> [!TIP]
> The [GWDG HPC Documentation](https://docs.hpc.gwdg.de/index.html) answers questions beyond the first steps of this guide. [HPC 101](hpc_101.md) collects basic commands and tips.
## Connecting to the HPC - SSH connection
Connect to the cluster with SSH. Follow the instructions in the [GWDG HPC docs](https://docs.hpc.gwdg.de/start_here/connecting/index.html).

> [!TIP]
> On Windows, the file `C:\Users\your_username\.ssh\config` must have no extension. The name `config.txt` does not work.
> The `IdentityFile` entry of the config must contain the full path to the private key, e.g. `C:\...\id_ed25519`.

## Install micromamba
> [!TIP]
> Use `micromamba` instead of `conda`. It resolves dependencies and installs packages faster.

Follow the [installation instructions](https://mamba.readthedocs.io/en/latest/installation/micromamba-installation.html). This command installs the latest version:
```bash
curl -Ls https://micro.mamba.pm/api/micromamba/$(uname)-$(uname -m)/latest | tar -xvj bin/micromamba
```

## Create micromamba environment for computing

> [!IMPORTANT]
> Create the environment in an interactive session with a GPU. Then `micromamba` selects the packages that support the GPU.

1) Start an interactive session:
```bash
# an interactive node (preferred)
srun -p grete:interactive -G 1g.10gb -c 4 --constraint=inet -t 01:00:00 --pty bash
# a shared GPU
srun -p grete:shared -G A100:1 --constraint=inet --pty -n 1 -c 4 -t 01:00:00 bash
```
2) Clone the [cochlea-net](https://github.com/computational-cell-analytics/cochlea-net) package and install it:
```bash
git clone https://github.com/computational-cell-analytics/cochlea-net.git
cd cochlea-net
# create and activate the environment
micromamba create -f environment.yaml -y
micromamba activate cochlea-net
# install the CLI commands
pip install -e .
```

3) Install the [MoBIE utils](https://github.com/mobie/mobie-utils-python) in the same environment:
```bash
pip install mobie_utils
```
