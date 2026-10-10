# pylint: disable=line-too-long
"""Centralized prompt loading with environment-variable-configurable directory."""
from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict

import yaml

logger = logging.getLogger(__name__)


_BUILTIN_PROMPT_DIR = Path(__file__).resolve().parent

# Canonical filenames — all consumers reference these instead of hardcoding.
INDEXING_YAML = "indexing.yaml"
RETRIEVAL_PROTOCOLS_YAML = "retrieval_protocols.yaml"
RETRIEVAL_DISCLOSURE_YAML = "retrieval_disclosure.yaml"
EXPERIENCE_YAML = "experience.yaml"
AGENTIC_RETRIEVAL_YAML = "agentic_retrieval.yaml"


def get_prompt_dir() -> Path:
    """Return the external prompts directory set via ``DISPATCH_PROMPT_DIR``.

    Returns ``None`` when the environment variable is not set, indicating
    that only the built-in directory should be used.
    """
    env = os.environ.get("DISPATCH_PROMPT_DIR", "").strip()
    if env:
        return Path(env)
    return _BUILTIN_PROMPT_DIR


@lru_cache(maxsize=8)
def load_prompt_yaml(filename: str) -> Dict:
    """Load and cache a YAML file, with per-file fallback.

    If ``DISPATCH_PROMPT_DIR`` is set and the file exists there, it is
    used.  Otherwise the built-in ``prompts/`` directory is tried.  This
    allows overriding *only* the files you need without copying all three.
    """
    search_dirs: list[Path] = []
    env_dir = os.environ.get("DISPATCH_PROMPT_DIR", "").strip()
    if env_dir:
        search_dirs.append(Path(env_dir))
    search_dirs.append(_BUILTIN_PROMPT_DIR)

    for directory in search_dirs:
        path = directory / filename
        logger.info(f"loading prompt file from {path}")
        if path.is_file():
            logger.info("prompt file loaded: %s (searched: %s)", path, ", ".join(str(d) for d in search_dirs))
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError(f"invalid prompt yaml: {path}")
            return raw

    searched = ", ".join(str(d) for d in search_dirs)
    raise FileNotFoundError(f"prompt file {filename!r} not found in: {searched}")


# ---------------------------------------------------------------------------
# Format-template preparation
# ---------------------------------------------------------------------------

_FORMAT_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

_SENTINEL_FMT = "__PROMPT_PH_{i}__"


def _prepare_format_template(raw: str) -> str:
    """Convert a YAML-loaded prompt into a Python ``str.format()`` template.

    * Format placeholders like ``{variable_name}`` are preserved as-is.
    * All other braces (literal JSON braces) are doubled so that
      ``str.format()`` outputs a single brace.
    """
    # 1. Temporarily replace format placeholders with sentinel markers.
    placeholders: list[str] = []

    def _save_placeholder(m: re.Match) -> str:
        placeholders.append(m.group(0))
        return _SENTINEL_FMT.format(i=len(placeholders) - 1)

    interim = _FORMAT_PLACEHOLDER_RE.sub(_save_placeholder, raw)

    # 2. Double all remaining braces (these are literal braces in the output).
    interim = interim.replace("{", "{{").replace("}", "}}")

    # 3. Restore format placeholders.
    for i, ph in enumerate(placeholders):
        interim = interim.replace(_SENTINEL_FMT.format(i=i), ph)

    return interim


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_prompt(filename: str, *keys: str) -> str:
    """Retrieve a single prompt string by navigating the YAML dict.

    The returned string is ready for ``str.format(**kwargs)`` — literal
    braces are already doubled and ``{variable}`` placeholders are preserved.
    """
    data = load_prompt_yaml(filename)
    node = data
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            raise KeyError(f"missing key {key!r} in {filename}")
        node = node[key]
    result = str(node).rstrip("\n")
    if not result:
        raise ValueError(f"empty prompt at {'.'.join(keys)} in {filename}")
    return _prepare_format_template(result)


def clear_cache() -> None:
    """Clear the YAML loading cache (useful for testing or hot-reload)."""
    load_prompt_yaml.cache_clear()
