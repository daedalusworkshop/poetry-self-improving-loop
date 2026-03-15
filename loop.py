#!/usr/bin/env python3
"""
Poetry Self-Improving Loop
Generate → rate → mutate → branch → repeat
"""

import json
import os
import random
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

PORT              = int(os.getenv("PORT", 7331))
MODEL             = "claude-haiku-4-5-20251001"
BRANCH_THRESHOLD  = 0.5   # rating above this spawns a new species
EXTINCTION_FLOOR  = 0.35  # fitness below this triggers auto-extinction
EXTINCTION_MIN    = 5     # minimum poems before extinction can trigger
FITNESS_WINDOW    = 5     # rolling average over last N rated poems

BASE_DIR      = Path(__file__).parent
DATA_DIR      = BASE_DIR / "data"
PROMPT_FILE   = BASE_DIR / "prompt.md"
POEMS_FILE    = DATA_DIR / "poems.json"
SPECIES_FILE  = DATA_DIR / "species.json"
WEB_DIR       = BASE_DIR / "web"

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
app    = Flask(__name__)

# Shared state between HTTP server and loop thread
rating_event  = threading.Event()
pending_rating: dict = {}
state_lock    = threading.Lock()


# ── Atomic I/O ────────────────────────────────────────────────────────────────

def atomic_write(path: Path, data: list | dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.rename(path)

def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())

def load_poems()   -> list: return load_json(POEMS_FILE, [])
def save_poems(p)  -> None: atomic_write(POEMS_FILE, p)
def load_species() -> list:
    # migrate old lineages.json if species.json doesn't exist yet
    old = DATA_DIR / "lineages.json"
    if not SPECIES_FILE.exists() and old.exists():
        old.rename(SPECIES_FILE)
    return load_json(SPECIES_FILE, [])
def save_species(s) -> None: atomic_write(SPECIES_FILE, s)

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Species helpers ───────────────────────────────────────────────────────────

def compute_fitness(species_id: str, poems: list) -> float:
    rated = [p for p in poems
             if p["lineage_id"] == species_id and p["status"] == "rated"]
    if not rated:
        return 0.5  # neutral default for new species
    recent = rated[-FITNESS_WINDOW:]
    return sum(p["rating"] for p in recent) / len(recent)

def select_next_species(all_species: list, poems: list) -> dict | None:
    active = [s for s in all_species if s["active"]]
    if not active:
        return None
    for s in active:
        s["fitness"] = compute_fitness(s["id"], poems)
    weights = [max(s["fitness"], 0.01) for s in active]
    return random.choices(active, weights=weights, k=1)[0]

def check_extinction(all_species: list, poems: list) -> list:
    for s in all_species:
        if not s["active"]:
            continue
        s["fitness"] = compute_fitness(s["id"], poems)
        if s["poem_count"] >= EXTINCTION_MIN and s["fitness"] < EXTINCTION_FLOOR:
            s["active"] = False
            print(f"💀 Species {s['id'][:8]} extinct (fitness={s['fitness']:.2f})")
    return all_species

def maybe_branch(all_species: list, sp: dict, poem_id: str, rating: float) -> list:
    if rating <= BRANCH_THRESHOLD:
        return all_species
    already = any(s.get("spawned_by_poem_id") == poem_id for s in all_species)
    if already:
        return all_species
    branch = {
        "id":                 str(uuid.uuid4()),
        "parent_id":          sp["id"],
        "prompt":             sp["prompt"],
        "fitness":            rating,
        "poem_count":         0,
        "spawned_by_poem_id": poem_id,
        "spawned_by_rating":  rating,
        "active":             True,
        "created_at":         now_iso(),
        "last_rated_at":      None,
    }
    all_species.append(branch)
    print(f"🌿 New species {branch['id'][:8]} spawned (rating={rating:.2f})")
    return all_species

def seed_if_empty() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    if load_species():
        return
    prompt = PROMPT_FILE.read_text().strip()
    sp = {
        "id":                 str(uuid.uuid4()),
        "parent_id":          None,
        "prompt":             prompt,
        "fitness":            0.5,
        "poem_count":         0,
        "spawned_by_poem_id": None,
        "spawned_by_rating":  None,
        "active":             True,
        "created_at":         now_iso(),
        "last_rated_at":      None,
    }
    save_species([sp])
    print(f"✦ First species seeded from prompt.md")


