/* ── State ─────────────────────────────────────────────────────────────────── */

let currentPoemId = null;
let sliderValue   = null;   // null = untouched
let highlights    = [];
let dragging      = false;
let submitted     = false;
let polling       = false;

/* ── Elements ──────────────────────────────────────────────────────────────── */

const poemEl        = document.getElementById('poem');
const statusEl      = document.getElementById('status');
const hlWrap        = document.getElementById('highlights');
const track         = document.getElementById('slider-track');
const fill          = document.getElementById('slider-fill');
const thumb         = document.getElementById('slider-thumb');
const sendHint      = document.getElementById('send-hint');
const speciesToggle = document.getElementById('species-toggle');
const speciesPanel  = document.getElementById('species-panel');
const speciesClose  = document.getElementById('species-close');
const speciesList   = document.getElementById('species-list');

/* ── Slider ────────────────────────────────────────────────────────────────── */

function valueFromEvent(e) {
  const rect = track.getBoundingClientRect();
  const x    = (e.touches ? e.touches[0].clientX : e.clientX) - rect.left;
  return Math.max(0, Math.min(1, x / rect.width));
}

function setSlider(val) {
  sliderValue = val;
  const pct   = (val * 100).toFixed(2) + '%';
  thumb.style.left = pct;
  fill.style.width = pct;
  thumb.classList.remove('untouched');
  sendHint.classList.add('visible');
}

thumb.addEventListener('mousedown',  startDrag);
thumb.addEventListener('touchstart', startDrag, { passive: true });
track.addEventListener('mousedown',  e => { setSlider(valueFromEvent(e)); startDrag(e); });
track.addEventListener('touchstart', e => { setSlider(valueFromEvent(e)); startDrag(e); }, { passive: true });

function startDrag(e) {
  if (submitted) return;
  dragging = true;
  thumb.classList.add('dragging');
}

document.addEventListener('mousemove', onDrag);
document.addEventListener('touchmove',  onDrag, { passive: true });

function onDrag(e) {
  if (!dragging) return;
  setSlider(valueFromEvent(e));
}

document.addEventListener('mouseup',  endDrag);
document.addEventListener('touchend', endDrag);

function endDrag() {
  if (!dragging) return;
  dragging = false;
  thumb.classList.remove('dragging');
  if (sliderValue !== null && !submitted) {
    submit();
  }
}

/* ── Highlights ────────────────────────────────────────────────────────────── */

poemEl.addEventListener('mouseup', captureHighlight);
poemEl.addEventListener('touchend', captureHighlight);

function captureHighlight() {
  if (submitted) return;
  const sel  = window.getSelection();
  const text = sel ? sel.toString().trim() : '';
  if (text && text.length > 0 && text.length < 200) {
    if (!highlights.includes(text)) {
      highlights.push(text);
      renderHighlights();
    }
    sel.removeAllRanges();
  }
}

function renderHighlights() {
  hlWrap.innerHTML = '';
  highlights.forEach((hl, i) => {
    const pill = document.createElement('span');
    pill.className   = 'hl-pill';
    pill.textContent = hl;
    pill.title       = 'Click to remove';
    pill.addEventListener('click', () => {
      if (submitted) return;
      highlights.splice(i, 1);
      renderHighlights();
    });
    hlWrap.appendChild(pill);
  });
}

/* ── Submit ────────────────────────────────────────────────────────────────── */

async function submit() {
  if (submitted || sliderValue === null || !currentPoemId) return;
  submitted = true;

  poemEl.classList.add('fading');
  statusEl.textContent = '';
  sendHint.classList.remove('visible');

  await fetch('/rate', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({
      poem_id:    currentPoemId,
      rating:     sliderValue,
      highlights: highlights,
    }),
  });

  // Reset slider
  sliderValue      = null;
  highlights       = [];
  hlWrap.innerHTML = '';
  thumb.style.left = '0%';
  fill.style.width = '0%';
  thumb.classList.add('untouched');

  // Wait for next poem
  statusEl.textContent = 'generating';
  pollForPoem();
}

