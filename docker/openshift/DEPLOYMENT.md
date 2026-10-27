# OpenShift 4.16 基础市场部署指导

本指导从构建镜像一直到 OCP Web 控制台安装、验收和回退。所有构建命令在能访问依赖源与镜像仓库的构建机上执行；安装人员可以只使用 OCP Web 控制台，不需要 `oc`。当前完整安装集合为 Marketplace（Backend）和 Frontend，可验证浏览、关键词检索、GitCode 登录、发布及人工审核。Runner/Worker 的镜像配方已提供，但尚无其 OCP 集群清单，本指导不将在线试用列为验收目标。

## 1. 准备环境与填写值

| 项目 | 部署前确定的值或条件 |
| --- | --- |
| 源码 | 在 SkillHub 仓库根目录构建，记录同一 Git 提交号；两张镜像来自同一提交 |
| 镜像仓库 | 构建机可推送，OCP 节点可拉取；确定仓库、项目、版本标签及私有仓库拉取凭据 |
| OCP | 应用 Project、可用的 `restricted-v2` SCC、创建 Secret/ConfigMap/Deployment/Service/Route 的权限 |
| 数据库 | 可由 Backend Pod 访问的 MySQL 兼容数据库、库名、账号、密码及建表/读写权限 |
| 对象存储 | 已创建的 Bucket、Backend Pod 与用户浏览器可访问的 S3 端点、区域及静态 AK/SK |
| GitCode | 发布者和不同的审核员账号；可配置回调地址的 GitCode OAuth 应用 |

本清单不安装数据库或对象存储。若测试库是 MariaDB 11.8 且启用了 `ORACLE` 等特殊 `sql_mode`，须在独立测试库验证建表、发布与审核；本交付物没有修改 SQL，不能将镜像构建成功视为数据库兼容性验收。

### MariaDB 数据库准备（先于 Backend Deployment）

以下步骤针对**全新专用测试库**。若准备复用已有 SkillHub 数据库，先备份并对照本节末尾的已有库说明，不要执行新库建库语句或直接启动新版 Backend。

1. 与数据库管理员确认 MariaDB 地址、端口、允许 OCP Backend Pod 访问的网络路径，以及本次专用库名。下面以 `openjiuwen_market` 为例；若该名称已被使用，换一个未使用的名称，并同步修改 ConfigMap 的 `STORE_DB_NAME`。在 MariaDB SQL 客户端或管理界面中先查询 `SELECT VERSION(), @@GLOBAL.sql_mode, @@SESSION.sql_mode;`，记录实际版本和模式。
2. 用有建库、建账号和授权权限的管理员连接 MariaDB。在有 `mariadb` 客户端的机器上可执行下面命令，密码由提示交互输入；若只提供数据库管理界面，在其 SQL 编辑器执行后续 SQL 即可：

   ```sh
   mariadb -h DB_HOST -P 3306 -u ADMIN_USER -p
   ```

3. 管理员执行以下 SQL。执行前替换密码；`'skillhub'@'%'` 中的 `%` 只是试部署示例，现场可按数据库所见的 OCP 出口来源收紧。`CREATE DATABASE` 若报库已存在，应停下核对，不要把它当成全新空库。

   ```sql
   CREATE DATABASE openjiuwen_market CHARACTER SET utf8mb4;
   CREATE USER IF NOT EXISTS 'skillhub'@'%' IDENTIFIED BY 'REPLACE_WITH_STRONG_PASSWORD';
   GRANT ALL PRIVILEGES ON openjiuwen_market.* TO 'skillhub'@'%';
   ```

   逐条确认 SQL 执行成功后，管理员立即检查建库和授权结果：

   ```sql
   SELECT SCHEMA_NAME, DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME
     FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = 'openjiuwen_market';
   SHOW GRANTS FOR 'skillhub'@'%';
   SELECT COUNT(*) AS table_count
     FROM information_schema.TABLES WHERE TABLE_SCHEMA = 'openjiuwen_market';
   ```

   预期查询到一个 `utf8mb4` 数据库、`openjiuwen_market.*` 上的授权，且新库 `table_count=0`。若 `CREATE USER IF NOT EXISTS` 返回账号已存在的警告，还须核对现有账号密码。使用 `CREATE USER` 和 `GRANT` 会立即更新权限，**不需要**执行 `FLUSH PRIVILEGES`；直接修改 `mysql` 权限表或以 `--skip-grant-tables` 启动服务器等特殊操作才涉及重新加载权限。

