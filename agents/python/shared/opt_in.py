"""Specialists the orchestrator may route to only when a flag is on.

This module has no imports, so the eval harness can read it without pulling in a model SDK.
"""

#: specialist name -> the ``Settings`` flag that makes it routable. Whatever the registry lists,
#: a flag that is off drops the specialist.
OPT_IN_SPECIALISTS: dict[str, str] = {"merchant-ops": "MERCHANT_OPS_AGENT_ENABLED"}
