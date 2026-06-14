import { API } from './api.js';
import { formatDateDDMMYYYY } from './util.js';

let pollHandle = null;

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
        `Сала: ${event.room_name}`,
        `Време: ${String(event.start_slot).padStart(2, '0')}:00 - ${String(event.end_slot).padStart(2, '0')}:00`,
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
            li.textContent = `${student.username} (${student.created_at})`;
            list.appendChild(li);
        });
        section.appendChild(list);
    }

    root.appendChild(section);
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

    renderEventInfo(root, data.event);

    const joinUrl = buildJoinUrl(kind, eventId, eventDate, data.join_token);
    renderQr(root, joinUrl);
    renderChallenge(root, data.challenge, data.event);
    renderRoster(root, data.students || []);

    const note = document.createElement('p');
    note.className = 'attendance-note';
    note.textContent = 'Листа се аутоматски освежава.';
    root.appendChild(note);
}

const App = {
    async init() {
        const root = document.getElementById('attendance-root');
        try {
            await refresh(root);
            pollHandle = setInterval(() => refresh(root).catch((err) => {
                const errorText = err.data?.error || err.message || '';
                if (err.status === 403 && errorText.includes('само током часа')) {
                    showAccessMessage(root, errorText);
                }
            }), 1000);
        } catch (err) {
            const errorText = err.data?.error || err.message || '';
            if (err.status === 403 && errorText.includes('само током часа')) {
                showAccessMessage(root, errorText);
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
