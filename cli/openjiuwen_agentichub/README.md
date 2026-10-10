# agentichub（Agentic Hub 命令行工具）

面向 openJiuwen Agentic Hub 的命令行工具：在本地生成与校验插件、Skill、SwarmSkill 工程，打包上传、检索与安装。**PyPI 发行名为 `openjiuwen-agentichub`，安装后的命令为 `agentichub`**（`pip install openjiuwen-agentichub`）。本仓库内 Python 包目录名为 **`openjiuwen_agentichub/`**。

> **分发说明**：CLI **尚未发布到 PyPI**。请从本仓库 **`skillhub/cli/openjiuwen_agentichub/`** 目录以可编辑方式安装（见下文）。后续发布 PyPI 后，可直接 `pip install openjiuwen-agentichub` 使用。

---

## 1. 环境要求

| 项目 | 说明 |
|------|------|
| Python | **≥ 3.11.4**（与 `pyproject.toml` 中 `requires-python` 一致） |
| 操作系统 | Windows / Linux / macOS 均可 |
| 对接市场 | 可选。使用市场相关子命令时需已知**市场根 URL**（见第 5 节；参数见第 6 节） |

---

## 2. 安装

在克隆后的仓库中进入 **`skillhub/cli/openjiuwen_agentichub`** 目录执行：

```bash
cd skillhub/cli/openjiuwen_agentichub
pip install -e .
```

- **`-e`（可编辑）**：修改源码后立即生效，适合开发与联调。
- 安装成功后，终端中应可使用命令 **`agentichub`**。建议执行 **`agentichub -h`** 确认子命令列表。

若安装成功但提示 **`agentichub` 不是内部或外部命令**（常见于 Windows 用户级安装路径未加入 PATH），可使用模块方式调用（效果相同；模块路径仍为包名 `openjiuwen_agentichub`）：

```bash
cd skillhub/cli/openjiuwen_agentichub
python -m openjiuwen_agentichub.main --help
```

---

## 3. 快速上手（不依赖市场）

以下命令仅需本地文件系统，**无需**访问市场服务。

```bash
# 默认生成 Skill
agentichub init my-skill --path .

# 六类资产
agentichub init my-swarm --path . --type swarmskill
agentichub init my-plugin --path . --type agent-plugin
agentichub init my-connector --path . --type agent-mcp
agentichub init my-expert --path . --type agent-template
agentichub init my-group --path . --type agent-group

agentichub validate ./my-plugin
agentichub pack ./my-plugin
agentichub pack ./my-plugin -o ./dist-zips
```

`--type` 取 `skill`（默认）、`swarmskill`、`agent-plugin`、`agent-mcp`、`agent-template`、`agent-group`。Skill / SwarmSkill 以 `SKILL.md` 为入口；后四类生成外层 `plugin.yaml` 和内层 `manifest.json`。

---

## 4. 环境变量

仅指**当前进程**可见的环境变量（`os.environ`；不含仓库内 `.env` 自动加载）。CLI **不**读取任意路径下的 `.env`；与 Compose / 市场仓库里的 `.env` **无自动联动**。若用 `.env` 启了市场，运行 CLI 的终端仍需**自行** `export` / `$env:` 下表变量（或写入用户级环境变量）。

| 变量 | 作用 |
|------|------|
| `OPENJIUWEN_MARKET_URL` | 市场服务**根地址**（不含 `/api/v1/...`），例如 `http://127.0.0.1:8100` |
| `OPENJIUWEN_USER_TOKEN` | 用户 Bearer Token；用于 `publish` / `delete`（与 `--token` 二选一优先级：命令行优先） |
| `OPENJIUWEN_SYSTEM_TOKEN` | 系统管理员 Token（请求头 `X-System-Token`）；与 `--system-token` 同理 |
| `LOG_LEVEL` | 根日志级别，默认 `INFO` |

**安全提示**：勿将 Token 写入仓库。`search`、`info`、`install`（下载 zip）对市场侧为**公开接口**，CLI **不会**附带用户鉴权头。

**配置示例（当前 PowerShell 会话）**：

```powershell
$env:OPENJIUWEN_MARKET_URL = "http://127.0.0.1:8100"
$env:OPENJIUWEN_USER_TOKEN = "<你的 Token>"
```

**配置示例（Linux / macOS）**：

```bash
export OPENJIUWEN_MARKET_URL=http://127.0.0.1:8100
export OPENJIUWEN_USER_TOKEN="<你的 Token>"
```

---

## 5. 对接市场服务（简要）

