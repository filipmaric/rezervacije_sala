import { API } from './api.js';
import { formatDateDDMMYYYY } from './util.js';

let pollHandle = null;
let activeSpotCheck = null;

function buildJoinUrl(kind, eventId, eventDate, token) {
    const basePath = window.APP_CONFIG?.BASE_PATH || '';
    return `${window.location.origin}${basePath}/attendance/${kind}/${eventId}/${eventDate}/join/${token}`;
}

function buildQrUrl(joinUrl) {
    return `https://api.qrserver.com/v1/create-qr-code/?size=240x240&data=${encodeURIComponent(joinUrl)}`;
}

function formatEventTitle(event) {
    if (event.course_name) {
        return event.course_name;
    }
    return event.description || 'Резервација';
}

function formatStudentLabel(student) {
    return student?.student_label || 'Непознато';
}

function formatAttendanceSource(student) {
    const source = String(student?.registration_source || '').toLowerCase();
    return source === 'android' ? 'android' : 'web';
}

function handleAttendanceError(err) {
    const errorCode = err.data?.error_code || '';
    const errorText = err.data?.error || err.message || '';
    if (errorCode === 'attendance_outside_class_time') {
        return { handled: true, type: 'outside_class', message: errorText };
    }
    if (errorCode === 'attendance_attempt_expired') {
        return { handled: true, type: 'session_expired', message: errorText };
    }
    if (errorCode === 'attendance_attempt_blocked') {
        return { handled: true, type: 'session_blocked', message: errorText };
    }
    if (errorCode === 'attendance_geofence_blocked' || errorCode === 'attendance_location_missing' || errorCode === 'attendance_location_required') {
        return { handled: true, type: 'geofence', message: errorText };
    }
    return { handled: false, message: errorText };
}

function renderEventInfo(root, event) {
    const info = document.createElement('div');
    info.className = 'attendance-panel';
    const groups = Array.isArray(event.groups)
        ? event.groups
        : (typeof event.groups === 'string' && event.groups.length ? event.groups.split(',') : []);

    const title = document.createElement('h2');
    title.textContent = formatEventTitle(event);
    info.appendChild(title);

    const details = document.createElement('p');
    const parts = [
        `Датум: ${formatDateDDMMYYYY(event.event_date || event.reservation_date)}`,
        `Време: ${String(event.start_slot).padStart(2, '0')}:00 - ${String(event.end_slot).padStart(2, '0')}:00`,
        `Сала: ${event.room_name}`,
    ];
    if (event.teacher_name) parts.push(`Наставник: ${event.teacher_name}`);
    if (groups.length) parts.push(`Групе: ${groups.join(', ')}`);
    if (event.is_canceled) parts.push('Час је отказан.');
    details.textContent = parts.join(' | ');
    info.appendChild(details);

    root.appendChild(info);
}

function renderQr(root, joinUrl) {
    const box = document.createElement('div');
    box.className = 'attendance-qr-box';

    const link = document.createElement('a');
    link.href = joinUrl;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.title = 'Отвори QR линк';

    const img = document.createElement('img');
    img.alt = 'QR код за пријаву присуства';
    img.src = buildQrUrl(joinUrl);
    link.appendChild(img);
    box.appendChild(link);

    root.appendChild(box);
}

function renderChallenge(root, challenge, event) {
    let box = root.querySelector('.attendance-challenge-box');
    if (!box) {
        box = document.createElement('div');
        box.className = 'attendance-challenge-box';
        root.appendChild(box);
    }

    box.innerHTML = '';

    const code = document.createElement('div');
    code.className = 'attendance-code';
    code.textContent = String(challenge.current_code);
    box.appendChild(code);

    const countdown = document.createElement('p');
    countdown.className = 'attendance-countdown';
    countdown.textContent = `Преостало: ${challenge.expires_in} секунди`;
    box.appendChild(countdown);
}

function renderGeofenceControl(root, data, kind, eventId, eventDate, pageRoot) {
    const box = document.createElement('section');
    box.className = 'attendance-panel attendance-geofence-panel';

    if (!data.attendance_geofence_available) {
        const warning = document.createElement('p');
        warning.className = 'attendance-geofence-warning';
        warning.textContent = data.attendance_geofence_warning || 'Локација за ову учионицу није подешена.';
        box.appendChild(warning);
        root.appendChild(box);
        return;
    }

    const label = document.createElement('label');
    label.className = 'attendance-geofence-toggle';

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = Boolean(data.attendance_geofence_enabled);

    const text = document.createElement('span');
    text.textContent = 'Провери локацију';

    const status = document.createElement('span');
    status.className = `attendance-geofence-status ${
        data.attendance_geofence_enabled ? 'attendance-geofence-status-on' : 'attendance-geofence-status-off'
    }`;
    status.textContent = data.attendance_geofence_enabled ? 'укључено' : 'искључено';

    label.appendChild(checkbox);
    label.appendChild(text);
    box.appendChild(label);
    box.appendChild(status);

    checkbox.addEventListener('change', async () => {
        checkbox.disabled = true;
        try {
            await API.setAttendanceGeofence(kind, eventId, eventDate, checkbox.checked);
            await refresh(pageRoot);
        } catch (error) {
            checkbox.checked = !checkbox.checked;
            window.alert(error.data?.error || error.message || 'Грешка при чувању провере локације.');
        } finally {
            checkbox.disabled = false;
        }
    });

    root.appendChild(box);
}

