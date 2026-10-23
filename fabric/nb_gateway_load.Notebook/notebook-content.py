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
# verify in project 10 plan 2: that the parameters set for the run still land after this restart.
%pip install /lakehouse/default/Files/wheels/lakehouse-0.1.0-py3-none-any.whl

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# MARKDOWN ********************

# # nb_gateway_load
#
# Project 10's gateway load. It runs once, by hand, after the session (the day after, once the cost
# export has the session's day), and never from the pipeline: a one-off session dataset must not
# change what project 9's daily load guards.
#
# Set `session` to the session's name and `eventhouse_count` to the Eventhouse's distinct
# `request_id` count for that session (from `GatewayCalls()`). It builds silver `gateway_calls` from
# the gateway shortcut, gold `fact_gateway_call`, checks the count and prints cost per gateway call.
# All logic lives in the `lakehouse` wheel; this notebook only installs it and calls
# `lakehouse.fabric_entry.load_gateway`.

# PARAMETERS CELL ********************

session = ""
eventhouse_count = ""

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

import json

import notebookutils

import lakehouse.fabric_entry

summary = lakehouse.fabric_entry.load_gateway(session, eventhouse_count, notebookutils.runtime.context)
print(json.dumps(summary, indent=2, sort_keys=True, default=str))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
