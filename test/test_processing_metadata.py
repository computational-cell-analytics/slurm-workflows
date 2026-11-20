#!/usr/bin/python
# -- coding: utf-8 --
"""Tests of `slurm_wf.add_processing_metadata`: the cochlea, the stains and the model keys of an older job, from its
archived script, from the other jobs of its archive folder, or from the other steps of its pipeline run."""
import contextlib
import io
import json
import os
import tempfile
import unittest

from slurm_workflows.projects.cochlea_net.processing_metadata import add_processing_metadata, run_processing
from stubs import SCRIPTS_DIR, read_file, run, write_file

DATA = "/data/cochlea-lightsheet"
SETTINGS = {"academic_id": "a", "hpc_user": "b",
            "models": {"IHC": {"IHC_v11b": "/path/to/v11"}, "Synapses": {"synapses_v3": "/path/to/v3.pt"}}}


def script(*lines):
    """Return an sbatch script with the given lines after the header."""
    return "\n".join(["#!/bin/bash", "#SBATCH --job-name=job", "source ~/.bashrc"] + list(lines)) + "\n"


def evidence(parts=(), keys=(), stains=()):
    return {"parts": set(parts), "keys": set(keys), "stains": set(stains)}


class ProcessingMetadataTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archive = os.path.join(self.tmp.name, "archive")

        # A new IHC run. Only the mask step names the version. A comment cannot add one. The backfilled
        # job in previous/ has no script, but its folder gives the cochlea.
        self.job("2026-11-18_download_image_IHC_GEK233L", "101", {"run": "101", "step": 1},
                 script("rclone copy remote:bucket/G_EK_000233_L/images/ome-zarr/Vglut3.ome.zarr x"))
        self.job("2026-11-18_mask_IHC_GEK233L", "102", {"run": "101", "step": 2, "depends_on": "101"},
                 script("# The mask of SGN_v2 is not read.",
                        f"export OUTPUT_FOLDER={DATA}/predictions/G_EK_000233_L/IHC_v11b"))
        self.job("2026-11-18_cleanup_input_IHC_GEK233L", "103", {"run": "101", "step": 3, "depends_on": "102"},
                 script(f"rm -r {DATA}/G_EK_000233_L/Vglut3.ome.zarr"))
        write_file(self.metadata_file("2026-11-18_mask_IHC_GEK233L", "99"),
                   json.dumps({"jobid": "99", "backfilled": True}))

        # An older IHC run, which links its steps only with 'depends_on'. The settings have no IHC_v11.
        self.job("2026-11-06_download_image_IHC_MAMD112L", "201", {"step": 1},
                 script("rclone copy remote:bucket/M_AMD_000112_L/images/ome-zarr/Vglut3.ome.zarr x"))
        self.job("2026-11-06_mask_IHC_MAMD112L", "202", {"step": 2, "depends_on": "201"},
                 script(f"export OUTPUT_FOLDER={DATA}/predictions/M_AMD_000112_L/IHC_v11"))

        # A synapse run with two non-default stains. The IHC key and the spelling of the IHC stain come
        # from the mask, the synapse key from the settings.
        self.job("2026-11-19_mask_synapses_MAMDN179L", "301", {"run": "301", "step": 1},
                 script(f"export OUTPUT_FOLDER={DATA}/predictions/M_AMD_N179_L/Homer1_synapse_v3_ihc_myo7a_v11b",
                        "export MASK_PATH=M_AMD_N179_L/images/ome-zarr/Myo7a_IHC_v11b.ome.zarr"))
        self.job("2026-11-19_apply_synapses_MAMDN179L", "302", {"run": "301", "step": 2, "depends_on": "301"},
                 script(f"export OUTPUT_FOLDER={DATA}/predictions/M_AMD_N179_L/Homer1_synapse_v3_ihc_myo7a_v11b"))

        # A synapse version without a key in the settings.
        self.job("2026-11-20_apply_synapses_MAMDN179R", "351", {"run": "351", "step": 1},
                 script(f"export OUTPUT_FOLDER={DATA}/predictions/M_AMD_N179_R/synapse_v5_ihc_v11b"))

        # A folder name from before the cochlea version suffix, and a folder with shell variables, which
        # names no cochlea and no full version.
        self.job("2026-03-16_apply_IHC_MAMDN163L", "361", None,
                 script(f"export OUTPUT_FOLDER={DATA}/predictions/M_AMD_N163_L_v2/IHC_v11b"))
        self.job("2026-08-19_apply_IHC-v11-1_MLR000226L", "371", None,
                 script("VERSION=1", f'export OUTPUT_FOLDER={DATA}/predictions/"$COCHLEA"/IHC_v11-"$VERSION"'))

        # A job without a group, a manual job with a zero-padded name which reads two versions, a job with
        # model keys, and a job which reads two IHC stains. The zero-padded name gives the cochlea.
        self.job("2026-10-26_mobie_add_image_GLR301L", "401", {"run": "401", "step": 1},
                 script("CHANNELS=(PV Vglut3)", "MOBIE_PROJECT=/mobie/G_LR_000301_L"))
        self.job("2026-11-11_object_measures_IHC_MAMD000126L", "501", None,
                 script('echo \'{"dataset_name": "M_AMD_000126_L", "segmentation_channel": "IHC_v11"}\' > x.json',
                        "--compare IHC_v9"))
        self.job("2026-11-19_mask_IHC_GLR234L", "601", {"run": "601", "step": 1},
                 script(f"export OUTPUT_FOLDER={DATA}/predictions/G_LR_000234_L/IHC_v11b"), models={"IHC": "IHC_v11"})
        self.job("2026-11-21_segment_IHC_GLR234R", "701", {"run": "701", "step": 1},
                 script(f"INPUT={DATA}/predictions/G_LR_000234_R/IHC_v11b",
                        f"OUTPUT={DATA}/predictions/G_LR_000234_R/Myo7a_IHC_v11b"))

    def tearDown(self):
        self.tmp.cleanup()

    def metadata_file(self, folder, previous=None):
        if previous is None:
            return os.path.join(self.archive, folder, "metadata.json")
        return os.path.join(self.archive, folder, "previous", previous, "metadata.json")

    def job(self, folder, jobid, pipeline, sbatch, models=None):
        metadict = {"date": folder[:10], "task": folder[11:], "jobid": jobid, "project": "cochlea-net"}
        if models is not None:
            metadict["models"] = models
        if pipeline is not None:
            metadict["pipeline"] = dict(pipeline, name="ihc")
        metadict["slurm_output"] = []
        write_file(self.metadata_file(folder), json.dumps(metadict))
        write_file(os.path.join(self.archive, folder, "sbatch.sbatch"), sbatch)

    def metadata(self, folder, previous=None):
        return json.loads(read_file(self.metadata_file(folder, previous)))

    def contents(self):
        files = {}
        for root_dir, _, names in os.walk(self.archive):
            for name in names:
                files[os.path.join(root_dir, name)] = read_file(os.path.join(root_dir, name))
        return files

    def add(self, pattern=None, dry_run=False):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            add_processing_metadata(self.archive, SETTINGS, pattern, dry_run)
        return output.getvalue()

    def test_entries_come_from_the_script_the_folder_or_the_run(self):
        output = self.add()

        for step in ("download_image", "mask", "cleanup_input"):
            with self.subTest(step):
                metadict = self.metadata(f"2026-11-18_{step}_IHC_GEK233L")
                self.assertEqual((metadict["cochlea"], metadict["stains"], metadict["models"]),
                                 ("G_EK_000233_L", {"IHC": "Vglut3"}, {"IHC": "IHC_v11b"}))
        for step in ("download_image", "mask"):
            with self.subTest(step):
                metadict = self.metadata(f"2026-11-06_{step}_IHC_MAMD112L")
                self.assertEqual((metadict["cochlea"], metadict["models"]), ("M_AMD_000112_L", {"IHC": "IHC_v11"}))
        for step in ("mask", "apply"):
            with self.subTest(step):
                metadict = self.metadata(f"2026-11-19_{step}_synapses_MAMDN179L")
                self.assertEqual(list(metadict["stains"].items()), [("Synapses", "Homer1"), ("IHC", "Myo7a")])
                self.assertEqual(list(metadict["models"].items()), [("Synapses", "synapses_v3"), ("IHC", "IHC_v11b")])

        # The backfilled job gets the cochlea of its folder, but no run gives its model.
        self.assertEqual(self.metadata("2026-11-18_mask_IHC_GEK233L", "99"),
                         {"jobid": "99", "backfilled": True, "cochlea": "G_EK_000233_L"})
        self.assertEqual(self.metadata("2026-10-26_mobie_add_image_GLR301L")["cochlea"], "G_LR_000301_L")
        self.assertNotIn("models", self.metadata("2026-10-26_mobie_add_image_GLR301L"))
        metadict = self.metadata("2026-03-16_apply_IHC_MAMDN163L")
        self.assertEqual((metadict["cochlea"], metadict["models"]), ("M_AMD_N163_L_v2", {"IHC": "IHC_v11b"}))

        # The entries follow 'project', where the deploy step writes them.
        keys = list(self.metadata("2026-11-18_mask_IHC_GEK233L"))
        self.assertEqual(keys[keys.index("project") + 1:keys.index("project") + 5],
                         ["cochlea", "stains", "models", "pipeline"])

        self.assertIn("Added processing keys to 14 job(s).", output)
        self.assertIn("Note: the settings file has no model IHC IHC_v11.", output)

    def test_jobs_without_single_values_get_no_entries(self):
        output = self.add()

        self.assertEqual(self.metadata("2026-11-20_apply_synapses_MAMDN179R")["cochlea"], "M_AMD_N179_R")
        self.assertNotIn("models", self.metadata("2026-11-20_apply_synapses_MAMDN179R"))
        self.assertEqual(self.metadata("2026-11-11_object_measures_IHC_MAMD000126L"),
                         {"date": "2026-11-11", "task": "object_measures_IHC_MAMD000126L", "jobid": "501",
                          "project": "cochlea-net", "cochlea": "M_AMD_000126_L", "slurm_output": []})
        self.assertNotIn("stains", self.metadata("2026-11-21_segment_IHC_GLR234R"))
        metadict = self.metadata("2026-11-19_mask_IHC_GLR234L")
        self.assertEqual((metadict["stains"], metadict["models"]), ({"IHC": "Vglut3"}, {"IHC": "IHC_v11"}))
        self.assertNotIn("models", self.metadata("2026-08-19_apply_IHC-v11-1_MLR000226L"))

        self.assertIn("the settings file has 0 Synapses model keys with the version part 'v5'", output)
        self.assertIn("the scripts name more than one IHC model: v11, v9", output)
        self.assertIn("the scripts name more than one IHC stain: Myo7a, Vglut3", output)
        self.assertIn("keeps its models {'IHC': 'IHC_v11'}. Its scripts give {'IHC': 'IHC_v11b'}", output)
        self.assertIn("No cochlea found for 1 job(s):\n  " + os.path.join(self.archive, "2026-08-19_apply_IHC-v11-1"),
                      output)
        self.assertIn("No model version found for 3 job(s):", output)

    def test_dry_run_writes_nothing(self):
        before = self.contents()
        output = self.add(dry_run=True)

        self.assertEqual(self.contents(), before)
        self.assertIn("Would add processing keys to 14 job(s).", output)

    def test_second_run_changes_nothing(self):
        self.add()
        before = self.contents()

        self.assertIn("Added processing keys to 0 job(s).", self.add())
        self.assertEqual(self.contents(), before)

    def test_pattern_selects_the_jobs_but_the_run_gives_the_version(self):
        self.add(pattern="download_image_IHC_GEK233L")

        self.assertEqual(self.metadata("2026-11-18_download_image_IHC_GEK233L")["models"], {"IHC": "IHC_v11b"})
        self.assertNotIn("models", self.metadata("2026-11-18_mask_IHC_GEK233L"))

    def test_command(self):
        settings_file = write_file(os.path.join(self.tmp.name, "settings.json"), json.dumps(SETTINGS))
        command = ["python", os.path.join(SCRIPTS_DIR, "add_processing_metadata.py"), self.archive, "--dry-run"]

        result = run(command + ["-s", settings_file])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Would add processing keys to 14 job(s).", result.stdout)

        result = run(command + ["-s", os.path.join(self.tmp.name, "missing.json")])
        self.assertEqual(result.returncode, 1)
        self.assertIn("not found", result.stderr)


class RunProcessingTest(unittest.TestCase):

    def test_key_of_a_script_wins_over_the_settings(self):
        found = {"IHC": evidence(["v11b"], ["IHC_v11b"], ["Vglut3"])}
        self.assertEqual(run_processing(found, {"models": {"IHC": {"ihc_v11b": "/path"}}}),
                         ({"IHC": "Vglut3"}, {"IHC": "IHC_v11b"}))

    def test_two_keys_of_one_version_part_are_ambiguous(self):
        found = {"Synapses": evidence(["v3"], stains=["CTBP2"])}
        settings = {"models": {"Synapses": {"synapse_v3": "/path", "synapses_v3": "/path"}}}
        with self.assertRaisesRegex(ValueError, r"2 Synapses model keys .*\['synapse_v3', 'synapses_v3'\]"):
            run_processing(found, settings)

    def test_stain_with_capitals_wins(self):
        found = {"IHC": evidence(["v11b"], ["IHC_v11b"], ["myo7a", "Myo7a"])}
        self.assertEqual(run_processing(found, {})[0], {"IHC": "Myo7a"})
