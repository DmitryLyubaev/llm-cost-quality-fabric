# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "synapse_pyspark"
# META   },
# META   "dependencies": {
# META     "lakehouse": {
# META       "default_lakehouse": "cada6955-c418-48c4-8a60-6c9772dd39f2",
# META       "default_lakehouse_name": "lh_portfolio",
# META       "default_lakehouse_workspace_id": "00000000-0000-0000-0000-000000000000",
# META       "known_lakehouses": [
# META         {
# META           "id": "cada6955-c418-48c4-8a60-6c9772dd39f2"
# META         }
# META       ]
# META     }
# META   }
# META }

# CELL ********************

# The wheel's file name follows pyproject.toml's version (tests/test_fabric_items.py checks it).
# This cell comes first because %pip restarts the Python interpreter and drops earlier variables.
%pip install /lakehouse/default/Files/wheels/lakehouse-0.1.0-py3-none-any.whl

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# # nb_access_load
#
# Project 11's access load for row-level security. It runs by hand, never from the pipeline, after
# the git-ignored `private/access.csv` (`upn,project`) is uploaded to `Files/private/access/` (the
# folder must hold that one CSV), and again whenever the file changes.
#
# It overwrites `security.user_project`, which the semantic model's `ProjectLead` role filters on.
# UPNs are stored lower-case. A bad row (a blank UPN, an unknown project or a duplicate) stops the
# load before anything is written, and the error names the line number only. The output is counts
# only, never a UPN. All logic lives in the `lakehouse` wheel; this notebook only installs it and
# calls `lakehouse.fabric_entry.load_access`.

# CELL ********************

import json

import notebookutils

import lakehouse.fabric_entry

summary = lakehouse.fabric_entry.load_access(notebookutils.runtime.context)
print(json.dumps(summary, indent=2, sort_keys=True))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
