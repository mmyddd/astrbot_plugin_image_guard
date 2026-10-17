# 🛡️ 图片内容审查卫士 (Image Guard Pro)

> **版本**: v1.13.0
> **作者**: mmyddd

AstrBot 视觉审核插件。它用多模态大模型（Gemini、GPT-4o 等）对群聊/私聊图片做语义审查，不依赖关键词匹配。

---

## ✨ 核心功能

- **双重检测**：OCR 识别图片中的敏感文字，语义模型识别违禁画面。
- **智能过滤**：GIF 保留动画并压缩；QQ 自带表情包、收藏贴图自动放行；静态图片与 GIF 压缩后送审。
- **静默执法**：群聊命中后按配置撤回消息并禁言，不发送提示；私聊给管理员发战报，附证据、执行状态和判定理由。
- **标签与审计**：违规图片由模型生成内容标签；记录页支持标签、关键词和处置状态检索。SAFE 图片不写历史、不存原图；启用缓存时只保留内容指纹与审核次数。

---

## ⚙️ 配置指南

配置中心（插件页）与 `_conf_schema.json` 共用同一份配置。

### 内容识图要写具体

`forbidden_descriptions` 描述的是画面，不是主题。写得太宽，建筑照、风景照也可能被判违规。

> 配置 `东亚` 时，一张赫鲁晓夫楼照片可能被模型按“东亚建筑”误判；写成 `东亚地图` 后，只有地图会命中。

建议配合较强的多模态模型使用，并把描述写成具体对象、构图或行为，例如“裸露的人体”“大面积血迹”“赌博平台界面”。

### 配置项