# ── Claude calls ──────────────────────────────────────────────────────────────

def spontaneous_generation(poems: list) -> dict:
    """All species extinct. Birth a new one from the corpus of what landed."""
    all_highlights = []
    for p in poems:
        all_highlights.extend(p.get("highlights", []))
    seen = set()
    unique_hl = [h for h in all_highlights if not (h in seen or seen.add(h))]

    if unique_hl:
        hl_str = ", ".join(f'"{h}"' for h in unique_hl[:20])
        content = (
            f"These are phrases that caused physical reactions in a reader: {hl_str}.\n\n"
            "Write a new poetry prompt (≤50 words) that might produce more language like this. "
            "Go somewhere the previous prompts didn't. Be specific, physical, strange. "
            "Do not explain — just write the prompt."
        )
    else:
        content = (
            "All previous poetry species have gone extinct — nothing landed. "
            "Write a completely new poetry prompt (≤50 words). "
            "Try a radically different approach: different sensory domain, different register, "
            "different relationship to language itself. Do not explain — just write the prompt."
        )

    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=150,
            messages=[{"role": "user", "content": content}],
        )
        new_prompt = msg.content[0].text.strip()
        words = new_prompt.split()
        if len(words) > 50:
            new_prompt = " ".join(words[:50])
    except Exception as e:
        print(f"✗ API error (spontaneous_generation): {e}")
        new_prompt = PROMPT_FILE.read_text().strip()

    print(f"✦ New species prompt: \"{new_prompt[:60]}...\"")

    tmp = PROMPT_FILE.with_suffix(".tmp")
    tmp.write_text(new_prompt)
    tmp.rename(PROMPT_FILE)

    return {
        "id":                 str(uuid.uuid4()),
        "parent_id":          None,
        "prompt":             new_prompt,
        "fitness":            0.5,
        "poem_count":         0,
        "spawned_by_poem_id": None,
        "spawned_by_rating":  None,
        "active":             True,
        "created_at":         now_iso(),
        "last_rated_at":      None,
    }


def generate_poem(sp: dict) -> dict | None:
    def call():
        msg = client.messages.create(
            model=MODEL,
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": (
                    "Write a poem of exactly 25 words. No title. No explanation. "
                    "Just the poem.\n\n"
                    f"Prompt: {sp['prompt']}"
                ),
            }],
        )
        return msg.content[0].text.strip()

    text = ""
    for attempt in range(2):
        try:
            text = call()
            if len(text.split()) == 25:
                break
            if attempt == 0:
                print(f"⚠ Word count {len(text.split())}, retrying...")
        except Exception as e:
            print(f"✗ API error (generate): {e}")
            if attempt == 1:
                return None

    return {
        "id":         str(uuid.uuid4()),
        "lineage_id": sp["id"],
        "text":       text,
        "rating":     None,
        "highlights": [],
        "created_at": now_iso(),
        "status":     "pending",
    }

def mutate_prompt(sp: dict, poem: dict, rating: float, highlights: list) -> str:
    hl = json.dumps(highlights) if highlights else "[]"
    content = (
        f"Current prompt (≤50 words):\n{sp['prompt']}\n\n"
        f"Most recent poem:\n{poem['text']}\n\n"
        f"Rating: {rating:.2f} (0=nothing, 0.5=frisson, 1=masterpiece)\n"
        f"Highlighted phrases: {hl}\n\n"
        "Rewrite the prompt (≤50 words) to produce more of what caused those highlights. "
        "If nothing was highlighted, evolve toward the rating signal alone. "
        "Keep what's working. Do not explain — just write the new prompt."
    )
    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=150,
            messages=[{"role": "user", "content": content}],
        )
        new_prompt = msg.content[0].text.strip()
        words = new_prompt.split()
        if len(words) > 50:
            new_prompt = " ".join(words[:50])
        return new_prompt
    except Exception as e:
        print(f"✗ API error (mutate): {e}")
        return sp["prompt"]


# ── HTTP endpoints ────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")

@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(WEB_DIR, filename)

@app.route("/poem")
def get_poem():
    poems = load_poems()
    pending = next((p for p in reversed(poems) if p["status"] == "pending"), None)
    if pending:
        return jsonify({"id": pending["id"], "text": pending["text"]})
    return jsonify({"id": None, "text": None})

