"""
ARMS Infrastructure: Database Adapter

Provides a unified interface for PostgreSQL (RDS) and Redis (ElastiCache)
persistence, replacing the flat JSON file approach used during development.

Architecture:
  - PostgreSQL: Durable storage for regime history, CDF state, PDS HWM,
    TDC/TIS records, execution audit trail, and session logs.
  - Redis: Sub-second cache for current regime score, live portfolio weights,
    ARAS hysteresis state, and inter-module communication within a single cycle.

Both connections are configured via environment variables.
When credentials are not available, the adapter falls back gracefully
to local JSON file persistence (development mode).

Reference: ARMS Infrastructure Specification v1.0
"""

import json
import os
import datetime
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger('arms.db')


# ═══════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════

@dataclass
class PostgresConfig:
    host: str
    port: int
    database: str
    user: str
    password: str
    ssl_mode: str = 'require'

    @classmethod
    def from_env(cls) -> Optional['PostgresConfig']:
        host = os.environ.get('ARMS_PG_HOST')
        if not host:
            return None
        return cls(
            host=host,
            port=int(os.environ.get('ARMS_PG_PORT', '5432')),
            database=os.environ.get('ARMS_PG_DATABASE', 'arms'),
            user=os.environ.get('ARMS_PG_USER', 'arms_app'),
            password=os.environ.get('ARMS_PG_PASSWORD', ''),
            ssl_mode=os.environ.get('ARMS_PG_SSL_MODE', 'require'),
        )


@dataclass
class RedisConfig:
    host: str
    port: int
    db: int = 0
    password: str = ''
    ssl: bool = True

    @classmethod
    def from_env(cls) -> Optional['RedisConfig']:
        host = os.environ.get('ARMS_REDIS_HOST')
        if not host:
            return None
        return cls(
            host=host,
            port=int(os.environ.get('ARMS_REDIS_PORT', '6379')),
            db=int(os.environ.get('ARMS_REDIS_DB', '0')),
            password=os.environ.get('ARMS_REDIS_PASSWORD', ''),
            ssl=os.environ.get('ARMS_REDIS_SSL', 'true').lower() == 'true',
        )


# ═══════════════════════════════════════════════════════════════
# PostgreSQL Adapter
# ═══════════════════════════════════════════════════════════════

