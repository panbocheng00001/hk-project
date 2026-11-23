#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对已打补丁的 Undetectable 本机 REST API 批量创建 20 个「不同指纹环境」的 profile，并可选启动成窗口。

用法:
  python create20.py                       # 自动探测 apiPort，创建 20 个，不启动
  python create20.py --port 3001           # 指定端口
  python create20.py --start               # 创建后逐个 /profile/start/<id> 开窗口
  python create20.py --count 20 --start --url https://ipinfo.io
  python create20.py --dry-run             # 只打印将要发送的 JSON

接口（本机 127.0.0.1:<apiPort>）:
  GET  /status                       探活
  GET  /timezoneslist                可用时区
  GET  /configslist                  可用 config id
  POST /profile/create   body=JSON   返回 {"profile_id": "..."} 或 {"error": "..."}
  GET  /profile/start/<id>           启动（开窗口）
  GET  /profile/stop/<id>            停止
  GET  /list                         profile 列表
"""
import argparse, json, re, subprocess, sys, time, urllib.request, urllib.error

try:                                   # Windows 控制台默认 GBK，强制 UTF-8 输出
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ---------------------------------------------------------------- 指纹矩阵
# 20 个互不相同的环境：os / 浏览器 / 语言 / 时区 / 经纬度 / 分辨率 / CPU / 内存
FP = [
    # (os, browser, language, timezone, "lat:lon", resolution, cpu, memory)
    ("windows", "chrome",  "en-US", "America/New_York",    "40.7128:-74.0060",  "1920x1080", 8,  16),
    ("windows", "edge",    "en-GB", "Europe/London",       "51.5074:-0.1278",   "1600x900",  12, 16),
    ("windows", "firefox", "de-DE", "Europe/Berlin",       "52.5200:13.4050",   "1366x768",  4,  8),
    ("windows", "yandex",  "ru-RU", "Europe/Moscow",       "55.7558:37.6173",   "1536x864",  8,  12),
    ("windows", "opera",   "pl-PL", "Europe/Warsaw",       "52.2297:21.0122",   "1440x900",  6,  8),
    ("windows", "chrome",  "fr-FR", "Europe/Paris",        "48.8566:2.3522",    "1680x1050", 16, 32),
    ("windows", "chrome",  "es-ES", "Europe/Madrid",       "40.4168:-3.7038",   "1280x720",  8,  8),
    ("windows", "edge",    "it-IT", "Europe/Rome",         "41.9028:12.4964",   "1920x1200", 12, 16),
    ("mac",     "safari",  "en-US", "America/Los_Angeles", "34.0522:-118.2437", "2560x1440", 10, 16),
    ("mac",     "chrome",  "en-CA", "America/Toronto",     "43.6532:-79.3832",  "2880x1800", 8,  16),
    ("mac",     "safari",  "ja-JP", "Asia/Tokyo",          "35.6762:139.6503",  "1920x1080", 8,  8),
    ("mac",     "chrome",  "ko-KR", "Asia/Seoul",          "37.5665:126.9780",  "1680x1050", 6,  8),
    ("linux",   "chrome",  "de-AT", "Europe/Vienna",       "48.2082:16.3738",   "1920x1080", 8,  16),
    ("linux",   "firefox", "nl-NL", "Europe/Amsterdam",    "52.3676:4.9041",    "1440x900",  4,  8),
    ("linux",   "chrome",  "pt-BR", "America/Sao_Paulo",   "-23.5505:-46.6333", "1366x768",  8,  8),
    ("linux",   "chrome",  "es-MX", "America/Mexico_City", "19.4326:-99.1332",  "1280x800",  6,  8),
    ("android", "chrome",  "en-IN", "Asia/Kolkata",        "19.0760:72.8777",   "1080x2340", 8,  6),
    ("android", "chrome",  "th-TH", "Asia/Bangkok",        "13.7563:100.5018",  "1440x3200", 8,  8),
    ("iphone",  "safari",  "zh-CN", "Asia/Shanghai",       "31.2304:121.4737",  "1170x2532", 6,  4),
    ("iphone",  "safari",  "ar-AE", "Asia/Dubai",          "25.2048:55.2708",   "1284x2778", 6,  6),
]

def build_body(i, start_url=None):
    os_, bro, lang, tz, geo, res, cpu, mem = FP[i]
    body = {
        "unique_name": True,                 # 交给服务端做重名校验
        "name": "fp_%02d_%s_%s" % (i + 1, os_, bro),
        "os": os_,
        "browser": bro,
        "language": lang,
        "timezone": tz,
        "geolocation": geo,                  # 支持 "lat:lon" / "off" / "auto"
        "auto_allow_geo": True,
        "resolution": res,
        "cpu": cpu,
        "memory": mem,
        "type": "local",
        "notes": "api-created fingerprint %02d" % (i + 1),
    }
    if start_url:
        body["start-pages"] = start_url
    return body

# ---------------------------------------------------------------- HTTP
def call(base, path, body=None, timeout=20):
    url = base + path
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
    except Exception as e:
        return {"__transport_error": str(e)}
    try:
        return json.loads(raw)
    except Exception:
        return {"__raw": raw}

def detect_port():
    """找 Undetectable.exe 的监听端口，逐个探 /status"""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Undetectable.exe",
                              "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    except Exception:
        return None
    pids = set(re.findall(r'"[^"]*Undetectable\.exe","(\d+)"', out, re.I))
    if not pids:
        return None
    try:
        net = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout
    except Exception:
        return None
    cands = set()
    for line in net.splitlines():
        f = line.split()
        if len(f) >= 5 and f[3].upper() == "LISTENING" and f[4] in pids:
            host, _, port = f[1].rpartition(":")
            if host in ("127.0.0.1", "0.0.0.0", "[::]", "[::1]", "localhost"):
                cands.add(int(port))
    for p in sorted(cands):
        st = call("http://127.0.0.1:%d" % p, "/status")
        if "__transport_error" not in st:
            return p
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--start", action="store_true", help="创建后启动每个 profile（开窗口）")
    ap.add_argument("--url", default=None, help="启动页，例如 https://ipinfo.io")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default="create20-report.json")
    a = ap.parse_args()

    n = min(a.count, len(FP))
    bodies = [build_body(i, a.url) for i in range(n)]
    if a.dry_run:
        for b in bodies:
            print(json.dumps(b, ensure_ascii=False))
        return 0

    port = a.port or detect_port()
    if not port:
        print("[!] 没探测到 API 端口：确认 Undetectable 正在运行且设置里已开启本地 API，再用 --port 指定")
        return 2
    base = "http://127.0.0.1:%d" % port
    print("[*] API: %s" % base)
    st = call(base, "/status")
    print("[*] /status -> %s" % json.dumps(st, ensure_ascii=False)[:300])
    tzs = call(base, "/timezoneslist")
    if isinstance(tzs, list) and tzs:
        print("[*] /timezoneslist: %d 项，示例 %s" % (len(tzs), tzs[:3]))

    ok, ids, fail = 0, [], []
    for b in bodies:
        r = call(base, "/profile/create", b)
        pid = r.get("profile_id") or r.get("id")
        if pid:
            ok += 1
            ids.append({"name": b["name"], "id": pid, "fp": {k: b[k] for k in
                        ("os", "browser", "language", "timezone", "geolocation", "resolution")}})
            print("[+] %-24s -> %s" % (b["name"], pid))
        else:
            fail.append({"name": b["name"], "resp": r})
            print("[-] %-24s -> %s" % (b["name"], json.dumps(r, ensure_ascii=False)[:200]))
        time.sleep(0.15)

    print("\n[*] 创建成功 %d / %d" % (ok, n))

    started = 0
    if a.start and ids:
        print("[*] 启动窗口 ...")
        for item in ids:
            r = call(base, "/profile/start/%s" % item["id"])
            item["start"] = r
            good = not r.get("error") and "__transport_error" not in r
            started += 1 if good else 0
            print("    start %-24s %s" % (item["name"], json.dumps(r, ensure_ascii=False)[:120]))
            time.sleep(0.4)
        print("[*] 启动成功 %d / %d" % (started, len(ids)))

    report = {"api": base, "created": ids, "failed": fail}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("[*] 报告: %s" % a.out)
    lst = call(base, "/list")
    if isinstance(lst, list):
        print("[*] /list 现在共 %d 个 profile" % len(lst))
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
