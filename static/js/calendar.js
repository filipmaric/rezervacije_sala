document.addEventListener("DOMContentLoaded", async function() {
    const monthSelect = document.getElementById('month');
    const yearSelect = document.getElementById('year');
    const calendarDiv = document.getElementById('calendar');
    const saveBtn = document.getElementById('saveBtn');
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';

    const currentUsername = 'demo'; // primer, po potrebi uzeti iz session

    // Popuni mesec i godinu
    for (let m = 1; m <= 12; m++) monthSelect.append(new Option(m, m));
    for (let y = 2024; y <= 2026; y++) yearSelect.append(new Option(y, y));

    monthSelect.value = new Date().getMonth() + 1;
    yearSelect.value = new Date().getFullYear();

    let calendarData = {}; // { 'YYYY-MM-DD': { is_working: 1, week_day: 2 } }
    let holidays = []; 

    async function loadCalendarData(month, year) {
	const res = await fetch(`/calendar_data?month=${month}&year=${year}`);
	const data = await res.json();
	calendarData = data.calendar;
	holidays = data.holidays;
    }
    
    async function renderCalendar() {
	const month = parseInt(monthSelect.value);
	const year = parseInt(yearSelect.value);
	
	await loadCalendarData(month, year); // sada imamo calendarData i holidays
	
	calendarDiv.innerHTML = '';

	const firstDay = new Date(year, month - 1, 1).getDay(); // nedelja=0
	const daysInMonth = new Date(year, month, 0).getDate();

	const days = ["Ned", "Pon", "Uto", "Sre", "Čet", "Pet", "Sub"];
	for (let i = 0; i < 7; i++) {
	    const day = document.createElement('div');
	    day.innerHTML = days[i];
	    calendarDiv.appendChild(day);
	}
	    

	// prazni slotovi pre prvog dana
	for (let i = 0; i < firstDay; i++) {
            const empty = document.createElement('div');
            calendarDiv.appendChild(empty);
	}

	for (let day = 1; day <= daysInMonth; day++) {
            const dateStr = `${year}-${String(month).padStart(2,'0')}-${String(day).padStart(2,'0')}`;
            const td = document.createElement('div');
            td.classList.add('day');

            const weekday = new Date(year, month-1, day).getDay();
	    const item = calendarData[dateStr];

            // Stil i status
            if (holidays.includes(dateStr)) {
		td.classList.add('holiday');
		calendarData[dateStr] = {'is_working': 0, week_day: -1};
            } else if (item !== undefined) {
		td.classList.add(item.is_working ? 'working-day' : 'non-working-day');
		if (item.week_day !== undefined && item.week_day !== -1)
		    td.classList.add('custom-weekday'); // vizuelni mark
            } else {
		td.classList.add('non-working-day');
		calendarData[dateStr] = {is_working: 0, week_day: -1};
            }

            td.textContent = day;

            td.addEventListener('click', () => {
		if (holidays.includes(dateStr)) return; // ne može se menjati praznik
		td.classList.toggle('working-day');
		td.classList.toggle('non-working-day');
		calendarData[dateStr] = {'is_working': td.classList.contains('working-day') ? 1 : 0};
            });

	    td.addEventListener('contextmenu', (e) => {
		e.preventDefault();
		if (holidays.includes(dateStr)) return;
		
		showWeekdayMenu(dateStr, td);
	    });

            calendarDiv.appendChild(td);
	}
    }

    const weekdayMenu = document.getElementById('weekdayMenu');
    const weekdayNames = ["Pon", "Uto", "Sre", "Čet", "Pet", "Sub", "Ned"];

    // otvaranje menija
    function showWeekdayMenu(dateStr, dayElem) {
	weekdayMenu.innerHTML = "";
	weekdayMenu.style.visibility = "hidden";
	weekdayMenu.style.display = "flex";
	weekdayMenu.style.left = "0px";
	weekdayMenu.style.top = "0px";

	// opcija Default
	const def = document.createElement('div');
	def.textContent = "Default (real day)";
	def.onclick = () => {
            calendarData[dateStr].week_day = -1;
            dayElem.classList.remove('custom-weekday');
            hideMenu();
	};
	weekdayMenu.appendChild(def);

	// 0–6
	weekdayNames.forEach((name, idx)=> {
            const opt = document.createElement('div');
            opt.textContent = name + ` (${idx})`;
            opt.onclick = () => {
		calendarData[dateStr].week_day = idx;
		dayElem.classList.add('custom-weekday');
		hideMenu();
            };
	    weekdayMenu.appendChild(opt);
	});

	const margin = 8;
	const cellRect = dayElem.getBoundingClientRect();
	const menuRect = weekdayMenu.getBoundingClientRect();
	let left = cellRect.right + margin;
	if (left + menuRect.width > window.innerWidth - margin) {
	    left = cellRect.left - menuRect.width - margin;
	}
	left = Math.min(Math.max(left, margin), Math.max(margin, window.innerWidth - menuRect.width - margin));
	const top = Math.min(
	    Math.max(cellRect.top, margin),
	    Math.max(margin, window.innerHeight - menuRect.height - margin)
	);
	weekdayMenu.style.left = left + "px";
	weekdayMenu.style.top = top + "px";
	weekdayMenu.style.visibility = "visible";
    }

    function hideMenu() { weekdayMenu.style.display = "none"; }

    // zatvori meni klikom van njega
    document.addEventListener('click', () => hideMenu());

    
    monthSelect.addEventListener('change', renderCalendar);
    yearSelect.addEventListener('change', renderCalendar);
    await renderCalendar();

    saveBtn.addEventListener('click', async () => {
	const updates = Object.entries(calendarData).map(([date, item]) => ({
	    date,
	    is_working: item.is_working,
	    week_day: item.week_day ?? -1
	}));	
	// POST request za backend:
	await fetch('/update_calendar', {
          method: 'POST',
          headers: {
              'Content-Type':'application/json',
              ...(csrfToken ? {'X-CSRFToken': csrfToken} : {}),
          },
          body: JSON.stringify(updates)
	  });
    });
});
