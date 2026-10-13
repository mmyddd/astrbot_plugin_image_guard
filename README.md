# 🛡️ 图片内容审查卫士 (Image Guard Pro)

> **版本**: v1.12.0
> **作者**: YEZI

这是一个专为 AstrBot 设计的高级视觉审核插件。它利用多模态大模型（如 Gemini 1.5/2.5 Pro, GPT-4o）的视觉理解能力，对群聊/私聊发送的图片进行**语义级审查**。

与传统的关键词匹配不同，它能“看懂”图片里的内容。

---

## ✨ 核心功能

### 1. 双重检测机制
* **🅰️ 文字识图 (OCR)**：图片里只要出现了你指定的敏感词（如“加群”、“退款”），立即封禁。
* **🅱️ 内容识图 (Semantic)**：图片画面包含你指定的违禁事物（如“东亚地图”、“血腥画面”），立即封禁。

### 2. 智能过滤 (Smart Filter) v1.6.4 New!
* ✅ **GIF 动图压缩**：GIF 会保留动画并压缩为 `image/gif` data URL；超限时依次减少颜色、尺寸和帧数。
* ✅ **自动放行**：QQ 自带表情包、收藏的贴图。Bot 不会浪费 Token 去检测这些内容。
* ❌ **精准拦截**：JPG/PNG 等静态图片与 GIF 动图都会按配置压缩后审查。

### 3. 静默执法 (Silent Assassin)
* **群内**：检测到违规瞬间**撤回消息 + 禁言用户**。Bot 在群内保持绝对沉默，不发送任何提示，极具威慑力。
* **私聊**：Bot 会私聊管理员发送**执法战报**，包含违规图片证据、撤回/禁言的执行状态以及 **LLM 给出的判罚理由**。

### 4. 违规标签与审计检索
* **自动标签**：判定为 `VIOLATION` 的图片会由视觉模型生成简短内容标签，例如“女性裸露”“白丝”“肌肤裸露”。
* **WebUI 筛选**：审核记录页在理由下方显示标签，并提供左侧标签统计筛选和关键词搜索。
* **隐私边界**：`SAFE` 图片不会写入审核历史或保存原图；启用审核缓存时只保留内容指纹和审核次数。

---

## ⚙️ 配置指南 (避坑必读)

### ⚠️ 特别警告：关于“内容识图”的描述技巧

在配置 `forbidden_descriptions`（违禁画面描述）时，请务必**精确描述**，不要使用宽泛的词汇！

> **❌ 错误示范**：
> 配置了违禁词：`"东亚"`
> 
> **💥 灾难后果**：
> 用户发了一张 **“赫鲁晓夫楼”**（老式居民楼）的照片。
> LLM 判定逻辑：赫鲁晓夫楼 -> 常见于中国北方 -> 属于东亚地区事物 -> **判定违规，禁言！**
> *(这就是典型的因描述不准确导致的冤假错案)*

> **✅ 正确示范**：
> 配置违禁词：`"东亚地图"`
> 
> **🎉 正确结果**：
> 只有当图片真的是一张地图时，才会触发封禁。发风景照、建筑照均安全。

**💡 专家建议**：配合 **Gemini-2.5-Pro** 等高智商模型，并使用精确的描述词，本插件的准确率极高，几乎不会误封。

---

### 📝 详细配置项说明

配置中心（插件页）与 `_conf_schema.json` 共用同一份配置，两处都可以改。

