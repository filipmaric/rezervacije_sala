const BASE_PATH = window.APP_CONFIG?.BASE_PATH || '';
const getUrl = (endpoint) => `${BASE_PATH}${endpoint}`;


async function handleResponse(res, errorText) {
        if (res.ok) return res.json();

        const contentType = res.headers.get("content-type");
        if (contentType && contentType.includes("application/json")) {
            const data = await res.json();
            throw new Error(data.error || "Непозната грешка");
        } else {
            const textError = await res.text();
            console.error("Server HTML Error:", textError);
            throw new Error(`${errorText} - серверска грешка (${res.status})`);
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
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username, password }),
        });

        if (!res.ok) {
            throw new Error("Пријава на систем није успела");
        }
        return handleResponse(res);
    },
    async logout() {
        const res = await fetch(getUrl("/logout"), { method: "POST" });
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
    async deleteReservation(resId) {
        const res = await fetch(getUrl("/reservation/" + resId), { method: "DELETE" });
	return handleResponse(res, "Грешка при отказивању резервације");
    },
    async reserve(body) {
        const res = await fetch(getUrl("/reserve"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        return handleResponse(res, "Грешка при резервацији");
    },
    async toggleWeekly(weeklySessionId, date) {
        const res = await fetch(getUrl("/weekly_session_cancel"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                weekly_session_id: weeklySessionId,
                date: date,
            }),
        });
        return handleResponse(res, "Грешка при отказивању часа");
    }
};
