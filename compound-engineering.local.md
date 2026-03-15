---
review_agents:
  - compound-engineering:review:security-sentinel
  - compound-engineering:review:performance-oracle
  - compound-engineering:review:architecture-strategist
  - compound-engineering:review:code-simplicity-reviewer
  - compound-engineering:review:kieran-python-reviewer
---

# Poetry Self-Improving Loop — Review Context

A solo poetry evolution system. Python (Flask + threading) + vanilla JS. No auth, localhost only.
Key files: `loop.py` (core loop + HTTP server), `web/` (UI), `data/` (JSON storage).
No database — flat JSON files with atomic writes. No tests yet.
