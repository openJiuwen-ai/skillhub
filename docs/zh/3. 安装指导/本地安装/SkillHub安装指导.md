# 本地安装指导

本文说明如何在本地安装并启动 **marketplace**，即 SkillHub 市场服务。步骤以 **Windows / PowerShell** 为例；Linux / macOS 可将路径与激活命令替换为对应写法，例如 `source .venv/bin/activate`。

> 本地安装适合开发调试。不想在本地安装 MySQL、Node.js 等依赖、只想快速跑起来的，见 [Docker 一键部署](../Docker方式安装/SkillHub安装指导-一键部署.md)。

## 1 环境要求

| 依赖 | 说明 |
|------|------|
| **Python** | 建议 **3.11+** |
| **包管理** | 需预先安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)，并确保 `uv --version` 可正常执行 |
| **MySQL** | 必选。当前版本仅支持 MySQL，需已安装并可连接；**须先手动建库**，见下文 |
| **对象存储** | 必选。使用 **MinIO** 或 **华为云 OBS**；资产发布包上传依赖 S3 兼容 API，需可访问的桶与密钥 |
| **登录与鉴权** | 按使用场景配置。浏览公开内容无需配置；调用受保护 API 可使用 GitCode Bearer Token；通过 Web 页面登录、发布和审核时需配置 OAuth |
| **Node.js** | 仅启动 Web 前端时需要。建议 **18+** 或 **20 LTS**；用于安装依赖并运行 `frontend`，即 Vite 开发服务器 |

> 仅浏览公开市场内容时无需登录。若要发布或审核 Skill，请按第 3.4 节准备 Web OAuth 应用和账号。

## 2 获取代码

```powershell
git clone https://gitcode.com/openJiuwen/skillhub.git
cd skillhub
```

后续命令默认从 **SkillHub 仓库根目录**开始执行。

## 3 准备依赖服务和账号

### 3.1 安装 MySQL

若本机尚未安装 MySQL，请先完成安装后再继续后续步骤：

