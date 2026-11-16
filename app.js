'use strict';

const DATA_URL    = 'data/questions.json';
const STORAGE_KEY = 'nremt_v1';
const LETTERS     = ['A', 'B', 'C', 'D', 'E', 'F'];

// ── Module-level data ──────────────────────────────────────────
let questionById = new Map();   // id → question object
let examIds = [];               // MCQ + multi IDs only
let allIds  = [];               // every ID

let state = null;               // persisted to localStorage

// ── State schema ───────────────────────────────────────────────
// {
//   v: 1,
//   mode: 'exam' | 'all',
//   cycle: 1,
//   correct: 0,            ← cumulative total (all cycles)
//   wrong: 0,              ← cumulative total (all cycles)
//   cycleCorrect: 0,       ← this cycle only
//   cycleWrong: 0,         ← this cycle only
//   cycleTotal: N,         ← size of pool at cycle start
//   queue: ['id', …],      ← remaining IDs, shuffled
// }

// ── Utilities ─────────────────────────────────────────────────

function $(id) { return document.getElementById(id); }

function shuffle(arr) {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

function escHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function saveState() {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch (_) {}
}

function loadState() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY)); } catch (_) { return null; }
}

// ── State helpers ──────────────────────────────────────────────

function poolFor(mode) { return mode === 'all' ? allIds : examIds; }

function freshState(mode, keepTotals = false) {
  const pool = poolFor(mode);
  return {
    v: 1,
    mode,
    cycle: 1,
    correct: keepTotals ? (state?.correct ?? 0) : 0,
    wrong:   keepTotals ? (state?.wrong   ?? 0) : 0,
    cycleCorrect: 0,
    cycleWrong:   0,
    cycleTotal:   pool.length,
    queue: shuffle(pool),
  };
}

function beginNextCycle() {
  const pool = poolFor(state.mode);
  state.cycle++;
  state.cycleCorrect = 0;
  state.cycleWrong   = 0;
  state.cycleTotal   = pool.length;
  state.queue        = shuffle(pool);
  saveState();
}

// ── Screen management ──────────────────────────────────────────

function showScreen(name) {
  for (const id of ['screen-loading', 'screen-quiz', 'screen-done']) {
    $(id).hidden = id !== name;
  }
}

function updateHeader() {
  $('n-correct').textContent   = state.correct;
  $('n-wrong').textContent     = state.wrong;
  $('n-remaining').textContent = state.queue.length;
  $('n-cycle').textContent     = state.cycle;

  const done = state.cycleTotal - state.queue.length;
  const pct  = state.cycleTotal > 0 ? (done / state.cycleTotal) * 100 : 0;
  $('progress-fill').style.width = pct + '%';

  const isExam = state.mode === 'exam';
  $('mode-exam').classList.toggle('active', isExam);
  $('mode-exam').setAttribute('aria-pressed', isExam ? 'true' : 'false');
  $('mode-all').classList.toggle('active', !isExam);
  $('mode-all').setAttribute('aria-pressed', isExam ? 'false' : 'true');
}

// ── Question rendering ─────────────────────────────────────────

