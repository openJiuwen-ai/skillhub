# Experience Knowledge Base — 经验知识库

基于历史成功经验加速 Skill 检索的模块。通过记录「用户 query → 正确 skill」的映射，
在后续相似 query 到来时直接返回经验结果，跳过耗时的树搜索流程。

## 架构概览

```
用户 query
   │
   ├── ① ExperienceRetriever（快速路径）
   │     将 query 做 embedding，与经验库做 cosine 相似度匹配
   │     sim ≥ threshold → 直接返回 skill_ids ✅
   │     无匹配 ↓
   │
   └── ② 原始 Tree Retriever（慢速路径）
         完整的渐进式树搜索
         成功 → 包装为 TraceRecord → add(trace) → 写入 pending buffer
                pending ≥ pending_flush_threshold → 自动 flush → 聚类 → 更新经验库
```

## 配置

### 最简方式 — 替换原 Retriever

```python
from retrieval.experience import ExperienceAwareRetriever, EmbeddingClient
from openai import OpenAI

openai_client = OpenAI(
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    api_key="sk-xxx",
)

# 使用方式与原 Retriever 完全一致
retriever = ExperienceAwareRetriever.from_index(
    index_dir="output_index",
    llm_openai_client=openai_client,           # 通义千问客户端（树搜索 + LLM 模式提取）
    llm_model="qwen-plus",                     # LLM 模型名
    embedding_client=EmbeddingClient(           # 向量化客户端（必须）
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="sk-xxx",
        model="text-embedding-v3",
    ),
    kb_dir="experience_kb",              # 经验库目录（包含 metadata + FAISS + embeddings）

    # ─── 可选参数 ───
    vector_algorithm="IndexFlatIP",     # FAISS 索引类型：IndexFlatIP（内积）或 IndexFlatL2
    experience_threshold=0.80,        # 经验匹配余弦相似度阈值
    experience_top_k=1,               # 取 top-k 个经验结果
    min_hits_for_pattern=2,           # 形成 pattern 所需的最小命中次数
    pending_flush_threshold=20,       # pending buffer 满多少条自动 flush
)

# 正常调用
results = retriever.search("帮我画一个甘特图")

# 服务关闭时自动 flush pending 记录
retriever.close()
```

### EmbeddingClient 后端选择

支持两种向量模型后端，**二选一**：

| 方式                       | 参数                               | 说明                        |
| ------------------------ | -------------------------------- | ------------------------- |
| DashScope API            | `base_url` + `api_key` + `model` | 调用 DashScope embedding 模型 |
| 本地 Sentence-Transformers | `model_name`                     | 本地加载模型，无需网络               |

```python
# 方式 1：DashScope API
EmbeddingClient(
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    api_key="sk-xxx",
    model="text-embedding-v3",
)

# 方式 2：本地模型（可选，适合离线环境）
# EmbeddingClient(model_name="sentence-transformers/all-MiniLM-L6-v2")
```

### 关键参数说明

| 参数                        | 默认值           | 说明                                         |
| ------------------------- | ------------- | ------------------------------------------ |
| `vector_algorithm`        | "IndexFlatIP" | FAISS 索引类型：IndexFlatIP（内积/余弦）或 IndexFlatL2 |
| `experience_threshold`    | 0.80          | 经验匹配的最小相似度，越高越严格                           |
| `experience_top_k`        | 1             | 返回 top-k 个经验结果                             |
| `min_hits_for_pattern`    | 2             | 最少多少次相同命中才尝试提取 pattern                     |
| `pending_flush_threshold` | 20            | pending buffer 阈值，达到后自动后台 flush            |

## 在线构建（自动积累）

开箱即用。每次检索成功后，系统自动记录并积累经验：

### 流程

```
1. 树搜索成功后，ExperienceAwareRetriever 自动构造 TraceRecord
   └→ 调用 SkillKnowledgeBuilder.add(trace) 追加到 pending buffer

2. pending count ≥ pending_flush_threshold
   └→ 启动后台线程执行 flush

3. flush 过程：
   a. 按 skill_ids 分组（相同 skill 的记录归到一起）
   b. 每组记录 < min_hits：放回 pending buffer，暂不处理
   c. 每组记录 ≥ min_hits：
      ├→ 批量 embedding
      ├→ FAISS K-Means 语义聚类
      ├→ 噪声点放回 pending buffer
      ├→ LLM 给每个有效 cluster 命名（pattern）
      └→ 尝试写入经验库（新建或跳过）
```

### 写入逻辑

新 cluster 与经验库中**相同 skill\_ids** 的条目做相似度匹配：

- 经验库中尚无该 skill\_ids → 新建经验条目
- 经验库中已有该 skill\_ids：
  - 用 pattern description 做相似度搜索
  - 相似度 ≥ 0.75 → 跳过（认为已存在相似经验）
  - 相似度 < 0.75 → 新建经验条目

### Embedding 计算方式

所有经验条目的 embedding 计算方式统一：

```
embedding = embed(query_pattern + "\n" + "\n".join(query_examples))
```

## 离线构建（预填充经验库）

如果想预先构建经验库（比如用历史日志），可以独立使用 `ExperienceBank` + `SkillKnowledgeBuilder`：

### 方式 1：直接写入经验条目

