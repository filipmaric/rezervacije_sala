# Setup

1. Create and activate a virtual environment.

```bash
python3 -m venv venv
source venv/bin/activate
```

2. Install runtime dependencies.

```bash
pip install flask flask-login pyrad pytest
```

3. Initialize the database schema.

```bash
python app.py init-db
```

4. Start the app locally.

```bash
python app.py
```

5. Optionally build the docs locally.

```bash
mkdocs build --strict
```
