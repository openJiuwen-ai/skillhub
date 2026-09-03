# SkillHub 一键部署（Docker Compose）

本文说明如何用 Docker Compose 一条命令启动 SkillHub 全套服务：MySQL、Redis、MinIO、Backend、Frontend 五个容器，外加一个自动建 Bucket 的初始化容器。所有依赖都是容器内全新的独立环境，不要求宿主机安装 MySQL、Redis 或 MinIO。步骤以 **Windows / PowerShell** 为例。

适用场景：**仅用于开发自验证**（本地快速拉起全套服务试用与联调）。为兼容 Linux（backend 经 host-gateway 访问 MinIO），MinIO S3 API 端口绑定 `0.0.0.0`，请勿在不可信网络环境运行。生产或多副本部署见 [K8s 方式安装指导](../K8s方式安装/SkillHub安装指导.md)。如果宿主机已有 MySQL 或 MinIO 并希望复用，请使用 [Docker 方式安装指导](./SkillHub安装指导.md)。

## 1 环境要求

| 依赖 | 说明 |
|------|------|
| **Docker Desktop** | 推荐 WSL 2 后端；`docker info` 能正常输出，且支持 `docker compose` 命令 |
| **登录与鉴权** | 按使用场景配置。浏览公开内容无需配置；通过 Web 页面登录、发布和审核时需配置 OAuth，见第 3 节 |

> 仅浏览公开市场内容时无需登录。若要发布或审核 Skill，请先完成第 3 节。

## 2 获取代码

```powershell
git clone https://gitcode.com/openJiuwen/skillhub.git
cd skillhub
```

后续命令默认从 **SkillHub 仓库根目录**开始执行。

## 3 准备 OAuth 应用和审核账号

需要通过 Web 页面登录、发布和审核 Skill 时，按 [OAuth 登录配置](../../6.%20运维指南/基础部署/OAuth登录配置.md) 完成准备（仅浏览公开内容可跳过本节）。完成后记录以下值，供第 4 节填写 `.env`：

- GitCode OAuth 应用的 Client ID 和 Client Secret（应用主页和回调地址使用 `http://localhost:9002`）
- 审核账号的 GitCode 登录名（需与发布账号不同，审核账号不能审核自己发布的 Skill）

## 4 配置 .env

将仓库根目录的 **`.env.example`** 复制为 **`.env`**：

```powershell
Copy-Item ".env.example" ".env"
```

编辑 `.env`，确认或修改以下配置。密码等取值请避免 `#`、空格和引号，防止解析异常：

```ini
# ── 服务基础 ──
STORE_HOST=127.0.0.1
STORE_PORT=8100
MARKET_DEBUG=false

# ── 数据库 ──
# compose 会自动创建数据库和 skillhub 专用账号，无需手动建库
DB_TYPE=mysql
DB_HOST=localhost          # 本地开发值；compose 内部自动覆盖为 mysql
DB_PORT=3306               # MySQL 端口；compose 内部自动覆盖为 3306，宿主机映射端口见 MYSQL_HOST_PORT
DB_USER=root               # compose 会创建非 root 专用账号 skillhub 并覆盖此项
DB_PASSWORD=root
STORE_DB_NAME=openjiuwen_market
# 可选：宿主机映射端口（默认 3001，避免与宿主机 MySQL 3306 冲突）
# MYSQL_HOST_PORT=3001

# ── Redis ──
REDIS_HOST=                # 留空即可；compose 内部自动覆盖为 redis
REDIS_PORT=6379
# 可选：宿主机映射端口（默认 6379；与宿主机 Redis 冲突时改为其他端口）
# REDIS_HOST_PORT=6379

# ── 对象存储（MinIO） ──
STORAGE_TYPE=MinIO
MARKET_BUCKET_NAME=openjiuwen-market-test
# compose 会自动设置 MARKET_S3_ENDPOINT（基于 MINIO_API_PORT），
# .env 中的值不影响 Docker 部署，仅用于本地非 Docker 开发
MARKET_S3_ACCESS_KEY=minioadmin
MARKET_S3_SECRET_KEY=minioadmin
# 可选：修改 MinIO API 端口（compose 中预签名 URL 端口会自动跟随此变量）
# MINIO_API_PORT=3003

# ── GitCode Web 登录（仅浏览公开内容时设为 false 并省略以下 OAuth 配置） ──
MARKET_GITCODE_OAUTH_ENABLED=true
MARKET_GITCODE_OAUTH_CLIENT_ID=你的GitCode客户端ID
MARKET_GITCODE_OAUTH_CLIENT_SECRET=你的GitCode客户端密钥
MARKET_GITCODE_OAUTH_REDIRECT_URI=http://localhost:9002/api/v1/auth/oauth/gitcode/callback
MARKET_GITCODE_OAUTH_SCOPE=user_info
MARKET_OAUTH_FRONTEND_ORIGIN=http://localhost:9002

# ── 审核管理员 ──
MARKET_REVIEW_ADMIN_USERNAMES=reviewer_login
```

