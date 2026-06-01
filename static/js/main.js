import { API } from './api.js';
import { CellRenderers, attachDragSensor } from './cellRenderers.js';
import { formatDateDDMMYYYY } from './util.js';

////////////////////////////////////////////////////////////////////////////////
// podaci o sesiji i UI elemente login/logout
////////////////////////////////////////////////////////////////////////////////
const AuthManager = {
    username: "",
    isAdmin: false,
    elements: {}, // reference na UI elemente

    init(elements) {
        this.elements = elements;
    },

    async updateUI() {
        try {
            const data = await API.whoami();
            if (data.logged_in) {
                this.username = data.username;
                this.isAdmin = await API.isAdmin(this.username);
                this.elements.loginForm.style.display = "none";
                this.elements.loginInfo.textContent = this.username;
                this.elements.logoutForm.style.display = "inline-block";
                this.elements.myReservationsLink.style.display = "inline-block";
            } else {
                this.reset();
            }
        } catch (err) {
            console.error("Auth error:", err);
            this.reset();
        }
    },

    reset() {
        this.username = "";
        this.isAdmin = false;
        this.elements.loginForm.style.display = "block";
        this.elements.logoutForm.style.display = "none";
        this.elements.loginInfo.textContent = "";
        this.elements.myReservationsLink.style.display = "none";
    },

    async login(username, password, onSuccess) {
        try {
            await API.login(username, password);
            await this.updateUI();
            if (onSuccess) onSuccess(); // callback
        } catch (err) {
            alert(err.message);
        }
    },

    async logout(onSuccess) {
        try {
            await API.logout();
            this.reset();
            if (onSuccess) onSuccess(); // callback
        } catch (err) {
            alert(err.message);
        }
    },

    isLoggedIn() {
        return !!this.username;
    }
};