将 **`OPENJIUWEN_MARKET_URL`** 设为插件市场**根地址**（不含 `/api/v1/...`）；或在各子命令上使用 **`--market-url`**（**命令行优先于环境变量**）。未设置且命令也未带 `--market-url` 时，依赖市场的子命令会报错退出。

**版本号约定**：发布参数 **`--plugin-version`** 必须为 **`x.y.z`** 三位非负整数段（如 `1.0.0`），**不支持** `1.0.0-rc1` 等预发布后缀。允许前缀 **`v`/`V`**，规范化后上传。`skill` 默认可不在 `SKILL.md` 写 `version`。

**帮助**：`agentichub -h` 列出子命令；`agentichub <子命令> -h` 与下表一致，以程序输出为准。

**各子命令的全部参数**见第 **6** 节。

---

## 6. 子命令与参数

| 子命令 | 简述 |
|--------|------|
| `init` | 生成插件脚手架 |
| `validate` | 校验插件目录 |
| `pack` | 将**单个**插件目录打成 zip |
| `publish` | 上传**单个**插件（zip 或先 pack 再传） |
| `info` | 查询插件版本详情 |
| `search` | 列表 / 搜索 |
| `delete` | 删除版本或整包 |
| `install` | 下载 zip 并安装到本地 |
| `skill-import` | **系统管理员**：批量导入**技能集合包**（`.zip` 或符合布局的**目录**）|

### 6.1 `init` — 生成脚手架

| 参数 | 必填 | 说明 |
|------|------|------|
| `name` | 是 | 插件名，如 `weather-plugin`（须满足 `^[a-z][a-z0-9-]*$`；`skill` 另有 Agent Skills 风格限制） |
| `--path` | 否 | 在哪个**父目录**下创建 `name/` 子目录，默认当前目录 `.` |
| `--type` | 否 | `skill`（默认）\| `swarmskill` \| `agent-plugin` \| `agent-mcp` \| `agent-template` \| `agent-group` |
| `--force` | 否 | 目标 `name` 目录已存在且非空时仍覆盖初始化 |

### 6.2 `validate` — 校验插件目录

| 参数 | 必填 | 说明 |
|------|------|------|
| `path` | 是 | 资产根目录。Skill / SwarmSkill 看 `SKILL.md`；插件、连接器、专家、专家团看外层 `plugin.yaml` 与内层 `<name>/manifest.json` |

### 6.3 `pack` — 打包为 zip

| 参数 | 必填 | 说明 |
|------|------|------|
| `path` | 是 | 插件根目录 |
| `-o` / `--output` | 否 | zip 输出目录，默认 `out`。若为**相对路径**，则相对于**插件根目录** `path` 解析；**绝对路径**则直接使用 |

打包前会先执行与 `validate` 相同的校验。Skill / SwarmSkill 打入技能目录；插件、连接器、专家、专家团打入外层 `plugin.yaml` 和内层目录。

### 6.4 `publish` — 上传市场

| 参数 | 必填 | 说明 |
|------|------|------|
| `path` | 条件 | 插件根目录；**与 `--file` 二选一**：未使用 `--file` 时**必填**（先 `pack` 再上传） |
| `-f` / `--file` | 条件 | 已有 zip 路径；指定则**跳过 pack**，直接上传该文件 |
| `--token` | 条件 | 普通用户：`Authorization: Bearer …`。与 `--system-token` **互斥**；可省略并从 `OPENJIUWEN_USER_TOKEN` 读取（命令行优先） |
| `--system-token` | 条件 | 系统管理员：`X-System-Token`。与 `--token` **互斥**；可省略并从 `OPENJIUWEN_SYSTEM_TOKEN` 读取 |
| `--market-url` | 条件 | 市场根 URL；未设则用 `OPENJIUWEN_MARKET_URL` |
| `--plugin-id` | 条件 | **首发勿传**（由市场分配）；**同一插件再次发版必须传**（与包内 `name` 对应的那条资产的 `plugin_id`） |
| `--plugin-version` | 是 | 发布版本号（`x.y.z`，可选 `v` 前缀） |
| `--version-desc` | 否 | **本版本的 release notes**（语义上）；市场侧落库/展示为**该版本的 changelog** 字段（与 `plugin.yaml` 的 `description`、插件卡片/详情里的**插件总描述**不是同一字段） |
| `--force` | 否 | 允许覆盖市场侧已存在的同版本（以服务端策略为准） |

