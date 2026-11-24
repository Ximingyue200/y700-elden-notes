#!/usr/bin/env python3
"""把新的 vendor 镜像写入 super.img 的 vendor_a extents。

用法:
  python super_patch_vendor.py super.img new_vendor.img vendor_extents.txt out.img
vendor_extents.txt 每行: start_sector end_sector physical_sector
（由 `lpdump super.img` 中 vendor_a 的 Extents 段落得到，数值为 512 字节扇区）
"""
import shutil, sys

def main():
    super_img, vendor_img, extents_file, out_img = sys.argv[1:5]
    ext = []
    with open(extents_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            a, b, off = map(int, line.split())
            ext.append((a, b, off))

    need = sum(b - a + 1 for a, b, _ in ext) * 512
    import os
    vsize = os.path.getsize(vendor_img)
    if vsize > need:
        raise SystemExit(f"vendor 镜像 {vsize} 大于 extents 总大小 {need}")

    print(f"copying {super_img} -> {out_img} ...")
    shutil.copyfile(super_img, out_img)
    v = open(vendor_img, "rb")
    with open(out_img, "r+b") as f:
        for a, b, off in ext:
            n = (b - a + 1) * 512
            chunk = v.read(n)
            if len(chunk) < n:
                chunk += b"\x00" * (n - len(chunk))
            f.seek(off * 512)
            f.write(chunk)
    v.close()
    print("done")

if __name__ == "__main__":
    main()
