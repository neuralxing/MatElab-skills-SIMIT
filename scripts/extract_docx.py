#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把实验原始材料抽取成 agent 可读的中间产物。

支持：
  - docx：按文档流顺序输出段落 + 表格 + 内嵌图片（图片顺序仅供参考）
  - md  ：解析 frontmatter、章节、表格、![[附件]] 引用并在搜索根中定位实体
  - 其他数据文件：--inspect 查看大小、编码、前几行、分隔符、数值列范围

用法：
    python -X utf8 extract_docx.py "报告.docx" -o "实验记录/_extract/xy101" --images
    python -X utf8 extract_docx.py "笔记.md" -o out --roots "D:/Obsidian/Attachments"
    python -X utf8 extract_docx.py "data.xy" --inspect
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".emf"}
TABLE_SEP = re.compile(r"^\|?[\s:\-|]+\|[\s:\-|]*$")
EMBED_DOC = re.compile(r"!\[\[([^\]]+)\]\]")
EMBED_MD = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


# --------------------------------------------------------------------------- #
# 通用
# --------------------------------------------------------------------------- #

def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:10]


def _write_json(path: Path, obj: Any) -> None:
    """frontmatter 里的 date/datetime 需要转字符串。"""
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def _markdown_table(rows: List[List[str]]) -> str:
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    norm = [list(r) + [""] * (width - len(r)) for r in rows]
    head = norm[0]
    lines = ["| " + " | ".join(str(c).replace("|", "\\|") for c in head) + " |"]
    lines.append("|" + "|".join(["---"] * width) + "|")
    for row in norm[1:]:
        lines.append("| " + " | ".join(str(c).replace("|", "\\|") for c in row) + " |")
    return "\n".join(lines)


sys.path.insert(0, str(Path(__file__).resolve().parent))
from matelab_client import resolve_attachment  # noqa: E402


# --------------------------------------------------------------------------- #
# docx
# --------------------------------------------------------------------------- #

def extract_docx(path: Path, out_dir: Path, want_images: bool) -> Dict[str, Any]:
    try:
        from docx import Document
        from docx.oxml.ns import qn
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("需要 python-docx：pip install python-docx") from exc

    doc = Document(str(path))
    img_dir = out_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)

    blocks: List[Dict[str, Any]] = []
    tables: List[List[List[str]]] = []
    images: List[Dict[str, Any]] = []
    md_lines: List[str] = []
    order_warning = False
    img_seq = 0

    body = doc.element.body
    for child in body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            from docx.text.paragraph import Paragraph
            para = Paragraph(child, doc)
            text = para.text.strip()
            style = (para.style.name or "") if para.style is not None else ""
            if text:
                if style.lower().startswith("heading"):
                    level = "".join(ch for ch in style if ch.isdigit()) or "1"
                    md_lines.append("#" * int(level) + " " + text)
                elif style.lower().startswith(("title", "subtitle")):
                    md_lines.append("## " + text)
                else:
                    md_lines.append(text)
                blocks.append({"type": "paragraph", "style": style, "text": text})
            if want_images:
                for blip in para._p.findall(".//" + qn("a:blip")):
                    rid = None
                    for key, value in blip.attrib.items():
                        if key.endswith("}embed") or key.endswith("}link"):
                            rid = value
                            break
                    if not rid or rid not in doc.part.related_parts:
                        continue
                    part = doc.part.related_parts[rid]
                    ext = Path(str(part.partname)).suffix.lower() or ".png"
                    img_seq += 1
                    filename = f"img_{img_seq:03d}{ext}"
                    (img_dir / filename).write_bytes(part.blob)
                    images.append({
                        "file": str((img_dir / filename).resolve()),
                        "after_text": text[:60],
                        "note": "顺序按段落近似，浮动图可能错位",
                    })
                    md_lines.append(f"![]({(img_dir / filename).name})")
                    blocks.append({"type": "image", "file": filename})
                    order_warning = True
        elif tag == "tbl":
            from docx.table import Table
            table = Table(child, doc)
            rows: List[List[str]] = []
            for row in table.rows:
                cells: List[str] = []
                for cell in row.cells:
                    cells.append(" ".join(cell.text.split()))
                rows.append(cells)
            tables.append(rows)
            blocks.append({"type": "table", "rows": rows})
            md_lines.append(_markdown_table(rows))
            md_lines.append("")

    text_md = "\n\n".join(md_lines).strip() + "\n"
    (out_dir / "text.md").write_text(text_md, encoding="utf-8")
    _write_json(out_dir / "tables.json", tables)

    result = {
        "source": str(path.resolve()),
        "kind": "docx",
        "sha1": _sha1(path),
        "size": path.stat().st_size,
        "blocks": blocks,
        "tables": tables,
        "images": images,
        "stats": {
            "paragraphs": sum(1 for b in blocks if b["type"] == "paragraph"),
            "tables": len(tables),
            "images": len(images),
            "image_order_warning": order_warning,
        },
        "note": "docx 图片顺序为按段落近似；浮动图片位置可能不准，需人工确认。",
    }
    return result


