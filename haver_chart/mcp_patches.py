"""Stop one cancelled tool call from killing the whole MCP session.

WHAT HAPPENED (2026-09-25)
  A picker page took 81.4s to enrich on a slow DLX morning. Claude's host gives a tool call
  about 60s, so at 59.9s it sent `notifications/cancelled`. The SDK answered the request
  with a cancellation error and marked it complete. Our handler is synchronous DLX work in a
  worker thread, which a cancel scope cannot interrupt, so it carried on and finished 21s
  later -- and the SDK then tried to send its result:

      mcp/shared/session.py  RequestResponder.respond
          assert not self._completed, "Request already responded to"

  That AssertionError escapes into the session's task group, the task group tears the
  session down, and every later call on it -- the panel's next three pages, and the chat's
  own calls -- comes back 404 "session terminated". One slow page cost the operator the
  rest of the panel and the conversation's connection.

  This is upstream python-sdk#2416 (fix: PR #2949, "make request responder completion
  idempotent"). mcp 1.29.0, installed on both machines, still carries the assert.

WHAT THIS DOES
  Makes a late `respond()` after a cancellation a logged no-op, and a late `cancel()` after
  a normal response skip the second, contradictory reply. Exactly one response per request
  still goes out; the loser returns quietly instead of crashing the session.

  Wrapping, not rewriting: each wrapper checks `_completed` and otherwise calls the SDK's
  own method, so nothing of the SDK's logic is copied here to drift. The check and the
  original assert run with no `await` between them, so on one event loop nothing can
  interleave.

  SELF-DISARMING. If the installed SDK no longer contains the racy assert -- i.e. the
  upstream fix has shipped and been installed -- this patches nothing, so upgrading `mcp`
  is enough to retire it.
"""
from __future__ import annotations

import inspect
import sys

_APPLIED = False


def apply() -> str:
    """Install the guard. Returns a one-line status for the startup log."""
    global _APPLIED
    if _APPLIED:
        return "already applied"
    try:
        from mcp.shared.session import RequestResponder
    except Exception as exc:                      # pragma: no cover
        return f"skipped: cannot import RequestResponder ({type(exc).__name__})"

    try:
        src = inspect.getsource(RequestResponder.respond)
    except Exception:                             # pragma: no cover
        src = ""
    if "assert not self._completed" not in src:
        _APPLIED = True
        return "not needed: installed mcp no longer asserts on a late respond"

    orig_respond = RequestResponder.respond
    orig_cancel = RequestResponder.cancel

    async def respond(self, response):
        if getattr(self, "_completed", False):
            # The host already had its answer -- a cancellation. Sending ours as well is
            # what used to kill the session; dropping it loses nothing the host still
            # wants, and the log line is what makes "the page timed out" diagnosable.
            print(f"[mcp-guard] dropped a late response to request {self.request_id!r}: "
                  f"the host cancelled it first (usually its ~60s tool timeout)",
                  file=sys.stderr, flush=True)
            return
        return await orig_respond(self, response)

    async def cancel(self):
        if getattr(self, "_completed", False):
            # A normal response already went out. Still stop any work in flight, but do not
            # follow a result with a contradictory "Request cancelled" for the same id.
            scope = getattr(self, "_cancel_scope", None)
            if scope is not None:
                scope.cancel()
            return
        return await orig_cancel(self)

    respond.__wrapped__ = orig_respond
    cancel.__wrapped__ = orig_cancel
    RequestResponder.respond = respond
    RequestResponder.cancel = cancel
    _APPLIED = True
    return "applied: late respond/cancel no longer tear down the session (python-sdk#2416)"
