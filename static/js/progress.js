const exerciseSelect = document.getElementById('exercise-select');
const orderIndexSelect = document.getElementById('order-index-select');
const workoutTypeSelect = document.getElementById('workout-type-select');
const startDateInput = document.getElementById('start-date');
const endDateInput = document.getElementById('end-date');
const periodSelect = document.getElementById('period-select');

let exerciseChart, repsChart, volumeChart, frequencyChart;

const PALETTE = ['#5b9dff', '#4caf7d', '#d9a441', '#e0616b', '#b083f0', '#3fc1c9', '#f08a5d', '#8ecae6'];

function qs(params) {
    const usp = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
        if (v !== null && v !== undefined && v !== '') usp.set(k, v);
    });
    const s = usp.toString();
    return s ? `?${s}` : '';
}

async function loadFilterOptions() {
    const [exercises, workoutTypes] = await Promise.all([
        fetch('/api/exercises').then((r) => r.json()),
        fetch('/api/workout_types').then((r) => r.json()),
    ]);

    exerciseSelect.innerHTML = '<option value="">All exercises</option>' +
        exercises.map((e) => `<option value="${e.id}">${e.name}</option>`).join('');

    workoutTypeSelect.innerHTML = '<option value="">All</option>' +
        workoutTypes.map((wt) => `<option value="${wt}">${wt}</option>`).join('');

    for (let i = 0; i < 8; i++) {
        const opt = document.createElement('option');
        opt.value = i;
        opt.textContent = `${i + 1}${ordinalSuffix(i + 1)}`;
        orderIndexSelect.appendChild(opt);
    }
}

function ordinalSuffix(n) {
    if (n % 10 === 1 && n % 100 !== 11) return 'st';
    if (n % 10 === 2 && n % 100 !== 12) return 'nd';
    if (n % 10 === 3 && n % 100 !== 13) return 'rd';
    return 'th';
}

function currentFilters() {
    return {
        exercise_id: exerciseSelect.value || null,
        order_index: orderIndexSelect.value || null,
        workout_type: workoutTypeSelect.value || null,
        start_date: startDateInput.value || null,
        end_date: endDateInput.value || null,
        period: periodSelect.value || 'week',
    };
}

async function refreshAll() {
    const filters = currentFilters();
    await Promise.all([
        refreshExerciseCharts(filters),
        refreshVolumeChart(filters),
        refreshFrequencyChart(filters),
    ]);
}

async function refreshExerciseCharts(filters) {
    const exerciseCtx = document.getElementById('exercise-chart');
    const repsCtx = document.getElementById('reps-chart');

    if (!filters.exercise_id) {
        if (exerciseChart) { exerciseChart.destroy(); exerciseChart = null; }
        if (repsChart) { repsChart.destroy(); repsChart = null; }
        drawEmptyMessage(exerciseCtx, 'Pick a specific exercise above to see its progress.');
        drawEmptyMessage(repsCtx, 'Pick a specific exercise above to see reps-at-weight.');
        return;
    }

    const points = await fetch(`/api/progress/exercise/${filters.exercise_id}${qs({
        start_date: filters.start_date, end_date: filters.end_date,
        order_index: filters.order_index, workout_type: filters.workout_type,
    })}`).then((r) => r.json());

    // Top-set-per-session for the weight / 1RM trend.
    const bySession = new Map();
    points.forEach((p) => {
        const existing = bySession.get(p.session_id);
        if (!existing || p.est_1rm > existing.est_1rm) bySession.set(p.session_id, p);
    });
    const topSets = [...bySession.values()].sort((a, b) => a.date.localeCompare(b.date));

    if (exerciseChart) exerciseChart.destroy();
    exerciseChart = new Chart(exerciseCtx, {
        type: 'line',
        data: {
            labels: topSets.map((p) => p.date),
            datasets: [
                {
                    label: 'Top-set weight',
                    data: topSets.map((p) => p.total_weight),
                    borderColor: PALETTE[0],
                    backgroundColor: PALETTE[0],
                    tension: 0.2,
                },
                {
                    label: 'Estimated 1RM',
                    data: topSets.map((p) => Math.round(p.est_1rm * 10) / 10),
                    borderColor: PALETTE[1],
                    backgroundColor: PALETTE[1],
                    borderDash: [4, 3],
                    tension: 0.2,
                },
            ],
        },
        options: chartOptions('Weight (lb)'),
    });

    // Reps-at-weight: one series per distinct total_weight value.
    const byWeight = new Map();
    points.forEach((p) => {
        const key = p.total_weight;
        if (!byWeight.has(key)) byWeight.set(key, []);
        byWeight.get(key).push({ x: p.date, y: p.reps_full });
    });
    const weightKeys = [...byWeight.keys()].sort((a, b) => a - b);

    if (repsChart) repsChart.destroy();
    repsChart = new Chart(repsCtx, {
        type: 'scatter',
        data: {
            datasets: weightKeys.map((w, i) => ({
                label: `${w} lb`,
                data: byWeight.get(w).sort((a, b) => a.x.localeCompare(b.x)),
                showLine: true,
                borderColor: PALETTE[i % PALETTE.length],
                backgroundColor: PALETTE[i % PALETTE.length],
            })),
        },
        options: {
            ...chartOptions('Reps'),
            scales: {
                x: { type: 'category', ticks: { color: '#9aa3b2' }, grid: { color: '#2a2f3a' } },
                y: { ticks: { color: '#9aa3b2' }, grid: { color: '#2a2f3a' }, title: { display: true, text: 'Reps', color: '#9aa3b2' } },
            },
        },
    });
}