几点说明：

- MySQL、Redis 和 MinIO 由 compose 自动创建并初始化，账号密码直接取自上面的值，无需手动建库建桶。
- `DB_HOST`、`DB_PORT`、`REDIS_HOST` 等在 `.env` 中填写本地开发值即可，compose 的 `environment` 段会自动覆盖为 Docker 内部服务名和端口。宿主机映射端口独立配置：`MYSQL_HOST_PORT`（默认 3001）、`REDIS_HOST_PORT`（默认 6379），与 `DB_PORT`/`REDIS_PORT` 解耦，避免本地开发值污染端口映射。
- `MARKET_S3_ENDPOINT` 由 compose 根据 `MINIO_API_PORT` 自动生成（默认 `http://minio:3003`），`.env` 中的值不影响 Docker 部署。Backend 容器通过 `extra_hosts` 将 `minio` 解析到宿主机网关，经映射端口访问 MinIO；浏览器需配置 hosts 映射才能使用预签名下载 URL（见第 4.1 节）。

### 4.1 浏览器下载预签名 URL（可选）

compose 已将 `MARKET_S3_ENDPOINT` 设为 `http://minio:3003`（端口跟随 `MINIO_API_PORT`），Backend 容器通过 `extra_hosts` 可正常访问 MinIO 进行上传/下载。但浏览器需要能解析 `minio` 主机名才能打开预签名下载 URL。

如需在浏览器中直接下载 Skill 包，只需在 hosts 文件中添加映射：

编辑 hosts 文件（Windows: `C:\Windows\System32\drivers\etc\hosts`，Linux/Mac: `/etc/hosts`），追加：

   ```
   127.0.0.1 minio
   ```

> 不配置此项不影响发布、列表、搜索等核心功能，仅影响浏览器直接下载 Skill 包的预签名 URL。
>
> 如需使用外部 MinIO/OBS 并自定义 `MARKET_S3_ENDPOINT`，请使用 `docker/docker-compose.override.yml` 覆盖 backend 的 `environment` 段（参见第 8 节）。

## 5 一键启动

> 本节及后续命令均在**仓库根目录**执行。Compose 文件位于 `docker/docker-compose.yml`，需带 `--env-file .env` 让根目录 `.env` 同时生效于变量插值与容器注入，命令前缀统一为：
> `docker compose -f docker/docker-compose.yml --env-file .env`

```powershell
docker compose -f docker/docker-compose.yml --env-file .env up -d --build
```

首次运行需要构建 Backend 和 Frontend 镜像，耗时取决于网络，后续启动秒级完成。

**构建加速**：如果 PyPI 或 apt 下载缓慢、超时，设置环境变量后重新构建：

```powershell
# PowerShell 临时设置
$env:PIP_INDEX_URL = "https://mirrors.aliyun.com/pypi/simple"
$env:PIP_TRUSTED_HOST = "mirrors.aliyun.com"
$env:APT_MIRROR = "mirrors.aliyun.com"
docker compose -f docker/docker-compose.yml --env-file .env up -d --build
```

也可在 `.env` 末尾取消注释 `PIP_INDEX_URL`、`PIP_TRUSTED_HOST`、`APT_MIRROR` 三行并填写镜像地址。

验证：执行 `docker compose -f docker/docker-compose.yml --env-file .env ps`，mysql、redis、minio、backend、frontend 均为 `Up (healthy)`，minio-init 显示 `Exited (0)` 属正常，它是一次性建桶任务，完成后自动退出。在 Docker Desktop 中可以看到 skillhub 分组下的 6 个容器：

![skillhub 容器列表](../../assets/img/一键部署-容器列表.png)

## 6 验证

### 6.1 健康检查

```powershell
curl.exe http://localhost:8100/api/health
curl.exe http://localhost:9002/health
curl.exe http://localhost:9002/api/health
```

均有正常响应即服务可用。Windows PowerShell 5.1 中 `curl` 是 `Invoke-WebRequest` 的别名，输出格式不同，这里用 `curl.exe` 获取原始响应。

