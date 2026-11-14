# -*- coding: utf-8 -*-
"""proxy_diag.py —— 把代理的 403 拆开看（到底是认证、白名单，还是端口不对）
=================================================================================
curl 只会告诉你 "CONNECT tunnel failed, response 403"，信息量为零。这里直接裸 socket
发 CONNECT，把代理回的**响应头和响应体**打出来，结论就明确了：

    407 Proxy Authentication Required  → 没带认证
    403 + "invalid user/password"      → 认证信息不对
    403 + "IP not allowed/whitelist"   → 需要先加白名单
    403 + "zone/plan"                  → 套餐里没这个区域
    连不上 / RST                        → 端口不对或被封

同时对比三种认证写法：不认证 / user（空密码）/ user:任意密码，以及 SOCKS5 的握手结果。
"""
from __future__ import annotations

import base64
import socket
import ssl
import sys
import time

HOST = "us.ipwo.net"
PORT = 7878
USER = "_custom_zone_HK_sid_93583746_time_10"


def tcp(host, port, timeout=12.0):
    t0 = time.time()
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        return s, int((time.time() - t0) * 1000), ""
    except Exception as exc:
        return None, int((time.time() - t0) * 1000), f"{type(exc).__name__}: {exc}"


def connect_probe(auth: str | None):
    s, ms, err = tcp(HOST, PORT)
    if not s:
        return f"TCP 连接失败（{ms}ms）{err}"
    try:
        hdr = [f"CONNECT ipinfo.io:443 HTTP/1.1", "Host: ipinfo.io:443",
               "User-Agent: curl/8.4.0", "Proxy-Connection: Keep-Alive"]
        if auth is not None:
            b = base64.b64encode(auth.encode()).decode()
            hdr.append(f"Proxy-Authorization: Basic {b}")
        s.sendall(("\r\n".join(hdr) + "\r\n\r\n").encode())
        s.settimeout(12.0)
        buf = b""
        try:
            while len(buf) < 4096:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
                if b"\r\n\r\n" in buf:
                    # 再等一点点看有没有响应体
                    s.settimeout(1.2)
                    try:
                        buf += s.recv(4096)
                    except Exception:
                        pass
                    break
        except socket.timeout:
            pass
        txt = buf.decode("utf-8", "replace").strip()
        first = txt.splitlines()[0] if txt else "(无响应)"
        body = txt.split("\r\n\r\n", 1)[1].strip() if "\r\n\r\n" in txt else ""
        return f"TCP {ms}ms | {first}" + (f" | body={body[:200]}" if body else "")
    finally:
        s.close()


def socks5_probe(auth: tuple[str, str] | None):
    s, ms, err = tcp(HOST, PORT)
    if not s:
        return f"TCP 连接失败（{ms}ms）{err}"
    try:
        s.settimeout(10)
        s.sendall(b"\x05\x01\x00" if not auth else b"\x05\x02\x00\x02")
        r = s.recv(2)
        if len(r) < 2:
            return f"握手无响应（{r!r}）"
        if r[0] != 5:
            return f"不是 SOCKS5（首字节 {r[0]}，可能是 HTTP 代理端口）"
        method = r[1]
        if method == 0x02:
            if not auth:
                return "SOCKS5 需要认证（本探针未带）"
            u, p = auth
            s.sendall(bytes([5, len(u)]) + u.encode() + bytes([len(p)]) + p.encode())
            rr = s.recv(2)
            return f"SOCKS5 认证结果 {rr!r}（0x00 = 通过）"
        return f"SOCKS5 无需认证（method={method}）"
    finally:
        s.close()


def main() -> int:
    print(f"目标代理 {HOST}:{PORT}  用户 {USER}")
    print("-" * 70)
    print("[1] 不认证 CONNECT :", connect_probe(None))
    print("[2] 空密码 CONNECT :", connect_probe(f"{USER}:"))
    print("[3] 任意密码 CONNECT:", connect_probe(f"{USER}:x"))
    print("[4] SOCKS5 握手    :", socks5_probe(None))
    print("[5] SOCKS5 带认证  :", socks5_probe((USER, "")))
    print("-" * 70)

    # 顺便确认这块域名解析到哪、是不是同一台机
    import socket as sk
    try:
        infos = sk.getaddrinfo(HOST, None)
        ips = sorted({i[4][0] for i in infos})
        print("DNS:", ", ".join(ips))
    except Exception as exc:
        print("DNS 解析失败:", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
