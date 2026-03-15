---
status: pending
priority: p2
issue_id: "002"
tags: [code-review, architecture, reliability]
---

# Loop thread dies silently — UI polls forever with no signal

## Problem Statement

`run_loop()` has no top-level exception handler. Any unhandled exception kills the thread permanently. The Flask server keeps running, the UI keeps showing "generating..." and polling `/poem` forever. No error is surfaced to the user.

## Findings

- `loop.py:400` — `run_loop` is a bare `while True` with no outer try/except
- `loop.py:514` — `daemon=True` means the thread is silently reaped on exit
- `web/app.js:141-158` — `pollForPoem` has no timeout or error state

## Proposed Solution

1. Wrap `while True` body in `except Exception` — log traceback, sleep briefly, continue (auto-restart)
2. Add module-level `loop_alive = True` flag set to `False` on persistent failure
3. Add `GET /status` endpoint returning `{"loop_alive": bool, "last_error": str | null}`
4. In `pollForPoem`: after 60s without a new poem, fetch `/status` and surface error to user

## Acceptance Criteria

- [ ] Loop auto-restarts after transient exceptions (API timeout, etc.)
- [ ] `/status` endpoint reports loop health
- [ ] UI shows error state after 60s of no poems
