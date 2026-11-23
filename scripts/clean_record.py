#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""对已写入的记录执行模板继承数据清理（清字段 + 删示例行）。

    python -X utf8 clean_record.py --config matelab.yaml --uid BP-144-20260916-01
    python -X utf8 clean_record.py --config matelab.yaml --uid A --uid B --yes
    python -X utf8 clean_record.py --config matelab.yaml --record BP-144.json --dry-run

默认只打印将要做什么（dry-run），加 --yes 才真正调用 /eln_api/update。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_record import build  # noqa: E402
from cleanup import apply_cleanup  # noqa: E402
from matelab_client import MatElabClient, MatElabError, load_config  # noqa: E402


def snapshot(client: MatElabClient, cfg: Dict[str, Any], uid: str) -> Optional[Dict[str, Any]]:
    for item in client.items(cfg["eln_name"], cfg.get("eln_owner")):
        if str(item.get("sn") or "") == uid:
            return item
    return None


def sync_note(cfg: Dict[str, Any], record_paths: List[str]) -> int:
    """把 record.json 里的 Note 重新写回记录，覆盖模板预填的默认文字。"""
    spec = (cfg.get("field_map") or {}).get("_derived.note") or {}
    module = spec.get("module")
    field = spec.get("field") or "Note"
    if not module:
        print("field_map._derived.note 未配置，无法同步 Note", file=sys.stderr)
        return 2

    client = MatElabClient(cfg, verbose=True)
    items = client.items(cfg["eln_name"], cfg.get("eln_owner"))
    sn_by_uid = {str(i.get("sn")): i for i in items}

    code = 0
    for record_path in record_paths:
        record = json.loads(Path(record_path).read_text(encoding="utf-8-sig"))
        payload, issues = build(record, cfg, check_attachments=False)
        if not issues.ok:
            print(f"[note] {record_path} 映射有错，跳过", file=sys.stderr)
            code = 2
            continue
        item = payload["dataset"][0]
        note = (item["data"].get(module) or {}).get(field)
        uid = item["uid"]
        if not note:
            print(f"[note] {uid}: record 里没有 Note 内容，跳过")
            continue
        if uid not in sn_by_uid:
            print(f"[note] {uid}: 线上找不到该记录，跳过", file=sys.stderr)
            code = 4
            continue
        client.update(cfg["eln_name"], uid, modify=[{"path": [module, field], "data": note}])
        print(f"[note] {uid}: 已重写 {module}/{field}（{len(note)} 字符）")
    return code


def run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)

    if args.sync_note:
        if not args.record:
            print("--sync-note 需要配合 --record 使用", file=sys.stderr)
            return 2
        if not args.yes:
            print("[note] 将要重写以下记录的 Note（dry-run，加 --yes 执行）：")
            for path in args.record:
                print("  -", path)
            return 0
        return sync_note(cfg, list(args.record))

    uids: List[str] = list(args.uid or [])
    for record_path in args.record or []:
        data = json.loads(Path(record_path).read_text(encoding="utf-8-sig"))
        uid = data.get("uid")
        if not uid:
            print(f"{record_path}: 缺 uid", file=sys.stderr)
            continue
        uids.append(str(uid))
    if not uids:
        print("需要 --uid 或 --record", file=sys.stderr)
        return 2

    client = MatElabClient(cfg, verbose=True)
    code = 0
    for uid in uids:
        item = snapshot(client, cfg, uid)
        if not item:
            print(f"[clean] 记录不存在：{uid}", file=sys.stderr)
            code = 4
            continue
        title = str(item.get("title") or "")
        print(f"\n[clean] {uid} | {title}")
        apply_cleanup(client, cfg, uid, title, verbose=True, dry_run=not args.yes)

    if not args.yes:
        print("\n[clean] 这是预览，未修改任何数据。确认后加 --yes。")
    return code


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="清理记录里继承的模板预填数据")
    parser.add_argument("--config", required=True, help="matelab.yaml")
    parser.add_argument("--uid", action="append", help="记录 UID（sn），可重复")
    parser.add_argument("--record", action="append", help="record.json，可重复（读取其 uid）")
    parser.add_argument("--sync-note", action="store_true",
                        help="用 --record 里的 narrative 重写 Note 富文本字段")
    parser.add_argument("--yes", action="store_true", help="真正执行（默认 dry-run）")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except MatElabError as exc:
        print(exc.describe(), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
