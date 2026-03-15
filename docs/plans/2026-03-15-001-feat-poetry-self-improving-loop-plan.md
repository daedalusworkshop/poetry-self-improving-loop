---
title: Poetry Self-Improving Loop
type: feat
status: active
date: 2026-03-15
origin: docs/brainstorms/2026-03-15-poetry-self-improving-loop-brainstorm.md
---

# ✨ Poetry Self-Improving Loop

## Overview

A solo poetry evolution system. Claude generates 25-word poems from a living prompt. The human reads each poem blind, rates it on a frisson slider (0–1), and optionally highlights words or phrases that produced a physical reaction. Ratings and highlights feed back to Claude, which rewrites the prompt toward whatever landed. High-scoring poems (rating > 0.5) branch the lineage — multiple prompt variants evolve in parallel under natural selection mechanics.

**Stack:** Python (loop + HTTP server) + vanilla HTML/JS (rating UI) + local JSON storage.

---

## Problem Statement / Motivation

Language that earns its abstraction is rare. Most AI-generated poetry is generic. This system treats the prompt as a living organism — mutated by what actually moves a human body, not what sounds poetic in theory. Over time it builds a corpus of highlighted moments: the specific language that landed. The goal is a prompt that reliably produces poems worth highlighting.

---

## Proposed Solution

A Python script (`loop.py`) owns the full generate → serve → rate → mutate → branch cycle. It:
1. Reads `prompt.md` (the current active prompt, ≤50 words)
2. Calls Claude API → gets a 25-word poem
3. Saves the poem to `data/poems.json` (status: `pending`)
4. Serves it to the web UI via a tiny local HTTP server
5. Receives the rating + highlights back from the UI
6. Calls Claude to rewrite `prompt.md` toward what landed
7. If rating > 0.5 → spawns a new branch in `data/lineages.json`
8. Selects the next branch to generate from (weighted by fitness)
9. Repeats

---

## Technical Approach

### Architecture

```
poetry-loop/
  loop.py              ← main loop: generate → serve → rate → mutate → branch
  prompt.md            ← current active prompt (≤50 words, evolves)
  requirements.txt     ← anthropic, python-dotenv
  .env                 ← ANTHROPIC_API_KEY
  data/
    poems.json         ← all poems with ratings, highlights, branch ids
    lineages.json      ← prompt tree: all branches with fitness scores
  web/
    index.html         ← poem display + slider + highlighter
    app.js             ← UI logic, fetch + POST to loop.py server
    style.css          ← minimal, full-attention design
```

### Data Models

**poems.json** (append-only array)
```json
[
  {
    "id": "uuid4",
    "lineage_id": "uuid4",
    "text": "the 25-word poem text",
    "rating": 0.72,
    "highlights": ["catch in the throat", "November"],
    "created_at": "2026-03-15T10:00:00Z",
    "status": "pending | rated"
  }
]
```

**lineages.json** (the prompt tree)
```json
[
  {
    "id": "uuid4",
    "parent_id": "uuid4 | null",
    "prompt": "the prompt text ≤50 words",
    "fitness": 0.61,
    "poem_count": 8,
    "spawned_by_poem_id": "uuid4 | null",
    "spawned_by_rating": 0.72,
    "active": true,
    "created_at": "2026-03-15T10:00:00Z",
    "last_rated_at": "2026-03-15T11:00:00Z"
  }
]
```

### HTTP API (loop.py server, localhost:7331)

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/poem` | Returns current pending poem (id + text) |
| `POST` | `/rate` | Receives `{poem_id, rating, highlights[]}` |
| `GET` | `/lineages` | Returns all lineages (for toggle view) |
| `POST` | `/lineage/:id/deactivate` | Manually deactivate a branch |
| `POST` | `/lineage/:id/activate` | Revive an inactive branch |

### Mutation Prompt (what Claude receives)

```
Current prompt (≤50 words):
<contents of prompt.md>

Most recent poem:
<poem text>

Rating: <value 0–1, e.g. 0.72 — toward frisson>
Highlighted phrases: <["catch in the throat", "November"] or []>

