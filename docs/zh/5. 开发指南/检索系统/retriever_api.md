# SDK 接口文档

本文描述当前已经实现并对外公开的 SDK 接口，分为两部分：

1. 离线构建
2. 在线检索

推荐导入方式：

```python
import openjiuwen_skillsdispatch as skillsdispatch
```

本文中的接口均已在代码中实现，并已从根包导出。

---

## 一、离线构建

离线构建统一通过 `IndexBuilder` 完成。

```python
from pathlib import Path


class IndexBuilder:
    @staticmethod
    def build(
        item_paths: list[str],
        output_dir: str | Path,
        *,
        config: BuildConfig | None = None,
    ) -> Path:
        ...

    @staticmethod
    def add(
        item_paths: list[str],
        base_index_dir: str | Path,
        output_dir: str | Path,
        *,
        config: BuildConfig | None = None,
    ) -> Path:
        ...

    @staticmethod
    def delete(
        item_paths: list[str],
        base_index_dir: str | Path,
        output_dir: str | Path,
        *,
        config: BuildConfig | None = None,
    ) -> Path:
        ...
```

返回值统一为输出索引目录的绝对路径。

### 1.1 构建方法枚举

```python
from enum import IntFlag


class BuildMethod(IntFlag):
    BM25 = 1
    EMBEDDING = 2
    TREE = 4
    ALL = BM25 | EMBEDDING | TREE
```

可用取值：

- `BuildMethod.BM25`
- `BuildMethod.EMBEDDING`
- `BuildMethod.TREE`
- `BuildMethod.BM25 | BuildMethod.EMBEDDING`
- `BuildMethod.ALL`

### 1.2 离线配置结构体 `BuildConfig`

```python
from dataclasses import dataclass
from openai import OpenAI


@dataclass(frozen=True)
class BuildConfig:
    method: BuildMethod = BuildMethod.ALL

    llm_openai_client: OpenAI | None = None
    llm_model: str = ""
    llm_seed: int | None = None

    embedding_openai_client: OpenAI | None = None
    embedding_model: str = ""
    embedding_batch_size: int = 16

    tree_branching_factor: int = 8
    tree_max_depth: int = 6
    tree_root_categories: list[str | dict[str, object]] | None = None

    tree_max_workers: int = 1
    tree_caching: bool = False
    tree_num_retries: int = 2
    tree_timeout_seconds: float = 180.0
    tree_context_window: int = 0
    tree_max_output_tokens: int = 0

    tree_postprocess_enabled: bool = True
    tree_postprocess_max_passes: int = 1
    tree_postprocess_min_skills: int = 6

    tree_equiv_grouping_enabled: bool = True
    tree_equiv_max_groups_per_parent: int = 6
    tree_equiv_allow_singleton_groups: bool = True
    tree_equiv_min_lexical_similarity: float = 0.12

    tree_deterministic_prompts: bool = True
    tree_discovery_seed: int = 42
    tree_prompt_fingerprint_version: str = "v1"
    tree_cache_observability: bool = True

    generate_tree_html: bool = True
    allow_fallback_tree: bool = True
```

### 1.3 `BuildConfig` 字段说明

#### 核心字段

- `method`
  指定本次构建需要生成哪些检索工件。

#### LLM 相关

- `llm_openai_client`
  OpenAI-compatible chat client。用于树构建阶段。

- `llm_model`
  树构建使用的模型名。

- `llm_seed`
  可选随机种子，用于提升构建过程可复现性。

#### Embedding 相关

- `embedding_openai_client`
  OpenAI-compatible embedding client。

- `embedding_model`
  embedding 模型名。

- `embedding_batch_size`
  embedding 批量大小。

#### Tree 结构相关

- `tree_branching_factor`
  树分支因子。

- `tree_max_depth`
  树最大深度。

- `tree_root_categories`
  可选根类目定义，支持字符串列表或带描述的对象列表。

#### Tree 构建过程相关

