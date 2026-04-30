"""
ARMS Execution: Circuit Breaker

This module monitors for extreme, sudden market panics and halts automated 
execution if thresholds are crossed. It acts as the ultimate failsafe before LAEP.

Reference: Codebase Game Plan v2.0 (Step 3: Behavioral Correctness)
"""
import datetime
from dataclasses import dataclass
from typing import Optional
from reporting.audit_log import SessionLogEntry, append_to_log

@dataclass
class CircuitBreakerStatus:
    is_tripped: bool
    reason: Optional[str] = None
    tripped_at: Optional[str] = None

class CircuitBreaker:
    def __init__(self):
        self.status = CircuitBreakerStatus(is_tripped=False)
        self.spx_open = 0.0
        self.spx_current = 0.0
        self.vix_open = 0.0
        self.vix_current = 0.0

    def update_market_data(self, spx_open: float, spx_current: float, vix_open: float, vix_current: float):
        self.spx_open = spx_open
        self.spx_current = spx_current
        self.vix_open = vix_open
        self.vix_current = vix_current

    def evaluate(self) -> CircuitBreakerStatus:
        """
        Evaluates hard fail-safes.
        If SPX drops > 5% intraday, or VIX spikes > 30% intraday, halt autonomous trading.
        """
        if self.status.is_tripped:
            return self.status # Remains tripped until manual reset

        spx_drop = (self.spx_current / self.spx_open) - 1.0 if self.spx_open > 0 else 0.0
        vix_spike = (self.vix_current / self.vix_open) - 1.0 if self.vix_open > 0 else 0.0

        if spx_drop <= -0.05:
            self._trip(f"SPX Intraday Drop exceeds 5% ({spx_drop:.2%})")
        elif vix_spike >= 0.30:
            self._trip(f"VIX Intraday Spike exceeds 30% ({vix_spike:.2%})")

        return self.status

    def _trip(self, reason: str):
        self.status.is_tripped = True
        self.status.reason = reason
        self.status.tripped_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        print(f"[CIRCUIT_BREAKER] 🛑 HALT TRIGGERED: {reason}")
        
        append_to_log(SessionLogEntry(
            timestamp=self.status.tripped_at,
            action_type='CIRCUIT_BREAKER_TRIP',
            triggering_module='CIRCUIT_BREAKER',
            triggering_signal=f"Trading Halted: {reason}"
        ))

    def reset(self, pm_rationale: str):
        """PM manual override to resume trading."""
        print(f"[CIRCUIT_BREAKER] 🟢 RESET BY PM: {pm_rationale}")
        append_to_log(SessionLogEntry(
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            action_type='CIRCUIT_BREAKER_RESET',
            triggering_module='CIRCUIT_BREAKER',
            triggering_signal=f"System resumed. Rationale: {pm_rationale}",
            pm_override=True,
            override_rationale=pm_rationale
        ))
        self.status = CircuitBreakerStatus(is_tripped=False)
