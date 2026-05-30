import app as myapp

def test_rooms(client, db):
    db.room(name="A1")
    db.room(name="Lab1", type="lab")

    r = client.get("/rooms")

    data = r.get_json()

    assert len(data) == 2

def test_rooms_filter(client, db):
    db.room(name="A1", type="lecture")
    db.room(name="Lab1", type="lab")

    r = client.get("/rooms?type=lab")

    data = r.get_json()

    assert len(data) == 1
    assert data[0]["name"] == "Lab1"    
