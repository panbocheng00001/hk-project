import json, sys
from pathlib import Path

d = json.loads(Path("tools/cft_versions.json").read_text(encoding="utf-8"))
vers = d.get("versions", [])
print(f"总版本数: {len(vers)}")

targets = {}
for v in vers:
    ver = v.get("version", "")
    major = ver.split(".")[0]
    if major not in ("120", "131", "133"):
        continue
    dl = v.get("downloads", {}).get("chrome", [])
    url = ""
    for x in dl:
        if x.get("platform") == "win64":
            url = x.get("url", "")
            break
    if url:
        cur = targets.get(major)
        # 取同 major 里版本号最大的
        if not cur or [int(p) for p in ver.split(".")] > [int(p) for p in cur[0].split(".")]:
            targets[major] = (ver, url)

for major in sorted(targets):
    ver, url = targets[major]
    print(f"{major}: {ver}\n    {url}")
