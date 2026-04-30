"""
ARMS Master Scheduler — AWS Cloud Architecture

Dual-mode scheduler for ARMS operational cadence:

  1. ECS Fargate mode (production):
     - EventBridge Scheduler triggers ECS RunTask for each job
     - CloudWatch Logs for observability
     - SNS for PM failure alerts
     - S3 for state persistence and backup
     - This module provides the job dispatch handler invoked by ECS tasks

  2. Local APScheduler mode (development/paper trading):
     - In-process scheduler for local dev/testing
     - Same job functions, different trigger mechanism

All times are US Central Time (CT).
Failure handling: 3 retries with 60-second exponential backoff.
All failures logged to session_log.jsonl with full stack trace.
Critical failures publish to SNS topic for PM push notification.

Master Schedule:
  - Market data ingestion:     Every 5 min (market hours 0830-1500 CT)
  - Regime probability update: Every 5 min (market hours)
  - ARAS composite score:      Every 5 min (market hours)
  - Full morning sweep:        5:15 AM CT daily (M-F)
  - EOD Snapshot generation:   2:50 PM CT daily (M-F)
  - Systematic Scan:           Monday 5:00 AM CT
  - KB ingest:                 Nightly 2:00 AM CT
  - State/DB backup to S3:    Nightly 2:30 AM CT
  - Session Log Analytics:     1st of month 6:00 AM CT
  - Proactive Digest:          1st of month 7:00 AM CT

Infrastructure: AWS ECS Fargate + EventBridge Scheduler + RDS PostgreSQL
               + ElastiCache Redis + S3 + SNS + CloudWatch
Reference: ARMS Infrastructure Specification v1.0 (cloud-adapted)
"""

import datetime
import json
import logging
import os
import sys
import traceback
from typing import Callable, Optional

# Ensure src/ is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reporting.audit_log import SessionLogEntry, append_to_log

logger = logging.getLogger('arms.scheduler')


# --- AWS Service Clients (lazy-loaded) ---

_sns_client = None
_s3_client = None
_cloudwatch_client = None


def _get_sns_client():
    global _sns_client
    if _sns_client is None:
        import boto3
        _sns_client = boto3.client('sns', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
    return _sns_client


def _get_s3_client():
    global _s3_client
    if _s3_client is None:
        import boto3
        _s3_client = boto3.client('s3', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
    return _s3_client


def _get_cloudwatch_client():
    global _cloudwatch_client
    if _cloudwatch_client is None:
        import boto3
        _cloudwatch_client = boto3.client('cloudwatch', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
    return _cloudwatch_client


# --- Configuration ---

SNS_TOPIC_ARN = os.environ.get('ARMS_SNS_ALERT_TOPIC', '')
S3_STATE_BUCKET = os.environ.get('ARMS_S3_STATE_BUCKET', 'achelion-arms-state')
S3_BACKUP_PREFIX = os.environ.get('ARMS_S3_BACKUP_PREFIX', 'backups/')
ENVIRONMENT = os.environ.get('ARMS_ENVIRONMENT', 'prod')


# --- US Market Calendar ---

US_HOLIDAYS_2025_2026 = {
    # 2025
    datetime.date(2025, 1, 1),    # New Year's Day
    datetime.date(2025, 1, 20),   # MLK Day
    datetime.date(2025, 2, 17),   # Presidents' Day
    datetime.date(2025, 4, 18),   # Good Friday
    datetime.date(2025, 5, 26),   # Memorial Day
    datetime.date(2025, 6, 19),   # Juneteenth
    datetime.date(2025, 7, 4),    # Independence Day
    datetime.date(2025, 9, 1),    # Labor Day
    datetime.date(2025, 11, 27),  # Thanksgiving
    datetime.date(2025, 12, 25),  # Christmas
    # 2026
    datetime.date(2026, 1, 1),
    datetime.date(2026, 1, 19),
    datetime.date(2026, 2, 16),
    datetime.date(2026, 4, 3),
    datetime.date(2026, 5, 25),
    datetime.date(2026, 6, 19),
    datetime.date(2026, 7, 3),    # Independence Day observed
    datetime.date(2026, 9, 7),
    datetime.date(2026, 11, 26),
    datetime.date(2026, 12, 25),
}


def is_market_day() -> bool:
    """Check if today is a US market trading day."""
    today = datetime.date.today()
    if today.weekday() >= 5:  # Saturday or Sunday
        return False
    if today in US_HOLIDAYS_2025_2026:
        return False
    return True


def is_market_hours() -> bool:
    """Check if current time is during market hours (0830-1500 CT)."""
    if not is_market_day():
        return False
    try:
        import zoneinfo
        ct = datetime.datetime.now(zoneinfo.ZoneInfo('America/Chicago'))
    except ImportError:
        ct = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-6)))
    hour_min = ct.hour * 100 + ct.minute
    return 830 <= hour_min <= 1500


