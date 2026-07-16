# Docker 方式安装指导

本文说明如何用 Docker Desktop 自行构建镜像并运行 SkillHub 完整服务。服务包含 Backend 和 Frontend 两个容器：Backend 是 marketplace 后端；Frontend 是前端 Web，由 Nginx 托管静态资源并反向代理 API。依赖的 MySQL、MinIO 可以用 Docker 容器，也可以用宿主机上已有的。步骤以 **Windows / PowerShell** 为例。

> 本文适合需要复用宿主机已有 MySQL / MinIO、或希望手动控制各容器启动方式的情况。宿主机没有这些依赖、想一条命令启动全套的，见 [Docker 一键部署](./SkillHub安装指导-一键部署.md)；生产和多副本部署见 [K8s 方式安装指导](../K8s方式安装/SkillHub安装指导.md)。

**目标**：Backend 与 Frontend 容器启动后，浏览器与本机 CLI 能正常调用 API，Skill 包能正常下载。下载分两步：服务端返回预签名 URL，客户端再直连对象存储。

## 网络概览

Skill 下载涉及三段链路，任何一段配置不当都会表现为「能发布但下载失败」：

| 环节 | 说明 | 配置不当时的常见现象 |
|------|------|----------------------|
| Frontend → Backend | Nginx 将 `/api/` 转到 `BACKEND_URL:BACKEND_PORT` | 页面空白、接口 502 |
| Backend → MinIO | 容器内访问 `MARKET_S3_ENDPOINT` | 启动失败、上传/读对象报错 |
| Browser / CLI → MinIO | 打开预签名 URL，直连 `MARKET_S3_ENDPOINT` 中的主机与端口 | 下载失败、超时、浏览器 502 |

`MARKET_S3_ENDPOINT` 中的主机必须同时满足两个条件：Backend 容器能访问，运行浏览器和 CLI 的机器也能访问。

## 1 环境要求

| 依赖 | 说明 |
|------|------|
| **Docker Desktop** | 推荐 WSL 2 后端；`docker info` 能正常输出 |
| **MySQL** | 必选。可用 Docker 容器或宿主机已安装的 MySQL；**须先手动建库**，见第 3.1 节 |
| **对象存储** | 必选。使用 **MinIO** 或 **华为云 OBS**；资产发布包上传依赖 S3 兼容 API，需可访问的桶与密钥 |
| **登录与鉴权** | 按使用场景配置。浏览公开内容无需配置；调用受保护 API 可使用 GitCode Bearer Token；通过 Web 页面登录、发布和审核时需配置 OAuth |

> 仅浏览公开市场内容时无需登录。若要发布或审核 Skill，请按第 4 节准备 OAuth 应用和账号。

## 2 获取代码

```powershell
git clone https://gitcode.com/openJiuwen/skillhub.git
cd skillhub
```

后续命令默认从 **SkillHub 仓库根目录**开始执行。

## 3 准备依赖服务

### 3.1 MySQL

可用宿主机已安装的 MySQL，也可用 Docker 新起一个容器。

#### 3.1.1 用 Docker 起 MySQL（推荐联调）

```powershell
docker pull mysql:8.0

docker run -d --name mysql-market `
  -p 3306:3306 `
  -e MYSQL_ROOT_PASSWORD=请替换为Root密码 `
  -e MYSQL_DATABASE=openjiuwen_market `
  -e MYSQL_USER=skillhub `
  -e MYSQL_PASSWORD=请替换为强密码 `
  -v mysql-market-data:/var/lib/mysql `
  mysql:8.0 `
  --character-set-server=utf8mb4 --collation-server=utf8mb4_0900_ai_ci
```

验证：执行 `docker logs mysql-market`，出现 `ready for connections` 表示可以连接，首次启动约需 10～30 秒。若宿主机 3306 已被占用，把端口映射改为 `-p 3307:3306`，并把第 5 节 `.env.docker` 中的 `DB_PORT` 改为 `3307`。

#### 3.1.2 使用宿主机已有 MySQL

确认 MySQL 服务已启动，并准备可执行建库和授权的 MySQL 管理员账号。

#### 3.1.3 创建数据库并授权