### 6.2 浏览器验证

浏览器访问 `http://localhost:9002`。页面能够正常打开且市场内容可以加载，说明 Frontend 与 Backend 已正常连通。全新部署的市场首页为空，属正常现象：

![市场首页](../../assets/img/一键部署-市场首页.png)

### 6.3 完整发布流程

使用准备的发布账号和审核账号，继续验证完整发布流程：

1. 使用发布账号登录并提交 Skill，确认状态为"审核中"。
2. 使用独立审核账号登录，在"待审核"中通过该 Skill。
3. 返回市场页面，确认该 Skill 已可见。

![发布后的市场首页](../../assets/img/一键部署-市场首页-已发布.png)

## 7 可选能力

完成基础部署后，以下能力可按需启用，不启用时核心功能不受影响。配置都写在 `.env` 中，改完后执行 `docker compose -f docker/docker-compose.yml --env-file .env up -d backend` 重建 Backend 容器生效。

| 能力　　　　 | 说明　　　　　　　　　　　　　　　　　　　　　| 不启用时的表现　　　　　　　　|
| --------------| -----------------------------------------------| -------------------------------|
| **审查** | 发布前自动检测安全风险　　　　　　　　　　　　| 直接进入审核　　　　　　　|
| **检索系统** | 语义搜索，比关键词匹配更准　　　　　　　　　　| 搜索退化为关键词匹配　　　　　|
| **分类标签** | 新发布 Skill 自动打分类标签，用于首页类别展示 | 首页无类别，Skill 无分类标签　|
| **推荐系统** | 首页「推荐精选」个性化排序（上限 `MARKET_REC_LIST_TOP_K`）　　　　　　　　| 「全部」/分类按 `install_count` 等字段排序 |

### 7.1 审查

在 `.env` 中配置，模型接口需兼容 OpenAI Chat Completions：

```env
MARKET_SKILL_REVIEW_ENABLED=true
MARKET_SKILL_REVIEW_MODEL_BASE_URL=https://your-review-model-service/v1
MARKET_SKILL_REVIEW_MODEL_API_KEY=***
MARKET_SKILL_REVIEW_MODEL_NAME=test-review-model
MARKET_SKILL_REVIEW_MODEL_TIMEOUT_SECONDS=300
```

- 关闭（默认）：发布后直接进入审核
- 开启：先审查；通过后转为「待审核」，不通过则发布失败
- 开启时若未配齐模型参数，发布会被拒绝
- 覆盖普通 Skill 与 SwarmSkill

验证：重建 Backend 后提交 Skill，状态先显示「审查中」即生效。审查完成后可在「审查详情」页查看各维度检查结果与 AI 语义复核结论：

![审查详情](../../assets/img/一键部署-系统审查详情.png)

### 7.2 检索系统

开启语义搜索需要一个向量模型（Embedding）服务，接口兼容 OpenAI 的 `/v1/embeddings` 即可。

在 `.env` 中配置：

```env
# 向量模型（必需）
MARKET_RETRIEVAL_EMBEDDING_API_BASE_URL=https://your-embedding-service/v1
MARKET_RETRIEVAL_EMBEDDING_API_KEY=***
MARKET_RETRIEVAL_EMBEDDING_MODEL=test-embedding-model
MARKET_RETRIEVAL_EMBEDDING_BATCH_SIZE=16

# 检索策略（默认值，无需改动）
MARKET_RETRIEVAL_BUILD_METHOD=embedding_bm25
MARKET_RETRIEVAL_SEARCH_METHOD=embedding

# 定时任务
MARKET_RETRIEVAL_REBUILD_CRON=0 * * * *
MARKET_RETRIEVAL_REBUILD_ON_STARTUP=true
```

模型服务若运行在宿主机上，容器内用 `host.docker.internal` 访问；运行在其他机器上则填写对应地址。Redis 已由 compose 自动提供，无需额外配置。

验证：重建 Backend 后执行 `docker compose -f docker/docker-compose.yml --env-file .env logs backend | Select-String "retrieval index rebuild run end"`，有输出就表示索引建好了。然后在市场页面用一个**近义词**搜索——例如 Skill 描述写的是「旅行」，改搜「旅游」——能搜到说明语义检索已生效；只有搜原词才命中，说明仍在走关键词兜底，索引未生效。

![语义检索效果](../../assets/img/一键部署-语义检索.png)

### 7.3 Skill 分类标签

