# K8s 方式安装指导

本文说明如何将 SkillHub 部署到 K8s 集群，包括 marketplace 后端和 frontend 前端；在线体验、系统审查、语义检索等可选能力见第 9 章。MySQL 和对象存储（MinIO 或华为云 OBS）复用已有服务，不部署在 K8s 集群内，需保证集群内 Pod 网络可达。

> K8s 方式适合生产和多副本部署。本地试用见 [Docker 一键部署](../Docker方式安装/SkillHub安装指导-一键部署.md)，本地开发见 [本地安装](../本地安装/SkillHub安装指导.md)。

## 部署流程总览

| 步骤 | 章节 | 内容 | 说明 |
|------|------|------|------|
| 1 | 第 3 章 | 准备 MySQL、对象存储、OAuth 应用 | 仅浏览公开内容可跳过 OAuth（3.3 节） |
| 2 | 第 4 章 | 创建 Namespace 和 Secret，修改 ConfigMap | 填写依赖服务的地址和密钥 |
| 3 | 第 5～6 章 | 构建镜像并加载到集群 | 远程集群需先推送到镜像仓库 |
| 4 | 第 7 章 | 部署 marketplace 和 frontend | |
| 5 | 第 8 章 | 验证部署 | 含发布审核的端到端验证 |
| 6 | 第 9 章 | 可选能力 | 在线体验、系统审查、语义检索、分类标签，按需启用 |

镜像已构建过的情况下，全程约 20～30 分钟；首次构建镜像需额外 15～25 分钟。仅部署基础服务时，完成第 8 章即部署结束，第 9 章的可选能力随时可按需追加。

## 1 环境要求

| 依赖 | 说明 |
|------|------|
| **K8s 集群** | 可用集群，`kubectl get nodes` 正常。支持 Docker Desktop K8s、kind、minikube 或远程集群 |
| **Docker** | 构建镜像需要，`docker info` 能正常输出 |
| **MySQL** | 必选。MySQL 8.0+，已部署且 K8s Pod 可通过网络访问；**须先手动建库**，见第 3.1 节 |
| **对象存储** | 必选。使用 **MinIO** 或 **华为云 OBS**；需已创建 Bucket；地址须同时被 K8s Pod 和浏览器/CLI 访问（见第 3.2 节） |
| **登录与鉴权** | 按使用场景配置。浏览公开内容无需配置；通过 Web 页面登录、发布和审核时需配置 OAuth |
| **LLM 服务** | 仅在线体验（可选能力，见第 9.4 节）需要。需可访问的 LLM API 地址和密钥 |

## 2 获取代码

```bash
git clone https://gitcode.com/openJiuwen/skillhub.git
cd skillhub
```

后续命令默认从 **SkillHub 仓库根目录**开始执行。

## 3 准备依赖服务和账号

### 3.1 准备 MySQL

确认 MySQL 已部署且 K8s Pod 可通过网络访问，再在 MySQL 客户端中建库并授权。以下示例创建数据库 `openjiuwen_market` 和业务账号 `skillhub`；请将密码替换为实际使用的强密码：

```sql
CREATE DATABASE IF NOT EXISTS openjiuwen_market
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE USER IF NOT EXISTS 'skillhub'@'%' IDENTIFIED BY '请替换为强密码';
GRANT ALL PRIVILEGES ON openjiuwen_market.* TO 'skillhub'@'%';
FLUSH PRIVILEGES;
```

已有 MySQL 账号时，可跳过 `CREATE USER`，并将 `GRANT` 中的账号替换为现有账号。完成后，在第 4.3 节的 ConfigMap 和第 4.2 节的 Secret 中填写相同的数据库名、账号和密码。

### 3.2 准备对象存储

确认 MinIO 或 OBS 已部署且 K8s Pod 可通过网络访问，并已创建 marketplace 使用的 Bucket。

- **MinIO**：确认 API 端口（默认 9000）可被 K8s Pod 访问。中间件部署在宿主机时如何填地址，见第 4.3 节表格。
- **华为云 OBS**：记录 Endpoint、AK、SK、区域和桶名称，供第 4 节配置使用。

> marketplace 启动时会通过 `head_bucket` 检查 Bucket。请先确认 Bucket 已创建且凭证具有访问权限，再继续配置。