4. 退出管理员连接，以业务账号重新连接，并执行以下只读检查。尽量从与 OCP Backend 相同的网络路径测试；若只能在管理机测试，仍须在 Pod 启动时验证实际连通性。新库的 `SHOW TABLES` 应为空；确认 `CURRENT_USER()` 对应获授权的账号、字符集是 `utf8mb4`、`@@SESSION.sql_mode` 是本次要验收的模式。`CREATE USER IF NOT EXISTS` 不会重置已有账号的密码；若账号已存在且登录失败，请管理员核对账号与密码，不要直接覆盖其他业务账号。

   ```sh
   mariadb -h DB_HOST -P 3306 -u skillhub -p openjiuwen_market
   ```

   ```sql
   SELECT CURRENT_USER(), DATABASE(), VERSION(), @@SESSION.sql_mode;
   SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME
     FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = 'openjiuwen_market';
   SHOW GRANTS;
   SHOW TABLES;
   ```

5. 在 OCP 的目标 Project 中填写 `marketplace-configmap.yaml`：`DB_TYPE=mysql`、实际 `DB_HOST`/`DB_PORT`、`DB_USER=skillhub`、与建库一致的 `STORE_DB_NAME`。在同一 Project 的业务 Secret 中填写 `DB_PASSWORD`。不要同时启用含不同连接信息的 `STORE_DB_URL`；它会优先于上述分项配置。数据库密码不要提交到 Git，也不要写进 ConfigMap。
6. 按第 3、4 节先创建业务 Secret 和 ConfigMap，再创建 Marketplace Deployment/Service。Backend 启动时使用 `mysql+pymysql` 和 `utf8mb4` 连接并创建缺失的表。查看 Pod Events、Logs 和 `/api/health`；若建表失败，先看第一条数据库异常，核对账号权限、网络、库名及实际 SQL 模式。启动成功后用业务账号再次执行 `SHOW TABLES;`，至少核对 `market_assets`、`market_asset_versions`、`market_skill_reviews`、`audit_logs` 等核心表，再按第 5 节验证发布与人工审核。

当前 Backend 启动时调用 SQLAlchemy `Base.metadata.create_all()` 创建缺失的表，因此对**全新空库**无需预先逐表执行 `sql/baseline` 和 `sql/incremental`。`create_all()` 不会升级已有表；如果使用已有业务库，先备份并按该库当前结构审查所需增量迁移，不要直接对旧库启动新版 Backend 或机械地重复执行全部脚本。MariaDB 11.8 的 `ORACLE`/`ANSI_QUOTES` 等模式尚未在目标集群实测；应先在同模式的独立测试库完成启动建表、发布和审核验证，再将结果用于正式部署判断。

## 2. 构建、检查并推送镜像

在仓库根目录执行以下命令；先把示例中的 `REGISTRY/PROJECT` 和 `VERSION` 换成实际值，不要使用 `latest` 作为本次发布标签。

这里的 `-t` 给构建产物命名并设置版本标签，格式为 `镜像仓库地址/仓库内项目或命名空间/镜像名:版本标签`。例如镜像仓库地址为 `harbor.company.example`、仓库内项目为 `skillhub`、本次版本为 `1.0.0` 时，Backend 镜像名是 `harbor.company.example/skillhub/skillhub-backend:1.0.0-ocp`。这里的 `PROJECT` 指**镜像仓库中的路径**，不要求与 OCP Project 名称相同；`harbor.company.example` 只是示例域名，不能原样用于推送。

```sh
docker build -f docker/openshift/dockerfiles/Dockerfile.backend \
  --target runtime -t REGISTRY/PROJECT/skillhub-backend:VERSION-ocp .
docker build -f docker/openshift/dockerfiles/Dockerfile.frontend \
  -t REGISTRY/PROJECT/skillhub-frontend:VERSION-ocp .

docker run --rm --entrypoint python3.11 REGISTRY/PROJECT/skillhub-backend:VERSION-ocp --version
docker run --rm --entrypoint dnf REGISTRY/PROJECT/skillhub-backend:VERSION-ocp --version
docker run --rm --entrypoint nginx REGISTRY/PROJECT/skillhub-frontend:VERSION-ocp -v

docker push REGISTRY/PROJECT/skillhub-backend:VERSION-ocp
docker push REGISTRY/PROJECT/skillhub-frontend:VERSION-ocp

docker buildx imagetools inspect REGISTRY/PROJECT/skillhub-backend:VERSION-ocp
docker buildx imagetools inspect REGISTRY/PROJECT/skillhub-frontend:VERSION-ocp
```

