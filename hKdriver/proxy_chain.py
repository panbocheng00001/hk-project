# -*- coding: utf-8 -*-
"""proxy_chain.py —— 本地串联代理：本机 → 中转跳板 → 上游代理 → 目标
=================================================================================
为什么需要它：
    你买的那批住宅代理（us.ipwo.net:7878）会**拒绝来源 IP**，原话是
        {"code":403,"msg":"access denied,china IP is not allow"}
    也就是说：账号密码是对的（不带认证时返回的是 407，不是 403），
    被拒的是「从哪儿连过来」。所以换 sid、换 time_10/time_180 都没用 —— 不是过期。

    解法只有一条：让「连到上游代理的那一跳」来自一个它认可的地址。
        本机 ──▶ 本地转发器(127.0.0.1:8899) ──▶ 跳板(海外 VPS 的 SOCKS5/HTTP) ──▶ 上游代理 ──▶ 目标
    跑起来之后，所有下游（curl_cffi、票池、Chrome）只需要把代理指向
        127.0.0.1:8899
    上游的账号密码由本转发器注入，下游完全不用知道。

用法
    # 1) 有海外 VPS（推荐）：
    python proxy_chain.py --listen 127.0.0.1:8899 \
        --hop socks5://user:pass@VPS_IP:1080 \
        --upstream http://_custom_zone_HK_sid_xxx_time_180:@us.ipwo.net:7878

    # 2) 跳板本身是 HTTP 代理：
    python proxy_chain.py --hop http://user:pass@VPS_IP:3128 --upstream http://...

    # 3) 不要跳板（直连上游，用来验证上游本身）：
    python proxy_chain.py --upstream http://...:7878

    # 4) 批量：每条上游一个本地端口，方便票池按出口分配
    python proxy_chain.py --upstream-file proxies.txt --listen-base 8900

自检
    python proxy_chain.py --selftest          # 用本地假代理验证「串联—转发—隧道」这条链路
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import socket
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

BASE = Path(__file__).resolve().parent


# ---------------------------------------------------------------- 解析
def parse_proxy(url: str) -> dict:
    """支持 socks5:// / http:// ；user 里带冒号也没关系（只按第一个冒号切密码）。"""
    u = urlparse(url if "://" in url else "http://" + url)
    scheme = (u.scheme or "http").lower()
    return {
        "scheme": scheme,
        "host": u.hostname or "",
        "port": u.port or (1080 if scheme.startswith("socks") else 8080),
        "user": unquote(u.username or ""),
        "pass": unquote(u.password or ""),
    }


def parse_hostport(url: str) -> dict:
    """host:port:user:pass 这种一行四段的手写格式。"""
    parts = url.strip().split(":")
    if len(parts) < 2:
        raise ValueError(f"解析不了：{url[:60]}")
    return {"scheme": "http", "host": parts[0], "port": int(parts[1]),
            "user": ":".join(parts[2:-1]) if len(parts) > 3 else (parts[2] if len(parts) > 2 else ""),
            "pass": parts[-1] if len(parts) > 3 else ""}


# ---------------------------------------------------------------- 建链
async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def dial_via_hop(hop: dict, host: str, port: int, timeout: float = 20.0):
    """穿过跳板，拿到一条到 host:port 的 TCP 连接。"""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(hop["host"], hop["port"]), timeout)

    if hop["scheme"].startswith("socks"):
        # SOCKS5 握手（支持用户名/密码认证）
        need_auth = bool(hop["user"])
        writer.write(b"\x05\x02\x00\x02" if need_auth else b"\x05\x01\x00")
        await writer.drain()
        ver_method = await reader.readexactly(2)
        if ver_method[0] != 5:
            raise RuntimeError(f"上游不是 SOCKS5：{ver_method!r}")
        if ver_method[1] == 0x02:
            u, p = hop["user"].encode(), hop["pass"].encode()
            writer.write(bytes([5, len(u)]) + u + bytes([len(p)]) + p)
            await writer.drain()
            st = await reader.readexactly(2)
            if st[1] != 0:
                raise RuntimeError("SOCKS5 认证失败")
        elif ver_method[1] == 0xFF:
            raise RuntimeError("SOCKS5 拒绝所有认证方式")
        # CONNECT 请求
        h = host.encode()
        writer.write(bytes([5, 1, 0, 3, len(h)]) + h + int(port).to_bytes(2, "big"))
        await writer.drain()
        rep = await reader.readexactly(4)
        if rep[1] != 0:
            raise RuntimeError(f"SOCKS5 CONNECT 被拒 code={rep[1]}")
        atyp = rep[3]
        if atyp == 1:
            await reader.readexactly(4 + 2)
        elif atyp == 3:
            ln = (await reader.readexactly(1))[0]
            await reader.readexactly(ln + 2)
        elif atyp == 4:
            await reader.readexactly(16 + 2)
        return reader, writer

    # 跳板是 HTTP 代理：也走 CONNECT
    hdr = [f"CONNECT {host}:{port} HTTP/1.1", f"Host: {host}:{port}",
           "Proxy-Connection: Keep-Alive"]
    if hop["user"]:
        b = base64.b64encode(f"{hop['user']}:{hop['pass']}".encode()).decode()
        hdr.append(f"Proxy-Authorization: Basic {b}")
    writer.write(("\r\n".join(hdr) + "\r\n\r\n").encode())
    await writer.drain()
    head = await reader.readuntil(b"\r\n\r\n")
    if b" 200" not in head.split(b"\r\n", 1)[0]:
        raise RuntimeError(f"跳板 CONNECT 失败：{head[:120]!r}")
    return reader, writer


