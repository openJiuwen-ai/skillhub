# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""OBS HTTPS 可单独关闭证书校验，默认仍校验。"""

from __future__ import annotations

from unittest.mock import patch

from plugins_market.core.s3_storage_client import S3StorageClient, S3StorageConfig


def _obs_config(monkeypatch, ssl_verify: str | None) -> S3StorageConfig:
    monkeypatch.setenv("MARKET_S3_ENDPOINT", "https://obs.internal.example")
    monkeypatch.setenv("MARKET_CREDENTIALS_MODE", "dynamic")
    monkeypatch.delenv("MARKET_S3_USE_SSL", raising=False)
    if ssl_verify is None:
        monkeypatch.delenv("MARKET_S3_SSL_VERIFY", raising=False)
    else:
        monkeypatch.setenv("MARKET_S3_SSL_VERIFY", ssl_verify)
    return S3StorageConfig("OBS")


def test_obs_https_keeps_ssl_and_skips_cert_verify_when_disabled(monkeypatch) -> None:
    config = _obs_config(monkeypatch, "false")
    assert config.use_ssl is True
    assert config.verify_ssl is False

    holder = S3StorageClient.__new__(S3StorageClient)
    holder.config = config
    with patch("plugins_market.core.s3_storage_client.boto3.client") as client:
        holder._new_boto3_s3_client("ak", "sk", None)

    kwargs = client.call_args.kwargs
    assert kwargs["endpoint_url"] == "https://obs.internal.example"
    assert kwargs["use_ssl"] is True
    assert kwargs["verify"] is False


def test_ssl_verify_defaults_true(monkeypatch) -> None:
    config = _obs_config(monkeypatch, None)
    assert config.use_ssl is True
    assert config.verify_ssl is True


def test_ssl_verify_off_aliases(monkeypatch) -> None:
    for raw in ("0", "off", "no", "FALSE"):
        config = _obs_config(monkeypatch, raw)
        assert config.verify_ssl is False
