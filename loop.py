#!/usr/bin/env python3
"""
Poetry Self-Improving Loop
Generate → rate → mutate → branch → repeat
All 4 species generate in parallel. You never wait.
"""

import concurrent.futures
import json
import os
import queue
import random
import threading
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

PORT             = int(os.getenv("PORT", 7331))
MODEL            = "claude-haiku-4-5-20251001"
BRANCH_THRESHOLD = 0.5
EXTINCTION_FLOOR = 0.35
EXTINCTION_MIN   = 5
FITNESS_WINDOW   = 5
TARGET_SPECIES   = 4
EXPLORATION_RATE = 0.2

BASE_DIR     = Path(__file__).parent
DATA_DIR     = BASE_DIR / "data"
PROMPT_FILE  = BASE_DIR / "prompt.md"
POEMS_FILE      = DATA_DIR / "poems.json"
SPECIES_FILE    = DATA_DIR / "species.json"
HIGHLIGHTS_FILE = DATA_DIR / "highlights.json"
WEB_DIR         = BASE_DIR / "web"

# Poem status values: "queued" | "showing" | "rated"
# queued  = generated, waiting to be shown
# showing = currently displayed in the browser
# rated   = user has submitted a rating

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
app    = Flask(__name__)

# ── Concurrency ───────────────────────────────────────────────────────────────

rating_queue = queue.Queue()   # Flask → processor; item: {poem_id, rating, highlights}
data_lock    = threading.Lock()  # serialises read-modify-write on JSON files
poem_pool    = concurrent.futures.ThreadPoolExecutor(
    max_workers=TARGET_SPECIES + 2, thread_name_prefix="gen"
)


# ── Atomic I/O ────────────────────────────────────────────────────────────────

