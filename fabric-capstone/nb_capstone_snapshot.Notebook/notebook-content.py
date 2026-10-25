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

# # nb_capstone_snapshot
#
# The capstone's gold snapshot (spec 4.1). It runs by hand, once, on the day of the freeze, in the
# capstone workspace as its admin, who also needs read access to prod; never from a pipeline. It
# copies prod `lh_portfolio`'s gold tables into this notebook's default lakehouse, `lh_capstone`, as
# a plain Delta copy, and writes the snapshot time and row counts to `Files/capstone/snapshot.json`.
# The data agent reads only `lh_capstone`, and the expected answers are computed from the same
# snapshot.
#
# Set `source_workspace_id` to the prod workspace's ID and `source_lakehouse_id` to prod
# `lh_portfolio`'s item ID when the notebook is run; they are never committed. Leave `tables` empty
# for the nine gold tables the data agent selects, or give a comma-separated subset. All logic lives
# in the `lakehouse` wheel; this notebook only installs it and calls
# `lakehouse.fabric_entry.snapshot_gold`.

# PARAMETERS CELL ********************

source_workspace_id = ""
source_lakehouse_id = ""
tables = ""

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

import json

import notebookutils

import lakehouse.fabric_entry

summary = lakehouse.fabric_entry.snapshot_gold(source_workspace_id, source_lakehouse_id, notebookutils.runtime.context, tables)
print(json.dumps(summary, indent=2, sort_keys=True, default=str))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
