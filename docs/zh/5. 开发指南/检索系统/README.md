# openjiuwen-skillsdispatch

`openjiuwen-skillsdispatch` 是 openJiuwen 的技能分发能力包，面向后续需要复用构建和检索能力的仓库。包内保留索引构建、BM25 检索、embedding 检索、progressive 检索，以及这些能力依赖的公共模型和工具代码。

## 安装

本地开发验证：

```bash
cd marketplace/retrieval
python -m pip install -e .
```

发布到 pip 源后安装：

```bash
python -m pip install openjiuwen-skillsdispatch
```

如果需要构建或检索 embedding/FAISS 索引，再安装可选依赖：

```bash
python -m pip install "openjiuwen-skillsdispatch[embedding]"
```

FAISS 不是 progressive 检索本身的必选依赖。progressive 主要依赖树索引和 LLM；只有 embedding 索引构建、FAISS 向量索引反序列化、embedding 检索链路需要 FAISS。默认安装不强制带上 `faiss-cpu`，避免 Python 版本、系统平台或本地编译环境导致基础安装失败。

## 需要保留的源码/配置

打包分发时需要保留以下源码目录：

- `indexing/`：离线索引构建入口，负责扫描 skill/plugin 目录，生成 catalog、BM25、embedding、tree 等索引产物。
- `retrieval/`：在线检索入口，负责加载索引并提供 BM25、embedding、progressive、混合检索能力。
- `models/`：构建和检索共用的数据结构定义。
- `orchestration/`：检索链路中复用的 LLM client、命名等编排辅助能力。
- `shared/`：S3/OBS 路径处理、rich 兼容层等公共工具。
- `pyproject.toml`：pip 包元信息、运行依赖、可选依赖和打包目录配置。
- `README.md`：作为 pip 包的长描述文档，会进入 wheel metadata。

不需要打进 wheel 的内容包括测试、样例数据、训练脚本、demo 服务、临时构建产物、`__pycache__`、`*.egg-info`、`build/`、`dist/` 等。

## 构建索引

BM25 构建示例：

```python
from indexing.workflows.artifacts import IndexBuildRuntimeConfig
from indexing.workflows.index_builder import IndexBuilder

IndexBuilder.build(
    ["/path/to/skills-or-plugins"],
    "/path/to/output-index",
    item_type="plugin",
    runtime_config=IndexBuildRuntimeConfig(build_method="bm25"),
)
```

如果要构建 embedding 索引，需要提供 embedding client，并确保安装了 FAISS 可选依赖。

## 检索索引

BM25 检索示例：

```python
from retrieval.service.models import RetrievalMethod, SearchConfig
from retrieval.service.retriever import Retriever

retriever = Retriever.from_index("/path/to/output-index")
result = retriever.search_details(
    "current time",
    config=SearchConfig(top_k=3, method=RetrievalMethod.BM25),
)

print(result.payloads)
```

progressive 检索需要传入 LLM client 和模型名：

```python
retriever = Retriever.from_index(
    "/path/to/output-index",
    llm_openai_client=openai_client,
    llm_model="your-model",
)
```

当索引里同时有 embedding 产物且调用方传入 embedding client 时，检索器可以按配置使用 embedding 结果做补充或混合排序。

## 构建 wheel

在 `marketplace/retrieval` 目录下执行：

```bash
python -m pip wheel . --no-deps --no-build-isolation -w dist
```

流水线可以通过仓库根目录的 `skillhub_build.sh` 统一更新版本号并构建 wheel，产物会输出到仓库根目录的 `dist/`。
