---
status: pending
priority: p1
issue_id: "001"
tags: [code-review, architecture, threading, correctness]
---

# Replace `rating_event` + `pending_rating` with `queue.Queue`

## Problem Statement

`pending_rating` is a plain dict guarded by `state_lock`, signaled via `threading.Event`. This is fundamentally broken: `Event` is a binary flag, not a mailbox. If the user double-submits a rating, the second write silently overwrites the first in `pending_rating`. The loop thread wakes once and consumes whichever rating is currently in the dict — the other is permanently lost. The poem it applied to stays `status: "pending"` forever, causing the loop to re-serve it on next restart.

There is also a TOCTOU window between `rating_event.clear()` and `rating_event.wait()` where a new rating can arrive, be consumed immediately, and corrupt the next poem's rating.

## Findings

- `loop.py:44-46` — `pending_rating: dict = {}`, `rating_event = threading.Event()`
- `loop.py:363-371` — Flask handler writes to dict and sets event
- `loop.py:449-458` — Loop thread clears event, waits, reads dict

## Proposed Solution

Replace with `queue.Queue(maxsize=0)` (unbounded). Flask handler does `rating_queue.put(rating_obj)`. Loop thread does `rating_queue.get(block=True)`. Eliminates the dict, the event, and the lock for this path. Standard Python producer-consumer pattern.

## Acceptance Criteria

- [ ] Double-submission from UI doesn't corrupt fitness scores
- [ ] No rating is ever silently dropped
- [ ] `pending_rating` dict and `rating_event` are removed