def atomic_write(path: Path, data: list | dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.rename(path)

def _load(path: Path, default: list | dict) -> list | dict:
    if not path.exists():
        return default
    return json.loads(path.read_text())

def load_poems()              -> list[dict]: return _load(POEMS_FILE, [])
def load_highlights()         -> list[dict]: return _load(HIGHLIGHTS_FILE, [])
def load_species()            -> list[dict]:
    old = DATA_DIR / "lineages.json"
    if not SPECIES_FILE.exists() and old.exists():
        old.rename(SPECIES_FILE)
    return _load(SPECIES_FILE, [])

def append_poem(poem: dict) -> None:
    with data_lock:
        poems = load_poems()
        poems.append(poem)
        atomic_write(POEMS_FILE, poems)

def record_highlights(phrases: list[str], poem_id: str,
                      species_id: str, rating: float) -> None:
    """Append highlighted phrases to the cross-species pool."""
    with data_lock:
        pool = load_highlights()
        for phrase in phrases:
            pool.append({
                "phrase":     phrase,
                "poem_id":    poem_id,
                "species_id": species_id,
                "rating":     rating,
                "created_at": now_iso(),
            })
        atomic_write(HIGHLIGHTS_FILE, pool)

def update_poem(poem_id: str, **kwargs) -> None:
    with data_lock:
        poems = load_poems()
        for p in poems:
            if p["id"] == poem_id:
                p.update(kwargs)
                break
        atomic_write(POEMS_FILE, poems)

def save_species_locked(all_species: list[dict]) -> None:
    with data_lock:
        atomic_write(SPECIES_FILE, all_species)

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Species helpers ───────────────────────────────────────────────────────────

def compute_fitness(species_id: str, poems: list[dict]) -> float:
    rated = [p for p in poems
             if p["lineage_id"] == species_id and p["status"] == "rated"]
    if not rated:
        return 0.5
    recent = rated[-FITNESS_WINDOW:]
    return sum(p["rating"] for p in recent) / len(recent)

def _new_species(prompt: str, parent_id: str | None = None,
                 spawned_by_poem_id: str | None = None,
                 spawned_by_rating: float | None = None) -> dict:
    return {
        "id":                 str(uuid.uuid4()),
        "parent_id":          parent_id,
        "prompt":             prompt,
        "fitness":            0.5,
        "poem_count":         0,
        "spawned_by_poem_id": spawned_by_poem_id,
        "spawned_by_rating":  spawned_by_rating,
        "active":             True,
        "created_at":         now_iso(),
        "last_rated_at":      None,
    }

def seed_if_empty() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    if load_species():
        return
    prompt = PROMPT_FILE.read_text().strip()
    with data_lock:
        atomic_write(SPECIES_FILE, [_new_species(prompt)])
    print("✦ First species seeded from prompt.md")

def check_extinction(all_species: list[dict], poems: list[dict]) -> list[dict]:
    for s in all_species:
        if not s["active"]:
            continue
        rated_count = sum(1 for p in poems
                          if p["lineage_id"] == s["id"] and p["status"] == "rated")
        s["fitness"] = compute_fitness(s["id"], poems)
        if rated_count >= EXTINCTION_MIN and s["fitness"] < EXTINCTION_FLOOR:
            s["active"] = False
            print(f"💀 Species {s['id'][:8]} extinct (fitness={s['fitness']:.2f})")
    return all_species

def maybe_branch(all_species: list[dict], sp: dict,
                 poem_id: str, rating: float) -> list[dict]:
    if rating <= BRANCH_THRESHOLD:
        return all_species
    if any(s.get("spawned_by_poem_id") == poem_id for s in all_species):
        return all_species
    branch = _new_species(sp["prompt"], parent_id=sp["id"],
                          spawned_by_poem_id=poem_id, spawned_by_rating=rating)
    all_species.append(branch)
    print(f"🌿 New species {branch['id'][:8]} (rating={rating:.2f})")
    return all_species


# ── Claude calls ──────────────────────────────────────────────────────────────

def _spawn_prompt(existing_prompts: list[str], unique_hl: list[str]) -> str:
    existing_str = "\n".join(f"- {p}" for p in existing_prompts) or "none"
    hl_str = ", ".join(f'"{h}"' for h in unique_hl[:10]) or "none yet"
    content = (
        f"These poetry prompts are already in play:\n{existing_str}\n\n"
        f"Phrases that gave the reader a physical reaction: {hl_str}\n\n"
        "Write a NEW poetry prompt (≤50 words) that goes somewhere completely different "
        "from all the above — different sensory domain, different relationship to grammar "
        "or silence or time. Be specific, physical, strange. "
        "Do not explain — just write the prompt."
    )
    try:
        msg = client.messages.create(
            model=MODEL, max_tokens=150,
            messages=[{"role": "user", "content": content}],
        )
        return " ".join(msg.content[0].text.strip().split()[:50])
    except Exception as e:
        print(f"✗ API error (spawn): {e}")
        return PROMPT_FILE.read_text().strip()


def spawn_new_species_parallel(needed: int, existing_prompts: list[str],
                                poems: list[dict]) -> list[dict]:
    """Spawn `needed` new species in parallel, each diverging from the others."""
    if needed <= 0:
        return []

    pool_entries = load_highlights()
    unique_hl = list(dict.fromkeys(e["phrase"] for e in pool_entries))

    futures = [
        poem_pool.submit(_spawn_prompt, list(existing_prompts), unique_hl)
        for _ in range(needed)
    ]

    new_species = []
    for f in concurrent.futures.as_completed(futures):
        prompt = f.result()
        sp = _new_species(prompt)
        new_species.append(sp)
        existing_prompts.append(prompt)
        print(f"🌱 New species: \"{prompt[:60]}...\"")

    return new_species


def ensure_species_count(all_species: list[dict], poems: list[dict]) -> list[dict]:
    active = [s for s in all_species if s["active"]]
    needed = TARGET_SPECIES - len(active)
    if needed <= 0:
        return all_species
    print(f"🌍 {len(active)} active, spawning {needed} in parallel...")
    existing_prompts = [s["prompt"] for s in all_species if s["active"]]
    new_species = spawn_new_species_parallel(needed, existing_prompts, poems)
    all_species.extend(new_species)
    return all_species


def generate_poem(sp: dict) -> dict | None:
    """Generate a 25-word poem. Returns None only if both API calls fail."""
    def call() -> str:
        msg = client.messages.create(
            model=MODEL, max_tokens=200,
            messages=[{
                "role": "user",
                "content": (
                    "Write a poem of exactly 25 words. No title. No explanation. "
                    f"Just the poem.\n\nPrompt: {sp['prompt']}"
                ),
            }],
        )
        return msg.content[0].text.strip()

    last_text: str | None = None
    for attempt in range(2):
        try:
            text = call()
            last_text = text
            if len(text.split()) == 25:
                break
            if attempt == 0:
                print(f"⚠ Word count {len(text.split())}, retrying...")
        except Exception as e:
            print(f"✗ API error (generate attempt {attempt + 1}): {e}")

    if last_text is None:
        return None

    return {
        "id":         str(uuid.uuid4()),
        "lineage_id": sp["id"],
        "text":       last_text,
        "rating":     None,
        "highlights": [],
        "created_at": now_iso(),
        "status":     "queued",
    }


def mutate_prompt(sp: dict, poem: dict, rating: float,
                  highlights: list[str], global_highlights: list[str]) -> str:
    if highlights:
        hl_section = (
            f"The reader highlighted from THIS poem: {json.dumps(highlights)}\n"
            "These mark a physical reaction — a chill, a catch in the throat. "
            "The specific words are not the point. Chase the felt quality behind them: "
            "the compression, the rhythm, the thing that made the body respond. "
            "Do not repeat the highlighted words. Go deeper than them.\n\n"
        )
    else:
        hl_section = "No phrases were highlighted from this poem.\n\n"

    if global_highlights:
        global_section = (
            f"Across ALL species, these phrases have caused physical reactions in the reader: "
            f"{json.dumps(global_highlights)}\n"
            "These are signals from the reader's body — moments of felt quality that transcend "
            "any single poem. They are not instructions; they are a compass heading. "
            "Let them inform the direction without repeating them.\n\n"
        )
    else:
        global_section = ""

    content = (
        f"Current prompt (≤50 words):\n{sp['prompt']}\n\n"
        f"Most recent poem:\n{poem['text']}\n\n"
        f"Rating: {rating:.2f} (0=nothing, 0.5=frisson, 1=masterpiece)\n\n"
        f"{hl_section}"
        f"{global_section}"
        "Rewrite the prompt (≤50 words) to move toward more of that quality. "
        "Keep what's working. Do not explain — just write the new prompt."
    )
    try:
        msg = client.messages.create(
            model=MODEL, max_tokens=150,
            messages=[{"role": "user", "content": content}],
        )
        return " ".join(msg.content[0].text.strip().split()[:50])
    except Exception as e:
        print(f"✗ API error (mutate): {e}")
        return sp["prompt"]


# ── Poem queue management ─────────────────────────────────────────────────────

def _pick_next_showing(poems: list[dict], all_species: list[dict]) -> dict | None:
    """Pick the best queued poem and mark it showing. Returns it, or None."""
    active_map = {s["id"]: s for s in all_species if s["active"]}
    queued = [p for p in poems
              if p["status"] == "queued" and p["lineage_id"] in active_map]
    if not queued:
        return None

    if random.random() < EXPLORATION_RATE:
        chosen = random.choice(queued)
    else:
        weights = [max(active_map[p["lineage_id"]]["fitness"], 0.01) for p in queued]
        chosen = random.choices(queued, weights=weights, k=1)[0]

    chosen["status"] = "showing"
    return chosen


def prefill_queue() -> None:
    """Background: generate a queued poem for every active species that needs one."""
    poems       = load_poems()
    all_species = load_species()
    active      = [s for s in all_species if s["active"]]

    needs = [
        sp for sp in active
        if not any(
            p["lineage_id"] == sp["id"] and p["status"] in ("queued", "showing")
            for p in poems
        )
    ]

    if not needs:
        return

    print(f"⟳ Pre-generating {len(needs)} poems in parallel...")
    futures = {poem_pool.submit(generate_poem, sp): sp for sp in needs}

    for future in concurrent.futures.as_completed(futures):
        poem = future.result()
        if poem:
            append_poem(poem)
            print(f"✦ Queued poem ready (species {poem['lineage_id'][:8]})")


def _generate_and_queue(sp: dict) -> None:
    """Generate one poem for sp and append it as queued."""
    # Reload sp to get its freshest prompt after mutation
    all_species = load_species()
    sp = next((s for s in all_species if s["id"] == sp["id"]), sp)
    poem = generate_poem(sp)
    if poem:
        append_poem(poem)
        print(f"✦ Replacement queued for species {sp['id'][:8]}")


# ── Rating processor ──────────────────────────────────────────────────────────

def process_rating(r: dict) -> None:
    poem_id    = r["poem_id"]
    rating     = r["rating"]
    highlights = r["highlights"]

    poems       = load_poems()
    all_species = load_species()

    poem = next((p for p in poems if p["id"] == poem_id), None)
    if poem is None:
        print(f"⚠ Poem {poem_id[:8]} not found, skipping")
        return
    sp = next((s for s in all_species if s["id"] == poem["lineage_id"]), None)
    if sp is None:
        print(f"⚠ Species for poem {poem_id[:8]} not found, skipping")
        return

    print(f"★ {rating:.2f}  highlights={highlights}")

    # Mark poem rated
    update_poem(poem_id, status="rated", rating=rating, highlights=highlights)

    # Record any highlights into the cross-species pool (frisson threshold)
    if highlights and rating >= 0.5:
        record_highlights(highlights, poem_id, sp["id"], rating)

    # Pull recent cross-species highlights (most recent 15, deduplicated)
    all_hl = load_highlights()
    seen: set[str] = set()
    global_highlights: list[str] = []
    for entry in reversed(all_hl):
        phrase = entry["phrase"]
        if phrase not in seen:
            seen.add(phrase)
            global_highlights.append(phrase)
        if len(global_highlights) == 15:
            break

    # Mutate prompt (API call — no lock held)
    print(f"⟳ Mutating species {sp['id'][:8]}...")
    new_prompt = mutate_prompt(sp, poem, rating, highlights, global_highlights)
    print(f"✦ New prompt: \"{new_prompt[:60]}...\"")

    # Update species, branch, extinction, refill — under lock for save only
    with data_lock:
        all_species = load_species()
        poems       = load_poems()

        for s in all_species:
            if s["id"] == sp["id"]:
                s["prompt"]       = new_prompt
                s["poem_count"]   = s.get("poem_count", 0) + 1
                s["fitness"]      = compute_fitness(s["id"], poems)
                s["last_rated_at"] = now_iso()
                sp = s
                break

        all_species = maybe_branch(all_species, sp, poem_id, rating)
        all_species = check_extinction(all_species, poems)
        # Note: ensure_species_count makes API calls; do it outside the lock below
        atomic_write(SPECIES_FILE, all_species)

    # Refill species count outside the lock (API calls)
    all_species = load_species()
    poems       = load_poems()
    all_species = ensure_species_count(all_species, poems)
    with data_lock:
        atomic_write(SPECIES_FILE, all_species)

    # Generate replacement poem for the mutated species in background
    poem_pool.submit(_generate_and_queue, sp)

    # Fill queue for any other species that are empty
    poem_pool.submit(prefill_queue)


def rating_processor() -> None:
    """Dedicated thread: drain rating_queue and call process_rating."""
    while True:
        try:
            r = rating_queue.get(block=True)
            process_rating(r)
            rating_queue.task_done()
        except Exception:
            traceback.print_exc()


# ── HTTP endpoints ────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")

@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(WEB_DIR, filename)

@app.route("/poem")
def get_poem():
    with data_lock:
        poems       = load_poems()
        all_species = load_species()

        # Return whatever is currently showing
        showing = next((p for p in poems if p["status"] == "showing"), None)
        if showing:
            return jsonify({"id": showing["id"], "text": showing["text"]})

        # Nothing showing — advance the best queued poem
        next_poem = _pick_next_showing(poems, all_species)
        if next_poem:
            atomic_write(POEMS_FILE, poems)
            return jsonify({"id": next_poem["id"], "text": next_poem["text"]})

    return jsonify({"id": None, "text": None})

@app.route("/rate", methods=["POST"])
def post_rate():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "invalid json"}), 400

    poem_id = data.get("poem_id")
    if not isinstance(poem_id, str) or not poem_id:
        return jsonify({"error": "invalid poem_id"}), 400

    try:
        rating = max(0.0, min(1.0, float(data.get("rating", 0))))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid rating"}), 400

    raw_hl = data.get("highlights", [])
    highlights = [str(h)[:200] for h in (raw_hl if isinstance(raw_hl, list) else [])[:10]]

    rating_queue.put({"poem_id": poem_id, "rating": rating, "highlights": highlights})
    return jsonify({"ok": True})