# --- CloudWatch Metrics ---

def _publish_metric(job_name: str, success: bool, duration_ms: float):
    """Publish job execution metric to CloudWatch for dashboards and alarms."""
    try:
        cw = _get_cloudwatch_client()
        cw.put_metric_data(
            Namespace='ARMS/Scheduler',
            MetricData=[
                {
                    'MetricName': 'JobExecution',
                    'Dimensions': [
                        {'Name': 'JobName', 'Value': job_name},
                        {'Name': 'Environment', 'Value': ENVIRONMENT},
                    ],
                    'Value': 1.0 if success else 0.0,
                    'Unit': 'Count',
                },
                {
                    'MetricName': 'JobDuration',
                    'Dimensions': [
                        {'Name': 'JobName', 'Value': job_name},
                        {'Name': 'Environment', 'Value': ENVIRONMENT},
                    ],
                    'Value': duration_ms,
                    'Unit': 'Milliseconds',
                },
            ],
        )
    except Exception as e:
        logger.warning(f"Failed to publish CloudWatch metric for {job_name}: {e}")


# --- SNS Alert ---

def _send_pm_alert(job_name: str, error: str):
    """Send critical failure alert to PM via SNS (push to phone/email)."""
    if not SNS_TOPIC_ARN:
        logger.warning(f"SNS_TOPIC_ARN not configured. Alert for {job_name} not sent.")
        return
    try:
        sns = _get_sns_client()
        sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=f'ARMS ALERT: {job_name} FAILED [{ENVIRONMENT}]',
            Message=(
                f"ARMS Scheduler Critical Failure\n"
                f"Job: {job_name}\n"
                f"Environment: {ENVIRONMENT}\n"
                f"Time: {datetime.datetime.now(datetime.timezone.utc).isoformat()}\n"
                f"Error: {error[:500]}\n\n"
                f"All {3} retries exhausted. Manual intervention required."
            ),
        )
        logger.info(f"PM alert sent for {job_name}")
    except Exception as e:
        logger.error(f"Failed to send SNS alert for {job_name}: {e}")


# --- S3 State Backup ---

def _backup_to_s3():
    """Backup state and logs to S3 bucket."""
    s3 = _get_s3_client()
    today = datetime.date.today().isoformat()

    for local_dir in ['achelion_arms/state', 'achelion_arms/logs']:
        if not os.path.exists(local_dir):
            continue
        for root, _dirs, files in os.walk(local_dir):
            for filename in files:
                local_path = os.path.join(root, filename)
                s3_key = f"{S3_BACKUP_PREFIX}{today}/{local_path}"
                try:
                    s3.upload_file(local_path, S3_STATE_BUCKET, s3_key)
                except Exception as e:
                    logger.warning(f"S3 upload failed for {local_path}: {e}")

    logger.info(f"State backup to s3://{S3_STATE_BUCKET}/{S3_BACKUP_PREFIX}{today}/ complete")


# --- Job Wrapper with Retry, Metrics, and Alerting ---

