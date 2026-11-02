import asyncio
import time

import pytest
from pulsegrid.worker.probe import PinnedBackend, UnsafeURL, permitted, probe, resolve, validate_url


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://user:secret@example.com",
        "http://example.com/#frag",
        "http://example.com:0",
        "http://example.com\\@127.0.0.1",
        "http://example.com/\nsecret",
    ],
)
def test_url_validation(url):
    with pytest.raises(UnsafeURL):
        validate_url(url)


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "169.254.169.254",
        "10.0.0.1",
        "172.17.0.1",
        "192.168.1.1",
        "::1",
        "::ffff:127.0.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "100.64.0.1",
        "168.63.129.16",
        "::ffff:168.63.129.16",
        "64:ff9b::7f00:1",
        "2002:7f00:1::",
    ],
)
def test_network_policy(ip):
    assert not permitted(ip, "evil.test", 80)


async def test_http_success_failure_redirect_timeout_body(fixture_server):
    assert (await probe("http://127.0.0.1:18080/")).success
    fixture_server["status"] = 503
    r = await probe("http://127.0.0.1:18080/")
    assert not r.success and r.status_code == 503
    fixture_server["status"] = 302
    before = fixture_server["hits"]
    r = await probe("http://127.0.0.1:18080/")
    assert not r.success and fixture_server["hits"] == before + 1
    fixture_server.update(status=200, delay=2)
    before = time.monotonic()
    r = await probe("http://127.0.0.1:18080/", timeout=1)
    assert not r.success and time.monotonic() - before < 1.5
    fixture_server.update(delay=0, body=True)
    before = time.monotonic()
    r = await probe("http://127.0.0.1:18080/", timeout=1)
    assert r.success and time.monotonic() - before < 0.5


async def test_mixed_dns_answers_fail_closed(monkeypatch):
    async def answers(*args, **kwargs):
        return [(2, 1, 6, "", ("8.8.8.8", 80)), (2, 1, 6, "", ("127.0.0.1", 80))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", answers)
    with pytest.raises(UnsafeURL):
        await resolve("mixed.test", 80)


async def test_connection_uses_validated_numeric_ip(monkeypatch):
    async def answers(*args, **kwargs):
        return [(2, 1, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", answers)
    backend = PinnedBackend()
    calls = []

    async def connect(host, *args):
        calls.append(host)
        return object()

    monkeypatch.setattr(backend.backend, "connect_tcp", connect)
    await backend.connect_tcp("public.test", 443)
    assert calls == ["93.184.216.34"]


async def test_rebinding_cannot_connect_private_after_public_validation(monkeypatch):
    from pulsegrid.api.main import safe_destination

    calls = 0

    async def answers(*args, **kwargs):
        nonlocal calls
        calls += 1
        return [(2, 1, 6, "", ("8.8.8.8" if calls == 1 else "127.0.0.1", 80))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", answers)
    await safe_destination("http://rebind.test")
    result = await probe("http://rebind.test")
    assert not result.success and result.error == "Destination blocked by network policy"


async def test_prohibited_target_error_redacts_query():
    r = await probe("http://169.254.169.254/?token=secret")
    assert not r.success and "secret" not in r.error


async def test_untrusted_tls_certificate_is_failure(tmp_path):
    import ssl
    from datetime import UTC, datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), False)
        .sign(key, hashes.SHA256())
    )
    certfile = tmp_path / "cert.pem"
    keyfile = tmp_path / "key.pem"
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile, keyfile)

    async def handler(reader, writer):
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 18081, ssl=context)
    try:
        r = await probe("https://127.0.0.1:18081/", timeout=2)
        assert not r.success and r.error == "Connection, DNS, or TLS failure"
    finally:
        server.close()
        await server.wait_closed()