////////////////////////////////////////////////////////////////////////////////
// prikaz tabela sa rezervacijama
////////////////////////////////////////////////////////////////////////////////
const TableManager = {
    container: null,
    rooms: {},

    init(container, rooms) {
        this.container = container;
        this.rooms = rooms;
    },


    getDayName(n) {
        const weekDays = [
            "понедељак",
            "уторак",
            "среда",
            "четвртак",
            "петак",
            "субота",
            "недеља",
        ];
	return weekDays[n];
    },

    getDayLabel(data) {
        return data.is_working ? ("распоред часова: " + this.getDayName(data.week_day)) : "ненаставни дан";
    },

    getWeekDay(date) {
	return (new Date(date).getDay() + 6) % 7;
    },

    renderHeader(data) {
	// .date, this.getDayLabel(data)
        const h2 = document.createElement("h2");
        h2.textContent = `Заузеће сала на дан ${this.getDayName(this.getWeekDay(data.date))}, ${formatDateDDMMYYYY(data.date)}`;
	if (!data.is_working || (data.is_working && data.week_day != this.getWeekDay(data.date)))
	    h2.textContent += ` (${this.getDayLabel(data)})`;
        this.container.appendChild(h2);
    },


    render(data) {
        this.container.innerHTML = "";
        const roomsByLocation = this.groupRoomsByLocation(this.rooms);
        this.renderHeader(data);

        for (const [location, locationRooms] of Object.entries(roomsByLocation)) {
            const h3 = document.createElement("h3");
            h3.textContent = location;
            this.container.appendChild(h3);
            this.container.appendChild(this.createTable(locationRooms, data));
        }
    },

    createTable(locationRooms, data) {
        const table = document.createElement("table");
        table.appendChild(this.createTableHeader(locationRooms));

        const tbody = document.createElement("tbody");
        const prevCells = {};

	const START_HOUR = 8, END_HOUR = 21;
        for (let hour = START_HOUR; hour < END_HOUR; hour++) {
            const tr = document.createElement("tr");
            const thTime = document.createElement("th");
            thTime.classList.add('time');
            thTime.textContent = `${hour}:00 - ${hour + 1}:00`;
            tr.appendChild(thTime);

            Object.keys(locationRooms).forEach(k => {
                const room = locationRooms[k];
                const td = this.createTd(room.id, hour, data, prevCells);
                if (td) tr.appendChild(td);
            });
            tbody.appendChild(tr);
        }
        table.appendChild(tbody);
        return table;
    },

    createTableHeader(rooms) {
        const thead = document.createElement("thead");
        const headerRow = document.createElement("tr");
        const th = document.createElement("th");
        th.textContent = "Време/Сала";
        headerRow.appendChild(th);

        Object.keys(rooms).forEach(k => {
            const room = rooms[k];
            const thRoom = document.createElement("th");
            const name = room.name;
            thRoom.textContent = name;
            headerRow.appendChild(thRoom);
        });
        thead.appendChild(headerRow);
        return thead;
    },

    createTd(room_id, hour, data, prevCells) {
        const roomItems = data.rooms[room_id] || [];
        const cellData = roomItems.find(item => item.start <= hour && hour < item.end);

        let contentKey = '';
	if (!cellData) {
	    // Ako je prazno, ključ je i dalje jedinstven za tu ćeliju
	    contentKey = `empty-${room_id}-${hour}`;
	} else if (cellData.type === 'weekly') {
	    // Za nedeljne koristimo ceo objekat (ili npr. weekly_session_id)
	    contentKey = JSON.stringify(cellData);
	} else {
	    // ZA REZERVACIJE: Uzimamo samo ono što ih čini "istim" u nizu
	    contentKey = `res-${cellData.username}-${cellData.description}`;
	}

        const prev = prevCells[room_id];
        if (prev && prev.key === contentKey) {
            prev.td.rowSpan++;
            if (cellData?.type === "weekly" && cellData.canceled) {
                attachDragSensor(prev.td, { room_id, sensorContainer: prev.sensorContainer }, hour);
            }
            return null;
        }

        const td = document.createElement("td");
        const ctx = {
            user: AuthManager.username,
            isAdmin: AuthManager.isAdmin,
            room_id: room_id,
            room: this.rooms[room_id]?.name || room_id,
            sensorContainer: null,
            onOpenAttendance: (kind, eventId, date) => {
                const basePath = window.APP_CONFIG?.BASE_PATH || "";
                window.location.href = `${basePath}/attendance/${kind}/${eventId}/${date}`;
            },
            onDelete: async (id) => {
                await API.deleteReservation(id);
                App.refresh(); // globalno osvežavanje celog prikaza
            },
            onToggleWeekly: async (id, date) => {
                await API.toggleWeekly(id, date);
                App.refresh(); // globalno osvežavanje celog prikaza
            }
        };

        const render = CellRenderers[cellData?.type || 'empty'] || CellRenderers.empty;
        render(cellData, data.date, hour, td, ctx);

        prevCells[room_id] = { td, key: contentKey, sensorContainer: ctx.sensorContainer };
        return td;
    },

    groupRoomsByLocation(rooms) {
        const locations = {};
        Object.entries(rooms).forEach(([id, room]) => {
            const loc = room.location || "Непозната локација";
            if (!locations[loc]) locations[loc] = {};
            locations[loc][id] = room;
        });
        return locations;
    }
};

