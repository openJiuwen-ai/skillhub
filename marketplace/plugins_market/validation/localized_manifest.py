# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared helpers for localized manifest fields."""

from __future__ import annotations

from typing import Any


def localized_manifest_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for locale in ("zh", "en"):
            candidate = value.get(locale)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return ""


def localized_manifest_tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        label = localized_manifest_text(item)
        if label and label not in seen:
            seen.add(label)
            result.append(label)
    return result


def localized_manifest_examples(value: Any) -> list[str]:
    """Flatten manifest examples (locale map or list) into display strings."""
    if isinstance(value, dict):
        result: list[str] = []
        for locale in ("zh", "en"):
            items = value.get(locale)
            if isinstance(items, list):
                for item in items:
                    text = localized_manifest_text(item)
                    if text:
                        result.append(text)
        if result:
            return result
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            text = localized_manifest_text(item)
            if text:
                result.append(text)
        return result
    return []
