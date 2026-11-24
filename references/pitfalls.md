# 踩坑清单

## 1. 字段名对不上 → code 2

`data` 的一级 key 必须是模板里的**模块名**，二级 key 必须是**字段名/列名**。
中文括号 `（）` 与英文 `()`、全角 `％` 与半角 `%` 都算不同。
改模板后只改 `matelab.yaml` 的 `field_map`，不要动代码。
定位方法：`check_template.py` 会逐字段报错；服务端 msg 常带模块名与字段名。

## 2. 强类型

- 数字字段传 `"625"` → 有的版本报 `应为数字`，脚本会自动转 float，转不了就报错。
- 数字字段不要放 `~625`、`未测`、`625 左右`。
- 日期必须是 `2000-12-31`；`2026/9/24` 会自动规范。
- 级联值必须在模板备选里，多一个空格都可能不过。

## 3. 表格是列式存储

`data[模块][列名] = [行1, 行2, ...]`，所有列长度必须相等。
空单元格用 `null`，不要用 `""`（数字列会炸）。

## 4. 上传 name 不是文件名

- `name` 是引用键，全局唯一且 ≤45 字符；重复会直接报错。
- 真实文件名放 multipart 的 `filename`，下载后仍可辨认。
- 生成规则：`{uid}_{序号:02d}_{hash6}`，例如 `XY101-20260924-01_01_a1b2c3`。

## 5. uid 撞车

导入用 `uid` 全局唯一，重复直接报错，且没有「先查后导」的原子保证。
做法：uid 里带日期与序号；同时写入模板「记录UID」字段，上传前用 `search` 查一次。

## 6. base64 白名单

`#base64{}` 只接受 `pdf|zip|doc|docx|png|gif|tiff|jpg|jpeg|svg`（注意没有 `docx` 之外的 Office 格式，没有 `txt`、`.xy`、`.dat`、`.gk`）。
所以一律走 `/eln_api/upload` 拿 `#file{}`；实在传不上就打包 zip。

## 7. 分片上传

- 单片 ≤20MB，建议 <10MB；**必须串行**，多线程会拼坏文件。
- 同一文件的 `uid` 必须一致，不同文件不能共用。
- 每片带上 `offset` 与 sha256 更稳；末片 `last=1` 才会返回 `name`。

## 8. 代理与内网

`eln.iphy.ac.cn:61262` 是内网服务。若环境有 `HTTP_PROXY=http://127.0.0.1:7890`，请求会被代理拦成超时。
把 `eln.iphy.ac.cn`、`matelab.iphy.ac.cn` 加入 `NO_PROXY`（脚本已按 `config.no_proxy` 自动设置）。

## 9. Windows / 中文路径

- PowerShell 会吃掉参数引号，所有命令用 `cmd /c`。
- 中文路径必须绝对路径，且用双引号包住。
- `python -X utf8` 避免 GBK 编码报错。

## 10. docx 抽取

- `python-docx` 只能按文档流取段落与表格，**浮动图片的位置拿不准**，表内图片也不保证顺序。
- 抽取结果会写明「图片顺序需人工确认」；看 `images/` 后手工对应说明。
- 合并单元格的表格按左上角值展开，可能出现重复值。

## 11. 记录定稿后不可改

网页端「定稿」后数据受保护，只能生成新版本。脚本默认不定稿；需要定稿请人工在网页操作。

## 12. 批量导入

单次最多 100 条，且必须同一模板 + 同一记录本。批量时按模板分组，逐组调用。
一条失败会导致整批失败，所以先 dry-run 校验全部，再一次性提交。

## 13. 时间戳

`datetime_create` 是服务器时间，可能与你本地差 8 小时。回执里以服务器返回为准，不要用本地时间反推。

## 14. 实测记录（2026-11，项目 matElab试用）

- `/eln_api/elns` 的真实返回不是数组，而是分组对象：`{"code":0,"my":[],"share":[],"public":[{...}]}`。
  只看顶层会误判成「没有记录本」。客户端已按 `my/share/public` 分组展开。
- 公共记录本（`public` 分组里的「全局共享」）**不能**用 `/eln_api/items` 查询，返回 `code: 2`「记录本不存在」。
  公共记录本只能看，不能通过 API 写入；必须在 `my` 分组里有自己的记录本。
- 账号没有自己的记录本时，`elns` 只返回 public，`check_template.py` 会在第一步 `items` 就失败。
  正确顺序：先在网页建记录本 → 再建模板 → 再跑 `check_template.py`。
- token 缓存在项目 `.matelab/token.json`，换账号或换服务器会自动失效（按 `base_url` + `username` 判定）。
- 真实 EDS docx（`20260910_XY_BiSITe_*.docx`）内容全是谱图截图 + 4 张谱表（Spectrum 274~277），没有正文段落。
  `extract_docx.py` 会输出 `paragraphs=0 tables=4 images=6`，这是正常的。录入时选一张代表性谱（如最后一列数据）
  或把 4 个谱做平均，并在 `narrative.conclusion` 里写明取值方式。
