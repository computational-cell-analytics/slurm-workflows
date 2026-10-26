#!/usr/bin/python
# -- coding: utf-8 --
"""author: Martin Schilling (martin.schilling@med.uni-goettingen.de), 2026

Standalone entry point of `slurm_wf.summary`, which works without an installation of `slurm_workflows`.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from slurm_workflows.cli import summary  # noqa: E402

if __name__ == "__main__":
    summary()
