| Mode | Runs | OK | p50 latency | p95 latency | Mean tokens | Mean cost | Agents involved |
|---|---|---|---|---|---|---|---|
| `workflow:pre-purchase` | 8 | 8 | 255 ms | 278 ms | not captured | not captured | price_history, reviews, shipping, stock |
| `handoff` | 8 | 8 | 5067 ms | 7174 ms | not captured | not captured | order-management, product-discovery |

Model `gpt-4.1` · 2 repetition(s) × 4 prompts · measured 2026-08-27T00:18:31+00:00 · commit `b53d20b`
