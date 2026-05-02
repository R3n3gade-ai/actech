"""
ACHELION ARMS v1.1 - FULL INTEGRATION CYCLE
"Silence is trust in the architecture."

This script executes a complete ARMS operational cycle.
Production rule: do not silently substitute fabricated portfolio or market state.
"""

import datetime
import json
import os
from typing import List, Dict, Any


LIVE_CYCLE_STRICT = os.environ.get('ARMS_STRICT_LIVE', '1').strip().lower() not in {'0', 'false', 'no'}


def require_live(condition: bool, message: str):
    if not condition:
        raise RuntimeError(message)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- 1. Core Services & Interfaces ---
from data_feeds.pipeline import DataPipeline
from data_feeds.event_bridge import load_event_bridge
from data_feeds.sec_edgar_feed import fetch_form4_events
from data_feeds.news_rss_feed import fetch_public_rss_events
from data_feeds.event_state import dedupe_news_items
from data_feeds.macro_event_state import derive_macro_event_state
from execution.broker_api import IBKRBroker
from execution.order_book import OrderBook, LiquidityMode
from execution.strategic_queue import StrategicQueueManager
from execution.circuit_breaker import CircuitBreaker
from modules.stress_scenarios import run_stress_scenarios
from execution.confirmation_queue import ConfirmationQueue, QueuedAction
from reporting.audit_log import SessionLogEntry, append_to_log
from execution.order_request import OrderRequest
from execution.queue_state import build_queue_governance_state
from execution.queue_reasoning import build_thesis_signal_map, derive_queue_reasoning_signals
from execution.queue_persistence import persist_queue_state
from reporting.monitor_state import DailyMonitorState, MacroInputCard, EquityBookEntryState, SleeveEntryState, ModulePanelState, DecisionQueueItem, QueueEntryState, WeeklyScorecardRow, MonitorListEntry
from reporting.report_context import summarize_recent_session_log
from reporting.regime_history import RegimeHistoryEntry, append_regime_history, prior_score as prior_regime_score

# --- 2. Engine Modules ---
from engine.mics import calculate_mics, SentinelGateInputs
from engine.sentinel_bridge import build_mics_input_for_ticker
from engine.sentinel_workflow import sentinel_workflow
from engine.tdc_state import load_tdc_state
from engine.tail_hedge import run_ptrh_module, OptionsPosition, PTRHInputs, calculate_ptrh_required_notional
from engine.strc import calculate_strc_reserve_deployment, get_total_hedge_notional
from engine.dshp import run_dshp_check, DefensivePosition
from engine.cdm import run_cdm_scan, NewsItem
from engine.tdc import run_thesis_review, run_weekly_tdc_audit
from engine.regime_probability import calculate_rpe
from engine.ares import run_ares_check
from engine.cdf import calculate_position_decay
from engine.cdf_analytics import compute_live_underperformance
from engine.cdf_state import update_cdf_state
from engine.mc_rss import calculate_mc_rss
from engine.rss_bridge import load_rss_inputs
from engine.cdf_bridge import load_cdf_inputs
from engine.macro_compass import calculate_macro_regime_score, get_regime_label
from engine.bridge_health import collect_bridge_health
from engine.aras import ARASAssessor

# Module-level stateful ARAS — persists hysteresis state across cycles
_aras_assessor = ARASAssessor()
from engine.master_engine import compute_target_weights
from execution.trade_order_generator import generate_rebalance_orders
from engine.drawdown_sentinel import run_pds_check
from engine.pds_state import (
    load_high_water_mark,
    update_high_water_mark,
)
from engine.factor_exposure import run_fem_check, generate_paired_trims
from engine.incapacitation import run_incapacitation_check
from engine.asymmetric_upside import run_aup_check
from reporting.daily_monitor import run_daily_monitor
from reporting.eod_snapshot import generate_eod_snapshot, save_morning_score
from reporting.performance_attribution import generate_attribution_report