- `tree_max_workers`
- `tree_caching`
- `tree_num_retries`
- `tree_timeout_seconds`
- `tree_context_window`
- `tree_max_output_tokens`

#### Tree 后处理相关

- `tree_postprocess_enabled`
- `tree_postprocess_max_passes`
- `tree_postprocess_min_skills`

#### 等价叶子分组相关

- `tree_equiv_grouping_enabled`
- `tree_equiv_max_groups_per_parent`
- `tree_equiv_allow_singleton_groups`
- `tree_equiv_min_lexical_similarity`

#### 调试与可观测性相关

- `tree_deterministic_prompts`
- `tree_discovery_seed`
- `tree_prompt_fingerprint_version`
- `tree_cache_observability`

#### 输出控制

- `generate_tree_html`
  是否同时生成树的 HTML 可视化。

- `allow_fallback_tree`
  当需要构树但未提供 LLM 能力时，是否允许退回 fallback tree。

### 1.4 当前实现中的行为说明

#### `BuildMethod.BM25`

会生成或刷新：

- `catalog.jsonl`
- `bm25_index.json`
- `manifest.json`

#### `BuildMethod.EMBEDDING`

会生成或刷新：

- `catalog.jsonl`
- `embedding_records.jsonl`
- `embedding_index.json`
- `manifest.json`

#### `BuildMethod.TREE`

会生成或刷新：

- `tree_index.yaml`
- `tree_index.html`（若 `generate_tree_html=True`）
- `manifest.json`

#### `BuildMethod.ALL`

生成完整索引：

- tree
- embedding
- bm25
- manifest

#### 一个当前实现里的重要说明

目前索引格式仍以 tree 作为结构骨架，因此即使 `method` 不包含 `TREE`，内部仍会维护树结构来保证 catalog、cid 和索引工件的一致性。

也就是说，当前 `method` 主要控制的是：

- 是否生成或刷新 embedding 工件
- 是否生成或刷新 BM25 工件
- 是否启用 LLM 构树或 fallback tree 行为

它还不是“完全独立的纯 tree-only / bm25-only / embedding-only 索引包格式”。

### 1.5 离线构建示例

#### 最小示例

```python
import openjiuwen_skillsdispatch as skillsdispatch

index_dir = skillsdispatch.IndexBuilder.build(
    item_paths=["/data/skills/web", "/data/skills/search"],
    output_dir="/tmp/skill-index",
)
```

#### 完整示例

```python
import openjiuwen_skillsdispatch as skillsdispatch
from openai import OpenAI

llm_client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="EMPTY")
embedding_client = OpenAI(base_url="http://127.0.0.1:9000/v1", api_key="EMPTY")

config = skillsdispatch.BuildConfig(
    method=skillsdispatch.BuildMethod.ALL,
    llm_openai_client=llm_client,
    llm_model="gpt-4.1",
    embedding_openai_client=embedding_client,
    embedding_model="text-embedding-3-large",
    embedding_batch_size=32,
    tree_branching_factor=6,
    tree_max_depth=5,
    tree_max_workers=4,
    generate_tree_html=True,
    allow_fallback_tree=False,
)

index_dir = skillsdispatch.IndexBuilder.build(
    item_paths=["/data/skills/web", "/data/skills/search"],
    output_dir="/tmp/skill-index",
    config=config,
)
```

#### 增量添加示例

```python
config = skillsdispatch.BuildConfig(
    method=skillsdispatch.BuildMethod.BM25 | skillsdispatch.BuildMethod.EMBEDDING,
    embedding_openai_client=embedding_client,
    embedding_model="text-embedding-3-large",
)

index_dir = skillsdispatch.IndexBuilder.add(
    item_paths=["/data/new_skills"],
    base_index_dir="/tmp/old-index",
    output_dir="/tmp/new-index",
    config=config,
)
```

---

## 二、在线检索

在线检索统一通过 `Retriever` 完成。

