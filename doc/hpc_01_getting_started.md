# Getting started

## What is an HPC project?
Working on the HPC is organized in HPC projects. Each project has a certain run time and consists of different members.
An overview of your projects can be found on the [GWDG HPC Project Portal](https://hpcproject.gwdg.de/projects/views/).
If you go to the project you can see different parameters linked to the project members, e.g. `Name`, `AcademicID`, `Nationality`,and `HPC User`.
The `AcademicID` is the general ID associated with the user for services associated with the GWDG.
The `Nationality` has to be set to work in an HPC project. A notification is send if someone is added to a project and has not yet set their nationality.
The `HPC User` is an identifier which is limited to the extend of the HPC project. It has to be used to log on servers for working with a specific project.

## Connecting to the HPC - SSH connection
Connect to the cluster using an SSH connection. Follow the instructions in the [GWDG HPC docs](https://docs.hpc.gwdg.de/start_here/connecting/index.html).
Some notes for troubleshooting:
* the config file in `C:\Users\your_username\.ssh\config` should NOT have an extension (`config.txt` will not work)
* the `IdentityFile` entry in the config should have the full path `C:\...\id_ed25519` to the private key

## Working on the cluster
A great overview about the work on the cluster is the [GWDG HPC Documentation](https://docs.hpc.gwdg.de/index.html) provided by the GWDG.
It's a great place to look for answers if you have questions going beyond the initial steps provided here.
Some basic information and tips are collected in [HPC 101](hpc_101.md)

## Install micromamba
On the cluster, package and environment managers like `conda` and `micromamba` are used.
I would recommend using `micromamba` as it is faster, particularly when it comes to dependency resolution and package installation.
Follow the instructions in the [docs](https://mamba.readthedocs.io/en/latest/installation/micromamba-installation.html).
This command should be sufficient to install the latest version.
```bash
curl -Ls https://micro.mamba.pm/api/micromamba/$(uname)-$(uname -m)/latest | tar -xvj bin/micromamba
```

## Create micromamba environment for computing

The environment we want to use should support a GPU. It is easier to install it in an interactive environment with an access to GPU resources to install the correct packages which support the GPU infrastructure.
1) Start an interactive environment:
```bash
# an interactive node (preferred)
srun -p grete:interactive -G 1g.10gb -c 4 --constraint=inet -t 01:00:00 --pty bash
# a shared GPU
srun -p grete:shared -G A100:1 --constraint=inet --pty -n 1 -c 4 -t 01:00:00 bash
```
2) Clone the [cochlea-net](https://github.com/computational-cell-analytics/cochlea-net) package:
```bash
# clone cochlea-net repository
git clone https://github.com/computational-cell-analytics/cochlea-net.git
# go to the cloned directory
cd cochlea-net
# install new micromamba environment
micromamba create -f environment.yaml -y
# activate the environment
micromamba activate cochlea-net
# install the CLI functionality
pip install -e .
```

3) Install the [MoBIE utils](https://github.com/mobie/mobie-utils-python) functionality with
```bash
pip install mobie_utils
```
