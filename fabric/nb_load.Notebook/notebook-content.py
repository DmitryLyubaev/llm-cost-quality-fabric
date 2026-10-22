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

# MARKDOWN ********************

# # nb_load
#
# Runs the lakehouse load (bronze to silver to gold) for one load date. All logic lives in the
# `lakehouse` wheel; this notebook only installs it and calls `lakehouse.fabric_entry.load`.

# PARAMETERS CELL ********************

load_date = ""
wheel = "lakehouse-0.1.0-py3-none-any.whl"

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

# verify in plan 3: that %pip expands {wheel} here (else use
# get_ipython().run_line_magic("pip", f"install /lakehouse/default/Files/wheels/{wheel}")), and
# that the Python restart %pip triggers keeps load_date (Microsoft documents that variables
# defined before an inline install are lost).
%pip install /lakehouse/default/Files/wheels/{wheel}

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }

# CELL ********************

import json

import notebookutils

import lakehouse.fabric_entry

summary = lakehouse.fabric_entry.load(load_date, notebookutils.runtime.context)
print(json.dumps(summary, indent=2, sort_keys=True, default=str))

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "synapse_pyspark"
# META }
