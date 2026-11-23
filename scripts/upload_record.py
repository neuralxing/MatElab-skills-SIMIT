#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""主 CLI：把 record.json 写入 MatElab。

    python -X utf8 upload_record.py --record record.json --config matelab.yaml --dry-run
    python -X utf8 upload_record.py --record record.json --config matelab.yaml --yes
    python -X utf8 upload_record.py --record record.json --config matelab.yaml --yes --update

安全默认：不带 --yes 时只做本地校验与预览，不联网、不写入。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_record import Issues, build, preview  # noqa: E402
from cleanup import apply_cleanup  # noqa: E402
from matelab_client import MatElabClient, MatElabError, load_config  # noqa: E402

MANIFEST_FIELDS = [
    "time", "uid", "record_ids", "eln", "template", "title",
    "attachments", "status", "note", "source",
]


def _manifest_path(cfg: Dict[str, Any]) -> Path:
    return Path(cfg["_token_dir"]) / "upload_manifest.csv"


def _append_manifest(cfg: Dict[str, Any], row: Dict[str, Any]) -> None:
    path = _manifest_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=MANIFEST_FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in MANIFEST_FIELDS})


def _receipt(cfg: Dict[str, Any], payload: Dict[str, Any], uploaded: List[Dict[str, Any]],
             response: Dict[str, Any], verify: Optional[Dict[str, Any]]) -> Path:
    uid = payload["dataset"][0]["uid"]
    out_dir = Path(cfg["_token_dir"]) / "records"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{uid}.md"
    item = payload["dataset"][0]
    lines = [
        f"# MatElab 上传回执：{item.get('title')}",
        "",
        f"- uid: `{uid}`",
        f"- 记录本: {payload.get('eln')}",
        f"- 模板: {payload.get('template')}",
        f"- 记录 ID: {', '.join(str(x) for x in response.get('id') or [])}",
        f"- 时间: {dt.datetime.now().isoformat(timespec='seconds')}",
        "",
        "## 字段摘要",
        "",
    ]
    for module, value in item.get("data", {}).items():
        if isinstance(value, str):
            import re
            text = re.sub(r"<[^>]+>", " ", value)
            lines.append(f"- **{module}**: {' '.join(text.split())[:180]}")
        elif isinstance(value, list):
            lines.append(f"- **{module}**: {len(value)} 个文件")
        elif isinstance(value, dict):
            if all(isinstance(v, list) for v in value.values()) and value:
                lines.append(f"- **{module}**: 表格 {max(len(v) for v in value.values())} 行")
            else:
                pairs = ", ".join(f"{k}={v}" for k, v in value.items() if v not in (None, ""))
                lines.append(f"- **{module}**: {pairs}")
    if uploaded:
        lines += ["", "## 附件", "", "| 引用名 | 原文件 | 大小 |", "|---|---|---|"]
        for item_up in uploaded:
            lines.append(
                f"| `{item_up.get('name')}` | {item_up.get('filename')} | "
                f"{item_up.get('size', '')} |"
            )
    if verify is not None:
        lines += ["", "## 回读校验", ""]
        if verify.get("checked"):
            for key, ok in verify["checked"].items():
                lines.append(f"- {'OK ' if ok else '不一致 '} {key}")
        if verify.get("error"):
            lines.append(f"- 校验失败: {verify['error']}")
    lines += ["", "## 原始 record.json", "", f"`{payload['_meta'].get('source_record')}`", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _replace_ref(payload: Dict[str, Any], old_ref: str, new_ref: str) -> None:
    """把所有文件字段里的旧引用替换成服务器返回的真实引用名。"""
    data = payload["dataset"][0].get("data") or {}
    for module, value in data.items():
        if isinstance(value, dict):
            for key, field_value in value.items():
                if isinstance(field_value, list):
                    value[key] = [new_ref if x == old_ref else x for x in field_value]
        elif isinstance(value, list):
            for entry in value:
                if isinstance(entry, dict) and entry.get("file") == old_ref:
                    entry["file"] = new_ref


def _dedupe_path(cfg: Dict[str, Any]) -> Tuple[str, str]:
    """查重/删除用的定位字段：优先 dedupe_field，否则取 field_map 第一个表单字段。"""
    field = cfg.get("dedupe_field") or {}
    module = field.get("module")
    name = field.get("field")
    if module and name:
        return str(module), str(name)
    for spec in (cfg.get("field_map") or {}).values():
        if spec.get("module") and spec.get("field") and (spec.get("kind") or "form") == "form":
            return str(spec["module"]), str(spec["field"])
    raise SystemExit("config.field_map 里找不到可用于查重的表单字段")


def _find_existing(client: MatElabClient, cfg: Dict[str, Any], uid: str,
                   issues: Issues) -> List[str]:
    """查重：用 items 的 sn / keywords 精确匹配。

    注意：/eln_api/search 的 keyword 过滤在实测中并不可靠（会返回无关记录），
    所以不用关键字检索判定重复。
    """
    try:
        return client.find_record_uids(cfg["eln_name"], uid, user=cfg.get("eln_owner"))
    except MatElabError as exc:
        issues.warn(f"查重失败（继续尝试导入）：{exc.describe().splitlines()[0]}")
        return []


def _build_update_ops(payload: Dict[str, Any], issues: Issues) -> List[Dict[str, Any]]:
    """把 data 转成 /eln_api/update 的 modify 列表（表单/表格/富文本）。"""
    ops: List[Dict[str, Any]] = []
    for module, value in (payload["dataset"][0].get("data") or {}).items():
        if isinstance(value, str):
            ops.append({"path": [module], "data": value})
            continue
        if isinstance(value, list):
            issues.warn(f"更新模式暂不处理文件集模块「{module}」，请人工在网页补充")
            continue
        if isinstance(value, dict):
            is_table = all(isinstance(v, list) for v in value.values()) and value
            if is_table:
                for col, col_values in value.items():
                    ops.append({"path": [module, col], "data": col_values})
            else:
                for field, field_value in value.items():
                    if field_value is None:
                        continue
                    ops.append({"path": [module, field], "data": field_value})
    return ops


def _verify(client: MatElabClient, cfg: Dict[str, Any],
            payload: Dict[str, Any]) -> Dict[str, Any]:
    """回读 3~5 个标量字段做一致性检查（与模板无关，按 payload 实际内容取样）。"""
    item = payload["dataset"][0]
    data = item.get("data") or {}
    checks: List[Tuple[str, List[str], Any]] = []
    for module, value in data.items():
        if not isinstance(value, dict):
            continue
        for field, expected in value.items():
            if isinstance(expected, (str, int, float)) and expected not in (None, ""):
                checks.append((f"{module}.{field}", [module, field], expected))
                break
        if len(checks) >= 5:
            break
    if not checks:
        return {"checked": {}, "note": "无可用校验字段"}

    reqs = [{"uid": f"c{i}", "path": path} for i, (_, path, _) in enumerate(checks)]
    try:
        rows = client.search(reqs, eln=[cfg["eln_name"]])
    except MatElabError as exc:
        return {"checked": {}, "error": exc.describe()}
    if not rows:
        return {"checked": {}, "error": "检索不到刚导入的记录（索引可能延迟，稍后手动核对）"}

    # keyword 过滤不可靠，这里按标题定位刚写入的那条
    row = next((r for r in rows if isinstance(r, dict)
                and str(r.get("title") or "") == str(item.get("title") or "")), rows[0])
    result: Dict[str, bool] = {}
    for i, (label, _, expected) in enumerate(checks):
        actual = row.get(f"c{i}")
        if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
            ok = abs(float(expected) - float(actual)) < 1e-6
        else:
            ok = str(actual).strip() == str(expected).strip()
        result[label] = ok
        if not ok:
            print(f"[verify] {label} 不一致: 期望 {expected!r} 实际 {actual!r}", file=sys.stderr)
    return {"checked": result}


def run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    record_path = Path(args.record)
    if not record_path.exists():
        print(f"record.json 不存在: {record_path}", file=sys.stderr)
        return 2
    record = json.loads(record_path.read_text(encoding="utf-8-sig"))
    payload, issues = build(
        record, cfg, seq=args.seq,
        allow_missing_attachments=args.allow_missing_attachments,
    )
    if payload:
        payload["_meta"]["source_record"] = str(record_path.resolve())
    payload_path = record_path.with_name("_payload.json")
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if not issues.ok:
        print(preview(payload, issues))
        print(f"\n[upload] 校验失败：{len(issues.errors)} 个错误。修正后重试。", file=sys.stderr)
        return 2

    print(preview(payload, issues))

    if not args.yes:
        print("\n[upload] 这是预览（dry-run），未写入任何数据。")
        print("          确认无误后加 --yes 执行上传。")
        if args.check_dupe:
            try:
                client = MatElabClient(cfg, verbose=True)
                hits = _find_existing(client, cfg, payload["dataset"][0]["uid"], issues)
                print(f"[upload] 查重结果：{'已存在同 uid 记录' if hits else '未发现重复'}")
            except MatElabError as exc:
                print("[upload] 查重失败：" + exc.describe(), file=sys.stderr)
        return 0

    # ---------------- 真正写入 ---------------- #
    client = MatElabClient(cfg, verbose=True)
    uid = payload["dataset"][0]["uid"]

    existing = _find_existing(client, cfg, uid, issues)
    if existing and not args.update:
        print(f"[upload] 记录 {uid} 已存在，跳过。需要覆盖请加 --update。", file=sys.stderr)
        _append_manifest(cfg, {
            "time": dt.datetime.now().isoformat(timespec="seconds"),
            "uid": uid, "record_ids": "", "eln": cfg.get("eln_name"),
            "template": cfg.get("template_name"),
            "title": payload["dataset"][0].get("title"),
            "attachments": 0, "status": "skipped_exists",
            "source": str(record_path),
        })
        return 4

    # 1) 附件上传
    uploaded: List[Dict[str, Any]] = []
    for item in payload.get("_attachments") or []:
        local = Path(item["_local_path"])
        if not local.exists():
            print(f"[upload] 附件不存在：{local}", file=sys.stderr)
            return 2
        try:
            info = client.upload_file(local, name=item["_name"], eln=cfg.get("eln_name"))
        except MatElabError as exc:
            message = str(exc) + json.dumps(exc.payload or {}, ensure_ascii=False)
            if any(word in message for word in ("name", "重复", "存在", "唯一")):
                # 引用名已被占用（上一次失败的上传残留），换名重传
                alt = f"{item['_name'][:36]}_{int(time.time()) % 100000}"
                print(f"[upload] 引用名冲突，改用 {alt} 重传", file=sys.stderr)
                try:
                    info = client.upload_file(local, name=alt, eln=cfg.get("eln_name"))
                except MatElabError as exc2:
                    print("[upload] 附件上传失败：\n" + exc2.describe(), file=sys.stderr)
                    return 3
            else:
                print("[upload] 附件上传失败：\n" + exc.describe(), file=sys.stderr)
                return 3
        server_name = info.get("name") or item["_name"]
        if server_name != item["_name"]:
            _replace_ref(payload, f"#file{{{item['_name']}}}", f"#file{{{server_name}}}")
            item["_name"] = server_name
        uploaded.append(info)

    # 2) 覆盖 or 导入
    try:
        if existing and args.update:
            dedupe_module, dedupe_name = _dedupe_path(cfg)
            record_uids = client.find_record_uids(
                cfg["eln_name"], uid, module=dedupe_module, field=dedupe_name,
            )
            if not record_uids:
                print("[upload] 找不到已有记录的平台 UID，无法覆盖；请到网页确认。", file=sys.stderr)
                return 3
            ops = _build_update_ops(payload, issues)
            client.update(cfg["eln_name"], record_uids[0], modify=ops)
            response = {"code": 0, "id": [record_uids[0]], "msg": "数据更新成功"}
            status = "updated"
        else:
            response = client.import_records(
                cfg["eln_name"], cfg["template_name"], payload["dataset"],
                user=cfg.get("eln_owner"), template_user=cfg.get("template_owner"),
            )
            status = "imported"
    except MatElabError as exc:
        print("[upload] 写入失败：\n" + exc.describe(), file=sys.stderr)
        print(f"[upload] 请求体已保留：{payload_path}", file=sys.stderr)
        print("[upload] 若报字段名/类型错误，先跑 check_template.py 定位模板字段。", file=sys.stderr)
        _append_manifest(cfg, {
            "time": dt.datetime.now().isoformat(timespec="seconds"),
            "uid": uid, "record_ids": "", "eln": cfg.get("eln_name"),
            "template": cfg.get("template_name"),
            "title": payload["dataset"][0].get("title"),
            "attachments": len(uploaded), "status": "failed",
            "note": str(exc)[:200], "source": str(record_path),
        })
        return 3

    # 3) 清理模板继承数据（预填字段 + 示例行），只在新导入时做
    if status == "imported" and not args.no_clear:
        fields_on = (cfg.get("clear_inherited") or {}).get("enabled")
        rows_on = (cfg.get("clear_rows") or {}).get("enabled")
        if fields_on or rows_on:
            try:
                sns = client.find_record_uids(cfg["eln_name"], uid, user=cfg.get("eln_owner"))
                if not sns:
                    print("[upload] 未取到记录 UID，跳过模板继承数据清理", file=sys.stderr)
                else:
                    apply_cleanup(client, cfg, sns[0],
                                  str(payload["dataset"][0].get("title") or ""))
            except MatElabError as exc:
                print("[upload] 模板继承数据清理失败（记录已写入，可稍后跑 clean_record.py）：\n"
                      + exc.describe(), file=sys.stderr)

    # 4) 回读校验
    verify: Optional[Dict[str, Any]] = None
    if not args.no_verify:
        verify = _verify(client, cfg, payload)

    # 4) manifest + 回执
    ids = response.get("id") or []
    _append_manifest(cfg, {
        "time": dt.datetime.now().isoformat(timespec="seconds"),
        "uid": uid, "record_ids": ";".join(str(x) for x in ids),
        "eln": cfg.get("eln_name"), "template": cfg.get("template_name"),
        "title": payload["dataset"][0].get("title"),
        "attachments": len(uploaded), "status": status, "source": str(record_path),
    })
    receipt = _receipt(cfg, payload, uploaded, response, verify)

    print(f"\n[upload] {status} 成功：{payload['dataset'][0].get('title')}")
    print(f"[upload] uid = {uid}  记录ID = {ids}")
    print(f"[upload] 附件 {len(uploaded)} 个，回执 {receipt}")
    if verify is not None:
        if verify.get("error"):
            print(f"[upload] 回读校验未完成：{verify['error']}", file=sys.stderr)
        elif verify.get("checked"):
            bad = [k for k, ok in verify["checked"].items() if not ok]
            print("[upload] 回读校验：" + ("全部一致" if not bad else f"不一致 {bad}"))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="上传实验记录到 MatElab")
    parser.add_argument("--record", required=True, help="record.json 路径")
    parser.add_argument("--config", required=True, help="matelab.yaml 路径")
    parser.add_argument("--dry-run", action="store_true", help="只预览（默认行为）")
    parser.add_argument("--yes", action="store_true", help="确认写入")
    parser.add_argument("--update", action="store_true", help="记录已存在时覆盖字段")
    parser.add_argument("--check-dupe", action="store_true", help="dry-run 时联网查重")
    parser.add_argument("--no-verify", action="store_true", help="跳过回读校验")
    parser.add_argument("--no-clear", action="store_true", help="不清理模板预填值")
    parser.add_argument("--seq", default="01", help="uid 序号（两位）")
    parser.add_argument("--allow-missing-attachments", action="store_true")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except MatElabError as exc:
        print(exc.describe(), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
