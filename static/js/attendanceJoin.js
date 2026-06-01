import { API } from './api.js';
import { formatDateDDMMYYYY } from './util.js';

let pollHandle = null;
let successShown = false;
let blockedShown = false;
let expiredShown = false;
let frozenUntilBucket = null;
let currentChallengeBucket = null;
let freezeCountdownHandle = null;

const CHALLENGE_ROUND_MS = 10000;

function pad2(value) {
    return String(value).padStart(2, '0');
}

function clearFreezeCountdown() {
    if (freezeCountdownHandle) {
        clearInterval(freezeCountdownHandle);
        freezeCountdownHandle = null;
    }
}

function updateFreezeCountdown(root) {
    if (blockedShown || frozenUntilBucket === null) {
        clearFreezeCountdown();
        return;
    }

    const message = root.querySelector('#attendance-message');
    const freezeNotice = root.querySelector('#attendance-freeze-notice');
    const buttons = root.querySelectorAll('.challenge-option-btn');
    const nextRoundAt = (frozenUntilBucket + 1) * CHALLENGE_ROUND_MS;
    const secondsLeft = Math.max(0, Math.ceil((nextRoundAt - Date.now()) / 1000));

    buttons.forEach((button) => {
        button.disabled = secondsLeft > 0;
    });

    if (freezeNotice) {
        freezeNotice.hidden = secondsLeft <= 0;
        freezeNotice.textContent = secondsLeft > 0
            ? `Погрешан број. Сачекајте нови круг за ${secondsLeft} секунди.`
            : '';
    }

    if (message && secondsLeft > 0) {
        message.textContent = '';
    }

    if (secondsLeft <= 0) {
        frozenUntilBucket = null;
        clearFreezeCountdown();
        refresh(root).catch((err) => {
            if (err.status === 403) {
                const errorText = err.data?.error || err.message || '';
                if (errorText.includes('истекла')) {
                    showSessionExpiredState(root);
                } else {
                    showBlockedState(root);
                }
            } else {
                const msg = root.querySelector('#attendance-message');
                if (msg) msg.textContent = err.message;
            }
        });
    }
}

function renderChallenge(root, data, state = {}) {
    const existing = root.querySelector('.challenge-panel');
    if (existing) existing.remove();

    currentChallengeBucket = data.challenge.bucket;
    if (frozenUntilBucket !== null && currentChallengeBucket > frozenUntilBucket) {
        frozenUntilBucket = null;
    }

    const panel = document.createElement('div');
    panel.className = 'challenge-panel';

    const title = document.createElement('h2');
    title.textContent = 'Потврдa присуства';
    panel.appendChild(title);

    const username = document.createElement('input');
    username.type = 'text';
    username.id = 'attendance-username';
    username.placeholder = 'Корисничко име';
    username.required = true;
    username.value = state.username || '';
    panel.appendChild(username);

    const password = document.createElement('input');
    password.type = 'password';
    password.id = 'attendance-password';
    password.placeholder = 'Лозинка';
    password.required = true;
    password.value = state.password || '';
    panel.appendChild(password);

    const choices = document.createElement('div');
    choices.className = 'challenge-options';
    data.challenge.options.forEach((option) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'challenge-option-btn';
        button.textContent = option;
        button.disabled = frozenUntilBucket !== null && currentChallengeBucket <= frozenUntilBucket;
        button.addEventListener('click', async () => {
            if (button.disabled) {
                return;
            }
            try {
                const payload = {
                    username: username.value,
                    password: password.value,
                    selected_code: Number(option),
                };
                const result = await API.submitAttendance(
                    root.dataset.kind,
                    root.dataset.eventId,
                    root.dataset.eventDate,
                    payload,
                );
                if (pollHandle) {
                    clearInterval(pollHandle);
                    pollHandle = null;
                }
                successShown = true;
                root.innerHTML = '';
                const successPanel = document.createElement('div');
                successPanel.className = 'attendance-panel attendance-success-panel';

                const success = document.createElement('h2');
                success.textContent = 'Успешно сте се пријавили';
                successPanel.appendChild(success);

                const note = document.createElement('p');
                note.textContent = 'Можете затворити ову страницу.';
                successPanel.appendChild(note);

                root.appendChild(successPanel);
            } catch (err) {
                if (err.status === 403) {
                    const errorText = err.data?.error || err.message || '';
                    if (errorText.includes('истекла')) {
                        showSessionExpiredState(root);
                    } else {
                        showBlockedState(root);
                    }
                    return;
                }
                if (err.status === 409) {
                    frozenUntilBucket = currentChallengeBucket;
                    clearFreezeCountdown();
                    updateFreezeCountdown(root);
                    freezeCountdownHandle = setInterval(() => updateFreezeCountdown(root), 1000);
                    return;
                }
                message.textContent = err.message;
            }
        });
        choices.appendChild(button);
    });
    panel.appendChild(choices);

    const freezeNotice = document.createElement('p');
    freezeNotice.id = 'attendance-freeze-notice';
    freezeNotice.className = 'attendance-freeze-notice';
    freezeNotice.hidden = true;
    panel.appendChild(freezeNotice);

    const message = document.createElement('p');
    message.id = 'attendance-message';
    panel.appendChild(message);
    root.appendChild(panel);
}

