def test_index_template(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Резервације учионица" in r.get_data(as_text=True)


def test_calendar_template(client):
    r = client.get("/calendar")
    assert r.status_code == 200
    assert "Mesec:" in r.get_data(as_text=True)
