import os
import re
import json
import sqlite3
import logging
import threading
import subprocess
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "projects.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


PRESET_TAGS = [
    "docker", "ai", "workflow", "api", "frontend", "backend",
    "database", "monitoring", "testing", "devops", "mcp",
    "auto-discovered", "web", "cli", "microservice",
]


SCAN_INTERVAL = 120

logger = logging.getLogger("dashboard")


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            github_url TEXT DEFAULT '',
            description TEXT DEFAULT '',
            local_path TEXT DEFAULT '',
            category TEXT DEFAULT 'opensource',
            status TEXT DEFAULT 'stopped',
            port TEXT DEFAULT '',
            start_command TEXT DEFAULT '',
            tags TEXT DEFAULT '[]',
            notes TEXT DEFAULT '',
            access_url TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pending_services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            port TEXT NOT NULL UNIQUE,
            process_name TEXT DEFAULT '',
            command TEXT DEFAULT '',
            local_path TEXT DEFAULT '',
            github_url TEXT DEFAULT '',
            source TEXT DEFAULT 'process',
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            dismissed INTEGER DEFAULT 0
        );
    """)
    try:
        conn.execute("ALTER TABLE projects ADD COLUMN access_url TEXT DEFAULT ''")
        conn.commit()
    except Exception:
        pass
    conn.close()


def row_to_dict(row):
    d = dict(row)
    if "tags" in d:
        try:
            d["tags"] = json.loads(d["tags"])
        except (json.JSONDecodeError, TypeError):
            d["tags"] = []
    return d


def check_port_in_use(port):
    """Check if a port is in use (project likely running)."""
    if not port:
        return False
    try:
        result = subprocess.run(
            ["lsof", "-i", f":{port}"],
            capture_output=True, text=True, timeout=3
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/projects", methods=["GET"])
def list_projects():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM projects ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    projects = [row_to_dict(r) for r in rows]
    for p in projects:
        if p.get("port"):
            p["port_active"] = check_port_in_use(p["port"])
        else:
            p["port_active"] = False
    return jsonify(projects)


@app.route("/api/projects", methods=["POST"])
def create_project():
    data = request.json
    now = datetime.now().isoformat()
    tags = json.dumps(data.get("tags", []))
    conn = get_db()
    access_url = data.get("access_url", "")
    if not access_url and data.get("port"):
        access_url = f"http://localhost:{data['port']}"
    cursor = conn.execute(
        """INSERT INTO projects
           (name, github_url, description, local_path, category, status,
            port, start_command, tags, notes, access_url, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            data.get("name", ""),
            data.get("github_url", ""),
            data.get("description", ""),
            data.get("local_path", ""),
            data.get("category", "opensource"),
            data.get("status", "stopped"),
            data.get("port", ""),
            data.get("start_command", ""),
            tags,
            data.get("notes", ""),
            access_url,
            now,
            now,
        ),
    )
    conn.commit()
    project_id = cursor.lastrowid
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    return jsonify(row_to_dict(row)), 201


