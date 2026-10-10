# pylint: disable=line-too-long,wrong-import-order,huawei-invalid-name
# pylint: disable=huawei-too-many-arguments,add-staticmethod-or-classmethod-decorator
from __future__ import annotations
import threading
from time import perf_counter
from pathlib import Path
from typing import Optional

try:
    from openai import OpenAI
except ModuleNotFoundError:  # pragma: no cover
    OpenAI = None

from shared.rich_compat import Panel

from .schema import (
    DEFAULT_TREE_OUTPUT_PATH,
    DynamicTreeConfig,
    TreeManagerConfig,
)
from indexing.scanners import create_scanner
from .expansion import TreeExpansionEngine as ExternalTreeExpansionEngine
from .grouping import TreeGroupingEngine
from .llm_runtime import TreeLLMRuntime as ExternalTreeLLMRuntime
from .preset_writer import TreePresetWriter as ExternalTreePresetWriter
from .repair import TreeRepairEngine as ExternalTreeRepairEngine
from .adapters import TreeBuilderAdaptersMixin
from .equivalence import TreeBuilderEquivalenceMixin
from .expansion_repair import TreeBuilderExpansionRepairMixin
from .runtime import TreeBuilderRuntimeMixin
from .shared import console


_TreeLLMRuntime = ExternalTreeLLMRuntime
_TreePresetWriter = ExternalTreePresetWriter
_TreeExpansionEngine = ExternalTreeExpansionEngine
_TreeRepairEngine = ExternalTreeRepairEngine


