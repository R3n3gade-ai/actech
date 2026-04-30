import json
import os
import datetime
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

from src.reporting.daily_monitor import run_daily_monitor

def load_json(filepath):
    with open(filepath, 'r') as f:
        return json.load(f)

def _load_optional_json(filepath):
    if not os.path.exists(filepath):
        return {}
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except Exception:
        return {}

def run():
    print("Loading organic data from state files...")
    snapshot = load_json('data/mj_portfolio_snapshot.json')
    pm_notes = load_json('data/mj_pm_notes.json')

    # Pull live persisted state when available
    regime_history = _load_optional_json('state/regime_history.json')
    queue_gov = _load_optional_json('state/queue_governance_state.json')

    # Derive score from most recent regime history entry, fall back to snapshot
    last_entry = regime_history[-1] if isinstance(regime_history, list) and regime_history else {}
    prior_entry = regime_history[-2] if isinstance(regime_history, list) and len(regime_history) >= 2 else {}
    current_score = last_entry.get('score', 0.72)
    prior_score = prior_entry.get('score', current_score)
    current_regime = last_entry.get('regime', 'WATCH')
    queue_status = queue_gov.get('headline_status', 'UNKNOWN')
    catalyst = last_entry.get('catalyst', 'Live event-state review required')
    
    equity_book = []
    for ticker, info in snapshot.get('equities', {}).items():
        equity_book.append({
            'ticker': ticker,
            'name': ticker,
            'weight': info.get('weight', 0.0),
            'session_perf': 0.0,  # unrealized return; true session PnL requires prior-close tracking
            'status': info.get('status', 'OK'),
            'rationale': info.get('flag', '')
        })
        
    defensive_sleeve = []
    for ticker, weight in snapshot.get('defensive_sleeve', {}).items():
        defensive_sleeve.append({
            'ticker': ticker,
            'weight': weight,
            'rationale': 'ACTIVE'
        })
        
    raw_inputs = {
        'nav': snapshot.get('nav', 0.0),
        'score': current_score,
        'score_direction': '↑' if current_score >= prior_score else '↓',
        'queue_status': queue_status,
        'macro_compass_score_yesterday': prior_score,
        'macro_compass_trigger': 0.65,
        'macro_compass_next_catalyst': catalyst,
        'macro_compass_drivers_up': 'Geopolitical resolution, Rate cut bets',
        'macro_compass_drivers_down': 'Temporary ceasefire expiry risk',
        'macro_inputs': {
            'VIX': {'value': pm_notes.get('macro_cards', {}).get('vix', {}).get('value', '20-22'), 'context': pm_notes.get('macro_cards', {}).get('vix', {}).get('sub', '')},
            'BRENT': {'value': pm_notes.get('macro_cards', {}).get('brent', {}).get('value', '$94'), 'context': pm_notes.get('macro_cards', {}).get('brent', {}).get('sub', '')},
            'SP500': {'value': pm_notes.get('macro_cards', {}).get('sp500', {}).get('value', '+2.5%'), 'context': pm_notes.get('macro_cards', {}).get('sp500', {}).get('sub', '')}
        },
        'equity_book': equity_book,
        'deployment_queue': [],
        'defensive_sleeve': defensive_sleeve,
        'module_status': {
            'PTRH': {'status': 'ACTIVE', 'detail': f"Multiplier {snapshot.get('ptrh_multiplier', 1.0)}x"},
            'ARES': {'status': 'WATCH', 'detail': "Queue WATCH"},
            'DSHP': {'status': 'MONITOR', 'detail': "Monitoring DBMF"}
        }
    }
    
    market_context = json.dumps(pm_notes)
    
    print("Synthesizing Daily Monitor using Gemini 3.1 Pro...")
    markdown_output = run_daily_monitor(raw_inputs, market_context)
    
    date_str = datetime.datetime.now().strftime('%Y%m%d')
    out_path = f"achelion_arms/logs/daily_monitor_organic_{date_str}.md"
    with open(out_path, "w") as f:
        f.write(markdown_output)
        
    print(f"Daily Monitor successfully generated at: {out_path}")

if __name__ == "__main__":
    run()