Rewrite the prompt (≤50 words) to produce more of what caused those highlights.
If nothing was highlighted, evolve toward the rating signal alone.
Keep what's working. Do not explain — just write the new prompt.
```

### Branch Selection: Natural Selection Mechanics

**Fitness score** = rolling average of the last N ratings for a lineage (N=5, or all if fewer).

**Weighted random selection** — when choosing which branch to generate the next poem from:
```python
weights = [lineage.fitness for lineage in active_lineages]
selected = random.choices(active_lineages, weights=weights, k=1)[0]
```

**Auto-extinction** — after each rating, check all active lineages:
- If `poem_count >= 5` AND `fitness < 0.35` → mark `active = False`
- Inactive lineages stop receiving new poem generations
- User can manually revive via UI or `POST /lineage/:id/activate`

**Branching** — when a poem is rated > 0.5:
- Clone the current lineage's prompt into a new lineage (`parent_id` = current)
- Both the parent and child lineage continue evolving independently
- The parent prompt also mutates normally (every rating mutates the prompt it came from)

---

## Implementation Phases

### Phase 1: Core Loop (Python)

**Goal:** The loop runs, generates poems, saves data, mutates prompt.

- [ ] `loop.py` — skeleton: load prompt, call Claude, save poem, await rating
- [ ] Claude API integration (anthropic SDK, model: `claude-haiku-4-5-20251001` for speed/cost)
- [ ] `data/poems.json` read/write helpers
- [ ] `data/lineages.json` read/write helpers
- [ ] Seed lineage from `prompt.md` on first run
- [ ] Mutation: send poem + rating + highlights to Claude, overwrite `prompt.md`
- [ ] Branch spawning when rating > 0.5
- [ ] Branch selection (weighted random by fitness)
- [ ] Auto-extinction check after each rating
- [ ] `requirements.txt` + `.env` setup

**Files:**
- `loop.py`
- `data/poems.json` (auto-created)
- `data/lineages.json` (auto-created)
- `prompt.md` (initial prompt, hand-written)
- `requirements.txt`
- `.env.example`

### Phase 2: Local HTTP Server + Web UI

**Goal:** Human can rate poems in the browser. Loop and UI are fully connected.

- [ ] Embed a minimal HTTP server in `loop.py` (Python's `http.server` or `flask`)
- [ ] `GET /poem` — returns pending poem JSON
- [ ] `POST /rate` — receives rating + highlights, triggers mutation cycle
- [ ] `GET /lineages` — returns lineage tree
- [ ] `index.html` — full-screen poem display, one poem at a time, no context
- [ ] Slider UI — smooth 0–1 range, `nothing` → `frisson` → `masterpiece` labels, hold-to-drag
- [ ] Frisson highlighter — text selection on the poem, highlights stored in state
- [ ] Submit sends rating + highlights to `POST /rate`
- [ ] UI polls `GET /poem` until a new pending poem appears (after submission)
- [ ] Blind rating — no branch name, no lineage info shown in default view

**Files:**
- `web/index.html`
- `web/app.js`
- `web/style.css`

### Phase 3: Lineage View (Toggle)

**Goal:** Hidden by default. Toggle reveals the full branch tree.

- [ ] `GET /lineages` endpoint returns full tree
- [ ] Hidden toggle button in UI (subtle, corner)
- [ ] Lineage panel: list of active branches, each showing:
  - Current prompt text
  - Fitness score
  - Poem count
  - Deactivate button
- [ ] Inactive branches shown separately with Revive button
- [ ] `POST /lineage/:id/deactivate` and `POST /lineage/:id/activate` wired up

**Files:**
- Updates to `web/index.html`, `web/app.js`, `web/style.css`
- Updates to `loop.py` (new endpoints)

---

## System-Wide Impact

### Interaction Graph

`loop.py` owns the full cycle:
1. `generate_poem()` → calls Anthropic API → appends to `poems.json` (status: pending)
2. HTTP server receives `POST /rate` → updates poem in `poems.json` (status: rated)
3. `mutate_prompt()` → calls Anthropic API → overwrites `prompt.md` + updates lineage in `lineages.json`
4. `maybe_branch()` → if rating > 0.5, inserts new lineage into `lineages.json`
5. `check_extinction()` → may set `active=False` on weak lineages
6. `select_next_lineage()` → weighted random over active lineages
7. Loop restarts at step 1 with selected lineage's prompt

### Error & Failure Propagation

- Claude API failure in `generate_poem()` → retry once, then log and skip to next cycle
- Claude API failure in `mutate_prompt()` → log error, keep existing `prompt.md`, continue loop
- `poems.json` write failure → log and halt (data integrity critical)
- Web UI timeout waiting for poem → poll with exponential backoff, show "generating..." state
- Port 7331 already in use → fail fast with clear error message on startup

### State Lifecycle Risks

- If `loop.py` crashes mid-mutation, `prompt.md` may be partially written → use atomic write (write to temp file, rename)
- If loop crashes after poem saved but before rating received, poem stays `status: pending` forever → on startup, detect stale pending poems and re-serve them
- Duplicate branching: ensure branch is only created once per rated poem (check by `spawned_by_poem_id`)

### API Surface Parity

- All loop state (poems, lineages, prompt) is in local JSON files — can be inspected/edited directly
- HTTP endpoints are the only external interface
- No authentication needed (localhost only)

---

## Acceptance Criteria

### Functional
- [ ] Running `python loop.py` starts the full system (server + loop)
- [ ] Opening `http://localhost:7331` shows a poem, nothing else
- [ ] Slider runs smoothly from nothing (left) to masterpiece (right), no numbers
- [ ] Text selection on poem words/phrases works; highlights are captured on submit
- [ ] Submitting a rating immediately queues the next poem generation
- [ ] After rating > 0.5, a new branch appears in `data/lineages.json`
- [ ] Branches with fitness < 0.35 after 5+ poems are automatically deactivated
- [ ] Toggling the lineage view shows all branches with their prompts and fitness
- [ ] Manually deactivating/activating a branch works from the UI
- [ ] `prompt.md` is updated after every rating

