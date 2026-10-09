#!/usr/bin/env python3
"""
Fan-site server: serves the website AND stores form signups in SQLite.
Zero dependencies — only Python 3.8+ standard library.

Run:   python server.py        (then open http://localhost:8000)
Data:  data/fans.db            (one portable file, copy it to move your data)
"""
import csv, io, json, os, re, secrets, shutil, sqlite3, sys, threading, time
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

BASE = Path(__file__).resolve().parent
PUBLIC = BASE / "public"
DATA = BASE / "data"
BACKUPS = BASE / "backups"
DB_PATH = DATA / "fans.db"
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))
MAX_BODY = 10 * 1024
KINDS = {
    "Get the fan digest": "digest", "digest": "digest",
    "Suggest a video topic": "topic", "topic": "topic",
    "Share a favourite moment": "moment", "moment": "moment",
}
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
MIME = {".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript",
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml",
        ".ico": "image/x-icon", ".webp": "image/webp", ".json": "application/json", ".txt": "text/plain"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS fan_signups (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  name       TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 100),
  email      TEXT NOT NULL CHECK (length(email) <= 254 AND email LIKE '%_@_%._%'),
  kind       TEXT NOT NULL DEFAULT 'digest' CHECK (kind IN ('digest','topic','moment')),
  message    TEXT CHECK (message IS NULL OR length(message) <= 500),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_signup_email_kind ON fan_signups (lower(email), kind);
CREATE INDEX IF NOT EXISTS idx_signups_created ON fan_signups (created_at DESC);
"""

write_lock = threading.Lock()


def connect():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")      # crash-safe, readers don't block writers
    c.execute("PRAGMA synchronous=FULL")      # fsync every commit: no lost data on power loss
    c.execute("PRAGMA busy_timeout=30000")
    return c


def init_db():
    DATA.mkdir(exist_ok=True)
    with connect() as c:
        c.executescript(SCHEMA)
        ok = c.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            print("WARNING: database integrity check failed:", ok, file=sys.stderr)


def get_admin_key():
    env = os.environ.get("ADMIN_KEY")
    if env:
        return env
    f = DATA / "admin_key.txt"
    if not f.exists():
        f.write_text(secrets.token_urlsafe(18))
    return f.read_text().strip()


ADMIN_KEY = None


def backup_once():
    """Consistent online backup using SQLite's backup API. Keeps the newest 14."""
    BACKUPS.mkdir(exist_ok=True)
    name = BACKUPS / f"fans-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.db"
    src = connect()
    dst = sqlite3.connect(name)
    with dst:
        src.backup(dst)
    src.close(); dst.close()
    for old in sorted(BACKUPS.glob("fans-*.db"))[:-14]:
        old.unlink(missing_ok=True)


def backup_loop():
    while True:
        try:
            backup_once()
        except Exception as e:
            print("backup failed:", e, file=sys.stderr)
        time.sleep(6 * 3600)


# simple per-IP rate limit: 10 signup requests / minute
hits = {}
def rate_limited(ip):
    now = time.time()
    q = [t for t in hits.get(ip, []) if now - t < 60]
    q.append(now)
    hits[ip] = q
    return len(q) > 10


def csv_safe(v):
    v = "" if v is None else str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


class Handler(BaseHTTPRequestHandler):
    server_version = "FanSite/1.0"

    def log_message(self, fmt, *a):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % a))

    def _send(self, code, body=b"", ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store" if ctype == "application/json" else "no-cache")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _authed(self, qs):
        key = (qs.get("key", [""])[0] or self.headers.get("X-Admin-Key", ""))
        return secrets.compare_digest(key, ADMIN_KEY)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if u.path == "/api/health":
            try:
                with connect() as c:
                    n = c.execute("SELECT count(*) FROM fan_signups").fetchone()[0]
                return self._send(200, {"ok": True, "signups": n})
            except Exception:
                return self._send(500, {"ok": False})
        if u.path in ("/api/export.csv", "/api/signups"):
            if not self._authed(qs):
                return self._send(401, {"error": "Unauthorized"})
            with connect() as c:
                rows = c.execute("SELECT id,name,email,kind,message,created_at,updated_at "
                                 "FROM fan_signups ORDER BY id").fetchall()
            if u.path == "/api/signups":
                keys = ["id", "name", "email", "kind", "message", "created_at", "updated_at"]
                return self._send(200, [dict(zip(keys, r)) for r in rows])
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["id", "name", "email", "kind", "message", "created_at", "updated_at"])
            for r in rows:
                w.writerow([csv_safe(x) for x in r])
            return self._send(200, "\ufeff" + buf.getvalue(), "text/csv; charset=utf-8",
                              {"Content-Disposition": 'attachment; filename="fan_signups.csv"'})
        # static files (only inside /public)
        rel = u.path.lstrip("/") or "index.html"
        f = (PUBLIC / rel).resolve()
        if PUBLIC.resolve() not in f.parents and f != PUBLIC.resolve():
            return self._send(403, {"error": "Forbidden"})
        if f.is_dir():
            f = f / "index.html"
        if not f.is_file():
            return self._send(404, {"error": "Not found"})
        self._send(200, f.read_bytes(), MIME.get(f.suffix.lower(), "application/octet-stream"))

    def do_POST(self):
        if urlparse(self.path).path != "/api/signup":
            return self._send(404, {"error": "Not found"})
        ip = self.client_address[0]
        if rate_limited(ip):
            return self._send(429, {"error": "Too many requests. Please wait a minute."})
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = -1
        if n <= 0 or n > MAX_BODY:
            return self._send(413 if n > 0 else 400, {"error": "Invalid request size"})
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8"))
            assert isinstance(data, dict)
        except Exception:
            return self._send(400, {"error": "Invalid JSON"})

        if data.get("website"):                      # honeypot: pretend success, store nothing
            return self._send(200, {"ok": True})
        name = str(data.get("name", "")).strip()
        email = str(data.get("email", "")).strip().lower()
        kind = KINDS.get(str(data.get("kind", "")))
        msg = str(data.get("msg", "")).strip()
        if not name or len(name) > 100:
            return self._send(400, {"error": "Please enter your name (max 100 characters)."})
        if len(email) > 254 or not EMAIL_RE.match(email):
            return self._send(400, {"error": "Please enter a valid email."})
        if not kind:
            return self._send(400, {"error": "Invalid option selected."})
        if len(msg) > 500:
            return self._send(400, {"error": "Message must be 500 characters or fewer."})

        try:
            with write_lock, connect() as c:
                c.execute(
                    """INSERT INTO fan_signups (name,email,kind,message) VALUES (?,?,?,?)
                       ON CONFLICT(lower(email),kind) DO UPDATE SET
                         name=excluded.name, message=excluded.message,
                         updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
                    (name, email, kind, msg or None))
            self._send(200, {"ok": True})
        except Exception as e:
            print("db error:", e, file=sys.stderr)
            self._send(500, {"error": "Server error. Please try again."})


def main():
    global ADMIN_KEY
    init_db()
    ADMIN_KEY = get_admin_key()
    backup_once()
    threading.Thread(target=backup_loop, daemon=True).start()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"\n  Site running:   http://localhost:{PORT}")
    print(f"  Export CSV:     http://localhost:{PORT}/api/export.csv?key={ADMIN_KEY}")
    print(f"  Database file:  {DB_PATH}\n  (Ctrl+C to stop)\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
