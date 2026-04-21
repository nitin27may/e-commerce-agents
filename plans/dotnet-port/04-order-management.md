# .NET Port 04 — ECommerceAgents.OrderManagement

## Goal

Port `agents/order_management/*` to `dotnet/src/ECommerceAgents.OrderManagement/`.

## Python source → .NET target

| Python | .NET |
|--------|------|
| `order_management/agent.py` | `OrderManagementAgent.cs` |
| `order_management/tools.py` (list/get/cancel/return/status) | `OrderManagementTools.cs` |
| `order_management/main.py` | `Program.cs` on `:8082` |

## Test project

`ECommerceAgents.OrderManagement.Tests/` — minimum **10 tests**:

- List orders (user-scoped), get order, status transitions, cancel eligibility, return eligibility.
- Negative cases: canceling shipped orders fails, non-existent order id fails cleanly.
- Agent smoke: intent "where is my order" triggers `list_orders` tool.

## Verification

- Parity test against Python equivalent returns identical order lists for the same seeded user.

## Out of scope

- Return workflow orchestration (lives in workflows, handled by Phase 7 refactor).