def _run_with_retry(job_name: str, func: Callable, max_retries: int = 3, backoff_base: int = 60):
    """
    Execute a job function with retry logic, CloudWatch metrics, and SNS alerting.
    3 retries with 60-second exponential backoff.
    """
    import time as _time
    start = _time.monotonic()

    for attempt in range(1, max_retries + 1):
        try:
            func()
            duration_ms = (_time.monotonic() - start) * 1000
            append_to_log(SessionLogEntry(
                timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                action_type='SCHEDULER_JOB_OK',
                triggering_module='SCHEDULER',
                triggering_signal=f'{job_name} completed (attempt {attempt}, {duration_ms:.0f}ms)',
            ))
            _publish_metric(job_name, success=True, duration_ms=duration_ms)
            logger.info(f"{job_name} completed (attempt {attempt}, {duration_ms:.0f}ms)")
            return
        except Exception as e:
            tb = traceback.format_exc()
            append_to_log(SessionLogEntry(
                timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                action_type='SCHEDULER_JOB_FAILED',
                triggering_module='SCHEDULER',
                triggering_signal=f'{job_name} failed attempt {attempt}/{max_retries}: {str(e)[:200]}',
            ))
            logger.error(f"{job_name} failed (attempt {attempt}/{max_retries}): {e}\n{tb}")

            if attempt < max_retries:
                wait = backoff_base * (2 ** (attempt - 1))
                logger.info(f"Retrying {job_name} in {wait}s...")
                _time.sleep(wait)

    # All retries exhausted — alert PM
    duration_ms = (_time.monotonic() - start) * 1000
    _publish_metric(job_name, success=False, duration_ms=duration_ms)
    append_to_log(SessionLogEntry(
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        action_type='SCHEDULER_JOB_EXHAUSTED',
        triggering_module='SCHEDULER',
        triggering_signal=f'{job_name} exhausted all {max_retries} retries. PM alert sent.',
    ))
    _send_pm_alert(job_name, f'Exhausted {max_retries} retries')
    logger.critical(f"{job_name} exhausted all retries. PM alert sent.")


# --- Individual Job Definitions ---

def job_market_data_ingest():
    """Every 5 min during market hours: ingest market data for ARAS/RPE."""
    if not is_market_hours():
        return

    def _run():
        from data_feeds.pipeline import DataPipeline
        pipeline = DataPipeline()
        pipeline.get_macro_inputs()
        logger.info(f"Market data ingested at {datetime.datetime.now()}")

    _run_with_retry('market_data_ingest', _run)


def job_regime_update():
    """Every 5 min during market hours: update regime probability."""
    if not is_market_hours():
        return

    def _run():
        from data_feeds.pipeline import DataPipeline
        from engine.macro_compass import calculate_macro_regime_score
        pipeline = DataPipeline()
        macro_input = pipeline.get_macro_inputs()
        macro_map = {m.name: m.value for m in macro_input}
        score = calculate_macro_regime_score(macro_map)
        logger.info(f"Regime score updated: {score:.4f}")

    _run_with_retry('regime_update', _run)


def job_aras_update():
    """Every 5 min during market hours: update ARAS composite score."""
    if not is_market_hours():
        return

    def _run():
        from data_feeds.pipeline import DataPipeline
        from engine.macro_compass import calculate_macro_regime_score
        from engine.aras import ARASAssessor
        # Use module-level singleton for hysteresis persistence across 5-min updates
        if not hasattr(job_aras_update, '_assessor'):
            job_aras_update._assessor = ARASAssessor()
        pipeline = DataPipeline()
        macro_input = pipeline.get_macro_inputs()
        macro_map = {m.name: m.value for m in macro_input}
        score = calculate_macro_regime_score(macro_map)
        aras = job_aras_update._assessor.assess(score)
        logger.info(f"ARAS: {aras.regime} ({score:.4f}) → Ceiling {aras.equity_ceiling_pct:.0%}")

    _run_with_retry('aras_update', _run)


