"""
Unit tests for the Thesis Integrity Layer (CDM & TDC modules).
"""

import unittest
from unittest.mock import patch
import sys
import os
from datetime import datetime

# Add the engine directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from cdm import NewsItem, CdmAlert, run_cdm_scan
from tdc import ThesisReviewResult, run_thesis_review
from config import position_dependency_map as pdm

class TestCdmEngine(unittest.TestCase):
    """Test suite for the Customer Dependency Map (CDM) engine."""

    def test_google_antitrust_scenario(self):
        """Verify the Google-MU scenario from the specification."""
        news_item = NewsItem(
            source='Test', headline='Google Antitrust', content='...',
            timestamp='', entities=['Google'], event_type='LEGAL_RULING'
        )
        alerts = run_cdm_scan([news_item])
        
        affected_tickers = {alert.ticker for alert in alerts}
        
        # Check for key affected tickers
        self.assertIn('MU', affected_tickers)
        self.assertIn('NVDA', affected_tickers)
        
        # Check that unrelated tickers are not affected
        self.assertNotIn('TSLA', affected_tickers)
        self.assertNotIn('PLTR', affected_tickers)
        
        # Check the details of the MU alert
        mu_alert = next((a for a in alerts if a.ticker == 'MU'), None)
        self.assertIsNotNone(mu_alert)
        self.assertEqual(mu_alert.severity, 'CRITICAL')
        self.assertEqual(mu_alert.event_type, 'LEGAL_RULING')

    def test_insider_sale_scenario(self):
        """Verify the insider sale scenario for ALAB."""
        # Temporarily modify the map for the test, as done in the original script
        pdm.POSITION_DEPENDENCIES['ALAB']['regulatory_counterparties'].append('ALAB')
        
        news_item = NewsItem(
            source='Test', headline='ALAB CEO Sale', content='...',
            timestamp='', entities=['ALAB'], event_type='INSIDER_SALE'
        )
        alerts = run_cdm_scan([news_item])
        
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert.ticker, 'ALAB')
        self.assertEqual(alert.event_type, 'INSIDER_SALE')
        self.assertEqual(alert.severity, 'CRITICAL') # Based on ALAB's sensitivity
        
        # Clean up the modification
        pdm.POSITION_DEPENDENCIES['ALAB']['regulatory_counterparties'].pop()
        
    def test_positive_event_is_ignored(self):
        """Verify that POSITIVE_DEVELOPMENT events do not generate alerts."""
        news_item = NewsItem(
            source='Test', headline='Google Capex Increase', content='...',
            timestamp='', entities=['Google'], event_type='POSITIVE_DEVELOPMENT'
        )
        alerts = run_cdm_scan([news_item])
        self.assertEqual(len(alerts), 0)


class TestTdcEngine(unittest.TestCase):
    """Test suite for the Thesis Dependency Checker (TDC) engine."""

    def setUp(self):
        """Create a standard CDM alert to be used in tests."""
        self.test_news_item = NewsItem(
            source='Test', headline='Google Antitrust', content='...',
            timestamp=datetime.utcnow().isoformat(), entities=['Google'], event_type='LEGAL_RULING'
        )
        self.test_cdm_alert = CdmAlert(
            ticker='MU',
            triggering_entity='Google',
            event_type='LEGAL_RULING',
            severity='CRITICAL',
            headline='Google Antitrust',
            source_item=self.test_news_item
        )

    @patch('tdc.llm_wrapper.call')
    def test_watch_scenario(self, mock_claude):
        """Verify that a 'WATCH' response from the AI is processed correctly."""
        mock_claude.return_value = """
        {
            "tis_score": 6.2, "tis_label": "WATCH", "gates_affected": [2],
            "bear_case_evidence": "Test bear case.", "bull_case_rebuttal": "Test bull case.",
            "recommended_action": "WATCH_FLAG"
        }
        """
        result = run_thesis_review(self.test_cdm_alert)
        
        self.assertEqual(result.position, 'MU')
        self.assertEqual(result.tis_label, 'WATCH')
        self.assertEqual(result.tis_score, 6.2)
        self.assertEqual(result.recommended_action, 'WATCH_FLAG')
        self.assertIn(2, result.gates_affected)

    @patch('tdc.llm_wrapper.call')
    def test_impaired_scenario(self, mock_claude):
        """Verify that an 'IMPAIRED' response flags a PM review."""
        mock_claude.return_value = """
        {
            "tis_score": 5.5, "tis_label": "IMPAIRED", "gates_affected": [2, 6],
            "bear_case_evidence": "Major issue.", "bull_case_rebuttal": "But maybe not.",
            "recommended_action": "PM_REVIEW"
        }
        """
        # Patch the print function to capture the output
        with patch('tdc.print') as mock_print:
            result = run_thesis_review(self.test_cdm_alert)
            
            self.assertEqual(result.tis_label, 'IMPAIRED')
            self.assertEqual(result.recommended_action, 'PM_REVIEW')
            
            # Check that the Tier 1 PM Review queueing message was printed
            self.assertTrue(any("Queuing Tier 1 PM Review" in call.args[0] for call in mock_print.call_args_list))

    @patch('tdc.llm_wrapper.call')
    def test_intact_scenario(self, mock_claude):
        """Verify a clean 'INTACT' response."""
        mock_claude.return_value = """
        {
            "tis_score": 9.0, "tis_label": "INTACT", "gates_affected": [],
            "bear_case_evidence": null, "bull_case_rebuttal": "All good.",
            "recommended_action": "MONITOR"
        }
        """
        result = run_thesis_review(self.test_cdm_alert)
        self.assertEqual(result.tis_label, 'INTACT')
        self.assertEqual(result.recommended_action, 'MONITOR')

if __name__ == '__main__':
    unittest.main()
