# -*- coding: utf-8 -*-
"""fetch_proxy.py —— 巨量IP（juliangip）取号 + 校验 + 落盘，一条命令。

为什么要封装：
    1) 取一次 IP 就是一次计费，不能边试边刷 —— 所以「取 → 验 → 写」必须一次做完；
    2) 接口返回的是纯文本 `ip:port`，直接粘进 proxies.txt 会漏掉 `http://` 前缀，
       而 token_pool.load_proxies() 是原样喂给 Chrome 的，少前缀直接 ERR_PROXY；
    3) 取到的 IP 未必能用（出口可能是机房/被判死），必须先过 proxy_check 的四道门。

用法：
    python fetch_proxy.py                 # 只取 + 验，不写文件
    python fetch_proxy.py --write         # 取 + 验，通过后覆盖写 proxies.txt
    python fetch_proxy.py --write --keep  # 取 + 验，追加到 proxies.txt 末尾
    python fetch_proxy.py --num 2 --write # 一次取 2 个

退出码：0 = 取到且验证通过；2 = 取到但验证不过；1 = 接口本身失败。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent

API = "http://v2.api.juliangip.com/company/postpay/getips"
PARAMS = {
    "auto_white": "1",     # 自动把本机出口 IP 加进白名单（否则来源 IP 直接被拒）
    "city": "深圳",        # 只要深圳出口（abs*.td.gov.hk 对大陆移动家宽最友好）
    "filter": "1",         # 过滤掉不可用的
    "isp": "3",            # 运营商：3 = 移动
    "num": "1",
    "pt": "1",             # 代理协议：1 = HTTP
    "result_type": "text",
    "split": "1",          # 多条用换行分隔，正好对上 proxies.txt 的格式
    "trade_no": "6280170248860427",
    "sign": "0080489861adf7c21cf67e611789576f",
}


def fetch(num: int | None = None) -> list[str]:
    """调接口，返回 `['117.68.5.89:37269', ...]`。"""
    p = dict(PARAMS)
    if num:
        p["num"] = str(num)
    url = API + "?" + urllib.parse.urlencode(p, encoding="utf-8")
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0", "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode("utf-8", "replace").strip()
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    out = [ln for ln in lines if ":" in ln and not ln.startswith("{")]
    if not out:
        # 出错时接口会回 JSON：{"code":..,"msg":..}
        raise RuntimeError(f"接口没给 IP，原始返回：{body[:400]}")
    return out


def check(lines: list[str]) -> tuple[int, str]:
    """跑 proxy_check.py 做四道门体检，返回 (退出码, 输出)。"""
    args = [sys.executable, "-X", "utf8", str(BASE / "proxy_check.py")]
    for ln in lines:
        args += ["--line", ln]
    env = {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    import os
    e = dict(os.environ)
    e.update(env)
    r = subprocess.run(args, cwd=str(BASE), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=e, timeout=300)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--num", type=int, default=0)
    ap.add_argument("--write", action="store_true", help="验证通过后写 proxies.txt")
    ap.add_argument("--keep", action="store_true", help="追加而不是覆盖")
    ap.add_argument("--out", default="proxies.txt")
    a = ap.parse_args()

    print("=" * 78)
    print("取号：", API)
    try:
        raw = fetch(a.num or None)
    except Exception as ex:
        print(f"❌ 取号失败：{ex}")
        return 1

    print(f"✅ 取到 {len(raw)} 个：")
    for x in raw:
        print("   ", x)

    urls = [x if x.startswith("http") else f"http://{x}" for x in raw]

    print("-" * 78)
    code, out = check(urls)
    print(out.strip())

    ok = code == 0 and "可用 1 /" in out.replace(f"可用 {len(urls)} /", "可用 1 /")
    if "可用 0 /" in out:
        ok = False

    if not ok:
        print("-" * 78)
        print("⚠ 体检没过 —— 没写文件。原始 IP 仍在上面，需要的话手粘。")
        return 2

    if a.write:
        p = BASE / a.out
        old = []
        if a.keep and p.exists():
            old = [ln.strip() for ln in p.read_text(encoding="utf-8-sig").splitlines()
                   if ln.strip() and not ln.strip().startswith("#")]
        lines = old + urls
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("-" * 78)
        print(f"✅ 已写入 {p}（{len(lines)} 条）")
    else:
        print("-" * 78)
        print("（--write 才会落盘；上面这些行可以直接贴进 proxies.txt）")

    print(json.dumps({"ok": True, "proxies": urls}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
