#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2025

Standalone entry point of `slurm_wf.write_metadata`, which works without an installation of `slurm_workflows`.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from slurm_workflows.cli import write_metadata  # noqa: E402

if __name__ == "__main__":
    write_metadata()