```python
from pathlib import Path
from typing import Sequence
from openai import OpenAI


class Retriever:
    @classmethod
    def from_index(
        cls,
        index_dir: str | Path,
        *,
        llm_openai_client: OpenAI | None = None,
        llm_model: str = "",
        embedding_openai_client: OpenAI | None = None,
        embedding_model: str = "",
    ) -> "Retriever":
        ...

    def search(
        self,
        query: str,
        *,
        config: SearchConfig,
    ) -> list[str]:
        ...

    def search_details(
        self,
        query: str | Sequence[dict[str, str]],
        *,
        config: SearchConfig,
    ) -> RetrieverSearchResult:
        ...
```

### 2.1 初始化接口 `from_index(...)`

`from_index(...)` 只用于绑定长期依赖：

- `index_dir`
- `llm_openai_client`
- `llm_model`
- `embedding_openai_client`
- `embedding_model`

当前实现中，这些 OpenAI-compatible client 会在 SDK 内部被包装成：

- `LLMClient`
- `OpenAIEmbeddingClient`

外部调用方不需要直接依赖内部包装类型。

### 2.2 检索方法枚举

```python
from enum import Enum


class RetrievalMethod(str, Enum):
    AUTO = "auto"
    BM25 = "bm25"
    EMBEDDING = "embedding"
    PROGRESSIVE = "progressive"
```

### 2.3 在线配置结构体 `SearchConfig`

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class SearchConfig:
    top_k: int
    method: RetrievalMethod = RetrievalMethod.AUTO
    llm_top_k: int | None = None

    bm25_k1: float = 1.5
    bm25_b: float = 0.75
    bm25_delta: float = 0.5

    embedding_batch_size: int = 64

    progressive_batch_size: int = 1
    progressive_max_tokens: int = 48
    progressive_request_timeout: float | None = None

    trie_constrained_decoding_enabled: bool = False
    trie_constraint_allow_user_nodes: bool = True
    trie_constraint_max_candidates: int = 512
    trie_constraint_fallback_payload: str = ""

    progressive_max_branch_choices: int = 6
    progressive_auto_expand_child_threshold: int = 3
    progressive_collapse_single_chain: bool = True
    progressive_max_collapse_steps: int = 8
    progressive_max_parallel_branches: int = 3
    progressive_enable_parallel_branches: bool = True
    progressive_auto_terminal_item_threshold: int = 12
    progressive_branch_choice_slack: int = 2
    progressive_branch_candidate_slack: int = 1
    progressive_round_robin_branch_reduce: bool = True
    progressive_branch_max_tokens: int = 96
    progressive_item_max_tokens: int = 128
```

### 2.4 `SearchConfig` 字段说明

#### 核心字段

- `top_k`
  最终返回结果数。

- `method`
  本次检索的主路线。

- `llm_top_k`
  当本次检索包含 LLM progressive 主路时，最多保留多少条 LLM 结果作为前缀。
  剩余名额由 embedding 和 BM25 去重补齐。

当前实现中：

- `llm_top_k is None` 时，等价于保留全部 progressive 主结果
- `llm_top_k > top_k` 时会自动截断到 `top_k`
- `llm_top_k <= 0` 时不会保留 progressive 主前缀，结果完全由补齐路径决定

#### BM25 配置

- `bm25_k1`
- `bm25_b`
- `bm25_delta`

#### Embedding 配置

- `embedding_batch_size`

#### Progressive 配置

- `progressive_batch_size`
- `progressive_max_tokens`
- `progressive_request_timeout`

#### Trie constrained decoding 配置

- `trie_constrained_decoding_enabled`
- `trie_constraint_allow_user_nodes`
- `trie_constraint_max_candidates`
- `trie_constraint_fallback_payload`

#### Progressive 树搜索细节

- `progressive_max_branch_choices`
- `progressive_auto_expand_child_threshold`
- `progressive_collapse_single_chain`
- `progressive_max_collapse_steps`
- `progressive_max_parallel_branches`
- `progressive_enable_parallel_branches`
- `progressive_auto_terminal_item_threshold`
- `progressive_branch_choice_slack`
- `progressive_branch_candidate_slack`
- `progressive_round_robin_branch_reduce`
- `progressive_branch_max_tokens`
- `progressive_item_max_tokens`

### 2.5 `RetrievalMethod` 的实际行为

#### `RetrievalMethod.BM25`

只执行 BM25 检索。

#### `RetrievalMethod.EMBEDDING`

先执行 embedding 检索，再用 BM25 去重补齐。

#### `RetrievalMethod.PROGRESSIVE`

当前实现中，`PROGRESSIVE` 会进入统一主流程：

- 若 LLM 可用，则执行 `progressive + embedding? + bm25`
- 若 LLM 不可用，则自动降级到 `embedding + bm25` 或 `bm25`

也就是说，当前它不是“严格只跑 progressive，不允许补齐”，而是“以 progressive 为主路的统一检索流程”。

#### `RetrievalMethod.AUTO`

当前实现逻辑是：

```text
if LLM available:
    progressive + embedding? + bm25