| 配置项 | 说明 | 推荐值 |
| :--- | :--- | :--- |
| **llm_providers** | **(核心)** 供应商列表，按顺序依次尝试，第一个返回结果的即本次审核结论。模板：**OpenAI 兼容** / **ModelScope** / **AstrBot 当前 Provider**。配置中心支持新增、编辑、排序、删除与**一键连通性测试**；页面读到的 API Key 是掩码，回传掩码即表示不修改。 | 配置中心 → LLM 供应商 |
| **reasoning_effort** | 透传给模型的推理强度，留空则不发送该参数。 | `medium` 或留空 |
| **custom_vision_prompt** | **自定义引导词**：教模型重点看哪些细节，会拼接到系统提示词。 | "请仔细分辨地图轮廓，非地图类图片一律放行。" |
| **sensitive_texts** | **文字识图（OCR）**：图片里出现这些文字即违规。 | `["加微信", "退款", "代充"]` |
| **forbidden_descriptions** | **内容识图（语义）**：画面符合这些描述即违规。 | `["东亚地图", "裸露画面"]` |
| **llm_max_tokens** | 单次审核输出的最大 token 数，本地 reasoning 模型容易先输出思考过程，建议调大避免结论被截断。 | `512` |
| **llm_timeout_seconds** | 接口请求超时（秒）。本地视觉模型首次处理图片较慢，建议 120 秒或更高。 | `120.0` |
| **compressed_image_max_bytes** | 送审前把图片 data URL 压缩到该字节数以内；GIF 会保留动画并按需减少颜色、尺寸或帧数。 | `1048576` |
| **group_scope** / **private_scope** | 启用审查的群号 / 私聊用户，填 `0` 表示全部。 | `["0"]` / `[]` |
| **audit_whitelist** | **审核白名单**：名单内用户（按发送者 QQ 判断）的图片不送审、不记录，群聊与私聊均生效。留空表示不使用。 | `["1000100", "1000101"]` |
| **skip_audit_for_banned** | **黑名单插件兼容**：是否跳过被 [AstrBot-ban-Plugins](https://github.com/dahetaoa/AstrBot-ban-Plugins) 禁用的用户。默认 `false`——被禁用户仍会被审核。 | `false` |
| **check_probability** | 抽查概率（0.0-1.0），1.0 为每图必查。 | `1.0` |
| **enable_recall** | 是否撤回群聊中的违规图片消息（需要 Bot 具备管理员权限）。 | `true` |
| **ban_duration** | 违规禁言时长（秒），`0` 表示不禁言。 | `86400` |
| **report_target_id** | 违规战报默认接收人（管理员 QQ 号），留空则不转发。 | 你的 QQ 号 |
| **group_report_targets** | **群上报映射**：为指定来源群单独指定战报接收目标，支持 `private` 私聊与 `group` 群聊；未命中时回退到 `report_target_id`。配置中心可在表格里直接增删。 | `908799265 → private:123456789` |
| **audit_history_max_records** | 审核历史最大保留条数，超出后删除最早记录，`0` 表示不保留。 | `500` |
| **audit_cache_enabled** | 启用审核缓存：同一张图片累计审核达到阈值后直接跳过。 | `true` |
| **audit_cache_threshold** | 跳过阈值（次）。`1` 表示同一图片只审一次，`0` 会被自动调整为 `1`。 | `3` |
| **audit_cache_max_entries** | 缓存条目上限，超出后淘汰计数最低的条目，`0` 表示不限制（不推荐）。 | `10000` |
| **keep_compressed_image_in_temp** | 调试开关：把压缩后的图片保存到 `data/temp/astrbot_plugin_image_guard` 并在日志中输出路径。 | `false` |
| **debug_log_llm_response** | 调试开关：把模型原始响应写入日志，可能包含判定理由或图片相关敏感信息。 | `false` |
| **dataset_capture_enabled** | **留存prompt**：把每次送审的图片 base64、完整提示词与模型返回落盘，模型给出的标签直接作为标注，供本地模型微调。**关闭时不创建任何目录与文件**。 | `false` |
| **dataset_dir** | 数据集存储目录，留空使用默认目录 `data/plugin_data/image_guard/dataset`。 | 留空 |
| **dataset_max_samples** | 样本条数上限，达到后停止留存（不淘汰旧样本，避免静默丢数据）。 | `2000` |
| **dataset_max_image_mb** | 单图留存上限（MB），超出只记元数据不存 base64。 | `8` |
| **dataset_min_free_mb** | 磁盘余量下限（MB），低于该值自动暂停留存。 | `512` |
| **dataset_capture_raw_response** | 是否额外保存接口原始响应 JSON（含 token 用量）。 | `false` |

### 🚦 门控降本（SystemOne，可选）

在视觉大模型之前插入一道低成本判定：每张图先问一次「是否命中违规清单」，拿到违规概率，低于阈值就直接放行、不调用视觉大模型。

**门控结论不会传给大模型**，也不会写进审核历史——大模型看到的规则、问题与门控关闭时完全一致，避免先入为主。门控调用失败、超时或配置不全时一律按放行处理（继续走视觉大模型），不会因为它故障而漏审。

| 配置项 | 说明 | 推荐值 |
| :--- | :--- | :--- |
| **systemone_enabled** | 门控总开关。关闭时插件行为与未接入门控时完全一致，不产生任何额外请求。 | `false` |
| **systemone_base_url** | 门控网关地址，留空使用默认 OpenRouter 地址。 | `https://openrouter.ai/api` |
| **systemone_endpoint_path** | 端点路径，留空则按网关地址自动判断（`/api` 结尾用 `/alpha/decisions`，`/v1` 结尾用 `/decisions`）。 | 留空 |
| **systemone_api_key** | 门控 API Key，独立于 `llm_providers`，可与视觉大模型分开计费。页面显示为掩码，回传掩码表示不修改。 | `sk-or-v1-...` |
| **systemone_model** | 门控模型名，需支持图文联合判定。 | `cloudflare/clef-flash` |
| **systemone_violation_threshold** | **违规概率阈值**：低于此值直接放行，高于或等于此值才送视觉大模型。调高更省钱但更易漏放。 | `0.3` |
| **systemone_timeout_seconds** | 门控单次请求超时（秒）。超时按放行处理，不会阻塞审核。 | `15.0` |
| **systemone_max_image_bytes** | 门控单图字节上限。**原始图**在此范围内直接送审不压缩，超过才走压缩链路。压缩会抹掉小字水印与边角贴纸，所以默认给得比较宽松。 | `300000` |
| **systemone_question** | 判定问题，留空使用内置问句。门控只输出概率、不生成任何文字。 | 内置问句 |
| **systemone_sensitive_texts** | 门控专用敏感文字，**留空即继承** `sensitive_texts`；填写后仅门控生效。 | `[]` |
| **systemone_forbidden_descriptions** | 门控专用违规画面描述，**留空即继承** `forbidden_descriptions`。 | `[]` |
| **systemone_vision_prompt** | 门控专用识图引导词，**留空即继承** `custom_vision_prompt`。 | `""` |

> 覆盖项做成「留空继承」而不是「留空清空」：开关刚打开、还没配覆盖项时，门控判据与视觉大模型完全一致，不会出现「门控按更宽的规则放行」这种漏放。只有两边判据一致，门控省下的钱才是真省下来的。

**怎么定阈值？** 安全图的概率通常聚集在 0.01~0.15，违规图聚集在 0.80~0.98，中间是空白区。阈值落在空白里既省钱又不漏放；落在空白右侧（>0.7）会开始漏掉明显违规的图。建议先用默认值 `0.3` 跑一周，看页面「审核漏斗」里的门控放行数与实际违规数是否对得上，再往下调。

---

## 🧪 留存prompt：把审核过程变成微调数据集

插件在**配置中心 → 数据集留存**提供一个「留存prompt」开关（默认**关闭**）。开启后，每次送审都会留下三样东西，外加模型自己打的标签：

1. **发送给模型的图片**（压缩后实际送审的 data URL 与 base64 正文，解码后的图片文件同时落盘）；
2. **发送给模型的提示词**（含自定义引导词与违规标准的完整 prompt）；
3. **模型返回的内容**（原文、REASON、RESULT）；
4. **模型打的标签**（`TAGS` 行解析结果，作为数据集的 `label` 与 `tags` 标注）。

### 存储位置与格式

默认目录 `data/plugin_data/image_guard/dataset`（可用 `dataset_dir` 改）：

```
dataset/
├── samples.jsonl          请求侧：一行一条，含 prompt 与图片 base64
├── responses.jsonl        响应侧：一行一条，含模型返回、标签与判定
├── images/<样本id>/img_1.jpg   解码后的图片，训练框架可直接按路径读取
└── export/<时间戳>/            dataset.jsonl / labels.jsonl / manifest.json
```

拆分两个文件是为了对齐记录时机：**调用模型前**写 `samples.jsonl`，**模型返回后**写 `responses.jsonl`，两者用同一条 `id` 关联。进程在中途崩溃时请求侧已经落盘，不会整条丢失。

`samples.jsonl` 单条记录：

```json
{
  "id": "20261013-224912-a1b2c3",
  "schema_version": 1,
  "created_at": "2026-10-13 22:49:12",
  "source": {"group_id": "888", "user_id": "10001", "user_name": "张三", "is_group": true},
  "request": {
    "prompt": "你是一个严格但公正的内容审核员……",
    "provider": "", "model": "",
    "image_count": 1,
    "images": [{"index": 1, "mime": "image/jpeg", "base64": "/9j/4AAQ…", "bytes": 288,
                "sha256": "…", "path": "images/20261013-224912-a1b2c3/img_1.jpg", "missing_reason": ""}]
  }
}
```

`responses.jsonl` 单条记录：

```json
{
  "id": "20261013-224912-a1b2c3",
  "status": "ok",
  "text": "REASON: 裸露画面\nRESULT: VIOLATION\nTAGS: [\"裸露\",\"白丝\"]",
  "tags": ["裸露", "白丝"],
  "label": "violation",
  "reason": "裸露画面",
  "error": "",
  "elapsed_ms": 1234,
  "provider": "Gemini 主线路",
  "model": "gemini-2.5-pro",
  "recorded_at": "2026-10-13 22:49:14",
  "raw": null
}
```

### 边界情况

| 情况 | 处理 |
| :--- | :--- |
| 开关关闭 | 不建目录、不写文件、不读盘，插件行为与没有该功能时完全一致 |
| 图片为空 / 非 base64（HTTP URL、本地路径、空值） | 该张图记 `missing_reason`，**不阻断整条样本**——prompt 与模型返回仍然留存 |
| base64 非法或解不出内容 | 同上，记 `missing_reason`，不写图片文件 |
| 单图超过 `dataset_max_image_mb` | 只记字节数与摘要，不存 base64 与图片文件 |
| 模型返回为空 | 记 `status: ok` + 空 `text`，保留 prompt 与图片便于排查；**不计入数据集、不导出、不上页面** |
| 模型调用失败 / 超时 | 请求侧照常落盘，响应侧记 `status: failed` 与错误原因；同样**不纳入数据集** |
| 达到 `dataset_max_samples` | 停止留存新样本（不淘汰旧样本），页面顶部提示先导出再清理 |
| 磁盘余量低于 `dataset_min_free_mb` | 自动暂停留存并写日志告警，避免写满磁盘拖垮 AstrBot |

### 导出

配置中心 → 数据集留存 → **导出数据集**，产物写入 `<dataset_dir>/export/<时间戳>/`，只有一种格式：

| 文件 | 用途 |
| :--- | :--- |
| `dataset.jsonl` | 训练文件。一行一条样本，多模态 sharegpt 结构 |
| `images/` | 样本图片。**导出时从留存目录复制过来**，导出产物可以整体搬走 |
| `dataset_info.json` | LLaMA-Factory 注册文件，说明各列怎么解析 |
| `labels.jsonl` | 标注侧车。与训练文件逐行对齐，按 `id` 关联，仅供筛选正负样本 |
| `manifest.json` | 本次导出的条数、跳过的条数与正负样本分布 |

`dataset.jsonl` 一行一条（下例为便于阅读做了换行与缩进，实际一行一条）：

```json
{"conversations":[{"from":"human","value":"<image>请按要求分析这张图片。"},{"from":"gpt","value":"REASON: 主体掀裙露大腿，衣冠不整\nRESULT: VIOLATION\nTAGS: [\"大腿裸露\", \"掀裙\", \"衣冠不整\"]"}],"system":"你是一个严格但公正的内容审核员。请分析图片是否包含违规信息。\n【自定义关注点】\n……\n标签要求：仅在 VIOLATION 时提供，最多8个具体、简短、互不重复的画面标签。\n","images":["images/20261013-235253-629fd0_1.jpg"]}
```

记忆点：**规则在 system，指令在 human，判定在 gpt**。规则与「看哪张图」解耦后，模型学到的是"按 system 里的规则判图"，换一套违规标准只改 system 即可复用同一个模型。

### 为什么不用 OpenAI 的 messages 结构

OpenAI 的 `messages` + `content[].image_url` 是**接口请求体**格式，不是训练格式：

| 框架 | 图片怎么给 | 认 image_url 吗 |
| :--- | :--- | :--- |
| LLaMA-Factory | 独立的 `images` 列，值是**文件路径**，按 `media_dir` 拼路径后用 `os.path.isfile` 校验 | ❌ `mm_plugin.py` 里没有 `image_url` 这个字面量，content 里的图片不会被解析 |
| ms-swift | 标准键 `messages` / `images`，图片列放**路径或 URL** | ❌ 只认自己的 content block 规范 |
| unsloth | `messages` 内容分块 `{"type":"image","image":<路径或PIL对象>}` | ❌ 字段名是 `image` 不是 `image_url`，需要几行 map 转换 |

所以导出改用 **sharegpt + `images` 路径列**——这是 LLaMA-Factory 与 ms-swift 都原生支持的通用结构（LLaMA-Factory 声明 `formatting` 取值就是 `{alpaca, sharegpt, openai}`，其中多模态只认 `images` 列）。

**训练命令（LLaMA-Factory）**：把导出目录当作 `dataset_dir`，图片和 `dataset_info.json` 已在里面：

```bash
llamafactory-cli train examples/train_lora/qwen2vl_lora_sft.yaml \
  dataset_dir <导出目录> \
  dataset image_guard \
  media_dir <导出目录>
```

> unsloth 需要一步转换：把 `conversations` 映射成 `messages`、`{"from":"human","value":"<image>…"}` 拆成 `{"type":"text"}` 与 `{"type":"image","image":<路径>}` 两个分块。官方 vision 微调文档里有对应的 `convert_to_conversation` 示例，十几行代码。选 sharegpt 是因为它被两个主流框架原生支持，覆盖面更广。

> 插件运行时是把规则和图片塞在同一条 user 消息里发给供应商的；导出时把规则拆到 system，是为了让微调后的模型行为更稳。两者对模型语义等价，替换现有供应商不需要改插件提示词。

**只有调用成功、返回非空且带图片的样本会进入数据集。** 失败、返回为空、在途记录、以及没有图片内容的样本都留在磁盘上供排查，但不进数据集——前三种没有监督目标，最后一种会教模型不看图作答。页面的「数据集样本」数与导出口径完全一致，另有独立提示说明被排除了多少条。

> `labels.jsonl` 只是为了让你筛选样本时不必解析训练文件。**训练时忽略它**。

> ⚠️ 留存的数据包含用户图片与提示词，属于敏感内容：目录会长期占用磁盘，请自行确保存储安全，并定期导出后清理。数据集只用于本地模型微调，请遵守你所在地区关于数据留存与个人信息的法律要求。

---

## 🖥️ 审核控制台（插件页）

插件在 AstrBot WebUI 中提供一个独立的页面（`pages/audit-history`），无需进入插件配置页即可完成日常运维：

* **处理中**：正在审核的图片会以顶部的"处理中"横条实时展示（谁发的、来自哪个群、几张、已用时、待审缩略图），每 1.5-4 秒轮询刷新，可收起。**命中 VIOLATION 才写入审核记录，判定 SAFE 的直接丢弃**，不产生历史条目。
* **审核概览**：KPI 带（违规总数 / 处置成功率 / 缓存跳过 / LLM 调用 / 已审核图片 / 审核用时 / 判定安全并丢弃）、14 天违规趋势图（可切 7 / 14 / 30 天）、供应商命中占比（面板高度固定，超出滚动查看，占比按全部供应商计算）、运行配置快照。
* **审核记录**：卡片流展示证据缩略图、判定理由、内容标签与处置状态；支持关键词搜索、标签筛选、处置状态筛选、时间排序、分页与 CSV 导出。
* **审核用时**：概览显示平均单张耗时与峰值（按送审样本统计），每条记录卡片与详情抽屉标注该次审核的实际耗时，CSV 导出含「审核用时(毫秒)」列。
* **审核白名单**：配置中心的「范围与处置」可维护白名单用户；名单内用户发送的图片**既不审核也不记录**，群聊与私聊均生效，概览顶栏会显示白名单人数。
* **记录详情**：点击卡片打开侧边抽屉，查看原图大图、完整理由、标签与用户 / 来源 / 禁言时长等信息。
* **配置中心**：七个分组标签页（审查规则 / LLM 供应商 / 门控降本 / 范围与处置 / 图片与调试 / 缓存与存储 / 数据集留存）。规则以标签形式增删；供应商支持增删改排序与连通性测试；改动先停留在页面，确认后写入配置并热重载插件；未保存时切换页面会二次确认。
* **数据集留存**：一个开关控制是否把「图片 base64 + 提示词 + 模型返回 + 模型标签」落盘；面板实时显示样本条数、占用空间与磁盘余量，支持一键导出为多模态 sharegpt 结构的 dataset.jsonl（图片与 LLaMA-Factory 注册文件一并复制，导出目录可整体搬走），以及清空留存（已导出的产物保留）。
* **维护操作**：清空审核缓存（重置计数，不动历史）、清理没有记录引用的本地图片、清空审核历史、删除单条记录。
* **主题**：跟随 AstrBot 面板的深浅色，也可在顶栏手动切换。

页面通过 `window.AstrBotPluginPage` bridge 调用下列插件 API：

| 方法 | 路由 | 说明 |
| :--- | :--- | :--- |
| GET | `audit/list` | 审核记录（最多 1000 条）、供应商统计、缓存统计、配置摘要、运行统计与处理中任务 |
| GET | `audit/pending` | 仅返回正在审核的任务（供页面轮询） |
| POST | `audit/delete` | 删除单条记录 |
| POST | `audit/clear` | 清空审核历史 |
| GET | `audit/config` | 读取插件配置（API Key 已掩码） |
| POST | `audit/config/update` | 按白名单合并配置并热重载 |
| POST | `audit/providers/save` | 整体保存供应商列表并热重载 |
| POST | `audit/providers/test` | 供应商连通性测试 |
| POST | `audit/cache/clear` | 清空审核缓存指纹 |
| POST | `audit/storage/prune` | 清理孤立图片 |
| GET | `audit/dataset` | 数据集留存状态与样本预览（不回传 base64 正文） |
| POST | `audit/dataset/export` | 导出 dataset.jsonl 与标注侧车 labels.jsonl |
| POST | `audit/dataset/clear` | 清空已留存的样本与图片 |

---

## 🔗 与黑名单插件的兼容

若同时安装了 [AstrBot-ban-Plugins](https://github.com/dahetaoa/AstrBot-ban-Plugins)，需要注意它的工作原理：该插件通过 `event.stop_event()` 中断事件传播，而 AstrBot 的事件派发在遇到已停止的事件时会**跳过后续所有插件**——所以只要本插件的处理器排在它之后，就会完全收不到消息，表现为「被 ban 的用户发图不再被审核」。

本插件已做两层处理：

1. **提高处理器优先级**（`priority=100`）：本插件会先于拦截类插件收到消息，不再被饿死。本插件从不调用 `stop_event()`，因此不会影响其它插件。
2. **可选的行为对齐**（`skip_audit_for_banned`，默认关闭）：开启后，本插件会直接读取黑名单插件的 `sp` 存储，命中全局/群禁用（含群内例外）时同样跳过审核；关闭时被禁用户的图片**仍会被审核**。

默认保持「仍审核」的理由：审核是静默动作——不回复用户、不产生任何消息，因此不违背「禁用 bot」的本意，同时避免被禁用户借机发送违规图片。若你希望被禁用户完全不被处理，把该项打开即可。

---

## 🛠️ 依赖与安装

1.  **AstrBot**: 建议升级至最新版。
2.  **协议端**: 必须使用 **NapCat** 或 **Lagrange** (OneBot 11)。
    * *注意：必须给予 Bot 管理员权限，否则无法撤回和禁言。*
3.  **Python 依赖**: 需要安装 `Pillow`，用于把超过 1MB 的图片压缩后再送审。
4.  **安装方法**:
    将插件文件夹放入 `data/plugins/` 目录，重启 AstrBot 即可。

## ❓ 常见问题 (FAQ)

**Q: 为什么 Bot 没反应？**
A: 
1. 检查是否开启了 `group_scope`（填 `0` 全开）。
2. GIF 会压缩后发送给模型；如果模型或供应商不支持 GIF，请检查后台日志中的接口报错。
3. 检查后台日志，看 LLM 是否报错。

**Q: 为什么撤回/禁言失败？**
A: 
1. Bot 在群里是管理员吗？
2. 你的协议端（NapCat）是否正常连接？
3. 如果失败，Bot 会私聊管理员发战报，状态栏会显示 `❌`。

**Q: 为什么识别不准？**

A: 请更换更强的模型（推荐 Gemini Pro 系列），并在配置中优化 `custom_vision_prompt` 引导词。