> **注意**：插件包和图标仅通过预签名 URL 访问，`MARKET_S3_ENDPOINT` 不仅要被 K8s Pod 访问，还必须能被用户浏览器和 CLI 直连。只配集群内部地址会导致页面正常、下载全部失败。

### 3.3 准备 OAuth 应用和审核账号

需要通过 Web 页面登录、发布和审核 Skill 时，按 [OAuth 登录配置](../../6.%20运维指南/基础部署/OAuth登录配置.md) 完成准备（仅浏览公开内容可跳过本节）。完成后记录以下值，供第 4 章配置使用：

- GitCode OAuth 应用的 Client ID 和 Client Secret（本地验证时应用主页和回调地址使用 `http://skillhub.local:9002`，与 ConfigMap 默认值一致）
- 审核账号的 GitCode 登录名（需与发布账号不同，审核账号不能审核自己发布的 Skill）

通过域名对外提供服务时无需配置 hosts，OAuth 应用直接使用实际域名。

## 4 配置 K8s 资源

### 4.1 文件结构

```text
docker/k8s/
├── namespace.yaml             # Namespace + RBAC + ResourceQuota
├── marketplace-config.yaml    # marketplace ConfigMap（非敏感配置）
├── marketplace-deploy.yaml    # marketplace Deployment + Service
├── frontend-deploy.yaml       # frontend Deployment + Service
├── skill-runner-config.yaml   # skill-runner ConfigMap（非敏感配置）
└── skill-runner-deploy.yaml   # skill-runner Deployment + Service
```

非敏感配置放在 ConfigMap，密码和密钥放在 Secret。部署文件通过 `envFrom` 同时引用两者。

### 4.2 创建 Namespace 和 Secret

先创建 Namespace 和 RBAC：

```bash
kubectl apply -f docker/k8s/namespace.yaml
```

验证：`kubectl get ns skillhub-system` 显示 `Active` 即创建成功。

再创建 Secret。请先将下面的 `REPLACE_WITH_*` 替换为真实值。命令为单行写法，bash 和 PowerShell 均可直接执行：

```bash
kubectl -n skillhub-system create secret generic skillhub-secrets --from-literal=DB_PASSWORD='REPLACE_WITH_DB_PASSWORD' --from-literal=MARKET_S3_ACCESS_KEY='REPLACE_WITH_S3_ACCESS_KEY' --from-literal=MARKET_S3_SECRET_KEY='REPLACE_WITH_S3_SECRET_KEY' --from-literal=MARKET_GITCODE_OAUTH_CLIENT_ID='REPLACE_WITH_CLIENT_ID' --from-literal=MARKET_GITCODE_OAUTH_CLIENT_SECRET='REPLACE_WITH_CLIENT_SECRET' --dry-run=client -o yaml | kubectl apply -f -
```

验证：`kubectl -n skillhub-system get secret` 能看到 `skillhub-secrets`。

### 4.3 修改 marketplace ConfigMap

编辑 `docker/k8s/marketplace-config.yaml`，按实际环境修改：

| 变量 | 何时修改 | 说明 |
|------|----------|------|
| `DB_HOST` | **必改** | MySQL 地址（替换占位符）。MySQL 装在本机填 `host.docker.internal`，装在其他机器填其内网 IP。端口/账号/库名默认 `3306` / `skillhub` / `openjiuwen_market`，与第 3.1 节一致即可 |
| `MARKET_S3_ENDPOINT` | **必改** | 对象存储地址（替换占位符）。MinIO 装在本机填 `http://host.docker.internal:9000`，装在其他机器填 `http://<内网IP>:9000`。`MARKET_BUCKET_NAME` 默认 `openjiuwen-market-test`，与已创建的 Bucket 一致即可 |
| `STORAGE_TYPE` / `MARKET_S3_REGION` | 使用 OBS 时 | 默认 `MinIO`；OBS 需将类型改为 `OBS` 并填写区域 |
| `MARKET_REVIEW_ADMIN_USERNAMES` | 需要发布/审核时必填 | 审核账号的 GitCode 登录名，多个用逗号分隔；不配置则第 8.5 节审核流程无法完成 |
| `MARKET_GITCODE_OAUTH_REDIRECT_URI` / `MARKET_OAUTH_FRONTEND_ORIGIN` | 通过域名访问时 | 默认 `skillhub.local:9002`，与第 3.3 节 OAuth 应用保持一致；用 Ingress 域名时改为实际域名 |

