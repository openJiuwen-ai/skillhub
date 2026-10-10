from __future__ import annotations

__all__ = [
    "DefaultSubtreeRenderer",
    "DisclosureConfig",
    "ExposedFragment",
    "ExposedNode",
    "SelectableResolution",
    "build_disclosure_messages",
    "build_exposed_fragment",
    "parse_selected_codes",
]


def __getattr__(name: str):
    if name == "DefaultSubtreeRenderer":
        from .default import DefaultSubtreeRenderer

        return DefaultSubtreeRenderer
    if name in {
        "DisclosureConfig",
        "ExposedFragment",
        "ExposedNode",
        "SelectableResolution",
        "build_disclosure_messages",
        "build_exposed_fragment",
        "parse_selected_codes",
    }:
        from . import disclosure

        return getattr(disclosure, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
