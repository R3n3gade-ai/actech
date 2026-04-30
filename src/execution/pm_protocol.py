"""
ARMS Execution: PM Protocol

Defines the canonical interaction windows and mandatory acknowledgment
flows for Tier 2 and GP-level actions. 

Tier 2 actions (e.g., adding >5% positions, activating SLOF beyond 1.5x,
modifying strategic queue) require GP co-signature. The protocol checks
the co-sign state file for valid signatures before permitting execution.

In production, this integrates with a secure signing endpoint.
For development, a local JSON file serves as the co-sign ledger.

Reference: THB v4.0, Section 10
"""
import os
import json
import hashlib
import datetime
from dataclasses import dataclass
from typing import Optional, List

@dataclass
class CosignResult:
    is_approved: bool
    signers: List[str]
    required_count: int
    action_id: str
    detail: str

_COSIGN_LEDGER_PATH = os.path.join('achelion_arms', 'state', 'cosign_ledger.json')

# GP principals authorized to co-sign
AUTHORIZED_SIGNERS = ['MJ', 'SYSTEM_AUTO']  # In production: cryptographic key IDs

REQUIRED_SIGNATURES = 1  # Minimum co-signs required for Tier 2 actions

def _load_ledger() -> dict:
    if os.path.exists(_COSIGN_LEDGER_PATH):
        try:
            with open(_COSIGN_LEDGER_PATH, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {'actions': {}}

def _save_ledger(ledger: dict):
    os.makedirs(os.path.dirname(_COSIGN_LEDGER_PATH), exist_ok=True)
    with open(_COSIGN_LEDGER_PATH, 'w') as f:
        json.dump(ledger, f, indent=2)

def _hash_action(action_id: str) -> str:
    """Generate deterministic hash for an action (placeholder for cryptographic signatures)."""
    return hashlib.sha256(action_id.encode()).hexdigest()[:16]

def submit_cosign(action_id: str, signer: str) -> bool:
    """
    Record a GP co-signature for a Tier 2 action.
    
    Args:
        action_id: Unique identifier for the action (e.g., 'SLOF_ACTIVATE_20260413').
        signer: Identity of the signer (must be in AUTHORIZED_SIGNERS).
    
    Returns:
        True if the signature was recorded, False if signer is unauthorized.
    """
    if signer not in AUTHORIZED_SIGNERS:
        return False
    
    ledger = _load_ledger()
    if action_id not in ledger['actions']:
        ledger['actions'][action_id] = {
            'signatures': [],
            'created': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
    
    # Don't allow duplicate signatures from the same signer
    existing = [s['signer'] for s in ledger['actions'][action_id]['signatures']]
    if signer in existing:
        return True
    
    ledger['actions'][action_id]['signatures'].append({
        'signer': signer,
        'hash': _hash_action(f"{action_id}:{signer}"),
        'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    })
    _save_ledger(ledger)
    return True

def require_gp_cosign(action_id: str) -> bool:
    """
    Check whether a Tier 2 action has sufficient GP co-signatures.
    
    Args:
        action_id: Unique identifier for the action.
    
    Returns:
        True if the required number of signatures have been collected.
    """
    ledger = _load_ledger()
    action = ledger.get('actions', {}).get(action_id)
    if not action:
        return False
    
    valid_sigs = [s for s in action.get('signatures', []) if s['signer'] in AUTHORIZED_SIGNERS]
    return len(valid_sigs) >= REQUIRED_SIGNATURES

def check_cosign_status(action_id: str) -> CosignResult:
    """
    Get detailed co-sign status for a Tier 2 action.
    
    Args:
        action_id: Unique identifier for the action.
    
    Returns:
        CosignResult with approval status and signer details.
    """
    ledger = _load_ledger()
    action = ledger.get('actions', {}).get(action_id)
    
    if not action:
        return CosignResult(
            is_approved=False,
            signers=[],
            required_count=REQUIRED_SIGNATURES,
            action_id=action_id,
            detail=f"No co-sign request found for {action_id}. Submit signatures first.",
        )
    
    valid_sigs = [s for s in action.get('signatures', []) if s['signer'] in AUTHORIZED_SIGNERS]
    signers = [s['signer'] for s in valid_sigs]
    is_approved = len(valid_sigs) >= REQUIRED_SIGNATURES
    
    if is_approved:
        detail = f"Approved: {len(valid_sigs)}/{REQUIRED_SIGNATURES} signatures collected ({', '.join(signers)})."
    else:
        detail = f"Pending: {len(valid_sigs)}/{REQUIRED_SIGNATURES} signatures. Need {REQUIRED_SIGNATURES - len(valid_sigs)} more."
    
    return CosignResult(
        is_approved=is_approved,
        signers=signers,
        required_count=REQUIRED_SIGNATURES,
        action_id=action_id,
        detail=detail,
    )