- `XY101-BiSiTe3.md` 里 3 张 `--/Pasted image ...png` 实体不在 `实验记录/` 目录，抽取时会列为 missing；
  需要把 Obsidian vault 的附件目录加进 `attachment_roots`，否则这 3 张图不会上传。

## 15. 真实上传踩到的坑（2026-11-23，模板 Flux method simit）

- **模板里有下拉字段**：`Experiment condition.Crucible`、`Atmosphere condition`、`End temperature`
  以及 `Start materials.Materials Form` 是选择类字段（有预设备选项），传自由文本会报
  `值不在备选项中`。导入格式 JSON 里只显示 `["文本","文本"]`，看不出候选项，只能去网页看下拉。
  未拿到选项前，这些字段不写，原值并入 `Note`。
- **`code: 4` 才有 `errs`**：`/eln_api/import` 字段级错误返回 `code 4` + `errs` 数组，
  每条形如 `值不在备选项中 (dataset：0, 模块: Experiment condition, 项目: Crucible)`。
  客户端已把 `errs` 打印出来；只看 `msg`（"多处错误"）没有任何信息量。
- **`items.sn` 就是导入时的自定义 uid**：删除/更新都按它定位（`/eln_api/delete` 的 `uids`）。
  `id` 是数字主键，不能拿去删除（会报「记录不存在」）。
- **`/eln_api/search` 的 `keyword` 过滤不可靠**：传一个不存在的关键词仍会返回无关记录
  （实测返回标题 `test` 的记录且 `p1` 是别的值）。所以查重不能靠 keyword，只能 `items` + `sn` 精确比对。
- **search 返回行结构**：`{id, eln_id, uid, title, eln_name, p1: 值, p2: 值}`；
  `uid` 才是记录 UID，`p*` 是请求里自定义的字段名。
- **文件字段取详情**：`path=["Experiment result","Photos",0]` 返回 `{hash,size,filename,timestamp}`，
  再往下加 `"url"` 取不到（为 null）。校验文件是否真的存在，用「数组 + 下标」而不是 `url`。
- **上传 name 冲突**：同名文件重复上传会报错；重试要换 name，并且必须把 payload 里**所有**
  文件字段（Photos / XRD measurements / EDS measurements 都算）的引用一起换掉，
  否则记录里会留下指向失败上传的 `#file{}`。
- `.gk` 这类自定义扩展名可以正常上传（走 `/eln_api/upload` 通道），不受 base64 白名单限制。

## 16. 模板自带的预填值会继承到每条新记录

`Flux method simit` 模板里预填了默认数据，新建记录（包括 API 导入）会原样继承：

| 字段 | 预填值 |
|---|---|
| Experiment condition / Crucible | `Alumina` |
| Experiment condition / Atmosphere condition | `Vacuum` |
| Experiment condition / End temperature | `Nature cooling` |
| Start materials / Materials Form | `Ball` / `Ball` / `Powder` |
| Experiment result / Product | `Single crystals` |
| Upload information / Materials Galaxy id | `SCG00000165` |

处理方式：`matelab.yaml` 的 `clear_inherited` 列出这些字段，导入成功后调用
`/eln_api/update` 的 `del` 逐项清空（表单字段用 `[模块, 字段]`，表格列用 `[模块, 列名]`）。
清理只在**新导入**时执行（`--update` 覆盖模式不清理，避免误删人工填写内容），
可用 `--no-clear` 关闭。信息本身已写进 `Note`，不会丢。

**重要**：这些单字段默认值**不是记录里的存储值**，而是模板在读取时套用的默认值。
对它们调 `/eln_api/update` 的 `del` 会返回 `path 1 指定的项不存在`，即**无法通过 API 清掉**；
`clear_inherited` 实际只是空操作（脚本会识别这个报错并提示）。
要彻底去掉，只能由模板作者在网页端改模板（模板没有开放写接口）。

能清的只有**行**（见下一条）：表格里的示例行是创建记录时复制进数据的，可以删。

## 17. 导入表格是「追加行」，不是覆盖

实测：XY101 记录的 `Materials Form` 返回 6 个值 `Ball, Ball, Powder, null, null, null` ——
前 3 个是模板示例行，后 3 个才是导入的数据。说明 `/eln_api/import` 往表里**追加**行，
模板自带的示例行（Cr / Ge / Te，配比 1:1:10，Te 摩尔质量写成 1276 = 127.6×10）会一直留着。

清理方式：`clear_rows` 规则按「marker 列非空」定位示例行（如 `Actual mass` 列，
我们的数据从不写它），再用 `/eln_api/update` 的 `del: [{path:[模块, 行号]}]` **倒序**删行。
注意 `del` 的 path 末位是整数=删行、字符串=删列。

对已写入的记录单独清理：