@app.route("/species")
def get_species():
    return jsonify(load_species())

@app.route("/species/<species_id>/deactivate", methods=["POST"])
def deactivate_species(species_id):
    with data_lock:
        all_species = load_species()
        for s in all_species:
            if s["id"] == species_id:
                s["active"] = False
                break
        atomic_write(SPECIES_FILE, all_species)
    return jsonify({"ok": True})

@app.route("/species/<species_id>/activate", methods=["POST"])
def activate_species(species_id):
    with data_lock:
        all_species = load_species()
        for s in all_species:
            if s["id"] == species_id:
                s["active"] = True
                break
        atomic_write(SPECIES_FILE, all_species)
    poem_pool.submit(prefill_queue)  # generate for newly activated species
    return jsonify({"ok": True})

@app.route("/status")
def get_status():
    poems = load_poems()
    species = load_species()
    return jsonify({
        "active_species": sum(1 for s in species if s["active"]),
        "queued_poems":   sum(1 for p in poems if p["status"] == "queued"),
        "showing_poems":  sum(1 for p in poems if p["status"] == "showing"),
        "rating_backlog": rating_queue.qsize(),
    })


# ── Startup ───────────────────────────────────────────────────────────────────

def startup() -> None:
    seed_if_empty()

    # Migrate old "pending" status to "queued"
    with data_lock:
        poems = load_poems()
        changed = False
        for p in poems:
            if p["status"] == "pending":
                p["status"] = "queued"
                changed = True
        if changed:
            atomic_write(POEMS_FILE, poems)

    # Ensure 4 species (sequential at startup, then parallel fills will happen)
    poems       = load_poems()
    all_species = load_species()
    all_species = ensure_species_count(all_species, poems)
    with data_lock:
        atomic_write(SPECIES_FILE, all_species)

    # Start background rating processor
    threading.Thread(target=rating_processor, daemon=True, name="rating-proc").start()

    # Pre-generate poems for all species in parallel (non-blocking)
    poem_pool.submit(prefill_queue)

    print(f"✦ Started. Open http://localhost:{PORT}")
    print(f"✦ Generating poems for all {TARGET_SPECIES} species in parallel...")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    startup()
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)
