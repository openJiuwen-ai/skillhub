# SkillHub OpenShift 手工试部署

本目录提供与 `docker/k8s/` 并存的 OpenShift 交付物。`dockerfiles/` 平铺提供 Backend、Frontend、Runner、Worker 四个组件的镜像配方。当前 OCP 手工试部署只导入 Backend 和 Frontend 的清单，验证浏览、关键词检索、登录、发布、人工审核；Runner/Worker 在线试用的部署清单与实际集群验收留待下一阶段。现有 Dockerfile、K8s 清单及业务代码不参与本次改动。

| 路径 | 用途 |
| --- | --- |
| `dockerfiles/Dockerfile.backend` | UBI 9 Python 3.11 Backend 镜像；运行镜像保留完整 `dnf` |
| `dockerfiles/Dockerfile.frontend` | Node 22 构建、Nginx 1.27 Alpine 运行 |
| `dockerfiles/Dockerfile.runner` | UBI 9 Python 3.11 Runner 控制面镜像；后续在线试用部署使用 |
| `dockerfiles/Dockerfile.worker` | UBI 9 Python 3.12 Worker 镜像；后续由 Runner 动态创建的 Pod 使用 |
| `nginx/` | 新前端镜像专用的非 root Nginx 配置与入口脚本 |
| `manifests/marketplace-*.yaml` | Marketplace（Backend）的 Secret、ConfigMap、Deployment、内部 Service；ConfigMap/Secret 按功能列出可选配置，只有取消注释后才生效 |
| `manifests/frontend-*.yaml` | Frontend 的 Deployment、内部 Service、HTTPS Route |
| `DEPLOYMENT.md` | 从构建、推送镜像到 OCP 页面安装、验收和回退的完整基础市场指导 |

按 [部署指导](DEPLOYMENT.md) 从构建执行到验收。设计背景见 [OPENSHIFT.md](../OPENSHIFT.md)。

本轮采用单 Backend 副本；数据库和对象存储由用户预先提供。`app.log`、`framework.log`、`access.log` 和 `interface.log` 写到 Marketplace 容器可写层，容器重新创建后丢失；已有应用/框架控制台日志和前端 Nginx 日志可由集群的容器日志链路采集。是否进入集中检索系统取决于 OCP 集群是否安装并配置日志采集。
