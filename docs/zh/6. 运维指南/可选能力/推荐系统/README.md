# 推荐系统

推荐系统是 SkillHub 的可选增强能力，用于市场首页「推荐精选」提供个性化 Skill 排序，并对外暴露 `POST /api/v1/recommend`。「全部」与分类页签按 `install_count` 查表。基础部署可以不启用。

部署步骤见[安装指导](../../../3.%20安装指导/本地安装/SkillHub安装指导.md)可选能力「推荐系统」小节。接口字段见[推荐系统 API](../../../7.%20API参考/推荐系统API.md)。设计细节见[开发指南 / 推荐系统](../../../5.%20开发指南/推荐系统/README.md)。

**给测试**：先看下面「启动时会不会自动跑一次？」和「怎么验收」两节，不要按「服务一启动四条离线任务都会跑一遍」来理解。

## 能力概览

| 场景 | 行为 |
|------|------|
| 已登录且 Redis 有该用户行为序列 | Milvus 向量召回 → MMR 多样性重排（`source=user_history`） |
| 无历史 / 召回失败 | Redis `install_count` 快照兜底（`source=topk_install`），条数不超过本次 `top_k` |
| `MARKET_RECOMMENDER_ENABLED=false` | 不走推荐；列表 `order_by=recommend` 自动回退为 `install_count` |
| 有搜索关键词 | 不走推荐，仍走检索 / 关键词逻辑 |
| 列表「全部」/ 分类页签 | 不走推荐，MySQL `install_count` 排序（老逻辑） |
| 列表「推荐精选」（`order_by=recommend` 且无 `category_id`） | 个性化召回，条数上限 `MARKET_REC_LIST_TOP_K` |

依赖：**MySQL**（行为与资产元数据）、**对象存储**（离线拉包）、**Redis**（用户序列与 TopK 快照）、**Milvus**（向量索引）、**独立 Embedding API**（与检索侧配置分离）。

## 主要配置变量

本表默认值为代码默认值。密钥类变量在配置了 `SERVER_AES_MASTER_KEY` 时须填密文，规则同其他 `MARKET_*` 密钥。

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `MARKET_RECOMMENDER_ENABLED` | 是否启用推荐（路由 + 列表推荐路径 + 离线调度） | `false` |
| `MARKET_REC_LIST_TOP_K` | 首页「推荐精选」一次召回上限，再按 page 切片；hydrate 后 `OFFLINE` 会再少几条。前端角标读 `GET /site/config` 的 `rec_list_top_k` | `50` |
| `MARKET_REC_REBUILD_ON_STARTUP` | 启动时是否立即跑 `redis_sync` + `milvus_full` | `true`（`.env.example` 示例常为 `false`） |
| `MARKET_REC_MMR_LAMBDA` | MMR 权重 λ∈[0,1]：越大越偏相关，越小越偏打散；`1.0`≈关闭多样性 | `0.5` |
| `MARKET_REC_EMBEDDING_API_BASE_URL` | 推荐 Embedding API（OpenAI-compatible `/embeddings`） | 空 |
| `MARKET_REC_EMBEDDING_API_KEY` | 推荐 Embedding 密钥（走 `SecurityUtils` 解密） | 空 |
| `MARKET_REC_EMBEDDING_MODEL` | 推荐 Embedding 模型名 | 空 |
| `MARKET_REC_EMBEDDING_BATCH_SIZE` | 离线建索引批大小 | `16` |
| `MILVUS_HOST` / `MILVUS_PORT` | Milvus 地址。Windows 调 WSL Docker 时填脚本打印的 WSL IP，不要假设永远是 `127.0.0.1` | `127.0.0.1` / `19530` |
| `MILVUS_COLLECTION` | 集合名 | `skill_index` |
| `MILVUS_USER` / `MILVUS_PASSWORD` | Milvus 鉴权（服务端开启 authorization 时必填；密码可走 `SecurityUtils` 密文） | 空（不鉴权） |
| `REDIS_TOPK_INSTALL_KEY` | 下载量快照 key | `skill_rec:topk:install` |
| `REDIS_TOPK_K` | `0`=快照写入全量 install 排序；在线兜底仍按本次 `top_k` 截断（列表路径用 `MARKET_REC_LIST_TOP_K`） | `0` |
| `REDIS_TOPK_TTL_SECONDS` | TopK 快照 TTL（每次同步会覆盖并续期） | `7200` |
| `REDIS_USER_SEQ_KEY_PREFIX` | 用户序列前缀 | `skill_rec:user` |
| `REDIS_USER_SEQ_TTL_SECONDS` | 用户序列 TTL | `7200` |
| `MARKET_REC_PACKAGE_SYNC_CRON` | 拉包同步 cron | `30 * * * *` |
| `MARKET_REC_MILVUS_INCREMENTAL_CRON` | Milvus 增量索引 cron | `0 * * * *` |
| `MARKET_REC_MILVUS_FULL_CRON` | Milvus 全量重建 cron | `0 3 * * *` |
| `MARKET_REC_REDIS_SYNC_CRON` | Redis 快照同步 cron | `15 * * * *` |

