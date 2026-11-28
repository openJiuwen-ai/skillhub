# Keycloak HTTPS 证书配置与挂载

本文适用于已经部署 Skillhub 和 Keycloak 的服务器环境，介绍 `ssl_verify: true` 下的证书信任、插件配置和文件挂载。通用配置适用于直接运行、Docker Compose、Kubernetes 和 OpenShift；平台相关操作在对应小节单独说明。本文不安装 Keycloak、数据库或对象存储，也不修改 GitCode/GitHub/AgentOS 的认证实现。

仓库 Kubernetes/OpenShift 基础清单的第三方登录总开关保持关闭；本文配置和公共可选渠道示例也为 `enabled: false`。只有部署者完成以下配置并显式打开两个开关后才启用该渠道。`ssl_verify` 只控制插件后端 HTTP 请求的证书校验，默认 `true`；它不控制浏览器证书信任、ID Token 验签或业务权限。

## 1. 确认地址、资源与版本

记录以下实际值；Kubernetes 使用后端所在命名空间，OpenShift 使用对应 Project。域名、Realm、用户名都是示例，不能原样用于生产配置。

| 项目 | 本文示例 |
| --- | --- |
| Skillhub 前端入口 | `https://hub.example.com` |
| Keycloak 对外地址 | `https://sso.example.com` |
| Keycloak Realm / Issuer | `company` / `https://sso.example.com/realms/company` |
| OIDC Client ID | `skillhub` |
| Skillhub 渠道 ID | `keycloak` |
| Skillhub 回调地址 | `https://hub.example.com/api/v1/auth/oauth/keycloak/callback` |
| 后端 Deployment / 容器（OpenShift 示例） | `skillhub-backend` / `backend`；Kubernetes 模板容器为 `marketplace` |
| 发布者 / 审核员 | `publisher01` / `reviewer01` |

`keycloak` 是本文及[登录与发布审核用户指南](<../4. 用户指南/Keycloak 登录与 Skill 发布审核.md>)统一使用的 YAML 渠道 ID；`adapter: keycloak` 选择适配器包。本例两个值相同，但用途不同，框架仍允许为同一适配器配置其他渠道 ID。已有部署可保留自己的渠道 ID，无需因示例统一而更名；如主动更名，须同步修改两边回调地址和检查命令。

Kubernetes/OpenShift 部署先在 Deployment 的 YAML 中找到实际后端容器，读取其 `envFrom`，确认实际引用的资源。下表默认名称来自 OpenShift 模板；Kubernetes 模板或已有部署可能不同，不要直接用模板名称覆盖已有部署：

| 资源 | 仓库默认名称 | 已有部署可能使用的名称 |
| --- | --- | --- |
| 后端普通 ConfigMap | `skillhub-backend-config` | `marketplace-env` |
| 后端业务 Secret | `skillhub-backend-secrets` | `skillhub-secrets` |
| 插件 ConfigMap | `skillhub-identity-extensions` | 按实际命名，和卷引用一致 |
| CA ConfigMap | `skillhub-ca-bundle` | 按实际命名，和卷引用一致 |

确认前后端镜像包含身份扩展功能，后端镜像包含 `ssl_verify` 字段。旧镜像可能因不认识该字段而拒绝当前渠道；使用本次构建的唯一版本标签或最终摘要，不能只重启仍指向旧镜像的 Pod。配置挂载本身不需要重新构建镜像。

后端多副本或多进程运行时，先配置共享 Redis 地址、ACL 用户、密码及一致的 `REDIS_KEY_PREFIX`。确认后端日志为 OAuth Redis PING 成功；Redis 初始化失败可能回退进程内存，Pod 探针通过不能证明临时登录状态已共享。数据库、Bucket 和现有业务配置按[安装指导](README.md)准备；OpenShift 另见[基础部署指导](../../../docker/openshift/DEPLOYMENT.md)。已有数据库先按[升级说明](升级说明.md)核对迁移，启动建表不会自动补齐已有表的字段。

## 2. 确认 Keycloak 可用并配置客户端