> 首次部署时改完直接进行第 7 章即可。已部署后再修改本文件，需重新执行 `kubectl apply -f docker/k8s/marketplace-config.yaml` 和 `kubectl -n skillhub-system rollout restart deployment/skillhub-backend` 才能生效。

## 5 构建镜像

在仓库根目录执行：

```bash
# Backend
docker build -f docker/Dockerfile.skillhub-backend -t skillhub-backend:latest .

# Frontend
docker build -f docker/Dockerfile.skillhub-frontend -t skillhub-frontend:latest .
```

backend 构建若遇 PyPI 超时或 JSON 截断，可指定镜像源：

```bash
docker build -f docker/Dockerfile.skillhub-backend --build-arg INDEX_URL=https://mirrors.aliyun.com/pypi/simple --build-arg TRUSTED_HOST=mirrors.aliyun.com -t skillhub-backend:latest .
```

apt 源不可达（`deb.debian.org` 连接超时、报 `Unable to locate package git`）时，在命令中追加 `--build-arg APT_MIRROR=mirrors.aliyun.com`。

验证：`docker images | grep skillhub` 能看到刚构建的镜像。

## 6 加载镜像到 K8s

第 5 章构建的镜像只在本机 Docker 里，需要让集群能访问到。按你的集群类型选择一种：

**Docker Desktop K8s**：与本机 Docker 共享镜像，无需操作，直接进入第 7 章。

**kind 集群**：把镜像加载进集群节点：

```bash
kind load docker-image skillhub-backend:latest skillhub-frontend:latest
```

集群名不是默认的 `kind` 时（`kind get clusters` 查看），追加 `--name <集群名>`，否则报 `no nodes found for cluster "kind"`。

**远程集群**：把镜像推送到集群可访问的镜像仓库（`docker tag` + `docker push`），然后把 `marketplace-deploy.yaml` 和 `frontend-deploy.yaml` 中的镜像地址改为仓库地址；私有仓库还需在 Pod 规范中配置 `imagePullSecrets`。

## 7 部署服务

按依赖顺序部署。

### 7.1 部署 marketplace

```bash
kubectl apply -f docker/k8s/marketplace-config.yaml
kubectl apply -f docker/k8s/marketplace-deploy.yaml
```

验证：`kubectl -n skillhub-system rollout status deployment/skillhub-backend` 显示 `successfully rolled out`，即 Pod 已通过健康检查。

### 7.2 部署 frontend

```bash
kubectl apply -f docker/k8s/frontend-deploy.yaml
```

frontend 通过 K8s 内部域名 `skillhub-backend.skillhub-system.svc.cluster.local:8100` 访问 marketplace，无需额外配置。

验证：`kubectl -n skillhub-system rollout status deployment/skillhub-frontend` 显示 `successfully rolled out`。

## 8 验证

### 8.1 Pod 状态

```bash
kubectl -n skillhub-system get pods
```

所有 Pod 应为 `Running` 且 `READY` 为 `1/1`。

### 8.2 日志检查

```bash
# Backend 日志
kubectl -n skillhub-system logs deployment/skillhub-backend

# Frontend 日志
kubectl -n skillhub-system logs deployment/skillhub-frontend
```

Backend 日志中应无持续报错。

### 8.3 端口转发

本文使用 port-forward 把服务映射到本机进行验证。只需转发 frontend：frontend 的 nginx 会把 `/api` 代理到 backend，页面和接口都走这一个入口。

```bash
kubectl -n skillhub-system port-forward svc/skillhub-frontend 9002:9002
```

CLI 需要直连 backend 时，另开终端再转发一条：

```bash
kubectl -n skillhub-system port-forward svc/skillhub-backend 8100:8100
```

> 生产环境对外暴露的方式因集群而异（Ingress、LoadBalancer、NodePort 等），按你的集群环境自行配置，并将域名和 OAuth 相关地址同步改为实际值。

### 8.4 验证访问

在 port-forward 保持运行的状态下，另开终端执行：

```bash
curl http://localhost:9002/api/health
```

正常响应说明 frontend → backend 反代链路连通（Pod 本身的健康由 readiness 探针保证，`READY 1/1` 即健康）。然后浏览器访问 `http://skillhub.local:9002`，页面能够正常打开且市场内容可以加载。

