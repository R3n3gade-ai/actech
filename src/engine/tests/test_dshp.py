"""
Unit tests for the Defensive Sleeve Harvest Protocol (DSHP) module.
"""

import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dshp import DefensivePosition, check_dshp_triggers
from config import dshp_config

class TestDshpEngine(unittest.TestCase):
    """Test suite for the DSHP engine logic."""

    def setUp(self):
        """Set a constant NAV for all tests."""
        self.NAV = 100_000_000.0

    def test_sgol_appreciation_trigger(self):
        """SGOL should trigger a harvest action when appreciation > threshold."""
        # Threshold is 20%
        entry_value = self.NAV * dshp_config.SGOL_TARGET_WEIGHT
        current_value = entry_value * 1.25  # 25% appreciation
        
        positions = {
            'SGOL': DefensivePosition(
                ticker='SGOL', entry_value=entry_value,
                current_value=current_value, current_weight=current_value / self.NAV
            )
        }
        actions = check_dshp_triggers(positions, self.NAV)
        
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action.instrument, 'SGOL')
        self.assertEqual(action.trigger, 'APPRECIATION')
        # Expected trim: 2.5M - (100M * 0.02) = 500k
        self.assertAlmostEqual(action.trim_amount_usd, 500000.0)

    def test_dbmf_appreciation_trigger(self):
        """DBMF should trigger on appreciation > 15%."""
        entry_value = self.NAV * dshp_config.DBMF_TARGET_WEIGHT
        current_value = entry_value * 1.18  # 18% appreciation
        
        positions = {
            'DBMF': DefensivePosition(
                ticker='DBMF', entry_value=entry_value,
                current_value=current_value, current_weight=current_value / self.NAV
            )
        }
        actions = check_dshp_triggers(positions, self.NAV)
        
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action.instrument, 'DBMF')
        self.assertEqual(action.trigger, 'APPRECIATION')

    def test_dbmf_weight_drift_trigger(self):
        """DBMF should trigger on weight drift > 1.5pp, even if appreciation is low."""
        # Target weight is 5.0%, drift threshold is 1.5pp -> trigger at 6.5%
        target_weight = dshp_config.DBMF_TARGET_WEIGHT
        current_weight = target_weight + dshp_config.DBMF_DRIFT_THRESHOLD + 0.001 # 6.6%
        
        positions = {
            'DBMF': DefensivePosition(
                ticker='DBMF',
                entry_value=self.NAV * target_weight, # 5M
                current_value=self.NAV * current_weight, # 6.6M (32% appreciation, will trigger this first)
                current_weight=current_weight
            )
        }
        # Let's make appreciation low so drift is the only trigger
        positions['DBMF'].entry_value = self.NAV * 0.06 # Make entry higher, so appreciation is low
        
        actions = check_dshp_triggers(positions, self.NAV)
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action.instrument, 'DBMF')
        self.assertEqual(action.trigger, 'WEIGHT_DRIFT')
        # Expected trim: 6.6M - (100M * 0.05) = 1.6M
        self.assertAlmostEqual(action.trim_amount_usd, 1600000.0)
        
    def test_no_trigger_within_thresholds(self):
        """No actions should be generated if all positions are within tolerance."""
        positions = {
            'SGOL': DefensivePosition(
                'SGOL', self.NAV * 0.02, self.NAV * 0.02 * 1.10, 0.022
            ),
            'DBMF': DefensivePosition(
                'DBMF', self.NAV * 0.05, self.NAV * 0.05 * 1.10, 0.055
            )
        }
        actions = check_dshp_triggers(positions, self.NAV)
        self.assertEqual(len(actions), 0)

    def test_multiple_triggers(self):
        """If both SGOL and DBMF are over threshold, both should trigger."""
        positions = {
            'SGOL': DefensivePosition(
                'SGOL', self.NAV * 0.02, self.NAV * 0.02 * 1.30, 0.026
            ),
            'DBMF': DefensivePosition(
                'DBMF', self.NAV * 0.05, self.NAV * 0.05 * 1.20, 0.060
            )
        }
        actions = check_dshp_triggers(positions, self.NAV)
        self.assertEqual(len(actions), 2)
        instruments = {a.instrument for a in actions}
        self.assertIn('SGOL', instruments)
        self.assertIn('DBMF', instruments)

if __name__ == '__main__':
    unittest.main()
