// _gen_golden.js —— 从本地复刻题服务生成 captcha-lab 的黄金样本
// 用途：tests/test_overlap.py 需要 tests/data/waf_canvas.png（seed=1000），
//      该文件不入库，按需由 captcha_mock 重新生成。
const fs = require("fs");
const path = require("path");

(async () => {
  const r = await fetch("http://127.0.0.1:8777/challenge?seed=1000");
  const j = await r.json();
  console.log("seed", j.seed, "bmp", j.bmpW + "x" + j.bmpH, "geom", JSON.stringify(j.geom));
  const b64 = String(j.dataUrl).split(",")[1];
  const out = path.resolve(__dirname, "..", "..", "captcha-lab", "tests", "data", "waf_canvas.png");
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, Buffer.from(b64, "base64"));
  console.log("->", out, fs.statSync(out).size, "B");
})().catch((e) => { console.error("ERR", e.message); process.exit(1); });
