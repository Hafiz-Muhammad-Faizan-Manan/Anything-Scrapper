"""URL validation and SSRF protection.

Every outbound request made by the backend (the page itself, each redirect hop, linked
stylesheets and every image) must pass through :func:`validate_url` first.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit, urlunsplit

from app.config import Settings, get_settings

ALLOWED_SCHEMES = {"http", "https"}
DEFAULT_PORTS = {"http": 80, "https": 443}
BLOCKED_HOSTNAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback", "broadcasthost"}
BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa", ".intranet", ".corp")
NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")

DNS_TIMEOUT_SECONDS = 5.0
DNS_ATTEMPTS = 3
# EAI_AGAIN on POSIX, WSATRY_AGAIN (11002) on Windows: transient resolver failures worth retrying.
TEMPORARY_DNS_ERRNOS = {getattr(socket, "EAI_AGAIN", -3), 11002}
DNS_CACHE_TTL_SECONDS = 30.0


class UnsafeURLError(ValueError):
    """Raised when a URL is malformed or points to a destination we refuse to contact."""


@dataclass(frozen=True)
class ValidatedURL:
    url: str
    host: str
    port: int
    addresses: tuple[str, ...]


_dns_cache: dict[tuple[str, int], tuple[float, tuple[str, ...]]] = {}


def is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for any address that is not a public unicast address."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return is_blocked_ip(ip.ipv4_mapped)
        if ip.sixtofour is not None and is_blocked_ip(ip.sixtofour):
            return True
        if ip.teredo is not None and is_blocked_ip(ip.teredo[1]):
            return True
        if ip in NAT64_PREFIX:
            return is_blocked_ip(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    return (
        not ip.is_global
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _literal_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse a host as an IP literal, including legacy shorthand forms like ``127.1`` or ``2130706433``."""
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    if all(c in "0123456789abcdefxABCDEFX." for c in host):
        try:
            return ipaddress.IPv4Address(socket.inet_aton(host))
        except (OSError, ValueError):
            return None
    return None


def check_url_syntax(url: str, settings: Settings | None = None) -> tuple[SplitResult, str, int]:
    """Synchronous structural checks. Returns ``(parts, host, port)``."""
    settings = settings or get_settings()
    if not isinstance(url, str) or not url.strip():
        raise UnsafeURLError("URL is empty.")
    url = url.strip()
    if len(url) > settings.max_url_length:
        raise UnsafeURLError("URL is too long.")
    if any(ord(c) < 0x20 or c == "\x7f" for c in url):
        raise UnsafeURLError("URL contains control characters.")

    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeURLError("Only http:// and https:// URLs are supported.")
    if parts.username is not None or parts.password is not None:
        raise UnsafeURLError("URLs containing credentials are not allowed.")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeURLError("URL must include a hostname.")
    try:
        port = parts.port or DEFAULT_PORTS[scheme]
    except ValueError as exc:
        raise UnsafeURLError("URL has an invalid port.") from exc

    if not settings.allow_private_networks:
        if port not in settings.allowed_port_set:
            raise UnsafeURLError(f"Port {port} is not allowed.")
        if host in BLOCKED_HOSTNAMES or host.endswith(BLOCKED_SUFFIXES):
            raise UnsafeURLError("Local or internal hostnames are not allowed.")
        ip = _literal_ip(host)
        if ip is not None and is_blocked_ip(ip):
            raise UnsafeURLError("Private, loopback or reserved IP addresses are not allowed.")
        if ip is None and "." not in host:
            raise UnsafeURLError("Single-label hostnames are not allowed.")
    return parts, host, port


def normalize_url(parts: SplitResult, host: str, port: int) -> str:
    scheme = parts.scheme.lower()
    netloc = f"[{host}]" if ":" in host else host
    if port != DEFAULT_PORTS[scheme]:
        netloc = f"{netloc}:{port}"
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


async def _resolve(host: str, port: int) -> tuple[str, ...]:
    key = (host, port)
    now = time.monotonic()
    cached = _dns_cache.get(key)
    if cached and cached[0] > now:
        return cached[1]
    loop = asyncio.get_running_loop()
    infos = None
    for attempt in range(DNS_ATTEMPTS):
        try:
            infos = await asyncio.wait_for(
                loop.getaddrinfo(host, port, type=socket.SOCK_STREAM), timeout=DNS_TIMEOUT_SECONDS
            )
            break
        except socket.gaierror as exc:
            # Resolvers (notably on Windows) sometimes report transient failures as "host not
            # found", so every failure gets a short retry; temporary errors get the full budget.
            temporary = exc.errno in TEMPORARY_DNS_ERRNOS
            if attempt == DNS_ATTEMPTS - 1 or (not temporary and attempt >= 1):
                raise UnsafeURLError(f"Could not resolve host '{host}'.") from exc
            await asyncio.sleep(0.3 * (attempt + 1))
        except UnicodeError as exc:
            raise UnsafeURLError(f"Could not resolve host '{host}'.") from exc
        except TimeoutError as exc:
            raise UnsafeURLError(f"DNS lookup for '{host}' timed out.") from exc
    assert infos is not None
    addresses = tuple(sorted({info[4][0] for info in infos}))
    if not addresses:
        raise UnsafeURLError(f"Could not resolve host '{host}'.")
    if len(_dns_cache) > 2048:
        _dns_cache.clear()
    _dns_cache[key] = (now + DNS_CACHE_TTL_SECONDS, addresses)
    return addresses


async def validate_url(url: str, settings: Settings | None = None) -> ValidatedURL:
    """Validate a URL for outbound use. Raises :class:`UnsafeURLError` when it is unsafe."""
    settings = settings or get_settings()
    parts, host, port = check_url_syntax(url, settings)
    normalized = normalize_url(parts, host, port)

    literal = _literal_ip(host)
    if literal is not None:
        addresses: tuple[str, ...] = (str(literal),)
    else:
        addresses = await _resolve(host, port)

    if not settings.allow_private_networks:
        for addr in addresses:
            try:
                ip = ipaddress.ip_address(addr.split("%", 1)[0])
            except ValueError as exc:
                raise UnsafeURLError("Host resolved to an invalid address.") from exc
            if is_blocked_ip(ip):
                raise UnsafeURLError("Host resolves to a private or internal network address.")
    return ValidatedURL(url=normalized, host=host, port=port, addresses=addresses)