function renderRoster(root, students) {
    const section = document.createElement('section');
    section.className = 'attendance-roster';

    const title = document.createElement('h3');
    title.textContent = 'Пријављени студенти';
    section.appendChild(title);

    if (!students.length) {
        const p = document.createElement('p');
        p.textContent = 'Нема пријављених студената.';
        section.appendChild(p);
    } else {
        const list = document.createElement('ol');
        students.forEach((student) => {
            const li = document.createElement('li');
            const label = document.createElement('span');
            label.textContent = formatStudentLabel(student);
            li.appendChild(label);

            const source = document.createElement('span');
            const normalizedSource = formatAttendanceSource(student);
            source.className = `attendance-source-badge attendance-source-${normalizedSource}`;
            source.textContent = normalizedSource;
            li.appendChild(source);
            list.appendChild(li);
        });
        section.appendChild(list);
    }

    root.appendChild(section);
}

function ensureTeacherLayout(root) {
    let layout = root.querySelector('.attendance-teacher-layout');
    if (layout) {
        return {
            layout,
            left: layout.querySelector('.attendance-teacher-column-left'),
            right: layout.querySelector('.attendance-teacher-column-right'),
        };
    }

    layout = document.createElement('div');
    layout.className = 'attendance-teacher-layout';

    const left = document.createElement('div');
    left.className = 'attendance-teacher-column attendance-teacher-column-left';

    const right = document.createElement('div');
    right.className = 'attendance-teacher-column attendance-teacher-column-right';

    layout.appendChild(left);
    layout.appendChild(right);
    root.appendChild(layout);

    return { layout, left, right };
}

function ensureSpotCheckDialog() {
    let dialog = document.getElementById('attendance-spot-check-dialog');
    if (dialog) {
        return dialog;
    }

    dialog = document.createElement('dialog');
    dialog.id = 'attendance-spot-check-dialog';
    dialog.className = 'attendance-dialog';

    const header = document.createElement('div');
    header.className = 'attendance-dialog-header';

    const titleWrap = document.createElement('div');
    titleWrap.className = 'attendance-dialog-title-wrap';

    const title = document.createElement('h3');
    title.id = 'attendance-spot-check-title';
    title.textContent = 'Провера присуства';
    titleWrap.appendChild(title);

    const subtitle = document.createElement('p');
    subtitle.id = 'attendance-spot-check-subtitle';
    subtitle.className = 'attendance-dialog-subtitle';
    titleWrap.appendChild(subtitle);

    header.appendChild(titleWrap);

    const closeButton = document.createElement('button');
    closeButton.type = 'button';
    closeButton.className = 'attendance-dialog-close';
    closeButton.textContent = '×';
    closeButton.setAttribute('aria-label', 'Затвори');
    closeButton.addEventListener('click', () => dialog.close());
    header.appendChild(closeButton);

    dialog.appendChild(header);

    const body = document.createElement('div');
    body.id = 'attendance-spot-check-body';
    dialog.appendChild(body);

    const footer = document.createElement('div');
    footer.className = 'attendance-dialog-footer';

    const confirmButton = document.createElement('button');
    confirmButton.type = 'button';
    confirmButton.className = 'attendance-summary-btn';
    confirmButton.id = 'attendance-spot-check-confirm';
    confirmButton.textContent = 'У реду';
    footer.appendChild(confirmButton);

    dialog.appendChild(footer);
    dialog.addEventListener('close', () => {
        activeSpotCheck = null;
    });

    document.body.appendChild(dialog);
    return dialog;
}

