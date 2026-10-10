from __future__ import annotations

import base64
import difflib
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unittest


PRE_REFACTOR_REV = "HEAD^"
SCENARIO_ENV = "REFACTOR_MESSAGE_COMPAT_SCENARIO"
LOGGER = logging.getLogger("refactor_message_compatibility")
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False
if not LOGGER.handlers:
    _REPORT_HANDLER = logging.StreamHandler()
    _REPORT_HANDLER.setFormatter(logging.Formatter("%(message)s"))
    LOGGER.addHandler(_REPORT_HANDLER)

DISPATCH_PACKAGES = (
    "marketplace/dispatch/models",
    "marketplace/dispatch/orchestration",
    "marketplace/dispatch/prompts",
    "marketplace/dispatch/retrieval",
)


SCENARIOS = (
    {
        "name": "generate_compact_flatten_full_tree",
        "root_kind": "flat",
        "query": "查天气",
        "top_k": 2,
        "config": {
            "selection_mode": "generate",
            "compact_boundary_codes_enabled": True,
            "flatten_full_tree_in_prompt": True,
            "max_exposure_depth_per_call": 99,
            "exposure_threshold": 1_000_000_000,
        },
    },
    {
        "name": "generate_noncompact_flatten_full_tree",
        "root_kind": "flat",
        "query": "查天气",
        "top_k": 2,
        "config": {
            "selection_mode": "generate",
            "compact_boundary_codes_enabled": False,
            "flatten_full_tree_in_prompt": True,
            "max_exposure_depth_per_call": 99,
            "exposure_threshold": 1_000_000_000,
        },
    },
    {
        "name": "generate_hierarchical_boundary_prompt",
        "root_kind": "branching",
        "query": "处理一份 PDF 并查询天气",
        "top_k": 2,
        "config": {
            "selection_mode": "generate",
            "compact_boundary_codes_enabled": True,
            "flatten_full_tree_in_prompt": False,
            "max_exposure_depth_per_call": 0,
            "exposure_threshold": 0,
        },
    },
    {
        "name": "generate_flattened_branching_top1",
        "root_kind": "branching",
        "query": "规划出行路线",
        "top_k": 1,
        "config": {
            "selection_mode": "generate",
            "compact_boundary_codes_enabled": True,
            "flatten_full_tree_in_prompt": True,
            "max_exposure_depth_per_call": 99,
            "exposure_threshold": 1_000_000_000,
        },
    },
    {
        "name": "logit_selection_compact_codes",
        "root_kind": "flat",
        "query": "查询天气",
        "top_k": 3,
        "candidate_scoring": True,
        "config": {
            "selection_mode": "logit_selection",
            "compact_boundary_codes_enabled": True,
            "flatten_full_tree_in_prompt": True,
            "max_exposure_depth_per_call": 99,
            "exposure_threshold": 1_000_000_000,
            "scoring_fallback_mode": "error",
            "scoring_return_probabilities": True,
        },
    },
)


