# Keycloak 登录与 Skill 发布审核

本指南适用于已在服务器上部署 Skillhub 和 Keycloak 的环境。管理员配置 Keycloak 登录后，发布者可以上传已下载的 Skill，审核员可在 Skillhub 中处理发布申请。

## 1. 确认服务器访问地址

准备 Skillhub 和 Keycloak 的实际 HTTPS 地址、目标 Realm，以及能管理 Keycloak 客户端和 Skillhub 后端配置的管理员账号。示例中的域名、Realm 和用户名按实际环境替换。

| 项目 | 服务器配置示例 |
|---|---|
| Skillhub 前端入口 | `https://hub.example.com` |
| Keycloak 访问地址 | `https://sso.example.com` |
| Keycloak Realm | `company` |
| Keycloak Issuer | `https://sso.example.com/realms/company` |
| OIDC 客户端 ID | `skillhub` |
| Skillhub 渠道 ID | `enterprise` |
| Skillhub 回调地址 | `https://hub.example.com/api/v1/auth/oauth/enterprise/callback` |
| 发布者与审核员 | `publisher01`、`reviewer01` |

浏览器需要能访问 Skillhub 和 Keycloak 的对外地址。Skillhub 后端需要能访问 Keycloak 的 Discovery、Token、UserInfo 和 JWKS 地址，并信任 Keycloak 的 HTTPS 证书。Skillhub 网关需要将回调请求转发给后端。

## 2. 在 Keycloak 配置客户端和账号

### 创建 OIDC 客户端

1. 登录 Keycloak 管理控制台并切换到目标 Realm。如果需要新建业务 Realm，在 Realm 选择器中选择 **Create realm**，填入名称，例如 `company`，并保存。
2. 进入 **Clients → Create client**。设置 **Client type** 为 `OpenID Connect`，**Client ID** 为 `skillhub`。如果客户端已存在，打开它，按后续步骤核对配置。
3. 打开 **Client authentication** 和 **Standard flow**。本流程使用授权码模式，可以关闭 **Direct access grants**、**Implicit flow** 和 **Service accounts roles**。
4. 在客户端的登录设置中，将第 1 节选定的完整回调地址填入 **Valid redirect URIs**，例如 `https://hub.example.com/api/v1/auth/oauth/enterprise/callback`。**Root URL** 和 **Home URL** 可以留空，或填写 Skillhub 前端入口。本流程可将 **Web origins** 留空。
5. 保存客户端。在 **Advanced** 设置中确认 **PKCE method** 为 `S256`，并确认 **ID Token Signature Algorithm** 为 `RS256`，或继承使用 `RS256` 的 Realm 默认值。不同 Keycloak 版本可能将 PKCE 放在创建向导或高级设置中。
6. 在 **Client scopes** 确认客户端可以请求 `profile` 和 `email`，并确认 UserInfo 能返回 `sub` 和 `preferred_username`。`name` 和 `picture` 可用于显示姓名和头像。定制过客户端映射时，检查用户名映射已启用 **Add to userinfo**。
7. 在 **Credentials** 页确认 **Client Authenticator** 为 **Client Id and Secret**，把生成的 **Client Secret** 存入部署所用的密钥管理系统，供 Skillhub 后端读取。

