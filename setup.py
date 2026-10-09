from setuptools import setup, find_packages

setup(
    name="slurm_workflows",
    packages=find_packages(exclude=["test"]),
    version="0.0.1",
    author="Martin Schilling",
    license="MIT",
    entry_points={
        "console_scripts": [
            "slurm_wf.deploy = slurm_workflows.cli:deploy",
            "slurm_wf.write_metadata = slurm_workflows.cli:write_metadata",
            "slurm_wf.update_metadata = slurm_workflows.cli:update_metadata",
            "slurm_wf.submit = slurm_workflows.cli:submit",
            "slurm_wf.archive = slurm_workflows.cli:archive",
        ]
    },
)
