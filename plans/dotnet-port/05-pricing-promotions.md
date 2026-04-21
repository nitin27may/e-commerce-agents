# .NET Port 05 — ECommerceAgents.PricingPromotions

## Goal

Port `agents/pricing_promotions/*` to `dotnet/src/ECommerceAgents.PricingPromotions/`.

## Python source → .NET target

| Python | .NET |
|--------|------|
| `pricing_promotions/agent.py` | `PricingPromotionsAgent.cs` |
| `pricing_promotions/tools.py` (validate_coupon, calculate_discount, get_active_promotions, etc.) | `PricingPromotionsTools.cs` |
| `pricing_promotions/main.py` | `Program.cs` on `:8083` |

## Test project

`ECommerceAgents.PricingPromotions.Tests/` — minimum **8 tests**:

- `validate_coupon`: active, expired, already-used-by-user, minimum-spend unmet.
- `calculate_discount`: percentage, fixed amount, stacking rules.
- `get_active_promotions`: category filter, date range.
- Agent smoke: applies discount on a canned cart.

## Verification

- Parity: same cart + coupon combination produces identical final totals in Python and .NET.

## Out of scope

- Promotion authoring UI — admin-side feature, not in scope.