CAPTURE_SCRIPT = r"""
import base64
import inspect
import json
import os
import sys

from models.retrieval import RetrieverItem, RetrieverNode
from retrieval.llm.base.protocols import ProgressiveLLMClient
from retrieval.llm.base.types import (
    CandidateScore,
    CandidateScoringResult,
    LLMClientCapabilities,
)
from retrieval.tree.codebooks import DEFAULT_COMPACT_BOUNDARY_CODEBOOK
from retrieval.tree.progressive import ProgressiveRetriever
from retrieval.tree.types import ProgressiveRetrieverConfig


scenario = json.loads(os.environ["REFACTOR_MESSAGE_COMPAT_SCENARIO"])


class CaptureLLM(ProgressiveLLMClient):
    name = "capture"

    def __init__(self):
        self.calls = []
        self.complete_call_index = 0

    @property
    def capabilities(self):
        return LLMClientCapabilities(
            completion=True,
            streaming=False,
            candidate_scoring=bool(scenario.get("candidate_scoring", False)),
            trie_constrained_decoding=False,
            progressive_prefix_kv_cache=False,
            thread_safe=True,
            local_resources=False,
        )

    def complete(self, model, messages, **kwargs):
        self.calls.append([dict(message) for message in messages])
        outputs = list(scenario.get("complete_outputs") or ["1\n2"])
        output = outputs[min(self.complete_call_index, len(outputs) - 1)]
        self.complete_call_index += 1
        return [output]

    def score_candidate_codes(
        self,
        *,
        model,
        messages,
        candidate_codes,
        code_to_canonical_id,
        top_k=None,
        require_single_token_codes=True,
        request_timeout=None,
    ):
        del model, top_k, require_single_token_codes, request_timeout
        self.calls.append([dict(message) for message in messages])
        codes = tuple(candidate_codes)
        total = max(1, len(codes))
        scores = tuple(
            CandidateScore(
                code=str(code),
                canonical_id=str(code_to_canonical_id.get(code, code)),
                token_id=index,
                logit=float(total - index),
                probability=float(total - index) / float(total),
                rank=index + 1,
            )
            for index, code in enumerate(codes)
        )
        return CandidateScoringResult(scores=scores, candidate_codes=codes)


def make_item(item_id, payload, label, description):
    return RetrieverItem(
        item_id=item_id,
        payload=payload,
        label=label,
        description=description,
    )


def build_root(kind):
    flat_items = (
        make_item(
            "weather",
            "LifeServices.MapsWeather.Weather",
            "天气",
            "查询指定城市天气",
        ),
        make_item(
            "travel",
            "LifeServices.Travel.Plan",
            "旅行规划",
            "规划出行路线",
        ),
        make_item(
            "doc",
            "OfficeDocs.Documents.Pdf",
            "PDF处理",
            "处理 PDF 文档",
        ),
    )
    if kind == "flat":
        return RetrieverNode(
            node_id="ROOT",
            label="ROOT",
            description="Skill capability tree root",
            items=flat_items,
        )
    if kind == "branching":
        return RetrieverNode(
            node_id="ROOT",
            label="ROOT",
            description="Skill capability tree root",
            children=(
                RetrieverNode(
                    node_id="life",
                    label="生活服务",
                    description="天气、地图和出行",
                    items=flat_items[:2],
                ),
                RetrieverNode(
                    node_id="office",
                    label="办公文档",
                    description="文档理解和处理",
                    items=(
                        flat_items[2],
                        make_item(
                            "sheet",
                            "OfficeDocs.Spreadsheets.Table",
                            "表格处理",
                            "处理电子表格",
                        ),
                    ),
                ),
                RetrieverNode(
                    node_id="media",
                    label="媒体工具",
                    description="图片和音频处理",
                    items=(
                        make_item(
                            "image",
                            "Media.Images.Edit",
                            "图片编辑",
                            "编辑图片",
                        ),
                        make_item(
                            "audio",
                            "Media.Audio.Transcribe",
                            "音频转写",
                            "转写音频内容",
                        ),
                    ),
                ),
            ),
        )
    raise AssertionError(f"unknown root kind: {kind}")


base_config_kwargs = {
    "top_k": int(scenario.get("top_k", 2)),
    "max_tokens": 96,
    "trie_constrained_decoding_enabled": False,
    "max_branch_choices": 6,
    "max_parallel_branches": 1,
    "enable_parallel_branches": False,
    "request_timeout": None,
    "compact_boundary_codes_enabled": True,
    "compact_boundary_codebook": DEFAULT_COMPACT_BOUNDARY_CODEBOOK,
    "flatten_full_tree_in_prompt": True,
    "max_exposure_depth_per_call": 99,
    "exposure_threshold": 1_000_000_000,
    "selection_mode": "generate",
    "scoring_require_single_token_codes": True,
    "scoring_return_probabilities": True,
    "scoring_fallback_mode": "error",
    "scoring_max_candidates": 512,
    "scoring_min_probability": None,
    "scoring_trace_top_n": 10,
}
base_config_kwargs.update(dict(scenario.get("config") or {}))
accepted_config_keys = inspect.signature(ProgressiveRetrieverConfig).parameters
config = ProgressiveRetrieverConfig(
    **{
        key: value
        for key, value in base_config_kwargs.items()
        if key in accepted_config_keys
    }
)

llm = CaptureLLM()
retriever = ProgressiveRetriever(llm=llm, config=config)
retriever.search(
    model="unit-test-model",
    query=str(scenario.get("query") or "查天气"),
    root=build_root(str(scenario.get("root_kind") or "flat")),
    top_k=int(scenario.get("top_k", 2)),
)

if not llm.calls:
    raise AssertionError("retrieval did not call the LLM")

payload = json.dumps(llm.calls, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
sys.stdout.write(base64.b64encode(payload).decode("ascii") + "\n")
"""