新发布的 Skill 由对话模型（LLM）自动分配分类标签，用于首页类别展示。它随检索模块一同启动，无需单独部署，模型接口兼容 OpenAI 即可。

在 `.env` 中配置：

```env
# 分类用的对话模型；不配时会自动复用检索系统的主模型配置
MARKET_RETRIEVAL_SKILL_TAG_LLM_MODEL=test-chat-model
MARKET_RETRIEVAL_SKILL_TAG_LLM_API_BASE_URL=https://your-chat-model-service/v1
MARKET_RETRIEVAL_SKILL_TAG_LLM_API_KEY=***

# 定时任务，默认每分钟执行一次，无需改动
MARKET_RETRIEVAL_SKILL_TAG_CRON=* * * * *
MARKET_RETRIEVAL_SKILL_TAG_ON_STARTUP=true
```

- 分类是增量进行的：只处理新发布、有变更或还没分类的 Skill，不会重复分类
- 如果没配模型或模型不可用，服务照常运行，只是不执行分类、首页没有类别展示，启动日志里会有明确报错

验证：发布一个 Skill，日志出现 `retrieval skill-tag refresh run end` 后，首页对应类别下就能看到该 Skill。

![分类标签效果](../../assets/img/一键部署-分类标签.png)

### 7.4 推荐系统

首页「推荐精选」走个性化推荐（上限 `MARKET_REC_LIST_TOP_K`）；「全部」与分类页仍按下载量。需 Milvus 与独立的 `MARKET_REC_EMBEDDING_*`。Redis 已由 compose 自动提供。在 `.env` 中配置：

```env
MARKET_RECOMMENDER_ENABLED=true
MARKET_REC_LIST_TOP_K=50
MARKET_REC_REBUILD_ON_STARTUP=true
MARKET_REC_MMR_LAMBDA=0.5
MARKET_REC_EMBEDDING_API_BASE_URL=https://your-embedding-service/v1
MARKET_REC_EMBEDDING_API_KEY=***
MARKET_REC_EMBEDDING_MODEL=your-embedding-model
MILVUS_HOST=host.docker.internal
MILVUS_PORT=19530
MILVUS_COLLECTION=skill_index
# MILVUS_USER=root
# MILVUS_PASSWORD=***
```

改完后重建 Backend。完整说明见[运维指南 / 推荐系统](../../6.%20运维指南/可选能力/推荐系统/README.md)。

## 8 跳过部分服务（使用外部依赖）

如果宿主机已有 MySQL、Redis 或 MinIO 并希望复用，可创建 `docker/docker-compose.override.yml` 来跳过对应容器并指向外部服务。仓库提供了示例模板：

```powershell
Copy-Item "docker/docker-compose.override.yml.example" "docker/docker-compose.override.yml"
```

编辑 `docker/docker-compose.override.yml`，按模板中的注释取消注释并修改地址。Docker Compose 会自动合并 `docker/` 目录下的 override 文件，无需额外 `-f` 参数。

常用场景：

| 场景 | 跳过容器 | 需修改的 Backend 环境变量 |
|------|----------|--------------------------|
| 使用外部 MySQL | mysql | `DB_HOST`、`DB_PORT`、`DB_USER`、`DB_PASSWORD` |
| 使用外部 Redis | redis | `REDIS_HOST`、`REDIS_PORT` |
| 使用外部 MinIO/OBS | minio、minio-init | `MARKET_S3_ENDPOINT`、`MARKET_S3_ACCESS_KEY`、`MARKET_S3_SECRET_KEY` |

## 9 常用命令

```powershell
# 查看所有服务状态
docker compose -f docker/docker-compose.yml --env-file .env ps

# 查看日志
docker compose -f docker/docker-compose.yml --env-file .env logs -f backend

# 重启某个服务
docker compose -f docker/docker-compose.yml --env-file .env restart backend

# 修改 .env 后重建某个服务
docker compose -f docker/docker-compose.yml --env-file .env up -d backend

# 停止全部服务（数据保留在数据卷中）
docker compose -f docker/docker-compose.yml --env-file .env down

# 停止并清空 MySQL、Redis 和 MinIO 的全部数据，恢复到全新环境
docker compose -f docker/docker-compose.yml --env-file .env down -v
```

## 10 常见问题

**部署问题**

