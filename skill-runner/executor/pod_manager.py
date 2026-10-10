# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Pod 生命周期抽象：create / wait_ready / delete，便于注入 fake 单测。"""
from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from ..config import settings

logger = logging.getLogger("skill_runner.pod_manager")


class PodManager(ABC):
    @abstractmethod
    async def create_pod(self, session_id: str, env: dict[str, str]) -> str:
        """创建 pod，返回 pod 名。不等待 Ready。"""

    @abstractmethod
    async def wait_ready(self, name: str, timeout: float) -> str:
        """轮询直到 pod Ready，返回 pod IP；超时抛 TimeoutError。"""

    @abstractmethod
    async def delete_pod(self, name: str) -> None:
        """删除 pod。"""

    async def reap_finished_pods(self) -> int:
        """删除 Failed/Succeeded 状态的孤儿 pod，返回回收数量。默认 no-op，由 K8s 实现覆盖。"""
        return 0


class K8sPodManager(PodManager):
    """每 session 一个裸 Pod，结束即删。kubernetes_asyncio 懒导入。"""

    def __init__(self) -> None:
        self._core = None
        self._api_client = None
        self._init_lock = asyncio.Lock()

    async def _api(self):
        if self._core is not None:
            return self._core
        async with self._init_lock:
            if self._core is not None:
                return self._core
            from kubernetes_asyncio import client, config

            try:
                config.load_incluster_config()
            except Exception:
                await config.load_kube_config()
            self._api_client = client.ApiClient()
            self._core = client.CoreV1Api(self._api_client)
        return self._core

    async def aclose(self) -> None:
        if self._api_client is not None:
            await self._api_client.close()
            self._api_client = None
            self._core = None

    def _pod_name(self, session_id: str) -> str:
        return f"skill-agent-{session_id}".lower().replace("_", "-")

    @staticmethod
    def _json_setting(raw: str, name: str):
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{name} must be valid JSON") from exc

    @staticmethod
    def _csv_setting(raw: str) -> list[str]:
        return [item.strip() for item in raw.split(",") if item.strip()]

    @staticmethod
    def _security_context() -> dict:
        if settings.pod_privileged:
            return {"privileged": True}
        sc: dict = {
            "runAsNonRoot": True,
            "runAsUser": settings.pod_run_as_user,
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
        }
        if settings.pod_seccomp_profile_type:
            sc["seccompProfile"] = {"type": settings.pod_seccomp_profile_type}
        return sc

    @staticmethod
    def _pod_security_context() -> dict:
        """Pod 级 securityContext。非特权时设 runAsNonRoot + seccomp，确保 PSS
        restricted 在各 k8s 版本下都通过。特权回退则不设。"""
        if settings.pod_privileged:
            return {}
        psc: dict = {"runAsNonRoot": True, "runAsUser": settings.pod_run_as_user}
        if settings.pod_seccomp_profile_type:
            psc["seccompProfile"] = {"type": settings.pod_seccomp_profile_type}
        return psc

    def _pod_manifest(self, name: str, env: dict[str, str]) -> dict:
        container: dict = {
            "name": "worker",
            "image": settings.k8s_pod_image,
            "imagePullPolicy": settings.k8s_image_pull_policy,
            "ports": [{"containerPort": settings.pod_port}],
            "env": [
                {"name": k, "value": v}
                for k, v in env.items()
                if k != "POD_IP"
            ]
            + [
                {
                    "name": "POD_IP",
                    "valueFrom": {"fieldRef": {"fieldPath": "status.podIP"}},
                }
            ],
            "readinessProbe": {
                "httpGet": {"path": "/healthz", "port": settings.pod_port},
                "initialDelaySeconds": 1,
                "periodSeconds": 15,
                "timeoutSeconds": 5,
                "failureThreshold": 10,
            },
            "securityContext": self._security_context(),
        }
        resources = self._json_setting(settings.k8s_resources, "SKILL_RUNNER_K8S_RESOURCES")
        if resources is not None:
            container["resources"] = resources

        spec: dict = {
            "restartPolicy": "Never",
            # 墙钟硬上限用"会话最大寿命"，与空闲超时解耦。
            # 注意：从 pod 创建起计，warm pod 预热也占用此预算，故设得比空闲超时宽。
            "activeDeadlineSeconds": settings.session_max_lifetime_seconds,
            # ttlSecondsAfterFinished 只对 Job 生效，裸 Pod 由 pod reaper 定期清理
            "containers": [container],
        }
        pod_sc = self._pod_security_context()
        if pod_sc:
            spec["securityContext"] = pod_sc
        if settings.k8s_runtime_class:
            spec["runtimeClassName"] = settings.k8s_runtime_class
        if settings.k8s_service_account_name:
            spec["serviceAccountName"] = settings.k8s_service_account_name
        else:
            spec["automountServiceAccountToken"] = False
        pull_secrets = self._csv_setting(settings.k8s_image_pull_secrets)
        if pull_secrets:
            spec["imagePullSecrets"] = [{"name": secret_name} for secret_name in pull_secrets]
        node_selector = self._json_setting(settings.k8s_node_selector, "SKILL_RUNNER_K8S_NODE_SELECTOR")
        if node_selector is not None:
            spec["nodeSelector"] = node_selector
        tolerations = self._json_setting(settings.k8s_tolerations, "SKILL_RUNNER_K8S_TOLERATIONS")
        if tolerations is not None:
            spec["tolerations"] = tolerations
        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": name,
                "labels": {"app": "skill-agent-worker", "managed-by": "skill-runner"},
            },
            "spec": spec,
        }

    async def create_pod(self, session_id: str, env: dict[str, str]) -> str:
        api = await self._api()
        name = self._pod_name(session_id)
        await api.create_namespaced_pod(
            namespace=settings.k8s_namespace,
            body=self._pod_manifest(name, env),
        )
        return name

    async def wait_ready(self, name: str, timeout: float) -> str:
        api = await self._api()
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            pod = await api.read_namespaced_pod(name=name, namespace=settings.k8s_namespace)
            phase = getattr(pod.status, "phase", None)
            pod_ip = getattr(pod.status, "pod_ip", None)
            conditions = getattr(pod.status, "conditions", None) or []
            ready = any(
                getattr(c, "type", None) == "Ready" and getattr(c, "status", None) == "True"
                for c in conditions
            )
            if phase == "Running" and ready and pod_ip:
                return pod_ip
            if phase in ("Failed", "Succeeded"):
                raise RuntimeError(f"pod {name} terminated early: phase={phase}")
            await asyncio.sleep(1.0)
        raise TimeoutError(f"pod {name} not ready within {timeout}s")

    async def delete_pod(self, name: str) -> None:
        api = await self._api()
        try:
            await api.delete_namespaced_pod(
                name=name, namespace=settings.k8s_namespace, grace_period_seconds=0
            )
        except Exception as exc:
            msg = str(exc)
            if "404" in msg or "Not Found" in msg:
                logger.debug("delete_pod: pod %s already gone (404)", name)
            else:
                logger.warning("delete_pod: failed to delete pod %s: %s", name, exc)

    async def reap_finished_pods(self) -> int:
        # 复用懒建的 _api_client，避免每次都新建连接泄漏
        api = await self._api()
        pods = await api.list_namespaced_pod(
            namespace=settings.k8s_namespace,
            label_selector="managed-by=skill-runner",
        )
        # Running 孤儿兜底阈值：合法会话不可能活过自身最大寿命，超此年龄仍 Running
        # 的 pod 必是孤儿（删除失败残留 / activeDeadline 未被强制）——安全删除。
        orphan_age = settings.session_max_lifetime_seconds + settings.pod_orphan_grace_seconds
        now = datetime.now(timezone.utc)
        reaped = 0
        for pod in pods.items:
            phase = getattr(pod.status, "phase", None)
            name = pod.metadata.name
            reason = ""
            if phase in ("Failed", "Succeeded"):
                reason = f"phase={phase}"
            elif phase == "Running":
                created = getattr(pod.metadata, "creation_timestamp", None)
                if created is not None and (now - created).total_seconds() > orphan_age:
                    reason = f"orphan (running >{orphan_age}s)"
            if not reason:
                continue
            try:
                await api.delete_namespaced_pod(name=name, namespace=settings.k8s_namespace)
                logger.info("pod reaper: deleted %s (%s)", name, reason)
                reaped += 1
            except Exception:
                logger.warning("pod reaper: failed to delete %s", name)
        return reaped