class TreeBuilder(
    TreeBuilderRuntimeMixin,
    TreeBuilderExpansionRepairMixin,
    TreeBuilderEquivalenceMixin,
    TreeBuilderAdaptersMixin,
):
    """
    Unified tree builder with auto-selection and node splitting.

    Features:
    - Auto-selects build method based on skill count
    - Splits oversized nodes (> max_skills_per_node)
    - Simple tree visualization
    """

    # Token budget constants for auto batch size calculation
    PROMPT_OVERHEAD_TOKENS = 3000    # prompt template + instructions
    OUTPUT_RESERVE_TOKENS = 4000     # JSON response reserve
    AVG_TOKENS_PER_SKILL = 75       # average tokens per skill entry
    DEFAULT_CONTEXT_WINDOW = 128000  # fallback context window size
    DEFAULT_MAX_OUTPUT_TOKENS = 32768  # fallback max output tokens

    def __init__(
        self,
        skills_dir: Path | str | None = None,
        output_path: Path | str | None = None,
        config: Optional[DynamicTreeConfig] = None,
        manager_config: TreeManagerConfig | None = None,
        client: OpenAI | None = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        llm_seed: int | None = None,
        max_workers: Optional[int] = None,
        display_skills_dir: Path | str | None = None,
        item_type: str = "skill",
        skill_entries: list[dict] | None = None,
    ):
        mcfg = manager_config or TreeManagerConfig()
        build_cfg = mcfg.build
        if skills_dir is None:
            raise ValueError("TreeBuilder requires a non-empty skills_dir")
        self.scanner = create_scanner(item_type, skills_dir, display_items_dir=display_skills_dir)
        self._skill_entries_override = (
            [dict(item) for item in (skill_entries or [])]
            if skill_entries is not None
            else None
        )
        default_tree_path = DEFAULT_TREE_OUTPUT_PATH
        self.output_path = Path(output_path) if output_path else default_tree_path
        self.config = config or DynamicTreeConfig(
            branching_factor=mcfg.branching_factor,
            max_depth=mcfg.max_depth,
            root_categories=mcfg.root_categories,
        )
        self.model = str(model or "").strip()
        self.api_key = str(api_key or "").strip()
        self.base_url = str(base_url or "").strip()
        self._manager_config = mcfg
        self._llm_seed = llm_seed
        if not self.model:
            raise ValueError("TreeBuilder requires a non-empty llm model")
        if client is None and not self.api_key:
            raise ValueError("TreeBuilder requires a non-empty llm api key")
        self._client = (
            client
            if client is not None
            else (
                OpenAI(api_key=self.api_key, base_url=self.base_url)
                if OpenAI is not None
                else None
            )
        )
        self.max_workers = max_workers or build_cfg.max_workers
        self._postprocess_enabled = bool(build_cfg.postprocess_enabled)
        self._postprocess_max_passes = max(0, int(build_cfg.postprocess_max_passes))
        self._postprocess_min_skills = max(2, int(build_cfg.postprocess_min_skills))
        self._equiv_grouping_enabled = bool(build_cfg.equiv_grouping_enabled)
        self._equiv_max_groups_per_parent = max(2, int(build_cfg.equiv_max_groups_per_parent))
        self._equiv_allow_singleton_groups = bool(build_cfg.equiv_allow_singleton_groups)
        self._equiv_min_lexical_similarity = max(0.0, min(1.0, float(build_cfg.equiv_min_lexical_similarity)))
        self._deterministic_prompts = build_cfg.deterministic_prompts
        self._discovery_seed = build_cfg.discovery_seed
        self._prompt_fingerprint_version = build_cfg.prompt_fingerprint_version
        self._cache_observability = build_cfg.cache_observability
        self._skill_profiles_enabled = bool(build_cfg.skill_profiles_enabled)
        self._skill_profile_select_rules_enabled = bool(build_cfg.skill_profile_select_rules_enabled)
        self._skill_profile_batch_size = max(1, int(build_cfg.skill_profile_batch_size))
        self._skill_profile_description_limit = max(40, int(build_cfg.skill_profile_description_limit))
        self._skill_profile_rule_limit = max(40, int(build_cfg.skill_profile_rule_limit))

        self._llm_calls = 0
        self._retry_calls = 0
        self._cache_hits = 0
        self._cache_misses = 0
        self._cache_unknown = 0
        self._prompt_fingerprints: set[str] = set()
        self._leaf_skills = 0  # Skills that have reached leaf nodes
        self._counter_lock = threading.Lock()  # Protects _llm_calls, _leaf_skills, _consecutive_failures
        self._progress = None  # Rich progress bar
        self._progress_task = None
        self._batch_size_cache = None
        self._max_output_tokens_cache = None
        self._thread_local = threading.local()  # Per-thread truncation flag
        self._executor = None  # Shared executor, set in _build_tree
        self._llm_semaphore = threading.Semaphore(self.max_workers)  # Limit concurrent LLM calls
        self._consecutive_failures = 0
        self.MAX_CONSECUTIVE_FAILURES = 5
        self._llm_runtime = ExternalTreeLLMRuntime(self)
        self._preset_writer = ExternalTreePresetWriter(self)
        self._expansion_engine = ExternalTreeExpansionEngine(self)
        self._repair_engine = ExternalTreeRepairEngine(self)
        self._grouping_engine = TreeGroupingEngine(self)

    def _auto_batch_size(self) -> int:
        """Calculate batch size from model context window."""
        return self._llm_runtime.auto_batch_size()

    def _get_max_output_tokens(self) -> int:
        """Get max output tokens for the model, with caching."""
        return self._llm_runtime.get_max_output_tokens()

    def _merged_extra_body(self) -> dict:
        return self._llm_runtime.merged_extra_body()

    def _model_limits(self) -> tuple[int, int]:
        """Resolve model limits."""
        return self._llm_runtime.model_limits()

    def build(
        self,
        verbose: bool = False,
        show_tree: bool = True,
    ) -> dict:
        console.print(Panel.fit("[bold cyan]Building Capability Tree[/bold cyan]", border_style="cyan"))
        step1_start = perf_counter()
        skill_entries = self._load_skill_entries()
        console.print(f"[dim]Step 1 elapsed: {(perf_counter() - step1_start) * 1000.0:.2f} ms[/dim]")
        if not skill_entries:
            console.print("[red]No skills found.[/red]")
            return {}

        step1b_start = perf_counter()
        skill_entries = self._enrich_skill_profiles(skill_entries, verbose=verbose)
        console.print(f"[dim]Step 1b elapsed: {(perf_counter() - step1b_start) * 1000.0:.2f} ms[/dim]")
        step2_start = perf_counter()
        tree_root = self._build_tree(skill_entries, verbose)
        console.print(f"[dim]Step 2 elapsed: {(perf_counter() - step2_start) * 1000.0:.2f} ms[/dim]")
        step3_start = perf_counter()
        tree_dict = self._tree_to_dict(tree_root)
        preset_dict = self._build_tree_preset(
            tree_dict,
            show_tree=show_tree,
        )
        console.print(f"[dim]Step 3 elapsed: {(perf_counter() - step3_start) * 1000.0:.2f} ms[/dim]")
        self._print_cache_stats()
        self._print_build_summary()
        return preset_dict


# Convenience function
def build_tree(
    skills_dir: Path | str | None = None,
    output_path: Path | str | None = None,
    config: DynamicTreeConfig | None = None,
    manager_config: TreeManagerConfig | None = None,
    client: OpenAI | None = None,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    llm_seed: int | None = None,
    max_workers: int | None = None,
    verbose: bool = False,
    show_tree: bool = True,
    display_skills_dir: Path | str | None = None,
    item_type: str = "skill",
    skill_entries: list[dict] | None = None,
) -> dict:
    """Build capability tree."""
    builder = TreeBuilder(
        skills_dir,
        output_path,
        config=config,
        manager_config=manager_config,
        client=client,
        model=model,
        api_key=api_key,
        base_url=base_url,
        llm_seed=llm_seed,
        max_workers=max_workers,
        display_skills_dir=display_skills_dir,
        item_type=item_type,
        skill_entries=skill_entries,
    )
    return builder.build(
        verbose=verbose,
        show_tree=show_tree,
    )
