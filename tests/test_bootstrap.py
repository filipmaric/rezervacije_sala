import app as myapp


def test_database_bootstraps_schema(tmp_path):
    old_database = myapp.DATABASE
    db_path = tmp_path / "fresh.db"
    myapp.DATABASE = str(db_path)

    try:
        with myapp.app.app_context():
            assert myapp.init_db() is True
            assert myapp.init_db() is False
            db = myapp.get_db()
            tables = {
                row["name"]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }

        assert db_path.exists()
        assert "rooms" in tables
        assert "reservations" in tables
    finally:
        myapp.DATABASE = old_database