class PostgresAdapter:
    """
    PostgreSQL adapter for durable ARMS state persistence.
    Falls back to local JSON when RDS is not configured.
    """

    def __init__(self, config: Optional[PostgresConfig] = None):
        self.config = config or PostgresConfig.from_env()
        self._conn = None
        self._fallback = self.config is None

        if self._fallback:
            logger.info("[DB] PostgreSQL not configured — using local JSON fallback")
        else:
            logger.info(f"[DB] PostgreSQL configured: {self.config.host}:{self.config.port}/{self.config.database}")

    def connect(self):
        if self._fallback:
            return
        try:
            import psycopg2
            self._conn = psycopg2.connect(
                host=self.config.host,
                port=self.config.port,
                dbname=self.config.database,
                user=self.config.user,
                password=self.config.password,
                sslmode=self.config.ssl_mode,
                connect_timeout=10,
            )
            self._conn.autocommit = False
            logger.info("[DB] PostgreSQL connection established")
        except Exception as e:
            logger.warning(f"[DB] PostgreSQL connection failed, falling back to JSON: {e}")
            self._fallback = True

    def disconnect(self):
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    @property
    def is_connected(self) -> bool:
        if self._fallback:
            return False
        if self._conn is None:
            return False
        try:
            self._conn.cursor().execute("SELECT 1")
            return True
        except Exception:
            return False

    def initialize_schema(self):
        """Create ARMS tables if they don't exist."""
        if self._fallback:
            return

        ddl = '''
        CREATE TABLE IF NOT EXISTS regime_history (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            score NUMERIC(5,4) NOT NULL,
            regime VARCHAR(20) NOT NULL,
            equity_ceiling_pct NUMERIC(5,4),
            queue_status VARCHAR(50),
            catalyst TEXT,
            note TEXT
        );

        CREATE TABLE IF NOT EXISTS cdf_state (
            ticker VARCHAR(10) PRIMARY KEY,
            underperforming_days INT NOT NULL DEFAULT 0,
            last_underperformance_pp NUMERIC(8,4) DEFAULT 0,
            last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS pds_state (
            id INT PRIMARY KEY DEFAULT 1,
            high_water_mark NUMERIC(16,2) NOT NULL,
            last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS tdc_state (
            ticker VARCHAR(10) PRIMARY KEY,
            tis_score NUMERIC(4,2),
            tis_label VARCHAR(20),
            bear_case TEXT,
            bull_case TEXT,
            last_reviewed TIMESTAMPTZ,
            consecutive_broken_days INT DEFAULT 0,
            last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS execution_log (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            correlation_id VARCHAR(64),
            action_type VARCHAR(30) NOT NULL,
            ticker VARCHAR(10),
            side VARCHAR(4),
            quantity NUMERIC(16,4),
            price NUMERIC(16,4),
            triggering_module VARCHAR(50),
            tier INT,
            details JSONB
        );

        CREATE TABLE IF NOT EXISTS session_log (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            action_type VARCHAR(50) NOT NULL,
            triggering_module VARCHAR(50),
            triggering_signal TEXT,
            ticker VARCHAR(10),
            correlation_id VARCHAR(64),
            details JSONB
        );

        CREATE INDEX IF NOT EXISTS idx_regime_history_ts ON regime_history(timestamp);
        CREATE INDEX IF NOT EXISTS idx_execution_log_ts ON execution_log(timestamp);
        CREATE INDEX IF NOT EXISTS idx_execution_log_ticker ON execution_log(ticker);
        CREATE INDEX IF NOT EXISTS idx_session_log_ts ON session_log(timestamp);
        '''
        try:
            cur = self._conn.cursor()
            cur.execute(ddl)
            self._conn.commit()
            logger.info("[DB] Schema initialized")
        except Exception as e:
            self._conn.rollback()
            logger.error(f"[DB] Schema initialization failed: {e}")

    # ── Regime History ──

    def insert_regime_history(self, score: float, regime: str, ceiling: float,
                               queue_status: str = '', catalyst: str = '', note: str = ''):
        if self._fallback:
            return self._json_append('regime_history', {
                'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'score': score, 'regime': regime, 'equity_ceiling_pct': ceiling,
                'queue_status': queue_status, 'catalyst': catalyst, 'note': note,
            })
        try:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO regime_history (score, regime, equity_ceiling_pct, queue_status, catalyst, note) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (score, regime, ceiling, queue_status, catalyst, note)
            )
            self._conn.commit()
        except Exception as e:
            self._conn.rollback()
            logger.error(f"[DB] insert_regime_history failed: {e}")

    def get_regime_history(self, limit: int = 100) -> List[Dict]:
        if self._fallback:
            return self._json_read('regime_history', limit)
        try:
            cur = self._conn.cursor()
            cur.execute("SELECT timestamp, score, regime, equity_ceiling_pct, queue_status, catalyst, note "
                        "FROM regime_history ORDER BY timestamp DESC LIMIT %s", (limit,))
            cols = ['timestamp', 'score', 'regime', 'equity_ceiling_pct', 'queue_status', 'catalyst', 'note']
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception as e:
            logger.error(f"[DB] get_regime_history failed: {e}")
            return []

    # ── CDF State ──

    def upsert_cdf_state(self, ticker: str, underperforming_days: int, underperformance_pp: float):
        if self._fallback:
            return self._json_upsert('cdf_state', ticker, {
                'ticker': ticker, 'underperforming_days': underperforming_days,
                'last_underperformance_pp': underperformance_pp,
            })
        try:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO cdf_state (ticker, underperforming_days, last_underperformance_pp, last_updated) "
                "VALUES (%s, %s, %s, NOW()) "
                "ON CONFLICT (ticker) DO UPDATE SET "
                "underperforming_days = EXCLUDED.underperforming_days, "
                "last_underperformance_pp = EXCLUDED.last_underperformance_pp, "
                "last_updated = NOW()",
                (ticker, underperforming_days, underperformance_pp)
            )
            self._conn.commit()
        except Exception as e:
            self._conn.rollback()
            logger.error(f"[DB] upsert_cdf_state failed: {e}")

    # ── PDS High Water Mark ──

    def upsert_hwm(self, hwm: float):
        if self._fallback:
            return self._json_upsert('pds_state', '1', {'high_water_mark': hwm})
        try:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO pds_state (id, high_water_mark, last_updated) VALUES (1, %s, NOW()) "
                "ON CONFLICT (id) DO UPDATE SET high_water_mark = GREATEST(pds_state.high_water_mark, EXCLUDED.high_water_mark), "
                "last_updated = NOW()",
                (hwm,)
            )
            self._conn.commit()
        except Exception as e:
            self._conn.rollback()
            logger.error(f"[DB] upsert_hwm failed: {e}")

    def get_hwm(self) -> Optional[float]:
        if self._fallback:
            rec = self._json_get('pds_state', '1')
            return rec.get('high_water_mark') if rec else None
        try:
            cur = self._conn.cursor()
            cur.execute("SELECT high_water_mark FROM pds_state WHERE id = 1")
            row = cur.fetchone()
            return float(row[0]) if row else None
        except Exception as e:
            logger.error(f"[DB] get_hwm failed: {e}")
            return None

    # ── Session Log ──

    def insert_session_log(self, entry: Dict):
        if self._fallback:
            return self._json_append('session_log', entry)
        try:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO session_log (action_type, triggering_module, triggering_signal, ticker, correlation_id, details) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (entry.get('action_type'), entry.get('triggering_module'),
                 entry.get('triggering_signal'), entry.get('ticker'),
                 entry.get('correlation_id'), json.dumps(entry.get('details', {})))
            )
            self._conn.commit()
        except Exception as e:
            self._conn.rollback()
            logger.error(f"[DB] insert_session_log failed: {e}")

    # ── JSON Fallback Helpers ──

    _JSON_BASE = os.path.join('achelion_arms', 'db_fallback')

    def _json_path(self, table: str) -> str:
        path = os.path.join(self._JSON_BASE, f'{table}.json')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def _json_append(self, table: str, record: Dict):
        path = self._json_path(table)
        data = []
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    data = json.load(f)
            except Exception:
                data = []
        data.append(record)
        with open(path, 'w') as f:
            json.dump(data, f, indent=2, default=str)

    def _json_read(self, table: str, limit: int = 100) -> List[Dict]:
        path = self._json_path(table)
        if not os.path.exists(path):
            return []
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            return data[-limit:]
        except Exception:
            return []

    def _json_upsert(self, table: str, key: str, record: Dict):
        path = self._json_path(table)
        data = {}
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    data = json.load(f)
            except Exception:
                data = {}
        data[key] = record
        with open(path, 'w') as f:
            json.dump(data, f, indent=2, default=str)

    def _json_get(self, table: str, key: str) -> Optional[Dict]:
        path = self._json_path(table)
        if not os.path.exists(path):
            return None
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            return data.get(key)
        except Exception:
            return None


