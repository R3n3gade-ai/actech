"""
ARMS Historical Simulation & Backtesting Engine
================================================
Feeds historical market data directly into production ARMS modules
to validate defensive architecture performance.

Phases:
  Phase 1 — Static book (single asset + ARAS/PDS)
  Phase 2 — Full Architecture AB (all modules wired)
"""

from simulation.historical_engine_phase1 import run_simulation_phase1
from simulation.historical_engine_phase2 import run_simulation_phase2
from simulation.tearsheet import generate_tearsheet

__all__ = [
    "run_simulation_phase1",
    "run_simulation_phase2",
    "generate_tearsheet",
]
