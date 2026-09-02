# 推荐系统 API

对外仅暴露 **个性化推荐** 接口。

- 路径：`POST /api/v1/recommend`
- 开关：需 `MARKET_RECOMMENDER_ENABLED=true`，否则 `503`
- 鉴权：**可选**（与 `GET /plugins` 相同，走 `resolve_viewer_context`）
  - 有效 `Authorization: Bearer <OAuth access token>`（可选 `X-OAuth-Provider: gitcode|github`）
  - 或有效 `X-System-Token: <SYSTEM_ADMIN_TOKEN>`（受信任服务代调）
  - 缺头、Bearer 无效/过期、System Token 无效、两种凭证同时传：视为匿名，走 Redis 下载量 TopK 兜底（`source=topk_install`），**不 401**

市场 Web 列表侧的「推荐精选」（`GET /api/v1/plugins?order_by=recommend`，不带 `category_id`）见 [TeamSkillsHub 接口参考](./TeamSkillsHub-接口参考.md)。列表与 POST 共用同一套召回引擎；POST 在召回后走与列表相同的市场过滤与卡片补全。POST 为可选鉴权。

---

## 用户身份如何生效

| 调用方 | `body.user_id` | 实际用于召回的用户 |
|--------|----------------|-------------------|
| 有效 Bearer | 省略，或必须等于 token 用户 | **始终**为 token 用户；不一致 → `403` |
| 有效 X-System-Token | 可传任意用户；空字符串 = 冷启动 | 以 body 为准（信任服务已认证该用户） |
| 匿名（无鉴权 / token 无效） | 忽略，即使传了也不采用 | 空字符串 → Redis TopK 冷启动 |

---

## 召回顺序

1. 生效 `user_id` 非空，且 Redis 有该用户 download / like / star 序列 → Milvus 相似召回 → MMR → `source=user_history`
2. 否则（空用户、无历史、召回失败）→ Redis 下载量快照兜底（条数不超过请求 `top_k`）→ `source=topk_install`

可选 `category_id` 时，Milvus / TopK 均按该类目过滤。

---

## `POST /api/v1/recommend`

### 请求头

| Header | 说明 |
|--------|------|
| `Content-Type` | `application/json` |
| `Authorization` | 可选。`Bearer <token>` 有效则个性化；无效则匿名冷启动 |
| `X-System-Token` | 可选。有效则可代任意 `user_id`；无效则匿名冷启动（勿与 Bearer 同时传） |
| `X-OAuth-Provider` | 可选，`gitcode`（默认）或 `github` |

### 请求体

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `user_id` | string | 否 | `""` | 见上表「用户身份如何生效」；`timestamp` 可不传 |
| `request_id` | string | 否 | `""` | 调用方请求 ID，响应原样回显（链路追踪用这个） |
| `timestamp` | number \| null | 否 | `null` | 仅写日志，不参与召回 |
| `top_k` | int | 否 | `10` | 过滤后最多返回条数，范围 1–500。服务端会适量超召再滤，可见条数可能少于 `top_k` |
| `category_id` | string | 否 | `""` | 根类目 ID；空=不限 |

### 响应 `data`

| 字段 | 类型 | 说明 |
|------|------|------|
| `request_id` | string | 回显 |
| `user_id` | string | **实际用于召回**的用户 ID |
| `source` | string | `user_history` / `topk_install` |
| `category_id` | string | 请求类目回显 |
| `items` | array | 过滤后的可见卡片。每条含召回 `score`，以及与 `GET /plugins` 列表项相同的字段（`name` / `display_name` / `short_desc` / `plugin_type` / `latest_version` / `tags` / `update_time` 等）。下架、未过审、不可见的 id 不会出现。顺序为召回序（置顶 `pin_order` 仍优先） |

### 错误

| HTTP | `error` / `error_code` | 说明 |
|------|------------------------|------|
| `403` | `recommend_user_mismatch` / `SKILLHUB_RECOMMEND_USER_MISMATCH` | 有效 Bearer 下 `body.user_id` 与登录用户不一致 |
| `422` | 校验失败 | 请求体校验失败 |
| `503` | `recommender_disabled` / `SKILLHUB_RECOMMENDER_DISABLED` | 推荐未启用 |
| `500` | `recommend_failed` / `SKILLHUB_RECOMMEND_FAILED` | 服务内部错误（详情仅服务端日志） |

---

## 调用示例

### Python（伙伴服务：System Token + 指定用户）

```python
import json
import urllib.request

BASE = "http://127.0.0.1:8100"
SYSTEM_TOKEN = "<SYSTEM_ADMIN_TOKEN>"  # 与服务端 .env 一致，勿提交到仓库


def recommend(*, user_id: str, top_k: int = 10, request_id: str = "", category_id: str = "") -> dict:
    body = {
        "user_id": user_id,
        "request_id": request_id,
        "top_k": top_k,
        "category_id": category_id,
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/api/v1/recommend",
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-System-Token": SYSTEM_TOKEN,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


print(json.dumps(
    recommend(user_id="<target-user-id>", request_id="demo-1", top_k=10),
    ensure_ascii=False,
    indent=2,
))
```

### Python（终端用户：Bearer，可不传 user_id）

```python
req = urllib.request.Request(
    f"{BASE}/api/v1/recommend",
    data=json.dumps({"request_id": "demo-2", "top_k": 10}).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer <oauth-access-token>",
    },
    method="POST",
)
```

### curl（System Token）

```bash
curl -sS -X POST "http://127.0.0.1:8100/api/v1/recommend" \
  -H "Content-Type: application/json" \
  -H "X-System-Token: <SYSTEM_ADMIN_TOKEN>" \
  -d '{"user_id":"<target-user-id>","request_id":"demo-1","top_k":10}'
```

---

## 相关文档

- [运维指南 / 推荐系统](../6.%20运维指南/可选能力/推荐系统/README.md)
- [开发指南 / 推荐系统](../5.%20开发指南/推荐系统/README.md)