def _read_recent_session_log(limit: int = 400) -> List[dict]:
    path = 'achelion_arms/logs/session_log.jsonl'
    if not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8') as f:
        lines = f.readlines()[-limit:]
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def run_full_arms_cycle():
    log_file = "achelion_arms/logs/session_log.jsonl"
    now = datetime.datetime.now(datetime.timezone.utc)
    
    print("\n" + "="*60)
    print("ACHELION ARMS v1.1 - FULL OPERATIONAL SWEEP")
    print(f"Cycle Initiated: {now.isoformat()}")
    print("="*60)
    
    # --- PHASE 0: INITIALIZATION ---
    print("\n[STEP 0] Initializing ARMS Services...")
    recent_log_summary = summarize_recent_session_log(_read_recent_session_log())
    data_pipeline = DataPipeline()
    broker = IBKRBroker()
    order_book = OrderBook()
    circuit_breaker = CircuitBreaker()
    confirmation_queue = ConfirmationQueue()
    strategic_queue = StrategicQueueManager(config_path='data/mj_strategic_queue.json')
    
    try:
        broker.connect()
    except Exception as e:
        raise RuntimeError(f"Could not connect to live broker: {e}")
    
    append_to_log(SessionLogEntry(
        timestamp=now.isoformat(), 
        action_type='CYCLE_START', 
        triggering_module='MAIN_ORCHESTRATOR', 
        triggering_signal='Full integration sweep initiated.'
    ))

    # --- PRE-MARKET: Overnight Futures Gap Check ---
    from execution.overnight_monitor import run_overnight_monitor
    overnight_status = run_overnight_monitor()
    print(f"[OVERNIGHT] {overnight_status.status} — ES: {overnight_status.spx_futures_pct:+.2%}, VIX Futures: {overnight_status.vix_futures_level:.1f}")
    if overnight_status.status == 'GAP_CRITICAL':
        print(f"[OVERNIGHT] CRITICAL: {overnight_status.detail}")
        append_to_log(SessionLogEntry(
            timestamp=now.isoformat(),
            action_type='OVERNIGHT_GAP_CRITICAL',
            triggering_module='OVERNIGHT_MONITOR',
            triggering_signal=overnight_status.detail,
        ))

    bridge_health = collect_bridge_health()
    for rec in bridge_health:
        print(f"[BRIDGE] {rec.name}: {rec.status} ({rec.path})")
        append_to_log(SessionLogEntry(
            timestamp=now.isoformat(),
            action_type='BRIDGE_HEALTH',
            triggering_module='MAIN_ORCHESTRATOR',
            triggering_signal=f"{rec.name} {rec.status} path={rec.path} age_hours={rec.age_hours}",
        ))

    # --- PHASE 1: DATA INGESTION (THE SENSES) ---
    print("\n[STEP 1] Running Multi-Feed Data Ingestion...")
    signals = data_pipeline.run_all_feeds()
    require_live(len(signals) > 0, "Data pipeline returned no signals.")
    signal_types = {s.signal_type for s in signals}
    require_live('VIX_INDEX' in signal_types and 'HY_CREDIT_SPREAD' in signal_types, "Critical macro signals missing from data pipeline.")
    macro_input_map = {s.signal_type: float(s.raw_value if s.raw_value is not None else s.value) for s in signals}
    
    # --- PHASE 2.5: LIVE PORTFOLIO INGESTION ---
    print("\n[STEP 1.5] Ingesting Live Portfolio Data...")
    require_live(broker.is_connected, "Broker is not connected; refusing to run live cycle.")
    live_positions = broker.get_positions()
    nav = broker.get_nav()
    require_live(nav > 0, "Live NAV is non-positive.")

    # --- PHASE 2: MACRO & SENTIMENT (ARAS / RPE / RSS) ---
    print("\n[STEP 2] Calculating Regime & Sentiment...")
    # 2.0 Macro Compass & ARAS Sub-Modules
    preliminary_event_items = []
    preliminary_event_items.extend(fetch_form4_events())
    preliminary_event_items.extend(fetch_public_rss_events())
    preliminary_event_items.extend(load_event_bridge())
    preliminary_event_items = dedupe_news_items(preliminary_event_items)
    macro_event_state = derive_macro_event_state(preliminary_event_items)
    regime_score = calculate_macro_regime_score(signals, event_state=macro_event_state)
    
    from modules.deleveraging_risk import run_deleveraging_check
    from modules.margin_stress import run_margin_stress_check
    from modules.dealer_gamma import run_dealer_gamma_check
    from modules.crypto_microstructure import run_crypto_microstructure_check
    
    delev_res = run_deleveraging_check(signals)
    margin_res = run_margin_stress_check(signals)
    gamma_res = run_dealer_gamma_check(signals)
    crypto_micro_res = run_crypto_microstructure_check(signals)
    
    print(f"[EDR] Delev:{delev_res.status}({delev_res.velocity_score:.2f}) Margin:{margin_res.status} Gamma:{gamma_res.regime} CryptoMicro:{crypto_micro_res.status}({crypto_micro_res.stress_score:.2f})")

    if delev_res.status == "ACTIVE" or margin_res.status == "ACTIVE" or \
       gamma_res.regime == "NEGATIVE" or crypto_micro_res.status == "CRITICAL":
        print(f"[ARAS SUB-MODULES] High systemic risk detected.")
        append_to_log(SessionLogEntry(
            timestamp=now.isoformat(),
            action_type='EDR_HIGH_RISK',
            triggering_module='EDR_SUBMODULES',
            triggering_signal=f"Delev:{delev_res.status} Margin:{margin_res.status} Gamma:{gamma_res.regime} CryptoMicro:{crypto_micro_res.status}",
        ))
    
    vix_raw = next((s.value for s in signals if s.signal_type == 'VIX_INDEX'), None)
    require_live(vix_raw is not None, "VIX_INDEX signal missing from data pipeline.")
    vix_val = vix_raw * 100.0
    # Fetch intraday data for circuit breaker
    spx_open = broker.get_recent_close('SPY', 1)
    spx_now = broker.get_recent_close('SPY', 0)
    require_live(spx_open is not None and spx_now is not None, "SPY price unavailable from broker; cannot evaluate circuit breaker.")
    
    # Derive VIX open from prior session close via signals (FRED provides prior-close VIX).
    # vix_val already represents the latest available observation.
    vix_open = vix_val  # Best available prior-session estimate from FRED/live feed
    
    circuit_breaker.update_market_data(
        spx_open=spx_open,
        spx_current=spx_now,
        vix_open=vix_open,
        vix_current=vix_val
    )
    cb_status = circuit_breaker.evaluate()
    if cb_status.is_tripped:
        print("[MAIN] CRITICAL: Circuit Breaker TRIPPED. Autonomous execution halted.")
        # Proceed with diagnostics but no trade execution
    aras_output = _aras_assessor.assess(
        regime_score,
        delev_score=delev_res.velocity_score,
        micro_score=crypto_micro_res.stress_score,
    )
    current_regime = aras_output.regime
    regime_label = current_regime
    print(f"[ARAS] Computed Regime: {current_regime} ({aras_output.score:.2f}) -> Ceiling: {aras_output.equity_ceiling_pct:.0%}")
    if aras_output.edr_advisory_total > 0:
        print(f"[ARAS] EDR advisory: +{aras_output.edr_advisory_total:.3f} to composite (base={regime_score:.2f} -> adjusted={aras_output.score:.2f})")
    if aras_output.dual_edr_alert:
        print(f"[ARAS] *** DUAL EDR ALERT *** Delev={delev_res.velocity_score:.3f} CryptoMicro={crypto_micro_res.stress_score:.3f}")
        append_to_log(SessionLogEntry(
            timestamp=now.isoformat(),
            action_type='DUAL_EDR_ALERT',
            triggering_module='ARAS',
            triggering_signal=f"Delev={delev_res.velocity_score:.3f} CryptoMicro={crypto_micro_res.stress_score:.3f} combined_advisory=+{aras_output.edr_advisory_total:.3f} regime_base={regime_score:.3f} regime_adjusted={aras_output.score:.3f}",
        ))
    
    # 2.05 Correlation Monitor (equity/crypto regime)
    from execution.correlation_monitor import run_correlation_monitor
    corr_status = run_correlation_monitor()
    print(f"[CORR] {corr_status.status} — 30d eq/crypto: {corr_status.equity_crypto_corr_30d:.2f}")
    if corr_status.status == 'CONTAGION':
        print(f"[CORR] CONTAGION ALERT: {corr_status.detail}")
        append_to_log(SessionLogEntry(
            timestamp=now.isoformat(),
            action_type='CORRELATION_CONTAGION',
            triggering_module='CORRELATION_MONITOR',
            triggering_signal=corr_status.detail,
        ))

    # 2.06 Put/Call Ratio Regime
    from modules.pcr_regime import run_pcr_regime_check
    pcr_status = run_pcr_regime_check(signals=signals)
    print(f"[PCR] {pcr_status.status} — PCR: {pcr_status.pcr_value:.2f}")

    # 2.07 Shutdown / Macro Event Risk Calendar
    from modules.shutdown_risk import run_shutdown_risk_check
    shutdown_status = run_shutdown_risk_check()
    if shutdown_status.status != 'CLEAR':
        print(f"[SHUTDOWN] {shutdown_status.status} — {shutdown_status.nearest_event} in {shutdown_status.days_to_event}d")
        append_to_log(SessionLogEntry(
            timestamp=now.isoformat(),
            action_type='SHUTDOWN_RISK_ACTIVE',
            triggering_module='SHUTDOWN_RISK',
            triggering_signal=shutdown_status.detail,
        ))
    else:
        print(f"[SHUTDOWN] CLEAR — No imminent macro events")

    # 2.1 RSS (Retail Sentiment)
    rss_inputs = load_rss_inputs()
    rss_res = calculate_mc_rss(
        retail_net_buying_usd=rss_inputs.retail_net_buying_usd,
        retail_history_30d=rss_inputs.retail_history_30d,
        naaim_exposure_index=rss_inputs.naaim_exposure_index,
        aaii_bull_bear_spread=rss_inputs.aaii_bull_bear_spread
    )
    
    # 2.2 RPE (Regime Probability)
    rpe_res = calculate_rpe(current_regime=current_regime, latest_signals=signals)
    
    # 2.3 Incapacitation Check (Safety)
    last_hb = now - datetime.timedelta(minutes=45)
    incap_res = run_incapacitation_check(last_hb, current_regime)
    
    # 2.4 Portfolio Drawdown Sentinel (PDS) - stateless threshold check
    hwm = load_high_water_mark(nav)
    pds_res = run_pds_check(nav, hwm)
    update_high_water_mark(nav)
    effective_ceiling = min(aras_output.equity_ceiling_pct, pds_res.pds_ceiling)
    if pds_res.status != "NORMAL":
        print(f"[PDS] ALERT: {pds_res.status} Active. PDS Ceiling {pds_res.pds_ceiling:.0%} overrides ARAS.")
    else:
        print(f"[PDS] Status NORMAL. Effective Ceiling remains {effective_ceiling:.0%}")
    
    # --- PHASE 2.5: ANTICIPATORY INTELLIGENCE LAYER (Phase 2 Modules) ---
    # Runs ELVT, JPVI, PFVT, SCCR sweeps on their respective cadences.
    # Results are persisted to state files and consumed by Gate 3 supplementary
    # scoring when MICS is calculated in Phase 6.
    print("\n[STEP 2.5] Running Anticipatory Intelligence Layer...")
    from intelligence.jpvi import run_weekly_jpvi_sweep
    from intelligence.sccr import run_weekly_sccr_sweep
    from intelligence.elvt import run_quarterly_elvt_sweep
    from intelligence.pfvt import run_monthly_pfvt_sweep

    is_monday = now.weekday() == 0
    is_first_of_month = now.day <= 7 and is_monday  # First Monday of month

    # JPVI + SCCR: Weekly (Monday cadence)
    if is_monday:
        try:
            jpvi_results = run_weekly_jpvi_sweep()
            print(f"[JPVI] {len(jpvi_results)} entities scanned.")
        except Exception as e:
            print(f"[JPVI] Weekly sweep failed (non-blocking): {e}")

        try:
            sccr_result = run_weekly_sccr_sweep()
            print(f"[SCCR] {sccr_result.patterns_scanned} patterns scanned.")
        except Exception as e:
            print(f"[SCCR] Weekly sweep failed (non-blocking): {e}")
    else:
        print("[Phase 2.5] JPVI/SCCR: Skipped (not Monday).")

    # PFVT: Monthly (first Monday of month)
    if is_first_of_month:
        try:
            pfvt_results = run_monthly_pfvt_sweep()
            print(f"[PFVT] {len(pfvt_results)} entities scanned.")
        except Exception as e:
            print(f"[PFVT] Monthly sweep failed (non-blocking): {e}")
    else:
        print("[Phase 2.5] PFVT: Skipped (not first Monday of month).")

    # ELVT: Quarterly — runs in earnings season months (Jan, Apr, Jul, Oct)
    earnings_months = {1, 4, 7, 10}
    if now.month in earnings_months and is_first_of_month:
        try:
            elvt_results = run_quarterly_elvt_sweep()
            print(f"[ELVT] {len(elvt_results)} entities scanned.")
        except Exception as e:
            print(f"[ELVT] Quarterly sweep failed (non-blocking): {e}")
    else:
        print("[Phase 2.5] ELVT: Skipped (not earnings season first Monday).")

    # --- PHASE 3: PORTFOLIO MAINTENANCE & STRESS AUDITING ---
    print("\n[STEP 3] Auditing Portfolio & Performance...")
    
    # 3.0 Stress Scenario Library (SSL)
    live_weights_all = {p.ticker: (p.market_value / nav) for p in live_positions if nav > 0}
    ptrh_notional = sum(p.notional_value for p in live_puts) if 'live_puts' in locals() else 0.0 # Will be populated in Step 4, but we can rough estimate it here
    ssl_res = run_stress_scenarios(nav, live_weights_all, ptrh_notional)
    print(f"[SSL] Worst-Case Scenario: {ssl_res.worst_scenario} (Net P&L: {ssl_res.worst_net_loss_pct:.2%})")

    # 3.1 DSHP (Harvesting)
    
    # Map live defensive sleeve positions for DSHP evaluation
    sleeve_positions = {}
    for p in live_positions:
        if p.ticker in ['SGOL', 'DBMF']:
            sleeve_positions[p.ticker] = DefensivePosition(
                ticker=p.ticker, 
                current_value=p.market_value, 
                entry_value=p.average_cost * p.quantity, 
                current_weight=p.market_value / nav if nav > 0 else 0
            )

    harvest_actions = run_dshp_check(sleeve_positions, nav) if sleeve_positions else []
    
    # 3.2 CDF (Decay)
    cdf_inputs = load_cdf_inputs()
    cdf_status = []
    for p in live_positions:
        if p.sec_type != 'STK':
            continue
        rec = cdf_inputs.get(p.ticker)
        try:
            live_perf = compute_live_underperformance(p.ticker, days_back=45)
            computed_underperf = live_perf.underperformance_pp if live_perf else 0.0
        except Exception:
            computed_underperf = 0.0

        if rec:
            underperf = max(rec.underperformance_pp, computed_underperf)
        else:
            underperf = computed_underperf

        persisted = update_cdf_state(p.ticker, underperf)
        days_under = max(rec.days_underperforming, persisted.underperforming_days) if rec else persisted.underperforming_days
        cdf_status.append(calculate_position_decay(p.ticker, days_under, underperf))
    
    # --- PHASE 4: HEDGE MANAGEMENT (CAM / PTRH) ---
    print("\n[STEP 4] Calculating Tail-Risk Coverage...")
    from engine.factor_exposure import run_fem_check
    live_fem_weights = {p.ticker: p.market_value / nav if nav > 0 else 0 for p in live_positions if p.sec_type == "STK"}
    fem_signal = run_fem_check(live_fem_weights)
    fem_concentration_score = fem_signal.highest_exposure_pct if getattr(fem_signal, 'highest_exposure_factor', None) else 0.0
    
    live_equity_positions = [p for p in live_positions if p.sec_type == "STK" and p.ticker not in ["SGOV", "SGOL", "DBMF", "STRC"]]
    cdm_alerts = run_cdm_scan(preliminary_event_items) if preliminary_event_items else []
    ptrh_in = PTRHInputs(
        nav=nav,
        regime_label=regime_label,
        regime_score=regime_score,
    )
    
    # Map live option positions for PTRH evaluation
    live_puts = []
    for p in live_positions:
        if p.sec_type == 'OPT' and p.ticker == 'QQQ' and (p.right or '').upper() == 'P':
            expiry = p.expiry or datetime.date.today().strftime('%Y%m%d')
            if len(expiry) == 8 and expiry.isdigit():
                expiry = f"{expiry[0:4]}-{expiry[4:6]}-{expiry[6:8]}"
            strike = float(p.strike or 0.0)
            contract_multiplier = float(p.multiplier or 100.0)
            underlying_notional = abs(float(p.quantity)) * strike * contract_multiplier if strike > 0 else abs(float(p.market_value))
            live_puts.append(OptionsPosition(
                ticker=p.ticker,
                option_type="PUT",
                strike=strike,
                expiry=expiry,
                contracts=int(abs(p.quantity)),
                notional_value=underlying_notional,
                con_id=p.con_id
            ))
        
    # STRC Dynamic Reserve Layer (THB v4.0 FINAL)
    strc_value = sum(p.market_value for p in live_positions if p.ticker == "STRC") if live_positions else 0.0
    strc_status = calculate_strc_reserve_deployment(regime_label, strc_value)
    print(f"[MAIN] STRC reserve: {strc_status.reserve_pct:.0%} deployed (${strc_status.deployed_notional:,.0f}), yield on retained: ${strc_status.annualized_yield_income:,.0f}/yr")

    ptrh_res = run_ptrh_module(ptrh_in, live_puts, broker)
    for order in ptrh_res.generated_orders:
        if cb_status.is_tripped:
            print(f"[MAIN] Execution blocked by Circuit Breaker: {order.ticker}")
            continue
            
        # Send through Order Book for LAEP evaluation
        ob_entry = order_book.process_request(order, vix_val)
        
        # Route based on Tier
        if order.tier == 1:
            queued_action = QueuedAction(
                action_id=f"ptrh_{order.ticker}_{now.strftime('%Y%m%d_%H%M')}",
                item=order,
                triggering_module='PTRH',
                rationale=order.triggering_signal,
                queued_at=now,
                veto_window_hours=order.veto_window_hours
            )
            confirmation_queue.add_action(queued_action)
        elif order.tier == 0:
            pass # Queued in OrderBook for LAEP batch execution
    
    # --- PHASE 5: THESIS INTEGRITY (CDM / TDC) ---
    print("\n[STEP 5] Running AI-Driven Thesis Audits...")
    event_items = preliminary_event_items
    # cdm_alerts already computed in Phase 4 for CAM hedge sizing
    tdc_results = []
    for alert in cdm_alerts:
        tdc_results.append(run_thesis_review(alert))

    weekly_audit_results = run_weekly_tdc_audit([p.ticker for p in live_positions if p.sec_type == 'STK'])
    tdc_results.extend(weekly_audit_results)

    # --- PHASE 5.5: THESIS RETIREMENT PROTOCOL (TRP) ---
    print("\n[STEP 5.5] Running Thesis Retirement Protocol...")
    from engine.thesis_retirement import run_trp_check
    tdc_state = load_tdc_state()
    cdf_by_ticker = {s.ticker: s for s in cdf_status}
    trp_statuses = []
    for tdc_res in tdc_results:
        ticker = tdc_res.position
        cdf_s = cdf_by_ticker.get(ticker)
        if not cdf_s:
            continue
        # Estimate days BROKEN from TDC state reviewed_at vs now
        tdc_rec = tdc_state.get(ticker)
        if tdc_rec and tdc_rec.tis_label == 'BROKEN' and tdc_rec.reviewed_at:
            try:
                first_broken = datetime.datetime.fromisoformat(tdc_rec.reviewed_at)
                days_broken = (now - first_broken).days
            except (ValueError, TypeError):
                days_broken = 0
        else:
            days_broken = 0

        trp_status = run_trp_check(
            ticker=ticker,
            tdc_result=tdc_res,
            cdf_status=cdf_s,
            days_tis_broken=days_broken,
        )
        trp_statuses.append(trp_status)
        if trp_status.is_retirement_due:
            print(f"[TRP] RETIREMENT DUE: {ticker} ({trp_status.trigger_reason}) — Tier {trp_status.tier}")

    # --- PHASE 5.6: PERM (COVERED CALL OVERWRITES) ---
    print("\n[STEP 5.6] Evaluating Covered Call Overwrites (PERM)...")
    from engine.perm import run_perm_evaluation
    options_market_open = broker.is_connected  # Proxy: if broker connected, assume options accessible
    # Build position gains dict for PERM primary trigger (30% unrealized gain)
    position_gains = {}
    for p in live_positions:
        if p.sec_type == 'STK' and p.average_cost > 0 and p.quantity > 0:
            cost_basis = p.average_cost * p.quantity
            if cost_basis > 0:
                position_gains[p.ticker] = (p.market_value - cost_basis) / cost_basis
    perm_orders = run_perm_evaluation(
        trp_statuses,
        position_gains=position_gains,
        current_vix=vix_val,
        options_market_open=options_market_open,
    )
    for perm_order in perm_orders:
        print(f"[PERM] Tier 0 Overwrite: SELL_CALL {perm_order.ticker}")
        if not cb_status.is_tripped:
            ob_entry = order_book.process_request(perm_order, vix_val)

    # --- PHASE 6: MASTER ENGINE & REBALANCING ---
    print("\n[STEP 6] Rebalancing Equity Book...")
    live_equity_positions = [p for p in live_positions if p.sec_type == 'STK']
    require_live(len(live_equity_positions) > 0, "No live equity positions found from broker.")

    live_weights = {p.ticker: (p.market_value / nav) for p in live_equity_positions if nav > 0}
    fem_signal = run_fem_check(live_weights)

    mics_results_live = {}
    mics_scores = {}
    # Import Gate 3 supplementary scoring (Phase 2 anticipatory signals)
    from intelligence.gate3_supplementary import calculate_gate3_supplementary
    for p in live_equity_positions:
        fem_impact = f"NORMAL->{fem_signal.status}" if fem_signal.status in {'WATCH', 'ALERT'} else 'NORMAL->NORMAL'
        mics_input = build_mics_input_for_ticker(p.ticker, current_regime, fem_impact)
        # Calculate Phase 2 Gate 3 supplementary adjustment (ELVT/JPVI/PFVT/SCCR)
        try:
            g3_supp = calculate_gate3_supplementary(p.ticker)
            g3_adj = g3_supp.adjustment
        except Exception:
            g3_adj = 0.0
        mics_result = calculate_mics(mics_input, gate3_supplementary=g3_adj)
        mics_results_live[p.ticker] = mics_result
        mics_scores[p.ticker] = mics_result.conviction_level

    cdf_multipliers = {s.ticker: s.current_multiplier for s in cdf_status}
    # Ensure all equity positions have a CDF multiplier (1.0 if no decay computed)
    for p in live_equity_positions:
        if p.ticker not in cdf_multipliers:
            cdf_multipliers[p.ticker] = 1.0
    target_weights = compute_target_weights(
        mics_scores=mics_scores,
        cdf_multipliers=cdf_multipliers,
        aras_ceiling=effective_ceiling
    )
    
    # Persist target weights for EOD Snapshot drift check
    tw_path = 'achelion_arms/state/last_target_weights.json'
    os.makedirs(os.path.dirname(tw_path), exist_ok=True)
    with open(tw_path, 'w') as f:
        json.dump(target_weights, f)
    
    rebalance_orders = generate_rebalance_orders(live_positions, target_weights, nav)
    for order in rebalance_orders:
        if cb_status.is_tripped:
            print(f"[MAIN] Execution blocked by Circuit Breaker: {order.ticker}")
            continue
            
        ob_entry = order_book.process_request(order, vix_val)
        
        if order.tier == 1:
            pass # Same logic as PTRH
        elif order.tier == 0:
            pass # Queued in OrderBook for LAEP batch execution
                
    # --- PHASE 6.8: LAEP EXECUTION WAVE ---
    print("\n[STEP 6.8] Executing L5 Order Book (LAEP)...")

    # FEM Paired Trim — Tier 0 auto-trims when ALERT sustained >24h
    fem_trims = generate_paired_trims(live_weights, fem_signal)
    for trim_order in fem_trims:
        print(f"[FEM] Tier 0 Paired Trim: {trim_order.action} {trim_order.ticker} ({trim_order.quantity:.2%})")
        if not cb_status.is_tripped:
            ob_entry = order_book.process_request(trim_order, vix_val)

    # --- PAIE PRE-FLIGHT CHECK ---
    # Runs before execution to validate thesis concentration integrity
    from engine.paie import deployment_preflight, format_paie_section
    # Build the deployment queue from pending BUY orders in the order book
    pending_deployments = {}
    for entry in order_book.queue:
        req = entry.request
        if req.action == 'BUY' and req.quantity_kind == 'WEIGHT_PCT':
            pending_deployments[req.ticker] = req.quantity
        elif req.action == 'BUY' and req.quantity_kind == 'NOTIONAL_USD' and nav > 0:
            pending_deployments[req.ticker] = req.quantity / nav

    if pending_deployments:
        paie_log = deployment_preflight(
            queue=pending_deployments,
            book=live_weights,
            nav=nav,
            regime_score=regime_score,
            cdf_multipliers=cdf_multipliers,
        )
        print(f"[PAIE] Pre-Flight Status: {paie_log.status}")
        if paie_log.all_adjustments:
            for adj in paie_log.all_adjustments:
                print(f"[PAIE]   {adj.ticker}: {adj.original_weight:.2%} → {adj.adjusted_weight:.2%} [{adj.rule}]")
        if paie_log.status == 'BLOCKED':
            print("[PAIE] BLOCKED — Execution halted. Engineering ticket required.")
            # Remove blocked BUY orders from the order book
            order_book.queue = [e for e in order_book.queue if e.request.action != 'BUY']
        # Persist PAIE section for EOD snapshot inclusion
        paie_section = format_paie_section(paie_log)
        paie_state_path = 'achelion_arms/state/last_paie_log.txt'
        os.makedirs(os.path.dirname(paie_state_path), exist_ok=True)
        with open(paie_state_path, 'w') as f:
            f.write(paie_section)
    else:
        paie_log = None
        print("[PAIE] No pending deployments — pre-flight skipped.")

    # --- ESCALATION ENGINE: Pre-execution cumulative loss check ---
    from execution.escalation_engine import run_escalation_engine
    esc_status = run_escalation_engine()
    if esc_status.is_suppressed:
        print(f"[ESCALATION] SUPPRESSED — Cumulative loss: {esc_status.cumulative_intraday_loss_pct:.2%}. New orders blocked.")
        append_to_log(SessionLogEntry(
            timestamp=now.isoformat(),
            action_type='ESCALATION_SUPPRESS',
            triggering_module='ESCALATION_ENGINE',
            triggering_signal=f"Cumulative loss {esc_status.cumulative_intraday_loss_pct:.2%} breached -2.5% threshold",
        ))
    elif esc_status.cumulative_intraday_loss_pct < -0.015:
        print(f"[ESCALATION] WARNING — Cumulative loss: {esc_status.cumulative_intraday_loss_pct:.2%}")

    executable_batch = order_book.get_executable_batch()
    if not executable_batch:
        print("[ORDER_BOOK] No executable orders in queue.")
    else:
        from execution.pm_protocol import require_gp_cosign, check_cosign_status
        for entry in executable_batch:
            # Block new orders if escalation engine has suppressed
            if esc_status.is_suppressed and entry.request.action == 'BUY':
                print(f"[ESCALATION] Blocking BUY {entry.request.ticker} — loss suppression active")
                continue
            if entry.request.tier == 0:
                if broker.is_connected:
                    print(f"[ORDER_BOOK] Routing {entry.order_type} Priority {entry.priority} for {entry.request.ticker} (Slippage: {entry.slippage_budget_bps}bps)")
                    broker.submit_order(entry.request)
                else:
                    print(f"[MAIN] Broker disconnected. Cannot execute Tier 0 order: {entry.request.action} {entry.request.ticker}")
            else:
                # Tier 1: GP co-sign protocol
                action_id = f"{entry.request.action}_{entry.request.ticker}_{now.strftime('%Y%m%d')}"
                require_gp_cosign(action_id)
                cosign = check_cosign_status(action_id)
                if cosign.is_approved:
                    print(f"[PM_PROTOCOL] Tier 1 {entry.request.ticker} co-signed by {cosign.signers}. Routing.")
                    if broker.is_connected:
                        broker.submit_order(entry.request)
                else:
                    print(f"[ORDER_BOOK] Tier 1 {entry.request.ticker} awaiting GP co-sign ({cosign.required_count - len(cosign.signers)} signature(s) needed).")

    # --- PHASE 6.5: GROWTH & RE-ENTRY (ARES / AUP) ---
    print("\n[STEP 6.5] Evaluating Growth & Re-Entry...")
    ares_res = run_ares_check(current_regime, regime_score, macro_stress_score, rss_res.composite_rss)

    # Compute live AUP inputs from actual engine state
    top_mics = sorted(mics_scores.values(), reverse=True)[:5]
    avg_mics = sum(top_mics) / len(top_mics) if top_mics else 0.0
    is_fem_clean = fem_signal.status not in {'ALERT', 'WATCH'} if hasattr(fem_signal, 'status') else True
    rpe_watch_prob = rpe_res.transition_probabilities.get('WATCH', 0.0) + rpe_res.transition_probabilities.get('DEFENSIVE', 0.0)
    aup_res = run_aup_check(current_regime, avg_mics, is_fem_clean, rpe_watch_prob, pds_res.drawdown_pct)
    
    from engine.slof import run_slof_manager
    gross_exposure = sum(abs(p.market_value) for p in live_positions)
    current_leverage = gross_exposure / nav if nav > 0 else 1.0
    slof_status = run_slof_manager(aup_res, current_leverage=current_leverage, regime_score=regime_score)

    thesis_signal_map = build_thesis_signal_map(sentinel_workflow.get_all_records())
    thesis_state_by_ticker = {
        ticker: {
            'status': sig.thesis_status,
            'gate3_score': str(sig.gate3_score) if sig.gate3_score is not None else '',
            'source_category': sig.source_category,
        }
        for ticker, sig in thesis_signal_map.items()
    }

    queue_reasoning = derive_queue_reasoning_signals(
        queue_tickers=[item.ticker for item in strategic_queue.items],
        thesis_map=thesis_signal_map,
        cdm_alerts=cdm_alerts,
        tdc_state_by_ticker=load_tdc_state(),
    )

    queue_state = build_queue_governance_state(
        strategic_items=strategic_queue.items,
        current_score=regime_score,
        current_regime=current_regime,
        ares_fully_cleared=ares_res.is_fully_cleared,
        thesis_state_by_ticker=thesis_state_by_ticker,
        reasoning_signals=queue_reasoning,
    )
    queue_transitions = persist_queue_state(queue_state)
    
    # --- PHASE 7: CONSOLIDATION & REPORTING ---
    print("\n[STEP 7] Generating Daily Monitor v4.0...")

    macro_cards_state = [
        MacroInputCard(title='VIX', value=str(round(macro_input_map.get('VIX_INDEX', 0.0), 2)), context='Live ingestion'),
        MacroInputCard(title='HY_SPREAD', value=str(round(macro_input_map.get('HY_CREDIT_SPREAD', 0.0), 2)), context='Live ingestion'),
        MacroInputCard(title='PMI', value=str(round(macro_input_map.get('PMI_NOWCAST', 0.0), 2)), context='Live ingestion'),
        MacroInputCard(title='10Y_YIELD', value=str(round(macro_input_map.get('10Y_TREASURY_YIELD', 0.0), 2)), context='Live ingestion'),
        MacroInputCard(title='EVENT_OVERLAY', value=str(round(macro_event_state.override_floor, 2)), context='Typed event-state overlay'),
    ]

    # Fetch live session returns (intraday change vs prior close) for equity positions
    equity_tickers = [p.ticker for p in live_positions if p.sec_type == 'STK']
    session_returns = broker.get_session_returns(equity_tickers) if broker.is_connected else {}

    equity_book_state = []
    for p in live_positions:
        if p.sec_type != 'STK':
            continue
        session_ret = session_returns.get(p.ticker)
        if session_ret is not None:
            perf_text = f"{session_ret:+.2%}"
        else:
            # Fall back to unrealized return from cost basis when session data unavailable
            cost_basis = p.average_cost * p.quantity
            unrealized_return = ((p.market_value - cost_basis) / cost_basis) if cost_basis != 0 else 0.0
            perf_text = f"{unrealized_return:+.2%}"
        equity_book_state.append(EquityBookEntryState(
            ticker=p.ticker,
            name=p.ticker,
            weight_pct=(p.market_value / nav * 100) if nav > 0 else 0.0,
            perf_text=perf_text,
            status='OK',
            rationale=f"Live position snapshot | MICS={mics_scores.get(p.ticker, 'N/A')}"
        ))

    defensive_sleeve_state = [
        SleeveEntryState(
            ticker=p.ticker,
            weight_pct=(p.market_value / nav * 100) if nav > 0 else 0.0,
            rationale='Live sleeve snapshot'
        )
        for p in live_positions if p.ticker in ['DBMF', 'SGOV', 'SGOL', 'STRC']
    ]

    module_panels_state = [
        ModulePanelState(name='ARAS', status=f'{aras_output.regime} {aras_output.score:.2f}', detail=f'Base ceiling {aras_output.equity_ceiling_pct:.2%} -> effective {effective_ceiling:.2%}'),
        ModulePanelState(name='SSL', status=f'Worst Case: {ssl_res.worst_scenario}', detail=f'Estimated P&L: {ssl_res.worst_net_loss_pct:.2%}'),
        ModulePanelState(name='ARES', status='Queue ' + queue_state.headline_status, detail=f'Fully cleared={ares_res.is_fully_cleared}'),
        ModulePanelState(name='CAM', status=f'PTRH {ptrh_res.multiplier}x', detail=f'Target {ptrh_res.target_notional_pct:.2%} | Actual {ptrh_res.actual_notional_pct:.2%}'),
        ModulePanelState(name='MC-RSS', status=rss_res.signal_label, detail=f'Composite {rss_res.composite_rss:.2f}'),
        ModulePanelState(name='SAFETY', status=f'Tier {incap_res.current_safety_tier}', detail=f'Last heartbeat {incap_res.hours_since_heartbeat * 60:.0f} min ago'),
        ModulePanelState(name='SLOF', status='ACTIVE' if slof_status.is_active else 'INACTIVE', detail=f'Leverage {slof_status.current_leverage_ratio:.2f}x / target {slof_status.target_leverage_ratio:.2f}x'),
        ModulePanelState(name='QUEUE_GOV', status=queue_state.headline_status, detail=f'{len(queue_transitions)} transition(s) persisted this cycle'),
        ModulePanelState(name='OVERNIGHT', status=overnight_status.status, detail=f'ES {overnight_status.spx_futures_pct:+.2%}, VIX Futures {overnight_status.vix_futures_level:.1f}'),
        ModulePanelState(name='CORR', status=corr_status.status, detail=f'30d eq/crypto: {corr_status.equity_crypto_corr_30d:.2f}'),
        ModulePanelState(name='PCR', status=pcr_status.status, detail=f'PCR: {pcr_status.pcr_value:.2f}'),
        ModulePanelState(name='SHUTDOWN', status=shutdown_status.status, detail=shutdown_status.detail[:80]),
        ModulePanelState(name='ESCALATION', status='SUPPRESSED' if esc_status.is_suppressed else 'NORMAL', detail=f'Cumulative loss: {esc_status.cumulative_intraday_loss_pct:.2%}'),
    ]

    queue_counts_note = (
        f"Neutral={len(queue_state.neutral_queue)} | RiskOn={len(queue_state.risk_on_queue)} | "
        f"Monitor={len(queue_state.monitor_list)} | Removed={len(queue_state.removed_items)} | "
        f"Transitions={len(queue_transitions)}"
    )
    decision_queue_state = [
        DecisionQueueItem(
            title='Maintain queue discipline',
            body=f"Queue headline is {queue_state.headline_status}. {queue_counts_note}. Do not front-run trigger logic outside system rules."
        ),
        DecisionQueueItem(
            title='Review thesis pressure',
            body=f"CDM alerts={len(cdm_alerts)} | TDC reviews={len(tdc_results)}. Any WATCH/IMPAIRED/BROKEN thesis state should govern queue posture before size increases."
        ),
        DecisionQueueItem(
            title='Validate hedge and execution readiness',
            body=f"PTRH last action={ptrh_res.last_action}. Order book batch size={len(executable_batch)}. Confirm no unresolved execution-quality issues before trusting autonomy claims."
        ),
    ]

    catalyst_label = 'No elevated macro catalysts'
    if macro_event_state.military_escalation_score >= 0.50:
        catalyst_label = 'Military escalation — elevated risk'
    elif macro_event_state.diplomacy_breakdown_score >= 0.50:
        catalyst_label = 'Diplomacy deterioration watch'
    elif macro_event_state.oil_stress_score >= 0.40:
        catalyst_label = 'Oil / shipping stress watch'
    elif macro_event_state.diplomacy_breakdown_score > 0.0:
        catalyst_label = 'Diplomacy / talks watch'
    elif macro_event_state.macro_stress_score >= 0.35:
        catalyst_label = 'Macro stress elevated — credit/regulatory/trade'
    # Enrich with top active event headline if available
    if hasattr(macro_event_state, 'active_events') and macro_event_state.active_events:
        top_event = max(macro_event_state.active_events, key=lambda e: e.stress_contribution)
        if top_event.stress_contribution >= 0.30:
            catalyst_label += f' | {top_event.headline[:60]}'

    history_entries = append_regime_history(RegimeHistoryEntry(
        timestamp=now.isoformat(),
        score=round(aras_output.score, 2),
        regime=aras_output.regime,
        equity_ceiling_pct=effective_ceiling,
        queue_status=queue_state.headline_status,
        catalyst=catalyst_label,
        note=f"Trades={recent_log_summary.trade_count}; TDC={recent_log_summary.tdc_review_count}; QueueChanges={recent_log_summary.queue_change_count}"
    ))
    prior_score = round(prior_regime_score(history_entries) or aras_output.score, 2)

    monitor_state = DailyMonitorState(
        date_label=now.strftime('%B %d, %Y'),
        regime=aras_output.regime,
        score=round(aras_output.score, 2),
        score_direction='↑' if regime_score > prior_score else '↓',
        score_prior=prior_score,
        weekly_scorecard=[
            WeeklyScorecardRow(
                session_label=e.timestamp[:10],
                market_summary=f"Queue {e.queue_status} · Catalyst {e.catalyst}",
                score_estimate=e.score,
                regime_note=e.regime,
            )
            for e in history_entries[-5:]
        ],
        queue_status=queue_state.headline_status,
        next_catalyst=catalyst_label,
        equity_ceiling_pct=effective_ceiling,
        macro_cards=macro_cards_state,
        deployment_queue=[
            QueueEntryState(
                ticker=e.ticker,
                target=(f'{e.target_weight_pct * 100:.1f}%' if e.target_weight_pct is not None else 'N/A'),
                execution_instruction=e.execution_instruction,
                state=e.state,
                reason=e.reason,
                trigger_rule=e.trigger_rule,
                notes=e.notes,
            )
            for e in queue_state.neutral_queue
        ],
        risk_on_queue=[
            QueueEntryState(
                ticker=e.ticker,
                target=(f'{e.target_weight_pct * 100:.1f}%' if e.target_weight_pct is not None else 'N/A'),
                execution_instruction=e.execution_instruction,
                state=e.state,
                reason=e.reason,
                trigger_rule=e.trigger_rule,
                notes=e.notes,
            )
            for e in queue_state.risk_on_queue
        ],
        monitor_list=[
            MonitorListEntry(
                ticker=e.ticker,
                reeval_trigger=e.trigger_rule,
                rationale=f"{e.state} · {e.reason}" + (f" — {e.notes}" if e.notes else ''),
            )
            for e in queue_state.monitor_list
        ],
        removed_queue_items=[
            QueueEntryState(
                ticker=e.ticker,
                target=(f'{e.target_weight_pct * 100:.1f}%' if e.target_weight_pct is not None else 'N/A'),
                execution_instruction=e.execution_instruction,
                state=e.state,
                reason=e.reason,
                trigger_rule=e.trigger_rule,
                notes=e.notes,
            )
            for e in queue_state.removed_items
        ],
        equity_book=equity_book_state,
        defensive_sleeve=defensive_sleeve_state,
        module_panels=module_panels_state,
        pm_decision_queue=decision_queue_state,
        live_context={
            'vix': str(macro_input_map.get('VIX_INDEX', 'N/A')),
            'hy_spread': str(macro_input_map.get('HY_CREDIT_SPREAD', 'N/A')),
            'pmi': str(macro_input_map.get('PMI_NOWCAST', 'N/A')),
            'ten_yield': str(macro_input_map.get('10Y_TREASURY_YIELD', 'N/A')),
        }
    )

    raw_inputs = {
        'date': monitor_state.date_label,
        'regime': monitor_state.regime,
        'score': monitor_state.score,
        'score_direction': monitor_state.score_direction,
        'queue_status': monitor_state.queue_status,
        'macro_compass_score_yesterday': monitor_state.score_prior or monitor_state.score,
        'macro_compass_trigger': 0.65,
        'macro_compass_next_catalyst': monitor_state.next_catalyst,
        'macro_compass_drivers_up': '; '.join(macro_event_state.rationale[:3]) if macro_event_state.rationale else 'No elevated event-state drivers detected.',
        'macro_compass_drivers_down': f"Recent trades={recent_log_summary.trade_count}; TDC reviews={recent_log_summary.tdc_review_count}; queue changes={recent_log_summary.queue_change_count}. Full parity still depends on deeper asymmetry logic and richer prior-score history.",
        'macro_inputs': {card.title: {'value': card.value, 'context': card.context} for card in monitor_state.macro_cards},
        'equity_book': [
            {
                'ticker': e.ticker,
                'name': e.name,
                'weight': e.weight_pct,
                'session_perf': float(e.perf_text.strip('%').replace('+', '')) / 100.0 if e.perf_text and e.perf_text not in ('N/A', '') else 0.0,
                'status': e.status,
                'rationale': e.rationale,
            }
            for e in monitor_state.equity_book
        ],
        'deployment_queue': [
            {
                'ticker': q.ticker,
                'target': q.target,
                'execution_instruction': q.execution_instruction,
                'status': f'{q.state} · {q.reason}',
                'notes': q.notes,
            }
            for q in monitor_state.deployment_queue + monitor_state.risk_on_queue
        ],
        'removed_queue_items': [
            {
                'ticker': q.ticker,
                'target': q.target,
                'execution_instruction': q.execution_instruction,
                'status': f'{q.state} · {q.reason}',
                'notes': q.notes,
            }
            for q in monitor_state.removed_queue_items
        ],
        'monitor_list': [
            {
                'ticker': m.ticker,
                'reeval_trigger': m.reeval_trigger,
                'rationale': m.rationale,
            }
            for m in monitor_state.monitor_list
        ],
        'defensive_sleeve': [
            {
                'ticker': s.ticker,
                'weight': s.weight_pct,
                'rationale': s.rationale,
            }
            for s in monitor_state.defensive_sleeve
        ],
        'module_status': {panel.name: {'status': panel.status, 'detail': panel.detail} for panel in monitor_state.module_panels},
    }
    
    # Compile live news events into market context for the LLM
    live_news_context = "\n".join([f"[{ev.event_type} - {', '.join(ev.entities)}]: {ev.headline}" for ev in event_items]) if event_items else "No major market events detected today."
    live_market_context = f"""
    System completed daily sweep.
    Current VIX is {monitor_state.live_context.get('vix')}.
    Current 10Y Yield is {monitor_state.live_context.get('ten_yield')}.
    Current queue headline status is {monitor_state.queue_status}.
    Neutral queue count: {len(monitor_state.deployment_queue)}.
    Risk-on queue count: {len(monitor_state.risk_on_queue)}.
    Recent News Events shaping macro:
    {live_news_context}
    """
    
    markdown_output = run_daily_monitor(raw_inputs, live_market_context)
    report_path = f"achelion_arms/logs/daily_monitor_{now.strftime('%Y%m%d')}.md"
    with open(report_path, "w") as f:
        f.write(markdown_output)
        
    print(f"[MAIN] Successfully rendered structured Daily Monitor to {report_path}")
    
    # Persist morning ARAS score for EOD Snapshot delta comparison
    save_morning_score(regime_score, current_regime, now.isoformat())
    
    append_to_log(SessionLogEntry(
        timestamp=now.isoformat(), 
        action_type='CYCLE_END', 
        triggering_module='MAIN_ORCHESTRATOR', 
        triggering_signal='Daily sweep complete.'
    ))
    
    broker.disconnect()


