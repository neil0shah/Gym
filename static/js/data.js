const jumpDateInput = document.getElementById('jump-date');
const jumpDateBtn = document.getElementById('jump-date-btn');
const prevDateBtn = document.getElementById('prev-date-btn');
const nextDateBtn = document.getElementById('next-date-btn');
const dateFilterInput = document.getElementById('date-filter');
const dateListEl = document.getElementById('date-list');
const dayEmptyMsg = document.getElementById('day-empty-msg');
const daySessionsEl = document.getElementById('day-sessions');
const workoutTypeOptions = document.getElementById('data-workout-type-options');

const sessionTemplate = document.getElementById('data-session-template');
const exerciseRowTemplate = document.getElementById('data-exercise-row-template');
const setTemplate = document.getElementById('data-set-template');

let loggedDates = []; // sorted descending, 'YYYY-MM-DD' strings
let exercises = []; // all exercises for this account, for the add/reassign dropdowns
let currentDate = null;

function escapeHtml(s) {
    const div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
}

async function loadFilterData() {
    const [dates, exerciseList, workoutTypes] = await Promise.all([
        fetch('/api/logged_dates').then((r) => r.json()),
        fetch('/api/exercises').then((r) => r.json()),
        fetch('/api/workout_types').then((r) => r.json()),
    ]);
    loggedDates = dates;
    exercises = exerciseList.sort((a, b) => a.name.localeCompare(b.name));
    workoutTypeOptions.innerHTML = workoutTypes.map((wt) => `<option value="${escapeHtml(wt)}"></option>`).join('');
    renderDateList();
}

function renderDateList() {
    const filter = dateFilterInput.value.trim();
    const filtered = filter ? loggedDates.filter((d) => d.includes(filter)) : loggedDates;
    dateListEl.innerHTML = filtered.map((d) => `
        <li><button type="button" class="date-list-item${d === currentDate ? ' active' : ''}" data-date="${d}">${d}</button></li>
    `).join('') || '<li class="hint">No dates match.</li>';
    dateListEl.querySelectorAll('.date-list-item').forEach((btn) => {
        btn.addEventListener('click', () => selectDate(btn.dataset.date));
    });
}

function exerciseOptionsHtml(selectedId) {
    return exercises.map((e) =>
        `<option value="${e.id}" ${e.id === selectedId ? 'selected' : ''}>${escapeHtml(e.name)}</option>`
    ).join('');
}

async function selectDate(dateStr) {
    currentDate = dateStr;
    jumpDateInput.value = dateStr;
    renderDateList();
    history.replaceState(null, '', `/data?date=${dateStr}`);
    await loadDay(dateStr);
}

async function loadDay(dateStr) {
    const sessions = await fetch(`/api/days/${dateStr}`).then((r) => r.json());
    daySessionsEl.innerHTML = '';
    if (sessions.length === 0) {
        dayEmptyMsg.hidden = false;
        dayEmptyMsg.textContent = `Nothing logged on ${dateStr}. Use Add / Import to log a new session.`;
        return;
    }
    dayEmptyMsg.hidden = true;
    sessions.forEach(renderSessionCard);
}

