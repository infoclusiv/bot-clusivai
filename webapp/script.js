const tg = window.Telegram.WebApp;
tg.expand();

// Extract params from URL
const urlParams = new URLSearchParams(window.location.search);
const userId = urlParams.get('user_id') || (tg.initDataUnsafe?.user?.id ? String(tg.initDataUnsafe.user.id) : null);
const mode = urlParams.get('mode') || 'edit'; // 'edit', 'calendar', or 'notes'
const reminderId = urlParams.get('id');
const initialMessage = urlParams.get('message') || '';
const initialDateTime = urlParams.get('date') || '';
const initialRecurrence = urlParams.get('recurrence') || null;

// DOM Elements - Views
const tabNav = document.getElementById('tab-nav');
const calendarView = document.getElementById('calendar-view');
const editView = document.getElementById('edit-view');
const notesView = document.getElementById('notes-view');

// DOM Elements - Tabs
const tabButtons = document.querySelectorAll('.tab-btn');

// DOM Elements - Calendar
const currentMonthYearHeader = document.getElementById('current-month-year');
const prevMonthBtn = document.getElementById('prev-month');
const nextMonthBtn = document.getElementById('next-month');
const calendarGrid = document.getElementById('calendar-grid');
const dayDetails = document.getElementById('day-details');
const selectedDayLabel = document.getElementById('selected-day-label');
const remindersList = document.getElementById('reminders-list');

// DOM Elements - Notes
const notesList = document.getElementById('notes-list');
const notesEmpty = document.getElementById('notes-empty');

// DOM Elements - Edit Form
const editTitle = document.getElementById('edit-title');
const editSubtitle = document.getElementById('edit-subtitle');
const messageInput = document.getElementById('message');
const dateInput = document.getElementById('date');
const timeInput = document.getElementById('time');
const dateGroup = document.getElementById('date-group');
const recurrenceGroup = document.getElementById('recurrence-group');
const dayCheckboxes = document.querySelectorAll('.day-pill input');
const saveButton = document.getElementById('save-button');
const deleteButton = document.getElementById('delete-button');
const cancelButton = document.getElementById('cancel-button');
const errorMessage = document.getElementById('error-message');

// State
let currentDate = new Date();
let reminders = [];
let notes = [];
let currentReminderId = reminderId;
let currentRecurrence = initialRecurrence;
let activeTab = 'calendar';

// ==================== INITIALIZATION ====================
function init() {
    if (mode === 'calendar' || mode === 'notes') {
        // Tab-based views
        tabNav.style.display = 'flex';
        if (mode === 'notes') {
            switchTab('notes');
        } else {
            switchTab('calendar');
        }
    } else {
        // Direct edit mode (from reminder alert)
        tabNav.style.display = 'none';
        showEditForm(initialMessage, initialDateTime, reminderId, initialRecurrence);
    }
}

// ==================== TAB NAVIGATION ====================
tabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
        switchTab(btn.dataset.tab);
    });
});

function switchTab(tab) {
    activeTab = tab;

    // Update tab button styles
    tabButtons.forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tab);
    });

    // Toggle views
    calendarView.style.display = tab === 'calendar' ? 'block' : 'none';
    notesView.style.display = tab === 'notes' ? 'block' : 'none';
    editView.style.display = 'none';

    if (tab === 'calendar') {
        showCalendar();
    } else if (tab === 'notes') {
        showNotes();
    }
}

// ==================== CALENDAR VIEW ====================
async function showCalendar() {
    calendarView.style.display = 'block';
    editView.style.display = 'none';
    notesView.style.display = 'none';
    tg.MainButton.hide();

    await fetchReminders();
    renderCalendar();
}

