#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""MatElab 数据 API 客户端。

只依赖 requests。作为库被 upload_record.py / check_template.py 调用，也可单独当 CLI 用：

    python -X utf8 matelab_client.py login
    python -X utf8 matelab_client.py items  --eln "晶体生长记录"
    python -X utf8 matelab_client.py search --eln "晶体生长记录" --path "基本信息/记录UID"
    python -X utf8 matelab_client.py upload --file "a.gk" --eln "晶体生长记录"
    python -X utf8 matelab_client.py delete --eln "晶体生长记录" --uid XXXX
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests

DEFAULT_BASE_URL = "https://eln.iphy.ac.cn:61262"
TOKEN_MARGIN_MS = 5 * 60 * 1000      # 提前 5 分钟刷新
MAX_REF_LEN = 45                     # MatElab name / uid 长度上限
CHUNK_SIZE = 8 * 1024 * 1024         # 单片 8MB，服务端上限 20MB


class MatElabError(RuntimeError):
    """接口返回的非 0 code，或网络异常。"""

    def __init__(self, message: str, *, code: Any = None, payload: Any = None,
                 status: Optional[int] = None, where: str = ""):
        self.code = code
        self.payload = payload
        self.status = status
        self.where = where
        prefix = f"[{where}] " if where else ""
        super().__init__(f"{prefix}{message}")

    def describe(self) -> str:
        lines = [str(self)]
        if self.code is not None:
            lines.append(f"  code = {self.code!r}")
        if self.status is not None:
            lines.append(f"  HTTP = {self.status}")
        msg = None
        if isinstance(self.payload, dict):
            msg = self.payload.get("msg") or self.payload.get("errmsg")
        if msg and msg not in str(self):
            lines.append(f"  msg  = {msg}")
        if isinstance(self.payload, dict):
            errs = self.payload.get("errs")
            if isinstance(errs, list):
                for err in errs:
                    lines.append(f"  err  = {err}")
            elif errs:
                lines.append(f"  err  = {errs}")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #

def load_config(path: str | os.PathLike) -> Dict[str, Any]:
    """读 yaml/json 配置，并把相对路径统一解析成绝对路径。"""
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise SystemExit(f"配置文件不存在: {p}")
    text = p.read_text(encoding="utf-8-sig")
    if p.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover
            raise SystemExit("需要 PyYAML：pip install pyyaml") from exc
        cfg = yaml.safe_load(text) or {}
    else:
        cfg = json.loads(text)

    cfg["_config_path"] = str(p)
    cfg.setdefault("base_url", DEFAULT_BASE_URL)
    cfg.setdefault("chunk_size", CHUNK_SIZE)
    cfg.setdefault("timeout", 60)
    cfg.setdefault("token_dir", ".matelab")

    root = Path(cfg.get("project_root") or p.parent).expanduser()
    cfg["_root"] = str(root.resolve())

    token_dir = Path(cfg["token_dir"])
    if not token_dir.is_absolute():
        token_dir = root / token_dir
    cfg["_token_dir"] = str(token_dir.resolve())

    roots = []
    for item in cfg.get("attachment_roots") or []:
        rp = Path(item)
        roots.append(str((rp if rp.is_absolute() else root / rp).resolve()))
    if not roots:
        roots = [str(root.resolve())]
    cfg["attachment_roots_resolved"] = roots
    return cfg


def _credential(cfg: Dict[str, Any], key: str) -> str:
    env_name = (cfg.get("credential_env") or {}).get(key)
    if not env_name:
        raise SystemExit(f"配置 credential_env.{key} 未设置")
    value = os.environ.get(env_name)
    if not value:
        raise SystemExit(
            f"环境变量 {env_name} 未设置。先执行：setx {env_name} \"...\" 然后重开终端"
        )
    return value