![市场首页](../../assets/img/一键部署-市场首页.png)

### 8.5 完整发布流程

前提：OAuth 已配置（Client ID/Secret 已写入 Secret），且 `MARKET_REVIEW_ADMIN_USERNAMES` 已填入审核账号。使用准备的发布账号和审核账号，继续验证完整发布流程：

1. 使用发布账号登录并提交 Skill，确认状态为“人工审核中”。
2. 使用独立审核账号登录，在“待审核”中通过该 Skill。
3. 返回市场页面，确认该 Skill 已可见。

![发布后的市场首页](../../assets/img/一键部署-市场首页-已发布.png)

## 9 可选能力

完成基础部署后，以下能力可按需启用。不启用时核心功能不受影响：

| 能力 | 说明 | 不启用时的表现 |
|------|------|----------------|
| **系统审查** | 发布前自动检测安全风险 | 直接进入人工审核 |
| **检索系统** | 语义搜索，比关键词匹配更准 | 搜索退化为关键词匹配 |
| **分类标签** | 新发布 Skill 自动打分类标签，用于首页类别展示 | 首页无类别，Skill 无分类标签 |
| **在线体验** | 在页面上直接运行 Skill，每个会话一个独立沙箱 Pod | 页面无在线体验入口 |

除在线体验（9.4 节，有独立的组件和配置文件）外，以下配置都写在 `docker/k8s/marketplace-config.yaml` 的 `data` 中，密钥类配置追加到 `skillhub-secrets` Secret。修改后需重新 apply 并重启 backend 生效（同第 4.3 节）。

### 9.1 系统审查

在 `marketplace-config.yaml` 中修改以下三项（其余配置保持默认），模型接口需兼容 OpenAI Chat Completions：

```yaml
MARKET_SKILL_REVIEW_ENABLED: "true"
MARKET_SKILL_REVIEW_MODEL_BASE_URL: "https://your-review-model-service/v1"
MARKET_SKILL_REVIEW_MODEL_NAME: "test-review-model"
```

模型密钥追加到 `skillhub-secrets` Secret：

```bash
kubectl -n skillhub-system patch secret skillhub-secrets --type='json' -p='[{"op": "add", "path": "/stringData", "value": {"MARKET_SKILL_REVIEW_MODEL_API_KEY": "REPLACE_WITH_REVIEW_API_KEY"}}]'
```

- 关闭（默认）：发布后直接进入人工审核
- 开启：先系统审查；通过后转为「待人工审核」，不通过则发布失败
- 开启时若未配齐模型参数，发布会被拒绝
- 覆盖普通 Skill 与 SwarmSkill

验证：提交 Skill 后，状态先显示「系统审查中」即生效。审查完成后可在「系统审查详情」页查看各维度检查结果与 AI 语义复核结论：

![系统审查详情](../../assets/img/一键部署-系统审查详情.png)

### 9.2 检索系统

开启语义搜索需要一个向量模型（Embedding）服务，接口兼容 OpenAI 的 `/v1/embeddings` 即可。marketplace 副本数大于 1 时属于多实例场景，需要 Redis 共享状态（含索引同步；OAuth 会话、限流等多实例能力同样依赖它），Redis 的准备和配置要点见 [Redis 多实例配置](../../6.%20运维指南/可选能力/在线体验/Redis多实例配置.md)。

在 `marketplace-config.yaml` 中修改以下配置（检索策略、定时任务等其余配置保持默认）：

```yaml
# 向量模型（必需）
MARKET_RETRIEVAL_EMBEDDING_API_BASE_URL: "https://your-embedding-service/v1"
MARKET_RETRIEVAL_EMBEDDING_MODEL: "test-embedding-model"

# Redis（多副本必需，单副本可留空）
REDIS_HOST: ""
```

密钥追加到 `skillhub-secrets` Secret，按实际使用的项执行：

```bash
kubectl -n skillhub-system patch secret skillhub-secrets --type='json' -p='[{"op": "add", "path": "/stringData", "value": {"MARKET_RETRIEVAL_EMBEDDING_API_KEY": "REPLACE_WITH_EMBEDDING_API_KEY", "MARKET_REDIS_PASSWORD": "REPLACE_WITH_REDIS_PASSWORD"}}]'
```

