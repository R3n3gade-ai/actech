"""
ARMS Configuration: Systematic Scan Universe

This file defines the universe of tickers that the Systematic Scan Engine
monitors every Monday morning at 6:00 AM CT.

The universe is focused on AI infrastructure: semiconductors, networking,
power/cooling, and data center REITs.

Reference: arms_fsd_master_build_v1.1.md, Section 11.3
"""

# Initial AI Infrastructure Universe for Phase 1
AI_INFRASTRUCTURE_UNIVERSE = [
    # Semiconductors (Fabless & Foundry)
    "NVDA", "AMD", "AVGO", "MRVL", "TSM", "ARM", "INTC", "MU", "SKHYNIX", 
    "ALAB", "KLAC", "LRCX", "ASML",
    
    # Networking & Connectivity
    "ANET", "CSCO", "JNPR", "PSTG", "NTAP",
    
    # Power & Cooling
    "ETN", "VRT", "ST", "HUBB", "WWD",
    
    # Data Center REITs & Hyperscalers
    "EQIX", "DLR", "AMZN", "MSFT", "GOOGL", "META", "ORCL",
    
    # Infrastructure Software & Services
    "PLTR", "SNOW", "DDOG", "CRWD", "PANW"
]

# Total tickers: 35+ companies
# Scan cost estimation: 35 tickers * $0.40/call = $14 per weekly sweep.
# Total monthly scan budget: ~$60.
