"""
ARMS Configuration: Position Dependency Map

This file contains the Customer Dependency Map (CDM) for all positions in the
current Architecture AB portfolio. It is the data layer for the CDM engine,
encoding the named entities whose health is material to each position's thesis.

This map is manually curated and updated by the PM when a position enters or
exits the book via SENTINEL.

Reference: ARMS Module Specification — CDM + TDC | Addendum 2 to FSD v1.1
"""

POSITION_DEPENDENCIES = {
    # Ticker: {dependencies}
    'TSLA': {
        'primary_demand_drivers': [],
        'thesis_enablers': ['US EV tax credit policy', 'SpaceX', 'xAI'],
        'regulatory_counterparties': ['NHTSA', 'SEC', 'DOE'],
        'alert_sensitivity': 'HIGH'
    },
    'NVDA': {
        'primary_demand_drivers': ['Microsoft', 'Google', 'Alphabet', 'Amazon', 'AWS', 'Meta', 'Oracle'],
        'thesis_enablers': ['US-China export control policy', 'TSMC'],
        'regulatory_counterparties': ['BIS', 'EU antitrust', 'Senate Commerce Committee'],
        'alert_sensitivity': 'CRITICAL'
    },
    'AMD': {
        'primary_demand_drivers': ['Microsoft', 'Google', 'Alphabet', 'Amazon', 'AWS', 'Meta'],
        'thesis_enablers': ['TSMC', 'x86 ecosystem'],
        'regulatory_counterparties': ['BIS', 'EU antitrust'],
        'alert_sensitivity': 'HIGH'
    },
    'PLTR': {
        'primary_demand_drivers': ['US DOD', 'US DHS', 'US intelligence community', 'NATO'],
        'thesis_enablers': ['US defense budget', 'AI executive orders'],
        'regulatory_counterparties': ['DOD', 'ITAR', 'FedRAMP'],
        'alert_sensitivity': 'HIGH'
    },
    'ALAB': {
        'primary_demand_drivers': ['Microsoft', 'Google', 'Alphabet', 'Amazon', 'AWS', 'Meta'],
        'thesis_enablers': ['AI data center build cycle', 'PCIe Gen 6'],
        'regulatory_counterparties': ['SEC'],
        'alert_sensitivity': 'CRITICAL'
    },
    'MU': {
        'primary_demand_drivers': ['Microsoft', 'Google', 'Alphabet', 'Amazon', 'AWS', 'Meta'],
        'thesis_enablers': ['Samsung', 'SK Hynix', 'HBM demand cycle', 'AI training infrastructure'],
        'regulatory_counterparties': ['DOC', 'BIS', 'CHIPS Act'],
        'alert_sensitivity': 'CRITICAL'
    },
    'ANET': {
        'primary_demand_drivers': ['Microsoft', 'Meta', 'Google', 'Alphabet', 'Amazon', 'AWS'],
        'thesis_enablers': ['AI data center networking'],
        'regulatory_counterparties': ['EU antitrust', 'FCC'],
        'alert_sensitivity': 'HIGH'
    },
    'AVGO': {
        'primary_demand_drivers': ['Google', 'Alphabet', 'Apple', 'Meta', 'OpenAI'],
        'thesis_enablers': ['TSMC', 'custom silicon adoption'],
        'regulatory_counterparties': ['EU antitrust', 'BIS'],
        'alert_sensitivity': 'HIGH'
    },
    'MRVL': {
        'primary_demand_drivers': ['Microsoft', 'Google', 'Alphabet', 'Amazon', 'AWS'],
        'thesis_enablers': ['5G infrastructure', 'cloud networking'],
        'regulatory_counterparties': ['BIS', 'EU antitrust'],
        'alert_sensitivity': 'HIGH'
    },
    'ARM': {
        'primary_demand_drivers': ['Apple', 'NVDA', 'Qualcomm', 'Samsung', 'Google'],
        'thesis_enablers': ['RISC-V', 'SoftBank'],
        'regulatory_counterparties': ['UK CMA', 'EU antitrust'],
        'alert_sensitivity': 'MEDIUM'
    },
    'ETN': {
        'primary_demand_drivers': ['Data center operators', 'utilities', 'hyperscalers'],
        'thesis_enablers': ['US data center build cycle', 'grid modernization'],
        'regulatory_counterparties': ['FERC', 'EPA'],
        'alert_sensitivity': 'MEDIUM'
    },
    'VRT': {
        'primary_demand_drivers': ['Hyperscalers', 'colocation data centers', 'telecom operators'],
        'thesis_enablers': ['AI data center build cycle', 'liquid cooling'],
        'regulatory_counterparties': [],
        'alert_sensitivity': 'MEDIUM'
    }
}

# Defines which event types trigger alerts for each sensitivity level.
ALERT_SENSITIVITY_MAPPING = {
    'CRITICAL': [
        'LEGAL_RULING', 'REGULATORY_FILING', 'EARNINGS_WARNING', 'MA_ANNOUNCEMENT', 'INSIDER_SALE'
    ],
    'HIGH': [
        'LEGAL_RULING', 'REGULATORY_FILING', 'EARNINGS_WARNING', 'MA_ANNOUNCEMENT', 'INSIDER_SALE',
        'LEADERSHIP_CHANGE', 'CREDIT_EVENT'
    ],
    'MEDIUM': [
        'PARTNERSHIP_CHANGE', 'PRODUCT_DELAY', 'LEADERSHIP_CHANGE'
    ]
}

# Configuration for the insider sale trigger.
INSIDER_SALE_CONFIG = {
    'severity': 'HIGH',
    'threshold_usd': 50000,
    'roles': ['CEO', 'CFO', 'President'],
    'exclude_10b5_1': True,
    'cdf_acceleration_days': 21,
    'tdc_review_hours': 6
}
