"""A small web client that only talks to public addresses.

Links in emails are untrusted, so before connecting we resolve the host ourselves, refuse
anything private or local, and connect to the address we checked (not a second lookup).
Every redirect goes through the same checks. Messages and logs carry the host only, never
the full link, because the link may hold the owner's token.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from app.services.unsubscribe.links import host_of

_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_REDIRECTS = (301, 302, 303, 307, 308)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; CleanSync unsubscribe)",
    "Accept": "text/html,*/*;q=0.5",
    "Accept-Encoding": "identity",
}


class RefusedAddress(Exception):
    pass


class WebTrouble(Exception):
    pass


@dataclass
class WebAnswer:
    status: int
    url: str
    content_type: str
    text: str


def is_public_ip(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    if isinstance(a, ipaddress.IPv4Address) and a in _CGNAT:
        return False
    return a.is_global and not (a.is_multicast or a.is_reserved or a.is_unspecified)


async def _system_resolve(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return []
    found: list[str] = []
    for info in infos:
        addr = str(info[4][0])
        if addr not in found:
            found.append(addr)
    return found


class SafeWeb:
    def __init__(
        self,
        *,
        resolve: Callable[[str], Awaitable[list[str]]] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        allow_private: bool = False,
        timeout_s: float = 10.0,
        max_bytes: int = 256 * 1024,
        max_redirects: int = 5,
    ) -> None:
        self.resolve = resolve
        self.transport = transport
        self.allow_private = allow_private
        self.timeout_s = timeout_s
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects

    async def request(self, method: str, url: str, *, form: dict | None = None) -> WebAnswer:
        try:
            return await asyncio.wait_for(self._run(method, url, form), self.timeout_s)
        except TimeoutError:
            raise WebTrouble(f"{host_of(url)} didn't answer in time.") from None

    async def _run(self, method: str, url: str, form: dict | None) -> WebAnswer:
        method = method.upper()
        for hop in range(self.max_redirects + 1):
            answer, location = await self._one_hop(method, url, form)
            if location is None:
                return answer
            if hop == self.max_redirects:
                raise WebTrouble(f"{host_of(url)} sent CleanSync round in circles.")
            try:
                new_url = urljoin(url, location)
            except ValueError:
                raise WebTrouble(f"{host_of(url)} sent a link CleanSync can't use.") from None
            if answer.status in (301, 302, 303) and method == "POST":
                method, form = "GET", None
            url = new_url
        raise WebTrouble(f"{host_of(url)} sent CleanSync round in circles.")  # pragma: no cover

    async def _one_hop(
        self, method: str, url: str, form: dict | None
    ) -> tuple[WebAnswer, str | None]:
        try:
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
            port = parts.port
        except ValueError:
            host, port, parts = host_of(url), -1, None
        if (
            parts is None
            or parts.scheme not in ("http", "https")
            or not host
            # Other ports only with the dev and test switch (the browser test's pretend site).
            or (port not in (None, 80, 443) and not self.allow_private)
        ):
            raise RefusedAddress(f"Only web links on the usual ports are used ({host}).")

        try:
            wire_host = host.encode("idna").decode("ascii")
        except UnicodeError:
            raise WebTrouble(f"{host} couldn't be found.") from None

        effective_port = port or (443 if parts.scheme == "https" else 80)
        if self.resolve is not None:
            addresses = await self.resolve(wire_host)
        else:
            addresses = await _system_resolve(wire_host, effective_port)
        if not addresses:
            raise WebTrouble(f"{host} couldn't be found.")
        if not self.allow_private and not all(is_public_ip(a) for a in addresses):
            raise RefusedAddress(f"{host} points to a private address, so CleanSync won't open it.")

        ip = addresses[0].split("%", 1)[0]
        ip_text = f"[{ip}]" if ":" in ip else ip
        netloc = ip_text if port is None else f"{ip_text}:{port}"
        target = parts._replace(netloc=netloc).geturl()
        host_header = wire_host if ":" not in wire_host else f"[{wire_host}]"
        if port is not None:
            host_header = f"{host_header}:{port}"
        headers = {**_HEADERS, "Host": host_header}
        extensions = {"sni_hostname": wire_host} if parts.scheme == "https" else {}

        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                follow_redirects=False,
                timeout=self.timeout_s,
                verify=True,
                trust_env=False,
                cookies=None,
            ) as client:
                async with client.stream(
                    method,
                    target,
                    headers=headers,
                    data=form if form is not None else None,
                    extensions=extensions,
                ) as response:
                    location = response.headers.get("location")
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        chunks.append(chunk)
                        size += len(chunk)
                        if size >= self.max_bytes:
                            break
                    raw = b"".join(chunks)[: self.max_bytes]
                    encoding = response.charset_encoding or "utf-8"
                    status = response.status_code
                    content_type = response.headers.get("content-type", "")
        except (httpx.HTTPError, httpx.InvalidURL):
            raise WebTrouble(f"Couldn't reach {host}.") from None

        try:
            text = raw.decode(encoding, errors="replace")
        except LookupError:
            text = raw.decode("utf-8", errors="replace")
        answer = WebAnswer(status=status, url=url, content_type=content_type, text=text)
        if status in _REDIRECTS and location:
            return answer, location
        return answer, None
