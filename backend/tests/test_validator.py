import ipaddress

import pytest

from app.config import Settings
from app.utils.validator import UnsafeURLError, check_url_syntax, is_blocked_ip, validate_url


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/a.png",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://",
        "http://user:pass@example.com/",
        "http://localhost/",
        "http://LOCALHOST./",
        "http://foo.localhost/",
        "http://printer.local/",
        "http://metadata.google.internal/",
        "http://127.0.0.1/",
        "http://127.1/",
        "http://2130706433/",
        "http://0x7f000001/",
        "http://0177.0.0.1/",
        "http://10.0.0.5/",
        "http://172.16.3.4/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.64.0.1/",
        "http://0.0.0.0/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[fd00::1]/",
        "http://[fe80::1]/",
        "http://224.0.0.1/",
        "http://example.com:22/",
        "http://intranet/",
        "http://exa\nmple.com/",
    ],
)
def test_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(UnsafeURLError):
        check_url_syntax(url, Settings())


@pytest.mark.parametrize(
    "ip,blocked",
    [
        ("8.8.8.8", False),
        ("93.184.215.14", False),
        ("2606:4700::1111", False),
        ("127.0.0.1", True),
        ("10.1.2.3", True),
        ("169.254.169.254", True),
        ("::ffff:10.0.0.1", True),
        ("2002:7f00:0001::", True),  # 6to4 wrapping 127.0.0.1
        ("64:ff9b::a00:1", True),  # NAT64 wrapping 10.0.0.1
        ("fc00::1", True),
        ("ff02::1", True),
    ],
)
def test_is_blocked_ip(ip: str, blocked: bool) -> None:
    assert is_blocked_ip(ipaddress.ip_address(ip)) is blocked


async def test_validate_public_literal_ip() -> None:
    result = await validate_url("HTTPS://93.184.215.14/Path?q=1#frag", Settings())
    assert result.url == "https://93.184.215.14/Path?q=1"
    assert result.addresses == ("93.184.215.14",)


async def test_validate_rejects_private_literal() -> None:
    with pytest.raises(UnsafeURLError):
        await validate_url("http://192.168.0.10/", Settings())


async def test_validate_rejects_hostname_resolving_to_private(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.utils import validator

    async def fake_resolve(host: str, port: int) -> tuple[str, ...]:
        return ("93.184.215.14", "10.0.0.7")  # one bad record is enough to reject

    monkeypatch.setattr(validator, "_resolve", fake_resolve)
    with pytest.raises(UnsafeURLError, match="private"):
        await validate_url("https://rebind.example.com/", Settings())


async def test_allow_private_networks_flag() -> None:
    result = await validate_url("http://127.0.0.1:9000/", Settings(allow_private_networks=True))
    assert result.port == 9000
