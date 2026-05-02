"""
ARMS Engine: ARAS Composite Risk Assessor (L3)

The highest authority on gross portfolio exposure. Consumes the 
Macro Compass score and applies the canonical equity ceiling limits.

EDR Advisory Integration (Addendum 7/8):
  Deleveraging Risk and Crypto Microstructure sub-modules provide
  advisory contributions to the composite score. Max individual: +0.06.
  Max dual-module combined: +0.12. Neither module can force a regime
  change independently. Advisory pressure only — never relaxes ceiling.

Canonical thresholds from the briefing corpus:
    Boundaries: 0.30, 0.50, 0.65, 0.80
    Escalation hold: 3 consecutive 5-minute observations
    De-escalation hold: 2 consecutive 5-minute observations
    CRASH exception: immediate on any single >= 0.81 print

Reference: THB v4.0 + GP briefing reconciliation
"""
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ArasOutput:
    regime: str
    score: float
    equity_ceiling_pct: float
    dual_edr_alert: bool = False
    edr_advisory_total: float = 0.0

# ─── Canonical regime definitions (CLAUDE.md v4.0) ───
REGIME_NAMES    = ["RISK_ON", "WATCH", "NEUTRAL", "DEFENSIVE", "CRASH"]
REGIME_CEILINGS = [1.00,      1.00,    0.75,      0.40,        0.15]
REGIME_BOUNDARIES = [0.30, 0.50, 0.65, 0.80]  # 4 boundaries → 5 regimes

UPGRADE_CONFIRMATIONS = 3
DOWNGRADE_CONFIRMATIONS = 2


def _target_regime_idx(score: float) -> int:
    if score <= 0.30:
        return 0
    if score <= 0.50:
        return 1
    if score <= 0.65:
        return 2
    if score <= 0.80:
        return 3
    return 4


def _edr_advisory_contribution(score: float) -> float:
    """
    Per Addendum 7/8 Section 5 contribution table.
    Maps an EDR sub-module score to its advisory contribution.
    Max individual contribution: +0.06.
    """
    if score >= 0.75:
        return 0.06
    if score >= 0.60:
        return 0.04
    if score >= 0.40:
        return 0.02
    return 0.0


class ARASAssessor:
    """
    Stateful ARAS regime assessor with consecutive-hold confirmation.

    EDR advisory contributions (Addendum 7/8 Section 5):
    Sub-module scores add to the composite before regime mapping.
    Dual-module alert fires when both exceed 0.60 simultaneously.
    """

    def __init__(self):
        self._regime_idx = 0  # Start at RISK_ON
        self._pending_target_idx = None
        self._pending_direction = None
        self._pending_count = 0

    @property
    def current_regime(self) -> str:
        return REGIME_NAMES[self._regime_idx]

    def assess(self, regime_score: float,
               delev_score: float = 0.0,
               micro_score: float = 0.0) -> ArasOutput:
        # ── EDR advisory contributions (Addendum 7/8 Section 5) ──
        delev_advisory = _edr_advisory_contribution(delev_score)
        micro_advisory = _edr_advisory_contribution(micro_score)
        edr_total = delev_advisory + micro_advisory

        # Dual-module alert: both > 0.60 (Addendum 8 Section 5)
        dual_alert = delev_score > 0.60 and micro_score > 0.60
        if dual_alert:
            logger.warning(
                "[ARAS] DUAL EDR ALERT: delev=%.3f micro=%.3f combined_advisory=+%.3f",
                delev_score, micro_score, edr_total
            )

        # Apply advisory pressure (upward only — never relaxes)
        adjusted_score = min(1.0, regime_score + edr_total)

        target_idx = _target_regime_idx(adjusted_score)

        if target_idx == 4:
            # CRASH: instant transition (spec — single >=0.81 print)
            self._regime_idx = 4
            self._pending_target_idx = None
            self._pending_direction = None
            self._pending_count = 0
        elif target_idx == self._regime_idx:
            self._pending_target_idx = None
            self._pending_direction = None
            self._pending_count = 0
        else:
            # ── ADJACENT-BAND STEPPING (spec: each transition is its own gate) ──
            # Source: ARMS_Auto_Deployment_Execution_v1.1 §6.1 lines 71-72:
            #   "Upgrade (e.g. DEFENSIVE → NEUTRAL): score must hold below upper
            #    boundary for 3 consecutive cycles"
            #   "Downgrade (e.g. NEUTRAL → DEFENSIVE): score must breach boundary
            #    for 2 consecutive cycles"
            # The examples are between ADJACENT regimes. A multi-band jump (e.g.
            # CRASH → WATCH) MUST traverse each intermediate band with its own
            # confirmation hold, otherwise DEFENSIVE/NEUTRAL get skipped on
            # rapid recoveries — observed in 2025 backtest where 4 CRASH days
            # downgraded directly to WATCH, registering 0 DEFENSIVE days during
            # a 22% drawdown.
            #
            # Semantic note: spec "upgrade" = becoming LESS defensive (lower
            # idx, e.g. DEFENSIVE→NEUTRAL = 3→2). Spec "downgrade" = becoming
            # MORE defensive (higher idx, e.g. NEUTRAL→DEFENSIVE = 2→3).
            # Defense is FASTER than offense (2 confirms vs 3) — asymmetric
            # by design per spec line 72.
            going_more_defensive = target_idx > self._regime_idx  # idx increases
            # Step exactly one band toward target.
            step_idx = self._regime_idx + (1 if going_more_defensive else -1)
            needed = (DOWNGRADE_CONFIRMATIONS if going_more_defensive
                      else UPGRADE_CONFIRMATIONS)
            direction = 'MORE_DEF' if going_more_defensive else 'LESS_DEF'

            if self._pending_target_idx == step_idx and self._pending_direction == direction:
                self._pending_count += 1
            else:
                self._pending_target_idx = step_idx
                self._pending_direction = direction
                self._pending_count = 1

            if self._pending_count >= needed:
                self._regime_idx = step_idx
                # Reset pending; next assess() will re-evaluate against the new
                # regime. If target is still further away, a new pending streak
                # begins on the next call.
                self._pending_target_idx = None
                self._pending_direction = None
                self._pending_count = 0

        return ArasOutput(
            regime=REGIME_NAMES[self._regime_idx],
            score=adjusted_score,
            equity_ceiling_pct=REGIME_CEILINGS[self._regime_idx],
            dual_edr_alert=dual_alert,
            edr_advisory_total=edr_total,
        )


def calculate_aras_ceiling(regime_score: float) -> ArasOutput:
    """
    STATELESS regime mapping (backward-compatible).
    Uses canonical GP thresholds but NO hysteresis or persistence.
    For full spec compliance, use ARASAssessor.assess() instead.
    """
    if regime_score < 0.30:
        regime = "RISK_ON"
        ceiling = 1.00
    elif regime_score <= 0.50:
        regime = "WATCH"
        ceiling = 1.00
    elif regime_score <= 0.65:
        regime = "NEUTRAL"
        ceiling = 0.75
    elif regime_score <= 0.80:
        regime = "DEFENSIVE"
        ceiling = 0.40
    else:
        regime = "CRASH"
        ceiling = 0.15

    return ArasOutput(regime=regime, score=regime_score, equity_ceiling_pct=ceiling)
