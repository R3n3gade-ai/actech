"""
ARMS Execution: Strategic Deployment Queue

This module manages dormant, regime-triggered multi-leg orders.
Unlike the reactive Confirmation Queue, the Strategic Queue holds
proactive PM deployment intent (e.g. "Buy GOOGL at 3.5% weight when
regime score drops <= 0.65"). It continuously monitors the ARAS output
and automatically fires these orders into the LAEP Order Book when
conditions are met.

Reference: Daily Monitor Section 4 (Deployment Queue)
"""

import json
import os
from dataclasses import dataclass
from typing import List, Optional, Dict, Any, Literal
from engine.aras import ArasOutput
from reporting.audit_log import SessionLogEntry, append_to_log
from execution.order_request import OrderRequest

@dataclass
class StrategicQueueItem:
    id_num: int
    ticker: str
    target_weight_pct: float
    execution_instruction: str
    trigger_condition_type: str # 'REGIME_SCORE', 'PRICE', 'EVENT'
    trigger_threshold: float
    status: Literal['DORMANT', 'WATCH', 'TRIGGERED', 'EXECUTED']
    
    def evaluate(self, current_aras: ArasOutput) -> bool:
        """Returns True if the order should fire based on current ARAS state."""
        if self.trigger_condition_type == 'REGIME_SCORE':
            return current_aras.score <= self.trigger_threshold
        return False

class StrategicQueueManager:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.items: List[StrategicQueueItem] = []
        self.load_queue()
        
    def load_queue(self):
        if not os.path.exists(self.config_path):
            self.items = []
            return
            
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.items = []
                for i, item in enumerate(data, 1):
                    self.items.append(StrategicQueueItem(
                        id_num=i,
                        ticker=item['ticker'],
                        target_weight_pct=item['target_weight_pct'],
                        execution_instruction=item['execution_instruction'],
                        trigger_condition_type=item.get('trigger_condition_type', 'REGIME_SCORE'),
                        trigger_threshold=item['trigger_threshold'],
                        status=item.get('status', 'DORMANT')
                    ))
        except Exception as e:
            print(f"[StrategicQueue] Error loading queue: {e}")
            self.items = []

    def evaluate_queue(self, current_aras: ArasOutput) -> List[OrderRequest]:
        """
        Evaluates the queue against the current regime and returns
        any OrderRequests that should be fired to the LAEP.
        """
        fired_orders = []
        
        for item in self.items:
            # Determine WATCH state (within 0.10 of trigger)
            if item.status in ['DORMANT', 'WATCH']:
                if current_aras.score <= item.trigger_threshold:
                    item.status = 'TRIGGERED'
                    print(f"[StrategicQueue] TRIGGERED: {item.ticker} at Score {current_aras.score:.2f}")
                    
                    # Log the trigger
                    append_to_log(SessionLogEntry(
                        timestamp="", # handled in append_to_log
                        action_type='STRATEGIC_QUEUE_FIRE',
                        triggering_module='STRATEGIC_QUEUE',
                        triggering_signal=f"ARAS Score {current_aras.score:.2f} hit trigger {item.trigger_threshold} for {item.ticker}",
                        ticker=item.ticker
                    ))
                    
                    # Generate the order to pass to L4/L5
                    fired_orders.append(OrderRequest(
                        ticker=item.ticker,
                        action='BUY',
                        quantity=item.target_weight_pct, # Passed as target weight initially
                        quantity_kind='WEIGHT_PCT',
                        order_type='VWAP', # Default per spec
                        execution_window_min=60,
                        slippage_budget_bps=15.0,
                        priority=2,
                        triggering_module='STRATEGIC_QUEUE',
                        triggering_signal=item.execution_instruction,
                        tier=0,
                        confirmation_required=False,
                        veto_window_hours=0.0
                    ))
                elif current_aras.score <= (item.trigger_threshold + 0.10):
                    item.status = 'WATCH'
                else:
                    item.status = 'DORMANT'
                    
        return fired_orders