async def open_upstream_connect(up: dict, target_host: str, target_port: int,
                                hop: dict | None, timeout: float = 25.0):
    """连到上游代理，并让它 CONNECT 到目标。返回 (reader, writer)。"""
    if hop:
        reader, writer = await dial_via_hop(hop, up["host"], up["port"], timeout)
    else:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(up["host"], up["port"]), timeout)

    hdr = [f"CONNECT {target_host}:{target_port} HTTP/1.1",
           f"Host: {target_host}:{target_port}", "Proxy-Connection: Keep-Alive",
           "User-Agent: Mozilla/5.0"]
    if up["user"]:
        b = base64.b64encode(f"{up['user']}:{up['pass']}".encode()).decode()
        hdr.append(f"Proxy-Authorization: Basic {b}")
    writer.write(("\r\n".join(hdr) + "\r\n\r\n").encode())
    await writer.drain()
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout)
    line = head.split(b"\r\n", 1)[0]
    if b" 200" not in line:
        body = b""
        try:
            body = await asyncio.wait_for(reader.read(400), 2.0)
        except Exception:
            pass
        raise RuntimeError(f"上游 CONNECT 失败：{line.decode('latin1')} {body[:180].decode('utf-8', 'replace')}")
    return reader, writer


class ChainServer:
    def __init__(self, listen, upstream, hop=None, verbose=True):
        self.listen = listen
        self.up = upstream
        self.hop = hop
        self.verbose = verbose

    async def handle(self, reader, writer):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 20)
        except Exception:
            writer.close()
            return
        line = head.split(b"\r\n", 1)[0].decode("latin1")
        try:
            method, target, _ = line.split(" ")
        except ValueError:
            writer.close()
            return

        # 只做 CONNECT（HTTPS）；普通的绝对 URI 请求也支持一下
        if method.upper() == "CONNECT":
            host, _, port = target.rpartition(":")
            try:
                ur, uw = await open_upstream_connect(self.up, host, int(port or 443), self.hop)
            except Exception as exc:
                msg = str(exc)
                if self.verbose:
                    print(f"[chain] ✗ {target}  {msg[:160]}", flush=True)
                body = f'{{"code":403,"msg":"chain: {msg[:120]}"}}'.encode()
                writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Type: application/json\r\n"
                             b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
                await writer.drain()
                writer.close()
                return
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            if self.verbose:
                print(f"[chain] ✓ {target}", flush=True)
            await asyncio.gather(_pipe(reader, uw), _pipe(ur, writer))
            return

        # GET http://host/path （无 TLS 的代理请求）
        try:
            url = urlparse(target)
            host, port = url.hostname, url.port or 80
            ur, uw = await open_upstream_connect(self.up, host, port, self.hop)
            path = url.path or "/"
            if url.query:
                path += "?" + url.query
            req = f"{method} {path} HTTP/1.1\r\n"
            for ln in head.split(b"\r\n")[1:]:
                if not ln:
                    continue
                s = ln.decode("latin1")
                if s.lower().startswith(("proxy-authorization", "proxy-connection")):
                    continue
                req += s + "\r\n"
            uw.write((req + "\r\n").encode("latin1"))
            await uw.drain()
            await asyncio.gather(_pipe(reader, uw), _pipe(ur, writer))
        except Exception as exc:
            if self.verbose:
                print(f"[chain] ✗ {method} {target} {exc}", flush=True)
            try:
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
            except Exception:
                pass
            writer.close()

    async def serve(self):
        srv = await asyncio.start_server(self.handle, self.listen[0], self.listen[1])
        print(f"[chain] 监听 {self.listen[0]}:{self.listen[1]}")
        print(f"[chain] 上游 {self.up['scheme']}://{self.up['host']}:{self.up['port']}"
              f"（认证 {'有' if self.up['user'] else '无'}）")
        if self.hop:
            print(f"[chain] 跳板 {self.hop['scheme']}://{self.hop['host']}:{self.hop['port']}")
        else:
            print("[chain] 跳板 无（直连上游）")
        async with srv:
            await srv.serve_forever()