@app.route("/api/projects/<int:project_id>", methods=["PUT"])
def update_project(project_id):
    data = request.json
    now = datetime.now().isoformat()
    conn = get_db()
    existing = conn.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not existing:
        conn.close()
        return jsonify({"error": "Project not found"}), 404

    existing = dict(existing)
    tags = json.dumps(data.get("tags", json.loads(existing.get("tags", "[]"))))
    conn.execute(
        """UPDATE projects SET
           name=?, github_url=?, description=?, local_path=?, category=?,
           status=?, port=?, start_command=?, tags=?, notes=?, access_url=?,
           updated_at=? WHERE id=?""",
        (
            data.get("name", existing["name"]),
            data.get("github_url", existing["github_url"]),
            data.get("description", existing["description"]),
            data.get("local_path", existing["local_path"]),
            data.get("category", existing["category"]),
            data.get("status", existing["status"]),
            data.get("port", existing["port"]),
            data.get("start_command", existing["start_command"]),
            tags,
            data.get("notes", existing["notes"]),
            data.get("access_url", existing.get("access_url", "")),
            now,
            project_id,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    return jsonify(row_to_dict(row))


@app.route("/api/projects/<int:project_id>", methods=["DELETE"])
def delete_project(project_id):
    conn = get_db()
    conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/projects/<int:project_id>/status", methods=["PATCH"])
def update_status(project_id):
    data = request.json
    now = datetime.now().isoformat()
    conn = get_db()
    conn.execute(
        "UPDATE projects SET status=?, updated_at=? WHERE id=?",
        (data["status"], now, project_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    return jsonify(row_to_dict(row))


SERVER_RUNTIMES = {
    "python", "python3", "python2", "python3.",
    "node", "npm", "npx", "tsx", "ts-node", "deno", "bun",
    "java", "javaw", "kotlin", "gradle", "mvn", "mvnw",
    "ruby", "rails", "puma", "unicorn", "bundle",
    "go", "air",
    "rust", "cargo",
    "php", "php-fpm",
    "nginx", "httpd", "apache2", "caddy", "traefik",
    "redis-server", "redis-se",
    "postgres", "pg_ctl", "mysqld", "mysql", "mongod", "mongos",
    "docker-pr", "containerd", "dockerd",
    "uvicorn", "gunicorn", "flask", "django", "fastapi",
    "next", "vite", "webpack", "esbuild",
    "pm2", "supervisord", "foreman",
    "streamlit", "jupyter", "jupyter-l", "jupyter-n",
    "grafana", "prometheus", "clickhouse",
    "ollama", "litellm",
    "n8n", "minio", "vault", "consul", "etcd",
}

PROJECT_MARKERS = {
    ".git", "package.json", "docker-compose.yml", "docker-compose.yaml",
    "Dockerfile", "requirements.txt", "pyproject.toml", "setup.py",
    "go.mod", "Cargo.toml", "Gemfile", "pom.xml", "build.gradle",
    "Makefile", "Procfile", "tsconfig.json", "manage.py",
    "compose.yml", "compose.yaml",
}

NON_PROJECT_PATHS = {"/", "/usr", "/var", "/tmp", "/private", ""}
HOME_DIR = os.path.expanduser("~")

SERVICE_NAME_KEYWORDS = {
    "server", "agent", "mcp", "proxy", "gateway", "daemon",
    "api", "service", "worker", "broker", "relay",
}

SELF_PORT = "9800"


def _looks_like_service_binary(cmd):
    """Check if the binary name contains keywords suggesting it's a deployed service."""
    if not cmd:
        return False
    binary = cmd.split()[0].split("/")[-1].lower()
    return any(kw in binary for kw in SERVICE_NAME_KEYWORDS)


def _is_macos_app(cmd):
    """Check if command is a macOS .app bundle (desktop app, not a service)."""
    if not cmd:
        return False
    binary = cmd.split()[0] if cmd.split() else ""
    return ".app/" in binary or binary.startswith("/Applications/")


def _get_runtime_from_cmd(cmd):
    """Extract the runtime name from the full command line."""
    if not cmd:
        return None
    binary = cmd.split()[0].split("/")[-1].lower()
    for rt in SERVER_RUNTIMES:
        if binary == rt or binary.startswith(rt):
            return rt
    return None


def _is_project_dir(path):
    """Check if path looks like a project directory (has project marker files)."""
    if not path or path in NON_PROJECT_PATHS:
        return False
    norm = path.rstrip("/")
    if norm == HOME_DIR or norm in NON_PROJECT_PATHS:
        return False
    if path.startswith("/Applications/") or path.startswith("/System/"):
        return False
    try:
        for marker in PROJECT_MARKERS:
            if os.path.exists(os.path.join(path, marker)):
                return True
    except OSError:
        pass
    return False


def _should_include_service(proc_name, cmd, cwd):
    """
    Decide if a listening process is a developer-deployed service.
    Layers:
      1. Known server runtime (python, node, java...) -> keep
      2. macOS .app bundle -> skip
      3. Binary name contains service keywords (agent, mcp, server...) -> keep
      4. Working dir is a real project directory -> keep
    """
    if _get_runtime_from_cmd(cmd):
        return not _is_macos_app(cmd)

    if _is_macos_app(cmd):
        return False

    if _looks_like_service_binary(cmd):
        return True

    return _is_project_dir(cwd)


def _discover_listening_ports():
    """Use lsof to find all TCP LISTEN ports and their processes."""
    try:
        result = subprocess.run(
            ["lsof", "-iTCP", "-sTCP:LISTEN", "-P", "-n"],
            capture_output=True, text=True, timeout=10
        )
        if not result.stdout:
            return []
    except Exception:
        return []

    services = {}
    for line in result.stdout.strip().split("\n")[1:]:
        parts = line.split()
        if len(parts) < 9:
            continue
        proc_name = parts[0]
        pid = parts[1]
        addr_port = parts[8]

        port_match = re.search(r":(\d+)$", addr_port)
        if not port_match:
            continue
        port = port_match.group(1)

        if port == SELF_PORT:
            continue

        key = f"{pid}:{port}"
        if key in services:
            continue

        cwd = _get_process_cwd(pid)
        cmd = _get_process_cmd(pid)

        if not _should_include_service(proc_name, cmd, cwd):
            continue

        github_url = _detect_github_url(cwd) if cwd else ""

        services[key] = {
            "pid": pid,
            "process_name": proc_name,
            "port": port,
            "command": cmd,
            "local_path": cwd,
            "github_url": github_url,
        }

    return list(services.values())


def _discover_docker_containers():
    """Detect running Docker containers with port mappings."""
    try:
        result = subprocess.run(
            ["docker", "ps", "--format",
             "{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Ports}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=10
        )
        if not result.stdout:
            return []
    except Exception:
        return []

    containers = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        container_id, name, image, ports_str, status = parts

        port_matches = re.findall(r"(?:\d+\.\d+\.\d+\.\d+:)?(\d+)->(\d+)", ports_str)
        host_ports = [m[0] for m in port_matches]

        compose_path = _get_docker_compose_path(container_id)
        github_url = _detect_github_url(compose_path) if compose_path else ""

        containers.append({
            "container_id": container_id[:12],
            "name": name,
            "image": image,
            "ports": host_ports,
            "status": status,
            "local_path": compose_path,
            "github_url": github_url,
            "source": "docker",
        })
    return containers


def _get_process_cwd(pid):
    """Get the working directory of a process via lsof."""
    try:
        result = subprocess.run(
            ["lsof", "-p", pid, "-Fn", "-a", "-d", "cwd"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split("\n"):
            if line.startswith("n") and line != "n":
                return line[1:]
    except Exception:
        pass
    return ""


def _get_process_cmd(pid):
    """Get the full command line of a process."""
    try:
        result = subprocess.run(
            ["ps", "-p", pid, "-o", "command="],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _get_docker_compose_path(container_id):
    """Try to find the compose project working dir for a container."""
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format",
             "{{index .Config.Labels \"com.docker.compose.project.working_dir\"}}",
             container_id],
            capture_output=True, text=True, timeout=5
        )
        path = result.stdout.strip()
        if path and path != "<no value>":
            return path
    except Exception:
        pass
    return ""


def _detect_github_url(path):
    """Try to read a git remote origin URL from the given path."""
    if not path:
        return ""
    try:
        result = subprocess.run(
            ["git", "-C", path, "config", "--get", "remote.origin.url"],
            capture_output=True, text=True, timeout=5
        )
        url = result.stdout.strip()
        if not url:
            return ""
        if url.startswith("git@github.com:"):
            url = "https://github.com/" + url[15:]
        if url.endswith(".git"):
            url = url[:-4]
        if "github.com" in url:
            return url
    except Exception:
        pass
    return ""


def _guess_project_name(svc):
    """Derive a human-friendly name from discovered service info."""
    if svc.get("name"):
        return svc["name"]
    if svc.get("local_path"):
        return Path(svc["local_path"]).name
    return svc.get("process_name", f"port-{svc.get('port', '?')}")


@app.route("/api/discover", methods=["GET"])
def discover_services():
    """Auto-discover locally running services and Docker containers."""
    conn = get_db()
    existing_ports = set()
    rows = conn.execute("SELECT port FROM projects WHERE port != ''").fetchall()
    conn.close()
    for r in rows:
        for p in str(r["port"]).split(","):
            existing_ports.add(p.strip())

    port_services = _discover_listening_ports()
    docker_containers = _discover_docker_containers()

    results = []
    seen_ports = set()

    for svc in port_services:
        port = svc["port"]
        if port in seen_ports:
            continue
        seen_ports.add(port)
        results.append({
            "name": _guess_project_name(svc),
            "port": port,
            "pid": svc["pid"],
            "process_name": svc["process_name"],
            "command": svc["command"],
            "local_path": svc["local_path"],
            "github_url": svc["github_url"],
            "source": "process",
            "already_tracked": port in existing_ports,
        })

    for ctn in docker_containers:
        for port in ctn["ports"]:
            if port in seen_ports:
                continue
            seen_ports.add(port)
            results.append({
                "name": ctn["name"],
                "port": port,
                "image": ctn["image"],
                "container_id": ctn["container_id"],
                "local_path": ctn["local_path"],
                "github_url": ctn["github_url"],
                "source": "docker",
                "already_tracked": port in existing_ports,
            })

    results.sort(key=lambda x: (x["already_tracked"], int(x["port"])))
    return jsonify(results)


@app.route("/api/discover/import", methods=["POST"])
def import_discovered():
    """Batch-import discovered services as projects."""
    items = request.json
    if not isinstance(items, list):
        return jsonify({"error": "Expected a list"}), 400

    now = datetime.now().isoformat()
    conn = get_db()
    imported = []
    for item in items:
        name = item.get("name", "")
        if not name:
            continue
        tags = ["auto-discovered"]
        if item.get("source") == "docker":
            tags.append("docker")
        cursor = conn.execute(
            """INSERT INTO projects
               (name, github_url, description, local_path, category, status,
                port, start_command, tags, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                item.get("github_url", ""),
                item.get("description", ""),
                item.get("local_path", ""),
                "opensource",
                "running",
                item.get("port", ""),
                item.get("command", ""),
                json.dumps(tags),
                f"Auto-discovered from {item.get('source', 'scan')}",
                now,
                now,
            ),
        )
        imported.append(cursor.lastrowid)
    conn.commit()
    conn.close()
    return jsonify({"imported": len(imported), "ids": imported}), 201


@app.route("/api/categories", methods=["GET"])
def list_categories():
    conn = get_db()
    rows = conn.execute(
        "SELECT DISTINCT category FROM projects ORDER BY category"
    ).fetchall()
    conn.close()
    categories = [r["category"] for r in rows]
    defaults = ["opensource", "self-developed", "tool", "learning"]
    for d in defaults:
        if d not in categories:
            categories.append(d)
    return jsonify(sorted(categories))


@app.route("/api/tags", methods=["GET"])
def list_tags():
    conn = get_db()
    rows = conn.execute("SELECT tags FROM projects").fetchall()
    conn.close()
    used = set()
    for r in rows:
        try:
            for t in json.loads(r["tags"]):
                used.add(t)
        except (json.JSONDecodeError, TypeError):
            pass
    all_tags = sorted(set(PRESET_TAGS) | used)
    return jsonify(all_tags)


@app.route("/api/pending", methods=["GET"])
def list_pending():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM pending_services WHERE dismissed = 0 ORDER BY last_seen DESC"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/pending/<int:pending_id>/import", methods=["POST"])
def import_pending(pending_id):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM pending_services WHERE id = ?", (pending_id,)
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Not found"}), 404

    svc = dict(row)
    now = datetime.now().isoformat()
    tags = ["auto-discovered"]
    if svc["source"] == "docker":
        tags.append("docker")
    access_url = f"http://localhost:{svc['port']}" if svc["port"] else ""
    conn.execute(
        """INSERT INTO projects
           (name, github_url, description, local_path, category, status,
            port, start_command, tags, notes, access_url, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            svc["name"], svc["github_url"], "", svc["local_path"],
            "opensource", "running", svc["port"], svc["command"],
            json.dumps(tags),
            f"Auto-discovered from {svc['source']}",
            access_url, now, now,
        ),
    )
    conn.execute("DELETE FROM pending_services WHERE id = ?", (pending_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/pending/<int:pending_id>/dismiss", methods=["POST"])
def dismiss_pending(pending_id):
    conn = get_db()
    conn.execute(
        "UPDATE pending_services SET dismissed = 1 WHERE id = ?", (pending_id,)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/pending/import-all", methods=["POST"])
def import_all_pending():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM pending_services WHERE dismissed = 0"
    ).fetchall()
    now = datetime.now().isoformat()
    count = 0
    for row in rows:
        svc = dict(row)
        tags = ["auto-discovered"]
        if svc["source"] == "docker":
            tags.append("docker")
        access_url = f"http://localhost:{svc['port']}" if svc["port"] else ""
        conn.execute(
            """INSERT INTO projects
               (name, github_url, description, local_path, category, status,
                port, start_command, tags, notes, access_url, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                svc["name"], svc["github_url"], "", svc["local_path"],
                "opensource", "running", svc["port"], svc["command"],
                json.dumps(tags),
                f"Auto-discovered from {svc['source']}",
                access_url, now, now,
            ),
        )
        count += 1
    conn.execute("DELETE FROM pending_services WHERE dismissed = 0")
    conn.commit()
    conn.close()
    return jsonify({"imported": count})


def _background_scan():
    """Periodic scan that feeds newly discovered services into pending_services."""
    while True:
        threading.Event().wait(SCAN_INTERVAL)
        try:
            _run_scan()
        except Exception as e:
            logger.warning("Background scan error: %s", e)


def _run_scan():
    conn = get_db()
    tracked_ports = set()
    for r in conn.execute("SELECT port FROM projects WHERE port != ''").fetchall():
        for p in str(r["port"]).split(","):
            tracked_ports.add(p.strip())
    for r in conn.execute(
        "SELECT port FROM pending_services WHERE dismissed = 0"
    ).fetchall():
        tracked_ports.add(r["port"])
    dismissed_ports = set()
    for r in conn.execute(
        "SELECT port FROM pending_services WHERE dismissed = 1"
    ).fetchall():
        dismissed_ports.add(r["port"])

    port_services = _discover_listening_ports()
    docker_containers = _discover_docker_containers()

    now = datetime.now().isoformat()
    seen_ports = set()
    new_count = 0

    for svc in port_services:
        port = svc["port"]
        if port in seen_ports or port in tracked_ports or port in dismissed_ports:
            continue
        seen_ports.add(port)
        name = _guess_project_name(svc)
        try:
            conn.execute(
                """INSERT INTO pending_services
                   (name, port, process_name, command, local_path,
                    github_url, source, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, port, svc["process_name"], svc["command"],
                 svc["local_path"], svc["github_url"], "process", now, now),
            )
            new_count += 1
        except sqlite3.IntegrityError:
            conn.execute(
                "UPDATE pending_services SET last_seen=?, name=?, command=? WHERE port=?",
                (now, name, svc["command"], port),
            )

    for ctn in docker_containers:
        for port in ctn["ports"]:
            if port in seen_ports or port in tracked_ports or port in dismissed_ports:
                continue
            seen_ports.add(port)
            try:
                conn.execute(
                    """INSERT INTO pending_services
                       (name, port, process_name, command, local_path,
                        github_url, source, first_seen, last_seen)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (ctn["name"], port, "", "", ctn["local_path"],
                     ctn["github_url"], "docker", now, now),
                )
                new_count += 1
            except sqlite3.IntegrityError:
                conn.execute(
                    "UPDATE pending_services SET last_seen=? WHERE port=?",
                    (now, port),
                )

    conn.commit()
    conn.close()
    if new_count:
        logger.info("Background scan: found %d new service(s)", new_count)


def start_background_scanner():
    t = threading.Thread(target=_background_scan, daemon=True)
    t.start()
    logger.info("Background scanner started (interval=%ds)", SCAN_INTERVAL)


if __name__ == "__main__":
    init_db()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(message)s")
    _run_scan()
    start_background_scanner()
    app.run(host="0.0.0.0", port=9800, debug=True, use_reloader=False)