elif embedding available:
    embedding + bm25
else:
    bm25
```

### 2.6 在线检索示例

#### BM25-only

```python
import openjiuwen_skillsdispatch as skillsdispatch

retriever = skillsdispatch.Retriever.from_index("/tmp/skill-index")

config = skillsdispatch.SearchConfig(
    top_k=5,
    method=skillsdispatch.RetrievalMethod.BM25,
)

payloads = retriever.search("browser automation", config=config)
```

#### Embedding + BM25

```python
import openjiuwen_skillsdispatch as skillsdispatch
from openai import OpenAI

embedding_client = OpenAI(base_url="http://127.0.0.1:9000/v1", api_key="EMPTY")

retriever = skillsdispatch.Retriever.from_index(
    "/tmp/skill-index",
    embedding_openai_client=embedding_client,
    embedding_model="text-embedding-3-large",
)

config = skillsdispatch.SearchConfig(
    top_k=5,
    method=skillsdispatch.RetrievalMethod.EMBEDDING,
)

payloads = retriever.search("browser automation", config=config)
```

#### Progressive + Embedding + BM25

```python
import openjiuwen_skillsdispatch as skillsdispatch
from openai import OpenAI

llm_client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="EMPTY")
embedding_client = OpenAI(base_url="http://127.0.0.1:9000/v1", api_key="EMPTY")

retriever = skillsdispatch.Retriever.from_index(
    "/tmp/skill-index",
    llm_openai_client=llm_client,
    llm_model="gpt-4.1",
    embedding_openai_client=embedding_client,
    embedding_model="text-embedding-3-large",
)

config = skillsdispatch.SearchConfig(
    top_k=10,
    method=skillsdispatch.RetrievalMethod.AUTO,
    llm_top_k=3,
)

payloads = retriever.search("extract tables from websites", config=config)
```

#### 获取详细结果

```python
result = retriever.search_details(
    [
        {"role": "user", "content": "Need a skill for browsing websites"},
        {"role": "user", "content": "It should also extract tables"},
    ],
    config=skillsdispatch.SearchConfig(
        top_k=8,
        method=skillsdispatch.RetrievalMethod.AUTO,
        llm_top_k=4,
    ),
)

print(result.payloads)
print(result.summary_lines)
print(result.trace_events)
```

---

## 三、当前公开导出

根包当前已导出这组与检索相关的主类型：

- `IndexBuilder`
- `BuildMethod`
- `BuildConfig`
- `Retriever`
- `RetrievalMethod`
- `SearchConfig`
- `RetrieverSearchResult`

推荐调用方式是：

```python
import openjiuwen_skillsdispatch as skillsdispatch
```

然后直接使用：

```python
skillsdispatch.IndexBuilder
skillsdispatch.BuildConfig
skillsdispatch.BuildMethod
skillsdispatch.Retriever
skillsdispatch.SearchConfig
skillsdispatch.RetrievalMethod
```
