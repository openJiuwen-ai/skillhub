# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Exercise Keycloak discovery against a real, untrusted local TLS server."""

import asyncio
import ipaddress
import json
import ssl
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from plugins_market.identity_extensions.contracts import IdentityError
from plugins_market.identity_extensions.keycloak import adapter as adapter_module
from plugins_market.identity_extensions.keycloak.config import KeycloakConfig


def _write_self_signed_certificate(tmp_path):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(private_key, hashes.SHA256())
    )
    certificate_path = tmp_path / "ca.pem"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path = tmp_path / "key.pem"
    key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return certificate_path, key_path


@pytest.fixture
def self_signed_oidc_server(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setattr(
        adapter_module.SecurityUtils,
        "get_decrypt_secret",
        lambda *args, **kwargs: "test-client-secret",
    )
    certificate_path, key_path = _write_self_signed_certificate(tmp_path)

    class DiscoveryHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = json.dumps(
                {
                    "issuer": issuer,
                    "authorization_endpoint": issuer + "/auth",
                    "token_endpoint": issuer + "/token",
                    "userinfo_endpoint": issuer + "/userinfo",
                    "jwks_uri": issuer + "/certs",
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, message, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), DiscoveryHandler) as server:
        issuer = f"https://127.0.0.1:{server.server_port}/realms/company"
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate_path, key_path)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield issuer, certificate_path
        finally:
            server.shutdown()
            thread.join(timeout=5)


@pytest.mark.parametrize(
    ("options", "trusted_ca", "succeeds"),
    [
        ({}, False, False),
        ({"ssl_verify": True}, False, False),
        ({"ssl_verify": False}, False, True),
        ({"ssl_verify": True}, True, True),
    ],
)
def test_keycloak_tls_verification_with_self_signed_server(
    self_signed_oidc_server, monkeypatch, options, trusted_ca, succeeds
):
    issuer, certificate_path = self_signed_oidc_server
    if trusted_ca:
        monkeypatch.setenv("SSL_CERT_FILE", str(certificate_path))
    config = KeycloakConfig(
        issuer=issuer,
        client_id="skillhub",
        client_secret_env="TEST_IDENTITY_SECRET",
        redirect_uri="https://hub.example.test/api/v1/auth/oauth/keycloak/callback",
        **options,
    )
    adapter = adapter_module.KeycloakAdapter("keycloak", config)
    if succeeds:
        authorization = asyncio.run(adapter.start_authorization(state="test-state"))
        assert authorization.url.startswith(issuer + "/auth?")
        assert parse_qs(urlsplit(authorization.url).query)["state"] == ["test-state"]
    else:
        with pytest.raises(IdentityError) as failure:
            asyncio.run(adapter.start_authorization(state="test-state"))
        assert isinstance(failure.value.__cause__, httpx.ConnectError)
        assert "CERTIFICATE_VERIFY_FAILED" in str(failure.value.__cause__)