def _configure_no_proxy(hosts: Iterable[str]) -> None:
    """把内网域名并入 NO_PROXY，避免被 127.0.0.1:7890 代理拦截。"""
    hosts = [h for h in hosts if h]
    if not hosts:
        return
    current = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    parts = [p.strip() for p in current.split(",") if p.strip()]
    for h in hosts:
        if h not in parts:
            parts.append(h)
    merged = ",".join(parts)
    os.environ["NO_PROXY"] = merged
    os.environ["no_proxy"] = merged


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".emf"}


def resolve_attachment(name: str, roots: Iterable[str | os.PathLike]) -> Optional[str]:
    """在搜索根中定位附件：绝对路径 → 精确路径 → 同名文件 → 同 stem → 模糊包含。"""
    raw = name.split("|")[0].strip()      # Obsidian ![[a.png|300]]
    raw = raw.split("#")[0].strip()
    if not raw:
        return None
    roots = [Path(r) for r in roots]
    p = Path(raw)
    if p.is_absolute() and p.exists():
        return str(p.resolve())
    for root in roots:
        direct = root / raw
        if direct.exists():
            return str(direct.resolve())

    target = Path(raw).name
    stem = Path(target).stem
    for root in roots:
        if not root.exists():
            continue
        for f in root.rglob(target):
            if f.is_file():
                return str(f.resolve())
    for root in roots:
        if not root.exists():
            continue
        for f in root.rglob(f"{stem}.*"):
            if f.is_file():
                return str(f.resolve())
    if stem:
        for root in roots:
            if not root.exists():
                continue
            for f in root.rglob(f"*{stem}*"):
                if f.is_file() and f.suffix.lower() in IMAGE_EXTS | {".docx", ".pdf"}:
                    return str(f.resolve())
    return None


# --------------------------------------------------------------------------- #
# 客户端
# --------------------------------------------------------------------------- #

