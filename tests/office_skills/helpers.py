"""Office skills test suite.

Black-box tests: run each skill's scripts via subprocess (CLI contract),
assert on produced files with hand-derived expected values.
Tests never import skill implementation modules.
"""
import os
import subprocess
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SKILLS_OFFICE = os.path.join(REPO_ROOT, "skills", "office")
TESTS_TMP = os.environ.get("OFFICE_SKILLS_TMP")


def skill_dir(name):
    d = os.path.join(SKILLS_OFFICE, name)
    if not os.path.isdir(d):
        raise FileNotFoundError(f"skill dir not found: {d}")
    return d


def run_script(skill, script, args, cwd=None, timeout=120):
    """Run a skill script as a subprocess. Returns CompletedProcess."""
    path = os.path.join(skill_dir(skill), "scripts", script)
    cmd = [sys.executable, path] + [str(a) for a in args]
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"  # 统一子进程 stdio 为 UTF-8（Windows 默认 cp936）
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        cwd=cwd or os.path.dirname(path), timeout=timeout, env=env,
    )


def run_python(code, cwd=None):
    """Run inline python (for independent verification logic)."""
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        encoding="utf-8", cwd=cwd, timeout=120,
    )
