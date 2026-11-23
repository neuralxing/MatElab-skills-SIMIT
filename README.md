# matelab-experiment-log

把晶体生长与表征的零散记录整理成结构化数据，通过 MatElab 数据 API 上传到电子实验记录本。

## 它解决什么

手工在网页上填一条实验记录要 10~20 分钟，XRD/EDS/PPMS 的数据文件还得一个个传。
这个 skill 做的是：

1. 从 docx 报告、Markdown 笔记、Excel、图片、数据文件里抽信息（表格、成分、工艺参数）；
2. 统一成 `record.json`（英文 key、单位规范）；
3. 按模板映射成 MatElab 需要的 `data` 结构；
4. 先 dry-run 给你看「将要写进去的每一格」；
5. 确认后上传附件 + 导入记录 + 清理模板继承数据 + 回读校验 + 写回执。

## 特性

- **配置驱动**：模板改名只改 `field_map`，不动代码；Flux / CVT 两套模板配置已附。
- **先预览后写入**：默认 dry-run，`--yes` 才 POST。
- **幂等**：uid 规则化 + `items.sn` 精确查重，重复导入会跳过或提示更新。
- **附件分片上传**：8 MB 分片串行，`name` 冲突自动换名，`.gk/.dat/.xy` 也能传。
- **模板继承清理**：模板自带的示例行会随导入追加，脚本按规则删行、清字段。
- **回执可追溯**：`.matelab/upload_manifest.csv` + 每条记录一份 Markdown 回执。

## 安装

把本目录放到 CodeBuddy 的项目级或用户级 skills 目录：

```
<PROJECT_ROOT>/.codebuddy/skills/matelab-experiment-log/    # 项目级
~/.codebuddy/skills/matelab-experiment-log/                 # 用户级
```

依赖：Python 3.10+，`pip install requests pyyaml python-docx openpyxl pillow`。

## 配置

1. 环境变量（密码不落盘）：

   ```bat
   setx eln_username "你的用户名"
   setx eln_password "你的密码"
   ```

2. 复制配置到项目根（仓库根的示例已内置 `no_proxy` 内网域名）：

   ```bat
   cmd /c copy "<SKILL>\matelab.example.yaml" "<PROJECT_ROOT>\matelab.yaml"
   ```

   按实际改 `eln_name`（记录本）、`template_name`（模板）、`attachment_roots`（附件搜索目录）。
   `matelab.yaml` 被 `.gitignore` 排除（每人的记录本与附件目录不同），所以仓库只放示例。

3. 模板准备（三选一）：

   | 方案 | 配置 | 说明 |
   |---|---|---|
   | 已有 Flux 模板 | `matelab.yaml` | 见 `references/template-flux-method-simit.md` |
   | 已有 CVT 模板 | `matelab_cvt.yaml` | 见 `assets/config.cvt-example.yaml` |
   | 自建中文模板 | `matelab.yaml` + 改 `field_map` | 按 `references/template-spec.md` 在网页建模板 |

4. 模板自检：

   ```bat
   cmd /c python -X utf8 "<SKILL>\scripts\check_template.py" --config "<PROJECT_ROOT>\matelab.yaml"
   ```

   会导入一条 `uid=__probe_xxx__` 的记录、回读校验、然后删除（`--keep` 可保留）。

## 一次典型使用

```bat
:: 1) 抽取 docx（文本、表格、内嵌图）
cmd /c python -X utf8 "<SKILL>\scripts\extract_docx.py" "实验记录\报告.docx" -o "实验记录\_extract\xy101" --images

:: 2) 按 templates\record.example.json 写 record.json

:: 3) 预览
cmd /c python -X utf8 "<SKILL>\scripts\upload_record.py" --record "record.json" --config "matelab.yaml" --dry-run

:: 4) 确认后写入
cmd /c python -X utf8 "<SKILL>\scripts\upload_record.py" --record "record.json" --config "matelab.yaml" --yes
```

产物：

| 路径 | 内容 |
|---|---|
| `.matelab/token.json` | access/refresh token 缓存（含过期时间） |
| `.matelab/upload_manifest.csv` | uid、记录 ID、记录本、模板、时间、附件数、状态 |
| `.matelab/records/{uid}.md` | 单条记录回执 |
| `record_payload.json` | 实际发送的 import 请求体，排错用 |

## 文件结构

```
matelab-experiment-log/
├── SKILL.md                        # agent 入口：工作流、排错表、红线
├── README.md
├── matelab.example.yaml            # Flux 配置示例（复制为 matelab.yaml）
├── assets/
│   ├── config.example.yaml         # 兼容副本（同根目录示例）
│   └── config.cvt-example.yaml     # CVT 模板配置
├── references/
│   ├── api.md                      # 接口细节与错误码
│   ├── field-mapping.md            # record.json 字段说明与单位约定
│   ├── pitfalls.md                 # 踩坑清单
│   ├── template-spec.md            # 自建中文模板的设计规格
│   └── template-flux-method-simit.md
├── scripts/
│   ├── matelab_client.py           # API 客户端（token/上传/导入/检索/删除/更新）
│   ├── extract_docx.py             # docx/md/数据文件抽取
│   ├── build_record.py             # record.json + field_map → payload
│   ├── upload_record.py            # 主 CLI（dry-run / --yes / --update）
│   ├── cleanup.py                  # 模板继承数据清理
│   ├── clean_record.py             # 对已有记录补清理
│   └── check_template.py           # 模板探针校验
└── templates/
    ├── record.example.json         # Flux 记录示例
    └── record.cvt-example.json     # CVT 记录示例
```

## 设计约束

- 模板改名不用改代码，只改 `field_map`。
- 表格列强类型：数值列不要放「未测」「—」。
- 附件 `name` 全局唯一且 ≤45 字符，生成规则 `{uid}_{序号:02d}_{hash6}`。
- 模板自带的下拉默认值无法通过 API 删除，只能模板作者在网页端改。
- `/eln_api/update` 的 `add`（给表格加行）实测不写值，补行只能删记录重传。

## License

MIT
