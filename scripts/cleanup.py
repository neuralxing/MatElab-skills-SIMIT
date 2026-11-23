#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""清理模板继承数据：清空预填字段、删除预填示例行。

模板（如 Flux method simit）自带示例数据，新建记录会继承：
  - 单个字段默认值（Crucible=Alumina 等）→ clear_inherited 清空
  - 表格示例行（Start materials 里的 Cr/Ge/Te 三行）→ clear_rows 删行
注意：/eln_api/import 是往表里**追加行**，不会覆盖模板原有行，所以必须显式删除。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from matelab_client import MatElabClient, MatElabError  # noqa: E402


def row_by_title(rows: List[Dict[str, Any]], title: str) -> Optional[Dict[str, Any]]:
    for row in rows:
        if isinstance(row, dict) and str(row.get("title") or "") == title:
            return row
    return None


def read_columns(client: MatElabClient, eln: str, title: str, module: str,
                 columns: List[str]) -> Optional[Dict[str, List[Any]]]:
    """读取表格模块若干列，按标题定位记录。"""
    reqs = [{"uid": f"c{i}", "path": [module, col]} for i, col in enumerate(columns)]
    rows = client.search(reqs, eln=[eln])
    row = row_by_title(rows, title)
    if row is None:
        return None
    return {col: (row.get(f"c{i}") or []) for i, col in enumerate(columns)}


def plan_row_deletes(arrays: Dict[str, List[Any]], rule: Dict[str, Any]) -> List[int]:
    """返回要删除的行号（升序）。

    判定：marker 列非空 = 模板示例行；expect_values 给出时再校验值列。
    我们的数据从不写 marker 列（如 Actual mass），所以不会误删。
    """
    marker = rule.get("marker_column")
    value_column = rule.get("value_column", "Materials")
    expect = {str(v).strip() for v in (rule.get("expect_values") or [])}
    length = max((len(v) for v in arrays.values()), default=0)
    hits: List[int] = []
    for i in range(length):
        if marker:
            marker_values = arrays.get(marker) or []
            if i >= len(marker_values) or marker_values[i] in (None, "", []):
                continue
        if expect:
            values = arrays.get(value_column) or []
            if i >= len(values) or str(values[i]).strip() not in expect:
                continue
        hits.append(i)
    return hits


def non_empty_fields(client: MatElabClient, eln: str, title: str,
                     fields: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """只保留当前确实有值的字段，避免对空字段重复 del 触发服务端报错。"""
    if not fields:
        return []
    reqs = [{"uid": f"f{i}", "path": [f["module"], f["field"]]} for i, f in enumerate(fields)]
    try:
        rows = client.search(reqs, eln=[eln])
    except MatElabError:
        return fields
    row = row_by_title(rows, title)
    if row is None:
        return fields
    keep = []
    for i, spec in enumerate(fields):
        value = row.get(f"f{i}")
        if value in (None, "", []):
            continue
        keep.append(spec)
    return keep


def _cell_eq(actual: Any, expected: Any) -> bool:
    if expected is None:
        return actual in (None, "")
    try:
        return abs(float(actual) - float(expected)) < 1e-6
    except (TypeError, ValueError):
        return str(actual).strip() == str(expected).strip()


def leading_rows_to_delete(arrays: Dict[str, List[Any]], rule: Dict[str, Any]) -> List[int]:
    """按 expected_rows 从头逐行比对，遇到不匹配即停（只删模板示例行）。"""
    columns = rule.get("columns") or []
    expected_rows = rule.get("expected_rows") or []
    compare = rule.get("compare_columns")
    indices = [i for i, col in enumerate(columns) if not compare or col in compare]
    hits: List[int] = []
    for index, expected in enumerate(expected_rows):
        if any(index >= len(arrays.get(col) or []) for col in columns):
            break
        if not all(_cell_eq(arrays[columns[i]][index], expected[i]) for i in indices):
            break
        hits.append(index)
    return hits


def clear_fields(client: MatElabClient, eln: str, sn: str,
                 fields: List[Dict[str, Any]], verbose: bool = True) -> None:
    if not fields:
        return
    client.update(eln, sn, del_=[{"path": [f["module"], f["field"]]} for f in fields])
    if verbose:
        print(f"[clean] 清空预填字段 {len(fields)} 项："
              + ", ".join(f"{f['module']}/{f['field']}" for f in fields))


def apply_cleanup(client: MatElabClient, cfg: Dict[str, Any], sn: str, title: str, *,
                  verbose: bool = True, dry_run: bool = False) -> None:
    eln = cfg["eln_name"]

    fields_cfg = cfg.get("clear_inherited") or {}
    fields = fields_cfg.get("fields") or []
    if fields_cfg.get("enabled") and fields:
        todo = non_empty_fields(client, eln, title, fields)
        if not todo:
            if verbose:
                print("[clean] 预填字段已为空，跳过")
        elif dry_run:
            print("[clean] 将清空预填字段 "
                  + ", ".join(f"{f['module']}/{f['field']}" for f in todo) + "（dry-run）")
        else:
            try:
                clear_fields(client, eln, sn, todo, verbose=verbose)
            except MatElabError as exc:
                errs = (exc.payload or {}).get("errs") or []
                if errs and all("不存在" in str(e) for e in errs):
                    # 这些值是模板默认值，记录里并没有存储值，删不掉也不用删
                    if verbose:
                        print("[clean] 预填字段来自模板默认值（记录内无存储值），"
                              "无需清理；要彻底去掉需模板作者改模板")
                else:
                    print("[clean] 清空字段失败：\n" + exc.describe(), file=sys.stderr)

    rows_cfg = cfg.get("clear_rows") or {}
    if not (rows_cfg.get("enabled") and rows_cfg.get("rules")):
        return

    for rule in rows_cfg["rules"]:
        module = rule["module"]
        marker = rule.get("marker_column")
        value_column = rule.get("value_column", "Materials")
        expected_mode = bool(rule.get("expected_rows"))
        if expected_mode:
            columns = list(rule.get("columns") or [])
            value_column = columns[0] if columns else value_column
        else:
            columns = [c for c in {value_column, marker} if c]
        try:
            arrays = read_columns(client, eln, title, module, columns)
        except MatElabError as exc:
            print(f"[clean] 读取 {module} 失败：{exc}", file=sys.stderr)
            continue
        if arrays is None:
            if verbose:
                print(f"[clean] {module}: 记录里没有该表格，跳过")
            continue
        hits = leading_rows_to_delete(arrays, rule) if expected_mode else plan_row_deletes(arrays, rule)
        if not hits:
            if verbose:
                print(f"[clean] {module}: 未发现模板示例行")
            continue
        if dry_run:
            print(f"[clean] {module}: 将删除第 {[h + 1 for h in hits]} 行（dry-run）")
            continue
        for index in sorted(hits, reverse=True):
            try:
                client.update(eln, sn, del_=[{"path": [module, index]}])
            except MatElabError as exc:
                print(f"[clean] 删除 {module} 第 {index + 1} 行失败："
                      + exc.describe(), file=sys.stderr)
                break
        if verbose:
            print(f"[clean] {module}: 已删除 {len(hits)} 行模板示例")
        try:
            after = read_columns(client, eln, title, module, columns)
            if after is not None and verbose:
                remaining = [v for v in (after.get(value_column) or []) if v not in (None, "")]
                print(f"[clean] {module} 现有 {len(remaining)} 行：{remaining}")
        except MatElabError:
            pass
