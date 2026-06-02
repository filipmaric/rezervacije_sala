import { API } from './api.js';
import { formatDateDDMMYYYY } from './util.js';

const DAY_NAMES = [
    'понедељак',
    'уторак',
    'среда',
    'четвртак',
    'петак',
    'субота',
    'недеља',
];

function formatCourseType(type) {
    const normalized = String(type || '').toLowerCase();
    const labels = {
        p: 'предавања',
        v: 'вежбе',
        k: 'колоквијум',
    };
    return labels[normalized] || type || '';
}

function formatHourRange(start, end) {
    const pad = (value) => String(value).padStart(2, '0');
    return `${pad(start)}:00 - ${pad(end)}:00`;
}

function formatSemesterLabel(semester) {
    return `${semester.name} (${formatDateDDMMYYYY(semester.start_date)} - ${formatDateDDMMYYYY(semester.end_date)})`;
}

function clearNode(node) {
    node.textContent = '';
}

function escapeCsvValue(value) {
    const text = String(value ?? '');
    if (/[",\n;]/.test(text)) {
        return `"${text.replace(/"/g, '""')}"`;
    }
    return text;
}

function buildAttendanceSummaryCsv(summaryEntries) {
    const rows = [
        ['Корисничко име', 'Име', 'Број присустава'],
        ...summaryEntries.map((entry) => [entry.username, '', entry.count]),
    ];
    return '\ufeff' + rows.map((row) => row.map(escapeCsvValue).join(';')).join('\n');
}

function downloadTextFile(filename, content, mimeType = 'text/csv;charset=utf-8') {
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
}

function renderEmptyMessage(container, message) {
    clearNode(container);
    const p = document.createElement('p');
    p.textContent = message;
    container.appendChild(p);
}

function createAttendanceListItem(student) {
    const li = document.createElement('li');
    li.textContent = `${student.username} (${student.created_at})`;
    return li;
}

function ensureAttendanceDialog() {
    let dialog = document.getElementById('attendance-dialog');
    if (dialog) {
        return dialog;
    }

    dialog = document.createElement('dialog');
    dialog.id = 'attendance-dialog';
    dialog.className = 'attendance-dialog';

    const header = document.createElement('div');
    header.className = 'attendance-dialog-header';

    const titleWrap = document.createElement('div');
    titleWrap.className = 'attendance-dialog-title-wrap';

    const title = document.createElement('h3');
    title.id = 'attendance-dialog-title';
    titleWrap.appendChild(title);

    const subtitle = document.createElement('p');
    subtitle.id = 'attendance-dialog-subtitle';
    subtitle.className = 'attendance-dialog-subtitle';
    titleWrap.appendChild(subtitle);

    header.appendChild(titleWrap);

    const actions = document.createElement('div');
    actions.className = 'attendance-dialog-actions';

    const closeButton = document.createElement('button');
    closeButton.type = 'button';
    closeButton.className = 'attendance-dialog-close';
    closeButton.textContent = 'Затвори';
    closeButton.addEventListener('click', () => dialog.close());
    actions.appendChild(closeButton);

    const downloadButton = document.createElement('button');
    downloadButton.type = 'button';
    downloadButton.className = 'attendance-dialog-download';
    downloadButton.textContent = 'Преузми CSV';
    downloadButton.hidden = true;
    actions.appendChild(downloadButton);

    header.appendChild(actions);

    const body = document.createElement('div');
    body.id = 'attendance-dialog-body';

    dialog.appendChild(header);
    dialog.appendChild(body);
    document.body.appendChild(dialog);
    return dialog;
}

function openAttendanceDialog(date, students) {
    const dialog = ensureAttendanceDialog();
    const title = dialog.querySelector('#attendance-dialog-title');
    const subtitle = dialog.querySelector('#attendance-dialog-subtitle');
    const body = dialog.querySelector('#attendance-dialog-body');
    const downloadButton = dialog.querySelector('.attendance-dialog-download');

    title.textContent = `Присутност за ${formatDateDDMMYYYY(date)}`;
    subtitle.textContent = '';
    downloadButton.hidden = students.length === 0;
    if (students.length > 0) {
        downloadButton.textContent = 'Преузми CSV';
        downloadButton.onclick = () => {
            const csv = buildAttendanceSummaryCsv(
                students.map((student) => ({
                    username: student.username,
                    count: 1,
                }))
            );
            const safeDate = String(date).replace(/[^0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'datum';
            downloadTextFile(`prisustvo_${safeDate}.csv`, csv);
        };
    } else {
        downloadButton.onclick = null;
    }
    clearNode(body);

    if (!students.length) {
        const p = document.createElement('p');
        p.textContent = 'Нема пријављених студената.';
        body.appendChild(p);
    } else {
        const list = document.createElement('ol');
        students.forEach((student) => {
            list.appendChild(createAttendanceListItem(student));
        });
        body.appendChild(list);
    }

    if (typeof dialog.showModal === 'function') {
        dialog.showModal();
    } else {
        dialog.setAttribute('open', 'open');
    }
}

function openAttendanceSummaryDialog(courseLabel, sessionLabel, summaryEntries) {
    const dialog = ensureAttendanceDialog();
    const title = dialog.querySelector('#attendance-dialog-title');
    const subtitle = dialog.querySelector('#attendance-dialog-subtitle');
    const body = dialog.querySelector('#attendance-dialog-body');
    const downloadButton = dialog.querySelector('.attendance-dialog-download');

    title.textContent = 'Сажетак присуства';
    subtitle.textContent = `${courseLabel} - ${sessionLabel}`;
    downloadButton.hidden = summaryEntries.length === 0;
    if (summaryEntries.length > 0) {
        downloadButton.onclick = () => {
            const csv = buildAttendanceSummaryCsv(summaryEntries);
            const safeCourse = courseLabel.replace(/[^\p{L}\p{N}]+/gu, '_').replace(/^_+|_+$/g, '') || 'kurs';
            const safeSession = sessionLabel.replace(/[^\p{L}\p{N}]+/gu, '_').replace(/^_+|_+$/g, '') || 'termin';
            downloadTextFile(`sazetak_prisustva_${safeCourse}_${safeSession}.csv`, csv);
        };
    } else {
        downloadButton.onclick = null;
    }
    clearNode(body);

    if (!summaryEntries.length) {
        const p = document.createElement('p');
        p.textContent = 'Нема студената који су присуствовали бар једном термину.';
        body.appendChild(p);
    } else {
        const table = document.createElement('table');
        const thead = document.createElement('thead');
        const headerRow = document.createElement('tr');
        ['Корисничко име', 'Име', 'Број присустава'].forEach((label) => {
            const th = document.createElement('th');
            th.textContent = label;
            headerRow.appendChild(th);
        });
        thead.appendChild(headerRow);
        table.appendChild(thead);

        const tbody = document.createElement('tbody');
        summaryEntries.forEach((entry) => {
            const tr = document.createElement('tr');
            [
                entry.username,
                '',
                entry.count,
            ].forEach((value) => {
                const td = document.createElement('td');
                td.textContent = String(value);
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        body.appendChild(table);
    }

    if (typeof dialog.showModal === 'function') {
        dialog.showModal();
    } else {
        dialog.setAttribute('open', 'open');
    }
}

function renderPersonalReservations(container, reservations) {
    if (!reservations.length) {
        renderEmptyMessage(container, 'Нема личних резервација у изабраном семестру.');
        return;
    }

    const table = document.createElement('table');
    const thead = document.createElement('thead');
    const headerRow = document.createElement('tr');
    ['Датум', 'Сала', 'Време', 'Опис'].forEach((label) => {
        const th = document.createElement('th');
        th.textContent = label;
        headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);
    table.appendChild(thead);

    const tbody = document.createElement('tbody');
    reservations.forEach((reservation) => {
        const tr = document.createElement('tr');

        const cells = [
            formatDateDDMMYYYY(reservation.date),
            reservation.room_name,
            formatHourRange(reservation.start_slot, reservation.end_slot),
            reservation.description || '',
        ];

        cells.forEach((value) => {
            const td = document.createElement('td');
            td.textContent = value;
            tr.appendChild(td);
        });

        tbody.appendChild(tr);
    });
    table.appendChild(tbody);

    clearNode(container);
    container.appendChild(table);
}

async function renderPersonalAttendanceSummary(container, reservations) {
    const summary = document.createElement('section');
    summary.className = 'attendance-summary';

    const details = document.createElement('details');
    details.className = 'attendance-collapsible';

    const summaryTitle = document.createElement('summary');
    summaryTitle.textContent = 'Присутност по личној резервацији';
    details.appendChild(summaryTitle);

    const list = document.createElement('ul');
    list.className = 'attendance-summary-list';
    details.appendChild(list);

    const entries = await Promise.all(reservations.map(async (reservation) => {
        const data = await API.getAttendanceRoster('reservation', reservation.id, reservation.date);
        return {
            reservation,
            students: data.students || [],
        };
    }));

    entries.forEach((entry) => {
        const item = document.createElement('li');
        item.className = 'attendance-summary-item';

        const dateLabel = document.createElement('span');
        dateLabel.className = 'attendance-summary-date';
        dateLabel.textContent = formatDateDDMMYYYY(entry.reservation.date);
        item.appendChild(dateLabel);

        const roomLabel = document.createElement('span');
        roomLabel.className = 'attendance-summary-room';
        roomLabel.textContent = entry.reservation.room_name;
        item.appendChild(roomLabel);

        const countLabel = document.createElement('span');
        countLabel.className = 'attendance-summary-count';
        countLabel.textContent = `${entry.students.length} присутних`;
        item.appendChild(countLabel);

        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'attendance-summary-btn';
        button.textContent = 'Погледај све';
        button.addEventListener('click', () => openAttendanceDialog(entry.reservation.date, entry.students));
        item.appendChild(button);

        list.appendChild(item);
    });

    summary.appendChild(details);
    return summary;
}

async function renderCourseSessions(container, courses) {
    if (!courses.length) {
        renderEmptyMessage(container, 'Нема предмета у изабраном семестру.');
        return;
    }

    clearNode(container);

    for (const course of courses) {
        const card = document.createElement('article');
        card.className = 'course-card';

        const title = document.createElement('h3');
        title.textContent = course.course_name;
        card.appendChild(title);

        const table = document.createElement('table');
        const thead = document.createElement('thead');
        const headerRow = document.createElement('tr');
        ['Дан', 'Сала', 'Време', 'Тип', 'Наставник', 'Групе'].forEach((label) => {
            const th = document.createElement('th');
            th.textContent = label;
            headerRow.appendChild(th);
        });
        thead.appendChild(headerRow);
        table.appendChild(thead);

        const tbody = document.createElement('tbody');
        course.sessions.forEach((session) => {
            const tr = document.createElement('tr');
            const cells = [
                DAY_NAMES[session.day_of_week] || '',
                session.room_name,
                formatHourRange(session.start_slot, session.end_slot),
                formatCourseType(session.course_type),
                session.teacher_name,
                session.groups.length ? session.groups.join(', ') : '',
            ];

            cells.forEach((value) => {
                const td = document.createElement('td');
                td.textContent = value;
                tr.appendChild(td);
            });

            tbody.appendChild(tr);
        });

        table.appendChild(tbody);
        card.appendChild(table);

        const details = document.createElement('details');
        details.className = 'attendance-collapsible attendance-summary';

        const summaryTitle = document.createElement('summary');
        details.appendChild(summaryTitle);

        const courseLabel = course.course_name;

        const sessionBlocks = await Promise.all(course.sessions.map(async (session) => {
            const block = document.createElement('section');
            block.className = 'attendance-session-block';

            const heading = document.createElement('h4');
            const sessionLabel = [
                DAY_NAMES[session.day_of_week] || '',
                formatHourRange(session.start_slot, session.end_slot),
                session.room_name,
                formatCourseType(session.course_type),
            ].filter(Boolean).join(' · ');
            heading.textContent = sessionLabel;
            block.appendChild(heading);

            const actions = document.createElement('div');
            actions.className = 'attendance-session-actions';

            const summaryButton = document.createElement('button');
            summaryButton.type = 'button';
            summaryButton.className = 'attendance-summary-btn';
            summaryButton.textContent = 'Сажетак присуства';
            summaryButton.addEventListener('click', () => {
                const summaryEntries = Array.from(attendanceTotals.values()).sort((a, b) => {
                    if (b.count !== a.count) return b.count - a.count;
                    return a.username.localeCompare(b.username);
                });
                openAttendanceSummaryDialog(courseLabel, sessionLabel, summaryEntries);
            });
            actions.appendChild(summaryButton);
            block.appendChild(actions);

            const instances = session.instances || [];
            if (!instances.length) {
                const empty = document.createElement('p');
                empty.textContent = 'Нема одржаних термина у изабраном семестру.';
                block.appendChild(empty);
                return block;
            }

            const list = document.createElement('ul');
            list.className = 'attendance-summary-list';
            block.appendChild(list);

            const entries = await Promise.all(instances.map(async (date) => {
                const data = await API.getAttendanceRoster('weekly', session.weekly_session_id, date);
                return {
                    date,
                    students: data.students || [],
                };
            }));

            const attendanceTotals = new Map();
            entries.forEach((entry) => {
                entry.students.forEach((student) => {
                    const key = student.username;
                    const current = attendanceTotals.get(key) || {
                        username: student.username,
                        count: 0,
                    };
                    current.count += 1;
                    attendanceTotals.set(key, current);
                });
            });

            entries.forEach((entry) => {
                const item = document.createElement('li');
                item.className = 'attendance-summary-item';

                const dateLabel = document.createElement('span');
                dateLabel.className = 'attendance-summary-date';
                dateLabel.textContent = formatDateDDMMYYYY(entry.date);
                item.appendChild(dateLabel);

                const countLabel = document.createElement('span');
                countLabel.className = 'attendance-summary-count';
                countLabel.textContent = `${entry.students.length} присутних`;
                item.appendChild(countLabel);

                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'attendance-summary-btn';
                button.textContent = 'Погледај све';
                button.addEventListener('click', () => openAttendanceDialog(entry.date, entry.students));
                item.appendChild(button);

                list.appendChild(item);
            });

            return block;
        }));

        if (!sessionBlocks.length) {
            const empty = document.createElement('p');
            empty.textContent = 'Нема одржаних термина у изабраном семестру.';
            details.appendChild(empty);
        } else {
            sessionBlocks.forEach((block) => details.appendChild(block));
        }

        details.querySelector('summary').textContent = 'Присутност по термину';
        card.appendChild(details);
        container.appendChild(card);
    }
}

function populateSemesterSelect(select, semesters, selectedId) {
    clearNode(select);
    semesters.forEach((semester) => {
        const option = document.createElement('option');
        option.value = semester.id;
        option.textContent = formatSemesterLabel(semester);
        if (Number(semester.id) === Number(selectedId)) {
            option.selected = true;
        }
        select.appendChild(option);
    });
}

async function loadReservations(selectedSemesterId = null) {
    const data = await API.getMyReservations(selectedSemesterId);
    const select = document.getElementById('semester-select');
    const personalContainer = document.getElementById('personal-reservations');
    const courseContainer = document.getElementById('course-reservations');

    populateSemesterSelect(
        select,
        data.semesters || [],
        data.selected_semester ? data.selected_semester.id : null,
    );

    if (data.selected_semester) {
        document.getElementById('page-message').textContent = `Преглед за семестар: ${data.selected_semester.name}`;
    } else {
        document.getElementById('page-message').textContent = 'Нема доступних семестара.';
    }

    renderPersonalReservations(personalContainer, data.personal_reservations || []);
    const oldPersonalSummary = document.getElementById('personal-attendance-summary');
    if (oldPersonalSummary) {
        oldPersonalSummary.remove();
    }
    if ((data.personal_reservations || []).length) {
        const summary = await renderPersonalAttendanceSummary(personalContainer.parentElement, data.personal_reservations || []);
        summary.id = 'personal-attendance-summary';
        personalContainer.parentElement.appendChild(summary);
    }
    await renderCourseSessions(courseContainer, data.courses || []);
}

const App = {
    async init() {
        const whoami = await API.whoami();
        if (!whoami.logged_in) {
            document.getElementById('page-message').textContent = 'Морате бити пријављени да бисте видели своје резервације.';
            document.getElementById('reservations-page').style.display = 'none';
            return;
        }

        document.getElementById('reservations-page').style.display = 'block';

        const select = document.getElementById('semester-select');
        select.addEventListener('change', async () => {
            await loadReservations(select.value);
        });

        await loadReservations();
    },
};

document.addEventListener('DOMContentLoaded', () => App.init());
