"""
ARMS Historical Simulation & Backtesting Engine
================================================
Drives the PRODUCTION engine modules over historical bars. The replay harness
calls ``src/engine/*`` directly — no inlined logic, no parallel engine.

Modules:
  - replay_harness  : production-engine driver (current backtest engine)
  - historical_engine_phase1 : retained legacy single-asset baseline (TODO: retire)
  - tearsheet       : tearsheet renderer
  - pdf_report      : institutional PDF report renderer
"""

def run_simulation_phase1(*args, **kwargs):
  from simulation.historical_engine_phase1 import run_simulation_phase1 as _run
  return _run(*args, **kwargs)


def run_backtest(*args, **kwargs):
  from simulation.replay_harness import run_backtest as _run
  return _run(*args, **kwargs)


def run_simulation_phase2(*args, **kwargs):
  from simulation.replay_harness import run_simulation_phase2 as _run
  return _run(*args, **kwargs)


def generate_tearsheet(*args, **kwargs):
  from simulation.tearsheet import generate_tearsheet as _generate
  return _generate(*args, **kwargs)

__all__ = [
    "run_simulation_phase1",
    "run_backtest",
    "run_simulation_phase2",  # deprecated alias → run_backtest
    "generate_tearsheet",
]