### Non-Functional
- [ ] No poem metadata (branch name, lineage id) visible during blind rating
- [ ] Poems are exactly 25 words (validate and retry if Claude exceeds/undershoots)
- [ ] Prompt stays ≤50 words after each mutation (validate and truncate if needed)
- [ ] Local JSON files are human-readable and hand-editable
- [ ] Startup creates `data/` directory and seed lineage if none exists

---

## Success Metrics

- After 20+ poems rated, the highlighted phrase corpus contains language that is meaningfully different from the seed prompt
- At least one branch lineage has diverged noticeably in style/content from another
- The experience feels meditative — one poem, full attention, no friction

---

## Dependencies & Risks

| Dependency | Risk | Mitigation |
|---|---|---|
| Anthropic API key | Blocks everything | `.env.example` with clear setup instructions |
| Claude API rate limits | Slows loop if generating fast | `claude-haiku-4-5-20251001` is fast and cheap; add delay between calls if needed |
| Port 7331 conflict | Can't start server | Make port configurable via env var |
| JSON file corruption | Data loss | Atomic writes (write-then-rename pattern) |

---

## Future Considerations

- **Cloud migration:** Swap `data/*.json` for Supabase when multi-device access needed
- **Mobile native:** Haptic feedback on frisson highlight requires native iOS/Android
- **Multi-rater:** Aggregate ratings from multiple people; prompts evolve toward shared taste
- **Export:** Generate a personal poetics document from the highlighted corpus
- **Downstream use:** Plug winning prompt into a poetry camera, postcard generator, confession booth

---

## Sources & References

### Origin
- **Brainstorm document:** [docs/brainstorms/2026-03-15-poetry-self-improving-loop-brainstorm.md](../brainstorms/2026-03-15-poetry-self-improving-loop-brainstorm.md)
  Key decisions carried forward:
  - Architecture: CLI + local HTTP server + minimal web UI (not a full framework)
  - Mutation: Claude rewrites the prompt (not rule-based)
  - Selection: weighted fitness + auto-extinction (full natural selection mechanics)
  - Branching: uncapped, competitive, rating > 0.5 threshold
  - Lineage view: hidden by default, user toggle

### Internal References
- `Plan.md` — original vision document
- `prompt.md` — the seed prompt (to be written by hand before first run)
