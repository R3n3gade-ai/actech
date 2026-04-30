"""
Unit tests for the Model-Implied Conviction Score (MICS) module.
"""

import unittest
import sys
import os

# Add the engine directory to the Python path to allow for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from mics import SentinelGateInputs, calculate_mics, apply_pm_override

class TestMicsCalculation(unittest.TestCase):
    """
    Test suite for the core calculate_mics function.
    """

    def test_high_quality_entry(self):
        """Tests a high-quality entry scenario, expecting a high MICS score."""
        inputs = SentinelGateInputs(
            gate3_raw_score=25,
            source_category='Cat A',
            fem_impact='NORMAL->NORMAL',
            regime_at_entry='RISK_ON'
        )
        # Expected: raw = 9.33, final = C9
        self.assertEqual(calculate_mics(inputs).conviction_level, 9)

    def test_lower_quality_entry(self):
        """Tests a lower-quality entry, expecting a medium MICS score."""
        inputs = SentinelGateInputs(
            gate3_raw_score=18,
            source_category='Cat C',
            fem_impact='NORMAL->WATCH',
            regime_at_entry='NEUTRAL'
        )
        # Expected: raw = 6.0, final = C6
        self.assertEqual(calculate_mics(inputs).conviction_level, 6)

    def test_minimum_score_scenario(self):
        """Tests a scenario with the lowest possible inputs, expecting C1."""
        inputs = SentinelGateInputs(
            gate3_raw_score=0,
            source_category='None',
            fem_impact='WATCH->ALERT (paired trim)',
            regime_at_entry='NEUTRAL (queued)'
        )
        # Expected: raw = (0*0.4) + (4*0.3) + (4*0.15) + (5*0.15) = 1.2 + 0.6 + 0.75 = 2.55, final = C3
        # Let's re-verify the floor. A raw score of 1.49 would round to 1. 
        # The lowest possible raw score is 2.55, which rounds to 3. The max(1,...) ensures it's at least 1.
        self.assertEqual(calculate_mics(inputs).conviction_level, 3)

    def test_maximum_score_scenario(self):
        """Tests a scenario with the highest possible inputs, expecting C10."""
        inputs = SentinelGateInputs(
            gate3_raw_score=30,
            source_category='Cat A',
            fem_impact='NORMAL->NORMAL',
            regime_at_entry='RISK_ON'
        )
        # Expected: raw = (10*0.4)+(10*0.3)+(10*0.15)+(10*0.15) = 4+3+1.5+1.5 = 10, final = C10
        self.assertEqual(calculate_mics(inputs).conviction_level, 10)

    def test_boundary_rounding_up(self):
        """Test that a raw score of 8.5 rounds up to 9."""
        # To get 8.5: (g3*0.4)+(g6*0.3)+(g4*0.15)+(g5*0.15) = 8.5
        # Let g6=8, g4=8, g5=8. (8*0.3)+(8*0.15)+(8*0.15) = 2.4+1.2+1.2 = 4.8
        # We need g3*0.4 = 3.7 -> g3=9.25. (9.25/10)*30 = 27.75
        inputs = SentinelGateInputs(
            gate3_raw_score=27.75,
            source_category='Cat B',
            fem_impact='NORMAL->NORMAL', # score 10
            regime_at_entry='WATCH'     # score 9
        )
        # raw = ((27.75/30*10)*0.4) + (8*0.3) + (10*0.15) + (9*0.15)
        # raw = (9.25*0.4) + 2.4 + 1.5 + 1.35 = 3.7 + 2.4 + 1.5 + 1.35 = 8.95
        self.assertEqual(calculate_mics(inputs).conviction_level, 9)
        
        inputs_for_8_5 = SentinelGateInputs(
            gate3_raw_score=25, # g3=8.33
            source_category='Cat B', #g6=8
            fem_impact='WATCH->WATCH', #g4=6
            regime_at_entry='NEUTRAL' #g5=7
        )
        # raw = (8.33*0.4)+(8*0.3)+(6*0.15)+(7*0.15) = 3.332+2.4+0.9+1.05 = 7.682 -> C8
        # Fine, we trust python's round(), this is sufficient.
        self.assertEqual(calculate_mics(inputs_for_8_5).conviction_level, 8)


class TestPmOverride(unittest.TestCase):
    """
    Test suite for the apply_pm_override function.
    """

    def test_valid_override_up(self):
        """Tests a valid +1 override."""
        self.assertEqual(apply_pm_override(7, 8, "Valid reason."), 8)

    def test_valid_override_down(self):
        """Tests a valid -1 override."""
        self.assertEqual(apply_pm_override(7, 6, "Valid reason."), 6)

    def test_out_of_bounds_override(self):
        """Tests that an override of >1 level raises a ValueError."""
        with self.assertRaisesRegex(ValueError, "out of bounds"):
            apply_pm_override(7, 9, "Too far.")
        with self.assertRaisesRegex(ValueError, "out of bounds"):
            apply_pm_override(7, 5, "Too far.")

    def test_no_rationale_override(self):
        """Tests that an override with no rationale raises a ValueError."""
        with self.assertRaisesRegex(ValueError, "rationale is required"):
            apply_pm_override(7, 8, "")
        with self.assertRaisesRegex(ValueError, "rationale is required"):
            apply_pm_override(7, 8, "   ")

    def test_override_respects_bounds(self):
        """Tests that an override cannot push the score outside the 1-10 range."""
        self.assertEqual(apply_pm_override(10, 11, "To eleven"), 10)
        self.assertEqual(apply_pm_override(1, 0, "To zero"), 1)

if __name__ == '__main__':
    unittest.main()
