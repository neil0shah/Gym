const groupsTableBody = document.querySelector('#groups-table tbody');
const noGroupsMsg = document.getElementById('no-groups-msg');
const exercisesTableBody = document.querySelector('#exercises-table tbody');
const selectAllCheckbox = document.getElementById('select-all');
const newGroupNameInput = document.getElementById('new-group-name');
const createGroupBtn = document.getElementById('create-group-btn');
const existingGroupSelect = document.getElementById('existing-group-select');
const addToGroupBtn = document.getElementById('add-to-group-btn');
const ungroupBtn = document.getElementById('ungroup-btn');
const exercisesStatus = document.getElementById('exercises-status');

const WEIGHT_TYPE_LABELS = {
    barbell_plate_per_side: 'Barbell (plate/side)',
    dumbbell_each: 'Dumbbell (each)',
    total_weight: 'Total / machine weight',
    plate_loaded_per_side: 'Plate-loaded machine (no bar)',
    bodyweight_fixed: 'Bodyweight (fixed estimate)',
};

let exercises = [];
let groups = [];

async function loadAll() {
    [exercises, groups] = await Promise.all([
        fetch('/api/exercises?has_data=true').then((r) => r.json()),
        fetch('/api/exercise_groups').then((r) => r.json()),
    ]);
    renderGroups();
    renderExercises();
}

function renderGroups() {
    groupsTableBody.innerHTML = '';
    noGroupsMsg.hidden = groups.length > 0;

    existingGroupSelect.innerHTML = '<option value="">Add selected to existing group…</option>' +
        groups.map((g) => `<option value="${g.id}">${escapeHtml(g.name)}</option>`).join('');

    groups.forEach((g) => {
        const memberNames = g.exercise_ids
            .map((id) => exercises.find((e) => e.id === id)?.name)
            .filter(Boolean)
            .join(', ');
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><input type="text" class="group-name-input" value="${escapeHtml(g.name)}" data-id="${g.id}"></td>
            <td>${escapeHtml(memberNames)}</td>
            <td><button class="delete-group-btn" data-id="${g.id}">Delete group</button></td>
        `;
        groupsTableBody.appendChild(tr);
    });

    groupsTableBody.querySelectorAll('.delete-group-btn').forEach((btn) => {
        btn.addEventListener('click', async () => {
            if (!confirm('Delete this group? Its exercises stay, just ungrouped.')) return;
            await fetch(`/api/exercise_groups/${btn.dataset.id}`, { method: 'DELETE' });
            await loadAll();
        });
    });

    groupsTableBody.querySelectorAll('.group-name-input').forEach((input) => {
        const saveIfChanged = async () => {
            const newName = input.value.trim();
            const group = groups.find((g) => g.id === parseInt(input.dataset.id, 10));
            if (!newName || !group || newName === group.name) { input.value = group ? group.name : ''; return; }
            const resp = await fetch(`/api/exercise_groups/${input.dataset.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: newName }),
            });
            if (!resp.ok) {
                setStatus('Failed to rename group: ' + await resp.text(), true);
                await loadAll();
                return;
            }
            setStatus(`Renamed group to "${newName}".`, false);
            await loadAll();
        };
        input.addEventListener('blur', saveIfChanged);
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') input.blur();
        });
    });
}

function renderExercises() {
    exercisesTableBody.innerHTML = '';
    const sorted = [...exercises].sort((a, b) => {
        if (!!a.group_id !== !!b.group_id) return a.group_id ? 1 : -1;
        return a.name.localeCompare(b.name);
    });
    const groupOptions = '<option value="">— (none)</option>' +
        groups.map((g) => `<option value="${g.id}">${escapeHtml(g.name)}</option>`).join('');

    sorted.forEach((e) => {
        const tr = document.createElement('tr');
        if (!e.group_id) tr.classList.add('unrecognized');
        tr.innerHTML = `
            <td><input type="checkbox" class="ex-checkbox" data-id="${e.id}"></td>
            <td><button type="button" class="ex-name-link" data-id="${e.id}">${escapeHtml(e.name)}</button></td>
            <td>${WEIGHT_TYPE_LABELS[e.weight_type] || e.weight_type}</td>
            <td><input type="checkbox" class="ex-combined-checkbox" data-id="${e.id}" ${e.combined_both_sides ? 'checked' : ''}></td>
            <td><select class="ex-group-select" data-id="${e.id}">${groupOptions}</select></td>
        `;
        tr.querySelector('.ex-group-select').value = e.group_id || '';
        exercisesTableBody.appendChild(tr);
    });
    selectAllCheckbox.checked = false;

    exercisesTableBody.querySelectorAll('.ex-combined-checkbox').forEach((cb) => {
        cb.addEventListener('change', async () => {
            const resp = await fetch(`/api/exercises/${cb.dataset.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ combined_both_sides: cb.checked }),
            });
            if (!resp.ok) {
                setStatus('Failed to update: ' + await resp.text(), true);
                cb.checked = !cb.checked;
                return;
            }
            const ex = exercises.find((e) => e.id === parseInt(cb.dataset.id, 10));
            if (ex) ex.combined_both_sides = cb.checked;
            setStatus('Updated.', false);
        });
    });

    exercisesTableBody.querySelectorAll('.ex-group-select').forEach((select) => {
        select.addEventListener('change', async () => {
            const id = select.dataset.id;
            const groupId = select.value ? parseInt(select.value, 10) : null;
            const resp = await fetch(`/api/exercises/${id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ group_id: groupId }),
            });
            if (!resp.ok) {
                setStatus('Failed to update group: ' + await resp.text(), true);
                await loadAll();
                return;
            }
            setStatus('Updated.', false);
            await loadAll();
        });
    });

    exercisesTableBody.querySelectorAll('.ex-name-link').forEach((btn) => {
        btn.addEventListener('click', () => openHistoryModal(parseInt(btn.dataset.id, 10)));
    });
}

