# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import os
import hashlib
import threading
import time
from datetime import datetime
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse

import boto3
from botocore.config import Config
from common.huaweicloud.huaweicloud_iam import HuaweiCloudIAM
from common.security.security_utils import SecurityUtils
from plugins_market.core.logging import get_logger

STORAGE_TYPES = ("MinIO", "OBS")
logger = get_logger(__name__)


def _normalize_market_credentials_mode(raw: str, *, default: str) -> str:
    v = (raw or "").strip().lower() or default
    if v in ("static", "dynamic"):
        return v
    raise ValueError(
        "MARKET_CREDENTIALS_MODE 须为 static 或 dynamic"
        f"（MinIO 未配置时默认 static，OBS 未配置时默认 dynamic），当前: {raw!r}"
    )


def _int_env_seconds(name: str, default: int, *, min_s: int = 60, max_s: int = 604800) -> int:
    """Parse optional env int for presigned TTL; clamp to S3/OBS 常见范围（约 7 天内）。"""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
        return max(min_s, min(v, max_s))
    except ValueError:
        return default


def _resolve_sts_session_duration_seconds() -> int:
    """OBS IAM 换临时凭证时的会话时长（秒），未配置则 3600。"""
    if os.getenv("MARKET_S3_STS_DURATION_SECONDS", "").strip():
        return _int_env_seconds(
            "MARKET_S3_STS_DURATION_SECONDS", 3600, min_s=900, max_s=43200
        )
    return 3600


