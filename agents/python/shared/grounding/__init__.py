"""Server-side grounding — verifies an agent's final response against real data.

``ledger.py`` records typed facts as tools run this turn; ``extractor.py`` pulls
claims (fenced product/order cards, bare UUIDs, dollar amounts, tracking numbers)
out of the agent's composed reply; ``verifier.py`` checks each claim against the
ledger first, then the database; ``middleware.py`` wires verification into the
agent-level middleware stack per ``GROUNDING_MODE``.
"""

from __future__ import annotations
