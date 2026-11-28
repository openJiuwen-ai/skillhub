# 第三方账号配置与证书挂载示例

本目录集中提供第三方账号插件的可选配置和证书挂载示例，当前以 Keycloak 为例。只维护一份原始插件配置，统一使用渠道 ID `keycloak` 和适配器 `keycloak`。示例不自动加载，不属于 Kubernetes 或 OpenShift 的默认部署集合。

| 文件 | 用途 |
| --- | --- |
| [identity-extensions-configmap.example.yaml](identity-extensions-configmap.example.yaml) | 唯一的原始插件 YAML，顶层为 `providers`；渠道默认 `enabled: false`，`ssl_verify: true` |
| [ca-bundle-configmap.example.yaml](ca-bundle-configmap.example.yaml) | 可信 CA 包的 ConfigMap 结构，必须用实际的公共及企业 CA PEM 内容替换占位值 |
| [marketplace-deployment-tls.patch.example.yaml](marketplace-deployment-tls.patch.example.yaml) | 向现有后端 Deployment 合并两个只读文件挂载的战略合并补丁 |

保留文件名 `identity-extensions-configmap.example.yaml`，但其内容不是 Kubernetes ConfigMap 资源，不含 `apiVersion`、`kind` 或 `data`。不要对这个原始文件执行 `kubectl apply -f`、`oc apply -f`，也不要直接导入控制台的 YAML 资源视图。

完整配置、CA 合并、各部署方式挂载及验证步骤见[Keycloak HTTPS 证书配置与挂载](<../../docs/zh/3. 安装指导/Keycloak HTTPS 证书配置与挂载.md>)；登录、Skill 发布和审核步骤见[用户指南](<../../docs/zh/4. 用户指南/Keycloak 登录与 Skill 发布审核.md>)。

- **Kubernetes / OpenShift**：从该原始文件创建 ConfigMap，固定数据键为 `identity-extensions.yaml`。从仓库根目录执行的首次创建示例为 `kubectl -n skillhub create configmap skillhub-identity-extensions --from-file=identity-extensions.yaml=./config/identity-extensions/identity-extensions-configmap.example.yaml`；命名空间按实际替换，OpenShift 可用 `oc` 替换 `kubectl`。创建/更新命令及控制台表单步骤见[通用指南第 4.1 节](<../../docs/zh/3. 安装指导/Keycloak HTTPS 证书配置与挂载.md#41-创建身份配置-configmap>)。生成的 ConfigMap 供部署使用，不作为第二份源码模板维护。
- **挂载补丁**：按实际 Deployment 和后端容器名修改。示例使用容器名 `backend`，仓库 Kubernetes 模板使用 `marketplace`。补丁不是完整 Deployment，不能直接作为完整资源应用或覆盖现有配置；不要批量应用未经填写的示例。
- **Docker Compose / 直接运行**：直接复制唯一原始文件到受控部署目录，可命名为 `identity-extensions.yaml`，按通用指南提供文件及可选 CA 包，使 `MARKET_IDENTITY_EXTENSIONS_CONFIG` 指向后端实际可读的原始配置路径。无需抽取嵌套字段或创建 ConfigMap。
- **默认开关**：本目录渠道示例保持关闭。只有显式打开 `MARKET_IDENTITY_EXTENSIONS_ENABLED` 和所选渠道的 `enabled`，并提供有效文件与凭据后才启用；迁移示例位置不会打开登录渠道。
- **渠道名称**：示例的 `providers.keycloak`、回调 `/api/v1/auth/oauth/keycloak/callback` 和身份查询中的渠道 ID 必须一致。`adapter: keycloak` 继续选择 Keycloak 适配器目录。已有部署可保留原渠道 ID，无需因为示例统一而更名。
- **证书与凭据**：后端已信任 Keycloak 的 CA 时无需额外 CA 包；企业内部 CA 场景合并所需公共 CA 和企业根 CA。`SSL_CERT_FILE` 指向实际可读的 CA 包，并会影响进程中使用默认环境设置的 HTTPX 客户端。Client Secret 通过现有密钥管理机制注入，不写入插件文件或 CA ConfigMap。

`client_secret_env: SKILLHUB_ENTERPRISE_SECRET` 是凭据变量名，与渠道 ID 独立，保留原名称以兼容已有 Secret 注入。若需自定义名称，同时修改 `client_secret_env` 和部署中的 Secret/环境变量键即可，不需要修改认证代码。

所有文件仅为部署配置示例，不修改 GitCode/GitHub/AgentOS 的认证、发布或审核实现。