| 配置项 | 说明 | 推荐值 |
| :--- | :--- | :--- |
| **llm_providers** | 供应商列表，按顺序尝试，第一个成功结果即本次结论。支持 OpenAI 兼容 / ModelScope / AstrBot 当前 Provider；页面可增删改排序、测试连通性。API Key 以掩码展示，回传掩码表示不修改。 | 配置中心 → LLM 供应商 |
| **reasoning_effort** | 透传给模型的推理强度，留空不发送。 | `medium` 或留空 |
| **custom_vision_prompt** | 自定义识图引导词，会拼接到系统提示词。 | "请仔细分辨地图轮廓，非地图类图片一律放行。" |
| **sensitive_texts** | OCR 敏感文字列表。 | `["加微信", "退款", "代充"]` |
| **forbidden_descriptions** | 语义违禁画面描述列表。 | `["东亚地图", "裸露画面"]` |
| **llm_max_tokens** | 单次审核最大输出 token。本地 reasoning 模型建议调大，避免结论被截断。 | `512` |
| **llm_timeout_seconds** | 接口超时（秒）。本地视觉模型首次处理可能较慢。 | `120.0` |
| **compressed_image_max_bytes** | 送审 data URL 的压缩上限；GIF 会保留动画并减少颜色、尺寸或帧数。 | `1048576` |
| **group_scope** / **private_scope** | 启用审查的群号 / 私聊用户。默认不启用，需显式添加；填 `0` 表示全部。 | `[]` / `[]` |
| **audit_whitelist** | 审核白名单。名单内用户（按发送者 QQ）的图片不送审、不记录，群聊与私聊均生效。 | `["1000100", "1000101"]` |
| **skip_audit_for_banned** | 是否跳过被 [AstrBot-ban-Plugins](https://github.com/dahetaoa/AstrBot-ban-Plugins) 禁用的用户。默认关闭，被禁用户仍会被静默审核。 | `false` |
| **check_probability** | 抽查概率（0.0–1.0），1.0 为每图必查。 | `1.0` |
| **enable_recall** | 是否撤回群聊中的违规图片消息，需要 Bot 具备管理员权限。 | `true` |
| **ban_duration** | 违规禁言时长（秒），0 表示不禁言。 | `86400` |
| **report_target_id** | 违规战报默认接收人（管理员 QQ），留空不转发。 | 你的 QQ 号 |
| **group_report_targets** | 群上报映射：为指定来源群单独指定接收目标，支持 private / group；未命中时回退到 `report_target_id`。 | `908799265 → private:123456789` |
| **audit_history_max_records** | 审核历史最大保留条数，超出删除最早记录，0 表示不保留。 | `500` |
| **audit_cache_enabled** | 启用审核缓存：同一图片累计达到阈值后直接跳过。 | `true` |
| **audit_cache_threshold** | 缓存跳过阈值（次）。1 表示只审一次，0 会被调整为 1。 | `3` |
| **audit_cache_max_entries** | 缓存条目上限，超出淘汰计数最低项，0 表示不限制（不推荐）。 | `10000` |
| **keep_compressed_image_in_temp** | 调试：把压缩图保存到 `data/temp/astrbot_plugin_image_guard` 并在日志输出路径。 | `false` |
| **debug_log_llm_response** | 调试：把模型原始响应写入日志，可能包含判定理由等敏感信息。 | `false` |
| **dataset_capture_enabled** | 留存prompt：把送审图片 base64、完整提示词、模型返回与标签落盘，供微调使用。关闭时不创建目录和文件。 | `false` |
| **dataset_dir** | 数据集目录，留空使用 `data/plugin_data/image_guard/dataset`。 | 留空 |
| **dataset_max_samples** | 样本条数上限，达到后停止留存，不淘汰旧样本。 | `2000` |
| **dataset_max_image_mb** | 单图留存上限（MB），超出只记元数据，不存 base64。 | `8` |
| **dataset_min_free_mb** | 磁盘余量下限（MB），低于该值暂停留存。 | `512` |
| **dataset_capture_raw_response** | 是否额外保存接口原始响应 JSON（含 token 用量）。 | `false` |

### 门控降本（SystemOne，可选）

在视觉大模型之前插入一道低成本判定：先问一次“是否命中违规清单”，拿到违规概率；低于阈值直接放行，不调用视觉大模型。

门控结论不会传给大模型，也不会写入审核历史。门控失败、超时或配置不完整时一律按放行处理，继续走视觉大模型，不会因门控故障漏审。

| 配置项 | 说明 | 推荐值 |
| :--- | :--- | :--- |
| **systemone_enabled** | 门控总开关。关闭时行为与未接入门控一致，不产生额外请求。 | `false` |
| **systemone_base_url** | 门控网关地址，留空用默认 OpenRouter 地址。 | `https://openrouter.ai/api` |
| **systemone_endpoint_path** | 端点路径，留空按网关自动判断（`/api` → `/alpha/decisions`，`/v1` → `/decisions`）。 | 留空 |
| **systemone_api_key** | 门控 API Key，与视觉大模型独立计费；页面显示掩码，回传掩码表示不修改。 | `sk-or-v1-...` |
| **systemone_model** | 门控模型名，需支持图文联合判定。 | `cloudflare/clef-flash` |
| **systemone_violation_threshold** | 违规概率阈值：低于直接放行，高于或等于送视觉大模型。调高更省钱但更易漏放。 | `0.3` |
| **systemone_timeout_seconds** | 门控超时（秒），超时按放行处理。 | `15.0` |
| **systemone_max_image_bytes** | 门控单图字节上限。原始图在范围内直接送审不压缩，超过才压缩。 | `300000` |
| **systemone_question** | 判定问题，留空用内置问句。门控只输出概率，不生成文字。 | 内置问句 |
| **systemone_sensitive_texts** | 门控专用敏感文字，留空继承 `sensitive_texts`。 | `[]` |
| **systemone_forbidden_descriptions** | 门控专用画面描述，留空继承 `forbidden_descriptions`。 | `[]` |
| **systemone_vision_prompt** | 门控专用识图引导词，留空继承 `custom_vision_prompt`。 | `""` |

覆盖项采用“留空继承”，这样刚打开门控、还没配覆盖项时，门控判据与视觉大模型一致，不会出现门控按更宽规则放行。

阈值怎么定：安全图的概率通常集中在 0.01–0.15，违规图集中在 0.80–0.98，中间是空白区。建议先用默认 0.3 跑一周，对照门控放行数与实际违规数，再决定是否下调。

---

## 🧪 留存prompt：把审核过程变成微调数据集

在配置中心的「数据集留存」页，有一个“留存prompt”开关，默认关闭。开启后会保存四类内容：

1. 实际送审的图片（data URL、base64 与解码后的图片文件）；
2. 完整提示词（含引导词与违规标准）；
3. 模型返回原文（REASON / RESULT）；
4. 模型标签（TAGS 解析结果，作为 `label` 与 `tags` 标注）。

### 存储结构

默认目录 `data/plugin_data/image_guard/dataset`（可用 `dataset_dir` 修改）：

```
dataset/
├── samples.jsonl          请求侧：prompt、图片 base64
├── responses.jsonl        响应侧：模型返回、标签与判定
├── images/<样本id>/       解码后的图片，训练框架可直接读取
└── export/<时间戳>/       dataset.jsonl / labels.jsonl / manifest.json
```

请求侧在调用模型前写入，响应侧在模型返回后写入，两者用同一条 `id` 关联。进程中途崩溃时，请求侧已落盘，不会整条丢失。

### 边界情况

| 情况 | 处理 |
| :--- | :--- |
| 开关关闭 | 不建目录、不写文件、不读盘 |
| 图片为空、非 base64 或 base64 非法 | 该图记 `missing_reason`，不阻断整条样本 |
| 单图超过 `dataset_max_image_mb` | 只记字节数与摘要，不存 base64 和图片文件 |
| 模型返回为空或调用失败 | 请求侧照常落盘，响应侧记录状态；不纳入数据集、不导出 |
| 达到 `dataset_max_samples` | 停止留存新样本，提示先导出再清理 |
| 磁盘余量低于 `dataset_min_free_mb` | 自动暂停留存并写日志告警 |

### 导出

配置中心 → 数据集留存 → 导出数据集，产物写入 `<dataset_dir>/export/<时间戳>/`：

| 文件 | 用途 |
| :--- | :--- |
| `dataset.jsonl` | 训练文件，一行一条多模态 `messages` 样本（system / user / assistant） |
| `images/` | 从留存目录复制的图片，导出产物可整体搬走 |
| `dataset_info.json` | LLaMA-Factory 注册文件 |
| `labels.jsonl` | 与训练文件逐行对齐的标注侧车，仅用于筛选正负样本，训练时忽略 |
| `manifest.json` | 本次导出的条数、跳过条数与正负样本分布 |

`dataset.jsonl` 每行形如：

```json
{"messages":[{"role":"system","content":"…审核规则…"},{"role":"user","content":"<image>请按要求分析这张图片。"},{"role":"assistant","content":"REASON: …\nRESULT: VIOLATION\nTAGS: [\"…\"]"}],"images":["images/…_1.jpg"]}
```

导出采用 `messages` 轮次 + 独立 `images` 路径列。OpenAI 的 `content[].image_url` 是接口请求格式，LLaMA-Factory、ms-swift 等训练框架不识别，因此不直接沿用。

记忆点：**规则在 system，指令在 user，判定在 assistant**。换一套违规标准只改 system，就能复用同一个模型。

训练命令（LLaMA-Factory，把导出目录同时作为 `dataset_dir` 和 `media_dir`）：

```bash
llamafactory-cli train examples/train_lora/qwen2vl_lora_sft.yaml \
  dataset_dir <导出目录> \
  dataset image_guard \
  media_dir <导出目录>
```

unsloth 需要把 user 内容拆成 text / image 两个 block，官方 vision 微调文档有对应示例。

只有调用成功、返回非空且带图片的样本会进入数据集。失败、空返回、在途记录和无图片样本留在磁盘供排查，但不纳入数据集。

### 单条删除与隐私

样本预览中每条记录都可单独删除，删除会同时清理请求侧、响应侧与图片文件，不影响已导出的产物。删除后条数上限重新计算。

留存数据包含用户图片与提示词，属于敏感内容。目录会长期占用磁盘，请确保存储安全，定期导出后清理，并遵守当地关于数据留存与个人信息的法律要求。

---

## 🖥️ 审核控制台（插件页）

页面位于 `pages/audit-history`，通过 AstrBot WebUI 打开，无需进入插件配置页。

- **概览**：Bento 布局。违规趋势与判定安全率为主卡，实时队列、处置成功率、节省调用为辅卡，最近违规与当前策略为长卡。趋势可切 7 / 14 / 30 天。命中 VIOLATION 才写记录；SAFE 直接丢弃，不产生历史条目。
- **记录**：左侧列表 + 右侧详情。列表支持「最近违规 / 审核中 / 处置失败」、关键词搜索、标签筛选、状态筛选、排序、分页与 CSV 导出；右侧展示原图、判定理由、处置结果与处理轨迹，并标注本次审核耗时（CSV 含「审核用时(毫秒)」列）。
- **配置**：左侧 7 个分组（审查规则 / LLM 供应商 / 门控降本 / 范围与处置 / 图片与调试 / 缓存与存储 / 数据留存），右侧单组表单。群号、私聊用户、白名单、敏感文字与画面描述统一用 chip + 添加按钮；右上角可进入 3 步配置向导。改动先留在页面，确认后写入配置并热重载；未保存时离开会先确认。
- **数据集**：开关控制留存；面板显示样本数、占用空间与磁盘余量，支持导出、单条删除和整体清空（不影响已导出产物）。
- **维护/主题**：可清空缓存、清理孤立图片、清空历史、删除单条记录；主题跟随 AstrBot，也可在顶栏切换。

页面通过 `window.AstrBotPluginPage` bridge 调用插件 API：

| 方法 | 路由 | 说明 |
| :--- | :--- | :--- |
| GET | `audit/list` | 记录、供应商统计、缓存统计、配置摘要、运行统计与处理中任务 |
| GET | `audit/pending` | 正在审核的任务 |
| POST | `audit/delete` | 删除单条记录 |
| POST | `audit/clear` | 清空审核历史 |
| GET | `audit/config` | 读取插件配置（Key 已掩码） |
| POST | `audit/config/update` | 合并配置并热重载 |
| POST | `audit/providers/save` | 保存供应商列表并热重载 |
| POST | `audit/providers/test` | 供应商连通性测试 |
| POST | `audit/systemone/test` | 门控连通性测试 |
| POST | `audit/cache/clear` | 清空审核缓存 |
| POST | `audit/storage/prune` | 清理孤立图片 |
| GET | `audit/dataset` | 数据集状态与样本预览 |
| POST | `audit/dataset/export` | 导出数据集 |
| POST | `audit/dataset/delete` | 删除单条留存记录 |
| POST | `audit/dataset/clear` | 清空留存样本与图片 |

---

## 🔗 与黑名单插件兼容

同时安装 [AstrBot-ban-Plugins](https://github.com/dahetaoa/AstrBot-ban-Plugins) 时要注意：它通过 `event.stop_event()` 中断事件，AstrBot 会跳过后续插件。因此本插件把处理器优先级设为 `priority=100`，先收到消息，且从不调用 `stop_event()`。

`skip_audit_for_banned` 默认关闭：被黑名单禁用的用户发图仍会被静默审核。审核不回复用户、不产生消息，不违背“禁用 bot”的本意，也能避免被禁用户借机发违规图片。开启后，本插件会读取黑名单插件的 `sp` 存储，命中全局/群禁用（含群内例外）时跳过审核。

---

## 🛠️ 依赖与安装

1. **AstrBot**：建议使用最新版。
2. **协议端**：NapCat 或 Lagrange（OneBot 11）。Bot 需要管理员权限，否则无法撤回和禁言。
3. **Python 依赖**：`Pillow`，用于压缩超过 1 MB 的图片。
4. **安装**：把插件目录放入 `data/plugins/`，重启 AstrBot。

## ❓ 常见问题

**Q: Bot 没反应？**
A: 先检查 `group_scope` 是否添加了目标群号（或填 `0` 全开，默认不启用）；再看日志里 GIF 是否被供应商拒绝、LLM 是否报错。

**Q: 撤回/禁言失败？**
A: 检查 Bot 是否为群管理员、NapCat 是否正常连接；失败时 Bot 会私聊管理员发战报，状态栏会显示 `❌`。

**Q: 识别不准？**
A: 换用更强的多模态模型，并把 `custom_vision_prompt` 和 `forbidden_descriptions` 写得更具体。宽泛的地域、主题类描述容易误判。