> **与检索区分**：推荐必须使用 `MARKET_REC_EMBEDDING_*`，不要复用 `MARKET_RETRIEVAL_EMBEDDING_*`。换模型维度后需 **Milvus full recreate**（`MARKET_REC_REBUILD_ON_STARTUP=true` 或手动 `python -m recommender.offline.milvus_index --mode full`）。

## 启动时会不会自动跑一次？

**不会把四条定时任务都立刻执行一遍。** 前提还得是 `MARKET_RECOMMENDER_ENABLED=true`，否则推荐路由、调度器、列表推荐路径全部不挂。

启动时实际发生的事：

1. **注册 cron**（只是登记，等到点才跑）

   | 任务 | 默认 crontab | 含义 |
   |------|----------------|------|
   | `package_sync` | `30 * * * *` | 每小时的第 30 分：从对象存储拉 Skill zip |
   | `milvus_incremental` | `0 * * * *` | 每小时整点：增量更新向量 |
   | `milvus_full` | `0 3 * * *` | 每天 03:00：删掉旧 collection 再全量重建 |
   | `redis_sync` | `15 * * * *` | 每小时的第 15 分：写 Redis 快照 |

2. **启动补偿**（可选，由 `MARKET_REC_REBUILD_ON_STARTUP` 控制）

   | 配置 | 启动当下 |
   |------|----------|
   | `true` | **立刻异步**跑 `redis_sync`，成功后再跑 `milvus_full`。**不跑** `package_sync`，也**不跑** incremental |
   | `false` | 启动什么离线任务都不跑，等到上面 crontab 的下一个触发点 |

代码里该开关默认是 `true`；根目录 `.env.example` 写成 `false`。**以你环境里的 `.env` 为准。** 本地若已写 `MARKET_REC_REBUILD_ON_STARTUP=false`，重启 marketplace **不会**自动建 Milvus、也不会刷 Redis。

启动日志怎么认：

- 已启用：出现 `recommender enabled — ... rebuild_on_startup=true/false`
- 会立刻建库：接着有 `REBUILD_ON_STARTUP=true, scheduling immediate offline jobs`，然后 `recommender job begin name=redis_sync(startup)`、`milvus_full(startup)`
- 任务跑完：`recommender job: redis_sync done`、`milvus_full done`（或 `recommender job failed`）

注意：`milvus_full` 要读本地下载目录里的 zip。若从未跑过 `package_sync`、目录是空的，full 会建出空（或几乎空）的 collection，**这不是接口挂了**。首次验收建议：`REBUILD_ON_STARTUP=true` **之前**先手动跑一次 `package_sync`，或启动后再手动补跑拉包 + full。

**Milvus 里没有 collection 时**：跑 `milvus_index`（incremental / full）或第一次在线检索时会按当前 embedding 维度**自动创建** `skill_index`。full 模式若已有同名 collection 会先删再建。

## 怎么验收

按顺序做，前面失败后面不用测「个性化」。

### 1. 开关与鉴权

- `.env`：`MARKET_RECOMMENDER_ENABLED=true`，改完**重启** marketplace。
- `POST /api/v1/recommend` **必须**带鉴权，否则 `401`：
  - 测试脚本 / 伙伴服务：`X-System-Token: <与 .env 里 SYSTEM_ADMIN_TOKEN 一致>`
  - 登录用户：`Authorization: Bearer <OAuth token>`
- 未开开关：该接口 `503`，`error=recommender_disabled`。列表 `order_by=recommend` 会静默改成按下载量排，**不是报错**。

示例（把 token、user_id、端口换成你们环境）：

