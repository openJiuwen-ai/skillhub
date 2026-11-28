# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Exercise Keycloak certificate trust over a real asynchronous TLS connection."""

import asyncio
import ipaddress
import json
import ssl
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from plugins_market.identity_extensions.contracts import IdentityError
from plugins_market.identity_extensions.keycloak import adapter as adapter_module
from plugins_market.identity_extensions.keycloak.config import KeycloakConfig


@pytest.fixture
def local_tls_material(tmp_path, monkeypatch):
    for variable in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
        monkeypatch.delenv(variable, raising=False)
    for variable in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(variable, "127.0.0.1,localhost")
    monkeypatch.setattr(
        adapter_module.SecurityUtils,
        "get_decrypt_secret",
        lambda *args, **kwargs: "test-client-secret",
    )

    # TLS trust does not depend on the ID Token signing algorithm. A small EC key
    # keeps each isolated test's certificate generation inexpensive.
    key = ec.generate_private_key(ec.SECP256R1())
    identity = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "skillhub-tls-test")])
    created_at = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .serial_number(x509.random_serial_number())
        .subject_name(identity)
        .issuer_name(identity)
        .public_key(key.public_key())
        .not_valid_before(created_at - timedelta(minutes=1))
        .not_valid_after(created_at + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.IPv4Address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    ca_file = tmp_path.joinpath("trusted-ca.pem")
    ca_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    server_pem = tmp_path.joinpath("server-identity.pem")
    server_pem.write_bytes(
        b"".join(
            (
                ca_file.read_bytes(),
                key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                ),
            )
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(server_pem)
    return context, ca_file


async def _authorize_over_tls(context, options, requests):
    metadata = {}
    finished = asyncio.Event()

    async def discovery_connection(reader, writer):
        try:
            headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
            requests.append(headers.split(b"\r\n", 1)[0].decode("ascii"))
            body = json.dumps(metadata).encode("utf-8")
            response_headers = "\r\n".join(
                (
                    "HTTP/1.1 200 OK",
                    "Content-Type: application/json",
                    f"Content-Length: {len(body)}",
                    "Connection: close",
                    "",
                    "",
                )
            ).encode("ascii")
            writer.writelines((response_headers, body))
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            finished.set()

    listener = await asyncio.start_server(
        discovery_connection, host="127.0.0.1", port=0, ssl=context,
        ssl_handshake_timeout=5, ssl_shutdown_timeout=5,
    )
    async with listener:
        port = listener.sockets[0].getsockname()[1]
        issuer = f"https://127.0.0.1:{port}/realms/company"
        metadata["issuer"] = issuer
        for field, relative in (
            ("authorization_endpoint", "auth"),
            ("token_endpoint", "token"),
            ("userinfo_endpoint", "userinfo"),
            ("jwks_uri", "certs"),
        ):
            metadata[field] = urljoin(f"{issuer}/", relative)
        config = KeycloakConfig(
            issuer=issuer,
            client_id="skillhub",
            client_secret_env="TEST_IDENTITY_SECRET",
            redirect_uri="https://hub.example.test/api/v1/auth/oauth/keycloak/callback",
            **options,
        )
        adapter = adapter_module.KeycloakAdapter("keycloak", config)
        authorization = await adapter.start_authorization(state="test-state")
        await asyncio.wait_for(finished.wait(), timeout=5)
        return authorization, metadata


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
    local_tls_material, monkeypatch, options, trusted_ca, succeeds
):
    context, ca_file = local_tls_material
    if trusted_ca:
        monkeypatch.setenv("SSL_CERT_FILE", str(ca_file))
    requests = []
    if succeeds:
        authorization, metadata = asyncio.run(_authorize_over_tls(context, options, requests))
        actual = urlsplit(authorization.url)
        assert actual._replace(query="") == urlsplit(metadata["authorization_endpoint"])
        assert parse_qs(actual.query)["state"] == ["test-state"]
        assert requests == ["GET /realms/company/.well-known/openid-configuration HTTP/1.1"]
    else:
        with pytest.raises(IdentityError) as failure:
            asyncio.run(_authorize_over_tls(context, options, requests))
        assert isinstance(failure.value.__cause__, httpx.ConnectError)
        assert "CERTIFICATE_VERIFY_FAILED" in str(failure.value.__cause__)
        assert requests == []
