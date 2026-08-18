import time
from contextlib import asynccontextmanager

import pytest
from authlib.jose import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def app_client():
    """Async HTTP client for an app whose lifespan is actually running.

    httpx's ASGITransport does not fire lifespan events, but the MCP session
    manager initialises its anyio task group there. Without it every request
    reaching the mounted /mcp app dies with "Task group is not initialized" —
    which says nothing about the code under test. Always reach for this instead
    of wiring up ASGITransport by hand.
    """

    @asynccontextmanager
    async def _client(app):
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                yield client

    return _client


@pytest.fixture(scope="session")
def rsa_key():
    """A test RSA key-pair used to sign synthetic JWTs."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_key = private_key.public_key()
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return {"private_pem": private_pem, "public_pem": public_pem, "kid": "test-kid-1"}


@pytest.fixture
def make_token(rsa_key):
    def _make(
        *,
        iss: str = "https://login.microsoftonline.com/campai-tenant-id/v2.0",
        aud: str = "api://admcms-mcp",
        oid: str = "user-oid-1",
        tid: str = "campai-tenant-id",
        upn: str = "roland@campai.nl",
        groups: list[str] | None = None,
        scp: str = "admcms.read",
        roles: list[str] | None = None,
        exp_offset: int = 3600,
    ) -> str:
        payload = {
            "iss": iss,
            "aud": aud,
            "oid": oid,
            "tid": tid,
            "upn": upn,
            "scp": scp,
            "exp": int(time.time()) + exp_offset,
            "iat": int(time.time()),
        }
        if groups is not None:
            payload["groups"] = groups
        if roles is not None:
            payload["roles"] = roles
        header = {"alg": "RS256", "kid": rsa_key["kid"]}
        token = jwt.encode(header, payload, rsa_key["private_pem"])
        return token.decode() if isinstance(token, bytes) else token

    return _make