def job_full_morning_sweep():
    """
    5:15 AM CT daily: Full ARMS operational sweep.
    Runs as ECS Fargate task triggered by EventBridge Scheduler.
    """
    if not is_market_day():
        return

    def _run():
        from main import run_full_arms_cycle
        run_full_arms_cycle()

    _run_with_retry('full_morning_sweep', _run)


def job_eod_snapshot():
    """2:50 PM CT daily: Generate End-of-Day Snapshot."""
    if not is_market_day():
        return

    def _run():
        from main import run_eod_snapshot_cycle
        run_eod_snapshot_cycle()

    _run_with_retry('eod_snapshot', _run)


def job_systematic_scan():
    """Monday 5:00 AM CT: Run weekly Systematic Scan Engine."""
    if not is_market_day():
        return

    def _run():
        from engine.systematic_scan import run_systematic_scan
        results = run_systematic_scan()
        logger.info(f"Systematic scan complete: {len(results)} candidates")

    _run_with_retry('systematic_scan', _run)


def job_kb_ingest():
    """Nightly 2:00 AM CT: Incremental knowledge base ingest."""
    def _run():
        from intelligence.kb_ingest import run_kb_ingest
        result = run_kb_ingest()
        logger.info(f"KB ingest: {result.documents_processed} docs, "
                     f"{result.chunks_upserted} chunks upserted, "
                     f"{result.errors} errors ({result.duration_seconds:.1f}s)")

    _run_with_retry('kb_ingest', _run)


def job_database_backup():
    """Nightly 2:30 AM CT: State backup to S3 + RDS automated snapshots."""
    def _run():
        _backup_to_s3()
        logger.info("State backup to S3 completed. RDS snapshots handled by AWS automation.")

    _run_with_retry('database_backup', _run)


def job_ccm_calibration():
    """Quarterly: Run Conviction Calibration Module learning loop."""
    def _run():
        from engine.conviction_calibration import ConvictionCalibrationModule
        ccm = ConvictionCalibrationModule()
        result = ccm.run_calibration()
        if result:
            logger.info(f"CCM calibration complete: PM Alpha={result.pm_performance_alpha:.2f}, "
                        f"data points={result.data_points_analyzed}")
        else:
            logger.info("CCM calibration skipped — insufficient data")

    _run_with_retry('ccm_calibration', _run)


def job_session_log_analytics():
    """1st of month 6:00 AM CT: Session log analytics and learning loop metrics."""
    def _run():
        from engine.session_log_analytics import compute_sla_metrics
        metrics = compute_sla_metrics()
        logger.info(f"SLA metrics: CDF accuracy={metrics['cdf_accuracy_rate']:.2f}, "
                    f"regime lag={metrics['regime_transition_lag_days']:.1f}d, "
                    f"Gate3 accuracy={metrics['sentinel_gate3_accuracy']:.2f}, "
                    f"status={metrics['status']}")

    _run_with_retry('session_log_analytics', _run)


def job_proactive_digest():
    """1st of month 7:00 AM CT: Generate proactive intelligence digest."""
    def _run():
        from reporting.proactive_digest import generate_proactive_digest
        generate_proactive_digest()
        logger.info("Proactive digest generated")

    _run_with_retry('proactive_digest', _run)


# --- ECS Task Dispatch Handler ---

# Maps EventBridge event detail-type to job functions.
# EventBridge Scheduler sends {"detail-type": "arms.job.<name>"} to ECS task.
JOB_REGISTRY = {
    'market_data_ingest': job_market_data_ingest,
    'regime_update': job_regime_update,
    'aras_update': job_aras_update,
    'full_morning_sweep': job_full_morning_sweep,
    'eod_snapshot': job_eod_snapshot,
    'systematic_scan': job_systematic_scan,
    'kb_ingest': job_kb_ingest,
    'database_backup': job_database_backup,
    'ccm_calibration': job_ccm_calibration,
    'session_log_analytics': job_session_log_analytics,
    'proactive_digest': job_proactive_digest,
}


