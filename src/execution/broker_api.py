# src/execution/broker_api.py
# IBKR broker adapter for ARMS paper-trading integration.

from __future__ import annotations

import logging
import math
import os
from typing import Dict, List, Literal, Optional, Tuple

import datetime

try:
    import ib_insync as ibi
    IB_AVAILABLE = True
except ImportError:
    IB_AVAILABLE = False
    logging.warning("ib_insync not found. Broker API unavailable for live cycle.")

from .interfaces import Broker, Position
from .order_request import OrderRequest


class IBKRBroker(Broker):
    """
    Interactive Brokers adapter using ib_insync.

    Current state:
    - Real paper connection required; no simulated connected-mode fallback.
    - Position snapshot implemented.
    - Order submission restricted to paper mode.
    - Order-status mapping partially implemented and under active hardening.
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        client_id: Optional[int] = None,
        paper: bool = True,
        connect_timeout_s: float = 10.0,
    ):
        # Detect connection settings from environment first so the adapter matches
        # the live paper-trading setup instead of assuming scaffold defaults.
        env_host = os.environ.get('IB_HOST') or os.environ.get('TWS_HOST')
        env_port = os.environ.get('IB_PORT') or os.environ.get('TWS_PORT')
        env_client_id = os.environ.get('IB_CLIENT_ID') or os.environ.get('TWS_CLIENT_ID')
        env_paper = os.environ.get('IB_PAPER')

        self.host = host or env_host or '127.0.0.1'
        self.port = int(port if port is not None else (env_port or 4002))
        self.client_id = int(client_id if client_id is not None else (env_client_id or 1))
        self.paper = paper if env_paper is None else env_paper.strip().lower() not in {'0', 'false', 'no'}
        self.connect_timeout_s = connect_timeout_s
        
        self.ib: Optional['ibi.IB'] = None
        self.is_connected = False

        print(
            f"[IBKRBroker] Initialized host={self.host} port={self.port} "
            f"client_id={self.client_id} paper={self.paper}"
        )

    def _require_connection(self):
        if not IB_AVAILABLE:
            raise RuntimeError("ib_insync backend unavailable; live broker operations cannot proceed.")
        if not self.is_connected or not getattr(self, 'ib', None):
            raise ConnectionError("Broker is not connected.")

    def _paper_guard(self):
        if not self.paper:
            raise RuntimeError(
                "Live trading is not enabled in this adapter path. "
                "Use paper=True until execution hardening is complete."
            )

    def connect(self):
        """Establish connection to IB Gateway / TWS."""
        self._paper_guard()

        if not IB_AVAILABLE:
            raise RuntimeError("ib_insync not installed; refusing live-cycle broker fallback.")

        print(
            f"[IBKRBroker] Connecting to IBKR at {self.host}:{self.port} "
            f"with client_id={self.client_id}..."
        )
        
        try:
            self.ib = ibi.IB()
            self.ib.connect(
                self.host,
                self.port,
                clientId=self.client_id,
                timeout=self.connect_timeout_s
            )
            self.is_connected = self.ib.isConnected()

            if self.is_connected:
                print(f"[IBKRBroker] Connected. Managed accounts: {self.ib.managedAccounts()}")
            else:
                self.ib = None
                raise RuntimeError("IBKR connection failed without exception; no live broker session established.")

        except Exception as e:
            self.is_connected = False
            self.ib = None
            raise RuntimeError(f"IBKR connection failed: {e}") from e

    def disconnect(self):
        if self.is_connected:
            print("[IBKRBroker] Disconnecting...")
            if self.ib:
                self.ib.disconnect()
        self.is_connected = False
        self.ib = None
        print("[IBKRBroker] Disconnected.")

    def get_positions(self) -> List[Position]:
        """Fetch current portfolio positions from the broker."""
        self._require_connection()

        if not IB_AVAILABLE or getattr(self, 'ib', None) is None:
            raise RuntimeError("ib_insync backend unavailable or disconnected; refusing scaffold portfolio state in live cycle.")

        print("[IBKRBroker] Fetching live positions...")

        normalized = []
        try:
            portfolio_items = self.ib.portfolio()
            for item in portfolio_items:
                contract = item.contract
                market_value = float(item.marketValue)
                avg_cost = float(item.averageCost)
                quantity = float(item.position)
                normalized.append(Position(
                    ticker=contract.symbol,
                    sec_type=contract.secType,
                    quantity=quantity,
                    average_cost=avg_cost,
                    market_value=market_value,
                    con_id=contract.conId,
                    expiry=getattr(contract, 'lastTradeDateOrContractMonth', None),
                    strike=float(contract.strike) if getattr(contract, 'strike', None) not in (None, '') else None,
                    right=getattr(contract, 'right', None),
                    multiplier=float(contract.multiplier) if getattr(contract, 'multiplier', None) not in (None, '') else None
                ))
        except Exception as e:
            print(f"[IBKRBroker] portfolio() lookup failed ({e}); attempting positions()+ticker fallback.")
            ib_positions = self.ib.positions()
            contracts = [p.contract for p in ib_positions]
            tickers = self.ib.reqTickers(*contracts) if contracts else []
            ticker_map = {t.contract.conId: t for t in tickers}

            for p in ib_positions:
                ticker = ticker_map.get(p.contract.conId)
                market_price = ticker.marketPrice() if ticker else None
                quantity = float(p.position)
                market_value = float(quantity * market_price) if market_price not in (None, 0, -1) else 0.0
                normalized.append(Position(
                    ticker=p.contract.symbol,
                    sec_type=p.contract.secType,
                    quantity=quantity,
                    average_cost=float(p.avgCost),
                    market_value=market_value,
                    con_id=p.contract.conId,
                    expiry=getattr(p.contract, 'lastTradeDateOrContractMonth', None),
                    strike=float(p.contract.strike) if getattr(p.contract, 'strike', None) not in (None, '') else None,
                    right=getattr(p.contract, 'right', None),
                    multiplier=float(p.contract.multiplier) if getattr(p.contract, 'multiplier', None) not in (None, '') else None
                ))

        print(f"[IBKRBroker] Retrieved {len(normalized)} positions with market values.")
        return normalized

    def get_nav(self) -> float:
        """Fetch current Net Asset Value from the broker."""
        self._require_connection()

        if not IB_AVAILABLE or getattr(self, 'ib', None) is None:
            raise RuntimeError("ib_insync backend unavailable or disconnected; refusing scaffold NAV in live cycle.")

        print("[IBKRBroker] Fetching live NAV...")
        try:
            account_values = self.ib.accountValues()
            for val in account_values:
                if val.tag == 'NetLiquidation' and val.currency == 'USD':
                    nav = float(val.value)
                    print(f"[IBKRBroker] Live NAV retrieved: ${nav:,.2f}")
                    return nav
        except Exception as e:
            raise RuntimeError(f"Failed to fetch live NAV from IBKR: {e}")

        raise RuntimeError("NetLiquidation USD value not returned by IBKR.")

    def _resolve_equity_quantity_from_notional(self, ticker: str, contract: 'ibi.Contract', notional_usd: float) -> int:
        qualified = self.ib.qualifyContracts(contract)
        if not qualified:
            raise RuntimeError(f"Could not qualify equity contract for {ticker}.")

        ticker_snapshot = self.ib.reqTickers(qualified[0])[0]
        market_price = ticker_snapshot.marketPrice()
        close_price = ticker_snapshot.close

        chosen_price = None
        price_source = None

        # Tier 1: Live market price
        if market_price not in (None, 0, -1) and not math.isnan(market_price):
            chosen_price = float(market_price)
            price_source = 'marketPrice'
        # Tier 2: Previous session close
        elif close_price not in (None, 0, -1) and not math.isnan(close_price):
            chosen_price = float(close_price)
            price_source = 'close'
        # Tier 3: Historical daily bar (most recent)
        else:
            print(f"[IBKRBroker] marketPrice and close unavailable for {ticker}; falling back to historical bars.")
            try:
                bars = self.ib.reqHistoricalData(
                    qualified[0],
                    endDateTime='',
                    durationStr='5 D',
                    barSizeSetting='1 day',
                    whatToShow='TRADES',
                    useRTH=True,
                    formatDate=1
                )
                if bars:
                    chosen_price = float(bars[-1].close)
                    price_source = 'historicalBar'
            except Exception as hist_err:
                print(f"[IBKRBroker] Historical bar fallback failed for {ticker}: {hist_err}")

        if chosen_price is None or chosen_price <= 0:
            raise RuntimeError(
                f"No valid price source available for {ticker} (tried marketPrice, close, historicalBar); "
                f"refusing notional conversion in live cycle."
            )

        print(f"[IBKRBroker] Using {price_source}={chosen_price:.4f} for notional conversion on {ticker}")
        return max(1, int(round(float(notional_usd) / chosen_price)))

    def submit_order(self, order: OrderRequest) -> str:
        self._require_connection()
        self._paper_guard()

        if order.tier == 1 and not order.confirmation_required:
            raise ValueError("Tier 1 orders must set confirmation_required=True.")
        if order.order_type == 'LIMIT' and order.limit_price is None:
            raise ValueError("LIMIT orders require limit_price.")
        if not IB_AVAILABLE or not self.ib:
            raise RuntimeError("ib_insync backend unavailable; refusing scaffold order submission.")

        print(f"[IBKRBroker] Preparing live paper order for {order.action} {order.quantity} {order.quantity_kind} {order.ticker}")

        # 1. Contract Resolution
        if order.action in {'BUY_PUT', 'SELL_PUT', 'SELL_CALL'}:
            if order.con_id:
                contract = ibi.Contract(conId=order.con_id)
                self.ib.qualifyContracts(contract)
            elif order.expiry and order.strike is not None and order.option_right:
                contract = ibi.Option(order.ticker, order.expiry.replace('-', ''), float(order.strike), order.option_right, 'SMART')
                qualified = self.ib.qualifyContracts(contract)
                if not qualified:
                    raise RuntimeError(
                        f"Could not qualify option contract for {order.ticker} "
                        f"{order.expiry} {order.strike} {order.option_right}."
                    )
            else:
                raise ValueError(
                    f"Option order for {order.ticker} requires either con_id or all of "
                    f"expiry/strike/option_right. Got: con_id={order.con_id}, "
                    f"expiry={order.expiry}, strike={order.strike}, right={order.option_right}. "
                    f"Upstream module (PTRH/DSHP) must resolve contract metadata before submission."
                )
        else:
            contract = ibi.Stock(order.ticker, 'SMART', 'USD')

        # 2. Quantity semantics
        if order.quantity_kind == 'NOTIONAL_USD':
            if order.action in {'BUY_PUT', 'SELL_PUT', 'SELL_CALL'}:
                # Resolve notional→contracts via option mid-price
                try:
                    tickers = self.ib.reqTickers(contract)
                    opt_ticker = tickers[0] if tickers else None
                    if opt_ticker and opt_ticker.bid and opt_ticker.ask:
                        mid = (opt_ticker.bid + opt_ticker.ask) / 2.0
                        multiplier = float(getattr(contract, 'multiplier', 100) or 100)
                        per_contract_cost = mid * multiplier
                        if per_contract_cost > 0:
                            broker_quantity = max(1, int(round(order.quantity / per_contract_cost)))
                        else:
                            raise RuntimeError(f"Zero per-contract cost for {order.ticker}; cannot convert notional.")
                    else:
                        raise RuntimeError(
                            f"No bid/ask available for {order.ticker} option contract; "
                            f"cannot convert NOTIONAL_USD to contract count."
                        )
                except RuntimeError:
                    raise
                except Exception as e:
                    raise RuntimeError(
                        f"Failed to resolve notional→contracts for {order.ticker}: {e}"
                    ) from e
            else:
                broker_quantity = self._resolve_equity_quantity_from_notional(order.ticker, contract, order.quantity)
        else:
            broker_quantity = float(order.quantity)

        # 3. Order Translation
        ib_action = 'BUY' if order.action in {'BUY', 'BUY_PUT'} else 'SELL'

        if order.order_type == 'MARKET':
            ib_order = ibi.MarketOrder(ib_action, broker_quantity)
        elif order.order_type == 'LIMIT':
            ib_order = ibi.LimitOrder(ib_action, broker_quantity, float(order.limit_price or 0.0))
        elif order.order_type == 'VWAP':
            ib_order = ibi.Order(
                action=ib_action,
                totalQuantity=broker_quantity,
                orderType='VWAP',
                algoStrategy='VWAP',
                algoParams=[ibi.TagValue('maxPctVol', '0.1')]
            )
        else:
            raise ValueError(f"Unsupported order type: {order.order_type}")
            
        # 3. Submission
        print(f"[IBKRBroker] Submitting to IBKR: {contract} -> {ib_order}")
        trade = self.ib.placeOrder(contract, ib_order)
        
        # In a real async environment, we wouldn't block here endlessly, but for
        # the synchronous orchestrator we might wait briefly or return the ID immediately.
        
        order_id = f"ibkr_live_{trade.order.orderId}" if trade.order.orderId else f"ibkr_temp_{id(trade)}"
        print(f"[IBKRBroker] Order {order.correlation_id} submitted. Temporary ID: {order_id}")
        return order_id

    def get_order_status(self, order_id: str) -> Literal['PENDING', 'FILLED', 'FAILED', 'CANCELLED']:
        self._require_connection()

        if not IB_AVAILABLE or not self.ib:
            raise RuntimeError("ib_insync backend unavailable; refusing scaffold status lookup.")

        print(f"[IBKRBroker] Checking status for {order_id}...")

        # Refresh open-order state from TWS
        self.ib.sleep(0)  # Process pending events

        trades = self.ib.trades()
        for trade in trades:
            ib_order_id = getattr(trade.order, 'orderId', None)
            # Match against both ibkr_live_NNN and ibkr_temp_NNN ID formats
            candidate_live = f"ibkr_live_{ib_order_id}" if ib_order_id is not None else None
            candidate_temp = f"ibkr_temp_{id(trade)}"
            if order_id not in (candidate_live, candidate_temp):
                continue

            status = (trade.orderStatus.status or '').lower()

            if status in {'filled'}:
                print(f"[IBKRBroker] Order {order_id} → FILLED")
                return 'FILLED'
            if status in {'cancelled', 'pendingcancel', 'apicancelled'}:
                print(f"[IBKRBroker] Order {order_id} → CANCELLED (ib_status={status})")
                return 'CANCELLED'
            if status in {'inactive'}:
                print(f"[IBKRBroker] Order {order_id} → FAILED (ib_status=inactive)")
                return 'FAILED'
            if status in {'submitted', 'presubmitted', 'pendingsubmit', 'apipending'}:
                print(f"[IBKRBroker] Order {order_id} → PENDING (ib_status={status})")
                return 'PENDING'

            # Unknown status — log and treat as PENDING rather than crashing
            print(f"[IBKRBroker] Order {order_id} has unknown IBKR status '{status}'; treating as PENDING.")
            return 'PENDING'

        # Order not found in current trade list — may have been purged after fill
        print(f"[IBKRBroker] Order {order_id} not found in active trade list; returning FILLED (assumed purged).")
        return 'FILLED'

    def get_options_chain(self, ticker: str, exchange: str = 'SMART') -> List['ibi.Option']:
        """
        Fetch the current options chain for the given underlying.
        Used by PTRH Adaptive Strike Protocol to find optimal delta contracts.
        """
        self._require_connection()
        if not IB_AVAILABLE or not self.ib:
            raise RuntimeError(f"ib_insync backend unavailable; cannot fetch options chain for {ticker}.")

        print(f"[IBKRBroker] Fetching options chain for {ticker}...")
        underlying = ibi.Index(ticker, exchange) if ticker in ['SPX', 'VIX'] else ibi.Stock(ticker, exchange, 'USD')
        self.ib.qualifyContracts(underlying)
        
        chains = self.ib.reqSecDefOptParams(underlying.symbol, '', underlying.secType, underlying.conId)
        if not chains:
            print(f"[IBKRBroker] No options chain returned for {ticker}.")
            return []
            
        chain = next((c for c in chains if c.exchange == exchange), chains[0])
        print(f"[IBKRBroker] Retrieved chain with {len(chain.expirations)} expiries and {len(chain.strikes)} strikes.")
        
        # Build raw option contracts for later filtering and market data request
        contracts = []
        for exp in chain.expirations:
            for strike in chain.strikes:
                contracts.append(ibi.Option(ticker, exp, strike, 'P', exchange))
        
        # Qualify contracts (batch in real life, but here we return raw and let the engine qualify subsets)
        return contracts

    def get_market_data(self, contracts: List['ibi.Contract'], require_greeks: bool = False) -> List['ibi.Ticker']:
        """
        Fetch a live market data snapshot for a list of contracts.
        Used to calculate Deltas and bid/ask spreads.

        If require_greeks=True and modelGreeks are missing from the initial
        snapshot, a second attempt is made with reqMktData + brief sleep to
        allow the TWS model to populate Greeks.  Contracts that still lack
        Greeks after the retry are included with a warning but not dropped,
        so the caller (PTRH) can apply its own Gate 4 abort logic.
        """
        self._require_connection()
        if not IB_AVAILABLE or not self.ib:
            raise RuntimeError("ib_insync backend unavailable; cannot fetch market data.")

        qualified = self.ib.qualifyContracts(*contracts)
        tickers = self.ib.reqTickers(*qualified)

        if require_greeks:
            missing_greeks = [t for t in tickers if not getattr(t, 'modelGreeks', None)]
            if missing_greeks:
                print(f"[IBKRBroker] {len(missing_greeks)}/{len(tickers)} contracts missing Greeks; "
                      f"requesting computed Greeks via reqMktData...")
                for t in missing_greeks:
                    self.ib.reqMktData(t.contract, genericTickList='106', snapshot=False)
                self.ib.sleep(2)  # Allow TWS model to compute Greeks
                # Re-read tickers after Greek computation
                tickers = self.ib.reqTickers(*qualified)
                still_missing = sum(1 for t in tickers if not getattr(t, 'modelGreeks', None))
                if still_missing:
                    print(f"[IBKRBroker] Warning: {still_missing} contracts still lack Greeks after retry.")

        return tickers

    def get_recent_close(self, ticker: str, days_ago: int = 45) -> Optional[float]:
        """Fetch an approximate historical close via IBKR daily bars."""
        self._require_connection()
        if not IB_AVAILABLE or not self.ib:
            return None

        contract = ibi.Stock(ticker, 'SMART', 'USD')
        qualified = self.ib.qualifyContracts(contract)
        if not qualified:
            return None

        duration_days = max(60, days_ago + 10)
        bars = self.ib.reqHistoricalData(
            qualified[0],
            endDateTime='',
            durationStr=f'{duration_days} D',
            barSizeSetting='1 day',
            whatToShow='TRADES',
            useRTH=True,
            formatDate=1
        )
        if not bars:
            return None

        idx = max(0, len(bars) - (days_ago + 1))
        return float(bars[idx].close)

    def get_session_returns(self, tickers: List[str]) -> Dict[str, float]:
        """
        Batch-fetch intraday session return for each ticker.

        Uses IBKR reqTickers to get marketPrice (current) and close (previous
        session close), then computes (current - prev_close) / prev_close.
        Returns a dict of ticker -> session return (e.g. 0.012 = +1.2%).
        Tickers where data is unavailable are omitted.
        """
        if not self.is_connected or not IB_AVAILABLE or not self.ib:
            return {}
        contracts = [ibi.Stock(t, 'SMART', 'USD') for t in tickers]
        qualified = self.ib.qualifyContracts(*contracts)
        if not qualified:
            return {}
        snapshots = self.ib.reqTickers(*qualified)
        result: Dict[str, float] = {}
        for snap in snapshots:
            symbol = snap.contract.symbol
            current = snap.marketPrice()
            prev_close = snap.close
            if (current in (None, 0, -1) or math.isnan(current)
                    or prev_close in (None, 0, -1) or math.isnan(prev_close)):
                continue
            result[symbol] = (float(current) - float(prev_close)) / float(prev_close)
        return result
