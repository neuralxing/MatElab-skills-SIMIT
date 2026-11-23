#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""模板探针校验：确认记录本存在、模板字段能接收数据、上传通道可用。

做法：构造一条 uid=__probe_xxx__ 的测试记录（含所有映射字段 + 一个 1x1 PNG），
导入后回读校验，再删除该记录。失败时给出具体字段线索。

    python -X utf8 check_template.py --config matelab.yaml
    python -X utf8 check_template.py --config matelab.yaml --keep
    python -X utf8 check_template.py --config matelab.yaml --no-upload   # 跳过文件集
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_record import Issues, build, preview  # noqa: E402
from cleanup import apply_cleanup  # noqa: E402
from matelab_client import MatElabClient, MatElabError, load_config  # noqa: E402

# 1x1 透明 PNG
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)


def probe_record(cfg: Dict[str, Any], uid: str, png_path: Optional[str]) -> Dict[str, Any]:
    field_map: Dict[str, Dict[str, Any]] = cfg.get("field_map") or {}
    record: Dict[str, Any] = {
        "uid": uid,
        "title": "模板字段自检（脚本生成，可删除）",
        "sample_id": "PROBE1",
        "sample_no": 1,
        "formula": "NaCl",
        "exp_type": "",
        "date": dt.date.today().isoformat(),
        "start_date": dt.date.today().isoformat(),
        "finish_date": dt.date.today().isoformat(),
        "operator": "probe",
        "project": "模板自检",
        "target": "字段连通性测试",
        "keywords": ["__probe__"],
        "growth": {
            # Method 是下拉，必须填模板里真实存在的选项
            "method": "Flux method",
            "flux": "probe",
            "crucible": "probe",
            "inner_diameter_mm": 1,
            "crucible_length_mm": 1,
            "atmosphere": "probe",
            "t_max": 1,
            "hold_h": 1,
            "end_temp": 1,
            "separation": "离心",
            "centrifugation": True,
            "quartz_seal": True,
            "tantalum_seal": False,
            "anneal": False,
            "furnace_no": "probe",
        },
        "heating": [{"temperature": 1, "time": 1, "total": 1}],
        "source_heating": [{"temperature": 1, "time": 1, "total": 0}],
        "deposition_heating": [{"temperature": 1, "time": 1, "total": 0}],
        "precursors": [{
            "name": "probe", "purity": "probe", "ratio": 1,
            "molar_mass": 1, "mass_g": 1, "form": "probe",
        }],
        "xrd": {"phases": "probe", "conclusion": "probe"},
        "eds_rows": [{"element": "probe", "wt": 1, "at": 1}],
        "transport": {"property": "probe"},
        "magnetism": {"measurement": "probe"},
        "is_target_product": True,
    }
    for key, spec in field_map.items():
        kind = (spec.get("kind") or "form").lower()
        ftype = (spec.get("type") or "text").lower()

        if kind == "richtext":
            record["narrative"] = {"summary": "模板自检富文本。", "process": "probe"}
            continue
        if kind in ("fileset", "filelist"):
            record["attachments"] = (
                [{"path": png_path, "text": "自检用 1x1 图片", "kind": "photo"}]
                if png_path else []
            )
            continue
        if kind == "table":
            columns: Dict[str, str] = spec.get("columns") or {}
            types: Dict[str, str] = spec.get("column_types") or {}
            row: Dict[str, Any] = {}
            for canon in columns:
                row[canon] = 1 if str(types.get(canon, "text")).lower() in ("number", "integer") else "probe"
            record_key = key.split(".")
            target = record
            for part in record_key[:-1]:
                target = target.setdefault(part, {})
            target[record_key[-1]] = [row]
            continue

        # 表单
        value: Any
        if ftype == "number" or ftype == "integer":
            value = 1
        elif ftype == "date":
            value = dt.date.today().isoformat()
        elif ftype == "time":
            value = "12:00"
        elif ftype == "bool":
            value = True
        elif ftype == "cascade":
            choices = spec.get("choices") or []
            value = choices[0] if choices else "probe"
        else:
            value = "probe"

        if key == "uid":
            value = uid
        if key == "exp_type" and spec.get("choices"):
            value = spec["choices"][0]

        parts = key.split(".")
        target = record
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
    return record


