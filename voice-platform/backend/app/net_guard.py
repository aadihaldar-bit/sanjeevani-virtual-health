"""Safety checks for URLs an organization asks the server to call (external model endpoints, external vector stores).

A tenant must not be able to aim the server at its own internal network or at the cloud metadata service.
Rules: https only (plain http only when EXTERNAL_ALLOW_HTTP=true, for local development), no credentials in the URL,
and every address the host name resolves to must be a public one. EXTERNAL_HOST_ALLOWLIST (comma separated host names
or domain suffixes) narrows the permitted hosts further when set.

The check runs when a URL is saved and again before it is used. DNS can change between a check and the connection, so
treat this as a strong filter rather than a proof; an egress firewall is the complete answer.
"""
import asyncio
import ipaddress
import os
import socket
from typing import Callable, Iterable, List
from urllib.parse import urlparse

BLOCKED_HOSTS = {"metadata.google.internal", "metadata", "localhost", "kubernetes.default.svc"}


class UnsafeURL(ValueError):
    """The URL points somewhere the server must not connect to."""


def _env_list(name: str) -> List[str]:
    return [h.strip().lower() for h in os.getenv(name, "").split(",") if h.strip()]


def _is_public(ip: str) -> bool:
    """Only globally routable unicast addresses: no private, shared (100.64/10), loopback, link-local, multicast,
    reserved or unspecified ranges, and never the cloud metadata address."""
    addr = ipaddress.ip_address(ip.split("%")[0])
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    if str(addr) == "169.254.169.254":
        return False
    return bool(addr.is_global and not (addr.is_multicast or addr.is_loopback or addr.is_link_local or addr.is_unspecified))


def _host_allowed(host: str, allowlist: Iterable[str]) -> bool:
    allowlist = list(allowlist)
    if not allowlist:
        return True
    return any(host == a or host.endswith("." + a.lstrip(".")) for a in allowlist)


def validate_external_url(url: str, resolver: Callable = socket.getaddrinfo) -> str:
    """Return the normalised URL or raise UnsafeURL with a message an operator can act on."""
    url = (url or "").strip()
    if not url:
        raise UnsafeURL("A URL is required")
    parsed = urlparse(url)
    allow_http = os.getenv("EXTERNAL_ALLOW_HTTP", "false").lower() == "true"
    if parsed.scheme != "https" and not (allow_http and parsed.scheme == "http"):
        raise UnsafeURL("Only https URLs are allowed")
    if not parsed.hostname:
        raise UnsafeURL("The URL has no host name")
    if parsed.username or parsed.password:
        raise UnsafeURL("Do not put credentials in the URL; use the key field")
    host = parsed.hostname.lower().rstrip(".")
    if host in BLOCKED_HOSTS or host.endswith(".internal") or host.endswith(".local"):
        raise UnsafeURL("That host name is not allowed")
    if not _host_allowed(host, _env_list("EXTERNAL_HOST_ALLOWLIST")):
        raise UnsafeURL("That host is not on this server's allow-list (EXTERNAL_HOST_ALLOWLIST)")
    if host in _env_list("EXTERNAL_HOST_DENYLIST"):
        raise UnsafeURL("That host is blocked")
    try:
        literal = ipaddress.ip_address(host)
        addresses = [str(literal)]
    except ValueError:
        try:
            infos = resolver(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        except OSError as exc:
            raise UnsafeURL(f"The host name could not be resolved ({exc.__class__.__name__})") from exc
        addresses = sorted({info[4][0] for info in infos})
    if not addresses:
        raise UnsafeURL("The host name did not resolve to any address")
    for ip in addresses:
        if not _is_public(ip):
            raise UnsafeURL("The host resolves to a private or internal address, which is not allowed")
    return url.rstrip("/")


async def avalidate_external_url(url: str, resolver: Callable = socket.getaddrinfo) -> str:
    """Same check without blocking the event loop on DNS."""
    return await asyncio.to_thread(validate_external_url, url, resolver)