第 3.1.1 节的 Docker MySQL 已按 `MYSQL_DATABASE` 自动建库，可跳过本节。其他情况请手动建库并授权，库名、账号要与第 5 节 `.env.docker` 一致：

```sql
CREATE DATABASE IF NOT EXISTS openjiuwen_market
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE USER IF NOT EXISTS 'skillhub'@'%' IDENTIFIED BY '请替换为强密码';
GRANT ALL PRIVILEGES ON openjiuwen_market.* TO 'skillhub'@'%';
FLUSH PRIVILEGES;
```

已有 MySQL 账号时，可跳过 `CREATE USER`，并将 `GRANT` 中的账号替换为现有账号。

#### 3.1.4 MySQL 的连接配置

以下值将在第 5 节填入 `.env.docker`。后端容器访问宿主机上的 MySQL 时，`DB_HOST` 填 `host.docker.internal`，Docker Desktop 默认会把该域名解析到宿主机：

```env
DB_TYPE=mysql
DB_HOST=host.docker.internal
DB_PORT=3306
DB_USER=skillhub
DB_PASSWORD=请替换为强密码
STORE_DB_NAME=openjiuwen_market
```

若 `host.docker.internal` 在你的环境解析异常，改为本机 IPv4，即 `ipconfig` 中以太网或 WLAN 的地址，并确认后端容器内能访问该 IP。

### 3.2 对象存储

在配置 `.env.docker` 前，须先准备 **MinIO** 或 **华为云 OBS**，并创建 marketplace 使用的 Bucket。二者选择其一即可。

#### 3.2.1 MinIO（Docker 容器）

**1）拉取并启动 MinIO**：

```powershell
docker pull minio/minio

docker run -d --name minio `
  -e "MINIO_ROOT_USER=skillhub-admin" `
  -e "MINIO_ROOT_PASSWORD=请替换为强密码" `
  -p 9000:9000 `
  -p 9001:9001 `
  -v "minio-data:/data" `
  minio/minio server /data --console-address ":9001"
```

若 `--name minio` 已被占用，改成 `--name skillhub-minio`。

**2）打开控制台 `http://localhost:9001`**，用启动时设置的账号登录，创建与第 5 节 `.env.docker` 中 `MARKET_BUCKET_NAME` 完全一致的 Bucket。

> 以上端口会发布到宿主机。共享或生产环境还应限制防火墙访问，创建最小权限专用账号，并配置 TLS；不要让 marketplace 长期使用 MinIO root 账号。

**3）记录 MinIO 的连接配置**，将在第 5 节填入 `.env.docker`，凭证与启动 MinIO 时设置的账号保持一致：

```env
STORAGE_TYPE=MinIO
MARKET_BUCKET_NAME=openjiuwen-market-test
MARKET_S3_ENDPOINT=http://host.docker.internal:9000
MARKET_S3_ACCESS_KEY=skillhub-admin
MARKET_S3_SECRET_KEY=请替换为强密码
# 可选：预签名 URL 有效期（秒），默认 1800
# MARKET_S3_PRESIGNED_EXPIRES=1800
```

后端容器访问宿主机上的 MinIO，`MARKET_S3_ENDPOINT` 中的主机用 `host.docker.internal`；浏览器和 CLI 也靠这个地址直连 MinIO 下载。若解析异常，改成 `http://<本机 IPv4>:9000`。

#### 3.2.2 华为云 OBS