class RefactorMessageCompatibilityTest(unittest.TestCase):
    def test_messages_sent_to_llm_match_pre_refactor_bytes_for_retrieval_configs(self) -> None:
        current_dispatch_root = Path(__file__).resolve().parents[1]
        repo_root = _git_repo_root(current_dispatch_root)

        with tempfile.TemporaryDirectory() as tmpdir:
            old_dispatch_root = _extract_dispatch_snapshot(
                repo_root=repo_root,
                revision=PRE_REFACTOR_REV,
                tmpdir=Path(tmpdir),
            )

            for scenario in SCENARIOS:
                with self.subTest(scenario=scenario["name"]):
                    old_message_bytes = _capture_message_bytes(old_dispatch_root, scenario=scenario)
                    current_message_bytes = _capture_message_bytes(current_dispatch_root, scenario=scenario)

                    # Cache-busting [req:HEX] prefix injected into user content is intentional
                    # (see commit 2c17930 for long-query no-return fix) and varies per call,
                    # so strip it before byte-comparison with the pre-refactor snapshot.
                    old_normalized = _strip_req_prefix(old_message_bytes)
                    current_normalized = _strip_req_prefix(current_message_bytes)

                    messages_equal = current_normalized == old_normalized
                    _log_message_compatibility_report(
                        scenario=scenario,
                        old_message_bytes=old_normalized,
                        current_message_bytes=current_normalized,
                        messages_equal=messages_equal,
                    )

                    if not messages_equal:
                        self.fail(
                            f"messages sent to LLM changed after refactor for scenario={scenario['name']}:\n"
                            + _format_json_bytes_diff(
                                before=old_normalized,
                                after=current_normalized,
                            )
                        )


def _git_repo_root(cwd: Path) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return Path(result.stdout.strip())


def _extract_dispatch_snapshot(*, repo_root: Path, revision: str, tmpdir: Path) -> Path:
    archive = subprocess.run(
        ["git", "archive", "--format=tar", revision, *DISPATCH_PACKAGES],
        cwd=repo_root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout

    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        try:
            tar.extractall(tmpdir, filter="data")
        except TypeError:
            tar.extractall(tmpdir)

    return tmpdir / "marketplace" / "dispatch"


_REQ_PREFIX_RE = re.compile(rb"\[req:[0-9a-f]+\]")


def _strip_req_prefix(message_bytes: bytes) -> bytes:
    """Remove the cache-busting [req:HEX] prefix injected into user message content.

    The prefix is a per-call uuid4-derived token added by build_disclosure_prompt_parts
    (see commit 2c17930). It is intentional in production but defeats byte-equality with
    the pre-refactor snapshot, so we normalize it out on both sides before comparison.
    """
    return _REQ_PREFIX_RE.sub(b"", message_bytes)


def _capture_message_bytes(dispatch_root: Path, *, scenario: dict[str, object]) -> bytes:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(dispatch_root)
    env[SCENARIO_ENV] = json.dumps(scenario, ensure_ascii=False, separators=(",", ":"))
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(CAPTURE_SCRIPT)],
        cwd=dispatch_root,
        env=env,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return base64.b64decode(result.stdout.strip())


def _format_json_bytes_diff(*, before: bytes, after: bytes) -> str:
    before_text = json.dumps(json.loads(before.decode("utf-8")), ensure_ascii=False, indent=2)
    after_text = json.dumps(json.loads(after.decode("utf-8")), ensure_ascii=False, indent=2)
    return "\n".join(
        difflib.unified_diff(
            before_text.splitlines(),
            after_text.splitlines(),
            fromfile=f"{PRE_REFACTOR_REV}:messages",
            tofile="working-tree:messages",
            lineterm="",
        )
    )


def _log_message_compatibility_report(
    *,
    scenario: dict[str, object],
    old_message_bytes: bytes,
    current_message_bytes: bytes,
    messages_equal: bool,
) -> None:
    old_calls = json.loads(old_message_bytes.decode("utf-8"))
    current_calls = json.loads(current_message_bytes.decode("utf-8"))
    result = "PASS" if messages_equal else "FAIL"
    config = json.dumps(
        scenario.get("config") or {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    LOGGER.info(
        "\n[refactor-message-compatibility]"
        f"\nscenario={scenario['name']}"
        f"\npre_refactor_revision={PRE_REFACTOR_REV}"
        f"\nroot_kind={scenario.get('root_kind')}"
        f"\ntop_k={scenario.get('top_k')}"
        f"\nconfig_overrides={config}"
        f"\nold_llm_call_count={len(old_calls)}"
        f"\ncurrent_llm_call_count={len(current_calls)}"
        f"\nold_message_bytes={len(old_message_bytes)}"
        f"\ncurrent_message_bytes={len(current_message_bytes)}"
        f"\nold_message_sha256={_sha256(old_message_bytes)}"
        f"\ncurrent_message_sha256={_sha256(current_message_bytes)}"
        f"\nbyte_equal={messages_equal}"
        f"\nresult={result}"
    )


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


if __name__ == "__main__":
    unittest.main()