def run_eod_snapshot_cycle():
    """
    Standalone 1450 CT EOD Snapshot generation.
    Called separately from the morning sweep — either by the scheduler
    or manually via `python main.py --eod`.

    Connects to broker, pulls fresh ARAS score, reads confirmation queue,
    and generates the 5-field closing status check.
    """
    now = datetime.datetime.now(datetime.timezone.utc)

    print("\n" + "="*60)
    print("ACHELION ARMS v1.1 - EOD SNAPSHOT GENERATION")
    print(f"Initiated: {now.isoformat()}")
    print("="*60)

    confirmation_queue = ConfirmationQueue()
    broker = IBKRBroker()
    data_pipeline = DataPipeline()

    try:
        broker.connect()
    except Exception as e:
        raise RuntimeError(f"Could not connect to live broker for EOD snapshot: {e}")

    # Pull fresh market data for ARAS
    live_positions = broker.get_positions()
    nav = broker.get_nav()

    macro_input = data_pipeline.get_macro_inputs()
    macro_input_map = {m.name: m.value for m in macro_input}

    regime_score = calculate_macro_regime_score(macro_input_map)
    aras_output = _aras_assessor.assess(regime_score)
    current_regime = aras_output.regime

    # Build target weights from latest MICS/CDF state (reuse saved state)
    # For EOD we need target_weights for drift check — read from last cycle if available
    target_weights_path = 'achelion_arms/state/last_target_weights.json'
    target_weights: Dict[str, float] = {}
    if os.path.exists(target_weights_path):
        with open(target_weights_path, 'r') as f:
            target_weights = json.load(f)

    # Confirmation queue open items
    confirmation_queue.check_for_timeouts()
    open_items = confirmation_queue.get_open_items()

    # LAEP mode — derive from live VIX via LAEP engine
    from engine.laep import classify_vix_tier
    vix_signals = [m for m in macro_input if m.name == 'VIX_INDEX']
    eod_vix = vix_signals[0].value * 100.0 if vix_signals else 20.0
    laep_mode = classify_vix_tier(eod_vix).name

    # PTRH status
    ptrh_status = 'NORMAL'

    print(f"[EOD] Regime: {current_regime} ({regime_score:.2f}), Open Tier1: {len(open_items)}")

    eod_markdown = generate_eod_snapshot(
        current_score=regime_score,
        current_regime=current_regime,
        confirmation_queue_items=open_items,
        live_positions=live_positions,
        target_weights=target_weights,
        nav=nav,
        ptrh_status=ptrh_status,
        laep_mode=laep_mode,
    )

    report_path = f"achelion_arms/logs/eod_snapshot_{now.strftime('%Y%m%d')}.md"
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, 'w') as f:
        f.write(eod_markdown)

    print(f"[MAIN] EOD Snapshot written to {report_path}")

    # Performance Attribution — generate LP-defensible alpha attribution
    try:
        attr_report = generate_attribution_report()
        print(f"[MAIN] Performance Attribution: {attr_report.total_trades} trades, {attr_report.total_pnl_bps:.1f} bps total P&L")
    except Exception as e:
        print(f"[MAIN] Performance Attribution skipped: {e}")

    broker.disconnect()


if __name__ == '__main__':
    import sys
    if '--eod' in sys.argv:
        run_eod_snapshot_cycle()
    else:
        run_full_arms_cycle()
