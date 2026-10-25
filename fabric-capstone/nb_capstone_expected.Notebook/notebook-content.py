# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {
# META     "lakehouse": {
# META       "default_lakehouse": "36b24c0b-c765-42bc-acf8-ae03c14eb962",
# META       "default_lakehouse_name": "lh_capstone",
# META       "default_lakehouse_workspace_id": "00000000-0000-0000-0000-000000000000",
# META       "known_lakehouses": [
# META         {
# META           "id": "36b24c0b-c765-42bc-acf8-ae03c14eb962"
# META         }
# META       ]
# META     }
# META   }
# META }

# CELL ********************

# The wheel's file name follows pyproject.toml's version (tests/test_capstone_snapshot.py checks it).
# This cell comes first because %pip restarts the Python interpreter and drops earlier variables.
# The wheel is uploaded to lh_capstone's Files/wheels/, since lh_capstone is this notebook's default.
%pip install /lakehouse/default/Files/wheels/lakehouse-0.1.0-py3-none-any.whl

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# # nb_capstone_expected
#
# The capstone's expected answers (spec 4.4). It runs by hand after the snapshot, in the capstone
# workspace as its admin; never from a pipeline. Upload the frozen `capstone/questions.json` to
# `Files/capstone/questions.json` first. It computes each question's expected answer from
# `lh_capstone`'s gold with `compute_expected` (an answer function plus a selector, never a typed
# value) and writes them beside the questions as `Files/capstone/expected.json`, the bytes the
# freeze hashes. All logic lives in the `lakehouse` wheel; this notebook only installs it and calls
# `lakehouse.fabric_entry.capstone_expected`.

# CELL ********************

import json

import notebookutils

import lakehouse.fabric_entry

summary = lakehouse.fabric_entry.capstone_expected(notebookutils.runtime.context)
print(json.dumps(summary, indent=2, sort_keys=True, default=str))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