Pod 须能访问 Embedding 服务的 Base URL。使用 MinIO 时，索引与附件同桶，无需额外配置。

验证：重启后查看日志，`kubectl -n skillhub-system logs deployment/skillhub-backend` 中出现 `retrieval index rebuild run end` 就表示索引建好了。然后在市场页面用一个**近义词**搜索——例如 Skill 描述写的是「旅行」，改搜「旅游」——能搜到说明语义检索已生效；只有搜原词才命中，说明仍在走关键词兜底，索引未生效。

![语义检索效果](../../assets/img/一键部署-语义检索.png)

### 9.3 Skill 分类标签

新发布的 Skill 由对话模型（LLM）自动分配分类标签，用于首页类别展示。它随检索模块一同启动，无需单独部署，模型接口兼容 OpenAI 即可。

在 `marketplace-config.yaml` 中修改以下两项（定时任务等其余配置保持默认）：

```yaml
# 分类用的对话模型；不配时会自动复用检索主模型配置 MARKET_RETRIEVAL_MODEL_*
MARKET_RETRIEVAL_SKILL_TAG_LLM_MODEL: "test-chat-model"
MARKET_RETRIEVAL_SKILL_TAG_LLM_API_BASE_URL: "https://your-chat-model-service/v1"
```

模型密钥追加到 `skillhub-secrets` Secret：

```bash
kubectl -n skillhub-system patch secret skillhub-secrets --type='json' -p='[{"op": "add", "path": "/stringData", "value": {"MARKET_RETRIEVAL_SKILL_TAG_LLM_API_KEY": "REPLACE_WITH_CHAT_API_KEY"}}]'
```

- 分类是增量进行的：只处理新发布、有变更或还没分类的 Skill，不会重复分类
- 如果没配模型或模型不可用，服务照常运行，只是不执行分类、首页没有类别展示，启动日志里会有明确报错

验证：发布一个 Skill，日志出现 `retrieval skill-tag refresh run end` 后，首页对应类别下就能看到该 Skill。

![分类标签效果](../../assets/img/一键部署-分类标签.png)

### 9.4 在线体验

在线体验（skill-runner）让用户在页面上直接运行 Skill：marketplace 把请求代理到 skill-runner 控制面，控制面为每个会话在 `skillhub-workers` 命名空间创建独立的 worker Pod 执行。需要一个接口兼容 OpenAI 的 LLM 服务。

**1）构建镜像并加载到集群**（远程集群改为推送到镜像仓库，并修改 `skill-runner-config.yaml` 中的 `SKILL_RUNNER_K8S_POD_IMAGE`、`SKILL_RUNNER_K8S_IMAGE_PULL_POLICY` 与 `SKILL_RUNNER_K8S_IMAGE_PULL_SECRETS`）：

```bash
docker build -f docker/Dockerfile.skill-runner -t skill-runner:latest .
docker build -f docker/skill-agent-worker/Dockerfile -t skill-agent-worker:latest .
# kind 集群执行（集群名非默认 kind 时追加 --name <集群名>，同第 6 章）；Docker Desktop K8s 跳过
kind load docker-image skill-runner:latest skill-agent-worker:latest
```

> **skill-runner 镜像构建说明**：镜像内通过 `pip` 安装 uv，且 pip/uv 默认使用阿里云 PyPI 镜像源（`Dockerfile.skill-runner` 中的 `PIP_INDEX_URL`、`UV_INDEX_URL`）。海外或企业内网环境构建前，请把这两个变量改为可达的镜像源。

**2）编辑 `docker/k8s/skill-runner-config.yaml`**，填写 LLM 配置（接口兼容 OpenAI）：

```yaml
SKILL_RUNNER_LLM_API_BASE: "https://your-llm-service/v1"
SKILL_RUNNER_LLM_MODEL_NAME: "your-model-name"
```

**3）创建 LLM 密钥 Secret**（将 `REPLACE_WITH_LLM_API_KEY` 替换为真实值）：

```bash
kubectl -n skillhub-system create secret generic skill-runner-llm --from-literal=SKILL_RUNNER_LLM_API_KEY='REPLACE_WITH_LLM_API_KEY' --dry-run=client -o yaml | kubectl apply -f -
```

**4）开启在线体验开关并部署**：

