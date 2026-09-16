#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pdf_to_images.py — 把 PDF 逐页转成图片，供错题复盘上传使用

为什么需要它：手机扫描整沓试卷时导出的是 PDF，而模型端点（deepseek-flash）
只收图片，不收 PDF。所以「传 PDF」必须在本地先转成页图。

依赖 PyMuPDF（fitz），本机 Python311 已装；不需要 poppler / LibreOffice。

用法：
  python pdf_to_images.py <pdf路径> <输出目录> [--max-pages 8] [--dpi 150] [--fmt jpg]

输出：stdout 打印一行 JSON（UTF-8），形如
  {"ok": true, "total_pages": 12, "converted": 8,
   "pages": [{"file": "xxx-p1.jpg", "page": 1, "width": 1240, "height": 1754, "bytes": 182344}]}

约定：
  · 只写文件到 <输出目录>，不碰数据库、不删原 PDF
  · 任何失败都返回 {"ok": false, "error": "..."} 且退出码非 0，方便上层提示用户
"""

import argparse
import json
import os
import sys

# Windows 控制台默认 GBK，直接 print 中文/特殊字符会 UnicodeEncodeError；
# 本脚本的输出是给 Node 读的 JSON，必须强制 UTF-8。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass


def emit(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False))
    sys.exit(code)


def convert(pdf_path, out_dir, max_pages, dpi, fmt):
    if not os.path.isfile(pdf_path):
        return {"ok": False, "error": f"PDF 不存在：{pdf_path}"}

    try:
        import fitz  # PyMuPDF
    except Exception as exc:
        return {
            "ok": False,
            "error": ("当前 Python 没有装 PyMuPDF（%s）。"
                      "请用带 fitz 的 Python 运行，或 pip install pymupdf" % exc),
        }

    os.makedirs(out_dir, exist_ok=True)

    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        return {"ok": False, "error": f"PDF 打不开（可能已加密或损坏）：{exc}"}

    try:
        if doc.needs_pass:
            return {"ok": False, "error": "PDF 已加密，无法转换"}

        total = doc.page_count
        if total == 0:
            return {"ok": False, "error": "PDF 没有页面"}

        n = min(total, max_pages)
        # 缩放矩阵：PyMuPDF 默认 72dpi，按目标 dpi 放大。
        # dpi 150 足够看清手写字，又不会把 20 页卷子撑成几十 MB。
        zoom = max(1.0, dpi / 72.0)
        matrix = fitz.Matrix(zoom, zoom)

        base = os.path.splitext(os.path.basename(pdf_path))[0]
        # 文件名里可能带中文和空格，压平一下，避免 URL 编码之外的麻烦
        safe = "".join(c if (c.isalnum() or c in "-_.") else "-" for c in base)[:60] or "page"
        ext = ".jpg" if fmt == "jpg" else ".png"

        pages = []
        for i in range(n):
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            fname = f"{safe}-p{i + 1}{ext}"
            fpath = os.path.join(out_dir, fname)
            if fmt == "jpg":
                # jpg_quality 是 PyMuPDF 较新版本参数；旧版只有 save 的默认压缩，
                # 传参失败就退回不带质量，保证兼容性。
                try:
                    pix.save(fpath, jpg_quality=82)
                except TypeError:
                    pix.save(fpath)
            else:
                pix.save(fpath)

            pages.append({
                "file": fname, "page": i + 1,
                "width": pix.width, "height": pix.height,
                "bytes": os.path.getsize(fpath),
            })

        return {
            "ok": True, "total_pages": total, "converted": n,
            "pages": pages, "skipped": max(0, total - n),
        }
    finally:
        doc.close()


def main():
    ap = argparse.ArgumentParser(description="PDF 逐页转图片（PyMuPDF）")
    ap.add_argument("pdf")
    ap.add_argument("outdir")
    ap.add_argument("--max-pages", type=int, default=8,
                    help="最多转几页（默认 8；一次喂给模型太多图既慢又贵）")
    ap.add_argument("--dpi", type=int, default=150, help="渲染精度，默认 150")
    ap.add_argument("--fmt", choices=["jpg", "png"], default="jpg")
    args = ap.parse_args()

    max_pages = max(1, min(20, args.max_pages))
    dpi = max(72, min(300, args.dpi))
    result = convert(args.pdf, args.outdir, max_pages, dpi, args.fmt)
    emit(result, 0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