function openSpotCheckDialog(root, kind, eventId, eventDate, event, students) {
    const dialog = ensureSpotCheckDialog();
    const subtitle = dialog.querySelector('#attendance-spot-check-subtitle');
    const body = dialog.querySelector('#attendance-spot-check-body');
    const confirmButton = dialog.querySelector('#attendance-spot-check-confirm');

    activeSpotCheck = {
        root,
        kind,
        eventId,
        eventDate,
        event,
        students,
    };

    subtitle.textContent = `${formatEventTitle(event)} • ${formatDateDDMMYYYY(event.event_date || event.reservation_date)}`;
    body.innerHTML = '';

    if (!students.length) {
        const empty = document.createElement('p');
        empty.textContent = 'Нема студената за ручну проверу.';
        body.appendChild(empty);
    } else {
        const list = document.createElement('ul');
        list.className = 'attendance-spot-check-list';
        students.forEach((student) => {
            const item = document.createElement('li');
            const label = document.createElement('label');
            label.className = 'attendance-spot-check-item';

            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.checked = false;
            checkbox.value = student.username;

            const text = document.createElement('span');
            text.textContent = formatStudentLabel(student);

            label.appendChild(checkbox);
            label.appendChild(text);
            item.appendChild(label);
            list.appendChild(item);
        });
        body.appendChild(list);
    }

    confirmButton.onclick = async () => {
        if (!activeSpotCheck) {
            return;
        }
        const selectedUsernames = activeSpotCheck.students.map((student) => student.username);
        const confirmedUsernames = Array.from(body.querySelectorAll('input[type="checkbox"]:checked'))
            .map((checkbox) => checkbox.value);
        confirmButton.disabled = true;
        try {
            await API.submitAttendanceSpotCheck(
                activeSpotCheck.kind,
                activeSpotCheck.eventId,
                activeSpotCheck.eventDate,
                {
                    selected_usernames: selectedUsernames,
                    confirmed_usernames: confirmedUsernames,
                }
            );
            activeSpotCheck = null;
            dialog.close();
        } catch (error) {
            const message = error.data?.error || error.message || 'Грешка при чувању провере.';
            const note = document.createElement('p');
            note.textContent = message;
            body.appendChild(note);
        } finally {
            confirmButton.disabled = false;
        }
    };

    if (typeof dialog.showModal === 'function') {
        dialog.showModal();
    } else {
        dialog.setAttribute('open', 'open');
    }
}

function showAccessMessage(root, messageText) {
    if (pollHandle) {
        clearInterval(pollHandle);
        pollHandle = null;
    }
    root.innerHTML = '';
    const panel = document.createElement('div');
    panel.className = 'attendance-panel attendance-blocked-panel';

    const title = document.createElement('h2');
    title.textContent = 'Пријава присуства је могућа само током часа';
    panel.appendChild(title);

    const note = document.createElement('p');
    note.textContent = messageText || 'Приступ је ограничен на време трајања часа.';
    panel.appendChild(note);

    root.appendChild(panel);
}

async function refresh(root) {
    const { kind, eventId, eventDate } = root.dataset;
    const data = await API.getAttendanceRoster(kind, eventId, eventDate);
    root.innerHTML = '';

    const { left, right } = ensureTeacherLayout(root);

    renderEventInfo(left, data.event);
    renderGeofenceControl(left, data, kind, eventId, eventDate, root);

    if (!data.attendance_open) {
        showAccessMessage(root, 'Пријава присуства је могућа само током часа.');
        return;
    }

    const joinUrl = buildJoinUrl(kind, eventId, eventDate, data.join_token);
    renderQr(left, joinUrl);
    renderChallenge(left, data.challenge, data.event);
    renderRoster(right, data.students || []);

    if (data.students && data.students.length) {
        const actions = document.createElement('div');
        actions.className = 'attendance-session-actions';

        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'attendance-summary-btn';
        button.textContent = 'Провери присуство';
        button.addEventListener('click', async () => {
            button.disabled = true;
            try {
                const shortlist = await API.getAttendanceSpotCheck(kind, eventId, eventDate, 5);
                openSpotCheckDialog(root, kind, eventId, eventDate, shortlist.event || data.event, shortlist.students || []);
            } catch (error) {
                window.alert(error.data?.error || error.message || 'Грешка при учитавању провере присуства.');
            } finally {
                button.disabled = false;
            }
        });
        actions.appendChild(button);
        right.appendChild(actions);
    }

}

const App = {
    async init() {
        const root = document.getElementById('attendance-root');
        try {
            await refresh(root);
            pollHandle = setInterval(() => refresh(root).catch((err) => {
                const result = handleAttendanceError(err);
                if (err.status === 403 && result.handled && result.type === 'outside_class') {
                    showAccessMessage(root, result.message);
                }
            }), 2000);
        } catch (err) {
            const result = handleAttendanceError(err);
            if (err.status === 403 && result.handled && result.type === 'outside_class') {
                showAccessMessage(root, result.message);
                return;
            }
            root.innerHTML = '';
            const p = document.createElement('p');
            p.textContent = err.message;
            root.appendChild(p);
        }
    },
};

document.addEventListener('DOMContentLoaded', () => App.init());
