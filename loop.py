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
BRANCH_THRESHOLD  = 0.5   # rating above this spawns a new lineage
EXTINCTION_FLOOR  = 0.35  # fitness below this triggers auto-extinction
EXTINCTION_MIN    = 5     # minimum poems before extinction can trigger
FITNESS_WINDOW    = 5     # rolling average over last N rated poems

BASE_DIR      = Path(__file__).parent
DATA_DIR      = BASE_DIR / "data"
PROMPT_FILE   = BASE_DIR / "prompt.md"
POEMS_FILE    = DATA_DIR / "poems.json"
LINEAGES_FILE = DATA_DIR / "lineages.json"
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

def load_poems()    -> list: return load_json(POEMS_FILE, [])
def save_poems(p)   -> None: atomic_write(POEMS_FILE, p)
def load_lineages() -> list: return load_json(LINEAGES_FILE, [])
def save_lineages(l)-> None: atomic_write(LINEAGES_FILE, l)

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Lineage helpers ───────────────────────────────────────────────────────────

def compute_fitness(lineage_id: str, poems: list) -> float:
    rated = [p for p in poems
             if p["lineage_id"] == lineage_id and p["status"] == "rated"]
    if not rated:
        return 0.5  # neutral default for new branches
    recent = rated[-FITNESS_WINDOW:]
    return sum(p["rating"] for p in recent) / len(recent)

def select_next_lineage(lineages: list, poems: list) -> dict | None:
    active = [l for l in lineages if l["active"]]
    if not active:
        return None
    for l in active:
        l["fitness"] = compute_fitness(l["id"], poems)
    weights = [max(l["fitness"], 0.01) for l in active]
    return random.choices(active, weights=weights, k=1)[0]

def check_extinction(lineages: list, poems: list) -> list:
    for l in lineages:
        if not l["active"]:
            continue
        l["fitness"] = compute_fitness(l["id"], poems)
        if l["poem_count"] >= EXTINCTION_MIN and l["fitness"] < EXTINCTION_FLOOR:
            l["active"] = False
            print(f"💀 Lineage {l['id'][:8]} extinct (fitness={l['fitness']:.2f})")
    return lineages

def maybe_branch(lineages: list, lineage: dict, poem_id: str, rating: float) -> list:
    if rating <= BRANCH_THRESHOLD:
        return lineages
    already = any(l.get("spawned_by_poem_id") == poem_id for l in lineages)
    if already:
        return lineages
    branch = {
        "id":                str(uuid.uuid4()),
        "parent_id":         lineage["id"],
        "prompt":            lineage["prompt"],
        "fitness":           rating,
        "poem_count":        0,
        "spawned_by_poem_id": poem_id,
        "spawned_by_rating": rating,
        "active":            True,
        "created_at":        now_iso(),
        "last_rated_at":     None,
    }
    lineages.append(branch)
    print(f"🌿 Branch {branch['id'][:8]} spawned (rating={rating:.2f})")
    return lineages

def seed_if_empty() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    if load_lineages():
        return
    prompt = PROMPT_FILE.read_text().strip()
    lineage = {
        "id":                str(uuid.uuid4()),
        "parent_id":         None,
        "prompt":            prompt,
        "fitness":           0.5,
        "poem_count":        0,
        "spawned_by_poem_id": None,
        "spawned_by_rating": None,
        "active":            True,
        "created_at":        now_iso(),
        "last_rated_at":     None,
    }
    save_lineages([lineage])
    print(f"✦ Seeded first lineage from prompt.md")


# ── Claude calls ──────────────────────────────────────────────────────────────

def generate_poem(lineage: dict) -> dict | None:
    def call():
        msg = client.messages.create(
            model=MODEL,
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": (
                    "Write a poem of exactly 25 words. No title. No explanation. "
                    "Just the poem.\n\n"
                    f"Prompt: {lineage['prompt']}"
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
        "lineage_id": lineage["id"],
        "text":       text,
        "rating":     None,
        "highlights": [],
        "created_at": now_iso(),
        "status":     "pending",
    }

