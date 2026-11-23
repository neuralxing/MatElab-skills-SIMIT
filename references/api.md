# MatElab 数据 API 速查

所有接口：`https://eln.iphy.ac.cn:61262`，Header `Authorization: Bearer {AccessToken}`（认证接口除外）。
返回 `code`：`0` 成功 / `1` token 无效 / `2` 数据格式错误 / `3` 服务器错误 / `'refresh'` 需刷新 token。

## 1. 认证

**POST `/tokens`**（`application/x-www-form-urlencoded`）

| 字段 | 说明 |
|---|---|
| username | 用户名 |
| password | 密码 |

返回 `access.token`（60 分钟）、`refresh.token`（24 小时）、`userid`、`realname`、`email`。

**POST `/tokens/tokens_refresh`**（`application/x-www-form-urlencoded`）

| 字段 | 说明 |
|---|---|
| refresh_token | 刷新 token |

返回新的 `access` 与 `refresh`（旧 refresh 失效）。

## 2. 文件上传

**POST `/eln_api/upload`**（`multipart/form-data`）

| 字段 | 必填 | 说明 |
|---|---|---|
| uid | ✓ | 同一文件各分片共用的唯一标识，≤45 字符 |
| file | ✓ | 分片内容（`filename` 用真实文件名） |
| eln | | 目标记录本，单服务器环境可省 |
| name | | 引用名（**不是文件名**），全局唯一，≤45 字符，空则用 uid |
| last | | 默认 1，末片置 1 |
| offset | | 当前分片在文件中的偏移 |
| hash | | 分片 sha256 |

约束：单文件 ≤4GB；单片 <10MB 建议、≤20MB；**必须串行**，禁止多线程。
返回末片：`{code, id, name, filename, url, size, hash}`；中间片：`{code, tempid, offset, hash}`。
引用语法：`#file{name}`。

## 3. 数据导入

**POST `/eln_api/import`**（JSON）

| 字段 | 必填 | 说明 |
|---|---|---|
| eln | ✓ | 记录本名称 |
| template | ✓ | 模板名称 |
| dataset | ✓ | 1~100 条 |
| user | | 他人记录本所有者 email / userid |
| template_user | | 他人模板所有者 email |
| quote | | `[{name, uid, path}]`，数据里用 `#quote{name}` |

dataset 元素：`uid`（✓，全局唯一）、`title`（✓）、`keyword`（`;` 分隔）、`path`（分类数组）、`data`（✓）。

data 结构：

```json
{
  "表单模块名": { "字段名": "值", "数字字段": 1.5 },
  "富文本模块名": "<p>html</p>",
  "表格模块名": { "列1": ["a", "b"], "列2": [1, 2] },
  "文件集模块名": [{ "file": "#file{name1}", "text": "<p>说明</p>" }]
}
```

字段类型：布尔 `true/false`；数字必须 number；日期 `2000-12-31`；时间 `23:59`；级联值必须在备选内；
文件 `#file{name}` 或 `#base64{filename.ext, b64}`（扩展名限 `pdf|zip|doc|docx|png|gif|tiff|jpg|jpeg|svg`）。

返回：`{code, eln_id, id: [记录id...], msg}`。

限制：一次调用只能一个模板 + 一个记录本；单次 ≤100 条；uid 重复直接报错。

## 4. 数据检索

**POST `/eln_api/search`**（JSON）

| 字段 | 说明 |
|---|---|
| eln | 数组；`"记录本名"` 或 `{"title": "...", "user": "..."}`；空=自己全部 |
| date_start / date_end | 闭区间，`2000-1-1` |
| keyword | 数组 |
| requests | 1~24 条，`{uid, path}`，path ≤4 项 |

path 语义：`[模块名或序号, 行名/列名或序号, 表格行号或文件详情字段, 文件详情字段如 url]`。

返回：`data: [{ id, <记录UID>, <请求uid>: 值 }]`；文件值形如 `{url, filename, size}`。

## 5. 记录列表

**POST `/eln_api/items`**（JSON）→ `{eln, user?}`

返回 `items[]`：`id`、`title`、`sn`、`template_id`、`keywords`、`locked`、`signed`、`version`、`datetime_create/modify/locked`、`config`（文档未说明）。

## 6. 创建空记录

**POST `/eln_api/create`**（JSON）→ `{eln, title, user?, path?, uid?, describe?}`

`describe` 为 HTML。**注意**：只能写一段 HTML，不能带模块/附件，仅作兜底。

## 7. 文档不一致处（实现已兼容）

- 文件集：一处示例为 `["#file{a}"]` 字符串数组，另一处为 `[{file, text}]` 对象数组 → 以对象数组为准，导入失败时脚本自动降级重试一次。
- `创建空记录.py` 用 `errcode` 判断，文档字段是 `code` → 以 `code` 为准。
- 文档称 access token 有效期「60 分钟」，刷新章节又写「30 分钟」→ 以 `expiredAt` 为准，提前 5 分钟刷新。
