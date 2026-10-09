# Fan Site + Reliable Signup Backend

One folder = website + backend + database. Needs only **Python 3.8+** (no installs).

## Run
- Windows: double-click `run.bat`
- Mac/Linux: `./run.sh`  (or `python3 server.py`)
- Open http://localhost:8000

The terminal prints your **export link** (contains a private admin key).
Others on the same Wi-Fi can open `http://<your-computer-IP>:8000`.

## Where is the data?
`data/fans.db` — a single SQLite file. Copy it anywhere to move/share your data
(open with DB Browser for SQLite, or any SQLite tool).
Automatic backups go to `backups/` every 6 hours (newest 14 kept).

## Export
`http://localhost:8000/api/export.csv?key=YOUR_KEY`  (opens in Excel/Sheets)
Key is in `data/admin_key.txt`, or set your own with env var `ADMIN_KEY`.

## Reliability features
- SQLite WAL mode + full fsync: no data loss on crash/power cut
- DB-level constraints (name, email, option, message <= 500 chars)
- Duplicate-proof: same email + purpose updates the row instead of duplicating
- Server-side validation, honeypot spam trap, rate limit, 10 KB body cap
- Parameterized SQL (no SQL injection), CSV-injection-safe export

## Deploy online (optional)
Use any host that runs Python or Docker (Render, Railway, Fly.io). Attach a
persistent disk/volume to `/app/data` so the database survives restarts.
Env vars: `PORT`, `ADMIN_KEY`.