从推送结果或最后两条命令的 `Digest` 记录最终镜像引用，例如 `REGISTRY/PROJECT/skillhub-backend@sha256:<实际摘要>`。OCP Deployment 中填入对应的**最终镜像摘要**，不要填基础镜像摘要。建议同时记录构建源码提交、架构、两张镜像的标签和摘要，以及上述版本检查结果。

四个 OpenShift Dockerfile 的基础镜像均使用 `版本标签@多架构摘要`；本阶段只构建 Backend/Frontend。三个 Python 镜像保留完整 `dnf`，供后续在派生镜像**构建阶段**安装 RPM，不在运行中的 Pod 内安装。企业镜像源可传 `--build-arg UBI_IMAGE=<带摘要的 UBI 9 standard 镜像>`，Python 包源可传 `--build-arg INDEX_URL=<包源地址>`；构建机还需能访问 npm 包源。基础镜像摘要固定不等于整个构建可复现：RPM 仓库和部分 Python 依赖仍会变化，部署时应使用本次已推送的最终摘要。

默认前端按根路径 `/` 构建。只有决定使用 `/hub` 前缀时，才在前端 `docker build` 命令中同时加入 `--build-arg FRONTEND_BASE_PATH=hub --build-arg VITE_API_BASE_URL=/hub/api/v1`，并验证对应 Route/回调；首次试部署建议使用默认根路径。`BACKEND_URL`、`BACKEND_PORT` 是容器启动时填入 Nginx 模板的变量，修改它们只需更新 Frontend Deployment 并创建新 Pod，无须重建前端静态资源。

## 3. 在 OCP 页面创建项目与凭据

1. 创建或选择应用 Project。确认镜像仓库可从集群拉取，并由管理员核对该 Project 实际允许使用的 SCC。下面七份清单都属于同一个应用 Project；清单不写死 `metadata.namespace`。
2. 私有仓库需要在该 Project 中创建镜像拉取 Secret，并在默认 ServiceAccount 中关联，或在两份 Deployment 的 Pod `spec.imagePullSecrets` 中填入该 Secret 名称。公开且集群可直接拉取的镜像不需要这一步。创建后在 Pod 事件中检查是否出现拉取失败。
3. 业务凭据使用名为 `skillhub-backend-secrets` 的 Opaque Secret。可以在 **Workloads → Secrets** 中逐个填写下表的键，也可以将 `manifests/marketplace-secret.yaml` 的**生效行**占位值替换为现场值后通过 **Import YAML** 创建；两种方式只选一种。Secret 清单中还按功能列出可选敏感键，注释行不会导入；启用对应功能时再取消注释并填真实值。`stringData` 便于人工填写，导入后 Kubernetes 会将其保存为 Secret 数据。不要把填入真实凭据的 YAML、截图或终端输出提交到仓库。

| Secret 键 | 何时填写 | 内容 |
| --- | --- | --- |
| `DB_PASSWORD` | Backend 启动前必填 | 数据库密码 |
| `MARKET_S3_ACCESS_KEY` | 静态凭据模式必填 | MinIO/OBS 静态 Access Key |
| `MARKET_S3_SECRET_KEY` | 静态凭据模式必填 | MinIO/OBS 静态 Secret Key |
| `MARKET_GITCODE_OAUTH_CLIENT_ID` | 启用 GitCode 登录前必填 | GitCode OAuth 应用 Client ID |
| `MARKET_GITCODE_OAUTH_CLIENT_SECRET` | 启用 GitCode 登录前必填 | GitCode OAuth 应用 Client Secret |

上表是**当前基础市场试部署中生效的键**；Secret 清单还以注释列出其他功能的敏感键，ConfigMap 清单以注释列出相应的非敏感配置。Backend Deployment 通过 `envFrom.secretRef` 将 Secret 中生效的键作为环境变量注入。常见扩展及其启用条件如下：