# --------------------------------------------------------------------------- #
# markdown
# --------------------------------------------------------------------------- #

def _split_frontmatter(text: str) -> tuple[Dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end = None
    for i in range(1, min(len(lines), 200)):
        if lines[i].strip() in ("---", "..."):
            end = i
            break
    if end is None:
        return {}, text
    raw = "\n".join(lines[1:end])
    try:
        import yaml
        meta = yaml.safe_load(raw) or {}
    except Exception:
        meta = {"_raw": raw}
    return meta, "\n".join(lines[end + 1:])


def _parse_md_tables(lines: List[str]) -> List[Dict[str, Any]]:
    tables: List[Dict[str, Any]] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("|") and i + 1 < len(lines) and TABLE_SEP.match(lines[i + 1].strip()):
            header = [c.strip() for c in line.strip("|").split("|")]
            rows: List[List[str]] = []
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                j += 1
            tables.append({"header": header, "rows": rows})
            i = j
            continue
        i += 1
    return tables


def extract_md(path: Path, out_dir: Path, roots: List[str]) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    meta, body = _split_frontmatter(text)
    lines = body.splitlines()

    sections: List[Dict[str, Any]] = []
    current = {"heading": "(root)", "level": 0, "lines": []}
    for line in lines:
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            if current["lines"]:
                current["text"] = "\n".join(current["lines"]).strip()
                sections.append(current)
            current = {"heading": m.group(2).strip(), "level": len(m.group(1)), "lines": []}
        else:
            current["lines"].append(line)
    if current["lines"]:
        current["text"] = "\n".join(current["lines"]).strip()
        sections.append(current)

    embeds: List[Dict[str, Any]] = []
    for m in EMBED_DOC.finditer(body):
        name = m.group(1)
        resolved = resolve_attachment(name, roots)
        embeds.append({
            "raw": m.group(0), "name": name, "resolved": resolved,
            "exists": bool(resolved),
            "kind": "doc" if Path(name.split("|")[0]).suffix.lower() not in IMAGE_EXTS else "image",
        })
    for m in EMBED_MD.finditer(body):
        name = m.group(1)
        resolved = resolve_attachment(name, roots)
        embeds.append({
            "raw": m.group(0), "name": name, "resolved": resolved,
            "exists": bool(resolved),
            "kind": "doc" if Path(name).suffix.lower() not in IMAGE_EXTS else "image",
        })

    tables = _parse_md_tables(lines)
    (out_dir / "text.md").write_text(body.strip() + "\n", encoding="utf-8")
    _write_json(out_dir / "tables.json", tables)

    return {
        "source": str(path.resolve()),
        "kind": "md",
        "sha1": _sha1(path),
        "size": path.stat().st_size,
        "frontmatter": meta,
        "sections": sections,
        "tables": tables,
        "embeds": embeds,
        "stats": {
            "sections": len(sections),
            "tables": len(tables),
            "embeds": len(embeds),
            "missing_embeds": sum(1 for e in embeds if not e["exists"]),
        },
    }


# --------------------------------------------------------------------------- #
# 其他数据文件
# --------------------------------------------------------------------------- #

def inspect_file(path: Path, max_lines: int = 15) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "source": str(path.resolve()),
        "kind": "data",
        "suffix": path.suffix.lower(),
        "size": path.stat().st_size,
        "sha1": _sha1(path),
    }
    if path.suffix.lower() in IMAGE_EXTS:
        info["kind"] = "image"
        try:
            from PIL import Image
            with Image.open(path) as im:
                info["image"] = {"format": im.format, "size": list(im.size), "mode": im.mode}
        except Exception as exc:
            info["image_error"] = str(exc)
        return info

    head = path.read_bytes()[:65536]
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            text = head.decode(enc)
            info["encoding"] = enc
            break
        except UnicodeDecodeError:
            continue
    else:
        info["encoding"] = None
        text = ""

    if text:
        lines = text.splitlines()[:max_lines]
        info["head"] = lines
        counts: Dict[str, int] = {}
        for sep_name, sep in (("comma", ","), ("tab", "\t"), ("semicolon", ";"), ("space", " ")):
            counts[sep_name] = sum(line.count(sep) for line in lines[:5])
        info["delimiters"] = counts
        info["columns_guess"] = max(counts, key=counts.get) if any(counts.values()) else None
        info["line_count_estimate"] = text.count("\n") + 1 if len(head) < 65536 else ">=64KB"

        # 数值列范围（仅简单空格/逗号分隔）
        sep = {"comma": ",", "tab": "\t", "semicolon": ";", "space": None}.get(
            info["columns_guess"] or "", None
        )
        rows = [line.split(sep) if sep else line.split() for line in text.splitlines()[:200]]
        rows = [r for r in rows if r and not r[0].startswith(("#", "!", "%"))]
        width = max((len(r) for r in rows), default=0)
        cols = []
        for c in range(min(width, 8)):
            values = []
            for r in rows:
                if len(r) <= c:
                    continue
                try:
                    values.append(float(r[c]))
                except (TypeError, ValueError):
                    continue
            if len(values) >= max(3, len(rows) // 2):
                cols.append({
                    "index": c,
                    "numeric_rows": len(values),
                    "min": min(values),
                    "max": max(values),
                })
        info["numeric_columns"] = cols
    return info


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="实验材料抽取（docx/md/数据文件）")
    parser.add_argument("input", help="输入文件")
    parser.add_argument("-o", "--out-dir", help="输出目录（默认 <输入同名>_extract）")
    parser.add_argument("--images", action="store_true", help="导出 docx 内嵌图片")
    parser.add_argument("--roots", action="append", default=None,
                        help="附件搜索根目录，可重复；默认用输入文件目录")
    parser.add_argument("--inspect", action="store_true", help="仅检查文件元信息")
    parser.add_argument("--json", action="store_true", help="把 extraction.json 打到 stdout")
    args = parser.parse_args(argv)

    path = Path(args.input)
    if not path.exists():
        print(f"输入不存在: {path}", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir) if args.out_dir else path.with_name(path.stem + "_extract")
    out_dir.mkdir(parents=True, exist_ok=True)
    roots = args.roots or [str(path.parent)]

    suffix = path.suffix.lower()
    if args.inspect:
        result = inspect_file(path)
    elif suffix == ".docx":
        result = extract_docx(path, out_dir, want_images=args.images)
    elif suffix in (".md", ".markdown"):
        result = extract_md(path, out_dir, roots)
    else:
        result = inspect_file(path)

    result["resolved_roots"] = roots
    _write_json(out_dir / "extraction.json", result)

    stats = result.get("stats") or {}
    print(f"[extract] {result['kind']} {path.name} -> {out_dir}")
    if stats:
        print("[extract] " + "  ".join(f"{k}={v}" for k, v in stats.items()))
    if result.get("kind") == "md":
        missing = [e["name"] for e in result["embeds"] if not e["exists"]]
        if missing:
            print("[extract] 未找到的附件：", file=sys.stderr)
            for name in missing:
                print("  - " + name, file=sys.stderr)
            print("[extract] 用 --roots 指定 Obsidian 附件目录后重试", file=sys.stderr)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