`publish`、`delete` 的用户或系统鉴权须通过 `--token` 或 `OPENJIUWEN_USER_TOKEN`（或 `--system-token` / `OPENJIUWEN_SYSTEM_TOKEN`）；未提供则报错退出。

**日志**：`LOG_LEVEL` 含 `INFO` 时，开始上传 multipart 前会打印「正在上传插件包，请稍候…」；大包耗时可更长。

### 6.5 `info` — 查询版本详情

不携带用户鉴权；需市场 URL。

| 参数 | 必填 | 说明 |
|------|------|------|
| `asset_id` | 是 | 资产 ID（与发布返回的 `plugin_id` 一致） |
| `-v` / `--version` | 是 | 目标版本，如 `1.0.0` |
| `--market-url` | 条件 | 市场根 URL；未设则用环境变量 |

### 6.6 `search` — 列表 / 搜索

不携带 Authorization。每条结果输出 `default_version`（安装/默认所用版本，可直接用于 `install -v`）、`version_count`（当前可见版本数）及 `versions`（逗号分隔，来自列表 API 的 `all_versions`；Git commit 为 7 位 hex；匿名用户通常仅含已通过审版本）。

| 参数 | 必填 | 说明 |
|------|------|------|
| `query` | 否 | 搜索关键词；可省略，表示空关键词 |
| `--market-url` | 条件 | 市场根 URL |
| `--type` | 否 | `plugin_type` 精确匹配：`skill` / `swarmskill` / `agent-plugin` / `agent-mcp` / `agent-template` / `agent-group` |
| `--author` | 否 | 发布者展示名（模糊） |
| `--asset-id` | 否 | 资产 ID |
| `--asset-type` | 否 | 资产类型（**精确匹配**；当前列表以 **`plugin`** 为主，其它取值以后端为准，如后续扩展 `workflow` 等） |
| `--publisher-id` | 否 | 发布者 ID |
| `--page` | 否 | 页码，默认 `1` |
| `--page-size` | 否 | 每页条数（默认 `20`，服务端上限 `200`） |
| `--order-by` | 否 | `install_count`（默认）\| `like_count` \| `create_time` \| `update_time` \| `review_count` |
| `--desc` | 否 | 是否降序：`true` / `false` / `yes` / `no` 等，默认 `true` |

### 6.7 `delete` — 删除版本或整包

| 参数 | 必填 | 说明 |
|------|------|------|
| `plugin_id` | 是 | 资产 ID |
| `--market-url` | 条件 | 市场根 URL |
| `--token` | 条件 | 普通用户 Bearer；与 `--system-token` **互斥**；可配合 `OPENJIUWEN_USER_TOKEN`（命令行优先） |
| `--system-token` | 条件 | 系统管理员 `X-System-Token`；与 `--token` **互斥**；可配合 `OPENJIUWEN_SYSTEM_TOKEN` |
| `--version` | 否 | 要删的版本号；**省略**则删除**全部**版本（与 CLI `--help` 描述一致；服务端行为以市场 API 为准） |

### 6.8 `install` — 下载并安装

不携带用户鉴权。具体行为取决于制品 zip 内 **`plugin.yaml` 的 `runtime.type`**，详见下表之后说明。

| 参数 | 必填 | 说明 |
|------|------|------|
| `asset_id` | 是 | 市场 `asset_id` |
| `--market-url` | 条件 | 市场根 URL |
| `--version` / `-v` | 否 | 语义化版本（如 `1.0.0`）；传给 **`GET /api/v1/artifacts/{asset_id}?version=`**；**省略**则由服务端决定（一般为最新版） |
| `-o` / `--output` | 否 | 插件包**父目录**（默认当前工作目录） |
| `--force` | 否 | 目标目录已存在时允许覆盖 |

**说明**

- **父目录**：`-o` 指向的目录；省略则为当前工作目录。
- **包目录**：`<父目录>/<资产名>/`。Skill / SwarmSkill 落到技能目录；插件、连接器、专家、专家团解压带 `plugin.yaml` 的目录，不执行 `pip`。

- **`skill` / `swarmskill`**：安装结果统一为 **`<父目录>/<slug>/`**，其中 `slug` 优先取 `plugin.yaml name`（若存在），否则取 `SKILL.md` frontmatter `name`；**不**跑 `pip`。

### 6.9 `skill-import` — 管理员批量导入技能集合包

调用市场 **`POST /api/v1/plugins/skill-import`**：**仅**系统管理员（**`X-System-Token`**），**不支持**普通用户 Bearer。用于一次上传内含**多个顶层 skill 目录**的集合包（与单条 `publish` 上架的 skill zip **不是**同一格式）。