管理控制台字段参考。 [Keycloak 客户端配置](https://www.keycloak.org/docs/latest/server_admin/index.html#_oidc_clients)

### 确定发布者和审核员账号

1. 在客户端所在的 Realm 选择两个已启用的账号，分别作为发布者和审核员。需要新增账号时，在 **Users → Add user** 中创建，并按界面填写所需信息。
2. 若新增了账号，在 **Credentials → Set password** 中设置密码。启用临时密码时，用户需要在首次登录时修改密码。
3. 检查发布者和审核员的 **Username**。后续配置审核名单时填写 `reviewer01` 这样的用户名。Skillhub 使用 Keycloak 返回的 `preferred_username` 与审核名单匹配。

新建用户时同时填写邮箱、名字和姓氏，并确认已完成 Realm 所要求的首次登录操作。若密码设置为临时密码，先按 Keycloak 提示修改后再返回 Skillhub。

两个账号都可以是 Realm 普通用户。Skillhub 按现有权限规则允许已登录用户发布，并向审核名单中的用户授予审核权限。 [Skillhub 角色与权限](./角色与权限.md)

## 3. 启用 Skillhub 的 Keycloak 登录

### 创建身份扩展配置

将仓库的 `config/identity-extensions.example.yaml` 复制到服务器的部署配置目录，命名为 `identity-extensions.yaml`。将示例地址和客户端 ID 替换为第 1、2 节确定的值。若部署已有扩展渠道，在现有 YAML 的 `providers` 中追加此项。 [配置模板](../../../config/identity-extensions.example.yaml)

```yaml
providers:
  enterprise:
    enabled: true
    adapter: keycloak
    display_name: 企业账号登录
    settings:
      issuer: https://sso.example.com/realms/company
      client_id: skillhub
      client_secret_env: SKILLHUB_ENTERPRISE_SECRET
      redirect_uri: https://hub.example.com/api/v1/auth/oauth/enterprise/callback
      scopes: [openid, profile, email]
      token_endpoint_auth_method: client_secret_basic
      login_claim: preferred_username
      name_claim: name
      avatar_claim: picture
      algorithms: [RS256]
```

- `enabled: true` 开启当前渠道，还须打开后端的框架总开关 `MARKET_IDENTITY_EXTENSIONS_ENABLED`，渠道才会生效。总开关默认 `false`，关闭时所有扩展渠道均不可用。需要独立控制该渠道时，可用 `enabled_env: ENTERPRISE_LOGIN_ENABLED` 替换此行，并同时将总开关与 `ENTERPRISE_LOGIN_ENABLED` 设为 `true`。原来的 `enabled_env: MARKET_IDENTITY_EXTENSIONS_ENABLED` 写法继续兼容。
- `display_name` 是登录页的按钮文字。`enterprise` 是 Skillhub 渠道 ID。更改渠道 ID 时，同步更新两边回调地址中的对应路径段。
- `issuer` 是 Realm 的标识。适配器会访问它下面的 `/.well-known/openid-configuration`，自动取得授权、Token、UserInfo 和 JWKS 地址。Discovery 返回的 `issuer` 必须与配置值一致。 [Keycloak OIDC 端点](https://www.keycloak.org/securing-apps/oidc-layers)
- `redirect_uri` 是 Keycloak 登录后让浏览器访问的 Skillhub 后端回调地址。客户端的 **Valid redirect URIs** 要包含同一个 URL。
- `client_secret_env` 只填环境变量名。`client_secret_basic` 指 Skillhub 后端在兑换授权码时，用客户端 ID 和密钥进行 HTTP Basic 客户端认证。
- 需要每次点击登录都重新显示 Keycloak 认证页面时，在 `settings` 下增加 `prompt: login`。Skillhub 退出后，Keycloak 的浏览器单点登录会话可能继续有效。启用此项后，再次登录将要求重新认证，可在认证页切换用户。

### 配置后端环境变量

将这些值写入现有的后端部署配置。容器场景使用容器内可读的绝对路径。

```dotenv
MARKET_IDENTITY_EXTENSIONS_CONFIG=/app/config/identity-extensions.yaml
MARKET_IDENTITY_EXTENSIONS_ENABLED=true
MARKET_OAUTH_FRONTEND_ORIGIN=https://hub.example.com
MARKET_REVIEW_ADMIN_USERNAMES=reviewer01
```

- `MARKET_IDENTITY_EXTENSIONS_ENABLED` 是所有第三方账号扩展的总开关，渠道的 `enabled` 或其他 `enabled_env` 不能覆盖它。关闭后扩展登录、回调、会话兑换以及已有扩展 token 的业务鉴权均被拒绝，GitCode、GitHub、AgentOS 和系统管理员 Token 的原有行为不变。修改开关后需重启全部后端副本；不会删除已有资产，也不会撤销 Keycloak 自身的 token 或单点登录会话。
- `MARKET_OAUTH_FRONTEND_ORIGIN` 决定后端完成 OIDC 回调后将用户送回哪个 Skillhub 前端。填写浏览器访问的前端基地址。前端以 `/hub` 部署时，基地址可为 `https://hub.example.com/hub`，回调继续使用同一域名根路径下的 `/api/v1/auth/oauth/enterprise/callback`，并确保网关转发这条路径。
- 审核名单已有账号时，将 `reviewer01` 追加到原名单，例如 `alice,bob,reviewer01`。如果部署使用 `MARKET_REVIEW_ADMIN_USERNAMES_FILE`，在原名单文件中追加，保持环境变量名单为空以继续使用文件。权限判断按用户名跨渠道匹配，应采用在已启用渠道中不会冲突的审核员用户名，并由管理员管控其使用。
- 通过部署的密钥管理机制，将 Keycloak Client Secret 以 `SKILLHUB_ENTERPRISE_SECRET` 注入后端进程。已启用 Skillhub 密钥加密的部署，沿用现有 Root Key 或 KMS 体系生成兼容密文再注入。将真实凭证仅保存在密钥管理系统及其受控运行环境。 [Skillhub 密钥读取实现](../../../marketplace/common/security/security_utils.py)
- Skillhub 默认禁止自审。启用自动审查的环境中，Skill 需先通过自动审查，再进入人工审核。 [配置项与默认值](../../../.env.example)

### 应用服务器配置

确认已部署的 Skillhub 前端和后端均包含身份扩展功能。后端有多个副本时，各副本使用同一个 Redis 保存 OAuth 登录状态。

**服务器使用 Docker Compose。** 在现有后端服务配置中设置上述环境变量，将服务器上的 `identity-extensions.yaml` 只读挂载到配置指定的容器路径，并通过现有的密钥管理机制注入 `SKILLHUB_ENTERPRISE_SECRET`。用服务器现有的 Compose 编排及其覆盖文件重建后端容器，使环境变量和挂载生效。

**Kubernetes 或 OpenShift。** 在后端引用的 ConfigMap 的 `data` 下更新这四个环境变量，值须写成字符串，例如 `MARKET_IDENTITY_EXTENSIONS_ENABLED: "true"`。仓库的 Kubernetes 清单使用 `marketplace-env`，OpenShift 清单使用 `skillhub-backend-config`。两份模板默认都将此开关设为 `"false"`。 [Kubernetes ConfigMap](../../../docker/k8s/marketplace-config.yaml) · [OpenShift ConfigMap](../../../docker/openshift/manifests/marketplace-configmap.yaml)

在现有 ConfigMap 的 `data` 下合并以下内容，并保留其他已有项。保存文件后，通过部署平台或 `kubectl apply -f <配置文件>` 应用。

```yaml
data:
  MARKET_IDENTITY_EXTENSIONS_CONFIG: "/app/config/identity-extensions.yaml"
  MARKET_IDENTITY_EXTENSIONS_ENABLED: "true"
  MARKET_OAUTH_FRONTEND_ORIGIN: "https://hub.example.com"
  MARKET_REVIEW_ADMIN_USERNAMES: "reviewer01"
```

在 Skillhub 所在命名空间创建用于挂载 YAML 的 ConfigMap。下面用命名空间 `skillhub` 示意，首次创建时在保存 `identity-extensions.yaml` 的配置目录执行。已有同名对象时，用部署平台更新它的 `identity-extensions.yaml` 键。

```shell
kubectl -n skillhub create configmap skillhub-identity-extensions --from-file=identity-extensions.yaml
```

通过集群控制台或已有的密钥管理流程创建 `skillhub-keycloak` Secret，添加键 `SKILLHUB_ENTERPRISE_SECRET`，将客户端密钥作为值。再把下面的环境变量与文件挂载合并到现有后端 Deployment。

ConfigMap、Secret 和后端 Deployment 应在同一个命名空间。本文所有命令的 `skillhub`、Deployment 名称和后端容器名称都按实际部署替换。集群控制台创建 Secret 时选择文本输入客户端密钥，确认键名为 `SKILLHUB_ENTERPRISE_SECRET`。

```yaml
# 后端 Deployment 的 spec.template.spec 下
volumes:
  - name: identity-extensions
    configMap:
      name: skillhub-identity-extensions

# 现有后端 container 下
env:
  - name: SKILLHUB_ENTERPRISE_SECRET
    valueFrom:
      secretKeyRef:
        name: skillhub-keycloak
        key: SKILLHUB_ENTERPRISE_SECRET
volumeMounts:
  - name: identity-extensions
    mountPath: /app/config/identity-extensions.yaml
    subPath: identity-extensions.yaml
    readOnly: true
```

`volumes` 属于 Pod 模板，`env` 和 `volumeMounts` 属于后端容器。后端通过 `envFrom` 读取上述环境变量 ConfigMap。应用修改后的 Deployment，确认后端完成滚动更新。仅更新 ConfigMap 中的开关或配置文件时，执行 `kubectl -n skillhub rollout restart deployment/skillhub-backend` 让新 Pod 读取最新配置。 [Kubernetes 后端部署模板](../../../docker/k8s/marketplace-deploy.yaml) · [OpenShift 部署说明](../../../docker/openshift/DEPLOYMENT.md)

合并时向现有 `env`、`volumeMounts` 和 `volumes` 列表追加项目。确认包含适配器的镜像已部署到后端，且配置文件在容器内可读。

```shell
kubectl -n skillhub rollout status deployment/skillhub-backend
```

## 4. 使用 Keycloak 登录 Skillhub

配置应用后，先检查三个位置。

1. 从后端 Pod 的终端访问 `https://sso.example.com/realms/company/.well-known/openid-configuration`，确认返回的 `issuer` 与 YAML 一致，且后端可访问响应中的 Token、UserInfo 和 JWKS 地址。
2. 在浏览器打开 `https://hub.example.com/api/v1/site/config`，确认 `identity_extensions` 中包含 `id: enterprise`、`adapter: keycloak` 和 `display_name: 企业账号登录`。
3. 如果渠道为空，检查后端 Pod 日志、ConfigMap 的开关和文件路径、Secret 的键名以及实际挂载。修正后滚动更新后端。

配置多个扩展渠道时，一个渠道初始化失败只会隐藏和禁用该渠道，其他正常渠道继续可用。同一 Keycloak 适配器下的渠道分别校验配置和 Secret。若只有某个渠道缺失，查后端 `stage=provider_init` 日志中的 `provider_id`、配置项序号 `entry_index` 和 `error_type`；若整份 YAML 无法读取、语法或顶层结构错误、存在重复键等，则所有扩展渠道关闭，查 `stage=registry_init`。日志不输出原始异常或密钥值。修复后滚动更新全部后端副本，初始化失败不会在每次登录时自动重试。

打开服务器上的 Skillhub，点击 **登录 → 企业账号登录**。浏览器跳转到 Keycloak 后，使用发布者账号登录，例如 `publisher01`。返回 Skillhub 后，在个人中心查看当前账号。

浏览器开发者工具的 **Network** 面板中，`/api/v1/auth/me` 响应的 `data.login` 应为 `publisher01`，`data.is_market_moderation_admin` 应为 `false`。审核员登录后，后一个字段应为 `true`。

排查登录问题时，可通过响应头的 `x-request-id` 查询后端现有日志目录中的 `app.log`，查看 `provider=enterprise` 以及 `oauth_start`、`oauth_callback`、`oauth_session_exchange` 或 `oauth_me` 操作。每个请求的完成日志包含操作 ID 和处理结果；回调通过 `302` 返回登录页时也可能是认证失败，应查看 `result` 和标准错误码。总开关关闭、渠道初始化失败及请求参数错误也会记录。上述操作日志不会输出原始凭证、完整回调参数或插件异常正文，日志查询时无需提供 Token 或 Client Secret。操作日志和资产发布、审核的数据库审计记录分别用于技术排查与业务追溯。 [操作日志说明](./扩展账号接入.md#操作日志与错误处理)

## 5. 用发布者账号上传资产

以已下载的 `hello-skill` 为例。

1. 在 Skillhub 右上角点击 **+ 发布**，选择 **Skill** 和 **新 Skill（首次发布）**。给已有 Skill 发布新版本时，在表单中关联该 Skill。
2. 点击目录选择器，选择已下载的 Skill 目录。如果下载的是 ZIP，先解压，再选择直接包含 `SKILL.md` 的目录。
3. 填写表单。**Skill 包名**与 `SKILL.md` 中的 `name` 一致，例如 `hello-skill`。填写**显示名**和**版本号**，首次发布可用 `1.0.0`。按发布需求选择 **公开** 或 **私有**。
4. 点击 **发布Skill**。在 **个人中心 → 我的上传** 查看该版本。**审核中**表示已进入人工审核队列。**审查中**表示自动审查尚未结束。**发布失败**时，打开详情查看原因。 [Skill 发布表单与状态](./Agent资产发布.md)

## 6. 用审核员账号审核 Skill

1. 在另一浏览器配置文件或无痕窗口打开 Skillhub，通过 **企业账号登录**使用审核员账号，例如 `reviewer01`。
2. 进入 **个人中心 → 待审核**，在 **资产类型** 中选择 **Skill**。按显示名找到提交的 Skill，打开详情，核对发布者和待审核版本。
3. 点击 **审核通过**。页面显示「审核已更新」后，可以在 **审核历史** 查看处理记录。如需退回，点击 **审核驳回**并填写原因。 [审核管理员操作](./角色与权限.md#审核管理员操作)
4. 审核通过后，发布者可在 **个人中心 → 我的上传** 查看 **发布成功** 的状态。公开的 Skill 随后进入市场，私有的 Skill 按权限访问。

## 常见问题

| 现象 | 按顺序检查 |
|---|---|
| 登录页没有 **企业账号登录** | 查 `/api/v1/site/config` 的 `identity_extensions`。为空时先确认总开关 `MARKET_IDENTITY_EXTENSIONS_ENABLED=true`，再检查渠道的 `enabled` 或 `enabled_env`、配置挂载、密钥注入和启动日志，并确认所有后端副本已重启。接口已有渠道时，确认浏览器加载的是包含扩展入口的前端构建。 |
| 其他第三方渠道正常，只有企业渠道缺失 | 查 `stage=provider_init`、`provider_id=enterprise` 的初始化日志，核对该渠道字段、适配器目录、Secret 注入及异步接口。修正后重启全部后端副本。正常渠道无需删除或重新配置。 |
| Keycloak 报 `Invalid parameter: redirect_uri` | 逐字符对照客户端的 **Valid redirect URIs** 和 YAML 的 `redirect_uri`，包括 HTTPS、端口、渠道 ID、路径和末尾斜杠。 |
| 登录回调失败或兑换授权码失败 | 检查回调请求是否经原访问域名到达 Skillhub 后端。核对客户端认证、密钥、`S256`、Issuer、证书和后端网络。多副本部署检查共享 Redis。修正后从登录页重新发起登录。 |
| 需要定位某次登录失败 | 用响应头 `x-request-id` 查询 `app.log` 中对应操作的完成结果、错误码和阶段诊断。发起登录、回调和会话兑换分别是不同请求，不应把 state、授权码或 Token 复制到日志中作为关联值。 |
| 使用审核员账号登录后没有 **待审核** | 查 `/api/v1/auth/me` 的 `login` 与权限标志。核对 Keycloak 的 `preferred_username`、Skillhub 生效的审核名单及其优先级。修改环境变量后滚动更新后端，再用独立窗口登录。 |
| 有 **待审核**菜单，但找不到版本 | 用发布者账号确认提交成功，并检查版本处于 **审核中**。若仍为 **审查中**，先看自动审查结果。 |

适配器配置、登录链路和扩展约定可参见仓库说明。 [扩展账号接入](./扩展账号接入.md)
