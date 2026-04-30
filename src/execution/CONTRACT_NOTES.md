# Execution Contract Notes

Canonical trade request object:
- `src/execution/order_request.py::OrderRequest`

## Current decision
ARMS now treats `OrderRequest` in `order_request.py` as the single execution contract.
`execution/interfaces.py` imports and re-exports that type for broker interface compatibility.

## Transitional schema choices
The contract currently remains intentionally pragmatic so existing modules can migrate without a full rewrite:
- `quantity` is `float` to support both share-like and notional-style requests during transition.
- `execution_window_min`, `slippage_budget_bps`, `priority`, and tier metadata have defaults so legacy callsites remain valid.
- `SELL_PUT` was added because PTRH correction/roll logic already uses it.

## Known remaining gap
The schema still needs a second-stage institutional upgrade to separate:
- equity shares
- option contracts
- notional hedge adjustments
- limit / routing metadata
- broker-side identifiers and correlation IDs

That upgrade should happen before live paper execution is considered production-like.
