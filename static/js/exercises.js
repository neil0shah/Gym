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
        fetch('/api/exercises').then((r) => r.json()),
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
            <td>${escapeHtml(g.name)}</td>
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
}

function renderExercises() {
    exercisesTableBody.innerHTML = '';
    exercises.forEach((e) => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><input type="checkbox" class="ex-checkbox" data-id="${e.id}"></td>
            <td>${escapeHtml(e.name)}</td>
            <td>${WEIGHT_TYPE_LABELS[e.weight_type] || e.weight_type}</td>
            <td>${e.group_name ? escapeHtml(e.group_name) : '—'}</td>
        `;
        exercisesTableBody.appendChild(tr);
    });
    selectAllCheckbox.checked = false;
}

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
