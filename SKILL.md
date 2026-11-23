---
name: matelab-experiment-log
description: "把晶体生长（助熔剂法/CVT/固相烧结）与 XRD/EDS/电学/磁学表征的实验记录（docx 报告、图片、口述文本、数据文件）整理成结构化记录，经 MatElab 数据 API 上传到指定记录本；含模板设计规格、上传前 dry-run 预览、幂等查重、模板继承数据清理与上传回执。触发词：整理实验记录、上传 MatElab、电子实验记录本、晶体生长记录、XRD/EDS 记录归档。"
version: 1.0.0
license: MIT
metadata:
  tags: [experiment, eln, matelab, crystal-growth, xrd, eds, ppms, automation]
---

# MatElab 实验记录整理与上传

把零散的实验原始材料整理成结构化记录，写入 MatElab 电子实验记录本。**默认只预览、不写入**，必须显式 `--yes` 才真正上传。

## 何时使用

- 用户给出一次晶体生长实验的 docx / 图片 / 口述 / 数据文件，要求「记到 MatElab / 电子实验记录本 / 上传实验记录」。
- 用户要把历史实验批量整理归档到 MatElab。
- 用户要检查记录本、模板字段是否就绪，或清理模板继承的预填数据。

## 前置条件（首次使用检查一遍）

1. **账号**：环境变量 `eln_username` / `eln_password` 已设置（绝不写进配置或回执）。
2. **配置**：项目根放一份 `matelab.yaml`（或 `matelab_cvt.yaml`）：

   ```bat
   cmd /c copy "<SKILL>\matelab.example.yaml" "<PROJECT_ROOT>\matelab.yaml"
   ```

3. **记录本**：MatElab 网页上已建好（`eln_name` 与网页显示完全一致）。
4. **模板**：网页上已有模板，且 `template_name` 与其完全一致。
   - 已有 Flux 模板 `Flux method simit`：见 `references/template-flux-method-simit.md`
   - 已有 CVT 模板 `CVT method simit`：用 `matelab_cvt.yaml`（见 `assets/config.cvt-example.yaml`）
   - 自建中文模板：按 `references/template-spec.md` 操作

   无论哪套，先跑一次模板探针：

   ```bat
   cmd /c python -X utf8 "<SKILL>\scripts\check_template.py" --config "<PROJECT_ROOT>\matelab.yaml"
   ```

5. **网络**：能直接访问 `eln.iphy.ac.cn:61262`。若开了本地代理，需把该域名加入 `NO_PROXY`。

> `<SKILL>` = `<项目根>/.codebuddy/skills/matelab-experiment-log`
> Windows 下所有命令用 `cmd /c`，不要用 PowerShell；中文路径一律加引号。

## 主工作流

### 第 1 步：抽取原始材料

```bat
cmd /c python -X utf8 "<SKILL>\scripts\extract_docx.py" "实验记录\报告.docx" -o "实验记录\_extract\xy101" --images
```

- docx：输出 `text.md`（段落 + 表格，保持顺序）、`tables.json`、`images/`（内嵌图导出）。
- md：解析 frontmatter、章节、表格、`![[...]]` 附件引用，并在 `attachment_roots` 中查找实体文件。
- 图片 / 数据文件：`--inspect` 看大小、编码、分隔符、数值列范围；图片可直接看图。
- 口述文本：无需脚本，直接进入第 2 步。

**关键信息缺失（温度、配比、样品编号、测试条件）必须问用户，禁止编造。**

### 第 2 步：写规范化 record.json

以 `<SKILL>\templates\record.example.json`（Flux）或 `record.cvt-example.json`（CVT）为骨架，
字段含义见 `references/field-mapping.md`。

```json
{
  "uid": "XY101-20260924-01",
  "sample_id": "XY101",
  "formula": "BiSiTe3",
  "exp_type": "Flux method",
  "date": "2026-09-24",
  "precursors": [{"name": "Bi", "purity": "99.999%", "ratio": 1, "mass_g": 0.209}],
  "growth": {"method": "Flux method", "crucible": "Al2O3 5 ml", "t_max": 625, "hold_h": 14, "cool_rate": 5},
  "heating": [
    {"temperature": 30, "time": 0, "total": 0},
    {"temperature": 625, "time": 8, "total": 0},
    {"temperature": 625, "time": 14, "total": 8},
    {"temperature": 450, "time": 35, "total": 22},
    {"temperature": 450, "time": null, "total": null}
  ],
  "eds_rows": [{"element": "Bi", "wt": 33.99, "at": 20.24, "note": "Spectrum 276"}],
  "narrative": {"summary": "...", "process": "...", "observations": "...", "conclusion": "..."},
  "attachments": [{"path": "实验记录/eds.docx", "text": "EDS 报告", "kind": "eds"}]
}
```