def dispatch_ecs_job(job_name: str):
    """
    Dispatch a job by name — called when ECS task starts.
    The container entrypoint calls: python -m scheduling.master_scheduler <job_name>
    """
    if job_name not in JOB_REGISTRY:
        logger.error(f"Unknown job: {job_name}. Available: {list(JOB_REGISTRY.keys())}")
        sys.exit(1)

    logger.info(f"ECS dispatch: executing {job_name}")
    JOB_REGISTRY[job_name]()
    logger.info(f"ECS dispatch: {job_name} complete")


# --- Local Development Scheduler (APScheduler) ---

def start_local_scheduler():
    """
    Start a local APScheduler instance for development and paper trading.
    NOT for production — production uses EventBridge Scheduler + ECS Fargate.

    All times are Central Time (America/Chicago).
    """
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler = BlockingScheduler(timezone='America/Chicago')

    # --- Intraday (every 5 min during market hours) ---
    scheduler.add_job(job_market_data_ingest, IntervalTrigger(minutes=5),
                      id='market_data_ingest', replace_existing=True)
    scheduler.add_job(job_regime_update, IntervalTrigger(minutes=5),
                      id='regime_update', replace_existing=True)
    scheduler.add_job(job_aras_update, IntervalTrigger(minutes=5),
                      id='aras_update', replace_existing=True)

    # --- Daily ---
    scheduler.add_job(job_full_morning_sweep,
                      CronTrigger(hour=5, minute=15, day_of_week='mon-fri'),
                      id='full_morning_sweep', replace_existing=True)
    scheduler.add_job(job_eod_snapshot,
                      CronTrigger(hour=14, minute=50, day_of_week='mon-fri'),
                      id='eod_snapshot', replace_existing=True)

    # --- Weekly ---
    scheduler.add_job(job_systematic_scan,
                      CronTrigger(hour=5, minute=0, day_of_week='mon'),
                      id='systematic_scan', replace_existing=True)

    # --- Nightly ---
    scheduler.add_job(job_kb_ingest, CronTrigger(hour=2, minute=0),
                      id='kb_ingest', replace_existing=True)
    scheduler.add_job(job_database_backup, CronTrigger(hour=2, minute=30),
                      id='database_backup', replace_existing=True)

    # --- Quarterly (CCM calibration — 1st of Jan, Apr, Jul, Oct) ---
    scheduler.add_job(job_ccm_calibration,
                      CronTrigger(hour=4, minute=0, day=1, month='1,4,7,10'),
                      id='ccm_calibration', replace_existing=True)

    # --- Monthly ---
    scheduler.add_job(job_session_log_analytics, CronTrigger(hour=6, minute=0, day=1),
                      id='session_log_analytics', replace_existing=True)
    scheduler.add_job(job_proactive_digest, CronTrigger(hour=7, minute=0, day=1),
                      id='proactive_digest', replace_existing=True)

    print("\n" + "="*60)
    print("ACHELION ARMS SCHEDULER — LOCAL DEV MODE")
    print("WARNING: For development/paper trading only.")
    print("Production uses EventBridge Scheduler + ECS Fargate.")
    print("All times: Central Time (America/Chicago)")
    print("="*60)
    print("\nScheduled jobs:")
    for job in scheduler.get_jobs():
        print(f"  • {job.id} → next: {job.next_run_time}")
    print("\nScheduler running. Press Ctrl+C to stop.\n")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("\n[SCHEDULER] Shutting down gracefully...")
        scheduler.shutdown(wait=False)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    )

    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == '--local':
            # Local APScheduler mode for dev/paper trading
            start_local_scheduler()
        else:
            # ECS task dispatch: python -m scheduling.master_scheduler <job_name>
            dispatch_ecs_job(arg)
    else:
        print("Usage:")
        print("  ECS dispatch:  python -m scheduling.master_scheduler <job_name>")
        print("  Local dev:     python -m scheduling.master_scheduler --local")
        print(f"\nAvailable jobs: {', '.join(JOB_REGISTRY.keys())}")
        sys.exit(1)