# ═══════════════════════════════════════════════════════════════
# Redis Adapter
# ═══════════════════════════════════════════════════════════════

class RedisAdapter:
    """
    Redis adapter for real-time ARMS state caching.
    Falls back to in-memory dict when ElastiCache is not configured.
    """

    def __init__(self, config: Optional[RedisConfig] = None):
        self.config = config or RedisConfig.from_env()
        self._client = None
        self._fallback = self.config is None
        self._memory: Dict[str, str] = {}

        if self._fallback:
            logger.info("[CACHE] Redis not configured — using in-memory fallback")
        else:
            logger.info(f"[CACHE] Redis configured: {self.config.host}:{self.config.port}")

    def connect(self):
        if self._fallback:
            return
        try:
            import redis
            self._client = redis.Redis(
                host=self.config.host,
                port=self.config.port,
                db=self.config.db,
                password=self.config.password or None,
                ssl=self.config.ssl,
                socket_timeout=5,
                decode_responses=True,
            )
            self._client.ping()
            logger.info("[CACHE] Redis connection established")
        except Exception as e:
            logger.warning(f"[CACHE] Redis connection failed, falling back to memory: {e}")
            self._fallback = True

    def disconnect(self):
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    @property
    def is_connected(self) -> bool:
        if self._fallback:
            return False
        try:
            return self._client.ping() if self._client else False
        except Exception:
            return False

    def set(self, key: str, value: Any, ttl_seconds: int = 3600):
        serialized = json.dumps(value, default=str) if not isinstance(value, str) else value
        if self._fallback:
            self._memory[key] = serialized
            return
        try:
            self._client.setex(key, ttl_seconds, serialized)
        except Exception as e:
            logger.warning(f"[CACHE] Redis SET failed for {key}: {e}")
            self._memory[key] = serialized

    def get(self, key: str) -> Optional[Any]:
        if self._fallback:
            raw = self._memory.get(key)
        else:
            try:
                raw = self._client.get(key)
            except Exception as e:
                logger.warning(f"[CACHE] Redis GET failed for {key}: {e}")
                raw = self._memory.get(key)

        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw

    def delete(self, key: str):
        if self._fallback:
            self._memory.pop(key, None)
            return
        try:
            self._client.delete(key)
        except Exception as e:
            logger.warning(f"[CACHE] Redis DELETE failed for {key}: {e}")

    # ── ARMS-Specific Cache Keys ──

    def cache_regime_score(self, score: float, regime: str, ceiling: float):
        self.set('arms:regime:current', {
            'score': score, 'regime': regime, 'ceiling': ceiling,
            'updated': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }, ttl_seconds=600)

    def get_cached_regime(self) -> Optional[Dict]:
        return self.get('arms:regime:current')

    def cache_portfolio_weights(self, weights: Dict[str, float]):
        self.set('arms:portfolio:weights', weights, ttl_seconds=600)

    def get_cached_weights(self) -> Optional[Dict[str, float]]:
        return self.get('arms:portfolio:weights')

    def cache_aras_state(self, state: Dict):
        self.set('arms:aras:state', state, ttl_seconds=600)

    def get_cached_aras_state(self) -> Optional[Dict]:
        return self.get('arms:aras:state')


# ═══════════════════════════════════════════════════════════════
# Singleton Instances
# ═══════════════════════════════════════════════════════════════

db = PostgresAdapter()
cache = RedisAdapter()