function renderSessionCard(session) {
    const node = sessionTemplate.content.cloneNode(true);
    const card = node.querySelector('.session-card');
    card.dataset.sessionId = session.id;

    const dateInput = card.querySelector('.sess-date');
    const confidenceSelect = card.querySelector('.sess-confidence');
    const workoutTypeInput = card.querySelector('.sess-workout-type');
    const noteInput = card.querySelector('.sess-note');
    const rawTextEl = card.querySelector('.raw-text');
    const rowsBody = card.querySelector('.exercise-rows');
    const addExerciseSelect = card.querySelector('.add-exercise-select');
    const addExerciseBtn = card.querySelector('.add-exercise-btn');
    const deleteSessionBtn = card.querySelector('.delete-session-btn');

    dateInput.value = session.date;
    confidenceSelect.value = session.date_confidence;
    workoutTypeInput.value = session.workout_type || '';
    noteInput.value = session.note || '';
    rawTextEl.textContent = session.raw_note_text || '(nothing recorded)';

    const saveSessionField = async (body) => {
        const resp = await fetch(`/api/day-sessions/${session.id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!resp.ok) {
            alert('Failed to save: ' + await resp.text());
            return;
        }
        if ('date' in body && body.date !== currentDate) {
            await Promise.all([loadFilterData(), loadDay(currentDate)]);
        }
    };
    dateInput.addEventListener('change', () => saveSessionField({ date: dateInput.value }));
    confidenceSelect.addEventListener('change', () => saveSessionField({ date_confidence: confidenceSelect.value }));
    workoutTypeInput.addEventListener('blur', () => saveSessionField({ workout_type: workoutTypeInput.value || null }));
    noteInput.addEventListener('blur', () => saveSessionField({ note: noteInput.value || null }));

    deleteSessionBtn.addEventListener('click', async () => {
        if (!confirm('Delete this entire session, including all its exercises and sets? This cannot be undone.')) return;
        await fetch(`/api/day-sessions/${session.id}`, { method: 'DELETE' });
        await Promise.all([loadFilterData(), loadDay(currentDate)]);
    });

    session.exercises.slice().sort((a, b) => a.order_index - b.order_index).forEach((se) => {
        rowsBody.appendChild(buildExerciseRow(se));
    });

    addExerciseSelect.innerHTML = exerciseOptionsHtml(null);
    addExerciseBtn.addEventListener('click', async () => {
        const exerciseId = parseInt(addExerciseSelect.value, 10);
        if (!exerciseId) return;
        const resp = await fetch(`/api/day-sessions/${session.id}/exercises`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ exercise_id: exerciseId }),
        });
        if (!resp.ok) { alert('Failed to add exercise: ' + await resp.text()); return; }
        const newRow = await resp.json();
        rowsBody.appendChild(buildExerciseRow(newRow));
    });

    daySessionsEl.appendChild(node);
}

function buildExerciseRow(se) {
    const node = exerciseRowTemplate.content.cloneNode(true);
    const row = node.querySelector('.exercise-row');
    row.dataset.sessionExerciseId = se.id;

    const orderInput = row.querySelector('.ex-order');
    const exSelect = row.querySelector('.ex-select');
    const setsCell = row.querySelector('.sets-cell');
    const addSetBtn = row.querySelector('.add-set-btn');
    const deleteExerciseBtn = row.querySelector('.delete-exercise-btn');

    orderInput.value = se.order_index;
    exSelect.innerHTML = exerciseOptionsHtml(se.exercise_id);

    orderInput.addEventListener('change', async () => {
        const resp = await fetch(`/api/day-session-exercises/${se.id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ order_index: parseInt(orderInput.value, 10) || 0 }),
        });
        if (!resp.ok) { alert('Failed to reorder: ' + await resp.text()); return; }
        await loadDay(currentDate);
    });

    exSelect.addEventListener('change', async () => {
        const resp = await fetch(`/api/day-session-exercises/${se.id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ exercise_id: parseInt(exSelect.value, 10) }),
        });
        if (!resp.ok) { alert('Failed to change exercise: ' + await resp.text()); }
    });

    deleteExerciseBtn.addEventListener('click', async () => {
        if (!confirm('Remove this exercise (and all its sets) from the session?')) return;
        await fetch(`/api/day-session-exercises/${se.id}`, { method: 'DELETE' });
        row.remove();
    });

    se.sets.slice().sort((a, b) => a.set_number - b.set_number).forEach((set) => {
        setsCell.appendChild(buildSetGroup(set));
    });

    addSetBtn.addEventListener('click', async () => {
        const resp = await fetch(`/api/day-session-exercises/${se.id}/sets`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ weight_recorded: 0, reps_full: 0, reps_partial: null }),
        });
        if (!resp.ok) { alert('Failed to add set: ' + await resp.text()); return; }
        const newSet = await resp.json();
        const group = buildSetGroup(newSet);
        setsCell.appendChild(group);
        group.querySelector('.set-weight').focus();
    });

    return row;
}

function buildSetGroup(set) {
    const node = setTemplate.content.cloneNode(true);
    const group = node.querySelector('.set-group');
    group.dataset.setId = set.id;

    const weightInput = group.querySelector('.set-weight');
    const repsFullInput = group.querySelector('.set-reps-full');
    const repsPartialInput = group.querySelector('.set-reps-partial');
    const deleteBtn = group.querySelector('.delete-set-btn');

    weightInput.value = set.weight_recorded;
    repsFullInput.value = set.reps_full;
    repsPartialInput.value = set.reps_partial === null || set.reps_partial === undefined ? '' : set.reps_partial;
    if (set.raw_rep_string) {
        group.title = `As originally logged: ${set.raw_rep_string}`;
    }

    const saveSet = async () => {
        const resp = await fetch(`/api/sets/${set.id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                weight_recorded: parseFloat(weightInput.value) || 0,
                reps_full: parseInt(repsFullInput.value, 10) || 0,
                reps_partial: repsPartialInput.value === '' ? null : parseInt(repsPartialInput.value, 10),
            }),
        });
        if (!resp.ok) alert('Failed to save set: ' + await resp.text());
    };
    [weightInput, repsFullInput, repsPartialInput].forEach((input) => {
        input.addEventListener('blur', saveSet);
        input.addEventListener('keydown', (e) => { if (e.key === 'Enter') input.blur(); });
    });

    deleteBtn.addEventListener('click', async () => {
        await fetch(`/api/sets/${set.id}`, { method: 'DELETE' });
        group.remove();
    });

    return group;
}

jumpDateBtn.addEventListener('click', () => {
    if (jumpDateInput.value) selectDate(jumpDateInput.value);
});
jumpDateInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && jumpDateInput.value) selectDate(jumpDateInput.value);
});

prevDateBtn.addEventListener('click', () => {
    // loggedDates is sorted descending, so "previous" (further back in time) is the next entry after the current one.
    const idx = loggedDates.indexOf(currentDate);
    const candidates = currentDate === null ? loggedDates : loggedDates.filter((d) => d < currentDate);
    if (candidates.length > 0) selectDate(candidates[0]);
    else if (idx === -1 && loggedDates.length > 0) selectDate(loggedDates[0]);
});
nextDateBtn.addEventListener('click', () => {
    const candidates = currentDate === null ? [] : loggedDates.filter((d) => d > currentDate).sort();
    if (candidates.length > 0) selectDate(candidates[0]);
});

dateFilterInput.addEventListener('input', renderDateList);

(async function init() {
    await loadFilterData();
    const params = new URLSearchParams(window.location.search);
    const requested = params.get('date');
    const initial = requested || (loggedDates.length > 0 ? loggedDates[0] : null);
    if (initial) await selectDate(initial);
})();
