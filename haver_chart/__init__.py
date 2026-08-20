"""haver-chart — the chat lane MCP (plan.md §13).

A translation layer over this repo's chart pipeline, exposing two tools to Claude
Desktop. Imports are restricted by §13.5 to `build_chart`, `render`, `transforms`,
`resolve`, `haver_search` and `validate`; the knowledge stores are read-only (§13.6).
"""
