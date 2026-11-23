# record.json 字段说明与映射规则

`record.json` 是中间层：英文 key、单位规范化、不含任何 MatElab 专有命名。上传时由 `build_record.py` 按 `matelab.yaml` 的 `field_map` 翻译成模板字段。

## 一、顶层字段

| key | 类型 | 必填 | 对应模板字段 | 说明 |
|---|---|---|---|---|
| `uid` | str | ✓ | 基本信息/记录UID | 幂等键，`{样品编号}-{YYYYMMDD}-{两位序号}`，≤45 字符 |
| `title` | str | | dataset.title | 不填则自动 `{sample_id} {exp_type} {date}` |
| `sample_id` | str | ✓ | 基本信息/样品编号 | 如 `XY101` |
| `formula` | str | | 基本信息/化学式 | 化学式用半角数字，如 `BiSiTe3` |
| `exp_type` | str | ✓ | 基本信息/实验类型 | 必须在级联选项内 |
| `date` | str | ✓ | 基本信息/生长日期 | `YYYY-MM-DD`，脚本会规范 `2026/9/24`、`2026年9月24日` |
| `operator` | str | | 基本信息/操作人 | |
| `project` | str | | 基本信息/项目课题 | |
| `target` | str | | 基本信息/目标产物 | |
| `keywords` | list[str] | | dataset.keyword | 脚本用 `;` 连接 |
| `path` | list[str] | | dataset.path | 分类路径，如 `["Bi-Si-Te"]` |
| `precursors` | list[obj] | | 原料配比（表格） | 列式写入，见下 |
| `growth` | obj | | 合成工艺（表单） | 见下 |
| `xrd` | obj | | XRD与物相（表单） | 见下 |
| `eds_rows` | list[obj] | | EDS成分（表格） | 见下 |
| `transport` | obj | | 电学输运（表单） | 见下 |
| `magnetism` | obj | | 磁性（表单） | 见下 |
| `narrative` | obj | | 实验过程与现象（富文本） | `summary` / `process` / `observations` / `conclusion` |
| `attachments` | list[obj] | | 原始数据与图片（文件集） | `{path, text}`，path 为本地绝对或相对路径 |

## 二、子结构

### growth

| key | 类型 | 单位 | 示例 |
|---|---|---|---|
| `method` | str | | `助熔剂法` / `CVT` / `固相烧结` |
| `flux` | str | | `Te 自助熔剂`、`I2 5 mg/cm³` |
| `crucible` | str | | `Al2O3` |
| `atmosphere` | str | | `真空` / `Ar` |
| `vacuum_torr` | str | Torr | `1e-4`（保留原文，文本字段） |
| `t_max` | number | ℃ | `625` |
| `hold_h` | number | h | `14` |
| `cool_rate` | number | ℃/h | `5` |
| `t_source` | number | ℃ | CVT 源区 |
| `t_growth` | number | ℃ | CVT 生长区 |
| `separation` | str | | `离心` |
| `yield_pct` | number | % | `35`（不要写 `35%`） |

### precursors[]

| key | 类型 | 说明 |
|---|---|---|
| `name` | str | `Bi` |
| `purity` | str | `99.999%` |
| `ratio` | number | 摩尔比，可小数 |
| `mass_g` | number / null | 称量质量 |
| `note` | str | `Alfa Aesar` |

### xrd

`phases` / `space_group` / `lattice` / `conclusion`，全部文本。`conclusion` 多行用 `\n`。

### eds_rows[]

`element`（`Bi`）、`line`（`M`）、`wt`（number）、`at`（number）、`note`。

汇总行（Total）不要写进表；脚本会自动附加到富文本结论里。

实测的 EDS docx 一次导出通常含多个谱（Spectrum 274~277，即 4 个测试点）。两种录入方式：

1. 只录代表性谱（如最后一个谱），在 `note` 写「Spectrum 276」；
2. 多谱取平均，在 `narrative.conclusion` 注明「4 点平均」。

同一张 EDS 表里不要混多个谱的行，否则列名语义会乱。

### transport

`property` / `temp_range` / `resistivity` / `carrier_type` / `carrier_conc` / `mobility` / `conclusion`，除 `carrier_type` 走级联外全是文本。

### magnetism

`measurement` / `temp_range` / `moment` / `t_transition`（number, K）/ `conclusion`。

### attachments[]

| key | 类型 | 说明 |
|---|---|---|
| `path` | str | 文件路径；相对路径按项目根与 `attachment_roots` 解析 |
| `text` | str | 文件集的说明文本（会包成 `<p>`） |
| `folder` | str | 选填，文件集里的目标文件夹（仅提示，脚本不建目录） |
| `optional` | bool | 选填，找不到文件时是否允许跳过（默认 false，直接报错） |

## 三、转换规则

1. **空值**：`null`、空字符串、空列表一律不写入 `data`，避免把 `""` 塞进数字字段。
2. **数字**：`"625"`、`"625 ℃"`、`"625℃"` 会尝试转成 `625.0`；`"~625"`、`"未测"` 无法转换时报错并提示改用文本字段。
3. **日期**：`2026/9/24`、`2026.9.24`、`2026年9月24日` → `2026-09-24`。
4. **级联**：值与 `choices` 完全匹配；不匹配时若只差空格/大小写，自动修正并告警；仍不匹配则报错并列出可选值。
5. **表格**：所有列长度必须一致；短列用 `null` 补齐；多余行报错。
6. **富文本**：`narrative` 的四个段落按 `摘要 / 过程与现象 / 表征结论` 生成 `<p>`，`\n` 转 `<br>`。
7. **附件**：`#file{name}`，`name` 由客户端生成 `{uid}_{序号:02d}_{hash6}`（≤45 字符）；不走 base64（`.gk`/`.xy`/`.dat` 不在白名单）。
8. **uid 长度**：>45 字符时截断样品编号并告警。

## 四、模板块缺失时的表现

`field_map` 指向的模块/字段在模板里不存在时，服务端返回 `code: 2` 且 msg 会提到具体位置，例如：

```
应为数字 (dataset: 1, data: 表格模块, 项目: 数字列)
```

或模块名不存在时的格式错误。看到这类报错，先跑 `check_template.py` 定位，再改 `matelab.yaml` 或改模板。
