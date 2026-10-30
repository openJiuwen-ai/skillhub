# Publish an asset

openJiuwen Agentic Hub accepts six kinds of assets: Skill, Swarm Skill, plugin, connector, expert, and expert team. The **+ Publish** button and `POST /api/v1/plugins` go through the same review. Until a version is approved, the market keeps showing the last approved version.

## Publish on the website

1. Sign in, then click **+ Publish** at the top right.
2. Choose a type. For a Skill or Swarm Skill, select a local folder. For a plugin, connector, expert, or expert team, select a zip. The page detects the package type and blocks submit when it does not match.
3. For a first publish, choose **New Skill** or the matching **New …** item. To publish another version of an existing asset, pick that asset in the related dropdown. The package name and display name stay locked.
4. Enter a version of three numeric parts, such as `1.0.0`, with no `v` prefix. If that version already exists, check **Force overwrite** before submitting.
5. Choose visibility. Public assets appear on the market after approval. Private assets are available only to the author, admins, and groups that were granted access. A new version keeps the existing visibility.
6. Click **Upload and publish**. The browser packs the files and computes the SHA-256 checksum. You do not type it.
7. Open your profile to see the status.

| Status | Meaning |
|--------|---------|
| In review | Rule or model checks are running |
| Pending approval | Waiting for a reviewer |
| Published | Approved. A public asset shows on its tab |
| Publish failed | Read the review detail or rejection reason, then publish a new version |

A Skill folder must contain `SKILL.md` at its root. Icons must be PNG and no larger than 5MB. The publish drawer can download a template. If this site has no template, prepare the folder as described below.

## Upload through the API

The website submits this same endpoint. Call it with the Bearer token from sign-in.

```http
POST /api/v1/plugins
Authorization: Bearer <token>
X-Checksum-SHA256: <SHA-256 of the zip, 64 lowercase hex characters>
Content-Type: multipart/form-data
```

| Field | Description |
|-------|-------------|
| `file` | Zip, required. A Skill or Swarm Skill must include `SKILL.md`. The other four types may be a raw package with `manifest.json`, or a market package that already has `plugin.yaml` |
| `plugin_version` | For example `1.0.0`. If omitted, the version inside the package is used |
| `plugin_id` | Set to `asset_id` when publishing a new version of an existing asset. Omit it for the first publish |
| `asset_name` | Package name. A rename must match the directory inside the package |
| `display_name` | Display name. The form value wins |
| `description` | Summary. If a Skill leaves this empty, the `description` in `SKILL.md` is used |
| `tags` | Tags separated by commas |
| `visibility` | `public` (default) or `private` |
| `force` | `true` overwrites the same version |
| `version_desc` | Optional version notes |

```bash
curl -X POST "https://agentichub.openjiuwen.com/api/v1/plugins" \
  -H "Authorization: Bearer <token>" \
  -H "X-Checksum-SHA256: $(sha256sum skill.zip | awk '{print $1}')" \
  -F "file=@skill.zip" \
  -F "plugin_version=1.0.0" \
  -F "display_name=Demo Skill"
```

On Windows PowerShell, compute the checksum first and put it in the header:

```powershell
(Get-FileHash .\skill.zip -Algorithm SHA256).Hash.ToLower()
```

For a self-hosted instance, replace the host with your market address. On success, `publish_result` is `reviewing`, then `pending_moderation`, and later `publish_success` or `publish_failed`.

| Failure | What to do |
|---------|------------|
| Checksum missing or different from the file | Recompute the zip SHA-256 and send it in `X-Checksum-SHA256` |
| Bad version | Use three numeric parts such as `1.0.0`, or a 7-character lowercase git commit. Do not add `v` |
| Same version already exists | Use a higher version, or send `force=true` |
| Type mismatch | Align the selected type with `kind` or `package_type` inside the package |
| Zip cannot be opened | Upload a real zip. Empty files, damaged archives, and folders renamed to `.zip` are rejected |
| Wrapped package version mismatch | `plugin_version` must equal `version` in `manifest.json` |

## Prepare the six package types

### Skill

On the website, select a folder, not a zip. The directory name must match `name` in `SKILL.md`.

```text
my-demo-skill/
└── SKILL.md
```

```markdown
---
name: my-demo-skill
description: One sentence on what it does
---
```

The package name starts with a lowercase letter and contains only lowercase letters, digits, and hyphens, up to 64 characters. The display name is 1 to 128 characters.

### Swarm Skill

Choose Swarm Skill. Still upload a directory with `SKILL.md`. The frontmatter must set `kind: swarm-skill` and at least two roles. Role `id` values must be unique.

```markdown
---
name: my-swarm-skill
description: Research and review with multiple roles
kind: swarm-skill
roles:
  - id: researcher
    description: Collect and analyze information
  - id: reviewer
    description: Review and check the result
---
```

A publish is rejected when `kind` is missing, there are fewer than two roles, an `id` is duplicated, `name` does not match the directory, or the YAML indentation is wrong.

### Plugin, connector, expert, and expert team

Upload a zip. `package_type` in `manifest.json` must match the type you selected.

| Type on the page | `package_type` | Shown on |
|------------------|----------------|----------|
| Plugin | `plugin` | Plugins |
| Connector | `mcp` | Connectors |
| Expert | `agent_template` | Experts |
| Expert team | `agent_group` | Expert teams |

A connector also needs a file for how it connects: local MCP uses `mcp.json` with `command`; remote MCP uses `mcp.json` with `url`; a command-line connector uses `cli.json`. An old package that has only `mcp.json` and no `manifest.json` cannot be published. Use a PNG icon and set it in `manifest.icon`.

If an expert is detected as a plugin, set `package_type` to `agent_template`. If an expert team is detected as an expert, set it to `agent_group`.

## Generate locally, then upload

When the `agentichub` command is already installed, generate and check the directory locally, then upload it on the website or through the API. The command calls the same `POST /api/v1/plugins`.

```bash
agentichub init my-demo-skill --type skill
agentichub validate my-demo-skill
agentichub pack my-demo-skill --output out
agentichub publish my-demo-skill \
  --plugin-version 1.0.0 \
  --market-url https://agentichub.openjiuwen.com \
  --token <token>
```

`--type` may also be `swarmskill`, `agent-plugin`, `agent-mcp`, `agent-template`, or `agent-group`. For an existing zip, use `agentichub publish --file out/my-demo-skill.zip`.