def _sts_cred_expires_at_to_ts(expires_at: Optional[str]) -> float:
    """华为 IAM 返回的 credential.expires_at → Unix 秒。"""
    if not expires_at:
        return 0.0
    try:
        return datetime.fromisoformat(expires_at.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _new_botocore_config(*, s3_subconfig: Dict[str, Any]) -> Config:
    """
    构造 botocore Config。
    - signature_version 固定 s3v4
    - OBS 需要关闭 payload signing（见 _s3_botocore_subconfig）
    - checksum 相关开关尽量走 Config，避免写入进程级环境变量
    """
    try:
        return Config(
            signature_version="s3v4",
            s3=s3_subconfig,
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        )
    except TypeError:
        return Config(signature_version="s3v4", s3=s3_subconfig)


class S3StorageConfig:
    """S3-compatible storage config. STORAGE_TYPE must be MinIO or OBS."""

    def __init__(self, storage_type: str = "MinIO"):
        """
        :param storage_type: Must be "MinIO" or "OBS".
        """
        raw = (storage_type or "MinIO").strip()
        if raw.upper() == "MINIO":
            self.storage_type = "MinIO"
        elif raw.upper() == "OBS":
            self.storage_type = "OBS"
        else:
            raise ValueError(
                f"STORAGE_TYPE must be one of {STORAGE_TYPES}, got: {storage_type!r}"
            )

        self.endpoint_url = (os.getenv("MARKET_S3_ENDPOINT") or "").rstrip("/")
        _cred_default = "static" if self.storage_type == "MinIO" else "dynamic"
        self.credentials_mode = _normalize_market_credentials_mode(
            os.getenv("MARKET_CREDENTIALS_MODE", ""),
            default=_cred_default,
        )
        if self.storage_type == "MinIO" and self.credentials_mode == "dynamic":
            raise ValueError("MARKET_CREDENTIALS_MODE=dynamic 仅适用于 STORAGE_TYPE=OBS")
        # MinIO / OBS static：永久 MARKET_S3_ACCESS_KEY、MARKET_S3_SECRET_KEY
        # OBS dynamic：运行时经 HUAWEICLOUD_* 调 IAM securitytokens，不读 STS 相关 env
        if self.storage_type == "OBS" and self.credentials_mode == "dynamic":
            self.access_key = ""
            self.secret_key = ""
            self.session_token = None
            self.sts_session_duration_seconds = _resolve_sts_session_duration_seconds()
        else:
            self.access_key = SecurityUtils.get_decrypt_secret("MARKET_S3_ACCESS_KEY", default="") or ""
            self.secret_key = SecurityUtils.get_decrypt_secret("MARKET_S3_SECRET_KEY", default="") or ""
            self.session_token = None
            self.sts_session_duration_seconds = 3600

        self.bucket_name = os.getenv("MARKET_BUCKET_NAME", "openjiuwen-market")
        self.region_name = os.getenv("MARKET_S3_REGION", "us-east-1")

        # use_ssl:
        # - MinIO 默认 http
        # - OBS 默认 https
        # - 同时允许显式覆盖：MARKET_S3_USE_SSL=true|false
        use_ssl_env = os.getenv("MARKET_S3_USE_SSL", "").strip().lower()
        if use_ssl_env in ("true", "1", "on"):
            self.use_ssl = True
        elif use_ssl_env in ("false", "0", "off"):
            self.use_ssl = False
        else:
            if self.storage_type == "MinIO":
                self.use_ssl = bool(self.endpoint_url.startswith("https://"))
            else:
                self.use_ssl = True

        # GET 预签名有效期（秒），默认 1800（30 分钟）；插件包与图标仅通过预签名 URL 访问
        self.presigned_expires_seconds = _int_env_seconds(
            "MARKET_S3_PRESIGNED_EXPIRES", 1800
        )


class S3StorageClient:
    """S3存储客户端，支持本地MinIO和云端S3"""

    # STS 剩余寿命低于该值（秒）时才重新换票；见 _sts_for_presign。
    _STS_MIN_USABLE_SECONDS = 60

    def __init__(self, config: S3StorageConfig):
        """
        初始化S3客户端
        :param config: S3存储配置
        """
        self.config = config
        self._obs_iam_dynamic = (
            config.storage_type == "OBS" and config.credentials_mode == "dynamic"
        )
        self._static_s3_client: Optional[Any] = None
        self._obs_iam_client: Optional[Any] = None
        # OBS dynamic：按临时凭证缓存复用的 boto3 client（凭证不变则不重建）
        self._dyn_s3_client: Optional[Any] = None
        self._dyn_s3_client_key: Optional[tuple] = None
        # 保护 _dyn_s3_client/_dyn_s3_client_key 的并发读写（见 _s3_client_for_sts）
        self._dyn_s3_client_lock = threading.Lock()

        if self._obs_iam_dynamic:
            self._obs_iam_client = HuaweiCloudIAM.from_env()
            try:
                _ = self._new_s3_client_from_iam_sts()
            except Exception as e:
                raise RuntimeError(
                    f"OBS dynamic：调用华为 IAM 换取临时凭证失败，请检查 HUAWEICLOUD_*: {e}"
                ) from e
            logger.info("OBS storage: IAM securitytokens 链路校验通过")
        else:
            if not config.access_key or not config.secret_key:
                raise RuntimeError("请配置 MARKET_S3_ACCESS_KEY 与 MARKET_S3_SECRET_KEY")
            self._static_s3_client = self._new_boto3_s3_client(
                config.access_key, config.secret_key, config.session_token
            )

        self._ensure_bucket_exists()

    def close(self) -> None:
        """释放可关闭的底层资源（httpx 连接池等）。"""
        if self._obs_iam_client is not None:
            try:
                self._obs_iam_client.close()
            except Exception as e:
                logger.warning("Failed to close HuaweiCloudIAM client: %s", e)

    def _s3_botocore_subconfig(self) -> Dict[str, Any]:
        addressing_style = "virtual" if self.config.storage_type == "OBS" else "path"
        s3_cfg: Dict[str, Any] = {"addressing_style": addressing_style}
        if self.config.storage_type == "OBS":
            s3_cfg["payload_signing_enabled"] = False
        return s3_cfg

    def _new_boto3_s3_client(
        self, access_key: str, secret_key: str, session_token: Optional[str]
    ):
        kwargs: Dict[str, Any] = {
            "endpoint_url": self.config.endpoint_url,
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
            "region_name": self.config.region_name,
            "use_ssl": self.config.use_ssl,
            "config": _new_botocore_config(s3_subconfig=self._s3_botocore_subconfig()),
        }
        if session_token:
            kwargs["aws_session_token"] = session_token
        return boto3.client("s3", **kwargs)

    def _s3_client_for_sts(self, sts: Dict[str, Any]):
        """OBS dynamic：按临时凭证缓存复用 boto3 client，仅凭证变化时重建。"""
        key = (sts["access"], sts["securitytoken"])
        with self._dyn_s3_client_lock:
            if self._dyn_s3_client is None or self._dyn_s3_client_key != key:
                self._dyn_s3_client = self._new_boto3_s3_client(
                    sts["access"], sts["secret"], sts["securitytoken"]
                )
                self._dyn_s3_client_key = key
            return self._dyn_s3_client

    def _new_s3_client_from_iam_sts(self):
        """OBS dynamic：STS 由本客户端持有的 HuaweiCloudIAM 换发；client 按凭证复用。"""
        if self._obs_iam_client is None:
            raise RuntimeError("OBS dynamic：HuaweiCloudIAM 客户端未初始化")
        sts = self._obs_iam_client.get_temporary_credentials_from_env(
            duration_seconds=self.config.sts_session_duration_seconds,
        )
        return self._s3_client_for_sts(sts)

    def _sts_for_presign(self, desired_seconds: int) -> tuple[dict, int]:
        """
        预签名在 OBS dynamic 下受 STS 剩余寿命限制。
        返回：用于签名的一组 sts（access/secret/securitytoken/expires_at）与最终 ExpiresIn。

        换票策略：仅当缓存凭证剩余寿命低于 _STS_MIN_USABLE_SECONDS 时才重新换票。
        华为 token 方式换取的临时凭证有效期固定较短（约 15 分钟），可能永远小于
        desired_seconds；若因"签不满 desired"就作废重换，每次预签名都会触发两次
        IAM 公网调用，且新票同样签不满——应直接使用缓存、按剩余寿命缩短 ExpiresIn。
        """
        if self._obs_iam_client is None:
            raise RuntimeError("OBS dynamic：HuaweiCloudIAM 客户端未初始化")

        buffer = 5.0
        sts = self._obs_iam_client.get_temporary_credentials_from_env(
            duration_seconds=self.config.sts_session_duration_seconds,
        )
        exp_ts = _sts_cred_expires_at_to_ts(sts.get("expires_at"))
        cap = int((exp_ts - time.time()) - buffer) if exp_ts > 0 else 0

        if cap < self._STS_MIN_USABLE_SECONDS:
            self._obs_iam_client.invalidate_sts_cache()
            sts = self._obs_iam_client.get_temporary_credentials_from_env(
                duration_seconds=self.config.sts_session_duration_seconds,
            )
            exp_ts = _sts_cred_expires_at_to_ts(sts.get("expires_at"))
            cap = int((exp_ts - time.time()) - buffer) if exp_ts > 0 else 0

        if cap >= desired_seconds:
            return sts, desired_seconds
        if cap >= 1:
            return sts, cap
        raise RuntimeError(
            f"OBS 临时凭证剩余有效过短（约 {max(0, cap)} 秒），无法生成预签名链接。"
        )

    @property
    def s3_client(self):
        """static/MinIO：构建期单例。OBS+dynamic：按 STS 凭证缓存复用 client。"""
        if self._obs_iam_dynamic:
            return self._new_s3_client_from_iam_sts()
        return self._static_s3_client

    def _ensure_bucket_exists(self):
        client = self.s3_client
        try:
            client.head_bucket(Bucket=self.config.bucket_name)
        except client.exceptions.ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            if error_code == '404':
                raise Exception(f"存储桶 '{self.config.bucket_name}' 不存在，请联系管理员创建存储桶") from e
            if self.config.storage_type == "OBS" and str(error_code) in {"403", "AccessDenied", "Forbidden"}:
                logger.warning(
                    "head_bucket 返回 403（可能无权限或策略限制），将跳过存在性校验：bucket=%s",
                    self.config.bucket_name,
                )
                return
            raise Exception(f"无法访问存储桶 '{self.config.bucket_name}': {str(e)}") from e
    
    def resolve_object_key(self, uri_or_key: Optional[str]) -> Optional[str]:
        """
        将对象 Key，或带 bucket 路径的完整访问 URL，解析为桶内对象 Key。
        """
        if not uri_or_key:
            return None
        raw = uri_or_key.strip()
        if not raw:
            return None
        if "://" not in raw:
            return raw
        try:
            p = urlparse(raw)
            path = (p.path or "").lstrip("/")
            bucket = self.config.bucket_name
            if bucket and path.startswith(f"{bucket}/"):
                return path[len(bucket) + 1:]
            return path or None
        except Exception:
            return None

    def presigned_get_url(
        self,
        key: str,
        expires_in: Optional[int] = None,
        download_filename: Optional[str] = None,
    ) -> str:
        """生成临时下载链接。"""
        desired = (
            expires_in
            if expires_in is not None
            else self.config.presigned_expires_seconds
        )
        if desired < 1:
            raise ValueError("预签名 ExpiresIn 须 >= 1 秒")

        if self._obs_iam_dynamic:
            sts, exp = self._sts_for_presign(desired)
            client = self._s3_client_for_sts(sts)
        else:
            exp = desired
            client = self.s3_client
        params: Dict[str, Any] = {"Bucket": self.config.bucket_name, "Key": key}
        if download_filename:
            params["ResponseContentDisposition"] = (
                f'attachment; filename="{download_filename}"'
            )
        return client.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=exp,
        )

    def delete_object(self, key: str) -> Dict[str, Any]:
        """Delete one object by key. Key is path within bucket."""
        try:
            self.s3_client.delete_object(Bucket=self.config.bucket_name, Key=key)
            return {"success": True, "key": key, "storage_type": self.config.storage_type}
        except Exception as e:
            # 仅服务端记录底层异常原文，对外返回通用错误，避免泄露存储后端实现细节（F-63）。
            logger.warning("delete_object failed for key '%s': %s", key, e)
            return {"success": False, "error": "storage delete failed", "key": key,
                    "storage_type": self.config.storage_type}

    def delete_objects(self, keys: list) -> Dict[str, Any]:
        errors: List[Dict[str, Any]] = []
        for k in keys:
            if not k:
                continue
            r = self.delete_object(k)
            if not r.get("success"):
                errors.append({"key": k, "error": r.get("error", "unknown")})
        return {"success": len(errors) == 0, "errors": errors}

    def list_keys(self, prefix: str) -> List[str]:
        """List all object keys under prefix."""
        keys: List[str] = []
        token: Optional[str] = None
        while True:
            kwargs = {"Bucket": self.config.bucket_name, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            resp = self.s3_client.list_objects_v2(**kwargs)
            for obj in resp.get("Contents", []) or []:
                k = obj.get("Key")
                if k:
                    keys.append(k)
            if resp.get("IsTruncated"):
                token = resp.get("NextContinuationToken")
                if not token:
                    break
            else:
                break
        return keys

    def delete_prefix(self, prefix: str) -> Dict[str, Any]:
        """Delete all objects under prefix."""
        keys = self.list_keys(prefix)
        if not keys:
            return {"success": True, "deleted": 0, "errors": []}
        r = self.delete_objects(keys)
        return {"success": r.get("success", False), "deleted": len(keys), "errors": r.get("errors", [])}

    def upload_bytes(
        self,
        body: bytes,
        s3_key: str,
        *,
        metadata: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Upload bytes to S3/MinIO. Key is path within bucket (e.g. plugins/...)."""
        try:
            kwargs: Dict[str, Any] = {
                "Bucket": self.config.bucket_name,
                "Key": s3_key,
                "Body": body,
            }
            if metadata:
                # S3/OBS 会将其映射为 x-amz-meta-*
                kwargs["Metadata"] = metadata
            self.s3_client.put_object(**kwargs)
            return {
                "success": True,
                "bucket": self.config.bucket_name,
                "key": s3_key,
                "storage_type": self.config.storage_type,
            }
        except Exception as e:
            logger.warning("upload_bytes failed for key '%s': %s", s3_key, e)
            return {
                "success": False,
                "error": "storage upload failed",
                "storage_type": self.config.storage_type,
            }

    def head_object(self, key: str) -> Dict[str, Any]:
        """Check object existence via metadata only (without reading body)."""
        try:
            resp = self.s3_client.head_object(Bucket=self.config.bucket_name, Key=key)
            return {
                "success": True,
                "exists": True,
                "key": key,
                "size": resp.get("ContentLength"),
                "metadata": resp.get("Metadata") or {},
                "storage_type": self.config.storage_type,
            }
        except Exception as e:
            logger.warning("head_object failed for key '%s': %s", key, e)
            raw_code = ""
            try:
                raw_code = str(e.response.get("Error", {}).get("Code", "")).strip()  # type: ignore[attr-defined]
            except Exception:
                raw_code = ""
            low_code = raw_code.lower()
            not_found = low_code in {"404", "nosuchkey", "notfound"}
            # 保留标准化的 error_code（如 NoSuchKey）供上层判断，但不再回传原始异常文本（F-63）。
            return {
                "success": False,
                "exists": False if not_found else None,
                "not_found": not_found,
                "error_code": raw_code or None,
                "error": "storage head failed",
                "key": key,
                "storage_type": self.config.storage_type,
            }

    def get_object_size_and_sha256(self, key: str, chunk_size: int = 1024 * 1024) -> Dict[str, Any]:
        """Stream object content to compute size and SHA256."""
        body = None
        try:
            resp = self.s3_client.get_object(Bucket=self.config.bucket_name, Key=key)
            body = resp.get("Body")
            if body is None:
                return {
                    "success": False,
                    "error": "object body is empty",
                    "storage_type": self.config.storage_type,
                }

            hasher = hashlib.sha256()
            total = 0
            while True:
                chunk = body.read(chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                hasher.update(chunk)

            return {
                "success": True,
                "size": total,
                "checksum_sha256": hasher.hexdigest(),
                "storage_type": self.config.storage_type,
            }
        except Exception as e:
            logger.warning("get_object_size_and_sha256 failed for key '%s': %s", key, e)
            return {
                "success": False,
                "error": "storage read failed",
                "storage_type": self.config.storage_type,
            }
        finally:
            if body is not None:
                try:
                    body.close()
                except Exception as close_error:
                    logger.warning("Failed to close object body for key '%s': %s", key, close_error)

    def read_bytes(self, key: str) -> bytes | None:
        """Return object body as bytes; None if not found or any error."""
        body = None
        try:
            resp = self.s3_client.get_object(Bucket=self.config.bucket_name, Key=key)
            body = resp.get("Body")
            if body is None:
                return None
            return body.read()
        except Exception as e:
            logger.debug("read_bytes failed key=%s: %s", key, e)
            return None
        finally:
            if body is not None:
                try:
                    body.close()
                except Exception as close_err:
                    logger.debug("read_bytes: failed to close body key=%s: %s", key, close_err)


_storage_client: Optional["S3StorageClient"] = None


def get_storage_client() -> "S3StorageClient":
    """Build or return cached S3StorageClient from env (STORAGE_TYPE=MinIO|OBS). Used as FastAPI Depends()."""
    global _storage_client
    if _storage_client is None:
        storage_type = os.getenv("STORAGE_TYPE", "MinIO").strip()
        config = S3StorageConfig(storage_type=storage_type or "MinIO")
        _storage_client = S3StorageClient(config)
    return _storage_client


def close_storage_client_if_initialized() -> None:
    """仅在单例已创建时才做 close，避免关机阶段意外初始化依赖链路。"""
    global _storage_client
    if _storage_client is None:
        return
    try:
        _storage_client.close()
    finally:
        _storage_client = None
