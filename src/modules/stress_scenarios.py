"""
ARMS Engine: Stress Scenario Library (SSL)

Runs daily after portfolio snapshot. Applies configured historical and 
custom stress scenarios to current portfolio weights to estimate P&L.

"Silence is trust in the architecture."
"""
from dataclasses import dataclass, field
from typing import List, Dict
import datetime
import json
import os
from engine.bridge_paths import bridge_path

@dataclass
class ScenarioPnL:
    scenario_id: str
    scenario_name: str
    portfolio_pnl_pct: float
    portfolio_pnl_usd: float
    hedge_offset_usd: float
    net_pnl_after_hedge: float

@dataclass
class ScenarioResults:
    as_of: str
    current_nav: float
    scenarios: List[ScenarioPnL]
    worst_scenario: str
    worst_net_loss_pct: float

def _load_scenarios() -> Dict[str, dict]:
    """
    Loads stress scenarios dynamically from a configurable JSON file rather 
    than relying on hardcoded constants. Real institutional risk systems 
    do not hardcode shock matrices.
    """
    # Look for the configuration in the state directory
    path = bridge_path('ARMS_SCENARIOS_JSON', 'stress_scenarios_config.json')
    if not os.path.exists(path):
        # Fail loudly instead of fabricating scenarios if configuration is missing
        print(f"[SSL] CRITICAL ERROR: Stress scenario configuration missing at {path}")
        raise FileNotFoundError(f"Missing mandatory SSL configuration: {path}")

    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"[SSL] CRITICAL ERROR: Invalid JSON in {path}")
        raise ValueError(f"Corrupted SSL configuration file: {e}")

def run_stress_scenarios(nav: float, portfolio_weights: Dict[str, float], ptrh_notional: float) -> ScenarioResults:
    """
    Applies the dynamically loaded scenarios to the live portfolio weights and PTRH hedge.
    """
    scenarios = _load_scenarios()
    
    results = []
    worst_scenario_id = "NONE"
    worst_net_loss = 0.0

    for sid, data in scenarios.items():
        # Institutional correlation logic: map specific tickers to the shock vectors defined in the config.
        # In a full system, this would use live Beta/Correlation engines. We use the config mapping here.
        equity_weight = sum(w for t, w in portfolio_weights.items() if t not in ['IBIT', 'ETHB', 'BSOL', 'SGOL', 'DBMF', 'SGOV'])
        crypto_weight = sum(w for t, w in portfolio_weights.items() if t in ['IBIT', 'ETHB', 'BSOL'])
        
        equity_pnl_pct = equity_weight * float(data.get("qqq", 0.0))
        crypto_pnl_pct = crypto_weight * float(data.get("btc", 0.0))
        
        defense_pnl_pct = 0.0
        offsets = data.get("offsets", {})
        for def_ticker, shock in offsets.items():
            if def_ticker in portfolio_weights:
                defense_pnl_pct += portfolio_weights[def_ticker] * float(shock)

        gross_pnl_pct = equity_pnl_pct + crypto_pnl_pct + defense_pnl_pct
        gross_pnl_usd = gross_pnl_pct * nav

        # PTRH Offset Estimation (Delta approximation in deep shock)
        hedge_offset_usd = 0.0
        qqq_shock = float(data.get("qqq", 0.0))
        if qqq_shock < 0:
            convexity_mult = 1.5 if qqq_shock < -0.20 else 1.0
            hedge_offset_usd = abs(qqq_shock) * ptrh_notional * 0.35 * convexity_mult

        net_usd = gross_pnl_usd + hedge_offset_usd
        net_pct = net_usd / nav if nav > 0 else 0.0

        if net_pct < worst_net_loss:
            worst_net_loss = net_pct
            worst_scenario_id = sid

        results.append(ScenarioPnL(
            scenario_id=sid,
            scenario_name=data.get("name", "Unknown"),
            portfolio_pnl_pct=gross_pnl_pct,
            portfolio_pnl_usd=gross_pnl_usd,
            hedge_offset_usd=hedge_offset_usd,
            net_pnl_after_hedge=net_usd
        ))

    return ScenarioResults(
        as_of=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        current_nav=nav,
        scenarios=results,
        worst_scenario=worst_scenario_id,
        worst_net_loss_pct=worst_net_loss
    )