- **`docker compose up` 报端口 3001、6379、3003 或 9002 被占用**：宿主机已有其他服务占用该端口。在 `.env` 中修改对应变量（`MYSQL_HOST_PORT`、`REDIS_HOST_PORT`、`MINIO_API_PORT`、`FRONTEND_PORT`）为其他端口，或停止占用端口的服务。
- **Backend 反复重启，日志报 `Can't connect to MySQL server`**：MySQL 尚未完全就绪。compose 已配置健康检查和自动重启（`restart: unless-stopped`），通常等待片刻后自动恢复。如持续失败，检查 MySQL 容器日志：`docker compose -f docker/docker-compose.yml --env-file .env logs mysql`。
- **修改 `DB_PASSWORD` 后 Backend 报 `Access denied`**：MySQL 数据卷已在首次启动时初始化，密码以首次为准，之后改 `.env` 不会生效。执行 `docker compose -f docker/docker-compose.yml --env-file .env down -v` 清卷重建（注意会清空已有数据），或登录 MySQL 手动修改密码。
- **Frontend 页面空白或接口 502**：确认 Backend 容器为 `running`。若 Backend 容器曾被重建，Frontend 内 Nginx 缓存了旧地址，执行 `docker compose -f docker/docker-compose.yml --env-file .env restart frontend` 即可。
- **`docker build` 时 PyPI 超时或 JSON 截断**：设置环境变量 `PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple`、`PIP_TRUSTED_HOST=mirrors.aliyun.com` 后重新构建，见第 5 节。
- **构建时报 `deb.debian.org` 连接超时或 `Unable to locate package git`**：apt 源不可达，设置环境变量 `APT_MIRROR=mirrors.aliyun.com` 后重新构建，见第 5 节。
- **点击下载没有跳转或浏览器打不开下载地址**：预签名下载 URL 基于 `MARKET_S3_ENDPOINT` 生成。compose 已将其设为 `http://minio:3003`（端口跟随 `MINIO_API_PORT`），Backend 容器通过 `extra_hosts` 可正常访问。浏览器需配置 hosts 映射 `127.0.0.1 minio` 才能解析预签名 URL 中的 `minio` 主机名，详见第 4.1 节。

**OAuth 问题**

- **登录失败或回调 404**：确认 `.env` 中 `MARKET_GITCODE_OAUTH_ENABLED=true`；回调地址与 GitCode 应用设置完全一致；`MARKET_OAUTH_FRONTEND_ORIGIN` 与浏览器实际访问地址一致，含端口。改完执行 `docker compose -f docker/docker-compose.yml --env-file .env up -d backend` 生效。

**检索问题**

- **日志 `retrieval: failed to create embedding client`**：`MARKET_RETRIEVAL_EMBEDDING_API_BASE_URL` 或 `MARKET_RETRIEVAL_EMBEDDING_API_KEY` 配置有误，或容器内访问不到该服务。
- **日志 `retrieval_search: index not ready for group=skill, fallback`**：索引尚未构建完成，接口已降级为数据库查询，等待构建完成即可。
- **搜索无结果**：确认 Embedding API 在容器内可正常调用，且日志已出现 `retrieval index rebuild run end`。
- **日志 `skill-tag 分类功能未启用`**：分类模型未配置或不可用，按第 7.3 节配置后重建 Backend 容器。

**推荐问题**

- **`503 recommender is disabled`**：未设置 `MARKET_RECOMMENDER_ENABLED=true` 或未重建 Backend
- **一直像下载量排序**：用户无 Redis 行为序列，或 `redis_sync` / Milvus 未就绪；见[运维指南 / 推荐系统](../../6.%20运维指南/可选能力/推荐系统/README.md)

## 11 更多文档

| 文档 | 说明 |
|------|------|
| [Docker 方式安装指导](./SkillHub安装指导.md) | 手动构建运行单容器，支持复用宿主机 MySQL / MinIO |
| [本地安装指导](../本地安装/SkillHub安装指导.md) | 不使用 Docker，直接在本地运行 Python + Node.js |
| [TeamSkillsHub 接口参考](../../7.%20API参考/TeamSkillsHub-接口参考.md) | **推荐** - 端点总览、curl 示例、可见性规则 |
| [推荐系统 API](../../7.%20API参考/推荐系统API.md) | 个性化推荐 HTTP 接口 |
| [OAuth 登录配置](../../6.%20运维指南/基础部署/OAuth登录配置.md) | GitCode / GitHub OAuth 完整配置 |
| [故障排查](../../6.%20运维指南/基础部署/故障排查.md) | 更多部署问题排查 |
| [升级说明](../升级说明.md) | 升级前检查项和变更记录 |
