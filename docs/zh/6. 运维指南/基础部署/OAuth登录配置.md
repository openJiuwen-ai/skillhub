# OAuth 登录配置

SkillHub Web 登录支持 GitCode、GitHub 和 AgentOS OAuth。如需使用 Web 登录，至少启用一种；同时启用时，登录页会显示三个入口。浏览公开内容无需配置 OAuth。

## 确定访问地址

配置 OAuth 前，先确定用户实际访问 frontend 的地址。本地开发统一使用：

```text
http://skillhub.local:9002
```

Windows 用户须先使用管理员权限编辑 `C:\Windows\System32\drivers\etc\hosts`，增加以下映射，再执行 `ipconfig /flushdns`：

```text
127.0.0.1 skillhub.local
# 如果需要使用agentos登录，取消如下注释，agentos.local为agentos前端域名
# 127.0.0.1 agentos.local
```

应用主页和回调地址统一填写为：

```text
应用主页：http://skillhub.local:9002
GitCode 回调：http://skillhub.local:9002/api/v1/auth/oauth/gitcode/callback
GitHub 回调：http://skillhub.local:9002/api/v1/auth/oauth/github/callback
AgentOS 回调：http://skillhub.local:9002/api/v1/auth/oauth/agentos/callback
```

应用主页、回调地址、浏览器访问地址和 `MARKET_OAUTH_FRONTEND_ORIGIN` 必须使用同一域名。不要混用 `localhost`、`127.0.0.1` 和 `skillhub.local`。

通过域名对外提供服务（如 K8s Ingress）时，无需配置 hosts，应用主页、回调地址和 `MARKET_OAUTH_FRONTEND_ORIGIN` 直接使用实际域名（含端口）。

> 配置项的写入位置随部署方式不同：本地安装和 Docker 一键部署均写 `.env`，K8s 写入 `marketplace-config.yaml`（非敏感项）和 `skillhub-secrets` Secret（Client ID/Secret）。

## GitCode OAuth

1. 登录 GitCode，进入“个人设置 → OAuth 应用”，创建应用。
2. 填写应用名称、描述和 Logo，将应用主页和回调地址设置为上文对应地址。
3. 应用权限仅选择“用户”下的“访问你的个人信息、最新动态等”。SkillHub 登录不需要公钥、组织、项目等权限。

![GitCode OAuth 应用配置](../../assets/img/一键部署-GitCode-OAuth应用.png)

4. 创建后记录 Client ID 和 Client Secret，并在 `.env` 中填写：

```ini
MARKET_GITCODE_OAUTH_ENABLED=true
MARKET_GITCODE_OAUTH_CLIENT_ID=你的GitCode客户端ID
MARKET_GITCODE_OAUTH_CLIENT_SECRET=你的GitCode客户端密钥
MARKET_GITCODE_OAUTH_REDIRECT_URI=http://skillhub.local:9002/api/v1/auth/oauth/gitcode/callback
MARKET_GITCODE_OAUTH_SCOPE=user_info
MARKET_OAUTH_FRONTEND_ORIGIN=http://skillhub.local:9002
```

GitCode 的授权、换令牌和用户信息接口已有默认值，通常无需修改。协议接口说明见 [GitCode OAuth 文档](https://docs.gitcode.com/docs/apis/oauth/)。

## GitHub OAuth

1. 登录 GitHub，依次进入 `Settings → Developer settings → OAuth Apps`。
2. 点击 `New OAuth App`，填写：
   - `Application name`：应用名称，例如 `SkillHub Local`。
   - `Homepage URL`：frontend 访问地址，例如 `http://skillhub.local:9002`。
   - `Authorization callback URL`：上文的 GitHub 回调地址。
3. 注册应用并生成 Client Secret，记录 Client ID 和 Client Secret。
4. 在 `.env` 中填写：

```ini
MARKET_GITHUB_OAUTH_ENABLED=true
MARKET_GITHUB_OAUTH_CLIENT_ID=你的GitHub客户端ID
MARKET_GITHUB_OAUTH_CLIENT_SECRET=你的GitHub客户端密钥
MARKET_GITHUB_OAUTH_REDIRECT_URI=http://skillhub.local:9002/api/v1/auth/oauth/github/callback
MARKET_GITHUB_OAUTH_SCOPE=read:user user:email
MARKET_OAUTH_FRONTEND_ORIGIN=http://skillhub.local:9002
```

GitHub OAuth App 的注册步骤见 [GitHub 官方文档](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app)。

## AgentOS OAuth

1. 目前AgentOS通过环境变量配置OAuth认证能力。需要在`AgentOS`的`.env`中填写：

```ini
OAUTH2_CLIENT_ID=你的AgentOS客户端ID
OAUTH2_CLIENT_SECRET=你的AgentOS客户端密钥
OAUTH2_REDIRECT_URI=http://skillhub.local:9002/api/v1/auth/oauth/agentos/callback
# 默认ACESS_TOKEN有效期为一天
OAUTH2_ACCESS_TOKEN_EXPIRE_MINUTES=1440
OAUTH2_FRONTEND_ORIGIN=http://agentos.local:8090
```

2. 在`SkillHub`的`.env` 中填写：

```ini
MARKET_AGENTOS_OAUTH_ENABLED=true
MARKET_AGENTOS_OAUTH_CLIENT_ID=你的AgentOS客户端ID
MARKET_AGENTOS_OAUTH_CLIENT_SECRET=你的AgentOS客户端密钥
MARKET_AGENTOS_OAUTH_REDIRECT_URI=http://skillhub.local:9002/api/v1/auth/oauth/agentos/callback
MARKET_AGENTOS_OAUTH_AUTHORIZE_URL=http://agentos.local:8090/api/v1/oauth2/authorize
MARKET_AGENTOS_OAUTH_TOKEN_URL=http://agentos.local:8090/api/v1/oauth2/token
MARKET_AGENTOS_AUTH_USER_API_URL=http://agentos.local:8090/api/v1/oauth2/userinfo
MARKET_OAUTH_FRONTEND_ORIGIN=http://skillhub.local:9002
```

## 配置审核管理员

要完成“发布 → 人工审核 → 市场可见”的闭环，至少准备两个不同账号。将审核账号的 GitCode 或 GitHub 登录名写入：

```ini
MARKET_REVIEW_ADMIN_USERNAMES=reviewer_login
```

审核账号不能审核自己发布的 Skill。修改配置后须重启 marketplace，并重新登录；个人中心出现“待审核”菜单即表示权限生效。

## 验证与排查

1. 启动 marketplace 和 frontend。
2. 打开登录页，选择已启用的 OAuth 提供商。
3. 授权完成后确认能返回 SkillHub，并进入个人中心。

若登录失败，依次检查：

- OAuth 应用登记的回调地址与 `.env` 完全一致。
- `MARKET_OAUTH_FRONTEND_ORIGIN` 与浏览器实际访问地址一致。
- Client ID 和 Client Secret 未填反、未包含多余空格。
- marketplace 能访问 OAuth 提供商的换令牌和用户信息接口。

Client Secret 不得提交到仓库、日志或截图；一旦泄露，须立即在 OAuth 应用页面重置。网络放行地址见[通信矩阵](./通信矩阵.md)，其他错误见[故障排查](./故障排查.md#oauth-登录失败)。
