#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""record.json + matelab.yaml -> MatElab import payload。

只做映射与校验，不联网。产物可直接给 upload_record.py 用：

    python -X utf8 build_record.py --record record.json --config matelab.yaml -o payload.json --preview

支持的映射类型（field_map.<key>.kind）：
    form      表单字段（默认）
    list      文本列表字段，如 "Method": ["熔剂法", "助熔剂"]
    table     表格模块，列式数组
    fileset   文件集模块，整块 [{file, text}]
    filelist  表单里的文件上传字段，值为 ["#file{..}", ...]，可按 kind 过滤
    richtext  图文混排模块，值为 HTML 字符串
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from matelab_client import load_config, make_ref_name, resolve_attachment  # noqa: E402

DATE_PATTERNS = [
    (re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$"), (1, 2, 3)),
    (re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日?$"), (1, 2, 3)),
    (re.compile(r"^(\d{4})(\d{2})(\d{2})$"), (1, 2, 3)),
]
NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


class Issues:
    def __init__(self) -> None:
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    @property
    def ok(self) -> bool:
        return not self.errors


def get_by_path(obj: Any, dotted: str) -> Any:
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


# --------------------------------------------------------------------------- #
# 类型转换
# --------------------------------------------------------------------------- #

def to_number(value: Any, key: str, issues: Issues) -> Optional[float]:
    if is_empty(value):
        return None
    if isinstance(value, bool):
        issues.error(f"{key}: 布尔值不能当数字")
        return None
    if isinstance(value, (int, float)):
        return int(value) if float(value).is_integer() else float(value)
    text = str(value).strip()
    m = NUM_RE.search(text)
    if not m:
        issues.error(f"{key}: 无法转成数字（{value!r}）——若模板该字段是数字，请填纯数字；否则把模板字段改成文本")
        return None
    if text != m.group(0):
        issues.warn(f"{key}: {value!r} 已提取数字 {m.group(0)}")
    num = float(m.group(0))
    return int(num) if num.is_integer() else num


def to_date(value: Any, key: str, issues: Issues) -> Optional[str]:
    if is_empty(value):
        return None
    text = str(value).strip()
    for pattern, groups in DATE_PATTERNS:
        m = pattern.match(text)
        if m:
            y, mo, d = (int(m.group(g)) for g in groups)
            try:
                return dt.date(y, mo, d).isoformat()
            except ValueError:
                issues.error(f"{key}: 日期非法 {value!r}")
                return None
    issues.error(f"{key}: 无法识别日期 {value!r}（应形如 2026-09-24）")
    return None


def to_cascade(value: Any, key: str, choices: List[str], issues: Issues) -> Optional[str]:
    if is_empty(value):
        return None
    text = str(value).strip()
    if text in choices:
        return text
    norm = {re.sub(r"\s+", "", c).lower(): c for c in choices}
    hit = norm.get(re.sub(r"\s+", "", text).lower())
    if hit:
        issues.warn(f"{key}: {value!r} 规范为级联值 {hit!r}")
        return hit
    issues.error(f"{key}: {value!r} 不在模板备选项中（可选：{' / '.join(choices)}）")
    return None


def convert(value: Any, spec: Dict[str, Any], key: str, issues: Issues) -> Any:
    ftype = (spec.get("type") or "text").lower()
    if ftype == "number":
        return to_number(value, key, issues)
    if ftype == "integer":
        num = to_number(value, key, issues)
        return None if num is None else int(round(num))
    if ftype == "date":
        return to_date(value, key, issues)
    if ftype == "time":
        return None if is_empty(value) else str(value).strip()
    if ftype == "bool":
        if is_empty(value):
            return None
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "y", "是")
    if ftype == "cascade":
        return to_cascade(value, key, spec.get("choices") or [], issues)
    if is_empty(value):
        return None
    if isinstance(value, (list, dict)):
        issues.error(f"{key}: 期望文本，收到 {type(value).__name__}")
        return None
    return str(value).strip()


# --------------------------------------------------------------------------- #
# 派生字段
# --------------------------------------------------------------------------- #

def _num_str(value: Any) -> Optional[str]:
    if is_empty(value):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def narrative_html(narrative: Any, issues: Issues) -> str:
    if is_empty(narrative):
        return ""
    if isinstance(narrative, str):
        return f"<p>{html.escape(narrative).replace(chr(10), '<br>')}</p>"
    if not isinstance(narrative, dict):
        issues.error("narrative: 期望对象或字符串")
        return ""
    labels = [
        ("summary", "摘要"),
        ("process", "配料与工艺"),
        ("observations", "过程与现象"),
        ("conclusion", "表征结论"),
    ]
    parts: List[str] = []
    for key, label in labels:
        value = narrative.get(key)
        if is_empty(value):
            continue
        body = html.escape(str(value)).replace("\n", "<br>")
        parts.append(f"<p><b>{label}</b></p><p>{body}</p>")
    for key, value in narrative.items():
        if key in {k for k, _ in labels} or is_empty(value):
            continue
        body = html.escape(str(value)).replace("\n", "<br>")
        parts.append(f"<p><b>{html.escape(key)}</b></p><p>{body}</p>")
    return "".join(parts)


def derive_record(record: Dict[str, Any], issues: Issues) -> Dict[str, Any]:
    """把 record 里分散的信息压成模板需要的样子（不改变原始 record）。"""
    g: Dict[str, Any] = record.get("growth") or {}
    derived: Dict[str, Any] = {}

    # 样品号（模板 Sample No. 是数字）
    sample_no = record.get("sample_no")
    if sample_no in (None, ""):
        m = re.search(r"(\d+)", str(record.get("sample_id") or ""))
        sample_no = int(m.group(1)) if m else None
    derived["sample_no"] = sample_no

    start = record.get("start_date") or record.get("date")
    derived["start_date"] = start
    derived["finish_date"] = record.get("finish_date") or start

    # 方法（文本列表）
    methods: List[str] = []
    for value in (record.get("exp_type"), g.get("method")):
        if value and str(value) not in methods:
            methods.append(str(value))
    derived["method"] = methods

    atmosphere = g.get("atmosphere")
    if is_empty(atmosphere):
        derived["atmosphere"] = None
    elif isinstance(atmosphere, list):
        derived["atmosphere"] = [str(a) for a in atmosphere]
    else:
        derived["atmosphere"] = [str(atmosphere)]

    derived["crucible"] = [str(g["crucible"])] if g.get("crucible") else None
    end_t = g.get("end_temp") or g.get("end_temperature")
    derived["end_temperature"] = [str(end_t)] if end_t not in (None, "") else None

    separation = str(g.get("separation") or "")
    derived["centrifugation"] = True if "离心" in separation else (
        False if separation else None
    )
    derived["tantalum_seal"] = g.get("tantalum_seal")
    derived["quartz_seal"] = g.get("quartz_seal")
    derived["anneal"] = g.get("anneal")

    # 原料表（模板列全是文本）
    rows: List[Dict[str, Any]] = []
    for p in record.get("precursors") or []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "").strip()
        purity = str(p.get("purity") or "").strip()
        rows.append({
            "materials": f"{name} ({purity})" if purity else name,
            "ratio": _num_str(p.get("ratio")),
            "molar_mass": _num_str(p.get("molar_mass")),
            "mass": p.get("mass_g"),
        })
    derived["precursors"] = rows

    # 升温/保温表
    heating = record.get("heating")
    if isinstance(heating, list) and heating:
        h_rows = []
        for step in heating:
            if isinstance(step, dict):
                h_rows.append({
                    "temperature": step.get("temperature", step.get("t")),
                    "time": step.get("time", step.get("hold_h")),
                    "total": step.get("total", step.get("total_h")),
                })
        derived["heating"] = h_rows
    elif g.get("t_max") is not None:
        # 模板语义：每行 =（本段开始时刻 total，持续 time，温度变到 temperature）
        derived["heating"] = [
            {"temperature": 30, "time": 0, "total": 0},
            {"temperature": g.get("t_max"), "time": 0, "total": 0},
            {"temperature": g.get("t_max"), "time": g.get("hold_h"), "total": 0},
        ]

    # 多温区模板：source_heating / deposition_heating 等原样传递
    for extra_key, extra_value in record.items():
        if extra_key.endswith("_heating") and isinstance(extra_value, list):
            derived[extra_key] = extra_value

    # XRD / EDS 文本结论
    xrd = record.get("xrd") or {}
    xrd_parts = []
    if xrd.get("phases"):
        xrd_parts.append(f"物相: {xrd['phases']}")
    if xrd.get("space_group"):
        xrd_parts.append(f"空间群: {xrd['space_group']}")
    if xrd.get("lattice"):
        xrd_parts.append(f"晶格参数: {xrd['lattice']}")
    if xrd.get("conclusion"):
        xrd_parts.append(str(xrd["conclusion"]))
    derived["xrd_result"] = "; ".join(xrd_parts) or None

    eds_rows = record.get("eds_rows") or []
    if eds_rows:
        bits = []
        for row in eds_rows:
            if not isinstance(row, dict):
                continue
            element = row.get("element") or ""
            wt = row.get("wt")
            at = row.get("at")
            piece = f"{element}"
            if wt is not None:
                piece += f" {wt} wt%"
            if at is not None:
                piece += f" / {at} at%"
            if row.get("note"):
                piece += f" ({row['note']})"
            bits.append(piece.strip())
        derived["eds_result"] = "; ".join(bits) or None

    # 备注（富文本）：样品编号 + 叙述 + 电学/磁学
    note_parts: List[str] = []
    meta = []
    if record.get("sample_id"):
        meta.append(f"样品编号: {record['sample_id']}")
    if record.get("formula"):
        meta.append(f"化学式: {record['formula']}")
    if record.get("project"):
        meta.append(f"项目: {record['project']}")
    if meta:
        note_parts.append("<p>" + " | ".join(html.escape(m) for m in meta) + "</p>")
    # 模板里 Crucible / Atmosphere / End temperature 是下拉，未取到选项前把原值写在 Note 里
    proc = []
    for label, key in (("坩埚", "crucible"), ("气氛", "atmosphere"),
                       ("助熔剂", "flux"), ("终止温度", "end_temp")):
        value = g.get(key)
        if not is_empty(value):
            if isinstance(value, list):
                text = " / ".join(str(v) for v in value)
            else:
                text = _num_str(value) if isinstance(value, (int, float)) else str(value)
            proc.append(f"{label}: {text}")
    if proc:
        note_parts.append("<p>" + html.escape("；".join(proc)) + "</p>")
    note_parts.append(narrative_html(record.get("narrative"), issues))

    transport = record.get("transport") or {}
    t_pairs = [(k, v) for k, v in transport.items() if not is_empty(v)]
    if t_pairs:
        body = "；".join(f"{k}={v}" for k, v in t_pairs)
        note_parts.append(f"<p><b>电学输运</b></p><p>{html.escape(body)}</p>")
    magnetism = record.get("magnetism") or {}
    m_pairs = [(k, v) for k, v in magnetism.items() if not is_empty(v)]
    if m_pairs:
        body = "；".join(f"{k}={v}" for k, v in m_pairs)
        note_parts.append(f"<p><b>磁性</b></p><p>{html.escape(body)}</p>")
    derived["note"] = "".join(p for p in note_parts if p) or None

    # 允许 record.json 直接用 xrd_result / eds_result 覆盖自动拼接的结论
    if record.get("xrd_result"):
        derived["xrd_result"] = record["xrd_result"]
    if record.get("eds_result"):
        derived["eds_result"] = record["eds_result"]

    derived["is_target"] = record.get("is_target_product")
    mg = record.get("materials_galaxy") or {}
    derived["mg_id"] = mg.get("id")
    derived["mg_link"] = mg.get("link")
    return derived


