"""Destination policy is enforced at connect time, including repeated HTTPX connections."""

import asyncio
import ipaddress
import socket
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpcore
import httpx
from httpcore._backends.auto import AutoBackend

from pulsegrid.config import settings


class UnsafeURL(ValueError):
    pass


def validate_url(url):
    if len(url) > 2048 or any(ord(c) < 33 or ord(c) == 127 for c in url) or "\\" in url:
        raise UnsafeURL("Invalid URL")
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise UnsafeURL("Only HTTP(S) URLs without credentials or fragments are allowed")
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise UnsafeURL("Invalid port")
        return parsed
    except ValueError:
        raise UnsafeURL("Invalid HTTP(S) destination") from None


def permitted(address, host, port):
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    mapped = getattr(ip, "ipv4_mapped", None)
    if str(mapped or ip) == "168.63.129.16":
        return False  # Azure platform virtual IP; public classification does not make it an ordinary target.
    if isinstance(ip, ipaddress.IPv6Address) and (
        ip in ipaddress.ip_network("64:ff9b::/96") or ip.sixtofour or ip.teredo
    ):
        return False  # Transition mechanisms can otherwise translate to prohibited IPv4 destinations.
    if (
        ip.is_global
        and not ip.is_multicast
        and not ip.is_unspecified
        and not (getattr(ip, "ipv4_mapped", None) and not ip.ipv4_mapped.is_global)
    ):
        return True
    # Exact hostname:port entries only, no blanket private subnet switch.
    return f"{host}:{port}" in settings().private_target_allowlist.split(",") and not ip.is_unspecified


async def resolve(host, port):
    async with asyncio.timeout(3):
        answers = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = sorted({answer[4][0] for answer in answers})
    if not addresses or not all(permitted(ip, host, port) for ip in addresses):
        raise UnsafeURL("Destination blocked by network policy")
    return addresses


class PinnedBackend(httpcore.AsyncNetworkBackend):
    def __init__(self):
        self.backend = AutoBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        addresses = await resolve(host, port)
        # Connect to the validated numeric address. httpcore retains the URL hostname
        # for Host and start_tls(server_hostname), closing the DNS rebinding gap.
        return await self.backend.connect_tcp(addresses[0], port, timeout, local_address, socket_options)

    async def connect_unix_socket(self, *args, **kwargs):
        raise UnsafeURL("Unix sockets prohibited")

    async def sleep(self, seconds):
        await asyncio.sleep(seconds)


class SafeTransport(httpx.AsyncHTTPTransport):
    def __init__(self):
        super().__init__(
            retries=0, trust_env=False, limits=httpx.Limits(max_connections=100, max_keepalive_connections=0)
        )
        self._pool._network_backend = PinnedBackend()


@dataclass
class ProbeResult:
    success: bool
    status_code: int | None = None
    latency_ms: float | None = None
    error: str | None = None


async def probe(url, method="GET", statuses=None, timeout=10):
    started = time.monotonic()
    try:
        validate_url(url)
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(
                transport=SafeTransport(), trust_env=False, follow_redirects=False, timeout=timeout
            ) as client:
                async with client.stream(
                    method, url, headers={"User-Agent": "PulseGrid/1.0", "Accept": "*/*"}
                ) as response:
                    # Time to response headers; close immediately without reading a body.
                    latency = (time.monotonic() - started) * 1000
                    ok = response.status_code in (statuses or [200])
                    return ProbeResult(
                        ok, response.status_code, latency, None if ok else f"HTTP {response.status_code}"
                    )
    except UnsafeURL:
        return ProbeResult(False, error="Destination blocked by network policy")
    except (TimeoutError, httpx.TimeoutException):
        return ProbeResult(False, error="Probe deadline exceeded")
    except (httpx.HTTPError, OSError, ValueError):
        return ProbeResult(False, error="Connection, DNS, or TLS failure")