- 通过 [MySQL Installer](https://dev.mysql.com/downloads/installer/) 安装，建议 8.0+
- 安装后确认服务已启动，并准备可执行建库和授权的 MySQL 管理员账号

### 3.2 创建数据库并授权

先确定 marketplace 使用的数据库名、账号和密码，再在 MySQL 客户端中建库并授权。以下示例创建数据库 `openjiuwen_market` 和业务账号 `skillhub`；请将密码替换为实际使用的强密码：

```sql
CREATE DATABASE IF NOT EXISTS openjiuwen_market
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE USER IF NOT EXISTS 'skillhub'@'%' IDENTIFIED BY '请替换为强密码';
GRANT ALL PRIVILEGES ON openjiuwen_market.* TO 'skillhub'@'%';
FLUSH PRIVILEGES;
```

已有 MySQL 账号时，可跳过 `CREATE USER`，并将 `GRANT` 中的账号替换为现有账号。完成后，在第 4 节的 `.env` 中填写相同的数据库名、账号和密码。

### 3.3 准备对象存储

在配置 `.env` 前，须先准备 **MinIO** 或 **华为云 OBS**，并创建 marketplace 使用的 Bucket。二者选择其一即可。

#### 3.3.1 MinIO 本地或自建

**1）下载 MinIO 服务端**：Windows 用户下载 [MinIO 服务端](https://dl.min.io/server/minio/release/windows-amd64/minio.exe)，保存到例如 `D:\minio\bin\`。Linux/macOS 用户请参考 [MinIO 官方文档](https://min.io/docs/minio/linux/index.html)。

**2）在 PowerShell 中设置自定义凭证并启动 MinIO**：

```powershell
$env:MINIO_ROOT_USER = "skillhub-admin"
$env:MINIO_ROOT_PASSWORD = "请替换为随机强密码"
D:\minio\bin\minio.exe server D:\minio\data --address "127.0.0.1:9000" --console-address "127.0.0.1:9001"
```

上述凭证仅在当前 PowerShell 会话中有效；新开终端启动 MinIO 时需重新设置。API 地址为 `http://127.0.0.1:9000`，控制台地址为 `http://127.0.0.1:9001`。

**3）打开 `http://127.0.0.1:9001`**，使用上述凭证登录控制台并创建 Bucket，例如 `openjiuwen-market-test`。

第 4 节 `.env` 中的 Access Key、Secret Key 和 Bucket 名称须与这里保持一致。

#### 3.3.2 华为云 OBS

如选择华为云 OBS，请参照[华为云 OBS：创建桶](https://support.huaweicloud.com/usermanual-obs/zh-cn_topic_0045829088.html)完成云上配置，并记录 Endpoint、AK、SK、区域和桶名称，供第 4 节配置使用。

> marketplace 启动时会通过 `head_bucket` 检查 Bucket。请先确认 Bucket 已创建且凭证具有访问权限，再继续配置 `.env`。

### 3.4 准备 GitCode OAuth 应用和审核账号

需要通过 Web 页面登录、发布和审核 Skill 时，按 [OAuth 登录配置](../../6.%20运维指南/基础部署/OAuth登录配置.md) 完成准备（仅浏览公开内容可跳过本节）。完成后记录以下值，供第 4 节填写 `.env`：

- GitCode OAuth 应用的 Client ID 和 Client Secret（应用主页和回调地址使用 `http://skillhub.local:9002`）
- 审核账号的 GitCode 登录名（需与发布账号不同，审核账号不能审核自己发布的 Skill）

## 4 配置环境变量

完成第 3 节的资源和账号准备后，将仓库根目录的 **`.env.example`** 复制为 **`.env`**，一次性填写数据库、对象存储、OAuth 和审核管理员配置。以下示例假设 MySQL 与 MinIO 均在本机运行，并使用 GitCode 登录：

```ini
# 服务基础配置
STORE_HOST=0.0.0.0
STORE_PORT=8100
# 默认关闭 DEBUG；仅在短时排障时改为 true
MARKET_DEBUG=false

# 数据库；与第 3.2 节创建的数据库和账号一致
DB_TYPE=mysql
DB_HOST=localhost
DB_PORT=3306
DB_USER=skillhub
DB_PASSWORD=请替换为强密码
STORE_DB_NAME=openjiuwen_market

# GitCode Token 鉴权；无需额外部署本地鉴权服务
AUTH_USER_API_URL=https://gitcode.com/api/v5/user

# GitCode Web 登录；使用第 3.4 节创建的 OAuth 应用
MARKET_GITCODE_OAUTH_ENABLED=true
MARKET_GITCODE_OAUTH_CLIENT_ID=你的GitCode客户端ID
MARKET_GITCODE_OAUTH_CLIENT_SECRET=你的GitCode客户端密钥
MARKET_GITCODE_OAUTH_REDIRECT_URI=http://skillhub.local:9002/api/v1/auth/oauth/gitcode/callback
MARKET_GITCODE_OAUTH_SCOPE=user_info
MARKET_OAUTH_FRONTEND_ORIGIN=http://skillhub.local:9002

# 人工审核管理员；填写第 3.4 节准备的审核账号登录名
MARKET_REVIEW_ADMIN_USERNAMES=reviewer_login

# 对象存储；与第 3.3 节设置的 MinIO 凭证保持一致
STORAGE_TYPE=MinIO
MARKET_BUCKET_NAME=openjiuwen-market-test
MARKET_S3_ENDPOINT=http://127.0.0.1:9000
MARKET_S3_ACCESS_KEY=skillhub-admin
MARKET_S3_SECRET_KEY=请替换为与MINIO_ROOT_PASSWORD相同的强密码

# 前端
FRONTEND_PORT=9002
BACKEND_URL=127.0.0.1
BACKEND_PORT=8100
```

如果第 3 节选择华为云 OBS，请将上例中的对象存储配置替换为：

```ini
STORAGE_TYPE=OBS
MARKET_CREDENTIALS_MODE=static
MARKET_BUCKET_NAME=你的OBS桶名称
MARKET_S3_ENDPOINT=https://obs.cn-north-4.myhuaweicloud.com
MARKET_S3_ACCESS_KEY=你的OBS_AK
MARKET_S3_SECRET_KEY=你的OBS_SK
MARKET_S3_REGION=cn-north-4
```

以上以华北-北京四为例；使用其他区域时，须同时修改 Endpoint 和 Region。

仅浏览公开内容时，可将 `MARKET_GITCODE_OAUTH_ENABLED` 设为 `false`，并省略 OAuth 和审核管理员配置。若改用 GitHub 登录，请参照[OAuth 登录配置](../../6.%20运维指南/基础部署/OAuth登录配置.md#github-oauth)替换对应变量。Client Secret 不得提交到仓库。

确认 `.env` 中的数据库、对象存储和账号信息均与第 3 节准备结果一致后，再继续启动服务。

## 5 安装依赖并启动 marketplace

首次使用 `uv` 时，请先参考 [uv 安装指南](https://docs.astral.sh/uv/getting-started/installation/)完成安装。确认 `uv --version` 可正常执行，且 MySQL 和 MinIO/OBS 均可访问后，在 **PowerShell** 或 **cmd** 中从仓库根目录执行：

```powershell
cd marketplace
uv sync
uv run python main.py
```

`uv sync` 会在 `marketplace` 目录创建并使用 `.venv`，无需手动激活虚拟环境。服务启动后默认监听 `0.0.0.0:8100`，实际地址由 `STORE_HOST` 和 `STORE_PORT` 决定。

如果依赖安装失败，请参考第 9 节“常见问题”。

## 6 安装依赖并启动 frontend

确认 marketplace 已按第 5 节启动后，新开一个 **PowerShell** 或 **cmd** 窗口，从仓库根目录执行：

```powershell
cd frontend
npm install
npm run dev
```

启动完成后：

- 已配置 OAuth：访问 `http://skillhub.local:9002`
- 仅浏览公开内容：也可访问终端显示的本地地址

前端默认通过 `/api/v1` 代理访问 marketplace，无需修改 API 地址。请保持 `BACKEND_PORT` 与 `STORE_PORT` 一致；本机安装时 `BACKEND_URL` 使用 `127.0.0.1`，不要填写监听地址 `0.0.0.0`。如需修改前端端口，请在仓库根目录的 `.env` 中设置 `FRONTEND_PORT`。

## 7 验证

浏览器访问 `http://skillhub.local:9002`。页面能够正常打开且市场内容可以加载，说明 frontend 与 marketplace 已正常连通。全新部署的市场首页为空，属正常现象：

![市场首页](../../assets/img/一键部署-市场首页.png)

使用准备的发布账号和审核账号，继续验证完整发布流程：

1. 使用发布账号登录并提交 Skill，确认状态为“人工审核中”。
2. 使用独立审核账号登录，在“待审核”中通过该 Skill。
3. 返回市场页面，确认该 Skill 已可见。

![发布后的市场首页](../../assets/img/一键部署-市场首页-已发布.png)

## 8 可选能力

完成基础部署后，以下能力可按需启用。不启用时核心功能不受影响：

| 能力 | 说明 | 不启用时的表现 |
|------|------|----------------|
| **系统审查** | 发布前自动检测安全风险 | 直接进入人工审核 |
| **检索系统** | 语义搜索，比关键词匹配更准 | 搜索退化为关键词匹配 |
| **分类标签** | 新发布 Skill 自动打分类标签，用于首页类别展示 | 首页无类别，Skill 无分类标签 |

### 8.1 系统审查

在 `.env` 中配置，模型接口需兼容 OpenAI Chat Completions：

```env
MARKET_SKILL_REVIEW_ENABLED=true
MARKET_SKILL_REVIEW_MODEL_BASE_URL=https://your-review-model-service/v1
MARKET_SKILL_REVIEW_MODEL_API_KEY=***
MARKET_SKILL_REVIEW_MODEL_NAME=test-review-model
MARKET_SKILL_REVIEW_MODEL_TIMEOUT_SECONDS=300
```

- 关闭（默认）：发布后直接进入人工审核
- 开启：先系统审查；通过后转为「待人工审核」，不通过则发布失败
- 开启时若未配齐模型参数，发布会被拒绝
- 覆盖普通 Skill 与 SwarmSkill

验证：提交 Skill 后，状态先显示「系统审查中」即生效。审查完成后可在「系统审查详情」页查看各维度检查结果与 AI 语义复核结论：

![系统审查详情](../../assets/img/一键部署-系统审查详情.png)

### 8.2 检索系统

开启语义搜索需要一个向量模型（Embedding）服务，接口兼容 OpenAI 的 `/v1/embeddings` 即可。部署了多个 marketplace 实例时需要 Redis 共享状态（含索引同步；OAuth 会话、限流等多实例能力同样依赖它），单实例不用配。

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

# Redis（多实例必需，单实例可留空）
REDIS_HOST=
REDIS_PORT=6379
REDIS_DB=0
MARKET_REDIS_PASSWORD=

# 定时任务
MARKET_RETRIEVAL_REBUILD_CRON=0 * * * *
MARKET_RETRIEVAL_REBUILD_ON_STARTUP=true
```

验证：启动后看日志，出现 `retrieval index rebuild run end` 就表示索引建好了。然后在市场页面用一个**近义词**搜索——例如 Skill 描述写的是「旅行」，改搜「旅游」——能搜到说明语义检索已生效；只有搜原词才命中，说明仍在走关键词兜底，索引未生效。

![语义检索效果](../../assets/img/一键部署-语义检索.png)

### 8.3 Skill 分类标签

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

## 9 常见问题

**部署问题**

- **启动时报 `Address already in use` / 端口被占用**：使用 `Get-NetTCPConnection -LocalPort 8100` 查找占用进程并停止旧服务；也可以修改 `STORE_PORT`，但须同步调整前端代理使用的 `BACKEND_PORT`。
- **`Unknown database`**：确认已执行建库 SQL，且 `STORE_DB_NAME` 与授权库名一致。
- **对象存储启动即报错，`head_bucket` 失败**：确认桶已创建、AK/SK 正确、`MARKET_S3_ENDPOINT` 可访问。
- **MinIO 启动报错「拒绝访问」或「找不到路径」**：MinIO 不会自动创建数据目录，需先手动创建。例如 `mkdir D:\minio\data -Force`，然后再启动 MinIO。
- **`uv sync` 失败或中断**：先确认网络可用且 Python 版本为 3.11 或更高版本，再删除 `marketplace/.venv` 并重新执行 `uv sync`。仍然失败时，在 `marketplace` 目录依次执行 `uv venv` 和 `uv pip install -e .`。
- **鉴权相关错误，401/403 或连接失败**：确认 `.env` 中 `AUTH_USER_API_URL` 配置正确，默认 `https://gitcode.com/api/v5/user`，且请求携带了有效的 GitCode Bearer Token。
- **前端页面无法加载资产列表 / 接口连错端口**：确认 marketplace 已启动；`BACKEND_PORT` 应对应 `STORE_PORT`，`BACKEND_URL` 应填写前端进程可访问的后端地址。本机开发可使用 `127.0.0.1`，不要将监听地址 `0.0.0.0` 作为代理目标，见第 6 节。
- **浏览器报 CORS / 跨域错误**：多为页面与 API **不同源**且未走代理。请使用 Vite 默认的 **`/api/v1` 相对路径** 与 **`BACKEND_*` 代理**；不要将 API 基地址改为 `http://...:8100` 等后端绝对地址。

**检索问题**

- **日志 `retrieval module not importable`**：检索子模块未正确安装，确认 `uv sync` 已执行且无报错
- **日志 `retrieval: failed to create embedding client`**：`MARKET_RETRIEVAL_EMBEDDING_API_BASE_URL` 或 `MARKET_RETRIEVAL_EMBEDDING_API_KEY` 配置有误，或服务不可达
- **日志 `retrieval_search: index not ready for group=skill, fallback`**：索引尚未构建完成，接口已降级为数据库查询，等待构建完成即可
- **搜索无结果**：确认 Embedding API 可正常调用，且索引已构建完成
- **多实例索引不同步**：确认 Redis 已配置且可连通；未配 Redis 时索引热加载仅在本进程生效
- **日志 `skill-tag 分类功能未启用`**：分类模型未配置或不可用，按 8.3 节配置 `MARKET_RETRIEVAL_SKILL_TAG_LLM_MODEL` / `_API_BASE_URL` / `_API_KEY` 后重启
- **索引构建报 `'gbk' codec can't encode character '\u280b'`**：Windows 控制台默认 GBK 编码，进度动画中的 Unicode 字符导致构建中断，检索会一直停留在关键词匹配。在启动 marketplace 前执行 `$env:PYTHONIOENCODING = "utf-8"` 再启动即可。注意这个变量要设为进程环境变量，写进 `.env` 文件无效；长期使用可加入 Windows 系统环境变量。

## 10 更多文档

| 文档 | 说明 |
|------|------|
| [TeamSkillsHub 接口参考](../../7.%20API参考/TeamSkillsHub-接口参考.md) | **推荐** - 端点总览、curl 示例、可见性规则 |
| [TeamSkillsHub API](../../7.%20API参考/TeamSkillsHub.md) | OpenAPI YAML 与错误码速查 |
| [ClawHub 兼容层](../../7.%20API参考/ClawHub兼容层.md) | ClawHub CLI 协议适配 |
| [用户指南索引](../../4.%20用户指南/README.md) | 终端用户操作与 FAQ |