function showEditForm(message = '', dateTime = '', id = null, recurrence = null) {
    calendarView.style.display = 'none';
    editView.style.display = 'block';
    notesView.style.display = 'none';

    currentReminderId = id;
    currentRecurrence = recurrence;
    messageInput.value = message;

    if (recurrence) {
        editTitle.textContent = "Editar Recordatorio";
        editSubtitle.textContent = "Recordatorio Recurrente (ID: " + id + ")";
        saveButton.textContent = "Guardar Cambios";
        dateGroup.style.display = 'none';
        recurrenceGroup.style.display = 'block';
        deleteButton.style.display = 'block';
        parseRRULE(recurrence);
    } else {
        editTitle.textContent = "Reprogramar";
        editSubtitle.textContent = id ? "ID: " + id : "Nuevo Recordatorio";
        saveButton.textContent = id ? "Reprogramar" : "Crear";
        dateGroup.style.display = 'block';
        recurrenceGroup.style.display = 'none';
        deleteButton.style.display = id ? 'block' : 'none';
    }

    if (dateTime) {
        const [date, time] = dateTime.split(' ');
        dateInput.value = date;
        timeInput.value = time.substring(0, 5);
    } else {
        const now = new Date();
        dateInput.value = now.toISOString().split('T')[0];
        timeInput.value = now.toTimeString().substring(0, 5);
    }
}

function parseRRULE(rrule) {
    dayCheckboxes.forEach(cb => cb.checked = false);
    const bydayMatch = rrule.match(/BYDAY=([^;]+)/);
    if (bydayMatch) {
        const days = bydayMatch[1].split(',');
        days.forEach(day => {
            const checkbox = document.querySelector(`.day-pill input[value="${day}"]`);
            if (checkbox) checkbox.checked = true;
        });
    }
}

function buildRRULE() {
    const selectedDays = Array.from(dayCheckboxes)
        .filter(cb => cb.checked)
        .map(cb => cb.value);

    if (selectedDays.length === 0) return null;
    return `FREQ=WEEKLY;BYDAY=${selectedDays.join(',')}`;
}

async function fetchReminders() {
    if (!userId) return;
    try {
        const response = await fetch(`/api/reminders?user_id=${userId}`);
        const data = await response.json();
        if (data.success) {
            reminders = data.reminders;
        }
    } catch (err) {
        console.error('Error fetching reminders:', err);
    }
}

function renderCalendar() {
    calendarGrid.innerHTML = '';
    const year = currentDate.getFullYear();
    const month = currentDate.getMonth();

    const monthNames = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"];
    currentMonthYearHeader.textContent = `${monthNames[month]} ${year}`;

    const firstDayOfMonth = (new Date(year, month, 1).getDay() + 6) % 7;
    const daysInMonth = new Date(year, month + 1, 0).getDate();

    for (let i = 0; i < firstDayOfMonth; i++) {
        const emptyDiv = document.createElement('div');
        emptyDiv.classList.add('calendar-day', 'empty');
        calendarGrid.appendChild(emptyDiv);
    }

    const today = new Date();
    const todayStr = today.toISOString().split('T')[0];

    for (let day = 1; day <= daysInMonth; day++) {
        const dayDiv = document.createElement('div');
        dayDiv.classList.add('calendar-day');
        dayDiv.textContent = day;

        const dateStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;

        if (dateStr === todayStr) {
            dayDiv.classList.add('today');
        }

        const dayReminders = reminders.filter(r => r.date.startsWith(dateStr));
        if (dayReminders.length > 0) {
            const dot = document.createElement('div');
            dot.classList.add('dot');
            dayDiv.appendChild(dot);
        }

        dayDiv.addEventListener('click', () => {
            selectDay(dayDiv, day, dateStr, dayReminders);
        });

        calendarGrid.appendChild(dayDiv);
    }
}

function selectDay(element, day, dateStr, dayReminders) {
    document.querySelectorAll('.calendar-day').forEach(d => d.classList.remove('selected'));
    element.classList.add('selected');

    if (dayReminders.length > 0) {
        dayDetails.style.display = 'block';
        selectedDayLabel.textContent = `Recordatorios para el ${day}`;
        remindersList.innerHTML = '';

        dayReminders.forEach(r => {
            const item = document.createElement('div');
            item.classList.add('reminder-item');

            const time = r.date.split(' ')[1].substring(0, 5);

            item.innerHTML = `
                <div class="reminder-info">
                    <span class="reminder-time">${time}</span>
                    <span class="reminder-text">${r.message}</span>
                </div>
                <div class="reminder-arrow">&rarr;</div>
            `;

            item.addEventListener('click', () => {
                showEditForm(r.message, r.date, r.id, r.recurrence);
            });

            remindersList.appendChild(item);
        });
    } else {
        dayDetails.style.display = 'none';
    }
}