function drawEmptyMessage(canvas, message) {
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = '#9aa3b2';
    ctx.font = '14px sans-serif';
    ctx.fillText(message, 12, 24);
}

async function refreshVolumeChart(filters) {
    const points = await fetch(`/api/progress/volume${qs({
        exercise_id: filters.exercise_id, workout_type: filters.workout_type,
        start_date: filters.start_date, end_date: filters.end_date, period: filters.period,
    })}`).then((r) => r.json());

    const ctx = document.getElementById('volume-chart');
    if (volumeChart) volumeChart.destroy();
    volumeChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: points.map((p) => p.period_start),
            datasets: [{
                label: 'Volume (weight x reps)',
                data: points.map((p) => Math.round(p.total_volume)),
                backgroundColor: PALETTE[0],
            }],
        },
        options: chartOptions('Volume'),
    });
}

async function refreshFrequencyChart(filters) {
    const points = await fetch(`/api/progress/frequency${qs({
        workout_type: filters.workout_type, start_date: filters.start_date,
        end_date: filters.end_date, period: filters.period,
    })}`).then((r) => r.json());

    const ctx = document.getElementById('frequency-chart');
    if (frequencyChart) frequencyChart.destroy();
    frequencyChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: points.map((p) => p.period_start),
            datasets: [{
                label: 'Sessions',
                data: points.map((p) => p.session_count),
                backgroundColor: PALETTE[2],
            }],
        },
        options: chartOptions('Sessions'),
    });
}

function chartOptions(yLabel) {
    return {
        responsive: true,
        plugins: {
            legend: { labels: { color: '#e6e9ef' } },
        },
        scales: {
            x: { ticks: { color: '#9aa3b2' }, grid: { color: '#2a2f3a' } },
            y: {
                ticks: { color: '#9aa3b2' },
                grid: { color: '#2a2f3a' },
                title: { display: true, text: yLabel, color: '#9aa3b2' },
            },
        },
    };
}

async function loadPRTable() {
    const prs = await fetch('/api/progress/prs').then((r) => r.json());
    const tbody = document.querySelector('#pr-table tbody');
    tbody.innerHTML = prs.map((p) => `
        <tr>
            <td>${p.exercise_name}</td>
            <td>${p.total_weight}</td>
            <td>${p.reps_full}</td>
            <td>${Math.round(p.est_1rm * 10) / 10}</td>
            <td>${p.date}</td>
        </tr>
    `).join('');
}

[exerciseSelect, orderIndexSelect, workoutTypeSelect, startDateInput, endDateInput, periodSelect]
    .forEach((el) => el.addEventListener('change', refreshAll));

(async function init() {
    await loadFilterOptions();
    await refreshAll();
    await loadPRTable();
})();