/* ── Poem polling ──────────────────────────────────────────────────────────── */

async function pollForPoem() {
  if (polling) return;
  polling = true;

  let delay = 1500;
  while (true) {
    await sleep(delay);
    try {
      const res  = await fetch('/poem');
      const data = await res.json();
      if (data.id && data.id !== currentPoemId) {
        displayPoem(data);
        break;
      }
    } catch (_) {}
    delay = Math.min(delay * 1.3, 4000);
  }
  polling = false;
}

function displayPoem(data) {
  currentPoemId        = data.id;
  submitted            = false;
  poemEl.textContent   = data.text;
  poemEl.classList.remove('fading', 'dim');
  statusEl.textContent = '';
  sendHint.classList.remove('visible');
}

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

/* ── Species panel ─────────────────────────────────────────────────────────── */

speciesToggle.addEventListener('click', openSpeciesPanel);
speciesClose.addEventListener('click',  () => speciesPanel.classList.remove('open'));

async function openSpeciesPanel() {
  speciesPanel.classList.add('open');
  speciesList.innerHTML = '<p style="color:#555;font-size:0.8rem">Loading...</p>';
  try {
    const res     = await fetch('/species');
    const species = await res.json();
    renderSpecies(species);
  } catch {
    speciesList.innerHTML = '<p style="color:#555;font-size:0.8rem">Could not load species.</p>';
  }
}

function renderSpecies(species) {
  speciesList.innerHTML = '';

  const active   = species.filter(s => s.active);
  const inactive = species.filter(s => !s.active);

  function card(s) {
    const div     = document.createElement('div');
    div.className = 'species-card' + (s.active ? '' : ' inactive');

    const fitness = typeof s.fitness === 'number' ? s.fitness : 0.5;
    const pct     = Math.round(fitness * 100);

    div.innerHTML = `
      <div class="species-prompt">${escHtml(s.prompt)}</div>
      <div class="fitness-bar-wrap">
        <div class="fitness-bar" style="width:${pct}%"></div>
      </div>
      <div class="species-meta">
        <span>fitness ${fitness.toFixed(2)}</span>
        <span>${s.poem_count} poem${s.poem_count !== 1 ? 's' : ''}</span>
        ${s.parent_id ? '<span>branched</span>' : '<span>origin</span>'}
      </div>
      <div class="species-actions">
        <button data-id="${s.id}" data-action="${s.active ? 'deactivate' : 'activate'}">
          ${s.active ? 'Kill' : 'Revive'}
        </button>
      </div>
    `;

    div.querySelector('button').addEventListener('click', async e => {
      const btn    = e.currentTarget;
      const id     = btn.dataset.id;
      const action = btn.dataset.action;
      await fetch(`/species/${id}/${action}`, { method: 'POST' });
      await openSpeciesPanel();
    });

    return div;
  }

  if (active.length) {
    const h = document.createElement('h2');
    h.textContent = `Active — ${active.length}`;
    speciesList.appendChild(h);
    active.forEach(s => speciesList.appendChild(card(s)));
  }

  if (inactive.length) {
    const h = document.createElement('h2');
    h.style.marginTop = '2rem';
    h.textContent = `Extinct — ${inactive.length}`;
    speciesList.appendChild(h);
    inactive.forEach(s => speciesList.appendChild(card(s)));
  }

  if (!species.length) {
    speciesList.innerHTML = '<p style="color:#555;font-size:0.8rem">No species yet.</p>';
  }
}

function escHtml(str) {
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

/* ── Init ──────────────────────────────────────────────────────────────────── */

async function init() {
  thumb.classList.add('untouched');
  statusEl.textContent = 'loading';

  try {
    const res  = await fetch('/poem');
    const data = await res.json();
    if (data.id) {
      displayPoem(data);
    } else {
      statusEl.textContent = 'generating';
      pollForPoem();
    }
  } catch {
    statusEl.textContent = 'could not connect';
  }
}

init();
