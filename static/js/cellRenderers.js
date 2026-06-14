import { formatApiDate } from './util.js';

function formatLectureType(type) {
    const normalized = String(type || '').toLowerCase();
    const labels = {
        p: 'предавања',
        v: 'вежбе',
        k: 'колоквијум',
    };
    return labels[normalized] || type || '';
}

// Pomoćna funkcija unutar cellRenderers.js koja eliminiše ponavljanje
function getWeeklyHTML(cellData) {
    const canceledLabel = cellData.canceled ? " (отказано)" : "";
    return `
        <span class='room'>${cellData.room}</span> <br />
        <span>${cellData.teacher}</span> <br />
        <span class='${cellData.lecture_type}'>${cellData.lecture_name}${canceledLabel}</span> <br />
        <span>${cellData.groups.join(", ")}</span>
    `;
}

// Pomoćna funkcija koja kreira ili dopunjava senzore koji omogućavaju
// rezervacije mišem (drag & drop) unutar delova otkazanog termina
export function attachDragSensor(td, ctx, hour) {
    // ako kontejner već ne postoji u kontekstu (prvi sat termina), kreiramo ga
    if (!ctx.sensorContainer) {
        ctx.sensorContainer = document.createElement("div");
        ctx.sensorContainer.classList.add("sensor-container");
        td.appendChild(ctx.sensorContainer);
    }

    // kreiramo pojedinačni senzor za trenutni sat
    const sensor = document.createElement("div");
    sensor.className = "drag-sensor empty-slot";
    sensor.dataset.hour = hour;
    sensor.dataset.room_id = ctx.room_id;

    ctx.sensorContainer.appendChild(sensor);
}

function renderCancelBtn(container, cellUsername, ctx, onAction) {
    if (cellUsername === ctx.user || ctx.isAdmin) {
        const btn = document.createElement("button");
        btn.className = "res-cancel-btn";
        btn.textContent = "×";
        btn.onclick = (e) => {
            e.stopPropagation();
            onAction();
        };
        container.appendChild(btn);
        return btn;
    }
    return null;
}

function renderAttendanceBtn(ownerUsername, ctx, onAction) {
    if (!ctx.user || ownerUsername !== ctx.user) {
        return null;
    }
    const btn = document.createElement("button");
    btn.className = "attendance-btn";
    btn.textContent = "▣";
    btn.title = "QR код за присуство";
    btn.onclick = (e) => {
        e.stopPropagation();
        onAction();
    };
    return btn;
}

function createTopBar() {
    const topBar = document.createElement("div");
    topBar.className = "res-top-bar";

    const left = document.createElement("div");
    left.className = "res-top-bar-left";

    const right = document.createElement("div");
    right.className = "res-top-bar-right";

    topBar.append(left, right);
    return { topBar, left, right };
}

// Pomoćna za kalendar (unutar ovog fajla)
function renderCalendarMenu(cellData, fullDate) {
	// Priprema podataka
	const startTime = formatApiDate(fullDate, cellData.start);
	const endTime = formatApiDate(fullDate, cellData.end);
	const title = cellData.description;
	const description = `Корисник: ${cellData.username}, Сала: ${cellData.room}, Опис: ${cellData.description}`;

	// Kreiranje glavnog omotača (Dropdown)
	const dropdown = document.createElement('div');
	dropdown.className = 'calendar-dropdown';

	// Kreiranje dugmeta
	const btn = document.createElement('button');
	btn.className = 'cal-btn';
	btn.innerHTML = '📅';

	// Kreiranje menija
	const menu = document.createElement('div');
	menu.className = 'cal-menu';

	// Google Calendar Link
	const googleUrl = `https://calendar.google.com/calendar/render?action=TEMPLATE&text=${encodeURIComponent(title)}&dates=${startTime}/${endTime}&details=${encodeURIComponent(description)}&location=${encodeURIComponent('MatF, сала ' + cellData.room)}`;
	const googleLink = document.createElement('a');
	googleLink.href = googleUrl;
	googleLink.target = '_blank';
	googleLink.innerText = 'Google Calendar';

	// ICS Download Link
	const icsContent = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "BEGIN:VEVENT",
            `DTSTART:${startTime}`,
            `DTEND:${endTime}`,
            `SUMMARY:${title}`,
            `DESCRIPTION:${description}`,
            `LOCATION:Сала ${cellData.room}`,
            "END:VEVENT",
            "END:VCALENDAR"
	].join("\n");

	const icsBlob = new Blob([icsContent], { type: 'text/calendar' });
	const icsUrl = URL.createObjectURL(icsBlob);
	const icsLink = document.createElement('a');
	icsLink.href = icsUrl;
	icsLink.download = `rezervacija_${cellData.id}.ics`;
	icsLink.innerText = 'Outlook / Apple (.ics)';

	// sklapanje elemenata
	menu.appendChild(googleLink);
	menu.appendChild(icsLink);
	dropdown.appendChild(btn);
	dropdown.appendChild(menu);

	return dropdown;
}

