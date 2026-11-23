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
from cleanup import apply_cleanup  # noqa: E402
from matelab_client import MatElabClient, MatElabError, load_config  # noqa: E402


def snapshot(client: MatElabClient, cfg: Dict[str, Any], uid: str) -> Optional[Dict[str, Any]]:
    for item in client.items(cfg["eln_name"], cfg.get("eln_owner")):
        if str(item.get("sn") or "") == uid:
            return item
    return None


def run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)

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
    parser.add_argument("--yes", action="store_true", help="真正执行（默认 dry-run）")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except MatElabError as exc:
        print(exc.describe(), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
