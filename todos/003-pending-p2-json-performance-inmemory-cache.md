---
status: pending
priority: p2
issue_id: "003"
tags: [code-review, performance, scalability]
---

# Full JSON file read on every operation — will slow linearly with corpus size

## Problem Statement

`load_poems()` deserializes the entire `poems.json` on every call. It is called 3-4 times per rating cycle. `poems.json` grows by one entry per poem forever with no cap. At 5,000 poems, each cycle deserializes ~1.5MB 3-4 times. Additionally, `compute_fitness` scans all poems O(n) per species per cycle.

## Findings

- `loop.py:61-69` — bare file reads on every call
- `loop.py:77-83` — `compute_fitness` linear scan, called once per species per selection + extinction + rating
- `loop.py:464` — `load_poems()` called again mid-loop after rating
- Performance agent: ~12 linear scans of 10k poems per rating cycle at scale

## Proposed Solution

1. Module-level in-memory `_poems: list[dict]` and `_species: list[dict]` caches
2. Protected by `state_lock` for Flask endpoint access
3. `load_poems()` returns from cache; only reads disk on cold start
4. Per-species `deque(maxlen=FITNESS_WINDOW)` for recent ratings — `compute_fitness` becomes O(1)
5. Periodic background archive of poems older than N to `poems_archive_YYYY-MM.json`

## Acceptance Criteria

- [ ] `GET /poem` is a pure in-memory lookup after startup
- [ ] Fitness computation is O(1) regardless of corpus size
- [ ] System remains usable after 10,000 poems
