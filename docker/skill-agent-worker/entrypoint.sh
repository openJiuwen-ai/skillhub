#!/bin/sh
set -e
# Worker 仅监听 Kubernetes Downward API 注入的 Pod IP，避免绑定所有网络接口。
exec uvicorn skill_runner.worker.app:app --host "${POD_IP:?POD_IP is required}" --port "${SKILL_RUNNER_POD_PORT:-8080}"