如选择华为云 OBS，请参照[华为云 OBS：创建桶](https://support.huaweicloud.com/usermanual-obs/zh-cn_topic_0045829088.html)完成云上配置，并记录 Endpoint、AK、SK、区域和桶名称。`.env.docker` 中替换为：

```env
STORAGE_TYPE=OBS
MARKET_CREDENTIALS_MODE=static
MARKET_BUCKET_NAME=你的OBS桶名称
MARKET_S3_ENDPOINT=https://obs.cn-north-4.myhuaweicloud.com
MARKET_S3_ACCESS_KEY=你的OBS_AK
MARKET_S3_SECRET_KEY=你的OBS_SK
MARKET_S3_REGION=cn-north-4
```

以上以华北-北京四为例；使用其他区域时，须同时修改 Endpoint 和 Region。

> marketplace 启动时会通过 `head_bucket` 检查 Bucket。请先确认 Bucket 已创建且凭证具有访问权限，再继续配置 `.env.docker`。

## 4 准备 OAuth 应用和审核账号

需要通过 Web 页面登录、发布和审核 Skill 时，按 [OAuth 登录配置](../../6.%20运维指南/基础部署/OAuth登录配置.md) 完成准备（仅浏览公开内容可跳过本节）。完成后记录以下值，供第 5 节填写 `.env.docker`：

- GitCode OAuth 应用的 Client ID 和 Client Secret（应用主页和回调地址使用 `http://skillhub.local:9002`）
- 审核账号的 GitCode 登录名（需与发布账号不同，审核账号不能审核自己发布的 Skill）

## 5 配置环境变量

完成第 3、4 节的资源和账号准备后，将仓库根目录的 **`.env.example`** 复制为 **`.env.docker`**：

```powershell
Copy-Item ".env.example" ".env.docker"
```

编辑 `.env.docker`，一次性填写数据库、对象存储、OAuth 和审核管理员配置。密码等取值请避免 `#`、空格和引号，防止 `--env-file` 解析异常。以下示例假设 MySQL 与 MinIO 均以 Docker 容器运行在本机，并使用 GitCode 登录：

```ini
# 服务基础配置
STORE_HOST=0.0.0.0
STORE_PORT=8100
# 默认关闭 DEBUG；仅在短时排障时改为 true
MARKET_DEBUG=false

# 数据库；与第 3.1 节创建的数据库和账号一致
DB_TYPE=mysql
DB_HOST=host.docker.internal
DB_PORT=3306
DB_USER=skillhub
DB_PASSWORD=请替换为强密码
STORE_DB_NAME=openjiuwen_market

# GitCode Token 鉴权；无需额外部署本地鉴权服务
AUTH_USER_API_URL=https://gitcode.com/api/v5/user

# GitCode Web 登录；使用第 4 节创建的 OAuth 应用
MARKET_GITCODE_OAUTH_ENABLED=true
MARKET_GITCODE_OAUTH_CLIENT_ID=你的GitCode客户端ID
MARKET_GITCODE_OAUTH_CLIENT_SECRET=你的GitCode客户端密钥
MARKET_GITCODE_OAUTH_REDIRECT_URI=http://skillhub.local:9002/api/v1/auth/oauth/gitcode/callback
MARKET_GITCODE_OAUTH_SCOPE=user_info
MARKET_OAUTH_FRONTEND_ORIGIN=http://skillhub.local:9002

# 人工审核管理员；填写第 4 节准备的审核账号登录名
MARKET_REVIEW_ADMIN_USERNAMES=reviewer_login

# 对象存储；与第 3.2 节设置的 MinIO 凭证保持一致
STORAGE_TYPE=MinIO
MARKET_BUCKET_NAME=openjiuwen-market-test
MARKET_S3_ENDPOINT=http://host.docker.internal:9000
MARKET_S3_ACCESS_KEY=skillhub-admin
MARKET_S3_SECRET_KEY=请替换为强密码

# 前端本机调试（npm run dev）用；前端容器在第 7.2 节用 -e 传参
FRONTEND_PORT=9002
BACKEND_URL=host.docker.internal
BACKEND_PORT=8100
```

如果第 3.2 节选择华为云 OBS，请将上例中的对象存储配置替换为 OBS 配置，见第 3.2.2 节。

仅浏览公开内容时，可将 `MARKET_GITCODE_OAUTH_ENABLED` 设为 `false`，并省略 OAuth 和审核管理员配置。若改用 GitHub 登录，请参照[OAuth 登录配置](../../6.%20运维指南/基础部署/OAuth登录配置.md#github-oauth)替换对应变量。Client Secret 不得提交到仓库。

确认 `.env.docker` 中的数据库、对象存储和账号信息均与第 3、4 节准备结果一致后，再继续构建镜像。

## 6 构建镜像

镜像由本机 `docker build` 生成，不依赖预置镜像仓库。以下命令在仓库根目录执行。

推荐顺序：MySQL、MinIO 就绪且 `.env.docker` 已保存 → 构建镜像 → 启动 Backend 并确认日志无持续报错 → 再启动 Frontend。

### 6.1 构建 Backend 镜像

```powershell
docker build -f docker/Dockerfile.skillhub-backend -t skillhub-backend:latest .
```

若 PyPI 超时或 JSON 截断，构建时指定镜像源：

```powershell
docker build -f docker/Dockerfile.skillhub-backend `
  --build-arg INDEX_URL=https://mirrors.aliyun.com/pypi/simple `
  --build-arg TRUSTED_HOST=mirrors.aliyun.com `
  -t skillhub-backend:latest .
```

apt 源不可达（`deb.debian.org` 连接超时、报 `Unable to locate package git`）时，在命令中追加 `--build-arg APT_MIRROR=mirrors.aliyun.com`。

验证：执行 `docker images skillhub-backend`，能看到 `latest` 标签即构建成功。

### 6.2 构建 Frontend 镜像

```powershell
docker build -f docker/Dockerfile.skillhub-frontend -t skillhub-frontend:latest .
```

站点默认部署在根路径，API 前缀为 `/api/v1`。若需挂载到 `/hub` 并使用 `/hub/api/v1`，增加构建参数：

```powershell
docker build -f docker/Dockerfile.skillhub-frontend `
  --build-arg FRONTEND_BASE_PATH=hub `
  --build-arg VITE_API_BASE_URL=/hub/api/v1 `
  -t skillhub-frontend:latest .
```

验证：执行 `docker images skillhub-frontend`，能看到 `latest` 标签即构建成功。

## 7 启动服务

### 7.1 启动 Backend

```powershell
docker run --rm --name skillhub-backend `
  -p 8100:8100 `
  --env-file ".env.docker" `
  skillhub-backend:latest
```

验证：日志出现 `Application startup complete` 表示启动成功。新开一个终端执行 `curl http://localhost:8100/api/health`，有正常响应而非连接拒绝即可。若日志反复报数据库连接或 `head_bucket` 错误，先按第 10 节排查再继续。

可选：持久化后端运行时数据目录：

```powershell
mkdir marketplace\data -Force

docker run --rm --name skillhub-backend `
  -p 8100:8100 `
  -v "${PWD}\marketplace\data:/app/data" `
  --env-file ".env.docker" `
  skillhub-backend:latest
```

### 7.2 启动 Frontend

确认 Backend 已启动且日志无持续报错后，新开一个终端执行：

```powershell
docker run -d --rm --name skillhub-frontend `
  -p 9002:9002 `
  -e BACKEND_URL=host.docker.internal `
  -e BACKEND_PORT=8100 `
  skillhub-frontend:latest
```

注意事项：

- `BACKEND_URL` 和 `BACKEND_PORT` 必须通过 `-e` 传入。前端容器内 Nginx 只读环境变量，不认 `.env.docker`。若只写进 `.env.docker` 而不加 `-e`，Nginx 会把 API 转发到前端容器自身的 `localhost:8100`，页面接口全部 502。
- `BACKEND_PORT` 必须等于第 7.1 节 `-p` 左侧的宿主机端口。示例为 `8100`；若后端改为 `-p 18080:8100`，这里要设 `BACKEND_PORT=18080`。
- Backend 未对浏览器开放 CORS。请通过 `http://localhost:9002` 同源访问页面，不要从其他来源的页面直接请求 `http://...:8100/api/...`。

验证：执行 `curl http://localhost:9002/health`，返回 `healthy` 表示 Nginx 已就绪。再执行 `curl http://localhost:9002/api/health`，有正常响应说明 Nginx 到 Backend 的反代已通。若构建时使用了 `FRONTEND_BASE_PATH=hub`，浏览器入口改为 `http://localhost:9002/hub`。

## 8 验证

### 8.1 健康检查

| 检查项 | 命令 |
|--------|------|
| Backend 健康检查 | `curl http://localhost:8100/api/health` |
| Frontend 健康检查 | `curl http://localhost:9002/health`，应返回 `healthy` |
| 经 Frontend 反代访问 Backend | `curl http://localhost:9002/api/health` |

Windows PowerShell 5.1 中 `curl` 是 `Invoke-WebRequest` 的别名，输出格式不同；想看原始响应内容可改用 `curl.exe`。

### 8.2 浏览器验证

已配置 OAuth 时，浏览器访问 `http://skillhub.local:9002`；仅浏览公开内容时也可直接访问 `http://localhost:9002`。页面能够正常打开且市场内容可以加载，说明 Frontend 与 Backend 已正常连通。

### 8.3 完整发布流程

使用准备的发布账号和审核账号，继续验证完整发布流程：

1. 使用发布账号登录并提交 Skill，确认状态为“人工审核中”。
2. 使用独立审核账号登录，在“待审核”中通过该 Skill。
3. 返回市场页面，确认该 Skill 已可见。

## 9 可选能力

完成基础部署后，以下能力可按需启用，不启用时核心功能不受影响。配置都写在 `.env.docker` 中，改完后须重启 Backend 容器才生效：先执行 `docker stop skillhub-backend`，再重新执行第 7.1 节的启动命令。

| 能力 | 说明 | 不启用时的表现 |
|------|------|----------------|
| **系统审查** | 发布前自动检测安全风险 | 直接进入人工审核 |
| **检索系统** | 语义搜索，比关键词匹配更准 | 搜索退化为关键词匹配 |
| **分类标签** | 新发布 Skill 自动打分类标签，用于首页类别展示 | 首页无类别，Skill 无分类标签 |

### 9.1 系统审查

在 `.env.docker` 中配置，模型接口需兼容 OpenAI Chat Completions：

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

验证：重启 Backend 后提交 Skill，状态先显示「系统审查中」即生效。

### 9.2 检索系统

开启语义搜索需要一个向量模型（Embedding）服务，接口兼容 OpenAI 的 `/v1/embeddings` 即可。部署了多个 Backend 实例时需要 Redis 共享状态（含索引同步；OAuth 会话、限流等多实例能力同样依赖它），单实例不用配。

在 `.env.docker` 中配置：

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

模型服务和 Redis 若运行在宿主机上，容器内同样用 `host.docker.internal` 访问；若运行在其他机器上，填写对应机器可访问的地址。

验证：重启 Backend 后查看日志，前台启动的直接看终端输出，否则执行 `docker logs skillhub-backend`。出现 `retrieval index rebuild run end` 就表示索引建好了。然后在市场页面用一个**近义词**搜索——例如 Skill 描述写的是「旅行」，改搜「旅游」——能搜到说明语义检索已生效；只有搜原词才命中，说明仍在走关键词兜底，索引未生效。

### 9.3 Skill 分类标签

新发布的 Skill 由对话模型（LLM）自动分配分类标签，用于首页类别展示。它随检索模块一同启动，无需单独部署，模型接口兼容 OpenAI 即可。

在 `.env.docker` 中配置：

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

## 10 常见问题

**部署问题**

- **`docker build` 拉取基础镜像超时**：检查 Docker Desktop 网络；可配置镜像源，在 Settings → Docker Engine 中设置 registry-mirrors。
- **`docker build` Backend 时 PyPI 超时或 JSON 截断**：按第 6.1 节指定镜像源构建。
- **Backend 启动报错 `head_bucket` 失败**：确认桶已创建、AK/SK 正确、`MARKET_S3_ENDPOINT` 在容器内可访问。
- **Frontend 启动后页面空白或接口 502**：确认 `BACKEND_URL` 和 `BACKEND_PORT` 已通过 `-e` 传入，而不是只写在 `.env.docker` 里；`curl http://localhost:9002/api/health` 应非 502。
- **`host.docker.internal` 不通或解析不对**：编辑 `C:\Windows\System32\drivers\etc\hosts`，确保存在 `127.0.0.1 host.docker.internal`；注释掉 Docker Desktop 写入的非回环地址行；保存后执行 `ipconfig /flushdns`。
- **浏览器 502、curl 正常**：浏览器走系统代理，本机 MinIO 请求被转到公司代理。在「设置 → 网络和 Internet → 代理」中增加「不使用代理」地址：`localhost;127.*;host.docker.internal;<local>`；或暂时关闭代理对比。
- **`Address already in use` / 端口被占用**：使用 `Get-NetTCPConnection -LocalPort 8100` 查找占用进程并停止旧服务；也可以修改 `-p` 映射的宿主机端口。

**对象存储问题**

- **下载失败，报 `NoSuchKey`**：数据库中有 Skill 元数据记录，但对象存储中对应的 zip 文件不存在。检查 `MARKET_S3_ENDPOINT`、`MARKET_BUCKET_NAME` 与实际存储一致；登录 MinIO 控制台确认文件是否存在。
- **下载失败，预签名 URL 过期**：预签名 URL 默认 30 分钟有效，由 `MARKET_S3_PRESIGNED_EXPIRES` 控制。重新获取下载链接即可。
- **下载仍失败时快速核对**：执行 `Test-NetConnection -ComputerName 127.0.0.1 -Port 9000` 确认端口可达；桶名与 `MARKET_BUCKET_NAME` 一致；改 `.env.docker` 后需重启 Backend 容器。

**OAuth 问题**

- **登录失败或回调 404**：确认 `.env.docker` 中 `MARKET_GITCODE_OAUTH_ENABLED=true`；回调地址与 GitCode 应用设置完全一致；`MARKET_OAUTH_FRONTEND_ORIGIN` 与浏览器实际访问地址一致，含端口。
- **登录循环**：检查 `MARKET_OAUTH_FRONTEND_ORIGIN` 与回调地址是否匹配；Client ID 和 Client Secret 未填反。

**检索问题**

- **日志 `retrieval: failed to create embedding client`**：`MARKET_RETRIEVAL_EMBEDDING_API_BASE_URL` 或 `MARKET_RETRIEVAL_EMBEDDING_API_KEY` 配置有误，或容器内访问不到该服务。
- **日志 `retrieval_search: index not ready for group=skill, fallback`**：索引尚未构建完成，接口已降级为数据库查询，等待构建完成即可。
- **搜索无结果**：确认 Embedding API 在容器内可正常调用，且日志已出现 `retrieval index rebuild run end`。
- **多实例索引不同步**：确认 Redis 已配置且各 Backend 容器内可连通；未配 Redis 时索引热加载仅在本容器生效。
- **日志 `skill-tag 分类功能未启用`**：分类模型未配置或不可用，按第 9.3 节配置 `MARKET_RETRIEVAL_SKILL_TAG_LLM_MODEL` / `_LLM_API_BASE_URL` / `_LLM_API_KEY` 后重启 Backend 容器。

## 11 本机 CLI

CLI 通常直连 `http://127.0.0.1:8100` 调 API，再按返回的预签名 URL 访问 MinIO。若 Backend 启动时改了 `-p` 映射，把 8100 换成对应的宿主机端口。请保证 `MARKET_S3_ENDPOINT` 对 CLI 所在环境可达，判断逻辑与浏览器相同；公司代理可能影响 CLI 的 HTTP 请求，需与浏览器类似配置或绕过。

示例：

```bash
curl --location 'http://localhost:8100/api/v1/plugins'
```

使用 `X-System-Token` 时，取值与 `.env.docker` 中的 `SYSTEM_ADMIN_TOKEN` 一致；使用 `Authorization` 时，参见 [GitCode 访问令牌](https://docs.gitcode.com/docs/help/home/user_center/security_management/user_pat)。

## 12 更多文档

| 文档 | 说明 |
|------|------|
| [TeamSkillsHub 接口参考](../../7.%20API参考/TeamSkillsHub-接口参考.md) | **推荐** - 端点总览、curl 示例、可见性规则 |
| [TeamSkillsHub API](../../7.%20API参考/TeamSkillsHub.md) | OpenAPI YAML 与错误码速查 |
| [ClawHub 兼容层](../../7.%20API参考/ClawHub兼容层.md) | ClawHub CLI 协议适配 |
| [OAuth 登录配置](../../6.%20运维指南/基础部署/OAuth登录配置.md) | GitCode / GitHub OAuth 完整配置 |
| [故障排查](../../6.%20运维指南/基础部署/故障排查.md) | 更多部署问题排查 |
| [升级说明](../升级说明.md) | 升级前检查项和变更记录 |