| 参数 | 必填 | 说明 |
|------|------|------|
| `BUNDLE` | 是 | **集合包 `.zip` 路径**，或**本地目录**（布局须与 zip 解压后一致：多个顶层 skill 子目录 + 可选根级 `manifest.json`）。为目录时 CLI 先在临时目录内打成 zip 再上传 |
| `--market-url` | 条件 | 市场根 URL；未设则用 `OPENJIUWEN_MARKET_URL` |
| `--system-token` | 条件 | **`X-System-Token`**；未设则用 `OPENJIUWEN_SYSTEM_TOKEN`（**必填其一**） |
| `--force` | 否 | 与表单 `force` 一致；可与 `manifest.json` 条目的 `force` 逻辑或 |
| `--fail-fast` | 否 | 首条条目失败即停止处理后续目录（默认部分成功） |

**退出码**：任一条目失败（`summary.failed > 0`）、路径无效、本地打包失败、或 zip 超过 **512MB** 预检时，进程以**非 0** 退出。HTTP **200** 仍可能带项内失败，请看日志中的 `summary` / 各 `entry` 状态。

**日志**：`LOG_LEVEL` 含 `INFO` 时，开始上传前会打印「正在上传 skills 包，请稍候…」；大包耗时可更长。

```bash
export OPENJIUWEN_MARKET_URL=http://127.0.0.1:8100
export OPENJIUWEN_SYSTEM_TOKEN="<系统管理员 Token>"

agentichub skill-import ./my-skills-bundle-dir
agentichub skill-import ./bundle.zip --fail-fast
```

### 6.10 市场相关命令示例

```bash
BASE=http://127.0.0.1:8100   # 占位；或已 export OPENJIUWEN_MARKET_URL

agentichub publish ./demo-weather --token <TOKEN> --market-url $BASE
agentichub publish -f ./out/demo-weather-0.0.1.zip --token <TOKEN> --market-url $BASE
agentichub info <asset_id> -v 1.0.0 --market-url $BASE
agentichub search weather --type agent-plugin --page-size 20 --order-by create_time --market-url $BASE
agentichub install <asset_id> --market-url $BASE
agentichub install <asset_id> -v 1.0.0 --market-url $BASE
agentichub install <asset_id> -o ./plugins --market-url $BASE
agentichub delete <asset_id> --token <TOKEN> --market-url $BASE
```

（Windows PowerShell 可将 `$BASE` 换为变量或字面 URL。）

---

## 7. 代码结构（维护与二次开发）

本发行物源码位于 **`skillhub/cli/openjiuwen_agentichub/`**；PyPI 发行名为 **`openjiuwen-agentichub`**，命令为 **`agentichub`**。共享实现在 **`skillhub/cli/cli_core/`**（随 wheel 一并打包）。

| 位置 | 职责 |
|------|------|
| `openjiuwen_agentichub/main.py` | 入口：日志初始化、解析参数、分发子命令 |
| `openjiuwen_agentichub/parsers.py` | 各子命令 `argparse` 定义 |
| `cli_core/handlers.py` | 子命令业务编排、日志输出、进程退出码 |
| `cli_core/plugin.py` | `init` / `validate` / `pack` / `publish` / `install` 落盘与 pip 等 |
| `cli_core/market.py` | 对市场 HTTP API 的调用与响应解析 |
| `cli_core/schemas/` | 与市场对齐的请求/响应模型（Pydantic） |
| `cli_core/logging_config.py` | 控制台日志与敏感信息脱敏 |

**HTTP 与重试**：`publish`（上传）与 `delete` 为**单次请求、不自动重试**，避免非幂等写操作在「服务端已成功、客户端未收到响应」时重复提交。`search`、`info`、制品元数据与 zip 下载等**只读**调用在 `cli_core/market.py` 内对网络错误及部分可恢复状态码做**有限次数退避重试**（细节见源码）。

约定：`cli_core/market.py` 只负责 HTTP 与契约对象；`cli_core/handlers.py` 负责面向用户的日志与返回码；扩展接口时优先改 **`cli_core/schemas`**，再改 **`market`** 与 **`handlers`**。

---

## 8. 脚手架类型与目录结构

`init --type` 支持六类 Agent 资产。默认 `skill`。

### `skill` / `swarmskill`

```text
my-skill/
  SKILL.md
  scripts/
  references/
  assets/
```

SwarmSkill 的 `SKILL.md` 含 `kind: swarm-skill` 和至少两个 `roles`。