def diagnose(exc: MatElabError, cfg: Dict[str, Any]) -> None:
    print("\n[check] 导入被拒，定位建议：", file=sys.stderr)
    text = str(exc) + " " + json.dumps(exc.payload or {}, ensure_ascii=False)
    print(f"  原始报错：{text.strip()[:400]}", file=sys.stderr)
    field_map = cfg.get("field_map") or {}
    hits = [key for key, spec in field_map.items()
            if spec.get("module") and spec["module"] in text]
    if hits:
        for key in hits:
            spec = field_map[key]
            print(f"  涉及 field_map.{key} -> 模块「{spec.get('module')}」"
                  f" 字段「{spec.get('field')}」", file=sys.stderr)
    if "应为数字" in text:
        print("  原因：该字段在模板里是数字格式，但收到文本。改 field_map 的 type 为 number，"
              "或把模板字段改成文本。", file=sys.stderr)
    elif "应为" in text:
        print("  原因：字段类型不匹配。核对模板里该字段的数据格式（文本/数字/日期/级联）。",
              file=sys.stderr)
    elif "不存在" in text or "无此" in text:
        print("  原因：模块名或字段名与模板不一致。逐字核对（含中英文括号、全角半角）。",
              file=sys.stderr)
    else:
        print("  若信息不足以定位，先手工在网页用该模板建一条记录，对照模块名与字段名。",
              file=sys.stderr)