// objekat koji sadrži funkcije za kreiranje sadržaja raznih vrsta ćelija
export const CellRenderers = {
    // ćelije sa rezervacijama
    reservation: (cellData, date, hour, td, ctx) => {
        td.classList.add("reservation");

	// moji časovi treba da budu malo drugačije prikazani
	if (cellData.username === ctx.user)
	    td.classList.add("my");

        const card = document.createElement("div");
        card.className = "res-card";

        const { topBar, left, right } = createTopBar();

        // cancel dugme (ako korisnik ima prava)
	const btn = renderCancelBtn(left, cellData.username, ctx, () => ctx.onDelete(cellData.id));

        // dugme za QR prisustvo samo tokom časa
        if (cellData.attendance_open !== false) {
            const attendanceBtn = renderAttendanceBtn(cellData.username, ctx, () =>
                ctx.onOpenAttendance("reservation", cellData.id, date)
            );
            if (attendanceBtn) {
                right.appendChild(attendanceBtn);
            }
        }

        // dugme za integraciju sa kalendarima
        const calMenu = renderCalendarMenu(cellData, date);
        right.appendChild(calMenu);

	// opis rezervacije
        // izdvajamo samo deo pre '@' ako je u pitanju email adresa
        const username = cellData.username.split('@')[0];
        const info = document.createElement("div");
        info.className = "res-info";
        info.innerHTML = `
            <span class="room">${cellData.room}</span>
            <span class="res-user">(${username})</span>
            <div class="res-desc">${cellData.description}</div>
        `;

        card.append(topBar, info);
        td.appendChild(card);
    },

    // ćelije sa časovima iz nedeljnog rasporeda
    weekly: (cellData, date, hour, td, ctx) => {
        td.classList.add("weekly");
	td.innerHTML = "";

        const card = document.createElement("div");
	card.className = "res-card cell-content"; 
	// moji časovi treba da budu malo drugačije prikazani
	if (cellData.teacher_username === ctx.user)
	    td.classList.add("my");

        // top bar sadrži kontrole - dugme za otkazivanje, kelendar
        const { topBar, left, right } = createTopBar();

        // dugme za otkazivanje/vraćanje časa
        const btn = renderCancelBtn(left, cellData.teacher_username, ctx, () => 
            ctx.onToggleWeekly(cellData.weekly_session_id, date)
        );

        // integracija sa kalendarom (samo ako čas nije otkazan)
        if (!cellData.canceled) {
            // Mapiramo podatke iz weekly u format koji renderCalendarMenu očekuje
            const calData = {
                id: cellData.weekly_session_id,
                description: `${cellData.lecture_name} (${cellData.teacher})`,
                username: cellData.teacher_username,
                room: ctx.room,
                start: cellData.start,
                end: cellData.end
            };
            if (cellData.attendance_open !== false) {
                const attendanceBtn = renderAttendanceBtn(cellData.teacher_username, ctx, () =>
                    ctx.onOpenAttendance("weekly", cellData.weekly_session_id, date)
                );
                if (attendanceBtn) {
                    right.appendChild(attendanceBtn);
                }
            }
            const calMenu = renderCalendarMenu(calData, date);
            right.appendChild(calMenu);
        }
        else {
            if (cellData.attendance_open !== false) {
                const attendanceBtn = renderAttendanceBtn(cellData.teacher_username, ctx, () =>
                    ctx.onOpenAttendance("weekly", cellData.weekly_session_id, date)
                );
                if (attendanceBtn) {
                    right.appendChild(attendanceBtn);
                }
            }
        }

        // sadržaj ćelije
        const info = document.createElement("div");
        info.className = "res-info";
        info.innerHTML = getWeeklyHTML(cellData);
        card.append(topBar, info);


	td.appendChild(card);

        if (cellData.canceled) {
	    td.classList.add("weekly-canceled");
	    // dodajemo senzore za rezervaciju pojedinačnih termina
	    // unutar otkazanog časa
	    attachDragSensor(td, ctx, hour);

	    card.style.position = "relative";
            card.style.zIndex = "10";
            card.style.pointerEvents = "none";

            // Dugme unutar kartice mora ponovo da prima klikove
            topBar.style.pointerEvents = "auto";
	}
    },

    // prazne ćelije
    empty: (cellData, date, hour, td, ctx) => {
        td.classList.add("empty-slot");
        td.dataset.room_id = ctx.room_id;
        td.dataset.hour = hour;
        const inner = document.createElement("div");
        inner.style.minHeight = "100px";
        td.appendChild(inner);
    }
};