@app.route("/rate", methods=["POST"])
def post_rate():
    data = request.get_json()
    with state_lock:
        pending_rating["poem_id"]    = data.get("poem_id")
        pending_rating["rating"]     = float(data.get("rating", 0))
        pending_rating["highlights"] = data.get("highlights", [])
        rating_event.set()
    return jsonify({"ok": True})

@app.route("/species")
def get_species():
    return jsonify(load_species())

@app.route("/species/<species_id>/deactivate", methods=["POST"])
def deactivate_species(species_id):
    all_species = load_species()
    for s in all_species:
        if s["id"] == species_id:
            s["active"] = False
            break
    save_species(all_species)
    return jsonify({"ok": True})

@app.route("/species/<species_id>/activate", methods=["POST"])
def activate_species(species_id):
    all_species = load_species()
    for s in all_species:
        if s["id"] == species_id:
            s["active"] = True
            break
    save_species(all_species)
    return jsonify({"ok": True})


# ── Main loop (background thread) ─────────────────────────────────────────────

def run_loop():
    seed_if_empty()
    print(f"✦ Loop started. Open http://localhost:{PORT}")

    while True:
        poems       = load_poems()
        all_species = load_species()

        # Resume stale pending poem if it exists
        pending_poem = next((p for p in reversed(poems) if p["status"] == "pending"), None)

        if pending_poem:
            poem = pending_poem
            sp   = next((s for s in all_species if s["id"] == poem["lineage_id"]), None)
            if sp is None:
                print(f"⚠ Orphaned pending poem (species gone), skipping")
                for p in poems:
                    if p["id"] == poem["id"]:
                        p["status"] = "rated"
                        p["rating"] = 0
                save_poems(poems)
                continue
        else:
            sp = select_next_species(all_species, poems)
            if sp is None:
                print("💀 All species extinct. Spontaneous generation...")
                sp          = spontaneous_generation(poems)
                all_species = load_species()
                all_species.append(sp)
                save_species(all_species)

            print(f"⟳ Generating from species {sp['id'][:8]}...")
            poem = generate_poem(sp)
            if poem is None:
                time.sleep(5)
                continue

            poems.append(poem)
            save_poems(poems)
            print(f"✦ Poem ready")

        # Wait for a rating from the UI
        rating_event.clear()
        print(f"⏳ Waiting for rating...")
        rating_event.wait()

        with state_lock:
            r = dict(pending_rating)

        if r.get("poem_id") != poem["id"]:
            print(f"⚠ Rating for wrong poem id, ignoring")
            continue

        rating     = r["rating"]
        highlights = r["highlights"]
        print(f"★ {rating:.2f}  highlights={highlights}")

        # Mark poem as rated
        poems = load_poems()
        for p in poems:
            if p["id"] == poem["id"]:
                p["rating"]     = rating
                p["highlights"] = highlights
                p["status"]     = "rated"
                break
        save_poems(poems)

        # Mutate prompt
        print(f"⟳ Mutating prompt...")
        new_prompt = mutate_prompt(sp, poem, rating, highlights)
        print(f"✦ New prompt: \"{new_prompt[:60]}...\"")

        # Update species
        all_species = load_species()
        for s in all_species:
            if s["id"] == sp["id"]:
                s["prompt"]        = new_prompt
                s["poem_count"]    = s.get("poem_count", 0) + 1
                s["fitness"]       = compute_fitness(s["id"], poems)
                s["last_rated_at"] = now_iso()
                sp                 = s
                break

        # Write prompt.md to best active species' prompt
        active = [s for s in all_species if s["active"]]
        if active:
            best = max(active, key=lambda s: s["fitness"])
            tmp  = PROMPT_FILE.with_suffix(".tmp")
            tmp.write_text(best["prompt"])
            tmp.rename(PROMPT_FILE)

        # Branch + extinction
        all_species = maybe_branch(all_species, sp, poem["id"], rating)
        all_species = check_extinction(all_species, poems)

        # Persist species update
        for i, s in enumerate(all_species):
            if s["id"] == sp["id"]:
                all_species[i] = sp
                break
        save_species(all_species)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    t = threading.Thread(target=run_loop, daemon=True)
    t.start()
    print(f"✦ Server at http://localhost:{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
