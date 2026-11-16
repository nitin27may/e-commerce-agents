# Provenance of the chapter 08 replay fixtures

`replay/d5e847ca2194a104.json` (the second turn: the model answering after the MCP tool result) was **not
recorded in one piece against a live model**. `replay/1a94f2ffbfb8122a.json` (the first turn, the model
asking for the tool) was recorded live and is unchanged.

- The response half of `d5e847ca2194a104.json` (the model's final answer) was recorded live, before
  `agent-framework-core` 1.21.
- Core 1.21 changed how MAF's MCP client shapes a tool result: it now returns only the
  `structuredContent` text and carries the raw server payload in `additional_properties`
  (`_mcp_tool_result_host_payload`). That changes the request the code sends on the second turn, so the
  stored request was **rewritten by hand** to the new shape and the file renamed to its new key
  (commit `706eb62`). The recorded response was left as it was. Commit `806dbc2` later re-keyed it again
  for core 1.22's function-call serialisation, which only changes the file name and the key.
- The consequence: the model that produced the stored answer saw the older tool-result text, not the
  `{"result": ...}` form the request now contains. The fixture proves the plumbing (MCP subprocess, tool
  call, replay key) and is not evidence of how a model answers the new shape.

**The next live re-record should replace this file.** Run the chapter's recording recipe with a key
(see `evals/fixtures/README.md` for the procedure) and delete this note along with the old fixture.
Nothing in this directory is read by the replay client except the `replay/*.json` files, so this note
does not affect any fixture's hash or key.
