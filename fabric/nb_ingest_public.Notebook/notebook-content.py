# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {
# META     "lakehouse": {
# META       "default_lakehouse": "f4af17d8-f5b3-4b67-a113-50f079a5efe0",
# META       "default_lakehouse_name": "lh_portfolio",
# META       "default_lakehouse_workspace_id": "c1f74f51-78f9-4cc7-b098-2bada9448793",
# META       "known_lakehouses": [
# META         {
# META           "id": "f4af17d8-f5b3-4b67-a113-50f079a5efe0"
# META         }
# META       ]
# META     }
# META   }
# META }

# CELL ********************

# The wheel's file name follows pyproject.toml's version (tests/test_fabric_items.py checks it).
# This cell comes first because %pip restarts the Python interpreter and drops earlier variables.
# verify in plan 3: that the pipeline-injected parameters cell still lands (and keeps load_date)
# after this restart.
%pip install /lakehouse/default/Files/wheels/lakehouse-0.1.0-py3-none-any.whl

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# # nb_ingest_public
#
# Lands the public source files under bronze for one load date. All logic lives in the
# `lakehouse` wheel; this notebook only installs it and calls `lakehouse.fabric_entry.ingest_public`.

# PARAMETERS CELL ********************

load_date = ""

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

import notebookutils

import lakehouse.fabric_entry

files_written = lakehouse.fabric_entry.ingest_public(load_date, notebookutils.runtime.context)
print(f"ingest_public {load_date}: {files_written} files written")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
