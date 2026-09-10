const rawInput = document.getElementById('raw-input');
const parseBtn = document.getElementById('parse-btn');
const parseStatus = document.getElementById('parse-status');
const warningsEl = document.getElementById('warnings');
const reviewRoot = document.getElementById('review-root');
const saveActions = document.getElementById('save-actions');
const saveBtn = document.getElementById('save-btn');
const saveStatus = document.getElementById('save-status');

function escapeHtml(s) {
    const div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
}

function hasFixedWeight(weightType) {
    return weightType === 'barbell_plate_per_side' || weightType === 'bodyweight_fixed';
}

function defaultFixedWeight(weightType) {
    return weightType === 'bodyweight_fixed' ? 160 : 45;
}

parseBtn.addEventListener('click', async () => {
    const text = rawInput.value;
    if (!text.trim()) return;
    parseStatus.textContent = 'Parsing...';
    parseStatus.className = 'status';
    saveStatus.textContent = '';
    try {
        const resp = await fetch('/api/parse', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ raw_text: text }),
        });
        if (!resp.ok) throw new Error(`Server error ${resp.status}`);
        const data = await resp.json();
        renderReview(data);
        parseStatus.textContent = `Parsed ${data.sessions.length} session(s).`;
        parseStatus.className = 'status success';
    } catch (err) {
        parseStatus.textContent = 'Failed to parse: ' + err.message;
        parseStatus.className = 'status error';
    }
});

function renderReview(data) {
    reviewRoot.innerHTML = '';

    if (data.warnings && data.warnings.length) {
        warningsEl.hidden = false;
        warningsEl.innerHTML = '<strong>Heads up — some lines were skipped:</strong><ul>' +
            data.warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join('') + '</ul>';
    } else {
        warningsEl.hidden = true;
        warningsEl.innerHTML = '';
    }

    const sessionTpl = document.getElementById('session-template');
    const exerciseTpl = document.getElementById('exercise-row-template');
    const setTpl = document.getElementById('set-template');

    data.sessions.forEach((session, sIdx) => {
        const node = sessionTpl.content.cloneNode(true);
        const card = node.querySelector('.session-card');
        card.querySelector('.session-title').textContent = `Session ${sIdx + 1}`;

        const dateInput = card.querySelector('.s-date');
        dateInput.value = session.date || '';
        const badge = card.querySelector('.confidence-badge');
        badge.textContent = session.date_confidence;
        badge.classList.add(session.date_confidence);
        dateInput.addEventListener('input', () => {
            badge.textContent = 'confirmed';
            badge.classList.remove('estimated');
            badge.classList.add('confirmed');
        });

        const workoutSelect = card.querySelector('.s-workout-type');
        const blankOpt = document.createElement('option');
        blankOpt.value = '';
        blankOpt.textContent = '(none)';
        workoutSelect.appendChild(blankOpt);
        (data.workout_types || []).forEach((wt) => {
            const opt = document.createElement('option');
            opt.value = wt;
            opt.textContent = wt;
            workoutSelect.appendChild(opt);
        });
        workoutSelect.value = session.workout_type_guess || '';

        card.querySelector('.s-note').value = session.note || '';

        const tbody = card.querySelector('.exercise-rows');
        session.exercises.forEach((ex, eIdx) => {
            const exNode = exerciseTpl.content.cloneNode(true);
            const row = exNode.querySelector('.exercise-row');
            if (ex.is_unrecognized) row.classList.add('unrecognized');
            row.querySelector('.ex-order').textContent = eIdx + 1;
            row.querySelector('.ex-name').value = ex.name;

            const wtSelect = row.querySelector('.ex-weight-type');
            wtSelect.value = ex.weight_type_guess;
            const barInput = row.querySelector('.ex-bar-weight');
            barInput.hidden = !hasFixedWeight(wtSelect.value);
            barInput.value = ex.bar_weight_guess ?? defaultFixedWeight(wtSelect.value);
            wtSelect.addEventListener('change', () => {
                barInput.hidden = !hasFixedWeight(wtSelect.value);
                if (!barInput.hidden && !barInput.value) {
                    barInput.value = defaultFixedWeight(wtSelect.value);
                }
            });

            const setsCell = row.querySelector('.sets-cell');
            ex.sets.forEach((st) => {
                const setNode = setTpl.content.cloneNode(true);
                setNode.querySelector('.set-weight').value = st.weight_recorded;
                setNode.querySelector('.set-reps-full').value = st.reps_full;
                const partialInput = setNode.querySelector('.set-reps-partial');
                partialInput.value = st.reps_partial ?? '';
                setsCell.appendChild(setNode);
            });

            tbody.appendChild(exNode);
        });

        card.querySelector('.raw-text').textContent = session.raw_text;
        reviewRoot.appendChild(node);
    });

    saveActions.hidden = data.sessions.length === 0;
}

