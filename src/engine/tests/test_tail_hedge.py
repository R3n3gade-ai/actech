"""Unit tests for the PTRH Full Automation module."""

import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import tail_hedge
from tail_hedge import OptionsPosition, PTRHInputs, calculate_ptrh_required_notional, run_ptrh_module

class TestPTRHAutomation(unittest.TestCase):
    """
    Test suite for the run_ptrh_module function.
    We use mocking to simulate execution calls and isolate the logic.
    """

    def test_clean_run_no_action(self):
        """No action when coverage is aligned and no roll is needed."""
        ptrh_inputs = PTRHInputs(nav=50_000_000, regime_label="WATCH", regime_score=0.45)
        required = calculate_ptrh_required_notional(ptrh_inputs)
        positions = [
            OptionsPosition('QQQ', 'PUT', 450.0, (datetime.date.today() + datetime.timedelta(days=90)).isoformat(), 100, required)
        ]
        status = run_ptrh_module(ptrh_inputs, positions)

        self.assertEqual(status.last_action, "NONE")
        self.assertEqual(len(status.generated_orders), 0)

    def test_dte_roll_without_live_contract_emits_alert(self):
        """A roll becomes an alert when no replacement contract can be resolved."""
        ptrh_inputs = PTRHInputs(nav=50_000_000, regime_label="WATCH", regime_score=0.45)
        required = calculate_ptrh_required_notional(ptrh_inputs)
        positions = [
            OptionsPosition('QQQ', 'PUT', 450.0, (datetime.date.today() + datetime.timedelta(days=29)).isoformat(), 100, required)
        ]
        status = run_ptrh_module(ptrh_inputs, positions)

        self.assertEqual(status.last_action, "NONE")
        self.assertTrue(any('roll due' in alert.lower() for alert in status.alerts))

    def test_under_hedged_correction_generates_buy_order(self):
        """A 5%+ under-hedge should generate a buy order."""
        class FakeBroker:
            is_connected = True

        ptrh_inputs = PTRHInputs(nav=50_000_000, regime_label="WATCH", regime_score=0.45)
        required = calculate_ptrh_required_notional(ptrh_inputs)
        positions = [
            OptionsPosition('QQQ', 'PUT', 450.0, (datetime.date.today() + datetime.timedelta(days=90)).isoformat(), 80, required * 0.70)
        ]

        original = tail_hedge._resolve_delta_primary_strike
        tail_hedge._resolve_delta_primary_strike = lambda broker, ticker, target_delta=-0.35: OptionsPosition(
            ticker='QQQ', option_type='PUT', strike=400.0,
            expiry=(datetime.date.today() + datetime.timedelta(days=75)).isoformat(),
            contracts=1, notional_value=0.0, delta=-0.35, bid=10.0, ask=10.5,
        )
        try:
            status = run_ptrh_module(ptrh_inputs, positions, FakeBroker())
        finally:
            tail_hedge._resolve_delta_primary_strike = original

        self.assertEqual(status.last_action, "CORRECT_DRIFT")
        self.assertEqual(len(status.generated_orders), 1)
        self.assertEqual(status.generated_orders[0].action, 'BUY_PUT')

    def test_over_hedged_outside_tolerance_generates_sell_order(self):
        """A 15%+ over-hedge should generate a sell order."""
        ptrh_inputs = PTRHInputs(nav=50_000_000, regime_label="WATCH", regime_score=0.45)
        required = calculate_ptrh_required_notional(ptrh_inputs)
        positions = [
            OptionsPosition('QQQ', 'PUT', 450.0, (datetime.date.today() + datetime.timedelta(days=90)).isoformat(), 120, required * 1.20, bid=10.0, ask=10.5)
        ]
        status = run_ptrh_module(ptrh_inputs, positions)

        self.assertEqual(status.last_action, "CORRECT_DRIFT")
        self.assertEqual(len(status.generated_orders), 1)
        self.assertEqual(status.generated_orders[0].action, 'SELL_PUT')
        
    def test_over_hedged_inside_tolerance_is_tolerated(self):
        """A minor over-hedge should not trigger correction."""
        ptrh_inputs = PTRHInputs(nav=50_000_000, regime_label="WATCH", regime_score=0.45)
        required = calculate_ptrh_required_notional(ptrh_inputs)
        positions = [
            OptionsPosition('QQQ', 'PUT', 450.0, (datetime.date.today() + datetime.timedelta(days=90)).isoformat(), 110, required * 1.10)
        ]
        status = run_ptrh_module(ptrh_inputs, positions)

        self.assertEqual(status.last_action, "NONE")
        self.assertEqual(len(status.generated_orders), 0)

if __name__ == '__main__':
    unittest.main()
