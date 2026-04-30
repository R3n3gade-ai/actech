"""
ARMS Module: Permanent Tail Risk Hedge (PTRH) Full Automation

This module provides the full, Tier-0 autonomous operation for the PTRH. It
uses the regime-dependent sizing table from THB v4.0 FINAL to determine required
coverage and executes all necessary actions (rolls, resizing, drift correction)
without human intervention.

"Silence is trust in the architecture."

Reference: ARMS THB v4.0 FINAL — Section 6 (PTRH)
"""

import datetime
from dataclasses import dataclass, field
from typing import List, Literal, Optional

from execution.interfaces import Broker
from execution.order_request import OrderRequest
from reporting.audit_log import SessionLogEntry, append_to_log

# --- PTRH Regime Sizing Table (THB v4.0 FINAL, Section 6.2) ---
# Base coverage: 1.2% NAV
# Strike: 10-15% OTM QQQ puts (target ~12.5%)
PTRH_BASE_COVERAGE_PCT = 0.012

# Regime multipliers — Addendum 1 §1.2 Regime Sizing Multiplier Table (CANONICAL)
# Verified against docs/Addendum_1_ARMS_Module_Spec_PTRH_DSHP_v1.0.md lines 141-175.
# Base notional = 1.2% NAV; each multiplier is applied to base.
# CRASH spec: 2.0× / 2.4% — maximum insurance deployment. STRC may HOLD adds
# in CRASH if IV is prohibitive, but the regime sizing multiplier itself is 2.0×.
PTRH_REGIME_TABLE = {
    "RISK_ON":    {"multiplier": 1.00, "coverage_pct": 0.012, "strc_reserve": 0.0},
    "WATCH":      {"multiplier": 1.00, "coverage_pct": 0.012, "strc_reserve": 0.0},
    "NEUTRAL":    {"multiplier": 1.25, "coverage_pct": 0.015, "strc_reserve": 0.50},
    "DEFENSIVE":  {"multiplier": 1.50, "coverage_pct": 0.018, "strc_reserve": 1.00},
    "CRASH":      {"multiplier": 2.00, "coverage_pct": 0.024, "strc_reserve": 1.00},
}


@dataclass
class PTRHInputs:
    nav: float
    regime_label: str
    regime_score: float


def calculate_ptrh_required_notional(inputs: PTRHInputs) -> float:
    """
    Calculate required PTRH put notional using regime table.
    THB v4.0 FINAL: CAM removed from scope. Simple regime multiplier only.
    """
    entry = PTRH_REGIME_TABLE.get(inputs.regime_label, PTRH_REGIME_TABLE["RISK_ON"])
    coverage_pct = entry["coverage_pct"]
    return coverage_pct * inputs.nav


@dataclass
class OptionsPosition:
    ticker: str
    option_type: Literal['PUT', 'CALL']
    strike: float
    expiry: str
    contracts: int
    notional_value: float
    con_id: Optional[int] = None
    delta: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None

    @property
    def dte(self) -> int:
        expiry_date = datetime.datetime.strptime(self.expiry, '%Y-%m-%d').date()
        return (expiry_date - datetime.date.today()).days


@dataclass
class PTRHStatus:
    regime: str
    multiplier: float
    target_notional_pct: float
    actual_notional_pct: float
    coverage_drift_pct: float
    nearest_expiry_dte: int
    last_roll_date: str
    dual_risk_active: bool
    dual_risk_standard_pct: float
    last_action: str
    last_action_timestamp: str
    alerts: List[str] = field(default_factory=list)
    generated_orders: List[OrderRequest] = field(default_factory=list)


