#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Undetectable.exe 客户端限制绕过补丁（可换机器使用）

用法（在目标电脑上）:
  python patch_undetectable.py                              # 用它内置的默认路径
  python patch_undetectable.py --src "C:\\path\\Undetectable.exe"
  python patch_undetectable.py --src Undetectable.exe --out .\\out\\Undetectable.exe
  python patch_undetectable.py --check-only                 # 只校验版本/字节，不改文件

说明:
  * 会先核对源文件 sha256 与每个补丁点的原始字节；不一致就跳过该补丁并明确报错。
  * 输出到 --out（默认 <源目录>\\out\\Undetectable.exe），从不覆盖源文件。
"""
import argparse, hashlib, os, shutil, sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ORIG_SHA256 = "aa0797b3cf8d2ab99d58f51a29f267d10a67d503d1095510e766b6cb17a9946a"
DEFAULT_SRC = r"C:\Users\Administrator\.dsh\attachments\v1\files\aa\aa0797b3cf8d2ab99d58f51a29f267d10a67d503d1095510e766b6cb17a9946a\Undetectable.exe"

# VA -> 文件偏移: file_off = VA - 0x140001000 + 0x400
def va2off(va):
    return va - 0x140001000 + 0x400

PATCHES = [
    # (名称, VA, 期望原字节, 新字节, 说明, 是否必选)
    ("P1 isLimitReached", 0x140213400,
     bytes.fromhex("80791000" "740b8b41" "18394114" "7c03b001"),
     bytes.fromhex("32c0c3"),
     "LimitsObserver::isLimitReached() 恒返回 false：每日建号额度失效（UI + REST）", True),
    ("P2 isFree", 0x1401FFC04,
     bytes.fromhex("e807feecff"),
     bytes.fromhex("31c0909090"),
     "跳过 Plan-name==\"free\" 比较，account->isFree=0：解除 Free 版功能锁 / 启动分支", True),
    ("P3 perm_create", 0x14023B76A,
     bytes.fromhex("7519"),
     bytes.fromhex("eb19"),
     "REST /profile/create 权限判断 jne→jmp：不再要求 profile_create_local 令牌", True),
]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=DEFAULT_SRC, help="原始 Undetectable.exe 路径")
    ap.add_argument("--out", default=None, help="输出路径（默认 <src 同目录>/out/Undetectable.exe）")
    ap.add_argument("--check-only", action="store_true", help="只校验，不写文件")
    a = ap.parse_args()

    src = os.path.abspath(a.src)
    if not os.path.isfile(src):
        print("[!] 源文件不存在: %s" % src)
        return 2
    out = os.path.abspath(a.out or os.path.join(os.path.dirname(src), "out", "Undetectable.exe"))

    data = bytearray(open(src, "rb").read())
    sha = hashlib.sha256(data).hexdigest()
    print("[i] 源: %s" % src)
    print("[i] 大小: %d 字节" % len(data))
    print("[i] sha256: %s" % sha)
    if sha != ORIG_SHA256:
        print("[!] 与目标版本不一致（期望 %s）。" % ORIG_SHA256)
        print("    版本不同则补丁点字节可能不同，下面会逐点校验；不匹配的点会被跳过。")

    ok, bad = 0, 0
    for name, va, expect, new, desc, required in PATCHES:
        off = va2off(va)
        cur = bytes(data[off:off + len(expect)])
        if cur == new:
            print("[=] %-18s VA=0x%X off=0x%X 已经是补丁后字节，跳过" % (name, va, off))
            ok += 1
            continue
        if cur != expect:
            print("[!] %-18s VA=0x%X off=0x%X 原字节不符：期望 %s 实际 %s —— 跳过"
                  % (name, va, off, expect.hex(), cur.hex()))
            bad += 1
            continue
        if not a.check_only:
            data[off:off + len(new)] = new
        print("[+] %-18s VA=0x%X off=0x%X  %s -> %s  %s"
              % (name, va, off, expect.hex(), new.hex(), desc))
        ok += 1

    if a.check_only:
        print("\n[i] 校验模式：未写文件。可直接打补丁的点: %d，不匹配: %d" % (ok, bad))
        return 0 if bad == 0 else 1

    if bad:
        print("\n[!] 有 %d 个补丁点不匹配，说明目标不是同一版本，已中止（未写文件）。" % bad)
        return 1

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "wb") as f:
        f.write(data)
    new_sha = hashlib.sha256(data).hexdigest()
    print("\n[✓] 已写入: %s" % out)
    print("[✓] 补丁后 sha256: %s" % new_sha)
    if new_sha == "98f892ddbbb8126a871efcff5c798cb788f07869994bc8c0dae9ddd9db8811e0":
        print("[✓] 与已知结果一致（3 个补丁全成功）")
    return 0

if __name__ == "__main__":
    sys.exit(main())