def normalize_uid(record: Dict[str, Any], seq: str, prefix: str, issues: Issues) -> str:
    uid = str(record.get("uid") or "").strip()
    if uid:
        if len(uid) > 45:
            fixed = uid[:45]
            issues.warn(f"uid 超过 45 字符，已截断：{uid} -> {fixed}")
            uid = fixed
        return uid
    sample = str(record.get("sample_id") or "S").strip()
    date = to_date(record.get("date"), "date", issues) or dt.date.today().isoformat()
    stem = f"{prefix}{sample}" if prefix else sample
    uid = f"{stem}-{date.replace('-', '')}-{seq}"
    if len(uid) > 45:
        uid = uid[:45]
        issues.warn(f"自动生成的 uid 超过 45 字符，已截断：{uid}")
    return uid


# --------------------------------------------------------------------------- #
# 附件
# --------------------------------------------------------------------------- #

def resolve_attachments(record: Dict[str, Any], cfg: Dict[str, Any], uid: str,
                        issues: Issues, *, check: bool,
                        allow_missing: bool) -> List[Dict[str, Any]]:
    items = record.get("attachments") or []
    if not isinstance(items, list):
        issues.error("attachments: 期望数组")
        return []
    roots = cfg.get("attachment_roots_resolved") or []
    out: List[Dict[str, Any]] = []
    for idx, item in enumerate(items, start=1):
        if isinstance(item, str):
            item = {"path": item, "text": ""}
        if not isinstance(item, dict) or not item.get("path"):
            issues.error(f"attachments[{idx}]: 缺少 path")
            continue
        raw_path = str(item["path"])
        resolved = raw_path
        exists = Path(raw_path).exists()
        if not exists and check:
            found = resolve_attachment(raw_path, roots)
            if found:
                resolved, exists = found, True
            elif item.get("optional") or allow_missing:
                issues.warn(f"附件缺失已跳过：{raw_path}")
                continue
            else:
                issues.error(f"附件不存在：{raw_path}")
                continue
        ref_name = item.get("name") or make_ref_name(uid, idx, resolved)
        out.append({
            "file": f"#file{{{ref_name}}}",
            "text": f"<p>{html.escape(str(item.get('text') or Path(resolved).name))}</p>",
            "kind": str(item.get("kind") or "other").lower(),
            "_local_path": resolved,
            "_name": ref_name,
            "_index": idx,
            "_missing": not exists,
        })
    return out