def _submit_ptrh_order(
    action: Literal['BUY_PUT', 'SELL_PUT'],
    quantity: float,
    quantity_kind: Literal['CONTRACTS', 'NOTIONAL_USD'],
    signal: str,
    tier: int = 0,
    contract: Optional[OptionsPosition] = None,
) -> OrderRequest:
    order = OrderRequest(
        ticker=contract.ticker if contract else 'QQQ',
        action=action,
        quantity=quantity,
        quantity_kind=quantity_kind,
        con_id=contract.con_id if contract else None,
        strike=contract.strike if contract else None,
        expiry=contract.expiry if contract else None,
        option_right='P' if contract and contract.option_type == 'PUT' else None,
        order_type='LIMIT' if tier == 0 else 'MARKET',
        limit_price=contract.ask if contract and contract.ask else None,
        execution_window_min=30,
        slippage_budget_bps=8.0,
        priority=1,
        triggering_module='PTRH',
        triggering_signal=signal,
        tier=tier,
        confirmation_required=(tier == 1),
        veto_window_hours=0.0,
    )

    append_to_log(SessionLogEntry(
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        action_type='TRADE',
        triggering_module='PTRH',
        triggering_signal=signal,
        correlation_id=order.correlation_id,
        ticker=order.ticker,
        regime_at_action='N/A',
    ))
    print(f"[PTRH] {action} planned for {order.ticker}: {signal} ({quantity} {quantity_kind})")
    return order


def _resolve_delta_primary_strike(broker: Broker, ticker: str, target_delta: float = -0.35) -> Optional[OptionsPosition]:
    if not hasattr(broker, 'get_options_chain') or not hasattr(broker, 'get_market_data'):
        return None

    print(f"[PTRH] Resolving Delta-Primary Strike for {ticker} (Target Delta: {target_delta})")
    try:
        raw_chain = broker.get_options_chain(ticker)
        if not raw_chain:
            print('[PTRH] Chain pull failed.')
            return None

        today = datetime.date.today()

        import ib_insync as ibi

        underlying = getattr(broker, 'ib', None).qualifyContracts(ibi.Stock(ticker, 'SMART', 'USD'))[0] if getattr(broker, 'ib', None) else None
        if not underlying:
            return None
        spot_ticker = broker.get_market_data([underlying])[0]
        spot_price = spot_ticker.marketPrice()

        gates = [
            {'level': 1, 'name': 'STANDARD', 'delta': (-0.40, -0.30), 'dte': (60, 90), 'spread_pct': 0.05},
            {'level': 2, 'name': 'RELAXED', 'delta': (-0.42, -0.28), 'dte': (55, 95), 'spread_pct': 0.07},
            {'level': 3, 'name': 'EXTENDED', 'delta': (-0.45, -0.25), 'dte': (45, 105), 'spread_pct': 0.10},
        ]

        best_contract = None
        active_gate = None
        min_delta_diff = float('inf')

        for gate in gates:
            print(f"[PTRH] Evaluating Gate {gate['level']} ({gate['name']}) constraints...")
            valid_expiries = []
            for contract in raw_chain:
                expiry_date = datetime.datetime.strptime(contract.lastTradeDateOrContractMonth, '%Y%m%d').date()
                dte = (expiry_date - today).days
                if gate['dte'][0] <= dte <= gate['dte'][1]:
                    valid_expiries.append(contract)
            if not valid_expiries:
                continue

            narrowed = [contract for contract in valid_expiries if spot_price * 0.85 <= float(contract.strike) <= spot_price * 0.90]
            tickers = broker.get_market_data(narrowed, require_greeks=True)

            for ticker_data in tickers:
                if not ticker_data.modelGreeks or not ticker_data.modelGreeks.delta:
                    continue

                delta = ticker_data.modelGreeks.delta
                spread = ticker_data.ask - ticker_data.bid if ticker_data.ask and ticker_data.bid else float('inf')
                mid = (ticker_data.bid + ticker_data.ask) / 2 if ticker_data.ask and ticker_data.bid else 1.0
                spread_pct = spread / mid if mid > 0 else float('inf')

                if gate['delta'][0] <= delta <= gate['delta'][1] and spread_pct <= gate['spread_pct']:
                    diff = abs(delta - target_delta)
                    if diff < min_delta_diff:
                        min_delta_diff = diff
                        best_contract = ticker_data

            if best_contract:
                active_gate = gate
                break

        if not best_contract or not active_gate:
            print('[PTRH] Gate 4 Abort: No contracts met Gate 3 (EXTENDED) constraints. True market dislocation detected.')
            append_to_log(SessionLogEntry(
                timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                action_type='PTRH_NO_EXECUTE',
                triggering_module='PTRH',
                triggering_signal='Gate 4 Abort: No compliant options available. PTRH budget preserved in cash sleeve.',
                ticker=ticker,
            ))
            return None

        contract = best_contract.contract
        dte = (datetime.datetime.strptime(contract.lastTradeDateOrContractMonth, '%Y%m%d').date() - today).days
        otm_pct = (spot_price - contract.strike) / spot_price if spot_price else 0.0

        log_message = f"Spot QQQ | Delta {best_contract.modelGreeks.delta:.3f} | OTM% {otm_pct:.2%} | DTE {dte} | Guardrail: Gate {active_gate['level']} ({active_gate['name']})"
        print(f"[PTRH] Calibration Log: {log_message}")

        if active_gate['level'] == 2:
            append_to_log(SessionLogEntry(
                timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                action_type='PTRH_FALLBACK_2',
                triggering_module='PTRH',
                triggering_signal='Gate 2 (RELAXED) invoked. Queue ARMS Architecture Review.',
                ticker=ticker,
            ))
        elif active_gate['level'] == 3:
            append_to_log(SessionLogEntry(
                timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                action_type='PTRH_FALLBACK_3',
                triggering_module='PTRH',
                triggering_signal='PTRH-SPEC-DRIFT Alert: Gate 3 (EXTENDED) invoked. Market snapshot generated. Immediate PM notification required.',
                ticker=ticker,
            ))

        return OptionsPosition(
            ticker=contract.symbol,
            option_type='PUT',
            strike=contract.strike,
            expiry=datetime.datetime.strptime(contract.lastTradeDateOrContractMonth, '%Y%m%d').strftime('%Y-%m-%d'),
            contracts=1,
            notional_value=0.0,
            con_id=contract.conId,
            delta=best_contract.modelGreeks.delta,
            bid=best_contract.bid,
            ask=best_contract.ask,
        )
    except Exception as exc:
        print(f'[PTRH] Error resolving strike: {exc}')
        return None


