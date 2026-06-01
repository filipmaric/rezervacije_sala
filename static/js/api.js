const BASE_PATH = window.APP_CONFIG?.BASE_PATH || '';
const getUrl = (endpoint) => `${BASE_PATH}${endpoint}`;
const getCsrfToken = () => document.querySelector('meta[name="csrf-token"]')?.content || '';

function withCsrfHeaders(headers = {}) {
    const result = new Headers(headers);
    const token = getCsrfToken();
    if (token) {
        result.set('X-CSRFToken', token);
    }
    return result;
}


async function handleResponse(res, errorText) {
        if (res.ok) return res.json();

        const contentType = res.headers.get("content-type");
        if (contentType && contentType.includes("application/json")) {
            const data = await res.json();
            const err = new Error(data.error || "Непозната грешка");
            err.status = res.status;
            err.data = data;
            throw err;
        } else {
            const textError = await res.text();
            console.error("Server HTML Error:", textError);
            const err = new Error(`${errorText} - серверска грешка (${res.status})`);
            err.status = res.status;
            throw err;
        }
}


export const API = {
    async whoami() {
        const res = await fetch(getUrl("/whoami"));
        return handleResponse(res);
    },

    async isAdmin(username) {
        const res = await fetch(getUrl(`/is_admin/${username}`));
        const data = await res.json();
        return data.is_admin;
    },
    async login(username, password) {
        const res = await fetch(getUrl("/login"), {
            method: "POST",
            headers: withCsrfHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ username, password }),
        });

        if (!res.ok) {
            throw new Error("Пријава на систем није успела");
        }
        return handleResponse(res);
    },
    async logout() {
        const res = await fetch(getUrl("/logout"), {
            method: "POST",
            headers: withCsrfHeaders(),
        });
        if (!res.ok) {
            throw new Error("Грешка при одјављивању");
        }
        return res;
    },
    async getOccupancy(date) {
        const res = await fetch(getUrl(`/occupancy?date=${date}`));
        return handleResponse(res, "Грешка при учитавању резервације");
    },
    async getRooms() {
        const res = await fetch(getUrl("/rooms"));
        return handleResponse(res);
    },
    async getMyReservations(semesterId) {
        const suffix = semesterId ? `?semester_id=${semesterId}` : "";
        const res = await fetch(getUrl(`/my_reservations_data${suffix}`));
        return handleResponse(res, "Грешка при учитавању мојих резервација");
    },
    async getAttendanceChallenge(kind, eventId, eventDate) {
        const res = await fetch(getUrl(`/attendance/${kind}/${eventId}/${eventDate}/challenge`), {
            credentials: "same-origin",
        });
        return handleResponse(res, "Грешка при учитавању података о присуству");
    },
    async getAttendanceRoster(kind, eventId, eventDate) {
        const res = await fetch(getUrl(`/attendance/${kind}/${eventId}/${eventDate}/data`), {
            credentials: "same-origin",
        });
        return handleResponse(res, "Грешка при учитавању листе присутних");
    },
    async submitAttendance(kind, eventId, eventDate, body) {
        const res = await fetch(getUrl(`/attendance/${kind}/${eventId}/${eventDate}/join`), {
            method: "POST",
            headers: withCsrfHeaders({ "Content-Type": "application/json" }),
            credentials: "same-origin",
            body: JSON.stringify(body),
        });
        return handleResponse(res, "Грешка при пријави присуства");
    },
    async deleteReservation(resId) {
        const res = await fetch(getUrl("/reservation/" + resId), {
            method: "DELETE",
            headers: withCsrfHeaders(),
        });
        return handleResponse(res, "Грешка при отказивању резервације");
    },
    async reserve(body) {
        const res = await fetch(getUrl("/reserve"), {
            method: "POST",
            headers: withCsrfHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify(body),
        });
        return handleResponse(res, "Грешка при резервацији");
    },
    async toggleWeekly(weeklySessionId, date) {
        const res = await fetch(getUrl("/weekly_session_cancel"), {
            method: "POST",
            headers: withCsrfHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({
                weekly_session_id: weeklySessionId,
                date: date,
            }),
        });
        return handleResponse(res, "Грешка при отказивању часа");
    }
};
