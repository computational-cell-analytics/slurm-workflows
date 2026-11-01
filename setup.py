from setuptools import setup, find_packages

setup(
    name="slurm_workflows",
    packages=find_packages(exclude=["test"]),
    version="0.0.1",
    author="Martin Schilling",
    license="MIT",
    entry_points={
        "console_scripts": [
            "slurm_wf.deploy = slurm_workflows.cli:main_deploy",
            "slurm_wf.write_metadata = slurm_workflows.cli:main_write_metadata",
            "slurm_wf.update_metadata = slurm_workflows.cli:main_update_metadata",
            "slurm_wf.summary = slurm_workflows.cli:main_summary",
            "slurm_wf.add_s3_dataset = slurm_workflows.projects.cochlea_net.cli:main_add_s3_dataset",
            "slurm_wf.submit = slurm_workflows.cli:main_submit",
            "slurm_wf.archive = slurm_workflows.cli:main_archive",
        ]
    },
)