def _estimate_contracts_for_notional(target_notional: float, contract: Optional[OptionsPosition]) -> int:
    if contract is None:
        return 0
    option_premium = contract.ask or contract.bid
    if option_premium is None or option_premium <= 0:
        return 0
    per_contract_cost = float(option_premium) * 100.0
    if per_contract_cost <= 0:
        return 0
    return max(1, int(round(float(target_notional) / per_contract_cost)))


def run_ptrh_module(
    ptrh_inputs: PTRHInputs,
    current_positions: List[OptionsPosition],
    broker: Optional[Broker] = None,
    dual_risk_override_pct: Optional[float] = None,
) -> PTRHStatus:
    alerts: List[str] = []
    generated_orders: List[OrderRequest] = []
    last_action = 'NONE'

    required_notional = calculate_ptrh_required_notional(ptrh_inputs)
    if dual_risk_override_pct is not None:
        required_notional = ptrh_inputs.nav * dual_risk_override_pct

    regime_entry = PTRH_REGIME_TABLE.get(ptrh_inputs.regime_label, PTRH_REGIME_TABLE["RISK_ON"])
    is_crash = ptrh_inputs.regime_label == 'CRASH'

    actual_notional = sum(position.notional_value for position in current_positions)
    target_pct = required_notional / ptrh_inputs.nav if ptrh_inputs.nav > 0 else 0
    actual_pct = actual_notional / ptrh_inputs.nav if ptrh_inputs.nav > 0 else 0
    drift = (actual_pct / target_pct) - 1 if target_pct > 0 else 0

    print('[PTRH] Scanning QQQ options chain for -0.35 Delta target (Addendum 6 Protocol)...')
    target_contract = None
    if broker and getattr(broker, 'is_connected', False):
        target_contract = _resolve_delta_primary_strike(broker, 'QQQ', -0.35)

    if not target_contract:
        alerts.append('PTRH target contract resolution failed; no compliant live contract returned.')
        append_to_log(SessionLogEntry(
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            action_type='PTRH_TARGET_UNRESOLVED',
            triggering_module='PTRH',
            triggering_signal='No compliant live target contract returned from options scan.',
            ticker='QQQ',
        ))

    roll_occurred = False
    for position in current_positions:
        if position.dte <= 30:
            signal = f'DTE Roll: {position.expiry} is at {position.dte} days. Delta-Optimal Strike Selection (-0.35 tgt) via Gate 1.'
            if target_contract is None:
                alerts.append(f'PTRH roll due for {position.expiry}, but no live replacement contract was resolved.')
                continue
            sell_contracts = max(1, int(abs(position.contracts)))
            buy_contracts = _estimate_contracts_for_notional(position.notional_value, target_contract)
            if buy_contracts <= 0:
                alerts.append('PTRH roll contract sizing failed; no executable contract count derived.')
                continue
            generated_orders.append(_submit_ptrh_order('SELL_PUT', sell_contracts, 'CONTRACTS', signal, contract=position))
            generated_orders.append(_submit_ptrh_order('BUY_PUT', buy_contracts, 'CONTRACTS', signal, contract=target_contract))
            last_action = 'ROLL'
            roll_occurred = True

    if not roll_occurred:
        is_under_hedged = drift < -0.05
        is_over_hedged = drift > 0.15

        if is_under_hedged or is_over_hedged:
            delta_notional = required_notional - actual_notional
            action = 'BUY_PUT' if delta_notional > 0 else 'SELL_PUT'

            if is_crash and action == 'BUY_PUT':
                alerts.append('CRASH regime: implied vol too expensive for new puts. Holding existing coverage.')
            else:
                signal = f'Drift Correction: actual {actual_pct:.2%} vs target {target_pct:.2%}. Delta-Optimal Strike Selected. Delta: ${abs(delta_notional):,.2f}'
                active_contract = target_contract if action == 'BUY_PUT' else (current_positions[0] if current_positions else target_contract)

                if active_contract is None:
                    alerts.append('PTRH drift correction required, but no live contract was available for execution.')
                else:
                    if action == 'BUY_PUT':
                        contract_qty = _estimate_contracts_for_notional(abs(delta_notional), active_contract)
                    else:
                        contract_qty = max(1, int(abs(active_contract.contracts)))

                    if contract_qty <= 0:
                        alerts.append('PTRH drift correction sizing failed; no executable contract count derived.')
                    else:
                        generated_orders.append(_submit_ptrh_order(action, contract_qty, 'CONTRACTS', signal, contract=active_contract))
                        last_action = 'CORRECT_DRIFT'

    nearest_dte = min(position.dte for position in current_positions) if current_positions else 0
    return PTRHStatus(
        regime=ptrh_inputs.regime_label,
        multiplier=regime_entry['multiplier'],
        target_notional_pct=target_pct,
        actual_notional_pct=actual_pct,
        coverage_drift_pct=drift * 100,
        nearest_expiry_dte=nearest_dte,
        last_roll_date=datetime.date.today().isoformat() if last_action == 'ROLL' else 'N/A',
        dual_risk_active=(dual_risk_override_pct is not None),
        dual_risk_standard_pct=dual_risk_override_pct if dual_risk_override_pct else 0.0,
        last_action=last_action,
        last_action_timestamp=datetime.datetime.now().isoformat() if last_action != 'NONE' else '',
        alerts=alerts,
        generated_orders=generated_orders,
    )


if __name__ == '__main__':
    print('ARMS PTRH Module Active (Simulation Mode)')
    test_inputs = PTRHInputs(nav=50_000_000, regime_label='RISK_ON', regime_score=0.25)
    test_pos = [OptionsPosition('QQQ', 'PUT', 400, '2026-05-05', 100, 600_000)]
    res = run_ptrh_module(test_inputs, test_pos)
    print(f'Status: {res}')