class MatElabClient:
    def __init__(self, cfg: Dict[str, Any], *, verbose: bool = True):
        self.cfg = cfg
        self.base_url = str(cfg["base_url"]).rstrip("/")
        self.timeout = int(cfg.get("timeout", 60))
        self.chunk_size = int(cfg.get("chunk_size", CHUNK_SIZE))
        self.verbose = verbose
        self.username = _credential(cfg, "username")
        self.password = _credential(cfg, "password")
        self.token_path = Path(cfg["_token_dir"]) / "token.json"
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        _configure_no_proxy(cfg.get("no_proxy") or [])
        self.session = requests.Session()
        self.session.trust_env = True
        self._tokens: Dict[str, Any] = self._load_tokens()
        self.user_info: Dict[str, Any] = self._tokens.get("user") or {}

    # ---------------- token ---------------- #

    def _load_tokens(self) -> Dict[str, Any]:
        if not self.token_path.exists():
            return {}
        try:
            data = json.loads(self.token_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if data.get("base_url") and data["base_url"] != self.base_url:
            return {}
        if data.get("username") and data["username"] != self.username:
            return {}
        return data

    def _save_tokens(self, payload: Dict[str, Any]) -> None:
        data = {
            "base_url": self.base_url,
            "username": self.username,
            "access": payload.get("access") or self._tokens.get("access"),
            "refresh": payload.get("refresh") or self._tokens.get("refresh"),
            "user": {
                "userid": payload.get("userid", self.user_info.get("userid")),
                "realname": payload.get("realname", self.user_info.get("realname")),
                "email": payload.get("email", self.user_info.get("email")),
            },
            "saved_at": int(time.time() * 1000),
        }
        self._tokens = data
        self.user_info = data["user"]
        self.token_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            os.chmod(self.token_path, 0o600)
        except OSError:
            pass

    def login(self) -> str:
        url = f"{self.base_url}/tokens"
        if self.verbose:
            print(f"[auth] 登录 {self.username} ...", file=sys.stderr)
        resp = self.session.post(
            url,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={"username": self.username, "password": self.password},
            timeout=self.timeout,
        )
        try:
            data = resp.json()
        except ValueError:
            raise MatElabError(
                f"登录返回非 JSON（HTTP {resp.status_code}）：{resp.text[:200]}",
                status=resp.status_code, where="auth",
            )
        if data.get("code") != 0:
            raise MatElabError(
                data.get("msg") or "登录失败", code=data.get("code"),
                payload=data, status=resp.status_code, where="auth",
            )
        self._save_tokens(data)
        return data["access"]["token"]

    def refresh(self) -> str:
        refresh_token = (self._tokens.get("refresh") or {}).get("token")
        if not refresh_token:
            return self.login()
        resp = self.session.post(
            f"{self.base_url}/tokens/tokens_refresh",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={"refresh_token": refresh_token},
            timeout=self.timeout,
        )
        try:
            data = resp.json()
        except ValueError:
            return self.login()
        if data.get("code") != 0:
            if self.verbose:
                print("[auth] refresh token 失效，重新登录", file=sys.stderr)
            return self.login()
        self._save_tokens(data)
        return data["access"]["token"]

    def token(self, force_refresh: bool = False) -> str:
        access = self._tokens.get("access") or {}
        token = access.get("token")
        expired_at = access.get("expiredAt") or 0
        now_ms = int(time.time() * 1000)
        if token and not force_refresh and expired_at - now_ms > TOKEN_MARGIN_MS:
            return token
        if force_refresh or (self._tokens.get("refresh") or {}).get("token"):
            return self.refresh()
        return self.login()

    # ---------------- 请求封装 ---------------- #

    def _handle(self, resp: requests.Response, where: str) -> Dict[str, Any]:
        try:
            data = resp.json()
        except ValueError:
            raise MatElabError(
                f"返回非 JSON（HTTP {resp.status_code}）：{resp.text[:300]}",
                status=resp.status_code, where=where,
            )
        if not isinstance(data, dict):
            raise MatElabError(f"返回结构异常：{data!r}"[:300], where=where)
        code = data.get("code")
        if code == 0:
            return data
        if code == "refresh":
            raise _NeedRefresh(where)
        raise MatElabError(
            data.get("msg") or data.get("errmsg") or "接口调用失败",
            code=code, payload=data, status=resp.status_code, where=where,
        )

    def post_json(self, path: str, payload: Dict[str, Any], where: str = "") -> Dict[str, Any]:
        where = where or path
        url = f"{self.base_url}{path}"
        for attempt in (0, 1):
            token = self.token(force_refresh=attempt > 0)
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            }
            try:
                resp = self.session.post(
                    url, headers=headers, data=json.dumps(payload), timeout=self.timeout
                )
            except requests.RequestException as exc:
                raise MatElabError(f"网络错误：{exc}", where=where) from exc
            try:
                return self._handle(resp, where)
            except _NeedRefresh:
                if attempt == 0:
                    if self.verbose:
                        print(f"[auth] {where} token 过期，刷新后重试", file=sys.stderr)
                    continue
                raise MatElabError("token 刷新后仍失效", code="refresh", where=where)
        raise MatElabError("请求未完成", where=where)

    # ---------------- 业务接口 ---------------- #

    def elns(self) -> List[Dict[str, Any]]:
        """列出记录本：my=自己的、share=共享给我、public=公共。"""
        data = self.post_json("/eln_api/elns", {}, where="elns")
        out: List[Dict[str, Any]] = []
        for group in ("my", "share", "public", "elns", "items", "list"):
            value = data.get(group)
            if isinstance(value, list):
                for entry in value:
                    if isinstance(entry, dict):
                        item = dict(entry)
                        item.setdefault("_group", group)
                        out.append(item)
        if out:
            return out
        if data.get("showtext"):
            item = dict(data)
            item.setdefault("_group", "my")
            return [item]
        return []

    def items(self, eln: str, user: Optional[str] = None) -> List[Dict[str, Any]]:
        payload: Dict[str, Any] = {"eln": eln}
        if user:
            payload["user"] = user
        data = self.post_json("/eln_api/items", payload, where="items")
        return data.get("items") or []

    def search(self, requests_: List[Dict[str, Any]], *, eln: Optional[List[Any]] = None,
               keyword: Optional[List[str]] = None, date_start: Optional[str] = None,
               date_end: Optional[str] = None) -> List[Dict[str, Any]]:
        payload: Dict[str, Any] = {"requests": requests_}
        if eln:
            payload["eln"] = eln
        if keyword:
            payload["keyword"] = keyword
        if date_start:
            payload["date_start"] = date_start
        if date_end:
            payload["date_end"] = date_end
        data = self.post_json("/eln_api/search", payload, where="search")
        return data.get("data") or []

    def import_records(self, eln: str, template: str, dataset: List[Dict[str, Any]],
                       user: Optional[str] = None, template_user: Optional[str] = None,
                       quote: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        if not 1 <= len(dataset) <= 100:
            raise ValueError(f"dataset 必须 1~100 条，当前 {len(dataset)} 条")
        payload: Dict[str, Any] = {"eln": eln, "template": template, "dataset": dataset}
        if user:
            payload["user"] = user
        if template_user:
            payload["template_user"] = template_user
        if quote:
            payload["quote"] = quote
        return self.post_json("/eln_api/import", payload, where="import")

    def export_records(self, eln: str, uids: List[str]) -> List[Dict[str, Any]]:
        """按记录 UID 导出原始数据结构（含文件的 hash/size/filename）。"""
        payload = {"uids": [{"eln": eln, "uid": uid} for uid in uids]}
        data = self.post_json("/eln_api/export", payload, where="export")
        return data.get("dataset") or []

    def create_empty(self, eln: str, title: str, *, user: Optional[str] = None,
                     path: Optional[List[str]] = None, uid: Optional[str] = None,
                     describe: Optional[str] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"eln": eln, "title": title}
        if user:
            payload["user"] = user
        if path:
            payload["path"] = path
        if uid:
            payload["uid"] = uid
        if describe:
            payload["describe"] = describe
        return self.post_json("/eln_api/create", payload, where="create")

    def delete(self, eln: str, uids: List[str], *, user: Optional[str] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"eln": eln, "uids": uids}
        if user:
            payload["user"] = user
        return self.post_json("/eln_api/delete", payload, where="delete")

    def update(self, eln: str, record_uid: str, *, modify: Optional[List[Dict[str, Any]]] = None,
               add: Optional[List[Dict[str, Any]]] = None,
               del_: Optional[List[Dict[str, Any]]] = None,
               add_module: Optional[List[Dict[str, Any]]] = None,
               del_module: Optional[List[Dict[str, Any]]] = None,
               user: Optional[str] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"eln": eln, "uid": record_uid}
        if user:
            payload["user"] = user
        if modify:
            payload["modify"] = modify
        if add:
            payload["add"] = add
        if del_:
            payload["del"] = del_
        if add_module:
            payload["addModule"] = add_module
        if del_module:
            payload["delModule"] = del_module
        if not any([modify, add, del_, add_module, del_module]):
            raise ValueError("update 至少需要一个 modify/add/del/addModule/delModule")
        return self.post_json("/eln_api/update", payload, where="update")

    # ---------------- 文件上传 ---------------- #

    def upload_file(self, file_path: str | os.PathLike, *, name: Optional[str] = None,
                    eln: Optional[str] = None, chunk_size: Optional[int] = None,
                    show_progress: bool = True) -> Dict[str, Any]:
        """分片串行上传，返回末片响应（含 name / url / size / hash）。"""
        path = Path(file_path)
        if not path.is_file():
            raise MatElabError(f"文件不存在: {path}", where="upload")
        size = path.stat().st_size
        chunk_size = int(chunk_size or self.chunk_size)
        if chunk_size > 20 * 1024 * 1024:
            raise ValueError("chunk_size 不能超过 20MB")
        ref_name = name or make_ref_name(path.stem, 0, path)
        if len(ref_name) > MAX_REF_LEN:
            ref_name = ref_name[:MAX_REF_LEN]
        upload_uid = ref_name  # 每个文件独立会话，name 已保证唯一
        filename = path.name
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"

        offset = 0
        last_payload: Dict[str, Any] = {}
        with path.open("rb") as handle:
            index = 0
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                offset += len(chunk)
                is_last = offset >= size
                fields = {
                    "uid": upload_uid,
                    "name": ref_name,
                    "last": "1" if is_last else "0",
                    "offset": str(offset - len(chunk)),
                    "hash": hashlib.sha256(chunk).hexdigest(),
                }
                if eln:
                    fields["eln"] = eln
                url = f"{self.base_url}/eln_api/upload"
                data: Dict[str, Any] = {}
                error: Optional[Exception] = None
                for attempt in (0, 1):
                    token = self.token(force_refresh=attempt > 0)
                    headers = {"Authorization": f"Bearer {token}"}
                    try:
                        resp = self.session.post(
                            url, headers=headers, data=fields,
                            files=[("file", (filename, chunk, mime))],
                            timeout=max(self.timeout, 120),
                        )
                        try:
                            data = self._handle(resp, "upload")
                        except _NeedRefresh:
                            if attempt == 0:
                                continue
                            raise MatElabError("token 刷新后仍失效", code="refresh", where="upload")
                        error = None
                        break
                    except MatElabError as exc:
                        error = exc
                        if exc.code in (1, "refresh") and attempt == 0:
                            continue
                        raise
                if error:
                    raise error
                if is_last:
                    last_payload = data
                index += 1
                if show_progress:
                    pct = 100.0 * offset / size if size else 100.0
                    print(
                        f"\r[upload] {filename} {offset}/{size} ({pct:5.1f}%) 片#{index}",
                        end="" if not is_last else "\n", file=sys.stderr, flush=True,
                    )
        if not last_payload:
            raise MatElabError(f"上传未拿到末片响应: {filename}", where="upload")
        last_payload.setdefault("name", ref_name)
        last_payload["local_path"] = str(path.resolve())
        last_payload["local_size"] = size
        return last_payload

    # ---------------- 记录 UID 解析 ---------------- #

    def find_record_uids(self, eln: str, marker: str, *,
                         module: Optional[str] = None, field: Optional[str] = None,
                         user: Optional[str] = None, keyword_only: bool = False) -> List[str]:
        """按自定义 uid（marker）找到平台记录 UID（即 items 里的 sn），用于 delete / update。

        实测：/eln_api/items 的 sn 就是记录 UID（导入时自定义的 uid 会原样出现在 sn 里）。
        """
        try:
            items = self.items(eln, user)
        except MatElabError:
            items = []
        uids: List[str] = []
        for item in items:
            sn = str(item.get("sn") or "")
            keywords = [str(k) for k in (item.get("keywords") or [])]
            if sn == marker or marker in keywords:
                if sn:
                    uids.append(sn)
        if uids or keyword_only:
            return uids

        # 兜底：用检索接口（某些版本 items 不返回 sn）
        if module and field:
            try:
                rows = self.search(
                    [{"uid": "__marker__", "path": [module, field]}],
                    eln=[eln], keyword=[marker],
                )
            except MatElabError:
                return []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                candidate = row.get("uid")
                if isinstance(candidate, str) and candidate:
                    uids.append(candidate)
        return uids


class _NeedRefresh(Exception):
    pass


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #

def make_ref_name(prefix: str, index: int, file_path: str | os.PathLike,
                  limit: int = MAX_REF_LEN) -> str:
    """生成上传引用名：{prefix}_{序号:02d}_{hash6}，总长 ≤45。

    文件不存在时也能生成（预览/示例场景），size 记为 0。
    """
    path = Path(file_path)
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    h = hashlib.sha256(f"{path.name}:{size}".encode("utf-8")).hexdigest()[:6]
    safe_prefix = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in prefix)
    suffix = f"_{index:02d}_{h}"
    room = limit - len(suffix)
    if room <= 0:
        return suffix[-limit:]
    return (safe_prefix[:room] or "f") + suffix


