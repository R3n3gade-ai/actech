# Achelion ARMS

**Autonomous Risk Management System**  
Achelion Capital Management, LLC — Confidential
---

## Current Status

- **Engine / Brain — COMPLETE.** All 36 engine modules built and audited line-by-line against the canonical briefing documents (Addendums 1–8, FSD v1.1, THB v4.0, GP Briefing). 26/26 unit tests passing. Three values are intentionally left as **provisional defaults pending PM confirmation and a discussion about hard parameters for for full autonomy. **:
  1. **Kevlar position size caps** 
  2. **PIM check-in heartbeat timers** 
  3. **Bridge data-staleness windows** 

- **Backtester — NEAR COMPLETE.** Phase 1 + Phase 2 historical replay engines operational; final calibration for missing tickers for historical time periods and report polish in progress. 

- **Execution Layer — IN PROGRESS.** IBKR broker adapter built; order book, confirmation queue, strategic queue, and trade-order generator currently being hardened. Live broker connection, smoke tests, and the daily-monitor wiring follow.

(
---

## Overview

ARMS is a fully autonomous hedge fund defense system managing $500M+ AUM across a 7-layer risk architecture. The system executes a complete daily operational cycle — pre-market sweep, intraday monitoring, EOD snapshot — with zero human intervention outside GP-designated veto windows.

**Architecture AB**: 58% Equity / 20% Crypto / 14% Defensive / 8% Cash

---

## Security & AI Orchestration Layer

> **Note:** These two layers are *additive* engineering layers built on top of the canonical PM / engineer-designer architecture defined in the briefing documents. They do not modify, override, or substitute for any specified ARMS module — they wrap the system to provide enterprise-grade secrets handling, auditability, and an autonomous operations capability that the original spec assumed would be a human responsibility.

### Security Stack

A purpose-built security perimeter wrapping every component of the engine, the data feeds, and the execution layer. Nothing in the canonical brief addressed secrets management, signed actions, immutable audit, or runtime intrusion detection — these were operational gaps that would have to be solved before any live broker connection. The stack closes them.

| Component | Role |
|---|---|
| **VAULT** | Encrypted secrets store. All API keys, broker credentials, and signing keys live here — never in `.env`, never in code, never in logs. |
| **SIGNET** | Cryptographic action signing. Every order, every state-changing engine call, every report submission is signed before it leaves the process. Tamper-evident by construction. |
| **IRONLOG** | Append-only, hash-chained audit log. Every engine decision, every PM input, every executed order is recorded with a chain-of-custody hash. Forensic-grade replay. |
| **SENTINEL-SEC** | Runtime anomaly detection. Watches for behavior that diverges from the engine's expected operating envelope — unexpected outbound calls, unauthorized config writes, abnormal data-feed patterns. |
| **GATEWAY** | Hardened API surface. Single ingress point with rate limiting, request validation, and per-route authentication. Nothing reaches the engine without passing the gateway. |
| **RBAC** | Role-based access control. PM, GP, Operator, Auditor, and Read-Only roles each have a strictly enumerated set of capabilities. Privilege escalation is impossible by design. |

### AI Orchestration — Agent Command System

A multi-agent layer that gives the engine continuous, autonomous operational coverage. The brain produced by the canonical spec is deterministic and correct, but it still needs a *driver* — something that watches the outputs, escalates the right items, runs research workflows, and keeps the human PM informed. This is that driver.

| Agent | Role |
|---|---|
| **ARCHON** | Lead orchestrator. Receives all engine outputs, plans daily and intra-day workflows, dispatches specialist agents, and synthesizes their results back to the PM. |
| **MACRO STRAT** | Regime intelligence. Monitors macro signals against ARAS / Macro Compass thresholds and flags impending regime shifts before they cross the line. |
| **ALPHA HUNTER** | Thesis research. Runs SENTINEL-style adversarial reviews on candidate positions, surfaces new opportunities, and stress-tests existing theses against fresh evidence. |
| **RISK WARDEN** | Defensive watch. Tracks PTRH, DSHP, Kevlar, FEM, and drawdown signals continuously and pre-stages actions for the PM Decision Queue. |
| **WEAPONS** | Hedge & options operations. Manages the tail-risk hedge book, PTRH rolls, IV-aware execution timing, and the LAEP order-book state. |
| **EXEC PILOT** | Order execution. Translates engine deployment instructions into broker actions through the IBKR adapter, with LAEP-compliant slicing and circuit-breaker awareness. |
| **COMPLIANCE** | Integrity & reporting. Validates every action against the canonical rulebook, generates the daily monitor and EOD snapshot, and maintains the audit trail for LP and regulatory review. |

The Security Stack is the **perimeter**. The Agent Command System is the **continuous operator**. The canonical ARMS engine is the **brain** that both are built around. All three together form the production system; none of the three replaces or modifies the canonical PM design.

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  MAIN ORCHESTRATOR               │
│              src/main.py (7-phase cycle)         │
├─────────────────────────────────────────────────┤
│  L1: DATA FEEDS (The Senses)                    │
│    FRED API → VIX, HY Spread, 10Y, T10Y2Y,     │
│               PCR, Margin Debt                   │
│    IBKR     → CME Futures Basis, OI, Stablecoin │
│               Pegs, CBOE SKEW                    │
│    CoinGlass→ Funding, OI, Liquidations,         │
│               Long/Short, BTC Price              │
│    ISM PMI  → CSV Bridge (manual monthly temp)   │
├─────────────────────────────────────────────────┤
│  L2: MACRO COMPASS                              │
│    VIX 30% + HY 30% + PMI 20% + 10Y 20%        │
│    + Typed Macro Event Overlay                   │
├─────────────────────────────────────────────────┤
│  L3: ARAS (Regime → Equity Ceiling)             │
│    5 Regimes: RISK_ON → WATCH → NEUTRAL →       │
│               DEFENSIVE → CRASH                  │
│    Hysteresis ±0.05 + NEUTRAL persistence        │
│    EDR Advisory: Delev + CryptoMicro (max +0.12)│
│    Dual-Module Alert when both > 0.60            │
├─────────────────────────────────────────────────┤
│  L4: MASTER ENGINE (Target Weights)             │
│    MICS conviction scoring, Kevlar 22% cap,      │
│    SENTINEL thesis workflow                      │
├─────────────────────────────────────────────────┤
│  EXECUTION LAYER                                │
│    IBKR Broker, LAEP 5-tier VIX order book,     │
│    Circuit breaker, Confirmation queue           │
└─────────────────────────────────────────────────┘
```

---

## Production Data Feed Architecture

| Feed | Source | Signals | Auth |
|------|--------|---------|------|
| **FRED** | FRED API | VIX, HY Spread, 10Y Yield, T10Y2Y, Equity PCR, Margin Debt | `FRED_API_KEY` |
| **IBKR** | IB Gateway | CME Futures Basis, OI, Stablecoin Pegs, CBOE SKEW | IB Gateway |
| **CoinGlass** | Public API | BTC Funding, OI, Liquidations, Long/Short Ratio, BTC Price | Free (no auth) |
| **ISM PMI** | CSV Bridge | PMI_NOWCAST | Manual monthly update |

No mocks. No synthetic fallbacks. All feeds are production except PMI (CSV bridge until production API selected).

---

## Module Inventory (118 files)

| Directory | Count | Purpose |
|-----------|------:|---------|
| `engine/` | 36 | Core risk engines, state persistence, conviction scoring |
| `data_feeds/` | 11 | Market data pipeline + 4 production feed plugins |
| `execution/` | 15 | Broker adapter, order book, queues, safety rails |
| `intelligence/` | 8 | LLM wrapper, ELVT, JPVI, PFVT, SCCR, Gate 3 |
| `reporting/` | 8 | Daily monitor, EOD snapshot, audit log, attribution |
| `modules/` | 7 | ARAS sub-modules (EDR) + stress scenarios |
| `simulation/` | 7 | Backtester Phase 1 + Phase 2 engines |
| `scheduling/` | 1 | Master scheduler (ECS/APScheduler) |
| `infra/` | 1 | PostgreSQL + Redis adapter |
| `config/` | 3 | Configuration constants |

---

## Addendum Status

| # | Title | Module | Status |
|---|-------|--------|--------|
| 1 | PTRH + DSHP | `engine/tail_hedge.py`, `engine/dshp.py` | Complete |
| 2 | CDM + TDC | `engine/cdm.py`, `engine/tdc.py` | Complete |
| 3 | Intelligence Phase 2/3 | `intelligence/elvt.py`, `jpvi.py`, `pfvt.py`, `sccr.py` | Complete |
| 4 | CAM Hedge Sizing | `engine/tail_hedge.py` (integrated) | Complete |
| 5 | SEM Automation | `scheduling/master_scheduler.py` | Complete |
| 6 | PTRH Adaptive Strike | `engine/tail_hedge.py` | Phase 1 Complete (4-gate fallback). Phase 2 (drift detection + IV recalibration) on roadmap. |
| 7 | **Deleveraging Risk (EDR)** | `modules/deleveraging_risk.py` | **Complete — wired into ARAS** |
| 8 | **Crypto Microstructure (EDR)** | `modules/crypto_microstructure.py` | **Complete — wired into ARAS + dual-alert** |

---

## Key Files

| File | Purpose |
|------|---------|
| `src/main.py` | Full 7-phase ARMS orchestration cycle |
| `src/engine/aras.py` | ARAS regime assessor with EDR advisory integration |
| `src/engine/macro_compass.py` | L2 macro regime scoring |
| `src/data_feeds/pipeline.py` | Production data pipeline (4 feeds) |
| `src/run_backtest.py` | Backtester entry point |
| `src/run_daily_report.py` | Standalone daily report runner |

---


## Repository Structure

```
Achelion-Tech/
├── .env.example             # Template for required environment variables
├── .gitignore
├── README.md                # This file
├── requirements.txt         # Curated runtime dependencies
├── requirements.lock.txt    # Full pinned transitive dependency tree
├── data/                    # Bridge files and templates
├── infra/                   # Terraform (ECS / RDS / Redis / S3)
├── SAMPLES/                 # Backtest reports and tearsheets
├── src/                     # All application code
│   ├── engine/              # Core risk engines (the brain)
│   ├── data_feeds/          # Market data pipeline + plugins
│   ├── execution/           # Broker adapter, order book, queues
│   ├── intelligence/        # LLM wrapper + Phase 2/3 modules
│   ├── reporting/           # Daily monitor, EOD snapshot, audit log
│   ├── modules/             # ARAS sub-modules (EDR) + stress scenarios
│   ├── simulation/          # Backtester Phase 1 + Phase 2
│   ├── scheduling/          # Master scheduler
│   └── config/              # Configuration constants
└── state/                   # Persistent state files (gitignored)
```

---

*Achelion Capital Management, LLC — Flow. Illumination. Discipline. Conviction.*