// ==================== NOTES VIEW ====================
async function showNotes() {
    notesView.style.display = 'block';
    calendarView.style.display = 'none';
    editView.style.display = 'none';
    tg.MainButton.hide();

    await fetchNotes();
    renderNotes();
}

async function fetchNotes() {
    if (!userId) return;
    try {
        const response = await fetch(`/api/notes?user_id=${userId}`);
        const data = await response.json();
        if (data.success) {
            notes = data.notes;
        }
    } catch (err) {
        console.error('Error fetching notes:', err);
    }
}

function renderNotes() {
    notesList.innerHTML = '';

    if (notes.length === 0) {
        notesEmpty.style.display = 'block';
        notesList.style.display = 'none';
        return;
    }

    notesEmpty.style.display = 'none';
    notesList.style.display = 'flex';

    notes.forEach(note => {
        const card = document.createElement('div');
        card.classList.add('note-card');
        card.dataset.noteId = note.id;

        const dateStr = formatNoteDate(note.created_at);

        card.innerHTML = `
            <div class="note-content">${escapeHtml(note.content)}</div>
            <div class="note-meta">
                <span class="note-date">${dateStr}</span>
                <div class="note-actions">
                    <button class="note-action-btn edit" onclick="editNote(${note.id}, this)">✏️ Editar</button>
                    <button class="note-action-btn delete" onclick="deleteNote(${note.id}, this)">🗑️</button>
                </div>
            </div>
        `;

        notesList.appendChild(card);
    });
}