const historyModalOverlay = document.getElementById('history-modal-overlay');
const historyModalTitle = document.getElementById('history-modal-title');
const historyModalBody = document.getElementById('history-modal-body');
const historyModalClose = document.getElementById('history-modal-close');

async function openHistoryModal(exerciseId) {
    const ex = exercises.find((e) => e.id === exerciseId);
    historyModalTitle.textContent = ex ? ex.name : 'Exercise history';
    historyModalBody.innerHTML = '<p class="hint">Loading…</p>';
    historyModalOverlay.hidden = false;

    const resp = await fetch(`/api/exercises/${exerciseId}/history`);
    if (!resp.ok) {
        historyModalBody.innerHTML = '<p class="status error">Failed to load history.</p>';
        return;
    }
    const sessions = await resp.json();
    if (sessions.length === 0) {
        historyModalBody.innerHTML = '<p class="hint">No logged sets for this exercise yet.</p>';
        return;
    }
    historyModalBody.innerHTML = sessions.map((s) => `
        <div class="history-entry">
            <div class="history-entry-head">
                <span class="history-entry-date">${s.date}</span>
                ${s.workout_type ? `<span class="history-entry-type">${escapeHtml(s.workout_type)}</span>` : ''}
                ${s.date_confidence === 'estimated' ? '<span class="confidence-badge estimated">Estimated date</span>' : ''}
                ${s.note ? `<span class="history-entry-note">${escapeHtml(s.note)}</span>` : ''}
            </div>
            <div class="history-sets">
                ${s.sets.map((set) => `
                    <span class="history-set">
                        <span class="set-idx">#${set.set_number}</span>
                        ${set.weight_recorded}&nbsp;lb &times; ${set.reps_full}${set.reps_partial ? `+${set.reps_partial}` : ''}
                    </span>
                `).join('')}
            </div>
        </div>
    `).join('');
}

function closeHistoryModal() {
    historyModalOverlay.hidden = true;
}

historyModalClose.addEventListener('click', closeHistoryModal);
historyModalOverlay.addEventListener('click', (e) => {
    if (e.target === historyModalOverlay) closeHistoryModal();
});
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !historyModalOverlay.hidden) closeHistoryModal();
});

function escapeHtml(s) {
    const div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
}

function selectedIds() {
    return Array.from(exercisesTableBody.querySelectorAll('.ex-checkbox:checked'))
        .map((cb) => parseInt(cb.dataset.id, 10));
}

selectAllCheckbox.addEventListener('change', () => {
    exercisesTableBody.querySelectorAll('.ex-checkbox').forEach((cb) => {
        cb.checked = selectAllCheckbox.checked;
    });
});

function setStatus(text, isError) {
    exercisesStatus.textContent = text;
    exercisesStatus.className = 'status' + (isError ? ' error' : ' success');
}

createGroupBtn.addEventListener('click', async () => {
    const name = newGroupNameInput.value.trim();
    const ids = selectedIds();
    if (!name) { setStatus('Enter a name for the new group.', true); return; }
    if (ids.length === 0) { setStatus('Select at least one exercise.', true); return; }
    const resp = await fetch('/api/exercise_groups', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, exercise_ids: ids }),
    });
    if (!resp.ok) {
        const errText = await resp.text();
        setStatus('Failed to create group: ' + errText, true);
        return;
    }
    newGroupNameInput.value = '';
    setStatus(`Created group "${name}" with ${ids.length} exercise(s).`, false);
    await loadAll();
});

addToGroupBtn.addEventListener('click', async () => {
    const groupId = existingGroupSelect.value;
    const ids = selectedIds();
    if (!groupId) { setStatus('Pick a group to add to.', true); return; }
    if (ids.length === 0) { setStatus('Select at least one exercise.', true); return; }
    const resp = await fetch(`/api/exercise_groups/${groupId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ add_exercise_ids: ids }),
    });
    if (!resp.ok) {
        const errText = await resp.text();
        setStatus('Failed to add to group: ' + errText, true);
        return;
    }
    setStatus(`Added ${ids.length} exercise(s) to the group.`, false);
    await loadAll();
});

ungroupBtn.addEventListener('click', async () => {
    const ids = selectedIds();
    if (ids.length === 0) { setStatus('Select at least one exercise.', true); return; }

    const byGroup = new Map();
    ids.forEach((id) => {
        const ex = exercises.find((e) => e.id === id);
        if (ex && ex.group_id) {
            if (!byGroup.has(ex.group_id)) byGroup.set(ex.group_id, []);
            byGroup.get(ex.group_id).push(id);
        }
    });

    if (byGroup.size === 0) { setStatus('None of the selected exercises are in a group.', true); return; }

    await Promise.all(
        Array.from(byGroup.entries()).map(([groupId, removeIds]) =>
            fetch(`/api/exercise_groups/${groupId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ remove_exercise_ids: removeIds }),
            })
        )
    );
    setStatus('Removed selected exercise(s) from their group(s).', false);
    await loadAll();
});

loadAll();