1. 确认 Keycloak 服务已运行并就绪。Kubernetes/OpenShift 检查 Pod；Docker 检查容器和应用状态。数据库就绪不代表 Keycloak 已启动。OpenShift 可在 **Workloads → Deployments/Pods** 中确认至少有一个已就绪的 Keycloak Pod。
2. 确认 Keycloak 对外地址和反向代理的路径、端口、TLS 终止方式正确。Kubernetes 检查 Ingress/Gateway、Service 及后端；OpenShift 可在 **Networking → Routes** 中检查 Route 指向的 Service 和就绪后端。
3. 在浏览器打开 `https://sso.example.com/realms/company/.well-known/openid-configuration`。必须返回 OIDC JSON，而不是登录页面或 OpenShift 的 `Application is not available`。复制 JSON 的 `issuer` 到插件配置；保留实际端口和部署前缀。Discovery 中应有 `authorization_endpoint`、`token_endpoint`、`userinfo_endpoint` 和 `jwks_uri`。
4. 在该 Realm 的 **Clients** 创建或打开 `skillhub` 客户端：使用 OpenID Connect，开启 Client authentication 和 Standard flow，PKCE 要求 `S256`；在 Advanced 的 ID Token Signature Algorithm 显式选择 `RS256`，或核对继承的 Realm 默认值为 `RS256`。
5. 在 **Valid redirect URIs** 填写完整 Skillhub 回调地址，和插件 `redirect_uri` 完全一致。地址来自 Skillhub 的浏览器访问入口加 `/api/v1/auth/oauth/keycloak/callback`，不是 Keycloak 的域名。客户端 ID 与 Skillhub 配置一致，Client Secret 从客户端 Credentials 获取。
6. 确认客户端关联 `profile` 和 `email` Scope；Default 会自动应用，Optional 需要被请求。用户名映射输出 `preferred_username`，并开启 **Add to userinfo**。可在 **Clients → skillhub → Client scopes → Evaluate** 选择用户，查看生成的 UserInfo，确认 `sub` 和预期的 `preferred_username`。
7. 在该 Realm 准备两个启用的业务用户并分别设置密码。按 Realm 的用户资料必填规则填写信息，完成临时密码修改、邮箱验证等要求。用户可从 `https://sso.example.com/realms/company/account/` 测试；不要用业务用户登录 `master` 管理控制台。审核员不需要 Keycloak 管理员角色。