```bash
curl -sS -X POST "http://127.0.0.1:8100/api/v1/recommend" \
  -H "Content-Type: application/json" \
  -H "X-System-Token: <SYSTEM_ADMIN_TOKEN>" \
  -d "{\"user_id\":\"<用户ID>\",\"request_id\":\"qa-1\",\"top_k\":10}"
```

看响应里的 `data.source` 和 `data.items`，不要只看 HTTP 200。

### 2. 看 `source` 判断走到哪条链路（这是预期，不是随机）

| `data.source` | 含义 | 什么时候会出现 |
|----------------|------|----------------|
| `user_history` | 用该用户 Redis 里的下载/点赞/收藏当种子，去 Milvus 找相似 Skill，再 MMR 打散 | 这个 `user_id` 在 Redis 里**已有**行为序列，且 Milvus 召回非空 |
| `topk_install` | **没走个性化**，返回 Redis 里按 `install_count` 排好的快照 | 用户 ID 为空；或该用户还没有行为；或 Milvus 挂了 / 召回空 |
| `items` 为空且 `source=topk_install` | Redis 快照 key 还不存在或已过期没续上 | **还没成功跑过 `redis_sync`**，或 Redis 连错实例 |

新号、从未下载/点赞/收藏的账号，**第一次调用就是 `topk_install`**，这是设计如此。要测 `user_history`：先用该账号下载（或点赞/收藏）几个 Skill → 等 `redis_sync` 跑完（或手动跑）→ 再调推荐。

Bearer 调用时：body 里的 `user_id` 必须空着，或等于登录用户，否则 `403 recommend_user_mismatch`。System Token 可以指定任意 `user_id`；传空字符串表示故意走冷启动 TopK。

### 3. 列表页（市场前端）

- 「全部」和各个分类页签：按 MySQL `install_count` 排（老逻辑），**不走推荐**。
- 「推荐精选」：`GET /api/v1/plugins?order_by=recommend`（不带 `category_id`），一次最多 `MARKET_REC_LIST_TOP_K` 条，再按页切片。hydrate 会丢掉 `OFFLINE` / 类型不符的 ID，所以 `total` 可能 **小于该上限**。
- 侧边栏「推荐精选」旁的数字：**不调推荐接口**，始终显示 `min(已上架数, rec_list_top_k)`（`rec_list_top_k` 来自 `GET /site/config`）。点进该 Tab 后也不改成列表 `total`。
- 搜索框有关键词时走检索，**不会**走推荐。
- 带 `category_id` 再传 `order_by=recommend` 会回退成下载量排序（分类下暂不展示推荐）。

列表失败时后端会打日志并改回 `install_count`，页面仍 200，所以单看「全部」有数据不能证明推荐生效。应打开「推荐精选」，或对一下 `POST /api/v1/recommend` 的 `source`，或看日志 `recommend path: source=...`。

打开「推荐精选」若卡住数秒：多半是 Windows 进程在连 WSL 里的 Milvus，而容器还没起来。在线连接超时约 **5 秒**后回退 Redis 下载量（仍不超过 `MARKET_REC_LIST_TOP_K`）。先跑 `tools/milvus/start_milvus.ps1`，确认 healthz 为 OK 再测个性化。

### 4. 离线数据没就绪时（最常见「测不通」）

推荐接口能通，但结果空 / 全是下载量序，通常是离线三步没做完：

1. `package_sync`：对象存储 zip → `data/skill_packages/`（可用 `MARKET_REC_DOWNLOAD_DIR` 改目录）
2. `milvus_index`：解析 `SKILL.md` 调 **推荐专用** Embedding，写入 Milvus
3. `redis_sync`：MySQL 行为 + 下载量排行 → Redis

这三步 **marketplace 进程会 load 根目录 `.env`**；你在另一个终端手动跑 Python 模块时，**不会自动读 `.env`**，必须自己把变量加载进当前窗口（见下节）。

### 5. 类目

请求里带 `category_id`（根类目 ID）。填错或不存在的 ID **不会 404**，结果可能是空列表，属预期。

## Redis 写入节奏

- 默认每小时第 15 分执行 `redis_sync`（可改 `MARKET_REC_REDIS_SYNC_CRON`）。
- 写入内容：`topk_install` 快照 + 各用户 `download` / `like` / `star` 序列。
- **覆盖**：每次用新快照 `SET` 同一 key，并重置 TTL；TTL 是同步中断时的过期兜底，不是“只活两小时就永久消失”。

## 离线任务