# --------------------------------------------------------------------------- #
# 主映射
# --------------------------------------------------------------------------- #

def build(record: Dict[str, Any], cfg: Dict[str, Any], *,
          seq: str = "01", allow_missing_attachments: bool = False,
          check_attachments: bool = True) -> Tuple[Dict[str, Any], Issues]:
    issues = Issues()
    field_map: Dict[str, Dict[str, Any]] = cfg.get("field_map") or {}
    if not field_map:
        issues.error("config.field_map 为空")
        return {}, issues

    record = dict(record)
    uid = normalize_uid(record, seq, str(cfg.get("uid_prefix") or ""), issues)
    record["uid"] = uid
    record["_derived"] = derive_record(record, issues)

    needs_files = any((s.get("kind") or "form").lower() in ("fileset", "filelist")
                      for s in field_map.values())
    attachments = (
        resolve_attachments(record, cfg, uid, issues,
                            check=check_attachments,
                            allow_missing=allow_missing_attachments)
        if needs_files else []
    )

    data: Dict[str, Any] = {}
    for key, spec in field_map.items():
        kind = (spec.get("kind") or "form").lower()
        module = spec.get("module")
        field = spec.get("field")
        if not module:
            issues.error(f"field_map.{key} 缺少 module")
            continue

        if kind == "richtext":
            value = get_by_path(record, key)
            if key == "narrative" and value is None:
                value = record.get("narrative")
            html_text = narrative_html(value, issues) if not isinstance(value, str) else value
            if html_text:
                data[module] = html_text
            continue

        if kind == "filelist":
            if not field:
                issues.error(f"field_map.{key} 缺少 field（filelist 需要字段名）")
                continue
            filters = spec.get("filter")
            allowed = set(filters) if isinstance(filters, list) else None
            refs = [a["file"] for a in attachments
                    if allowed is None or a["kind"] in allowed]
            if refs:
                data.setdefault(module, {})[field] = refs
            continue

        if kind == "fileset":
            entries = [{"file": a["file"], "text": a["text"]} for a in attachments]
            if entries:
                data[module] = entries
            continue

        if kind == "list":
            value = get_by_path(record, key) if "." in key else record.get(key)
            if is_empty(value):
                continue
            values = value if isinstance(value, list) else [value]
            choices = spec.get("choices") or []
            aliases = {str(k).strip().lower(): str(v).strip()
                       for k, v in (spec.get("aliases") or {}).items()}
            out_values: List[str] = []
            for item in values:
                if is_empty(item):
                    continue
                text = str(item).strip()
                if choices:
                    hit = aliases.get(text.lower())
                    if hit is None:
                        hit = next((c for c in choices
                                    if re.sub(r"\s+", "", c).lower() == re.sub(r"\s+", "", text).lower()),
                                   None)
                    if hit is None:
                        issues.error(f"{key}: {text!r} 不在备选项中（可选：{' / '.join(choices)}）")
                        continue
                    if hit != text:
                        issues.warn(f"{key}: {text!r} -> {hit!r}")
                    text = hit
                out_values.append(text)
            if out_values:
                data.setdefault(module, {})[field or key] = out_values
            continue

        if kind == "table":
            rows = get_by_path(record, key) if "." in key else record.get(key)
            columns: Dict[str, str] = spec.get("columns") or {}
            if is_empty(rows):
                continue
            if not isinstance(rows, list):
                issues.error(f"{key}: 期望数组，收到 {type(rows).__name__}")
                continue
            col_values: Dict[str, List[Any]] = {col: [] for col in columns.values()}
            for i, row in enumerate(rows):
                if not isinstance(row, dict):
                    issues.error(f"{key}[{i}]: 期望对象")
                    continue
                extra = set(row) - set(columns)
                if extra:
                    issues.warn(f"{key}[{i}]: 未映射的字段 {sorted(extra)} 已忽略")
                for canon, col_name in columns.items():
                    raw = row.get(canon)
                    is_numeric_col = str((spec.get("column_types") or {}).get(canon, "")).lower() in ("number", "integer")
                    if raw is None or raw == "":
                        value = None
                    elif is_numeric_col:
                        value = to_number(raw, f"{key}.{canon}", issues)
                    else:
                        value = str(raw).strip()
                    col_values[col_name].append(value)
            length = max((len(v) for v in col_values.values()), default=0)
            for col, values in col_values.items():
                if len(values) < length:
                    values.extend([None] * (length - len(values)))
                if spec.get("drop_empty_columns") and all(v is None for v in values):
                    continue
                col_values[col] = values
            data.setdefault(module, {}).update(col_values)
            continue

        # 普通表单字段
        value = get_by_path(record, key) if "." in key else record.get(key)
        if key == "uid" and cfg.get("dedupe_by_uid_field", True):
            value = uid
        converted = convert(value, spec, key, issues)
        if converted is None:
            if spec.get("required") and is_empty(value):
                issues.error(f"{key}: 必填字段为空")
            continue
        if spec.get("required") and is_empty(converted):
            issues.error(f"{key}: 必填字段为空")
            continue
        data.setdefault(module, {})[field or key] = converted

    # 未映射字段提示
    known_top = {k.split(".")[0] for k in field_map}
    meta_keys = {"uid", "title", "sample_id", "sample_no", "formula", "exp_type", "date",
                 "start_date", "finish_date", "operator", "project", "target", "keywords",
                 "path", "narrative", "attachments", "heating", "is_target_product",
                 "materials_galaxy", "precursors", "growth", "xrd", "eds_rows",
                 "transport", "magnetism", "xrd_result", "eds_result"}
    for key in record:
        if key.startswith("_") or key.endswith("_heating"):
            continue
        if key not in known_top and key not in meta_keys:
            issues.warn(f"record.json 中的 {key!r} 未在 field_map 中映射，已忽略")

    title = record.get("title") or " ".join(
        str(x) for x in [record.get("sample_id"), record.get("formula"),
                         record.get("exp_type"), record.get("date")] if x
    ).strip() or uid

    dataset_item: Dict[str, Any] = {"uid": uid, "title": title, "data": data}

    keywords = list(record.get("keywords") or [])
    if cfg.get("uid_in_keywords", True) and uid not in keywords:
        keywords.append(uid)
    if keywords:
        dataset_item["keyword"] = ";".join(str(k) for k in keywords)
    path = record.get("path") or cfg.get("record_path")
    if path:
        dataset_item["path"] = path if isinstance(path, list) else [str(path)]

    payload = {
        "eln": cfg.get("eln_name"),
        "template": cfg.get("template_name"),
        "dataset": [dataset_item],
        "_meta": {
            "uid": uid,
            "source_record": record.get("_source"),
            "attachments": len(attachments),
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        },
        "_attachments": attachments,
    }
    if not payload["eln"]:
        issues.error("config.eln_name 未设置")
    if not payload["template"]:
        issues.error("config.template_name 未设置")
    return payload, issues