`uid` 规则 `{样品编号}-{YYYYMMDD}-{两位序号}`；只写已知字段，未知留 `null` 或省略。

### 第 3 步：预览（必做）

```bat
cmd /c python -X utf8 "<SKILL>\scripts\upload_record.py" --record "record.json" --config "matelab.yaml" --dry-run
```

预览会打印：目标记录本/模板、uid、每个模块的字段与值、附件清单（含大小与解析结果）、问题列表
（字段名不匹配、类型不符、附件缺失、选项不在备选）。**把预览给用户确认。**

### 第 4 步：上传写入

```bat
cmd /c python -X utf8 "<SKILL>\scripts\upload_record.py" --record "record.json" --config "matelab.yaml" --yes
```

流程：查重（`items.sn` / 关键词）→ 逐个附件上传拿 `#file{name}` → `/eln_api/import` 导入 →
清理模板继承数据（`clear_inherited` 字段 + `clear_rows` 示例行）→ `/eln_api/search` 回读关键字段 →
写 `.matelab/upload_manifest.csv` 与 `.matelab/records/{uid}.md` 回执。

- 记录已存在：默认跳过；需要覆盖加 `--update`（只 update 表单/表格字段，文件集需手工补）。
- 失败：请求体保留在 `_payload.json`，按下方排错表处理，不要盲目重试。

### 第 5 步：回执

把记录 ID、uid、附件数、回读结果告诉用户；提示记录在网页端可继续手工补充（定稿前可编辑）。

## 排错表

| 现象 | 原因 | 处理 |
|---|---|---|
| `code: 2`，msg 含模块名/字段名 | 字段名与模板不一致 | 逐字核对 `field_map`，注意中英文括号、全角半角 |
| `code: 4` + `errs` 含「值不在备选项中」 | 该字段是下拉/级联，有预设备选项 | 到网页看该字段选项，改成选项之一；拿不到选项就先不映射，原值写进 Note |
| `code: 1` | token 无效 | 检查 `eln_username`/`eln_password`，删 `.matelab/token.json` 重登 |
| `code: 'refresh'` | access token 过期 | 客户端自动刷新；仍失败说明 refresh token 也过期（>24h） |
| `uid 已存在` | 记录已导入过 | 加 `--update` 或换新 uid |
| 上传返回 name 冲突 | `name` 全局唯一，旧残留 | 客户端自动换名重传；仍冲突换 uid 前缀 |
| 附件 `.gk/.dat/.xy` 被拒 | base64 扩展名白名单不含该格式 | 走 `/eln_api/upload` 通道（默认行为） |
| `update` 返回 `协同编辑数据更新完成` 但没生效 | 记录正在网页端编辑 | 关闭/保存该记录后重跑 |
| 请求超时 / DNS 解析失败 | 不在内网或域名解析异常 | 确认能直接访问内网（无需 VPN）；`NO_PROXY` 加该域名 |
| docx 图片顺序错乱 | python-docx 不保证浮动图位置 | 抽取结果会标注「图片顺序需人工确认」，看图后手工排序 |

## 常用命令

```bat
:: 登录自检
cmd /c python -X utf8 "<SKILL>\scripts\matelab_client.py" login
:: 列账号下的记录本（my=自己的，share=共享，public=公共）
cmd /c python -X utf8 "<SKILL>\scripts\matelab_client.py" elns
:: 列记录本内记录
cmd /c python -X utf8 "<SKILL>\scripts\matelab_client.py" items --eln "<记录本名>"
:: 只看映射结果，不联网
cmd /c python -X utf8 "<SKILL>\scripts\build_record.py" --record record.json --config matelab.yaml -o payload.json --preview
:: 清理某条记录里继承的模板预填数据（默认 dry-run，加 --yes 才执行）
cmd /c python -X utf8 "<SKILL>\scripts\clean_record.py" --config matelab.yaml --uid <记录UID>
```

## 红线

- 不把密码、token 写进任何文件、日志或回执。
- 不修改原始实验资料。
- 不在未预览、未获用户确认的情况下写入 MatElab。
- 表格列是强类型的：数值列不要塞「未测」「—」，留空或改文本列。
- 一次 import 只能一个模板 + 一个记录本，跨记录本要多次调用。