def mutate_prompt(lineage: dict, poem: dict, rating: float, highlights: list) -> str:
    hl = json.dumps(highlights) if highlights else "[]"
    content = (
        f"Current prompt (≤50 words):\n{lineage['prompt']}\n\n"
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
        return lineage["prompt"]


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
        pending_rating["poem_id"]   = data.get("poem_id")
        pending_rating["rating"]    = float(data.get("rating", 0))
        pending_rating["highlights"] = data.get("highlights", [])
        rating_event.set()
    return jsonify({"ok": True})

@app.route("/lineages")
def get_lineages():
    return jsonify(load_lineages())

@app.route("/lineage/<lineage_id>/deactivate", methods=["POST"])
def deactivate_lineage(lineage_id):
    lineages = load_lineages()
    for l in lineages:
        if l["id"] == lineage_id:
            l["active"] = False
            break
    save_lineages(lineages)
    return jsonify({"ok": True})

@app.route("/lineage/<lineage_id>/activate", methods=["POST"])
def activate_lineage(lineage_id):
    lineages = load_lineages()
    for l in lineages:
        if l["id"] == lineage_id:
            l["active"] = True
            break
    save_lineages(lineages)
    return jsonify({"ok": True})


# ── Main loop (background thread) ─────────────────────────────────────────────

def run_loop():
    seed_if_empty()
    print(f"✦ Loop started. Open http://localhost:{PORT}")

    while True:
        poems    = load_poems()
        lineages = load_lineages()

        # Resume stale pending poem if it exists
        pending_poem = next((p for p in reversed(poems) if p["status"] == "pending"), None)

        if pending_poem:
            poem    = pending_poem
            lineage = next((l for l in lineages if l["id"] == poem["lineage_id"]), None)
            if lineage is None:
                print(f"⚠ Orphaned pending poem (lineage gone), skipping")
                for p in poems:
                    if p["id"] == poem["id"]:
                        p["status"] = "rated"
                        p["rating"] = 0
                save_poems(poems)
                continue
        else:
            lineage = select_next_lineage(lineages, poems)
            if lineage is None:
                print("✗ No active lineages. Waiting...")
                time.sleep(5)
                continue

            print(f"⟳ Generating from lineage {lineage['id'][:8]}…")
            poem = generate_poem(lineage)
            if poem is None:
                time.sleep(5)
                continue

            poems.append(poem)
            save_poems(poems)
            print(f"✦ Poem ready")

        # Wait for a rating from the UI
        rating_event.clear()
        print(f"⏳ Waiting for rating…")
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
        print(f"⟳ Mutating prompt…")
        new_prompt = mutate_prompt(lineage, poem, rating, highlights)
        print(f"✦ New prompt: \"{new_prompt[:60]}...\"")

        # Update lineage
        lineages = load_lineages()
        for l in lineages:
            if l["id"] == lineage["id"]:
                l["prompt"]       = new_prompt
                l["poem_count"]   = l.get("poem_count", 0) + 1
                l["fitness"]      = compute_fitness(l["id"], poems)
                l["last_rated_at"] = now_iso()
                lineage           = l
                break

        # Write prompt.md to best active lineage's prompt
        active = [l for l in lineages if l["active"]]
        if active:
            best = max(active, key=lambda l: l["fitness"])
            tmp = PROMPT_FILE.with_suffix(".tmp")
            tmp.write_text(best["prompt"])
            tmp.rename(PROMPT_FILE)

        # Branch + extinction
        lineages = maybe_branch(lineages, lineage, poem["id"], rating)
        lineages = check_extinction(lineages, poems)

        # Persist lineage update
        for i, l in enumerate(lineages):
            if l["id"] == lineage["id"]:
                lineages[i] = lineage
                break
        save_lineages(lineages)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    t = threading.Thread(target=run_loop, daemon=True)
    t.start()
    print(f"✦ Server at http://localhost:{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
