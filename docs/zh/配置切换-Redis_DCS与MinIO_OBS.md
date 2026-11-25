# Redis / DCS 与 MinIO / OBS 配置切换说明

openJiuwen Agentic Hub marketplace（含推荐链路）通过 **环境变量** 在本地组件与华为云托管服务之间切换。协议兼容，无需改业务代码。

## 1. 缓存：Redis ↔ DCS

DCS（Redis 版）协议与 Redis 相同，改连接参数即可。

| 变量 | 本地 Redis | 华为云 DCS |
|------|------------|------------|
| `CACHE_BACKEND` | `redis`（默认） | `dcs` |
| `REDIS_HOST` / `MARKET_REDIS_HOST` | `127.0.0.1` | DCS 内网地址 |
| `REDIS_PORT` | `6379` | 控制台端口（常为 6379） |
| `REDIS_USERNAME` / `MARKET_REDIS_USERNAME` | 可空（不传，即 default） | ACL 用户名；禁止 default 时必填 |
| `MARKET_REDIS_PASSWORD` | 可空 | DCS 密码 |
| `MARKET_REDIS_SSL` / `REDIS_SSL` | 一般不设（false） | **可不设**：`CACHE_BACKEND=dcs` 时默认 **true** |

示例（测试环境 DCS）：

```env
CACHE_BACKEND=dcs
REDIS_HOST=192.168.x.x
REDIS_PORT=6379
MARKET_REDIS_PASSWORD=********
# 若实例未开 SSL，再显式关掉：
# MARKET_REDIS_SSL=false
```

影响范围：OAuth session、通用 cache、Playground 多实例状态、推荐 Redis 快照读写。

### 多应用共享 Redis：应用命名空间

在 marketplace 根目录 `.env` 或部署环境变量中设置：

```env
REDIS_KEY_PREFIX=skillhub:prod
```

未配置、空值或仅有空白时保持原有键名。非空值去掉首尾空白后，若末尾没有英文冒号则自动补齐；
例如 `skillhub:prod` 和 `skillhub:prod:` 均生效为 `skillhub:prod:`。已经以冒号结尾时不再添加。

该前缀覆盖 marketplace 的缓存、OAuth、下载去重、标星记录、Playground 状态及锁、
推荐快照及用户序列、推荐建库锁及共享状态、检索重建锁、热加载 Stream 和离线 Embedding 计数。
频道名称使用同一命名规则；当前检索通知采用 Stream，没有 Pub/Sub 频道调用。
值、Hash 字段、Set 成员、Stream 消息字段、消费者组和消费者名称不加前缀。

原有 `REDIS_USER_SEQ_KEY_PREFIX` 仍是推荐业务前缀：上述配置配合其默认值 `skill_rec:user`，
用户下载序列最终为 `skillhub:prod:skill_rec:user:<uid>:download`。

所有 marketplace 副本及独立推荐同步、建库任务必须使用同一个 `REDIS_KEY_PREFIX`；
独立 CLI 任务需显式注入该环境变量，不能假定自动加载 `.env`。K8s 使用 marketplace ConfigMap，
Docker Compose 通过现有 `env_file` 注入；更新后重启服务和相关任务。

启用或修改前缀不会迁移或回读旧命名空间。缓存可以回源重建，推荐快照需要重新同步；
现有 OAuth、在线试用会话及长期标星展示状态需要安排切换或迁移。不得清空共享 Redis 数据库。
前缀提供命名隔离；权限隔离需配合 Redis ACL，内存和连接等资源仍由各应用共享。
离线 Embedding 计数随命名空间隔离；共用模型账号的跨应用总配额需另行统一控制。
本配置仅作用于 marketplace，不影响独立 skill-runner。

## 2. 对象存储：MinIO ↔ OBS

主站已支持 `STORAGE_TYPE`；推荐离线拉包与主站共用同一套 `MARKET_S3_*`。

| 变量 | 本地 MinIO | 华为云 OBS |
|------|------------|------------|
| `STORAGE_TYPE` | `MinIO`（默认） | `OBS` |
| `MARKET_S3_ENDPOINT` | `http://localhost:9000` | OBS endpoint（如 `https://obs.cn-north-4.myhuaweicloud.com`） |
| `MARKET_BUCKET_NAME` | 桶名 | 桶名 |
| `MARKET_S3_ACCESS_KEY` / `SECRET` | MinIO 密钥 | OBS 永久 AK/SK（static）或留空走 dynamic |
| `MARKET_CREDENTIALS_MODE` | 默认 `static` | 默认 `dynamic`（IAM）；可设 `static` |
| `MARKET_S3_REGION` | 可空 | 建议填写区域 |
| `MARKET_S3_USE_SSL` | 可空（随 endpoint） | 默认推断 **true** |
| `MARKET_S3_SSL_VERIFY` | 默认 **true** | 自建 OBS 证书不可信时设 **false**（仍走 https，boto3 `verify=False`） |
| addressing | 默认 `path` | 默认 `virtual`（可用 `MARKET_S3_ADDRESSING_STYLE` 覆盖） |

示例（本地）：

```env
STORAGE_TYPE=MinIO
MARKET_S3_ENDPOINT=http://localhost:9000
MARKET_BUCKET_NAME=openjiuwen-market-test
MARKET_S3_ACCESS_KEY=minioadmin
MARKET_S3_SECRET_KEY=minioadmin
```

示例（OBS static）：

```env
STORAGE_TYPE=OBS
MARKET_S3_ENDPOINT=https://obs.cn-north-4.myhuaweicloud.com
MARKET_BUCKET_NAME=your-bucket
MARKET_CREDENTIALS_MODE=static
MARKET_S3_ACCESS_KEY=********
MARKET_S3_SECRET_KEY=********
MARKET_S3_REGION=cn-north-4
```

示例（OBS dynamic / IAM）：

```env
STORAGE_TYPE=OBS
MARKET_S3_ENDPOINT=https://obs.cn-north-4.myhuaweicloud.com
MARKET_BUCKET_NAME=your-bucket
# MARKET_CREDENTIALS_MODE 可不写（OBS 默认 dynamic）
HUAWEICLOUD_SDK_AK=
HUAWEICLOUD_SDK_SK=
HUAWEICLOUD_SDK_PROJECT_ID=
HUAWEICLOUD_SDK_REGION=cn-north-4
```

## 3. 切换检查清单

1. 改 `.env` 后 **重启** marketplace / 推荐任务进程  
2. Redis/DCS：`PING` 成功（看启动日志 `Cache: Redis OK` / `ssl=True`）  
3. MinIO/OBS：上传或列表接口能访问桶；推荐 `package_sync` 能下到 zip  
4. DCS / OBS 务必走 **内网** 地址，安全组放行业务机  

## 4. 相关代码

- 存储类型解析：`plugins_market/core/s3_storage_client.py`（`STORAGE_TYPE`）  
- 缓存后端 / SSL：`plugins_market/core/redis_client.py`（`CACHE_BACKEND`）  
- 推荐侧读取同一套 env：`recommender/shared/config.py`