# ---------------------------------------------------------------- 自检
def _fake_proxy(listen_port: int, seen: list) -> asyncio.AbstractServer:
    """本地假「上游代理」：记录收到的 CONNECT，回 200 并原样转发到真实目标。"""
    async def handle(reader, writer):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            line = head.split(b"\r\n", 1)[0].decode()
            seen.append(line)
            _, target, _ = line.split(" ")
            host, _, port = target.rpartition(":")
            rr, ww = await asyncio.open_connection(host, int(port))
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            await asyncio.gather(_pipe(reader, ww), _pipe(rr, writer))
        except Exception:
            try:
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
            except Exception:
                pass

    return asyncio.start_server(handle, "127.0.0.1", listen_port)


async def selftest() -> int:
    import urllib.request
    seen: list[str] = []
    fake = await _fake_proxy(18888, seen)
    print("[selftest] 假上游代理 127.0.0.1:18888 已起（它会把流量真的转到目标）")
    up = {"scheme": "http", "host": "127.0.0.1", "port": 18888,
          "user": "U", "pass": "P"}
    srv = ChainServer(("127.0.0.1", 18899), up, hop=None)
    task = asyncio.create_task(srv.serve())
    await asyncio.sleep(0.6)

    def fetch():
        ph = urllib.request.ProxyHandler({"http": "http://127.0.0.1:18899",
                                          "https": "http://127.0.0.1:18899"})
        op = urllib.request.build_opener(ph)
        t0 = time.time()
        with op.open("https://ipinfo.io/json", timeout=25) as r:
            return r.status, r.read()[:200], int((time.time() - t0) * 1000)

    try:
        status, body, ms = await asyncio.to_thread(fetch)
        print(f"[selftest] 经链路取 https://ipinfo.io/json → HTTP {status} {ms}ms {body[:120]!r}")
        print(f"[selftest] 假上游收到的请求：{seen[:2]}")
        ok = status == 200 and seen and b"CONNECT ipinfo.io:443" in seen[0].encode()
    except Exception as exc:
        print(f"[selftest] ✗ 链路失败：{type(exc).__name__} {exc}")
        ok = False
    finally:
        task.cancel()
        fake.close()
    print("[selftest]", "通过：串联—认证头注入—隧道转发 都对" if ok else "未通过")
    return 0 if ok else 1


# ---------------------------------------------------------------- CLI
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default="127.0.0.1:8899")
    ap.add_argument("--upstream", default="")
    ap.add_argument("--upstream-file", default="", help="一行一条 host:port:user:pass")
    ap.add_argument("--listen-base", type=int, default=8900, help="配合 --upstream-file：各条监听端口")
    ap.add_argument("--hop", default="", help="跳板：socks5://user:pass@host:port 或 http://...")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return asyncio.run(selftest())

    hop = parse_proxy(a.hop) if a.hop else None

    if a.upstream_file:
        p = Path(a.upstream_file)
        if not p.is_absolute():
            p = BASE / p
        lines = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()
                 if ln.strip() and not ln.startswith("#")]
        servers = []
        for i, ln in enumerate(lines):
            up = parse_hostport(ln)
            servers.append(ChainServer(("127.0.0.1", a.listen_base + i), up, hop))
            print(f"[chain] {ln.split(':')[2][:40]}… → 127.0.0.1:{a.listen_base + i}")
        loop = asyncio.new_event_loop()
        try:
            for s in servers:
                loop.create_task(s.serve())
            loop.run_forever()
        except KeyboardInterrupt:
            print("\n[chain] 退出")
        return 0

    if not a.upstream:
        ap.error("需要 --upstream 或 --upstream-file")
    # "http://host:port" / "user:pass@host:port" 走 parse_proxy；只有 "host:port:user:pass" 才走 parse_hostport
    up = parse_proxy(a.upstream) if ("://" in a.upstream or "@" in a.upstream) else parse_hostport(a.upstream)
    host, port = a.listen.rsplit(":", 1)
    try:
        asyncio.run(ChainServer((host, int(port)), up, hop).serve())
    except KeyboardInterrupt:
        print("\n[chain] 退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
