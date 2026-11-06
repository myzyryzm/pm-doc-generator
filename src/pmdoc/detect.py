"""
detect.py — guess a repo's kind from what is on disk.

A project config can name each repo's kind; when it doesn't, this decides. The
kind only selects prompt guidance and trace order, so a wrong guess degrades a
trace rather than breaking it — but the evidence is always printed, so a human
can see why and override it in the YAML.

Checked in order: frontend, worker, backend. A repo that is both an API and a
scheduler (one serverless.yml with http AND schedule events) is a backend: the
mapping prompt inventories processes in every kind of repo anyway.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

FRONTEND_DEPS = {"react", "react-dom", "vue", "@angular/core", "next", "nuxt", "svelte",
                 "@sveltejs/kit", "solid-js", "preact", "react-native", "expo"}
SERVER_DEPS = {"express", "fastify", "koa", "@nestjs/core", "@hapi/hapi", "hapi",
               "apollo-server", "@apollo/server", "hono"}
WORKER_DEPS = {"bull", "bullmq", "agenda", "node-cron", "bee-queue", "kue"}
PY_SERVER_DEPS = ("fastapi", "flask", "django", "starlette", "aiohttp", "sanic", "tornado")
PY_WORKER_DEPS = ("celery", "rq", "dramatiq", "apscheduler")

_SKIP_DIRS = {"node_modules", ".git", "venv", ".venv", "dist", "build", "__pycache__",
              "commonPackages", "vendor", ".next"}

_SLS_HTTP_RE = re.compile(r"^\s*-?\s*(http|httpApi)\s*:", re.MULTILINE)
_SLS_ASYNC_RE = re.compile(r"^\s*-?\s*(schedule|sqs|stream|eventBridge|sns|s3|kafka)\s*:",
                           re.MULTILINE)
_PY_ROUTE_RE = re.compile(r"@(app|router|bp|blueprint)\.(route|get|post|put|patch|delete)\(")


def _walk(root: Path, max_depth: int = 4):
    """Yield files under root, skipping dependency/build dirs, to a bounded depth."""
    stack = [(root, 0)]
    while stack:
        d, depth = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for p in entries:
            if p.is_dir():
                if p.name not in _SKIP_DIRS and not p.name.startswith(".") and depth < max_depth:
                    stack.append((p, depth + 1))
            else:
                yield p


def _package_deps(root: Path) -> set[str]:
    deps: set[str] = set()
    for pkg in [root / "package.json", *root.glob("*/package.json")]:
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            deps.update((data.get(key) or {}).keys())
    return deps


def _python_deps_text(root: Path) -> str:
    chunks = []
    for name in ("requirements.txt", "pyproject.toml", "Pipfile", "setup.py", "setup.cfg"):
        for p in [root / name, *root.glob(f"*/{name}")]:
            try:
                chunks.append(p.read_text(encoding="utf-8").lower())
            except OSError:
                pass
    return "\n".join(chunks)


def _serverless_files(root: Path) -> list[Path]:
    return [p for p in _walk(root, max_depth=2)
            if p.name in ("serverless.yml", "serverless.yaml", "serverless.ts")]


def detect_kind(path: Path | str) -> tuple[str, list[str]]:
    """Return (kind, evidence). Kind is one of frontend/backend/worker/unknown."""
    root = Path(path)
    deps = _package_deps(root)
    pytext = _python_deps_text(root)

    fe = sorted(deps & FRONTEND_DEPS)
    srv = sorted(deps & SERVER_DEPS)
    if fe and not srv:
        return "frontend", [f"package.json depends on {', '.join(fe[:3])}"]

    sls_http, sls_async = [], []
    for f in _serverless_files(root):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = f.relative_to(root)
        if n := len(_SLS_HTTP_RE.findall(text)):
            sls_http.append(f"{rel}: {n} http events")
        if n := len(_SLS_ASYNC_RE.findall(text)):
            sls_async.append(f"{rel}: {n} async/scheduled events")

    py_srv = [d for d in PY_SERVER_DEPS if re.search(rf"\b{d}\b", pytext)]
    py_wrk = [d for d in PY_WORKER_DEPS if re.search(rf"\b{d}\b", pytext)]
    js_wrk = sorted(deps & WORKER_DEPS)
    procfile = root / "Procfile"
    proc_text = procfile.read_text(encoding="utf-8") if procfile.exists() else ""
    proc_worker_only = "worker:" in proc_text and "web:" not in proc_text

    if (sls_async and not sls_http) or proc_worker_only or \
            ((py_wrk or js_wrk) and not (py_srv or srv or sls_http)):
        ev = sls_async or ([f"Procfile defines worker only"] if proc_worker_only else []) \
            or [f"depends on {', '.join(py_wrk or js_wrk)}"]
        return "worker", ev

    if sls_http:
        return "backend", sls_http + sls_async
    if srv:
        return "backend", [f"package.json depends on {', '.join(srv[:3])}"]
    if py_srv:
        return "backend", [f"python deps include {', '.join(py_srv)}"]
    if (root / "manage.py").exists():
        return "backend", ["manage.py (Django)"]

    route_dirs = [p for p in root.rglob("*") if p.is_dir() and p.name in ("routes", "controllers")
                  and not (set(p.relative_to(root).parts) & _SKIP_DIRS)][:3]
    if route_dirs:
        return "backend", [f"{d.relative_to(root)}/ directory" for d in route_dirs]
    for f in _walk(root, max_depth=3):
        if f.suffix == ".py":
            try:
                if _PY_ROUTE_RE.search(f.read_text(encoding="utf-8", errors="ignore")):
                    return "backend", [f"{f.relative_to(root)}: route decorators"]
            except OSError:
                continue

    if fe:  # frontend deps alongside a server framework, e.g. a Next.js app with express
        return "frontend", [f"package.json depends on {', '.join(fe[:3])} (and {', '.join(srv[:2])})"]
    has_index = (root / "index.html").exists() or (root / "public" / "index.html").exists()
    if has_index and any(f.suffix in (".tsx", ".jsx", ".vue", ".svelte") for f in _walk(root / "src")
                         if (root / "src").is_dir()):
        return "frontend", ["index.html + component sources under src/"]
    return "unknown", ["no framework markers found"]
