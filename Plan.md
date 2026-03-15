## What this is

A system that learns to write better poems, guided entirely by human feeling. A person reads a poem, rates it, highlights what moved them, and the prompt evolves toward more of that.

---

## The two moving parts

**prompt.md** — the poetry prompt the system is currently working with. 50 words max. This is what gets iterated. It evolves based entirely on what humans respond to.

**poem.md** — the output. A single poem, 25 words. Nothing else.

---

## The loop

1. System generates a poem from the current prompt
2. Human reads it blind (no context, no branch info)
3. Human rates it on a smooth (+ haptic feedback) slider: **nothing → frisson → masterpiece**
4. Human can highlight any word or phrase that gave them frisson
5. Ratings and highlights feed back into the prompt — the prompt evolves toward whatever produced those moments
6. Each iteration branches — multiple prompt lineages evolve in parallel

---

## The human interface

One poem on screen at a time. Full attention.

**The slider** — below the poem. Hold to drag, release to send. Runs from **nothing** (left) through **frisson** (middle) to **master-fucking-piece** (right). No numbers. No stars.

**The frisson highlighter** — select any word or phrase in the poem that gave you a physical reaction. A chill, a catch in the throat, a moment of recognition. You can highlight nothing, or you can highlight everything. It's yours.

Grading is blind — no branch info, no metadata, just the poem.

---

## What accumulates

Over time you build a corpus of highlighted moments — the specific language that actually landed. Not scores, not labels. Just the phrases that made someone feel something.

The prompt iterates toward more of that.

---

## The goal

A prompt that reliably produces poems worth highlighting. Something you could plug into a poetry camera, a postcard generator, a confession booth — anything that needs language that earns its abstraction.

The human is the only judge that matters.