function formatNoteDate(dateStr) {
    if (!dateStr) return '';
    try {
        const date = new Date(dateStr);
        const options = { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' };
        return date.toLocaleDateString('es-CO', options);
    } catch {
        return dateStr;
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

async function editNote(noteId, btnElement) {
    const card = btnElement.closest('.note-card');
    const currentContent = card.querySelector('.note-content').textContent;

    // Create modal overlay
    const overlay = document.createElement('div');
    overlay.classList.add('note-edit-overlay');
    overlay.innerHTML = `
        <div class="note-edit-modal">
            <h3>✏️ Editar Nota</h3>
            <textarea id="edit-note-content">${escapeHtml(currentContent)}</textarea>
            <div class="modal-buttons">
                <button class="secondary" id="modal-cancel">Cancelar</button>
                <button id="modal-save">Guardar</button>
            </div>
        </div>
    `;

    document.body.appendChild(overlay);

    const textarea = overlay.querySelector('#edit-note-content');
    textarea.focus();
    textarea.setSelectionRange(textarea.value.length, textarea.value.length);

    // Cancel
    overlay.querySelector('#modal-cancel').addEventListener('click', () => {
        overlay.remove();
    });

    // Click outside to cancel
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) overlay.remove();
    });

    // Save
    overlay.querySelector('#modal-save').addEventListener('click', async () => {
        const newContent = textarea.value.trim();
        if (!newContent) return;

        const saveBtn = overlay.querySelector('#modal-save');
        saveBtn.disabled = true;
        saveBtn.textContent = 'Guardando...';

        try {
            const response = await fetch(`/api/notes/${noteId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content: newContent })
            });

            const result = await response.json();
            if (result.success) {
                overlay.remove();
                await fetchNotes();
                renderNotes();
            } else {
                alert('Error al guardar: ' + (result.error || 'Desconocido'));
                saveBtn.disabled = false;
                saveBtn.textContent = 'Guardar';
            }
        } catch (err) {
            alert('Error de conexión.');
            saveBtn.disabled = false;
            saveBtn.textContent = 'Guardar';
        }
    });
}

async function deleteNote(noteId, btnElement) {
    tg.showConfirm("¿Estás seguro de que deseas eliminar esta nota?", async (confirmed) => {
        if (confirmed) {
            btnElement.disabled = true;
            btnElement.textContent = '...';

            try {
                const response = await fetch(`/api/notes/${noteId}`, {
                    method: 'DELETE'
                });

                const result = await response.json();
                if (result.success) {
                    // Animate removal
                    const card = btnElement.closest('.note-card');
                    card.style.transition = 'all 0.3s ease';
                    card.style.opacity = '0';
                    card.style.transform = 'translateX(50px)';
                    setTimeout(async () => {
                        await fetchNotes();
                        renderNotes();
                    }, 300);
                } else {
                    alert('Error al eliminar: ' + (result.error || 'Desconocido'));
                    btnElement.disabled = false;
                    btnElement.textContent = '🗑️';
                }
            } catch (err) {
                alert('Error de conexión.');
                btnElement.disabled = false;
                btnElement.textContent = '🗑️';
            }
        }
    });
}

// ==================== CALENDAR EVENT LISTENERS ====================
prevMonthBtn.addEventListener('click', () => {
    currentDate.setMonth(currentDate.getMonth() - 1);
    renderCalendar();
    dayDetails.style.display = 'none';
});

nextMonthBtn.addEventListener('click', () => {
    currentDate.setMonth(currentDate.getMonth() + 1);
    renderCalendar();
    dayDetails.style.display = 'none';
});

saveButton.addEventListener('click', async () => {
    errorMessage.style.display = 'none';

    const message = messageInput.value.trim();
    const time = timeInput.value;
    let date = dateInput.value;
    let recurrence = currentRecurrence;

    if (currentRecurrence) {
        recurrence = buildRRULE();
        if (!recurrence) {
            showError('Por favor, selecciona al menos un día.');
            return;
        }
        date = new Date().toISOString().split('T')[0];
    } else {
        if (!date) {
            showError('Por favor, selecciona una fecha.');
            return;
        }
    }

    if (!message || !time) {
        showError('Por favor, completa todos los campos.');
        return;
    }

    saveButton.disabled = true;
    saveButton.textContent = 'Guardando...';

    const data = {
        user_id: userId,
        id: currentReminderId,
        message: message,
        date: `${date} ${time}:00`,
        recurrence: recurrence
    };

    try {
        const response = await fetch('/api/reprogram', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });

        const result = await response.json();
        if (result.success) {
            if (mode === 'calendar' || mode === 'notes') {
                showCalendar();
                // Re-show tab nav in case we were in edit mode
                tabNav.style.display = 'flex';
                switchTab('calendar');
            } else {
                tg.close();
            }
        } else {
            showError('Error al guardar: ' + (result.error || 'Desconocido'));
        }
    } catch (err) {
        showError('Error de conexión con el servidor.');
    } finally {
        saveButton.disabled = false;
        saveButton.textContent = currentRecurrence ? "Guardar Cambios" : (currentReminderId ? "Reprogramar" : "Crear");
    }
});

deleteButton.addEventListener('click', () => {
    tg.showConfirm("¿Estás seguro de que deseas eliminar este recordatorio?", async (confirmed) => {
        if (confirmed) {
            deleteButton.disabled = true;
            deleteButton.textContent = "Eliminando...";

            try {
                const response = await fetch('/api/delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ user_id: userId, id: currentReminderId })
                });

                const result = await response.json();
                if (result.success) {
                    if (mode === 'calendar' || mode === 'notes') {
                        tabNav.style.display = 'flex';
                        switchTab('calendar');
                    } else {
                        tg.close();
                    }
                } else {
                    showError('Error al eliminar: ' + (result.error || 'Desconocido'));
                    deleteButton.disabled = false;
                    deleteButton.textContent = "Eliminar Recordatorio";
                }
            } catch (err) {
                showError('Error de conexión.');
                deleteButton.disabled = false;
                deleteButton.textContent = "Eliminar Recordatorio";
            }
        }
    });
});

cancelButton.addEventListener('click', () => {
    if ((mode === 'calendar' || mode === 'notes') && editView.style.display === 'block') {
        tabNav.style.display = 'flex';
        switchTab(activeTab);
    } else {
        tg.close();
    }
});

function showError(msg) {
    errorMessage.textContent = msg;
    errorMessage.style.display = 'block';
}

init();
