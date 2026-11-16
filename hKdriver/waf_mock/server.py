# -*- coding: utf-8 -*-
"""waf_mock/server.py —— 本地复刻「华为云 WAF 形状对齐滑块」，用于离线测求解器
=====================================================================
为什么要它：
    线上验证码一次只能试一轮，失败就换题、还会被 WAF 风控。
    这里按实测参数 1:1 复刻结构、几何、行程比与交互，求解器可以无限次跑。

几何与行为全部取自 2026-11-16 实测（见 ../验证码特征_华为WAF形状滑块.md）：
    canvas 420×428；滑轨 y=348，x0=24..x1=394；手柄直径 22，初始 cx=27.5
    Submit 中心 (210, 402)，尺寸 140×36
    行程比 0.317（手柄走 1px，图形走 0.317px）
    背景层是 CSS background-image（空心轮廓 + 深色网格），前景 canvas 只画实心 + 滑轨 + 按钮
    DOM 里同样有一个 #swap（换一题），用来验证求解器不会误点它

用例：
    python waf_mock/server.py --port 8766            # 打开 http://127.0.0.1:8766/
    python waf_mock/server.py --port 8766 --dir neg  # 反向行程（测方向自学习）

接口：
    GET  /               页面
    GET  /api/state      当前题目真值 + 计数（外部脚本断言用）
    POST /api/report     页面上报（题目真值 / 提交结果）
    POST /api/reset      清零计数
"""
from __future__ import annotations

import argparse
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOCK = threading.Lock()
STATE: dict = {
    "task": None,          # {"solid_x":.., "hollow_x":.., "fam":.., "prompt":.., "ratio":..}
    "attempts": 0,         # 提交次数
    "passed": 0,           # 通过次数
    "last_result": None,   # {"dx":.., "ok":.., "at":..}
    "dir": "pos",
}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def _json(self, obj, code: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/state"):
            with LOCK:
                return self._json(dict(STATE))
        return super().do_GET()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            data = {}
        with LOCK:
            if self.path.startswith("/api/report"):
                if data.get("task"):
                    STATE["task"] = data["task"]
                if data.get("result"):
                    STATE["last_result"] = data["result"]
                    if data["result"].get("ok"):
                        STATE["passed"] += 1
                    else:
                        STATE["attempts"] += 1
                return self._json({"ok": True})
            if self.path.startswith("/api/reset"):
                STATE.update(task=None, attempts=0, passed=0, last_result=None)
                return self._json({"ok": True})
        return self._json({"ok": False, "err": "unknown"}, 404)

    def log_message(self, fmt, *args):      # 静音，免得刷屏
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--dir", choices=["pos", "neg"], default="pos",
                    help="pos=手柄右移图形右移；neg=反向（测方向自学习）")
    a = ap.parse_args()
    STATE["dir"] = a.dir
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"WAF mock on http://127.0.0.1:{a.port}/  (dir={a.dir})")
    print(f"state: http://127.0.0.1:{a.port}/api/state")
    srv.serve_forever()


if __name__ == "__main__":
    main()