marketplace 进程内 APScheduler（需 `MARKET_RECOMMENDER_ENABLED=true`）：

| 任务 | 作用 |
|------|------|
| `package_sync` | 从对象存储拉最新 Skill zip 到本地下载目录 |
| `milvus_incremental` / `milvus_full` | 解析 `SKILL.md`（name+description）向量化并 upsert Milvus；full 可重建 schema |
| `redis_sync` | 从 MySQL 聚合行为与 install 排行写入 Redis |

也可在 `marketplace/` 下手动执行。这些命令**自己不读 `.env`**。启动 `python main.py` 时才会加载仓库根目录（`skillhub_0731/.env`）进进程。另开终端只激活 venv，**拿不到** marketplace 已经 load 过的变量。

Windows PowerShell 示例（在 `marketplace/`，已 `uv sync` / 激活 `.venv`）。`.env` 只灌进这一条子进程，把最后的模块名换成要跑的任务：

```powershell
python -c "from pathlib import Path; from dotenv import load_dotenv; import subprocess, sys; load_dotenv(Path('..')/'.env', override=True); raise SystemExit(subprocess.call([sys.executable, '-m', 'recommender.offline.package_sync']))"
```

Linux / macOS 可先 `set -a; source ../.env; set +a` 再执行：

```bash
python -m recommender.offline.package_sync
python -m recommender.offline.milvus_index --mode incremental
python -m recommender.offline.milvus_index --mode full
python -m recommender.offline.redis_sync
```

各命令要什么：

| 命令 | 最少要能连上 | 成功时大概能看到 |
|------|----------------|------------------|
| `package_sync` | MySQL + 对象存储 | 下载目录出现 zip；日志 `package_sync done` |
| `milvus_index --mode incremental` | 上一项 + Embedding + Milvus | collection 没有则自动建；按变更 upsert |
| `milvus_index --mode full` | 同上 | **先删再建** collection，全量写入；换模型维度必须用这个 |
| `redis_sync` | MySQL + Redis | Redis 出现 `skill_rec:topk:install`；有行为的用户有 `skill_rec:user:{id}:download` 等 |

手动跑时缺哪类配置，就会连错库、连不上 Milvus，或 Embedding 401。推荐 Embedding 必须用 `MARKET_REC_EMBEDDING_*`，填检索那套 `MARKET_RETRIEVAL_EMBEDDING_*` **无效**。

## 运维关注点

- Redis / Milvus 网络可达（容器或跨机部署时注意主机名与端口）。Windows + WSL Docker：marketplace 在 Windows 时 `MILVUS_HOST` 用 WSL 网卡 IP（`hostname -I` / `start_milvus.ps1` 输出）；WSL 休眠后容器会没了，表现为推荐接口空等数秒再兜底。本地部署见仓库根目录 `tools/milvus/本地容器化部署.md`。华为云 DCS 见[配置切换-Redis_DCS 与 MinIO_OBS](../../配置切换-Redis_DCS与MinIO_OBS.md)。
- Milvus 若开启 `authorizationEnabled`，须配置 `MILVUS_USER` / `MILVUS_PASSWORD`（默认内置多为 `root`/`Milvus`，生产改密）；密码支持 `SecurityUtils` 密文。未开鉴权时这两项留空即可。
- Embedding API 配额与维度一致性；换模型维度或 schema 升级后必须 `--mode full`。
- 首次启用建议临时 `MARKET_REC_REBUILD_ON_STARTUP=true`，并确保下载目录已有 zip（或先手动 `package_sync`）。日志出现 `recommender job: redis_sync done` 且 milvus upsert 成功后，可改回 `false` 以免每次发版都全量重建。
- 用户无 Redis 历史时 `source=topk_install`，属预期。
- Redis key `skill_rec:topk:install` 缺失（TTL 到期且同步没跑）时，API 仍 200 但 `items=[]`。补跑 `redis_sync` 即可，不必当接口缺陷。

## 相关开发 / API 文档

- [开发指南 / 推荐系统](../../../5.%20开发指南/推荐系统/README.md)
- [API 参考 / 推荐系统 API](../../../7.%20API参考/推荐系统API.md)

## 配置边界

推荐相关变量保留在根目录 `.env.example` 的 `[Recommender]` 段。未启用时可整段保持默认关闭；启用时须配齐 Redis、Milvus 与 `MARKET_REC_EMBEDDING_*`。