> **重要**：
> - 先把 `docker/k8s/marketplace-config.yaml` 中的 `PLAYGROUND_ENABLED` 改为 `"true"`，否则 Skill 详情页不会出现「在线体验」入口。
> - 确认第 2 步的 LLM 配置和第 3 步的 Secret 已填入真实值。

```bash
kubectl apply -f docker/k8s/skill-runner-config.yaml
kubectl apply -f docker/k8s/skill-runner-deploy.yaml
kubectl apply -f docker/k8s/marketplace-config.yaml
kubectl -n skillhub-system rollout restart deployment/skillhub-backend
```

验证：打开一个已过审 Skill 的详情页，确认显示「在线体验」入口；进入并发送一条消息，能收到模型回复即链路正常。会话创建后，worker Pod 会出现在 `skillhub-workers` 命名空间，可用 `kubectl -n skillhub-workers get pods` 观察其创建和回收。

![在线体验入口](../../assets/img/在线体验-入口.png)

会话运行结束后，右侧面板展示最终结果（下图为 swarm 类 Skill 的运行效果）：

![在线体验运行结果](../../assets/img/在线体验-运行结果.png)

## 10 卸载

```bash
kubectl delete -f docker/k8s/skill-runner-deploy.yaml
kubectl delete -f docker/k8s/skill-runner-config.yaml
kubectl delete -f docker/k8s/frontend-deploy.yaml
kubectl delete -f docker/k8s/marketplace-deploy.yaml
kubectl delete -f docker/k8s/marketplace-config.yaml
kubectl -n skillhub-system delete secret skillhub-secrets skill-runner-llm --ignore-not-found
kubectl delete -f docker/k8s/namespace.yaml
```

> Secret 删除命令须在删除 namespace 之前执行；`--ignore-not-found` 兼容未部署在线体验、因而不存在 `skill-runner-llm` 的情况。卸载 namespace 也会清理其中残留的 Secret、ConfigMap、Deployment 和 Service。MySQL 数据和 MinIO/OBS 中的数据不受影响。

验证：`kubectl get ns skillhub-system` 返回 `NotFound` 即卸载完成。

## 11 常见问题

**部署问题**

- **Pod 一直处于 `ImagePullBackOff`**：确认镜像已加载到 K8s，kind 需 `kind load docker-image`；远程集群确认镜像地址和 `imagePullPolicy` 正确。
- **Backend Pod 启动失败，`head_bucket` 报错**：确认 Bucket 已创建、AK/SK 正确、`MARKET_S3_ENDPOINT` 在 Pod 内可访问。可用 `kubectl exec` 进入 Pod 测试连通性。
- **Backend Pod 无法连接 MySQL**：确认 `DB_HOST` 在 Pod 内可达（宿主机部署时地址怎么填，见第 4.3 节表格）。
- **Frontend 502 或页面空白**：确认 Backend Pod 已 Running 且 `READY 1/1`。frontend 通过 ClusterIP 访问 marketplace，不受 port-forward 影响。
- **`host.docker.internal` 不通**：编辑 `C:\Windows\System32\drivers\etc\hosts`，确保存在 `127.0.0.1 host.docker.internal`；注释掉 Docker Desktop 写入的非回环地址行；保存后执行 `ipconfig /flushdns`。
- **修改 ConfigMap 或 Secret 后未生效**：改完需重启对应 Deployment，例如 `kubectl -n skillhub-system rollout restart deployment/skillhub-backend`。

**OAuth 问题**

- **登录失败或回调 404**：确认 ConfigMap 中 `MARKET_GITCODE_OAUTH_ENABLED` 为 `"true"`；回调地址与 GitCode 应用设置完全一致；`MARKET_OAUTH_FRONTEND_ORIGIN` 与浏览器实际访问地址一致（含端口）；Client ID 和 Client Secret 已通过 Secret 注入。
- **修改 OAuth 配置后未生效**：同上，重启 marketplace Deployment 后再试。

**port-forward 问题**

- **port-forward 连接即断开**：目标 Pod 可能已重启或端口不匹配。确认 Pod 处于 Running 状态；用 `kubectl get svc -o jsonpath='{.spec.ports}'` 检查 `port` 和 `targetPort` 是否一致，不一致时改用 `kubectl port-forward pod/<pod-name>`。

**在线体验问题**