详细字段见[用户指南第 2 节](<../4. 用户指南/Keycloak 登录与 Skill 发布审核.md>)及 [Keycloak OIDC 端点说明](https://www.keycloak.org/securing-apps/oidc-layers)。

## 3. 选择证书信任方式

| Keycloak 证书情况 | `ssl_verify` | 自定义证书包及 `SSL_CERT_FILE` |
| --- | --- | --- |
| 后端已经信任的公共 CA，域名、有效期及证书链正常 | `true` | 无需配置；保留 HTTPX 默认公共 CA 信任 |
| 后端尚未信任的企业内部 CA，例如 `Beta2 Infrastructure Root CA` | `true` | 合并公共 CA 和企业根 CA，提供可读证书文件；Kubernetes/OpenShift 可使用 CA ConfigMap 挂载 |

浏览器可以访问不代表容器内 Python 已信任相同的 CA；浏览器信任需要由用户终端自己的证书策略配置。`ssl_verify: true` 不意味着所有部署都必须挂载额外证书。

### 3.1 获取企业根 CA

优先使用证书维护人员提供并确认的 PEM 根 CA。若 Keycloak 通过 Route 配置内部 CA，在 OpenShift 中可打开 **Networking → Routes → Keycloak Route → 详情 → TLS 设置 → CA 证书**，复制完整内容，包括 `BEGIN CERTIFICATE` 和 `END CERTIFICATE`。

区分三个字段：**证书**通常是服务器域名证书；**键**是私钥，本流程不需要；**CA 证书**才是待核对的签发链。若其中仅有中间 CA，仍需证书维护人员提供正确根 CA，服务器端也要提供必要的中间证书链。不能仅根据相同的 CA 名称认定证书相同。

### 3.2 合并公共 CA 和企业根 CA

使用客户统一维护的可信 CA 包，或在浏览器下载 curl 项目维护的 [Mozilla 公共 CA 包 cacert.pem](https://curl.se/ca/cacert.pem)，[来源说明](https://curl.se/docs/caextract.html)。文件下载到操作者电脑，以浏览器下载记录为准；不会自动出现在容器中。有严格信任基线要求时，由运维保留当前镜像实际使用的公共 CA 集合再追加企业 CA。

1. 用文本编辑器打开公共证书包，另存为 `ca-bundle.pem`。
2. 保留原内容，在文件末尾另起一行，追加完整的企业根 CA PEM 块；需要的多张 CA 分别按完整块追加。
3. 确保前一个 `END CERTIFICATE` 与后一个 `BEGIN CERTIFICATE` 分行，使用普通 UTF-8 文本，不包含说明占位符或私钥。
4. 不把客户专用证书包提交到源码仓库。仓库只提供结构模板，不捆绑 Beta2 或其他客户 CA。

HTTPX 默认使用公共 CA，并支持通过 `SSL_CERT_FILE` 指定自定义证书包。该环境变量影响后端进程中采用默认环境设置的 HTTPX 客户端，不只影响 Keycloak，因此要保留所需公共 CA，不能把它当成渠道级的关闭校验开关。[HTTPX 证书说明](https://www.python-httpx.org/advanced/ssl/)

## 4. 准备插件配置、证书和密钥

仓库只维护一份[原始插件配置](../../../config/identity-extensions/identity-extensions-configmap.example.yaml)。复制到部署配置目录并命名为 `identity-extensions.yaml`，替换示例地址、客户端 ID 和回调地址；已有渠道时合并到现有 `providers`，不要覆盖其他渠道。渠道 ID 统一示例为 `keycloak`，默认 `enabled: false`，`settings.ssl_verify: true`。准备正式启用时，再按第 5 节显式开启渠道和总开关。Client Secret 通过运行环境的密钥管理机制注入，不写入此文件。

虽然模板文件名含 `configmap`，其内容是顶层为 `providers` 的原始插件 YAML，不含 `apiVersion`、`kind` 或 `data`，可以直接供 Skillhub 读取；不能作为 Kubernetes 资源直接执行 `apply -f` 或通过 Import YAML 导入。Kubernetes/OpenShift 从这份文件生成 ConfigMap，不再维护第二份嵌套配置模板。


直接运行时，将配置文件和可选 CA 包放在后端进程可读的受控目录；Docker Compose 使用只读文件挂载；Kubernetes/OpenShift 使用 ConfigMap 卷。下面的 ConfigMap 操作适用于 Kubernetes/OpenShift，所有资源须在同一命名空间/Project。创建可选资源不会自动启用登录，不要递归批量导入尚未填写的示例。

### 4.1 创建身份配置 ConfigMap

Kubernetes 和 OpenShift 使用同一份原始配置作为输入。以下命令从仓库根目录执行，先按实际部署填写输入文件；生产部署可使用其受控副本，并替换 `--from-file` 右侧的路径。示例命名空间 `skillhub` 替换为后端实际命名空间/Project。准备阶段保持渠道和总开关关闭。

```sh
kubectl -n skillhub create configmap skillhub-identity-extensions \
  --from-file=identity-extensions.yaml=./config/identity-extensions/identity-extensions-configmap.example.yaml \
  --dry-run=client -o yaml | kubectl -n skillhub apply -f -
```

OpenShift 也可使用 `oc`，输入文件、数据键及挂载路径保持相同：

```sh
oc -n skillhub create configmap skillhub-identity-extensions \
  --from-file=identity-extensions.yaml=./config/identity-extensions/identity-extensions-configmap.example.yaml \
  --dry-run=client -o yaml | oc -n skillhub apply -f -
```

`--from-file` 左侧的 `identity-extensions.yaml` 是 ConfigMap 的数据键，右侧是输入文件路径。必须显式指定这个数据键，不能让模板文件名变成数据键，否则现有 `subPath: identity-extensions.yaml` 找不到文件。`--dry-run=client` 只生成资源，管道右侧的 `apply` 才创建或更新 ConfigMap。

只使用 OpenShift 控制台时，进入 **Workloads → ConfigMaps → Create ConfigMap → Form view**：Name 填 `skillhub-identity-extensions`；Key 填 `identity-extensions.yaml`；Value 粘贴填写后的原始插件 YAML 全文，或通过文件上传填入该值。不要把原始文件导入 YAML 资源视图。已有同名 ConfigMap 时更新该数据键，不需要另建一份源码模板。

创建后确认 `data.identity-extensions.yaml` 的正文以 `providers` 为顶层。正式启用时将部署配置中的 `providers.keycloak.enabled` 改为 `true`，再次生成/更新 ConfigMap，并按第 6 节重启全部后端副本；`settings.ssl_verify` 保持 YAML 布尔值 `true`，不要加引号。

### 4.2 创建 CA ConfigMap（仅内部 CA 场景）

通过集群部署工具创建以下 ConfigMap；OpenShift 可进入 **ConfigMaps → Create ConfigMap → Form view**：

| 表单字段 | 值 |
| --- | --- |
| Name | `skillhub-ca-bundle` |
| Key | `ca-bundle.pem` |
| Value | 合并后的 `ca-bundle.pem` 全部正文；可以浏览/拖入文件或粘贴文本 |

创建后在详情确认数据键和 PEM 内容。值不能是操作者电脑的文件路径。也可填写[证书包结构示例](../../../config/identity-extensions/ca-bundle-configmap.example.yaml)，但必须替换整个占位值；它不是有效证书，不能原样部署。YAML `data.ca-bundle.pem: |` 下每行需正确缩进，使用表单上传可避免缩进错误。CA 是公开证书，可以使用 ConfigMap，私钥和 Client Secret 不放在这里。

### 4.3 追加 Client Secret

Kubernetes/OpenShift 打开后端实际引用的业务 Secret，追加 `SKILLHUB_ENTERPRISE_SECRET`，值为 Keycloak 的 Client Secret，保留原有数据库和存储凭据。它对应插件 `client_secret_env`，不是把密钥直接写进插件 YAML。已有 Root Key/KMS 加密体系时沿用原有兼容的密钥注入方式。

仓库 OpenShift 模板已经通过 `envFrom.secretRef` 引用 `skillhub-backend-secrets`，不必新建另一个 Secret。若已有部署引用 `skillhub-secrets`，就在该资源追加。不要把通用 Kubernetes 示例的独立 Secret 名称直接套进已有 OpenShift 部署。

## 5. 设置开关、地址与证书路径

`MARKET_OAUTH_FRONTEND_ORIGIN` 是 GitCode、GitHub、AgentOS 和第三方插件（如 Keycloak）共用的前端回跳地址，只需配置一次。OpenShift 配置模板的注释示例位于登录/审核公共配置区域，紧随 `MARKET_GITHUB_OAUTH_ENABLED`，Keycloak 同样使用它；部署时在后端实际引用的 ConfigMap 的 `data` 下取消该项注释，填写浏览器实际访问的前端地址，包含协议、实际端口及路径前缀。根路径示例为 `https://hub.example.com`，`/hub` 部署为 `https://hub.example.com/hub`；已有正确值时沿用，不重复添加同名项。保存后滚动重启全部后端副本。此项不代替各渠道的启用开关、客户端凭据和回调地址。

以下是后端进程所需的环境变量。Kubernetes/OpenShift 在后端实际引用的普通 ConfigMap 的 `data` 中合并；Docker Compose 放到现有后端服务的 `environment` 中；直接运行则使用现有服务管理器注入。保留其余配置：

```yaml
data:
  MARKET_IDENTITY_EXTENSIONS_ENABLED: "true"
  MARKET_IDENTITY_EXTENSIONS_CONFIG: "/app/config/identity-extensions.yaml"
  MARKET_OAUTH_FRONTEND_ORIGIN: "https://hub.example.com"
  MARKET_REVIEW_ADMIN_USERNAMES: "reviewer01"
  # 仅内部 CA 路线：必须同时完成下一节的证书文件挂载。
  SSL_CERT_FILE: "/app/certs/ca-bundle.pem"
```

插件渠道的 `enabled` 同时改为 `true`。总开关为 `false` 时，渠道自己的开关不能覆盖它。前端地址使用实际浏览器访问入口，审核员填写 UserInfo 返回的登录名；已有审核名单时追加而不是覆盖。不要修改已有 GitCode/GitHub/AgentOS 的启用项、凭据或回调。第三方登录统一使用 `MARKET_IDENTITY_EXTENSIONS_ENABLED` 总开关，不另设产品专用总开关。

公共 CA 路线省略 `SSL_CERT_FILE`；若部署已有自定义证书包配置，应先核对它是否被其他服务使用，再决定保留或更新。`SSL_CERT_FILE` 是完整容器文件路径，设置环境变量不会创建文件。`MARKET_OAUTH_FRONTEND_ORIGIN` 负责登录完成后的前端回跳，插件 `redirect_uri` 负责接收授权码，不能相互替代。`/hub` 构建的前端基地址可为 `https://hub.example.com/hub`，回调仍可使用根 `/api/v1/.../callback`，须确保该路径被网关转发。

## 6. 按部署方式提供文件

### 6.1 直接运行后端

将第 4 节的配置保存为后端进程可读的文件，使 `MARKET_IDENTITY_EXTENSIONS_CONFIG` 指向其绝对路径。内部 CA 场景同样让 `SSL_CERT_FILE` 指向实际的 `ca-bundle.pem`。例如配置文件放在 `/etc/skillhub/identity-extensions.yaml`，CA 包放在 `/etc/skillhub/certs/ca-bundle.pem`，环境变量就填写这两个路径。

`/app` 是本文容器内路径约定，并非 Skillhub 强制要求。直接运行不需要 ConfigMap 或卷，但文件必须存在、可读。通过现有密钥管理机制注入 Client Secret 后，重启后端服务，并在其实际运行环境中执行第 8 节检查。

### 6.2 Docker Compose

在现有 Compose 文件旁准备 `config/identity-extensions.yaml` 和内部 CA 场景所需的 `certs/ca-bundle.pem`，合并到现有后端服务。服务名以实际为准，仓库 Compose 使用 `backend`：

```yaml
services:
  backend:
    environment:
      MARKET_IDENTITY_EXTENSIONS_ENABLED: "true"
      MARKET_IDENTITY_EXTENSIONS_CONFIG: "/app/config/identity-extensions.yaml"
      MARKET_OAUTH_FRONTEND_ORIGIN: "https://hub.example.com"
      MARKET_REVIEW_ADMIN_USERNAMES: "reviewer01"
      SSL_CERT_FILE: "/app/certs/ca-bundle.pem"
    volumes:
      - type: bind
        source: ./config/identity-extensions.yaml
        target: /app/config/identity-extensions.yaml
        read_only: true
        bind:
          create_host_path: false
      - type: bind
        source: ./certs/ca-bundle.pem
        target: /app/certs/ca-bundle.pem
        read_only: true
        bind:
          create_host_path: false
```

上述内容是合并片段，不是完整 Compose 配置。保留现有镜像、启动命令、环境变量、挂载和依赖；已有审核名单时追加。Client Secret 继续通过现有受控方式注入为 `SKILLHUB_ENTERPRISE_SECRET`，不在示例中写明文。公共 CA 已被信任时省略 CA 文件挂载及 `SSL_CERT_FILE`。同时将插件文件中的渠道 `enabled` 改为 `true`。

相对路径按实际 Compose 项目目录解析。使用现有的 Compose 文件及覆盖文件重建后端容器，使环境变量和挂载生效；仅执行容器 restart 不会应用新的环境变量或挂载定义。随后进入新后端容器执行第 8 节检查。

### 6.3 Kubernetes / OpenShift

ConfigMap 卷、`mountPath`、`subPath` 和只读挂载均为 Kubernetes 通用机制。仓库提供的[原始身份配置、CA 和挂载补丁示例](../../../config/identity-extensions/)位于公共目录 `config/identity-extensions/`。原始身份配置按第 4.1 节生成 ConfigMap；CA ConfigMap 和 Deployment 挂载补丁采用标准 Kubernetes API。用于其他集群时，按实际 Deployment、容器、命名空间及 ConfigMap 名称调整；例如仓库 Kubernetes 后端容器名为 `marketplace`，OpenShift 模板为 `backend`，不能直接照搬容器名。

在现有后端 Deployment 中合并[可选挂载补丁](../../../config/identity-extensions/marketplace-deployment-tls.patch.example.yaml)的对应字段。这是战略合并补丁，不是完整资源，不能直接作为完整 Deployment 应用。OpenShift 可打开 **Workloads → Deployments → skillhub-backend → YAML**，只把相关字段合并到已有 YAML：

1. 在 `spec.template.spec.volumes` 列表中追加 `identity-extensions` 和 `identity-ca-bundle`。已有同名卷时核对并更新，不能添加重复项。
2. 在已有 `containers` 中找到实际后端容器（OpenShift 模板为 `name: backend`，Kubernetes 模板为 `name: marketplace`），在它的 `volumeMounts` 列表中追加两个只读挂载。不要新建第二个 `containers` 或第二个 `backend` 容器。
3. 保留原来的镜像、POD_IP、启动命令、`envFrom`、探针、资源限制和其他挂载。已有部署的 ConfigMap/Secret 引用名称不需要改成模板名称。
4. 公共 CA 路线只保留身份配置卷，删除补丁中 `identity-ca-bundle` 的卷和挂载两项，不设置自定义 `SSL_CERT_FILE`。

| 卷名 | ConfigMap / 数据键 | 容器 `mountPath` | `subPath` |
| --- | --- | --- | --- |
| `identity-extensions` | `skillhub-identity-extensions` / `identity-extensions.yaml` | `/app/config/identity-extensions.yaml` | `identity-extensions.yaml` |
| `identity-ca-bundle` | `skillhub-ca-bundle` / `ca-bundle.pem` | `/app/certs/ca-bundle.pem` | `ca-bundle.pem` |

卷名在 `volumes` 和 `volumeMounts` 中一致；`subPath` 对应卷内文件，证书包的 `items.key` 对应 ConfigMap 数据键。`defaultMode: 420` 为 `0644`，允许非 root 容器读取。无需进入容器手工 `mkdir`、复制证书、运行 `update-ca-trust` 或修改系统文件。

保存 Pod 模板变更后 Deployment 会自动滚动更新。单纯更新 ConfigMap/Secret 不会自动让所有旧 Pod 加载新配置；应通过部署工具滚动重启，例如 `kubectl -n <命名空间> rollout restart deployment/<后端Deployment名称>`。OpenShift 可在 Deployment 的 Actions 中使用 **Restart rollout**，按钮译名依版本而异。插件配置、环境变量和 CA 包均在更新后检查全部副本。

## 7. Kubernetes / OpenShift：确认滚动更新没有被配额阻止

本节适用于 Deployment 部署；直接运行和 Docker Compose 按第 6 节重启服务或重建容器。以下为 OpenShift 控制台检查步骤，其他 Kubernetes 平台可通过部署工具查看同样的 Conditions、Events 和 ReplicaSet 状态。

1. 在 Deployment **详情 → Conditions** 和 **Events** 中核对进度。`Available=True` 可能只表示旧 Pod 仍可服务；同时出现 `Progressing=False`、`ReplicaSetCreateError` 表示新模板尚未生效。
2. 如果报 `count/replicasets.apps` 已达上限，先联系管理员调整配额，或评估清理无需回滚的历史版本。在 Deployment 的 **ReplicaSets** 列表逐个确认目标所有者、期望/实际副本数均为 `0`、没有运行中的 Pod，必要时先下载 YAML 留存，再进入该历史 ReplicaSet 的 Actions 删除。删除会移除对应回滚记录，不删除当前服务中的 ReplicaSet、Deployment、数据库或 Secret。
3. 释放名额后等待控制器重试，观察新 ReplicaSet 是否创建、Pod 是否就绪。仍失败时检查新事件，而不是反复删除旧 Pod。释放的名额可能立即被新 ReplicaSet 占用，配额数字不一定停留在删除后的数值。
4. 在 **Deployment → Pods** 核对新 Pod 的所有者是新 ReplicaSet。仅凭创建时间不够：旧 ReplicaSet 重建的 Pod 仍使用旧挂载模板，但它可能读取到 ConfigMap 中更新后的环境变量，形成“有 `SSL_CERT_FILE`、无对应文件”的现象。
5. 等全部后端副本 `Running`、容器 `1/1 Ready` 且滚动更新完成，再继续。新 Pod 的详情卷表或 YAML 应包含第 6 节的挂载。

历史名称按每次模板变化生成，不在配置中写死现场的 ReplicaSet 名称。需要限制后续历史数量时，可按回滚要求单独评估 `spec.revisionHistoryLimit`；降低该值不保证立即修复一个已经失败的部署，本文不改变基础模板的保留策略。[Deployment 历史清理](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/#clean-up-policy)

## 8. 在实际后端运行环境验证证书与登录

### 8.1 确认挂载和证书信任

在实际后端运行环境执行以下检查：直接运行时使用后端服务相同的环境变量和用户；Docker Compose 进入重建后的后端容器；Kubernetes/OpenShift 进入新 Pod 的实际后端容器（OpenShift 可打开 Pod 的 **Terminal**）。内部 CA 路线执行，直接运行时替换为第 6.1 节的实际路径：

```sh
printenv SSL_CERT_FILE
ls -l /app/config/identity-extensions.yaml /app/certs/ca-bundle.pem
test -r /app/certs/ca-bundle.pem && echo "证书文件存在且可读"
```

预期路径与第 6 节所选部署方式一致；两个文件均存在且可读。对每个后端副本分别检查，不使用旧容器或旧 Pod 的 Terminal 标签页。下方脚本按环境变量读取实际路径。

下面的只读脚本适用于两条 CA 路线，从实际插件配置读取 Issuer，不输出 Client Secret 或 Token。渠道更名时修改 `channel`：

```sh
python3.11 - <<'PY'
import os
import ssl
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import yaml

channel = "keycloak"
config = yaml.safe_load(
    Path(os.environ["MARKET_IDENTITY_EXTENSIONS_CONFIG"]).read_text(encoding="utf-8")
)["providers"][channel]["settings"]
assert config.get("ssl_verify", True) is True, "请保持 ssl_verify: true"
issuer = config["issuer"].rstrip("/")
ca_path = os.getenv("SSL_CERT_FILE")
if ca_path:
    assert Path(ca_path).is_file(), f"证书文件不存在: {ca_path}"
    context = ssl.create_default_context(cafile=ca_path)
    print("证书包:", ca_path, "CA 数量:", len(context.get_ca_certs()))
else:
    print("使用 HTTPX 默认公共 CA 信任")

with httpx.Client(verify=True, timeout=20, follow_redirects=False) as client:
    response = client.get(issuer + "/.well-known/openid-configuration")
    print("Discovery HTTP:", response.status_code)
    response.raise_for_status()
    metadata = response.json()
    assert metadata["issuer"] == issuer, "Discovery issuer 与配置不一致"
    for field in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint", "jwks_uri"):
        parts = urlsplit(metadata[field])
        assert parts.scheme == "https" and parts.hostname, field + " 必须是 HTTPS 地址"
    keys = client.get(metadata["jwks_uri"])
    print("JWKS HTTP:", keys.status_code)
    keys.raise_for_status()
    assert keys.json().get("keys"), "JWKS 没有公钥"
    print("Issuer:", metadata["issuer"])
PY
```

预期 Discovery、JWKS 都为 `200`，Issuer 完全一致。脚本显式使用 `verify=True`，没有绕过证书验证。Token 和 UserInfo 的实际访问由完整登录及后续身份查询验证；不要为诊断把密钥或 Token 粘贴到终端输出、日志或截图中。

### 8.2 验证登录、发布与审核

1. 浏览器打开 Skillhub 的 `/api/v1/site/config`，确认扩展渠道 `id: keycloak`、`adapter: keycloak` 和登录按钮文字正确。渠道为空时先查总开关、渠道开关和初始化日志。
2. 从 Skillhub 登录按钮发起，而不是手工复制包含临时参数的 Keycloak 授权 URL。使用发布者账号，完成 Realm 要求的操作后返回 Skillhub。
3. 在浏览器开发者工具 Network 中查看 `/api/v1/auth/me`：发布者 `data.login` 是预期用户名、`is_market_moderation_admin` 为 `false`；独立会话中审核员对应标志为 `true`。默认不允许自己审核自己发布的资产。
4. 使用已下载的 Skill 包执行[用户指南中的上传和审核步骤](<../4. 用户指南/Keycloak 登录与 Skill 发布审核.md>)，分别验证发布、审核通过、公开检索与下载。它们沿用现有鉴权和业务逻辑，`ssl_verify: true` 同样覆盖后续 UserInfo 查询。

完成标准：所有后端进程或容器副本已更新，所需文件可读，校验证书的 Discovery/JWKS 请求成功，完整登录和 `/auth/me` 身份正确，Skill 发布与不同审核员审批成功。`/api/health` 成功不能替代数据库结构、Redis 共享、证书或业务验证。

## 9. 故障排查与证书更新

| 现象 | 检查与处理 |
| --- | --- |
| Discovery 返回 `Application is not available` | 检查 Keycloak 副本数、就绪状态、Service 后端和 Route 路径/端口 |
| `CERTIFICATE_VERIFY_FAILED`、`self-signed certificate in certificate chain` | 确认新 Pod 读取了实际 CA 包，根 CA 正确，服务器提供必要中间链；保留 `ssl_verify: true` 解决信任问题 |
| `SSL_CERT_FILE` 有值，但 `ls` 找不到文件 | 检查实际 Pod 所属 ReplicaSet、卷是否挂到 `backend`、ConfigMap 名称和键、路径大小写，查新 Pod 的 `FailedMount` 事件 |
| 新 Deployment 有卷，但仍是旧 Pod | 检查 Conditions/Events 中配额、镜像拉取、调度或准入错误；按第 7 节解除阻塞 |
| PEM 加载失败或 CA 数量异常 | 数据值须为完整证书正文，不是本地路径、示例占位符、损坏的 PEM 或私钥 |
| 证书域名不匹配或过期 | 修复 Route 的服务器证书；增加根 CA 不能修复域名或有效期错误 |
| `OIDC issuer mismatch` | 对照 Discovery 的 Issuer，检查 Keycloak hostname/反向代理及 Realm；不要改为一个与 Token 签发方不一致的内部地址 |
| Keycloak 报 `Invalid parameter: redirect_uri` | 核对协议、端口、渠道 ID 和完整回调路径；客户端允许地址与插件配置一致 |
| 登录回调在不同后端间失败 | 检查所有副本使用共享 Redis，PING 成功且命名空间一致，未回退内存 |
| 登录成功，发布报缺少 `has_preview` | 已有库需核对 [B006 增量迁移](../../../sql/incremental/v0.0.2.B006/openjiuwen_market/DDL/market_asset_versions_has_preview.sql)及[升级说明](升级说明.md)；备份后按条件执行，重启不会自动补列 |

更新企业 CA 或公共 CA 包时，按原维护流程重新合并、更新 `skillhub-ca-bundle`，再按部署方式重启服务、重建容器或滚动更新全部后端副本并重复第 8 节。Kubernetes/OpenShift 的 `subPath` 挂载不向已有 Pod 自动同步 ConfigMap 后续内容，HTTP 客户端也可能保留已加载的信任上下文；不要直接编辑运行中容器的证书文件。[ConfigMap 挂载说明](https://kubernetes.io/docs/tasks/configure-pod-container/configure-pod-configmap/)

保持 `ssl_verify: true`，无需开启 `allow_insecure_http`，也无需修改对象存储的 `MARKET_S3_SSL_VERIFY`。若首次接入或证书更新失败，先检查新版本的实际状态和上述错误，再决定恢复上一个已验证的部署配置；不要覆盖其他渠道、资产或数据库。GitCode/GitHub/AgentOS 的原有认证代码和基础清单的默认开关均不因本文而改变。
