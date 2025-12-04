import json
import requests
import re
from bs4 import BeautifulSoup, NavigableString, Tag

def extract_lines(td):
    """
    Iz jednog td elementa izvlači tekst po linijama, 
    zadržavajući prazne linije ako postoje dupli <br><br>
    """
    lines = []
    current_line = ""

    for elem in td.children:
        if isinstance(elem, NavigableString):
            current_line += str(elem)
        elif isinstance(elem, Tag) and elem.name == "br":
            lines.append(current_line)
            current_line = ""
    # Dodaj poslednju liniju ako postoji
    lines.append(current_line)
    return [line.strip() for line in lines]  # ukloni whitespace, ali zadrži prazne linije

def parse_cell(td):
    inner = td.find("table")
    
    if inner is None:
        # običan slučaj
        return extract_lines(td)
    
    # ugnježdena tabela (tipično 2 reda, jedan prazan)
    collected = []
    for tr in inner.find_all("tr"):
        for c in tr.find_all("td"):
            raw = c.decode_contents()
            # Pretvori <br> u nove redove
            raw = td.get_text(separator="\n", strip=True)
            parts = [p.strip() for p in raw.split("\n") if p.strip()]
            collected.extend(parts)
    
    return collected

def process_url(url):
    r = requests.get(url)
    r.encoding = "utf-8"
    html = r.text
    soup = BeautifulSoup(html, "html5lib")

    h1 = soup.find("h1", {"align": "center"})
    if h1:
        # Ovo uklanja <font> i vraca samo tekst
        room_text = h1.get_text(strip=True)
        match = re.search(r":\s*(\w+)", room_text)
        if match:
            room_number = match.group(1)
        if not room_number:
            raise RuntimeError("Nema broja učionice")
    
    outer_tables = [t for t in soup.find_all("table") if t.find_parent("table") is None]
    if not outer_tables:
        raise RuntimeError("Nema spoljne tabele u dokumentu.")
    
    main_wrapper = outer_tables[0]
    inner_tables = main_wrapper.find_all("table", recursive=True)
    schedule_table = inner_tables[1]
    
    rows = schedule_table.find_all("tr")
    # prve dve vrste preskačemo
    rows = rows[2:]

    schedule = []

    day = 0
    for r in rows:
        row = []
        start = 8
        tds = r.find_all(["td", "th"])
        tds = tds[1:]
        for td in tds:
            parts = parse_cell(td)
        
            span = td.get("colspan", "1")
            span = int(span)
        
            if len(parts) >= 4:
                # normalizacija na subject/teacher/classroom
                subject = parts[0] if len(parts) > 0 else None
                group = parts[1] + parts[2] if len(parts) > 2 else None
                teacher = parts[3] if len(parts) > 3 else None

                row.append({
                    "subject": subject,
                    "teacher": teacher,
                    "group": group,
                    "room": room_number,
                    "day": day,
                    "start_slot": start,
                    "end_slot": start+span
                })
#            else:
#                print(td)

            start += span
        schedule.append(row)
        day += 1

    schedule = [item for sublist in schedule for item in sublist]
    return schedule



rooms = [0, 1, 2, 3, 5, 6, 7, 8, 9, 10, 33, 34, 11, 12, 13, 14, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, ]

BASE_URL = "https://poincare.matf.bg.ac.rs/~miljan.knezevic/raspored/ZS2526/room_{:03d}.html"
schedule = []
for r in rooms:
    url = BASE_URL.format(r)
    schedule = schedule + process_url(url)

print(json.dumps(schedule, ensure_ascii=False, indent=2))
