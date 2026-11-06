"""Project config loading and repo-kind auto-detection."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import ROOT  # noqa: F401  (sets sys.path)
from pmdoc.detect import detect_kind
from pmdoc.project import ProjectError, load_project


def project_folder(path):
    """Point PROJECT_FOLDER at `path` for one test (None = unset)."""
    env = {k: v for k, v in os.environ.items() if k != "PROJECT_FOLDER"}
    if path is not None:
        env["PROJECT_FOLDER"] = str(path)
    return mock.patch.dict(os.environ, env, clear=True)


class ProjectTest(unittest.TestCase):
    def setUp(self):
        env = project_folder(None)      # isolate from the developer's .env
        env.start()
        self.addCleanup(env.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        (self.tmp / "web").mkdir()
        (self.tmp / "web" / "package.json").write_text(json.dumps({"dependencies": {"react": "18"}}))
        (self.tmp / "api").mkdir()

    def write(self, text: str) -> Path:
        p = self.tmp / "proj.yaml"
        p.write_text(text)
        return p

    def test_loads_paths_kinds_and_defaults(self):
        cfg = self.write(f"""
name: mini
description: A tiny app.
repos:
  - name: api
    path: {self.tmp / 'api'}
    kind: backend
    hints: routes in app.py
  - path: {self.tmp / 'web'}
""")
        p = load_project(str(cfg))
        self.assertEqual(p.name, "mini")
        self.assertEqual([r.name for r in p.repos], ["api", "web"])   # name defaults to dir name
        self.assertEqual(p.repos[0].kind_source, "config")
        self.assertEqual(p.repos[1].kind, "frontend")
        self.assertTrue(p.repos[1].kind_source.startswith("detected"))
        self.assertEqual(p.api_prefixes, [])
        self.assertEqual(p.collect_out_dir, "corpus")

    def test_relative_paths_resolve_against_project_folder(self):
        cfg = self.write("name: rel\nrepos:\n  - path: api\n    kind: backend\n")
        with project_folder(self.tmp):
            p = load_project(str(cfg))
        self.assertEqual(p.repos[0].path, (self.tmp / "api").resolve())
        self.assertEqual(p.folder, self.tmp.resolve())
        self.assertEqual(p.display_path(p.repos[0].path), "api")
        self.assertEqual(p.display_path(Path("/elsewhere/x")), "/elsewhere/x")

    def test_relative_path_needs_project_folder(self):
        cfg = self.write("name: rel\nrepos:\n  - path: api\n")
        with project_folder(None), self.assertRaisesRegex(ProjectError, "PROJECT_FOLDER"):
            load_project(str(cfg))
        with project_folder(self.tmp / "missing"), self.assertRaisesRegex(ProjectError, "not a directory"):
            load_project(str(cfg))

    def test_absolute_paths_ignore_project_folder(self):
        cfg = self.write(f"name: abs\nrepos:\n  - path: {self.tmp / 'api'}\n    kind: backend\n")
        with project_folder(None):
            self.assertEqual(load_project(str(cfg)).repos[0].path, (self.tmp / "api").resolve())

    def test_only_repos_filters_and_rejects_unknown(self):
        cfg = self.write(f"name: mini\nrepos:\n  - name: api\n    path: {self.tmp/'api'}\n"
                         f"  - name: web\n    path: {self.tmp/'web'}\n")
        self.assertEqual([r.name for r in load_project(str(cfg), ["web"]).repos], ["web"])
        with self.assertRaises(ProjectError):
            load_project(str(cfg), ["nope"])

    def test_errors(self):
        with self.assertRaises(ProjectError):
            load_project("definitely-not-a-project")
        for bad in ("name: x\nrepos: []\n",
                    f"name: x\nrepos:\n  - path: {self.tmp/'missing'}\n",
                    f"name: x\nrepos:\n  - path: {self.tmp/'api'}\n    kind: mobile\n",
                    f"name: my_app\nrepos:\n  - path: {self.tmp/'api'}\n"):
            with self.subTest(bad=bad), self.assertRaises(ProjectError):
                load_project(str(self.write(bad)))

    def test_shop_app_config_loads(self):
        (self.tmp / "shop-api").mkdir()
        (self.tmp / "shop-web").mkdir()
        with project_folder(self.tmp):
            p = load_project("shop-app")
        self.assertEqual({r.name: r.kind for r in p.repos}, {"shop-api": "backend", "shop-web": "frontend"})
        self.assertEqual(p.repos[0].path, (self.tmp / "shop-api").resolve())
        self.assertEqual(p.api_prefixes, ["/api"])


class DetectTest(unittest.TestCase):
    def make(self, files: dict[str, str]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
        return root

    def test_kinds(self):
        cases = {
            "frontend": {"package.json": json.dumps({"dependencies": {"vue": "3"}})},
            "backend": {"serverless.yml": "functions:\n  a:\n    events:\n      - http:\n          path: /x\n"},
            "worker": {"serverless.yml": "functions:\n  a:\n    events:\n      - schedule: rate(1 day)\n"},
            "unknown": {"README.md": "hello"},
        }
        for kind, files in cases.items():
            with self.subTest(kind=kind):
                self.assertEqual(detect_kind(self.make(files))[0], kind)

    def test_express_with_react_is_backend(self):
        root = self.make({"package.json": json.dumps({"dependencies": {"express": "4", "react": "18"}})})
        self.assertEqual(detect_kind(root)[0], "backend")

    def test_python_framework(self):
        self.assertEqual(detect_kind(self.make({"requirements.txt": "fastapi==0.1\n"}))[0], "backend")


if __name__ == "__main__":
    unittest.main()