```python
from retrieval.experience import ExperienceBank, EmbeddingClient

embed_client = EmbeddingClient(
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    api_key="sk-xxx",
    model="text-embedding-v3",
)

kb = ExperienceBank(
    index_dir="experience_kb",
    embedding_client=embed_client,
)

# 手动创建经验条目
item = kb.create_item(
    query_pattern="数据可视化相关",
    query_examples=[
        "帮我画一个柱状图",
        "生成一个折线图",
        "做一个饼图",
    ],
    skill_ids=["chart_skill", "visualization_skill"],
)
kb.add(item)
```

### 方式 2：通过 SkillKnowledgeBuilder 批量导入历史数据

```python
from retrieval.experience import (
    ExperienceBank, SkillKnowledgeBuilder, EmbeddingClient, TraceRecord
)
from openai import OpenAI

openai_client = OpenAI(
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    api_key="sk-xxx",
)

embed_client = EmbeddingClient(
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    api_key="sk-xxx",
    model="text-embedding-v3",
)

kb = ExperienceBank(
    index_dir="experience_kb",
    embedding_client=embed_client,
)

# 注意：build() 要求 KB 为空目录，否则会抛出 ValueError
builder = SkillKnowledgeBuilder(
    kb=kb,
    embedding_client=embed_client,
    llm_client=openai_client,          # 内部会自动用 coerce_generation_client 包装
    llm_model="qwen-plus",
    min_hits_for_pattern=2,
)

# 构造历史 TraceRecord 列表
history = [
    TraceRecord(trace_id="t1", query="帮我画一个柱状图", skill_ids=["chart_skill"]),
    TraceRecord(trace_id="t2", query="生成一个折线图", skill_ids=["chart_skill"]),
    TraceRecord(trace_id="t3", query="做一个饼图", skill_ids=["chart_skill"]),
    TraceRecord(trace_id="t4", query="写一个 SQL 查询", skill_ids=["sql_skill"]),
    TraceRecord(trace_id="t5", query="帮我查一下数据库", skill_ids=["sql_skill"]),
]

# 全量构建（聚类 → 蒸馏 → 写入）
builder.build(history)
# 完成后经验库已持久化到 experience_kb 目录
```

### 方式 3：增量追加历史数据

如果经验库已有数据，不能调用 `build()`（会抛 ValueError），应使用 `add()` + `flush()` 增量写入：

```python
builder = SkillKnowledgeBuilder(
    kb=kb,
    embedding_client=embed_client,
    llm_client=openai_client,
    llm_model="qwen-plus",
    min_hits_for_pattern=2,
)

for trace in history:
    builder.add(trace)

# 手动触发聚类和写入
builder.flush()
```

## 经验库存储结构

经验库采用**目录结构**存储，包含完整性校验：

```
experience_kb/
├── meta.json              # 完整性清单（SHA256 校验和）
├── scalar/
│   └── metadata.jsonl     # 经验条目元数据（JSONL，不含 embedding）
└── vector/
    ├── faiss_index.bin    # FAISS 向量索引
    └── embeddings.npy     # embedding 矩阵（numpy 格式）
```

加载时会检查 `meta.json` 中的 SHA256，不一致时输出 warning 并继续加载。

### metadata.jsonl 格式

每行一个 JSON 对象：

```json
{"id": "exp_0000", "query_pattern": "数据可视化相关", "query_examples": ["帮我画一个柱状图", "生成一个折线图"], "skill_ids": ["chart_skill"], "success_count": 1, "created_at": 1718000000.0, "last_hit_at": 1718000000.0}
```

| 字段               | 类型         | 说明                            |
| ---------------- | ---------- | ----------------------------- |
| `id`             | string     | 唯一标识，如 `exp_0000`             |
| `query_pattern`  | string     | 描述性意图短语（约 10-30 字），由 LLM 概括生成 |
| `query_examples` | list\[str] | 属于该意图的实际用户 query 示例           |
| `skill_ids`      | list\[str] | 该意图对应的 skill ID 列表            |
| `success_count`  | int        | 累计命中次数                        |
| `created_at`     | float      | 创建时间戳                         |
| `last_hit_at`    | float      | 最后命中时间戳                       |

**注意**：`embedding` 字段**不存储**在 `metadata.jsonl` 中，而是单独保存在 `vector/embeddings.npy`。加载时会从 numpy 文件重新填充到内存对象。

### meta.json 格式

```json
{
  "version": 1,
  "vector_count": 5,
  "vector_algorithm": "IndexFlatIP",
  "vector_sha256": "abc123...",
  "scalar_sha256": "def456..."
}
```

- `vector_sha256`：`vector/faiss_index.bin` 的校验和
- `scalar_sha256`：`scalar/metadata.jsonl` 的校验和

## 核心组件

| 组件                         | 职责                                               |
| -------------------------- | ------------------------------------------------ |
| `ExperienceBank`           | 经验条目的增删查改 + 目录持久化（metadata + FAISS + embeddings） |
| `ExperienceRetriever`      | 快速路径检索：query embed → FAISS 搜索                    |
| `SkillKnowledgeBuilder`    | 记录成功 trace → 语义聚类 → LLM 蒸馏 pattern → 构建/更新经验条目   |
| `ExperienceAwareRetriever` | 外层包装器，组合 fast path（经验库） + slow path（树检索） + 自动收集  |
| `EmbeddingClient`          | 向量模型客户端，支持 OpenAI API 和 sentence-transformers    |
| `TraceRecord`              | 单条成功检索记录的数据结构（query + skill\_ids）                |
| `TraceDistiller`           | LLM 蒸馏器，将聚类后的 trace 概括为通用 pattern                |

