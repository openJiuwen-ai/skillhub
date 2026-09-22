from __future__ import annotations

import time

from plugins_market.validation.plugin_yaml import safe_load_yaml


def test_one_mib_plugin_yaml_parses_quickly() -> None:
    padding = "note: " + ("n" * (1024 * 1024 - 256)) + "\n"
    text = (
        "name: demo-skill\nversion: 1.0.0\ndisplay_name: Demo\n"
        "description: demo skill\nruntime:\n  type: skill\nmetadata:\n"
        "  author: tester\n  tags: []\n" + padding
    )
    started = time.perf_counter()
    data = safe_load_yaml(text, context="plugin.yaml")
    elapsed = time.perf_counter() - started
    assert data["name"] == "demo-skill"
    assert elapsed < 5.0