- **点击「在线体验」报 503 `skill_runner_unavailable`**：确认 skill-runner 已部署且 Pod 为 Running；确认 `PLAYGROUND_ENABLED` 为 `"true"` 且 `SKILL_RUNNER_URL` 指向 `http://skill-runner.skillhub-system.svc.cluster.local:8900`，改 ConfigMap 后需重启 backend。
- **创建会话后 worker Pod 未出现或 `ImagePullBackOff`**：`kubectl -n skillhub-workers get pods` 查看。确认 `skill-agent-worker:latest` 镜像已加载到集群（kind 需 `kind load docker-image`）；远程集群确认 `SKILL_RUNNER_K8S_POD_IMAGE` 地址和拉取凭证。
- **worker Pod 创建失败，报 Pod Security 相关错误**：`skillhub-workers` 命名空间启用了 restricted Pod Security。确认未改动 `skill-runner-config.yaml` 中的 `SKILL_RUNNER_POD_PRIVILEGED`（应为 `"false"`）和安全上下文配置。
- **会话建立但发送消息无回复**：多为 LLM 配置问题。确认 `SKILL_RUNNER_LLM_API_BASE`、`SKILL_RUNNER_LLM_MODEL_NAME` 已填写、`skill-runner-llm` Secret 中的密钥有效，且 skill-runner Pod 能访问该 LLM 服务；查看 `kubectl -n skillhub-system logs deployment/skill-runner` 中的 llm_proxy 报错。
- **无回复且日志报 `UnsupportedProtocol: Request URL is missing an 'http://' or 'https://' protocol`**：skill-runner Pod 读到的仍是占位配置。执行 `kubectl -n skillhub-system exec deployment/skill-runner -- env | grep SKILL_RUNNER_LLM`，若显示 `REPLACE_WITH_*`，说明带占位值部署或改配置后未重启 Pod。确认 `skill-runner-config.yaml` 已填真实值后，执行 `kubectl -n skillhub-system rollout restart deployment/skill-runner` 重启控制面，再 `kubectl -n skillhub-workers delete pods --all` 删除用旧配置创建的 worker Pod（会中断进行中的会话，控制面随后自动重建）。
- **跑到一半 Session failed，报 `pod stream error: peer closed connection without sending complete message body`**：多为单轮运行时长超过 `SKILL_RUNNER_SESSION_MAX_LIFETIME`（默认 3600 秒，写入 worker Pod 的 `activeDeadlineSeconds`，从 Pod 创建起计时），K8s 到点强杀 Pod 导致流中断。长耗时 Skill 需调大 `skill-runner-config.yaml` 中该值，然后重启 skill-runner 并删除 `skillhub-workers` 下的旧 Pod。

**检索问题**

- **日志 `retrieval: failed to create embedding client`**：`MARKET_RETRIEVAL_EMBEDDING_API_BASE_URL` 或 `MARKET_RETRIEVAL_EMBEDDING_API_KEY` 配置有误，或 Pod 内访问不到该服务。
- **日志 `retrieval_search: index not ready for group=skill, fallback`**：索引尚未构建完成，接口已降级为数据库查询，等待构建完成即可。
- **搜索无结果**：确认 Embedding API 在 Pod 内可正常调用，且日志已出现 `retrieval index rebuild run end`。
- **多副本索引不同步**：确认 Redis 已配置且 Pod 可连通；未配 Redis 时索引热加载仅在各副本本进程生效。
- **日志 `skill-tag 分类功能未启用`**：分类模型未配置或不可用，按 9.3 节配置后重启 Deployment。

## 12 更多文档

| 文档 | 说明 |
|------|------|
| [TeamSkillsHub 接口参考](../../7.%20API参考/TeamSkillsHub-接口参考.md) | **推荐** - 端点总览、curl 示例、可见性规则 |
| [OAuth 登录配置](../../6.%20运维指南/基础部署/OAuth登录配置.md) | GitCode / GitHub OAuth 完整配置 |
| [故障排查](../../6.%20运维指南/基础部署/故障排查.md) | 更多部署问题排查 |
| [skill-runner 部署](../../6.%20运维指南/可选能力/在线体验/skill-runner部署.md) | 在线体验（skill-runner）详细配置与 worker 镜像构建 |
| [升级说明](../升级说明.md) | 升级前检查项和变更记录 |