| 启用的功能 | 可增加的敏感键 | 其他配置位置 |
| --- | --- | --- |
| Redis 服务启用密码认证 | `MARKET_REDIS_PASSWORD` | Redis 地址、端口等放 ConfigMap；多 Backend 副本需要共享状态，本轮单副本不用 Redis |
| OBS 动态 IAM 凭据 | `HUAWEICLOUD_USERNAME`、`HUAWEICLOUD_PASSWORD` | ConfigMap 设 `MARKET_CREDENTIALS_MODE=dynamic`，并指定 `HUAWEICLOUD_PROJECT_ID` 或 `HUAWEICLOUD_PROJECT_NAME` 等非敏感项 |
| GitHub 或 AgentOS OAuth | `MARKET_GITHUB_OAUTH_CLIENT_ID`、`MARKET_GITHUB_OAUTH_CLIENT_SECRET`；或 `MARKET_AGENTOS_OAUTH_CLIENT_ID`、`MARKET_AGENTOS_OAUTH_CLIENT_SECRET` | 各自的启用开关和回调地址放 ConfigMap |
| 系统管理员令牌 | `SYSTEM_ADMIN_TOKEN` | 仅使用 `X-System-Token` 管理接口时需要 |
| 检索、推荐或 AI 审查 | `MARKET_RETRIEVAL_EMBEDDING_API_KEY`、`MARKET_RETRIEVAL_MODEL_API_KEY`、`MARKET_REC_EMBEDDING_API_KEY`、`MARKET_SKILL_REVIEW_MODEL_API_KEY` 等按启用能力填写 | 模型地址、名称及开关放 ConfigMap；本轮 AI 审查关闭 |
| 启用加密或推荐服务认证 | `SERVER_AES_MASTER_KEY`、`MARKET_MILVUS_PASSWORD` 等按实际能力填写 | 加密模式、Milvus 地址等非敏感项放 ConfigMap |

更多环境变量背景见仓库根目录 [`.env.example`](../../.env.example)。OpenShift 清单按本次代码基线的正式部署变量组织，旧别名和仅供其他部署方式使用的构建变量未逐一重复；后续业务代码新增变量时仍需同步更新模板。启用新能力时，应同时核对应用代码、相应外部服务和模板中的 ConfigMap/Secret 配置。

只浏览、暂不启用 GitCode 登录时，可在导入 Secret 清单前删除 OAuth 两行；准备启用时再在页面添加。若使用逐键创建方式，不必导入 Secret 清单。镜像仓库拉取 Secret 与此业务 Secret 分开创建，前者由 Pod 拉取镜像使用，后者通过 Backend Deployment 的 `envFrom.secretRef` 注入应用。若采用 OBS 动态 IAM 凭据，应将 ConfigMap 的 `MARKET_CREDENTIALS_MODE` 改为 `dynamic`，删除静态 AK/SK 两行，并按应用要求另行配置 `HUAWEICLOUD_*` 凭据及 IAM 网络链路；当前模板针对静态 AK/SK。后端启动时创建数据库表并检查 Bucket 存在及访问权限。

## 4. 导入清单

每次使用 **Import YAML** 前确认页面选中的 Project，并替换清单**生效行**中的全部 `REPLACE_WITH_...` 值。可选注释行未启用时无需替换；启用时先取消注释并填有效值，避免把空值或样例占位符导入应用。

| 顺序 | 文件 | 导入前核对 |
| --- | --- | --- |
| 1 | `manifests/marketplace-secret.yaml` | 按第 3 节填写业务凭据；若已在 Secrets 页面逐键创建，则跳过此文件 |
| 2 | `manifests/marketplace-configmap.yaml` | 数据库主机/端口/账号/库名，Bucket、S3 端点、存储类型、区域、SSL、审核员 GitCode `login`；先保持 GitCode OAuth 关闭 |
| 3 | `manifests/marketplace-deployment.yaml` | `REPLACE_WITH_BACKEND_IMAGE` 改为第 2 节记录的 Backend 最终镜像引用；私有仓库按第 3 节关联拉取 Secret |
| 4 | `manifests/marketplace-service.yaml` | Backend Service 名称及 8100 端口 |
| 5 | `manifests/frontend-deployment.yaml` | `REPLACE_WITH_FRONTEND_IMAGE` 改为 Frontend 最终镜像引用；`BACKEND_URL=skillhub-backend`、`BACKEND_PORT=8100` 与 Service 对齐 |
| 6 | `manifests/frontend-service.yaml` | 前端 Service 的 9002 端口 |
| 7 | `manifests/frontend-route.yaml` | Route 指向 `skillhub-frontend` Service，HTTPS 边缘终止；可由集群分配主机名或按现场域名/证书策略修改 |

MinIO 示例使用 `STORAGE_TYPE=MinIO`、`MARKET_CREDENTIALS_MODE=static`、`MARKET_S3_REGION=us-east-1`；若现场 Bucket 的区域不同，应按实际值修改。OBS 静态凭据应使用 `STORAGE_TYPE=OBS`、`MARKET_CREDENTIALS_MODE=static`，并填写对应端点、区域和 SSL 选项。用于生成预签名下载链接的 S3 地址还须从用户浏览器可达；只有 Pod 能解析的集群内部地址不足以完成下载验收。