### `agent-plugin` / `agent-mcp` / `agent-template` / `agent-group`

```text
my-plugin/
  plugin.yaml                 # runtime.type = 对应的 agent-* 类型
  README.md                   # 只留在本地，打包时不放进市场 zip
  my-plugin/
    manifest.json             # package_type = plugin | mcp | agent_template | agent_group
    README.md
```

市场上的外层只接受 `plugin.yaml`、可选 `icon.png`，以及与 `plugin.yaml.name` 同名的唯一内层目录。

插件脚手架带 `tools/example.py`。连接器带 `mcp.json`，内容是 `mcpServers` 对象，首个 server 的 `url` 对应 `integration.type=remote-mcp`。专家带 `persona/persona.md`。专家团的 `manifest.agents` 为 `leader` 与 `analyst`。

---

## 9. `plugin.yaml` 与校验摘要（MVP）

说明：Skill / SwarmSkill 可以只有 `SKILL.md`。插件、连接器、专家、专家团使用外层 `plugin.yaml` 加内层 `manifest.json`，两边的名字和版本必须一致。

| 字段 / 规则 | 说明 |
|-------------|------|
| `name` | 正则 `^[a-z][a-z0-9-]*$` |
| `version` | 与市场约定一致：**仅** `x.y.z` 三位数字段（如 `1.0.0`），无预发布/构建后缀 |
| `display_name`、`description` | 非空字符串 |
| `runtime.type` | `skill` \| `swarmskill` \| `agent-plugin` \| `agent-mcp` \| `agent-template` \| `agent-group` |
| `metadata.author`、`metadata.tags` | 必填；`tags` 为非空字符串数组 |
| `skill` / `swarmskill` | 入口是根目录或唯一子目录里的 `SKILL.md`。`name` 与目录名一致，`description` 非空。SwarmSkill 还要 `kind: swarm-skill` 和至少两个不重复的 `roles[].id` |
| 插件 / 连接器 / 专家 / 专家团 | 内层 `manifest.package_type` 分别为 `plugin`、`mcp`、`agent_template`、`agent_group`。`manifest.version` 与 `plugin.yaml.version` 相同。插件和连接器用 `manifest.id`，专家和专家团用 `manifest.name`，都要等于 `plugin.yaml.name` |
| 插件 | 根 manifest 不能写 `persona`、`agent_card`、`model`、`subagents`、`memories`、`rubrics` |
| 连接器 | `integration.type` 为 `stdio-mcp`、`remote-mcp`、`cli` 或 `skill-only`。除 `skill-only` 外，`integration.file` 指向的文件必须在内层目录里。`stdio-mcp` / `remote-mcp` 的 `mcp.json` 必须有非空 `mcpServers`，首个 server 的 `command` 或 `url` 要和 `integration.type` 一致 |

---

## 10. 开发与测试

在 **`skillhub/cli/openjiuwen_agentichub`** 目录下：

```bash
pip install -e .
pip install pytest
python -m pytest -q
```

---

## 11. 常见问题

**Q：`agentichub` 命令找不到？**  
A：使用 `python -m openjiuwen_agentichub.main`，或检查 Python 的 `Scripts` 目录是否已加入 PATH。

**Q：提示连不上市场？**  
A：确认 `OPENJIUWEN_MARKET_URL` 为**根地址**（不要带 `/api/v1`），且该 URL 在本机或网络内可访问。

**Q：发布失败提示版本格式错误？**  
A：使用 `x.y.z` 三位数字版本；不要使用 `1.0.0-rc1` 等形式。

**Q：路径含空格报错？**  
A：在 shell 中为路径加引号，例如 `agentichub validate "D:\My Plugins\demo"`。

**Q：只执行 `agentichub` 不带子命令会怎样？**  
A：会打印总帮助并退出（退出码非 0）；查看子命令请用 `agentichub -h` 或 `agentichub <子命令> -h`。

**Q：`search` 里关键词含 `*`、`#`、`(` 等异常？**  
A：多数由 **Shell 先解释** 导致：`*` 会展开为文件名、`#` 起注释、`(` 在 bash 中有语法含义。请对关键词**加引号**，例如 `agentichub search '*'`、`agentichub search '#'`（PowerShell 同理用引号包裹）。

**Q：`publish` 失败时打印一大段 JSON？**  
A：已改为尽量解析服务端 `message` 并输出**单行可读文案**（如 `Invalid X-System-Token`、版本冲突说明等）。若仍为长文本，请直接查看市场接口返回或本地复现请求排查。