```bat
python -X utf8 clean_record.py --config matelab.yaml --uid BP-144-20260916-01          :: 预览
python -X utf8 clean_record.py --config matelab.yaml --uid BP-144-20260916-01 --yes    :: 执行
```

`upload_record.py` 在导入成功后会自动调用同一套清理（`--no-clear` 关闭）。

## 18. 记录被网页端打开时，API 写入不落库

如果某条记录正在浏览器里编辑，`/eln_api/update` 会返回
`{"code":0,"msg":"协同编辑数据更新完成"}`——看着成功，但改的是「当前编辑版本」，
**不写数据库**，于是随后 `search` 读到的还是旧值（甚至模板默认值 + 已删行混在一起）。

识别方法：比对 `items` 的 `datetime_modify`——没变就说明没落库；
或删行时返回 `path 1 指定的行不存在`，说明存储里根本没这些行（search 里看到的是会话/模板合成的视图）。

处理：让用户在网页上关闭（或保存）该记录，再重跑 `clean_record.py`。

## 19. Note 不是独立模块，而是 Experiment result 里的 richtext 字段

「查看模板导入格式」的 JSON 里 `Note` 看起来像独立模块（`"Note": "文本"`），但实际记录结构中
它是 **`Experiment result` 模块里的一个 richtext 字段**（同模块还有 Photos / XRD result / EDS result 等）。

后果：如果按 `module: "Note"` 写入，导入**不会报错**，但内容被静默丢弃，记录里仍显示模板预填的默认文字
（例如「失败，生长出GeTe2单晶」）。

正确配置：

```yaml
_derived.note:
  module: "Experiment result"
  field: "Note"
  type: html
```

对已写入的记录补写：

```bat
python -X utf8 clean_record.py --config matelab.yaml --sync-note --record record.json --yes
```

模板里的默认文字无法用 API 删除，只能模板作者在网页编辑模板时清空。

## 20. 表单里的多文件字段（Photos）无法用 API 追加

实测（2026-11，记录 XY047 / 模板 CVT）：给 `Experiment result.Photos` 追加一张图，官方文档给的两条规则
都会失败：

| 尝试 | 返回 |
|---|---|
| `add` + `data: ["#file{x}"]` | `值应为字符串(add: 0)` |
| `add` + `data: "#file{x}"` | `data 应为数组` |
| `add` + `data: [{"file": "#file{x}", "text": "..."}]` | `值应为字符串(add: 0)` |
| `add` + `name` 改用字段 uid | `data 应为数组` |
| `add` + `data: ""` | `值应为字符串(add: 0)` |
| `modify` `path: [模块, "Photos", 8]`（追加位） | `path 2 指定的项不存在` |
| `modify` `path: [模块, "Photos", -1]` | `Server Error (code 3)` |

结论：该版本对「表单里的 file 字段」的 `add` 校验有缺陷（字段明明存在却报 `data 应为数组`，
换成数组又报 `值应为字符串`），**无法通过 API 追加**。

可用的替代方案：

1. **网页手工拖入**（最省事）：记录 → 对应模块 → 文件字段 → 拖文件；
2. **重建记录**：`/eln_api/export` 导出原始数据（文件是 `{hash,size,filename}`），
   把旧文件转成 `#hash{filename,sha256}` 引用 + 新文件 `#file{name}`，用 `/eln_api/import`
   建一条新记录再删旧记录。代价是记录 ID 变化、历史丢失；
3. 先只上传文件（`/eln_api/upload` 拿到 `#file{name}`），等接口修复后再挂到记录上。

> `#hash{filename,sha256hex}` 只对「本人上传过 / 当前记录已有 / 有权限的其他记录本」的文件有效，
> 拿不到 hash 时不要硬编。

### 绕过：先删字段、再重建（**只在字段为空时安全**）

```python
client.update(eln, uid, del_=[{"path": ["Experiment result", "Photos"]}])
client.update(eln, uid, add=[{
    "module": "Experiment result", "type": "file", "name": "Photos",
    "data": ["#file{new}"],
}])
```

原理：字段不存在时，`add` 会走「必须数组」那条分支，正好避开写反的校验。

- **空字段**：实测 XY074 成功（删后 add 数组，写入 1 张 317608 B）。
- **非空字段**：**不要用**。XY047 实测（8 张）：`del` 后原文件被从存储删除，
  再用 `#hash{filename,sha256}` 重建时报「引用hash … 不存在」，下载接口也返回
  `File not found`——**原图不可恢复**，只能从本地重新上传。
  这说明 `/eln_api/export` 里的 hash 只在文件仍被记录引用时有效，记录一删就失效。

结论：只有当字段本来为空、或你有原始文件可以重新 `/eln_api/upload` 时，才用这个绕过；
否则老老实实在网页上拖文件。

客户端封装 `MatElabClient.replace_file_field(eln, uid, module, field, refs)`
同样有上述风险，`refs` 里的每个引用都必须能被服务端解析。