在 **Workloads → Deployments/Pods** 查看两个 Pod 的状态、Events 和 Logs。Backend `/api/health` 与 Frontend `/health` 探针应成功；Route 的 `https://HOST/api/health` 应代理到 Backend。若 Backend 未就绪，依次核对镜像拉取、数据库与 Bucket 连通性、Secret、建表权限及 Pod Logs；若 Frontend 未就绪，检查 Nginx 错误日志与 `/tmp` 写权限。

## 5. 启用登录、发布与审核

1. 在 **Networking → Routes** 取得前端 HTTPS 地址 `https://HOST`。在 GitCode OAuth 应用中注册回调地址 `https://HOST/api/v1/auth/oauth/gitcode/callback`，必须与应用中配置的值完全一致。
2. 确认 `skillhub-backend-secrets` 中的 `MARKET_GITCODE_OAUTH_CLIENT_ID` 和 `MARKET_GITCODE_OAUTH_CLIENT_SECRET` 已填写真实值；如果之前为只浏览场景省略了这两项，此时在 Secrets 页面添加。
3. 编辑 `skillhub-backend-config`，设 `MARKET_GITCODE_OAUTH_ENABLED=true`、`MARKET_GITCODE_OAUTH_REDIRECT_URI=https://HOST/api/v1/auth/oauth/gitcode/callback`、`MARKET_OAUTH_FRONTEND_ORIGIN=https://HOST`。若前端是 `/hub` 构建，最后一项使用 `https://HOST/hub`。
4. 在 Backend Deployment 页面执行 **Restart rollout**，等待新 Pod 就绪。通过 `envFrom` 注入的 ConfigMap/Secret 环境变量只有新 Pod 读取。
5. 用发布者登录并上传测试资产；用不同的审核员登录，在审核页面批准；退出登录后确认公开列表、详情、关键词检索与下载链接可用，必要时再测试拒绝。

`MARKET_SKILL_REVIEW_ENABLED=false` 关闭 AI 审查，受审核资产仍进入人工审核。审核员 `login` 由 `MARKET_REVIEW_ADMIN_USERNAMES` 精确匹配，可用逗号分隔多个账号。关键词检索不要求 Embedding 服务；本次不部署 Runner/Worker，不验收在线试用。

| 验收动作 | 预期结果 |
| --- | --- |
| 打开 Route 首页、分类与资产详情 | 页面和公开数据正常加载，浏览器无 `/api/` 请求失败 |
| 用关键词检索公开资产 | 返回匹配结果，无需配置 Embedding |
| 发布者 GitCode 登录并上传测试资产 | 登录回跳到同一 Route，受审核资产显示待审核 |
| 审核员 GitCode 登录并批准 | 审核员能看到待审核项，批准后资产进入公开列表 |
| 退出登录后检索、查看并下载该资产 | 列表、详情和下载链接可用 |

## 6. 日志、限制与回退

OCP Pod **Logs** 页面可查看现有 Backend 应用/框架/访问控制台输出和前端 Nginx 标准输出/错误；能否进入集中检索还取决于集群日志采集组件，须在其查询页面单独验证。Marketplace 的 `/app/logs`、`/app/data` 与原 K8s 部署一样写入容器可写层，`interface.log` 等文件不会自动进入容器日志采集，容器重新创建后丢失。

这套清单只运行一个 Backend 副本，OAuth 临时状态存于进程内存；增加副本前需配置共享状态并验证会话。清单省略固定 `runAsUser` 和 `fsGroup`，由 SCC 决定实际 UID。若 Pod 被拒绝或 `/app/logs`、`/app/data` 不可写，先核对 Pod Events、实际 UID 与镜像目录权限，再处理问题。实际 OCP 的 SCC、外部网络、MariaDB SQL 模式、GitCode OAuth 和完整业务流程仍需在目标集群验收。参见 [OCP 4.16 SCC 文档](https://docs.redhat.com/en/documentation/openshift_container_platform/4.16/html/authentication_and_authorization/managing-pod-security-policies)与[日志文档](https://docs.redhat.com/en/documentation/openshift_container_platform/4.16/html/logging/visualizing-logs)。

回退时，在两个 Deployment 页面恢复上一次已验证的**最终镜像摘要**并创建新 Pod；只撤销本次试部署时，可删除 Route、Frontend/Marketplace 工作负载及其 Service/ConfigMap。数据库、Bucket 和 Secret 是否保留由现场负责人决定。
