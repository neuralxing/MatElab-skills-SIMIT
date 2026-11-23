# 实际模板：Flux method simit（记录本：晶体生长记录）

实验室现有模板，`matelab.yaml` 直接复用它，不再新建。字段名全部为英文，取自网页
「模板 → ⋯ → 查看模板导入格式」的 JSON，**大小写、空格、全角括号都必须完全一致**。

## 一、模板结构

| 模块 | 类型 | 字段 | 说明 |
|---|---|---|---|
| **Experimental scheme** | 表单 | Start date / Finish date（日期） | 起始/结束日期 |
| | | Target product（文本） | 目标产物 |
| | | Method（**下拉多选**） | 生长方法，值须在模板选项内；已验证 `Flux method` 可用 |
| | | Select flux（文本） | 助熔剂/输运剂 |
| | | Sample No.（数字） | 样品号，数字；`XY101` → `101` |
| **Experiment condition** | 表单 | Crucible（文本列表） | 坩埚，如 `Al2O3 5 ml` |
| | | Inner diameter (mm) / Length (mm)（数字） | 坩埚尺寸 |
| | | Atmosphere condition（文本列表） | 气氛 |
| | | Tantalum seal / Quartz seal（布尔） | 钽封/石英封 |
| | | Furnace number（文本） | 炉号 |
| | | Centrifugation / Anneal（布尔） | 离心/退火 |
| | | End temperature（文本列表） | 终止温度 |
| **Start materials** | 表格 | Materials / Molar ratios / Molar mass（g/mol） / Actual mass（g） / Materials Form | **全部文本**；另有重复列 `Actual mass` 未使用 |
| **Heating time** | 表格 | Temperature (℃) / Time (h) / Total time (h) | **全为数字** |
| **Note** | 图文混排 | — | 摘要、过程、现象、电学/磁性结论都放这里 |
| **Experiment result** | 表单 | Product（文本列表） | 产物 |
| | | Photos / XRD measurements / EDS measurements（**文件**） | 三组附件 |
| | | XRD result / EDS result（文本） | 结论文本 |
| | | Target pruduct or not（布尔） | 模板原拼写如此，勿改 |
| **Upload information** | 表单 | Materials Galaxy id / link（文本） | 材料库关联 |

## 二、与「理想模板」的差异及处理

| 差异 | 处理 |
|---|---|
| 没有「记录UID」字段 | `dedupe_by_uid_field: false`、`uid_in_keywords: true`，uid 写进 `dataset.keyword`，查重用关键词检索 |
| 没有 EDS 成分表格 | `eds_rows` 拼成文本写入 `Experiment result.EDS result`，形如 `Bi 33.99 wt% / 20.24 at% (...)` |
| 没有电学/磁性模块 | 自动拼成 HTML 段落追加到 `Note`（`电学输运` / `磁性` 两段） |
| `Sample No.` 是数字 | 从 `sample_id` 提取数字（`XY101` → `101`）；无数字则留空 |
| `Method`、`Crucible`、`Atmosphere condition`、`End temperature` 是文本列表 | 用 `kind: list`，标量自动包成单元素数组 |
| 表格列全是文本 | 数值转字符串；`Actual mass（g）` 存 `0.209` 这样的字符串，不会报类型错 |
| 三个文件字段而非文件集 | 用 `kind: filelist` + `filter`（`photo` / `xrd` / `eds`），附件在 record.json 里打 `kind` |
| `Target pruduct or not` 拼写错误 | 配置里照抄原拼写 |
| `Crucible` / `Atmosphere condition` / `End temperature` / `Materials Form` 是**下拉选择** | 值必须在模板备选里；选项未知时先不映射，原值自动并入 `Note`（`坩埚: ...；气氛: ...；终止温度: ...`） |

## 二·五、模板预填值（会被继承）

| 字段 | 预填值 | 处理 |
|---|---|---|
| Crucible | `Alumina` | 清空（原文写进 Note） |
| Atmosphere condition | `Vacuum` | 清空 |
| End temperature | `Nature cooling` | 清空 |
| Start materials / Materials Form | `Ball` / `Ball` / `Powder` | 清空整列 |
| Experiment result / Product | `Single crystals` | 清空 |
| Upload information / Materials Galaxy id | `SCG00000165` | 清空（别人的编号） |

这些**单字段默认值取不掉**：它们不是记录里的存储值，`/eln_api/update` 的 `del` 会报
`path 1 指定的项不存在`，只能由模板作者在网页端改模板。`clear_inherited` 会识别该报错并跳过，
不会报失败。

另外模板在 `Start materials` 里预填了 3 行示例（Cr / Ge / Te，配比 1:1:10，`Actual mass` 列有值）。
导入是**追加行**，所以新记录会变成 6 行。`clear_rows` 按「`Actual mass` 列非空」定位并倒序删行，
只删示例行，不会碰我们写的 Cr/Ga/Te 行（这列我们留空）。

## 三、探针结果（2026-11-23 实测）

```
[check] 记录本：晶体生长记录  模板：Flux method simit
[check] 映射：27 个字段 / 7 个模块
[check] 导入成功：记录 ID [647813335613440]
[check] 探针记录已删除（['__probe_1123145854__']）
[check] 结论：模板与配置可用。
```

说明：`/eln_api/create` 与 `/eln_api/import` 都接受本模板；删除按 `items` 返回的 `sn`
（等于导入时的自定义 uid）。探针会临时上传一张 1×1 PNG 填满三个文件字段。

## 四、record.json 里需要注意的键

```json
{
  "sample_id": "XY101", "sample_no": 101,
  "start_date": "2026-09-24", "finish_date": "2026-09-30",
  "target": "BiSiTe3 single crystal",
  "growth": {
    "method": "Flux method", "flux": "Te self-flux",
    "crucible": "Al2O3 5 ml", "inner_diameter_mm": 15, "crucible_length_mm": 40,
    "atmosphere": "Vacuum sealed quartz",
    "t_max": 625, "hold_h": 14, "end_temp": 450,
    "separation": "离心", "quartz_seal": true, "tantalum_seal": false,
    "anneal": false, "furnace_no": "Tube furnace #2"
  },
  "heating": [{"temperature": 625, "time": 14, "total": 22}],
  "precursors": [{"name": "Bi", "purity": "99.999%", "ratio": 1,
                  "molar_mass": 208.98, "mass_g": 0.209, "form": "shot"}],
  "attachments": [{"path": "...", "text": "EDS 报告", "kind": "eds"}]
}
```

`kind` 取值：`photo` → Photos；`xrd` → XRD measurements；`eds` / `other` → EDS measurements。