////////////////////////////////////////////////////////////////////////////////
// akcije kojima korisnik mišem rezerviše salu
////////////////////////////////////////////////////////////////////////////////
const DragAndDropManager = {
    isDragging: false,
    dragStart: null,
    dragRoom: null,
    container: null,
    rooms: {},

    init(container, rooms) {
        this.container = container;
        this.rooms = rooms;
        this.setupHandlers();
    },

    setupHandlers() {
        const slots = this.container.querySelectorAll(".empty-slot");
        slots.forEach(slot => {
            slot.onmousedown = (e) => {
                this.isDragging = true;
                this.dragStart = slot;
                this.dragRoom = slot.dataset.room_id;
                slot.classList.add("selected");
            };
            slot.onmouseenter = () => {
                if (this.isDragging && slot.dataset.room_id === this.dragRoom) {
                    slot.classList.add("selected");
                }
            };
            slot.onmouseup = () => this.handleMouseUp();
        });
    },

    async handleMouseUp() {
        if (!this.isDragging) return;
        this.isDragging = false;

        const selected = Array.from(this.container.querySelectorAll(".empty-slot.selected"));
        selected.forEach(el => el.classList.remove("selected"));

        if (!selected.length) return;

        const roomId = parseInt(selected[0].dataset.room_id);
        const hours = selected.map(s => parseInt(s.dataset.hour)).sort((a,b) => a-b);
        const start = hours[0];
        const end = hours[hours.length - 1] + 1;

	const roomName = this.rooms.find(r => r.id === roomId)?.name || roomId;
        const description = prompt(`Опис за термин у сали ${roomName} (${start}:00-${end}:00):`);
        if (!description) return;

        let username = AuthManager.isAdmin ? prompt("Власник резервације:") : "";

        try {
            await API.reserve({
                date: document.getElementById("date-input").value,
                room_id: roomId,
                start_slot: start,
                end_slot: end,
                description,
                ...(username && { username })
            });
            App.refresh();
        } catch (err) { alert(err.message); }
    }
};


const App = {
    async init() {
        // inicijalizacija AuthManagera sa UI elementima
        AuthManager.init({
            loginForm: document.getElementById("login-form"),
            loginInfo: document.getElementById("login-info"),
            logoutForm: document.getElementById("logout-form"),
            myReservationsLink: document.getElementById("my-reservations-link")
        });

	// prikazujemo odgovarajuće elemente za login/logout
        await AuthManager.updateUI();

	// postavljamo današnji datum
        const dateInput = document.getElementById("date-input");
        const params = new URLSearchParams(window.location.search);
        const date = params.get('date');
        if (/^\d{4}-\d{2}-\d{2}$/.test(date)) {
            dateInput.value = date;
           console.log(date);
        } if (!dateInput.value) dateInput.value = new Date().toISOString().split("T")[0];

        // učitavanje soba
        const rooms = await API.getRooms();

	// inicijalizacija menažera
        TableManager.init(document.getElementById("occupancy"), rooms);
        DragAndDropManager.init(document.getElementById("occupancy"), rooms);

	// povezujemo UI sa događajima
        this.setupEvents();

	// osvežavamo prikaz tabele
        this.refresh();
    },

    // učitavamo i prikazujemo podatke za odabrani datum
    async refresh() {
	// datum za koji se prikazuju podaci
        const date = document.getElementById("date-input").value;
	// učitavamo podatke i prikazujemo ih
        const data = await API.getOccupancy(date);
        TableManager.render(data);
	// ako je korisnik ulogovan, uključujemo rezervacije pomoću drag & drop
        if (AuthManager.isLoggedIn()) DragAndDropManager.setupHandlers();
    },

    // povezujemo UI sa događajima
    setupEvents() {
	// interfejs za podešavanje datuma
        document.getElementById("date-prev").onclick = () => this.changeDate(-1);
        document.getElementById("date-next").onclick = () => this.changeDate(1);
        document.getElementById("date-input").onchange = () => this.refresh();

	// dugme za logout
        document.getElementById("logout").onclick = () => AuthManager.logout(() => this.refresh());

	// formular za login
        document.getElementById("login-form").onsubmit = async (e) => {
            e.preventDefault();
            const u = document.getElementById("username").value;
            const p = document.getElementById("password").value;
            await AuthManager.login(u, p, () => this.refresh());
            e.target.reset();
        };
    },

    // menjamo datum za dati broj dana (bilo pozitivan, bilo negativan)
    changeDate(days) {
        const input = document.getElementById("date-input");
        const d = new Date(input.value);
        d.setDate(d.getDate() + days);
        input.value = d.toISOString().split("T")[0];
        this.refresh();
    }
};

document.addEventListener("DOMContentLoaded", () => App.init());
