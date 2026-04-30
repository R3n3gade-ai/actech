"""
ARMS Configuration: Defensive Sleeve Harvest Protocol (DSHP)

This file contains the configuration constants for the DSHP module, including
harvest thresholds, target weights, and veto window settings. These can be
adjusted by the PM without requiring code changes.

Reference: ARMS Module Specification — PTRH Automation + DSHP
"""

# --- Instrument-Specific Rules ---

# SGOL (Gold)
SGOL_TARGET_WEIGHT = 0.020        # 2.0% of NAV
SGOL_HARVEST_THRESHOLD = 0.200   # Harvest if >20% appreciation from entry

# DBMF (Managed Futures)
DBMF_TARGET_WEIGHT = 0.050         # 5.0% of NAV
DBMF_HARVEST_THRESHOLD = 0.150   # Harvest if >15% appreciation from entry
DBMF_DRIFT_THRESHOLD = 0.015     # Or, harvest if weight drifts >1.5pp above target

# STRC and SGOV have no harvest triggers.
# SGOV acts as the receiver for harvested proceeds.

# --- Tier 1 Confirmation Queue Settings ---

DSHP_VETO_WINDOW_HOURS = 4.0     # PM has 4 hours to respond before auto-execution

DSHP_VETO_GP_ALERT_THRESHOLD = 2 # GP review is triggered if >2 vetoes in 30 days