function renderQuestion(q) {
  // Type badge
  const typeLabels = {
    mcq:       'Multiple Choice',
    multi:     'Select All That Apply',
    flashcard: 'Flashcard',
  };
  const typeEl = $('meta-type');
  typeEl.textContent = typeLabels[q.type] || q.type;
  typeEl.className   = 'badge badge-type-' + q.type;

  // Category badge
  const catEl = $('meta-cat');
  catEl.textContent = q.category || '';
  catEl.className   = 'badge badge-cat';

  // Difficulty badge
  const diffEl = $('meta-diff');
  diffEl.textContent = q.difficulty || '';
  diffEl.className   = 'badge badge-diff-' + (q.difficulty || '');

  $('question-text').textContent = q.question;

  // Reset dynamic sections
  $('choices').innerHTML = '';
  $('choices').removeAttribute('hidden');
  $('multi-submit').hidden   = true;
  $('multi-submit-btn').disabled = false;
  $('reveal-wrap').hidden    = true;
  $('reveal-answer').hidden  = true;
  $('self-report').hidden    = true;
  $('reveal-btn').hidden     = false;
  $('feedback').hidden       = true;

  if (q.type === 'mcq')           renderMCQ(q);
  else if (q.type === 'multi')    renderMulti(q);
  else                            renderFlashcard(q);

  showScreen('screen-quiz');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function makeChoiceBtn(text, index) {
  const btn      = document.createElement('button');
  btn.className  = 'choice-btn';
  btn.dataset.i  = index;
  btn.setAttribute('role', 'listitem');
  btn.innerHTML  =
    `<span class="choice-label">${LETTERS[index]}</span>` +
    `<span class="choice-text">${escHtml(text)}</span>`;
  return btn;
}

// MCQ ──────────────────────────────────────────────────────────

function renderMCQ(q) {
  const list = $('choices');
  q.choices.forEach((text, i) => {
    const btn = makeChoiceBtn(text, i);
    btn.addEventListener('click', () => submitMCQ(i, q, list), { once: true });
    list.appendChild(btn);
  });
}

function submitMCQ(chosen, q, list) {
  const btns = list.querySelectorAll('.choice-btn');
  btns.forEach(b => { b.disabled = true; });
  btns[q.correct].classList.add('is-correct');
  if (chosen !== q.correct) btns[chosen].classList.add('is-wrong');

  commitAnswer(chosen === q.correct, q);
  showFeedback(chosen === q.correct, q);
}

// Multi-select ─────────────────────────────────────────────────

function renderMulti(q) {
  const list     = $('choices');
  const selected = new Set();

  q.choices.forEach((text, i) => {
    const btn = makeChoiceBtn(text, i);
    btn.setAttribute('aria-pressed', 'false');
    btn.addEventListener('click', () => {
      if (selected.has(i)) {
        selected.delete(i);
        btn.classList.remove('is-selected');
        btn.setAttribute('aria-pressed', 'false');
      } else {
        selected.add(i);
        btn.classList.add('is-selected');
        btn.setAttribute('aria-pressed', 'true');
      }
    });
    list.appendChild(btn);
  });

  $('multi-submit').hidden = false;
  $('multi-submit-btn').addEventListener('click', () => submitMulti(selected, q, list), { once: true });
}

function submitMulti(selected, q, list) {
  const correctSet = new Set(q.correct);
  const btns = list.querySelectorAll('.choice-btn');

  btns.forEach((b, i) => {
    b.disabled = true;
    b.classList.remove('is-selected');
    if (correctSet.has(i))                        b.classList.add('is-correct');
    else if (selected.has(i) && !correctSet.has(i)) b.classList.add('is-wrong');
  });
  $('multi-submit-btn').disabled = true;

  const isCorrect =
    correctSet.size === selected.size &&
    [...selected].every(i => correctSet.has(i));

  commitAnswer(isCorrect, q);
  showFeedback(isCorrect, q);
}

// Flashcard ────────────────────────────────────────────────────

function renderFlashcard(q) {
  $('choices').setAttribute('hidden', '');
  $('reveal-wrap').hidden = false;

  $('reveal-btn').addEventListener('click', () => {
    $('reveal-btn').hidden    = true;
    $('reveal-answer').textContent = q.answer || '(no answer provided)';
    $('reveal-answer').hidden = false;
    $('self-report').hidden   = false;
  }, { once: true });

  $('self-correct').addEventListener('click', () => {
    $('self-report').hidden = true;
    commitAnswer(true, q);
    showFeedback(true, q);
  }, { once: true });

  $('self-wrong').addEventListener('click', () => {
    $('self-report').hidden = true;
    commitAnswer(false, q);
    showFeedback(false, q);
  }, { once: true });
}

// ── Feedback ───────────────────────────────────────────────────

function showFeedback(isCorrect, q) {
  $('reveal-wrap').hidden = true;

  const v = $('verdict');
  v.textContent = isCorrect ? '✓ Correct!' : '✗ Incorrect';
  v.className   = 'verdict ' + (isCorrect ? 'is-correct' : 'is-wrong');

  const ex = $('explanation');
  ex.textContent = q.explanation || '';
  ex.hidden = !q.explanation;

  $('feedback').hidden = false;
  $('next-btn').focus();
}

// ── Answer commit ──────────────────────────────────────────────

function commitAnswer(isCorrect, _q) {
  if (isCorrect) { state.correct++;      state.cycleCorrect++; }
  else           { state.wrong++;        state.cycleWrong++;   }
  state.queue.shift();   // pop the question we just answered
  saveState();
  updateHeader();
}

// ── Flow control ───────────────────────────────────────────────

function advance() {
  if (state.queue.length === 0) {
    showCycleDone();
  } else {
    nextQuestion();
  }
}

function nextQuestion() {
  // Skip any IDs that no longer exist in the loaded bank (data version mismatch)
  while (state.queue.length > 0 && !questionById.has(state.queue[0])) {
    state.queue.shift();
  }
  if (state.queue.length === 0) { showCycleDone(); return; }

  renderQuestion(questionById.get(state.queue[0]));
}

function showCycleDone() {
  $('done-cycle').textContent      = state.cycle;
  $('done-total').textContent      = state.cycleTotal.toLocaleString();
  $('done-correct').textContent    = state.cycleCorrect;
  $('done-wrong').textContent      = state.cycleWrong;
  $('done-next-cycle').textContent = state.cycle + 1;
  showScreen('screen-done');
  updateHeader();
}

// ── Mode switching ─────────────────────────────────────────────

function switchMode(newMode) {
  if (state.mode === newMode) return;

  const answered = state.cycleTotal - state.queue.length;
  if (answered > 0) {
    const ok = confirm(
      `Switching modes will restart your current cycle (${answered} of ${state.cycleTotal} answered). Continue?`
    );
    if (!ok) return;
  }

  state = freshState(newMode, /* keepTotals */ true);
  saveState();
  updateHeader();
  nextQuestion();
}

// ── Reset ──────────────────────────────────────────────────────

function doReset() {
  state = freshState(state?.mode ?? 'exam', /* keepTotals */ false);
  saveState();
  updateHeader();
  nextQuestion();
}

// ── Keyboard shortcuts ─────────────────────────────────────────
// Enter / Space / → : next question (when feedback visible)
// A / B / C / D     : click that choice (when choices visible)

document.addEventListener('keydown', e => {
  if (!$('screen-quiz') || $('screen-quiz').hidden) return;
  if (e.target.closest('button, input, select, textarea')) return;

  if (!$('feedback').hidden) {
    if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowRight') {
      e.preventDefault();
      $('next-btn').click();
    }
    return;
  }

  const idx = 'abcdef'.indexOf(e.key.toLowerCase());
  if (idx !== -1) {
    const btn = $('choices').querySelectorAll('.choice-btn:not([disabled])')[idx];
    if (btn) btn.click();
  }
});