function renderEventInfo(root, event) {
    const panel = document.createElement('div');
    panel.className = 'attendance-panel';

    const title = document.createElement('h2');
    if (event.course_name) {
        title.textContent = event.course_name;
    } else {
        title.textContent = event.description || 'Резервација';
    }
    panel.appendChild(title);

    const details = document.createElement('p');
    const date = event.event_date || event.reservation_date;
    details.textContent = `Датум: ${formatDateDDMMYYYY(date)} | Сала: ${event.room_name} | Време: ${pad2(event.start_slot)}:00 - ${pad2(event.end_slot)}:00`;
    panel.appendChild(details);

    if (event.teacher_name) {
        const teacher = document.createElement('p');
        teacher.textContent = `Наставник: ${event.teacher_name}`;
        panel.appendChild(teacher);
    }

    root.appendChild(panel);
}

function showBlockedState(root) {
    blockedShown = true;
    expiredShown = false;
    frozenUntilBucket = null;
    clearFreezeCountdown();
    if (pollHandle) {
        clearInterval(pollHandle);
        pollHandle = null;
    }
    root.innerHTML = '';

    const panel = document.createElement('div');
    panel.className = 'attendance-panel attendance-blocked-panel';

    const title = document.createElement('h2');
    title.textContent = 'Морате поново да скенирате QR код';
    panel.appendChild(title);

    const note = document.createElement('p');
    note.textContent = 'Ова сесија је закључана због превише нетачних покушаја.';
    panel.appendChild(note);

    root.appendChild(panel);
}

function showSessionExpiredState(root) {
    expiredShown = true;
    blockedShown = false;
    frozenUntilBucket = null;
    clearFreezeCountdown();
    if (pollHandle) {
        clearInterval(pollHandle);
        pollHandle = null;
    }
    root.innerHTML = '';

    const panel = document.createElement('div');
    panel.className = 'attendance-panel attendance-blocked-panel';

    const title = document.createElement('h2');
    title.textContent = 'Сесија је истекла';
    panel.appendChild(title);

    const note = document.createElement('p');
    note.textContent = 'Поново скенирајте QR код да бисте добили нову сесију.';
    panel.appendChild(note);

    root.appendChild(panel);
}

async function refresh(root) {
    if (successShown || blockedShown || expiredShown) {
        return;
    }
    if (frozenUntilBucket !== null) {
        return;
    }
    const existingUsername = root.querySelector('#attendance-username')?.value || '';
    const existingPassword = root.querySelector('#attendance-password')?.value || '';
    const data = await API.getAttendanceChallenge(
        root.dataset.kind,
        root.dataset.eventId,
        root.dataset.eventDate,
    );
    root.innerHTML = '';
    renderEventInfo(root, data.event);
    renderChallenge(root, data, {
        username: existingUsername,
        password: existingPassword,
    });
}

const App = {
    async init() {
        const root = document.getElementById('attendance-join-root');

        try {
            await refresh(root);
            pollHandle = setInterval(async () => {
                if (frozenUntilBucket !== null) {
                    return;
                }
                try {
                    await refresh(root);
                } catch (err) {
                    if (err.status === 403) {
                        const errorText = err.data?.error || err.message || '';
                        if (errorText.includes('истекла')) {
                            showSessionExpiredState(root);
                        } else {
                            showBlockedState(root);
                        }
                        return;
                    }
                    const message = root.querySelector('#attendance-message');
                    if (message) message.textContent = err.message;
                }
            }, 5000);
        } catch (err) {
            if (err.status === 403) {
                const errorText = err.data?.error || err.message || '';
                if (errorText.includes('истекла')) {
                    showSessionExpiredState(root);
                } else {
                    showBlockedState(root);
                }
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