# --------------------------------------------------------------------------- #
# 预览
# --------------------------------------------------------------------------- #

def preview(payload: Dict[str, Any], issues: Issues) -> str:
    lines: List[str] = []
    lines.append(f"记录本 : {payload.get('eln')}")
    lines.append(f"模板   : {payload.get('template')}")
    item = (payload.get("dataset") or [{}])[0]
    lines.append(f"uid    : {item.get('uid')}")
    lines.append(f"标题   : {item.get('title')}")
    if item.get("keyword"):
        lines.append(f"关键词 : {item['keyword']}")
    if item.get("path"):
        lines.append(f"分类   : {' / '.join(item['path'])}")
    lines.append("")

    atts = payload.get("_attachments") or []

    def att_info(ref: str) -> str:
        entry = next((a for a in atts if a["file"] == ref), None)
        if not entry:
            return ""
        local = Path(entry["_local_path"])
        if entry.get("_missing") or not local.exists():
            return "[缺失]"
        return f"{local.stat().st_size / 1024:.1f} KB"

    for module, value in (item.get("data") or {}).items():
        if isinstance(value, str):
            text = re.sub(r"<[^>]+>", " ", value)
            text = re.sub(r"\s+", " ", text).strip()
            lines.append(f"[{module}] (富文本) {text[:120]}{'…' if len(text) > 120 else ''}")
        elif isinstance(value, list):
            if value and isinstance(value[0], str) and str(value[0]).startswith("#file{"):
                lines.append(f"[{module}] (文件) {len(value)} 项")
                for ref in value:
                    lines.append(f"   - {ref}  {att_info(ref)}")
            elif value and isinstance(value[0], str):
                lines.append(f"[{module}] (文本列表) {' / '.join(value)}")
            else:
                lines.append(f"[{module}] (文件集) {len(value)} 项")
                for entry in value:
                    caption = re.sub(r"<[^>]+>", "", entry.get("text", ""))
                    lines.append(f"   - {entry.get('file')}  {caption}  {att_info(entry.get('file', ''))}")
        elif isinstance(value, dict):
            is_table = bool(value) and all(isinstance(v, list) for v in value.values())
            if is_table:
                headers = list(value)
                rows = max(len(v) for v in value.values())
                lines.append(f"[{module}] (表格) {rows} 行 × {len(headers)} 列")
                lines.append("   " + " | ".join(headers))
                for r in range(rows):
                    cells = []
                    for h in headers:
                        v = value[h][r] if r < len(value[h]) else None
                        cells.append("" if v is None else str(v))
                    lines.append("   " + " | ".join(cells))
            else:
                lines.append(f"[{module}]")
                for k, v in value.items():
                    if isinstance(v, list):
                        if v and all(isinstance(x, str) for x in v) and not v[0].startswith("#file{"):
                            lines.append(f"   {k}: {' / '.join(v)}")
                        else:
                            lines.append(f"   {k}: {len(v)} 项")
                    else:
                        lines.append(f"   {k}: {v}")
        lines.append("")

    if issues.warnings:
        lines.append("警告：")
        lines.extend(f"  ! {w}" for w in issues.warnings)
    if issues.errors:
        lines.append("错误：")
        lines.extend(f"  x {e}" for e in issues.errors)
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="record.json -> MatElab import payload")
    parser.add_argument("--record", required=True, help="record.json")
    parser.add_argument("--config", required=True, help="matelab.yaml")
    parser.add_argument("-o", "--out", help="payload 输出路径")
    parser.add_argument("--seq", default="01", help="uid 序号（两位），默认 01")
    parser.add_argument("--allow-missing-attachments", action="store_true")
    parser.add_argument("--no-check-attachments", action="store_true",
                        help="不解析本地附件（只做结构映射）")
    parser.add_argument("--preview", action="store_true", help="打印人类可读预览")
    parser.add_argument("--json", action="store_true", help="把 payload 打到 stdout")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    record_path = Path(args.record)
    if not record_path.exists():
        print(f"record.json 不存在: {record_path}", file=sys.stderr)
        return 2
    record = json.loads(record_path.read_text(encoding="utf-8-sig"))
    payload, issues = build(
        record, cfg, seq=args.seq,
        allow_missing_attachments=args.allow_missing_attachments,
        check_attachments=not args.no_check_attachments,
    )

    if payload and payload.get("_meta"):
        payload["_meta"]["source_record"] = str(record_path.resolve())
    out_path = Path(args.out) if args.out else record_path.with_name("_payload.json")
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[build] payload -> {out_path}")

    if args.preview or not issues.ok:
        print()
        print(preview(payload, issues))

    if not issues.ok:
        print(f"\n[build] {len(issues.errors)} 个错误，未通过校验", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
