# Poetry Self-Improving Loop — Brainstorm

**Date:** 2026-03-15
**Status:** Draft

---

## What We're Building

A solo poetry evolution system. Claude generates 25-word poems from a living prompt. One person reads each poem blind, rates it on a frisson slider (nothing → frisson → masterpiece), and optionally highlights words or phrases that produced a physical reaction. Ratings and highlights feed back to Claude, which rewrites the prompt toward whatever landed. High-scoring poems (frisson+) branch the lineage — multiple prompt variants evolve in parallel. Data starts local, migrates to cloud when needed. Web first, mobile later.

---

## Why This Approach

**Architecture: CLI loop + minimal web viewer**

A Python script (`loop.py`) owns the generate→rate→mutate cycle. A lightweight web UI serves the poem for rating. No backend server initially — the script is the backend, polling or triggered manually. Data lives in JSON files on disk.

*Why:* Gets the loop running and feeling real within a day. No infrastructure overhead. The poetry experience — one poem, full attention — doesn't need a complex stack.

---

## Key Decisions

| Decision | Choice | Reasoning |
|---|---|---|
| Platform | Web first, mobile later | Faster to ship; haptics can come with native mobile |
| Poem generator | Claude (Anthropic) | Already in ecosystem; quality output |
| Mutation strategy | Claude rewrites the prompt | Readable, flexible, can reason about what landed |
| Branching trigger | Frisson+ ratings only | Prunes low performers; focuses exploration on what works |
| Data storage | Local JSON → cloud later | Ship fast; add Supabase/Firebase when multi-device needed |
| Raters | Solo (one person) | Deeply personal taste shapes the prompt |
| Architecture | CLI + web viewer | Minimal, fast, iterate quickly |

---

## How the Loop Works

```
1. loop.py reads current prompt.md
2. Calls Claude API → gets a 25-word poem
3. Saves poem to data/poems.json (status: pending)
4. Web UI polls for pending poem → displays it full-screen
5. User rates via slider + optional highlight
6. Rating posted back → saved to poems.json
7. loop.py detects rating:
   - Any rating → Claude rewrites prompt.md using poem + highlights + rating
   - Frisson+ rating → also saves current prompt as a branch in lineages.json
8. loop.py generates next poem from updated prompt
9. Repeat
```

---

## Data Model (sketch)

**poems.json**
```json
{
  "id": "uuid",
  "prompt_id": "uuid",
  "text": "the poem text here",
  "rating": 0.72,
  "highlights": ["word", "phrase that landed"],
  "created_at": "iso8601",
  "status": "pending | rated"
}
```

**lineages.json**
```json
{
  "id": "uuid",
  "parent_id": "uuid | null",
  "prompt": "the prompt text (≤50 words)",
  "spawned_by_poem": "poem_uuid",
  "spawned_by_rating": 0.72,
  "created_at": "iso8601"
}
```

---

## What Claude Receives When Rewriting the Prompt

```
Current prompt: <prompt.md contents>

Most recent poem:
<poem text>

Rating: <slider value, e.g. 0.72 — toward frisson>
Highlighted phrases: ["catch in the throat", "November"]

Rewrite the prompt (≤50 words) to produce more of what caused those highlights.
If nothing was highlighted, evolve toward the rating signal alone.
Keep what's working. Don't explain — just write the new prompt.
```

---

## Resolved Questions

1. **Branch threshold:** 0.5 — anything past the slider midpoint triggers a branch. Generous exploration.

2. **Rating comms:** `loop.py` runs a tiny local HTTP server. The web UI POSTs ratings to it. Clean, no file polling.

3. **Branch count:** Uncapped. Branches compete with each other. Clear mechanism to mark a branch inactive (manual or auto). User can also revive inactive branches.

4. **Branch selection model:** Weighted probability + auto-extinction.
   - Each branch has a fitness score (rolling average of recent ratings)
   - Higher fitness = more likely to be shown next poem
   - Branches that stay weak long enough hit an extinction threshold → auto-marked inactive
   - User can manually revive any inactive branch

5. **Lineage view:** Hidden by default — you just experience the poems. The evolution happens invisibly. Toggle available to reveal the full tree when you want to inspect it.

---

## What Accumulates

- A corpus of highlighted phrases — the specific language that actually landed
- A lineage tree of prompts, each traceable to the ratings that spawned it
- A personal taste profile implicit in every rating and highlight

---

## Future Possibilities

- Export the corpus of highlights as a personal poetics — a document of what moves you
- Plug the winning prompt into a poetry camera, postcard generator, or confession booth
- Add a second rater → prompts evolve toward shared taste
- Mobile native for haptic slider feedback
