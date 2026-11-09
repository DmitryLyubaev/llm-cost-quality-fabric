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
%pip install /lakehouse/default/Files/wheels/lakehouse-0.1.0-py3-none-any.whl

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# # nb_model_check
#
# Project 11's model-versus-gold check. Run it by hand, as a user (the owner, signed in), after the
# daily load and the model's refresh, and never from the pipeline: semantic link's `evaluate_dax` is
# not supported under a service principal's token, and a pipeline deployed by the service principal
# runs its notebooks as the service principal.
#
# It runs DAX queries against `sm_portfolio` as you, then as each lead in `security.user_project`
# (`effective_user_name`), and compares them with the package's gold answers: counts exactly, money
# and rates within 0.005. As each lead, only their own projects may show. Set `sessions` to a
# comma-separated list of gateway sessions to compare cost per gateway call too; leave it empty to
# skip that answer. It never prints a UPN: leads are named by their projects. A difference raises
# `QualityCheckFailed` naming the measure, the key and both values. All logic lives in the
# `lakehouse` wheel; this notebook only installs it and calls `lakehouse.fabric_entry.check_model`.

# PARAMETERS CELL ********************

sessions = ""

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

import json

import notebookutils

import lakehouse.fabric_entry

summary = lakehouse.fabric_entry.check_model(notebookutils.runtime.context, sessions)
print(json.dumps(summary, indent=2, sort_keys=True))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