def run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    client = MatElabClient(cfg, verbose=True)
    field_map = cfg.get("field_map") or {}
    modules: Dict[str, List[str]] = {}
    for key, spec in field_map.items():
        module = spec.get("module") or "?"
        kind = (spec.get("kind") or "form").lower()
        if kind == "table":
            label = "表(" + " / ".join((spec.get("columns") or {}).values()) + ")"
        elif kind == "richtext":
            label = "(富文本)"
        elif kind == "fileset":
            label = "(文件集)"
        else:
            label = spec.get("field") or key
        modules.setdefault(module, []).append(label)

    print(f"[check] 记录本：{cfg.get('eln_name')}")
    print(f"[check] 模板  ：{cfg.get('template_name')}")
    print(f"[check] 映射  ：{len(field_map)} 个字段 / {len(modules)} 个模块")

    try:
        items = client.items(cfg["eln_name"], cfg.get("eln_owner"))
        print(f"[check] 记录本可访问，现有记录 {len(items)} 条")
    except MatElabError as exc:
        print("[check] 读取记录本失败：\n" + exc.describe(), file=sys.stderr)
        print("[check] 检查 eln_name 是否与网页完全一致，以及账号是否有权限。", file=sys.stderr)
        return 3

    uid = f"__probe_{dt.datetime.now().strftime('%m%d%H%M%S')}__"
    png_path: Optional[str] = None
    tmpdir: Optional[tempfile.TemporaryDirectory] = None
    if not args.no_upload and any((s.get("kind") == "fileset") for s in field_map.values()):
        tmpdir = tempfile.TemporaryDirectory(prefix="matelab_probe_")
        png_path = str(Path(tmpdir.name) / "probe.png")
        Path(png_path).write_bytes(TINY_PNG)

    record = probe_record(cfg, uid, png_path)
    if args.emit_record:
        Path(args.emit_record).write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[check] 探针 record.json -> {args.emit_record}")

    payload, issues = build(record, cfg, seq="01")
    if not issues.ok:
        print(preview(payload, issues), file=sys.stderr)
        print("[check] 本地映射校验失败，先修 field_map。", file=sys.stderr)
        return 2

    # 上传探针 PNG（一次上传，所有文件字段复用同一个 #file 引用）
    if png_path:
        file_specs = [s for s in field_map.values()
                      if (s.get("kind") or "").lower() in ("fileset", "filelist")]
        try:
            info = client.upload_file(png_path, name=f"probe_{uid[-9:]}", eln=cfg.get("eln_name"))
            ref = f"#file{{{info.get('name')}}}"
            data = payload["dataset"][0]["data"]
            for spec in file_specs:
                module = spec["module"]
                if (spec.get("kind") or "").lower() == "fileset":
                    data[module] = [{"file": ref, "text": "<p>自检用 1x1 图片</p>"}]
                else:
                    data.setdefault(module, {})[spec["field"]] = [ref]
            print(f"[check] 文件上传通道 OK（{info.get('name')}），已填入 {len(file_specs)} 个文件字段")
        except MatElabError as exc:
            print("[check] 文件上传失败：\n" + exc.describe(), file=sys.stderr)
            return 3

    try:
        resp = client.import_records(
            cfg["eln_name"], cfg["template_name"], payload["dataset"],
            user=cfg.get("eln_owner"), template_user=cfg.get("template_owner"),
        )
    except MatElabError as exc:
        diagnose(exc, cfg)
        if tmpdir:
            tmpdir.cleanup()
        return 3

    ids = resp.get("id") or []
    print(f"[check] 导入成功：记录 ID {ids}")

    # 回读
    sample_paths = []
    for canon in ("sample_id", "uid", "date", "growth.t_max", "xrd.phases", "magnetism.t_transition"):
        spec = field_map.get(canon)
        if spec and spec.get("field"):
            sample_paths.append((canon, [spec["module"], spec["field"]]))
    verify_ok = None
    if sample_paths:
        try:
            rows = client.search(
                [{"uid": f"v{i}", "path": path} for i, (_, path) in enumerate(sample_paths)],
                eln=[cfg["eln_name"]],
            )
            if rows:
                row = next((r for r in rows if isinstance(r, dict)
                            and str(r.get("title") or "") == record.get("title")), rows[0])
                checks = {canon: str(row.get(f"v{i}")) for i, (canon, _) in enumerate(sample_paths)}
                print("[check] 回读：" + ", ".join(f"{k}={v}" for k, v in checks.items()))
                verify_ok = all(v not in ("None", "") for v in checks.values())
            else:
                print("[check] 回读为空（索引可能延迟），不影响字段校验结论", file=sys.stderr)
        except MatElabError as exc:
            print("[check] 回读失败：" + exc.describe(), file=sys.stderr)

    # 清理模板继承数据（顺便验证 /eln_api/update 的 del 路径正确）
    fields_on = (cfg.get("clear_inherited") or {}).get("enabled")
    rows_on = (cfg.get("clear_rows") or {}).get("enabled")
    if fields_on or rows_on:
        try:
            sns = client.find_record_uids(cfg["eln_name"], uid, user=cfg.get("eln_owner"))
            if not sns:
                print("[check] 未取到探针记录 UID，跳过清理测试", file=sys.stderr)
            else:
                apply_cleanup(client, cfg, sns[0], str(record.get("title") or ""))
                print("[check] 模板继承数据清理 OK")
        except MatElabError as exc:
            print("[check] 清理失败：\n" + exc.describe(), file=sys.stderr)

    # 删除探针
    if args.keep:
        print(f"[check] 已保留探针记录（uid={uid}），请在网页确认后手动删除。")
    else:
        try:
            dedupe = cfg.get("dedupe_field") or {}
            probe_module = dedupe.get("module")
            probe_field = dedupe.get("field")
            if not (probe_module and probe_field):
                first = next((s for s in field_map.values()
                              if s.get("module") and s.get("field")
                              and (s.get("kind") or "form") == "form"), None)
                if first:
                    probe_module, probe_field = first["module"], first["field"]
            record_uids = client.find_record_uids(
                cfg["eln_name"], uid,
                module=probe_module or "Experimental scheme",
                field=probe_field or "Sample No.",
            )
            if record_uids:
                client.delete(cfg["eln_name"], record_uids, user=cfg.get("eln_owner"))
                print(f"[check] 探针记录已删除（{record_uids}）")
            else:
                print(f"[check] 未能取得平台记录 UID，请到网页手动删除 uid={uid}", file=sys.stderr)
        except MatElabError as exc:
            print("[check] 删除探针失败，请手动删除：\n" + exc.describe(), file=sys.stderr)

    if tmpdir:
        tmpdir.cleanup()

    print("\n[check] 字段清单：")
    for module, fields in modules.items():
        print(f"  {module}: {', '.join(f for f in fields if f)}")
    print("\n[check] 结论：" + ("模板与配置可用。" if verify_ok is not False
                                else "导入成功但回读值有空，检查必填字段与模板默认值。"))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="MatElab 模板探针校验")
    parser.add_argument("--config", required=True, help="matelab.yaml")
    parser.add_argument("--keep", action="store_true", help="保留探针记录")
    parser.add_argument("--no-upload", action="store_true", help="跳过文件上传测试")
    parser.add_argument("--emit-record", help="把探针 record.json 写到指定路径")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except MatElabError as exc:
        print(exc.describe(), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