saveBtn.addEventListener('click', async () => {
    const sessionCards = reviewRoot.querySelectorAll('.session-card');
    const sessions = [];
    let missingDate = false;

    sessionCards.forEach((card) => {
        const dateVal = card.querySelector('.s-date').value;
        if (!dateVal) missingDate = true;
        const confidence = card.querySelector('.confidence-badge').classList.contains('confirmed')
            ? 'confirmed' : 'estimated';
        const workoutType = card.querySelector('.s-workout-type').value || null;
        const note = card.querySelector('.s-note').value.trim() || null;
        const rawText = card.querySelector('.raw-text').textContent;

        const exercises = [];
        card.querySelectorAll('.exercise-row').forEach((row, idx) => {
            const name = row.querySelector('.ex-name').value.trim();
            const weightType = row.querySelector('.ex-weight-type').value;
            const barWeightInput = row.querySelector('.ex-bar-weight');
            const barWeight = hasFixedWeight(weightType)
                ? parseFloat(barWeightInput.value || defaultFixedWeight(weightType)) : null;

            const sets = [];
            row.querySelectorAll('.set-group').forEach((group, setIdx) => {
                const weight = parseFloat(group.querySelector('.set-weight').value);
                const repsFull = parseInt(group.querySelector('.set-reps-full').value, 10);
                const partialRaw = group.querySelector('.set-reps-partial').value;
                const repsPartial = partialRaw === '' ? null : parseInt(partialRaw, 10);
                const rawRepString = (repsPartial !== null) ? `${repsFull}+${repsPartial}` : `${repsFull}`;
                sets.push({
                    set_number: setIdx + 1,
                    weight_recorded: weight,
                    reps_full: repsFull,
                    reps_partial: repsPartial,
                    raw_rep_string: rawRepString,
                });
            });

            exercises.push({
                name,
                order_index: idx,
                weight_type: weightType,
                bar_weight: barWeight,
                sets,
            });
        });

        sessions.push({
            date: dateVal,
            date_confidence: confidence,
            workout_type: workoutType,
            raw_text: rawText,
            note,
            exercises,
        });
    });

    if (missingDate) {
        saveStatus.textContent = 'Every session needs a date before saving — fill in any blank date fields.';
        saveStatus.className = 'status error';
        return;
    }

    saveStatus.textContent = 'Saving...';
    saveStatus.className = 'status';
    try {
        const resp = await fetch('/api/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ sessions }),
        });
        if (!resp.ok) {
            const errText = await resp.text();
            throw new Error(errText);
        }
        const data = await resp.json();
        saveStatus.textContent = `Saved ${data.session_count} session(s), ${data.new_exercise_count} new exercise(s) classified.`;
        saveStatus.className = 'status success';
        reviewRoot.innerHTML = '';
        saveActions.hidden = true;
        rawInput.value = '';
        warningsEl.hidden = true;
        parseStatus.textContent = '';
    } catch (err) {
        saveStatus.textContent = 'Failed to save: ' + err.message;
        saveStatus.className = 'status error';
    }
});