def format_ts(ms: Optional[int]) -> str:
    if not ms:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ms / 1000))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _client_from_args(args: argparse.Namespace) -> MatElabClient:
    cfg = load_config(args.config)
    return MatElabClient(cfg, verbose=not getattr(args, "quiet", False))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="MatElab 数据 API 客户端")
    parser.add_argument("--config", default="matelab.yaml", help="配置文件路径")
    parser.add_argument("--quiet", action="store_true", help="不打印进度")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("login", help="登录并缓存 token")
    sub.add_parser("elns", help="列出可编辑的记录本")

    p_items = sub.add_parser("items", help="列出记录本内的记录")
    p_items.add_argument("--eln", required=True)
    p_items.add_argument("--user", default=None)

    p_search = sub.add_parser("search", help="按 模块/字段 路径检索")
    p_search.add_argument("--eln", action="append", default=None)
    p_search.add_argument("--path", action="append", required=True,
                          help="形如 基本信息/记录UID，可重复")
    p_search.add_argument("--keyword", action="append", default=None)

    p_upload = sub.add_parser("upload", help="上传单个文件")
    p_upload.add_argument("--file", required=True)
    p_upload.add_argument("--eln", default=None)
    p_upload.add_argument("--name", default=None)

    p_delete = sub.add_parser("delete", help="按平台记录 UID 删除记录")
    p_delete.add_argument("--eln", required=True)
    p_delete.add_argument("--uid", action="append", required=True)

    p_import = sub.add_parser("import", help="直接导入一个 payload.json（排错用）")
    p_import.add_argument("--payload", required=True)

    args = parser.parse_args(argv)
    client = _client_from_args(args)

    try:
        if args.cmd == "login":
            client.login()
            print(f"登录成功: {client.user_info.get('realname')} "
                  f"(userid={client.user_info.get('userid')})")
            print("token 缓存:", client.token_path)
        elif args.cmd == "elns":
            rows = client.elns()
            if not rows:
                print("（账号下暂无记录本，请先在网页创建）")
            for row in rows:
                print(f"{row.get('_group', '?'):6} | {row.get('showtext')} "
                      f"| id={row.get('id')} | 定稿 {row.get('num', 0)} "
                      f"| owner={row.get('owner')}")
        elif args.cmd == "items":
            items = client.items(args.eln, args.user)
            print(json.dumps(items, ensure_ascii=False, indent=2))
        elif args.cmd == "search":
            reqs = [{"uid": f"p{i + 1}", "path": p.split("/")}
                    for i, p in enumerate(args.path)]
            rows = client.search(reqs, eln=args.eln, keyword=args.keyword)
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        elif args.cmd == "upload":
            info = client.upload_file(args.file, name=args.name, eln=args.eln)
            print(json.dumps(info, ensure_ascii=False, indent=2))
        elif args.cmd == "delete":
            print(json.dumps(client.delete(args.eln, args.uid), ensure_ascii=False))
        elif args.cmd == "import":
            payload = json.loads(Path(args.payload).read_text(encoding="utf-8-sig"))
            resp = client.import_records(
                payload["eln"], payload["template"], payload["dataset"],
                user=payload.get("user"), template_user=payload.get("template_user"),
            )
            print(json.dumps(resp, ensure_ascii=False, indent=2))
    except MatElabError as exc:
        print(exc.describe(), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
