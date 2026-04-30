# ARMS - Phase 1 Development Plan (Months 1-2)

This document outlines the development tasks for the first phase of the ARMS project, based on the specifications in the `arms_fsd_master_build_v1.1.md` document. The priority is to establish the core autonomous functionality, starting with data-driven conviction scoring and live execution capabilities.

## Guiding Principles
- **Priority Sequence:** Tasks will follow the sequence laid out in FSD v1.1, Section 8.1.
- **Immediate-Build Frontiers:** Focus on closing the five immediate-build frontiers as the primary goal of this phase.
- **Inch Today, Mile Later:** Build modular "receptors" that can be upgraded with institutional-grade components later without re-architecting the core system.

---

## Phase 1 Task Breakdown

### Task 1: Model-Implied Conviction Score (MICS)
- **Objective:** Replace manual conviction assignment with a data-driven, formulaic score. Close Frontier F3.
- **File:** `src/engine/mics.py`
- **Estimated Time:** 2-3 Days
- **Sub-tasks:**
    - [ ] Implement the MICS formula as specified in FSD v1.1, Section 11.1.
    - [ ] Create data structures to handle inputs from SENTINEL gates (Gate 3, 4, 5, 6).
    - [ ] Implement the PM override logic (bounded ±1 level with rationale).
    - [ ] Develop a script to retroactively score the current Architecture AB portfolio and flag divergences for GP review.
    - [ ] Unit tests for the MICS calculation and override logic.

### Task 2: PTRH Full Autonomy (CAM & Automation)
- **Objective:** Fully automate all Permanent Tail Risk Hedge operations (sizing, rolls, execution), removing all related PM decisions from the queue.
- **Files:** `src/engine/tail_hedge.py` (Update), `src/engine/cam.py` (New)
- **Estimated Time:** 3-4 Days
- **Sub-tasks:**
    - [ ] **CAM:** Implement the Coverage Adequacy Model formula in `cam.py` to autonomously determine required coverage.
    - [ ] **Automation:** Update `tail_hedge.py` to incorporate CAM and automate all four core actions: Regime Sizing, DTE Roll, Coverage Verification/Drift Correction, and Strike Selection.
    - [ ] **Tier 0:** Ensure all operations are fully Tier 0 with no PM veto, except for the one-time dual-risk standard review.
    - [ ] **Monitor:** Update the daily monitor to show PTRH status as a confirmation panel, not a decision item.
    - [ ] Unit tests for both CAM calculations and the four automated actions.

### Task 3: Thesis Integrity Layer (CDM & TDC)
- **Objective:** Automate cross-sector signal propagation and continuous thesis integrity auditing.
- **Files:** `src/config/position_dependency_map.py`, `src/engine/cdm.py`, `src/engine/tdc.py`
- **Estimated Time:** 3-4 Days
- **Sub-tasks:**
    - [ ] **CDM:** Create the `position_dependency_map.py` configuration file with the initial 12 positions.
    - [ ] **CDM:** Build the named entity recognition and alert propagation logic in `cdm.py`.
    - [ ] **TDC:** Build the thesis integrity review logic in `tdc.py`, integrating with the Claude API wrapper.
    - [ ] **TDC:** Implement both CDM-triggered and weekly scheduled thesis audits.
    - [ ] **TDC:** Implement the Thesis Integrity Score (TIS) and the corresponding PM review queue logic.
    - [ ] Unit tests for CDM alert propagation and TDC scoring.

### Task 4: Defensive Sleeve Harvest Protocol (DSHP)
- **Objective:** Systematically crystallize gains in the defensive sleeve.
- **Files:** `src/engine/dshp.py`, `src/config/dshp_config.py`
- **Estimated Time:** 1-2 Days
- **Sub-tasks:**
    - [ ] Create the `dshp_config.py` file with instrument-specific thresholds.
    - [ ] Implement the harvest trigger logic for SGOL (appreciation) and DBMF (appreciation or drift).
    - [ ] Integrate DSHP actions into the Tier 1 confirmation queue.
    - [ ] Unit tests for harvest trigger logic and queue integration.

### Task 5: Feed-Agnostic Data Ingestion Pipeline
- **Objective:** Build the foundational data pipeline. Close Frontier F1 (receptor).
- **Files:** `src/data_feeds/`, `src/data_feeds/free_signals.py`
- **Estimated Time:** 1 Week
- **Sub-tasks:**
    - [ ] Define and freeze the abstract `FeedPlugin` interface and `SignalRecord` dataclass.
    - [ ] Implement the core pipeline that consumes `SignalRecord` objects.
    - [ ] Create initial free feed plugins (SEC EDGAR, FRED, etc.).
    - [ ] Configure the scheduler (Prefect) to run the ingestion pipeline.

### Task 6: Session Log & Analytics
- **Objective:** Establish the immutable audit trail and the receptor for the learning loop. Close Frontier F4 (receptor).
- **Files:** `src/reporting/audit_log.py`, `src/engine/session_log_analytics.py`
- **Estimated Time:** 1-2 Days
- **Sub-tasks:**
    - [ ] Define and freeze the `SessionLogEntry` dataclass.
    - [ ] Implement the `audit_log.py` module to write structured log entries.
    - [ ] Integrate logging calls into all other modules.
    - [ ] Implement the `session_log_analytics.py` module to calculate the three initial metrics.
    - [ ] Schedule the SLA module to run monthly via Prefect.

### Task 7: Systematic Scan Engine
- **Objective:** Create a low-fidelity autonomous thesis generation engine. Close Frontier F2 (low-fidelity).
- **File:** `src/engine/systematic_scan.py`
- **Estimated Time:** 2 Weeks
- **Sub-tasks:**
    - [ ] Define the initial universe of companies in a configuration file.
    - [ ] Build the process to pull filings/transcripts from the SEC EDGAR feed.
    - [ ] Integrate with the Claude API wrapper for SENTINEL Gate 1 & 2 analysis.
    - [ ] Develop the quantitative logic to calculate the Gate 3 mispricing score.
    - [ ] Schedule the Scan Engine to run weekly via Prefect.

### Task 8: Brokerage API Integration & Execution
- **Objective:** Connect ARMS signals to live (paper) execution.
- **Files:** `src/execution/broker_api.py`, `src/execution/confirmation_queue.py`
- **Estimated Time:** 1-2 Weeks
- **Sub-tasks:**
    - [ ] **Interface Freeze:** Define and freeze the `OrderRequest` dataclass.
    - [ ] **Broker Connection:** Implement `broker_api.py` using `ib_insync` for the IBKR paper trading account.
    - [ ] **Confirmation Queue:** Build the information-quality confirmation queue.
    - [ ] **Testing:** Execute all three required testing protocols (30-day paper, Regime Transition, Kill Chain).

### Task 9: Regime Probability Engine (RPE)
- **Objective:** Add an anticipatory layer to the regime assessment.
- **File:** `src/engine/regime_probability.py`
- **Estimated Time:** 1 Week
- **Sub-tasks:**
    - [ ] Design and implement the model for calculating transition probabilities.
    - [ ] Implement the RPE module to run every 5 minutes.
    - [ ] Integrate the RPE output as an advisory signal into relevant modules.