// ── Boot ───────────────────────────────────────────────────────

async function boot() {
  showScreen('screen-loading');

  // Load question bank
  let data;
  try {
    const res = await fetch(DATA_URL);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch (err) {
    $('screen-loading').innerHTML =
      `<p style="color:var(--wrong);text-align:center;padding:2rem">
        Failed to load questions (${err.message}).<br>
        <button class="btn btn-primary" style="margin-top:1rem" onclick="location.reload()">Retry</button>
      </p>`;
    return;
  }

  // Index questions
  for (const q of data.questions) {
    questionById.set(q.id, q);
    if (q.type === 'mcq' || q.type === 'multi') examIds.push(q.id);
    allIds.push(q.id);
  }

  // Update mode button labels with counts
  $('count-exam').textContent = `(${examIds.length.toLocaleString()})`;
  $('count-all').textContent  = `(${allIds.length.toLocaleString()})`;

  // Load or initialise state
  const saved = loadState();
  if (saved?.v === 1 && Array.isArray(saved.queue)) {
    state = saved;
    // Revalidate queue against current question bank
    state.queue = state.queue.filter(id => questionById.has(id));
  } else {
    state = freshState('exam');
    saveState();
  }

  // Wire static controls
  $('reset-btn').addEventListener('click', () => { $('reset-modal').hidden = false; });
  $('reset-cancel').addEventListener('click', () => { $('reset-modal').hidden = true; });
  $('reset-confirm').addEventListener('click', () => {
    $('reset-modal').hidden = true;
    doReset();
  });

  $('next-btn').addEventListener('click', advance);
  $('next-cycle-btn').addEventListener('click', () => {
    beginNextCycle();
    updateHeader();
    nextQuestion();
  });

  $('mode-exam').addEventListener('click', () => switchMode('exam'));
  $('mode-all').addEventListener('click',  () => switchMode('all'));

  updateHeader();

  // If queue was exhausted in a previous session, show cycle-done screen
  if (state.queue.length === 0) {
    showCycleDone();
  } else {
    nextQuestion();
  }
}

document.addEventListener('DOMContentLoaded', boot);
