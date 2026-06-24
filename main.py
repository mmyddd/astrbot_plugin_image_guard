import httpx
import re
import random
import json
from datetime import datetime
from pathlib import Path
from .image_processor import prepare_audit_images
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Image, Plain

AUDIT_IMAGE_DIR = Path("data") / "plugin_data" / "image_guard" / "audit_images"

@register("image_guard", "YEZI", "图片内容审查卫士", "1.7.0")
class ImageGuard(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config

        # ── 审核历史 API ──
        context.register_web_api(
            "/astrbot_plugin_image_guard/audit/list",
            self._api_audit_list,
            ["GET"],
            "获取审核记录列表",
        )
        context.register_web_api(
            "/astrbot_plugin_image_guard/audit/clear",
            self._api_audit_clear,
            ["POST", "DELETE"],
            "清空审核记录",
        )
        context.register_web_api(
            "/astrbot_plugin_image_guard/audit/delete",
            self._api_audit_delete,
            ["POST", "DELETE"],
            "删除单条审核记录",
        )
        context.register_web_api(
            "/astrbot_plugin_image_guard/audit/config",
            self._api_audit_config_get,
            ["GET"],
            "获取插件配置",
        )
        context.register_web_api(
            "/astrbot_plugin_image_guard/audit/config/update",
            self._api_audit_config_update,
            ["POST"],
            "更新插件配置并重载",
        )
        context.register_web_api(
            "/astrbot_plugin_image_guard/audit/image",
            self._api_audit_image,
            ["GET"],
            "获取审核记录图片",
        )

    # ── 消息处理入口 ────────────────────────────────────────────

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_image_message(self, event: AstrMessageEvent):
        # === 1. 范围控制逻辑 ===
        group_id = event.get_group_id() or ""
        user_id = event.get_sender_id() or ""
        is_group = bool(group_id)

        group_scope = [str(x) for x in self.config.get("group_scope", ["0"])]
        private_scope = [str(x) for x in self.config.get("private_scope", [])]

        if is_group:
            if "0" not in group_scope and group_id not in group_scope: return
        else:
            if "0" not in private_scope and user_id not in private_scope: return

        # === 2. 表情包与GIF强过滤 (Sticker Filter) ===
        raw_chain = []
        try:
            if hasattr(event, "original_event") and hasattr(event.original_event, "message"):
                raw_chain = event.original_event.message
            elif hasattr(event.message_obj, "raw_message"):
                raw_chain = event.message_obj.raw_message

            if isinstance(raw_chain, list):
                for seg in raw_chain:
                    if isinstance(seg, dict) and seg.get("type") == "image":
                        data = seg.get("data", {})
                        sub_type = int(data.get("sub_type", 0))
                        if sub_type != 0: return  # 忽略表情包
        except Exception:
            pass

        # === 3. 提取图片 URL 并过滤 GIF ===
        message_obj = event.message_obj
        if not message_obj.message: return

        image_urls = []
        for component in message_obj.message:
            if isinstance(component, Image):
                if component.url:
                    clean_url = component.url.split('?')[0].lower()
                    if clean_url.endswith('.gif'):
                        continue
                    image_urls.append(component.url)

        if not image_urls: return

        # === 4. 概率抽查 ===
        if random.random() > self.config.get("check_probability", 1.0): return

        # === 5. 检查配置 ===
        forbidden_texts = self.config.get("sensitive_texts", [])
        forbidden_descs = self.config.get("forbidden_descriptions", [])

        if not forbidden_texts and not forbidden_descs: return

        # === 6. 审核逻辑 ===
        custom_instruction = self.config.get("custom_vision_prompt", "")
        prompt = (
            "你是一个严格但公正的内容审核员。请分析图片是否包含违规信息。\n"
            f"【自定义关注点】\n{custom_instruction}\n\n"
            "【违规标准】\n"
            f"1. 包含文字：{str(forbidden_texts)}\n"
            f"2. 包含画面：{str(forbidden_descs)}\n\n"
            "【输出格式要求】\n"
            "请严格按照以下两行格式输出，不要包含其他废话：\n"
            "REASON: [这里简要说明判断理由，不超过20字]\n"
            "RESULT: [SAFE 或 VIOLATION]\n"
        )

        try:
            max_image_bytes = int(self.config.get("compressed_image_max_bytes", 1048576))
            keep_compressed_image_in_temp = bool(
                self.config.get("keep_compressed_image_in_temp", False)
            )
            audit_image_urls = await prepare_audit_images(
                image_urls,
                max_image_bytes,
                keep_compressed_image_in_temp=keep_compressed_image_in_temp,
                compressed_image_temp_dir=Path("data") / "temp" / "astrbot_plugin_image_guard",
                log_compression_result=logger.info,
            )
            response_text = await self._call_audit_llm(prompt, audit_image_urls)
            if self.config.get("debug_log_llm_response", False):
                logger.info(f"[ImageGuard] LLM 返回内容: {response_text}")

            # === 7. 解析结果 ===
            result_match = re.search(r"RESULT:\s*(VIOLATION|SAFE)", response_text, re.IGNORECASE)
            reason_match = re.search(r"REASON:\s*(.+)", response_text, re.IGNORECASE)

            is_violation = False
            reason_str = "未说明理由"

            if result_match and "VIOLATION" in result_match.group(1).upper():
                is_violation = True
            # 兜底检测
            if not result_match and "VIOLATION" in response_text.upper():
                is_violation = True

            if reason_match:
                reason_str = reason_match.group(1).strip()
            elif is_violation:
                reason_str = response_text.split('\n')[0][:50]

            # === 8. 判罚 ===
            if is_violation:
                logger.info(f"[ImageGuard] 违规命中: {reason_str}")
                await self.enforce_penalty(event, image_urls[0], is_group, reason_str)

        except Exception as e:
            logger.error(f"[ImageGuard] Check failed: {e}")

    # ── 多供应商调用核心 ──────────────────────────────────────

    async def _call_audit_llm(self, prompt, image_urls):
        """按 llm_providers 列表顺序依次尝试，第一个成功的即返回。

        列表中每个条目是一个"供应商"，由其 ``__template_key`` 字段区分类型：
        - ``openai_compatible``：通过 OpenAI 兼容 API 调用（需填写 api_key / base_url）
        - ``astrbot_provider``：复用 AstrBot 当前会话配置的 LLM Provider
        """
        providers = self.config.get("llm_providers", [])
        if not providers:
            logger.info("[ImageGuard] 未配置任何供应商，直接使用 AstrBot Provider")
            result = await self._call_astrbot_provider(prompt, image_urls)
            await self._track_provider_usage("AstrBot Provider (fallback)")
            return result

        last_exception = None
        for prov in providers:
            template = prov.get("__template_key", "")
            prov_name = prov.get("name", "Unknown")
            try:
                if template == "astrbot_provider":
                    logger.info(f"[ImageGuard] 尝试 AstrBot Provider「{prov_name}」...")
                    result = await self._call_astrbot_provider(prompt, image_urls)
                    await self._track_provider_usage(prov_name)
                    return result

                else:
                    # 所有非 astrbot_provider 的模板（openai_compatible / modelscope 等）
                    # 均视为 OpenAI 兼容接口处理
                    logger.info(f"[ImageGuard] 尝试 OpenAI 兼容供应商「{prov_name}」...")
                    result = await self._call_single_api(prompt, image_urls, prov)
                    if not result or not result.strip():
                        raise ValueError(f"「{prov_name}」返回内容为空")
                    logger.info(f"[ImageGuard] 供应商「{prov_name}」审核成功")
                    await self._track_provider_usage(prov_name)
                    return result

            except Exception as e:
                last_exception = e
                logger.warning(f"[ImageGuard] 供应商「{prov_name}」调用失败: {e}")
                continue

        # 全部失败
        if last_exception:
            raise RuntimeError(
                f"所有供应商均不可用（共 {len(providers)} 个）"
            ) from last_exception
        raise RuntimeError("没有可用的供应商配置")

    async def _call_single_api(self, prompt, image_urls, provider: dict):
        """调用单个 OpenAI 兼容 API 进行审核。

        Args:
            provider: 供应商配置字典，包含 api_key / base_url / model / name 等字段。
        """
        api_key = provider.get("api_key", "")
        base_url = provider.get("base_url", "")
        model_name = provider.get("model", "")
        api_name = provider.get("name", "OpenAI API")

        if not api_key or not base_url:
            return None

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt}
                ]
            }
        ]
        for url in image_urls:
            messages[0]["content"].append({
                "type": "image_url",
                "image_url": {"url": url}
            })

        timeout_seconds = float(self.config.get("llm_timeout_seconds", 120))
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            payload = {
                "model": model_name or "gpt-4o",
                "messages": messages,
                "max_tokens": int(self.config.get("llm_max_tokens", 512)),
            }
            reasoning_effort = self.config.get("reasoning_effort", "")
            if reasoning_effort:
                payload["reasoning_effort"] = reasoning_effort

            resp = await client.post(
                f"{base_url.rstrip('/')}/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"}
            )
            resp.raise_for_status()
            response_json = resp.json()
            if self.config.get("debug_log_llm_response", False):
                logger.info(
                    f"[ImageGuard] {api_name} 原始响应: "
                    f"{json.dumps(response_json, ensure_ascii=False)}"
                )

            choice = response_json["choices"][0]
            message = choice.get("message", {})
            content = message.get("content") or choice.get("text")
            if not content or not str(content).strip():
                finish_reason = choice.get("finish_reason", "unknown")
                raise ValueError(
                    f"{api_name} 返回内容为空，finish_reason={finish_reason}"
                )

            return str(content)

    async def _call_astrbot_provider(self, prompt, image_urls):
        """回退到 AstrBot 当前会话配置的 LLM Provider。"""
        provider = self.context.get_using_provider()
        if not provider:
            raise ValueError("No provider available")

        resp = await provider.text_chat(
            prompt=prompt,
            image_urls=image_urls,
            session_id=None,
        )
        return resp.completion_text

    # ── 判罚执行 ────────────────────────────────────────────────

    async def enforce_penalty(self, event: AstrMessageEvent, violation_img_url: str, is_group: bool, reason: str):
        """执行判罚 (依赖 OneBot 协议)"""
        user_id = event.get_sender_id()
        group_id = event.get_group_id()
        user_name = event.get_sender_name()

        recalled = False
        banned = False
        duration = int(self.config.get("ban_duration", 86400))

        client = None
        if hasattr(event, "bot"): client = event.bot
        elif hasattr(event, "client"): client = event.client

        if not client: return
        if not hasattr(client, "api") or not hasattr(client.api, "call_action"):
            return

        # A. 撤回消息
        if self.config.get("enable_recall", True) and is_group:
            try:
                msg_id = None
                if hasattr(event.message_obj, "message_id"):
                    msg_id = event.message_obj.message_id

                if msg_id:
                    await client.api.call_action('delete_msg', message_id=msg_id)
                    recalled = True
            except Exception as e:
                logger.warning(f"[ImageGuard] Silent Recall failed: {e}")

        # B. 禁言用户
        if duration > 0 and is_group:
            try:
                await client.api.call_action(
                    "set_group_ban",
                    group_id=group_id,
                    user_id=user_id,
                    duration=duration
                )
                banned = True
            except Exception as e:
                logger.warning(f"[ImageGuard] Silent Ban failed: {e}")

        # C. 上报证据 (私聊)
        report_target = self._get_report_target(group_id, is_group)
        if report_target:
            try:
                target_type, target_id = report_target
                source_str = f"群 {group_id}" if is_group else "私聊"
                status_str = f"撤回:{'✅' if recalled else '❌'} 禁言:{'✅' if banned else '❌'}"

                text_content = (
                    f"🕵️ [静默执法报告]\n"
                    f"来源: {source_str}\n"
                    f"用户: {user_name} ({user_id})\n"
                    f"理由: {reason}\n"
                    f"状态: {status_str}\n"
                    f"证据:"
                )

                message_payload = [
                    {"type": "text", "data": {"text": text_content}},
                    {"type": "image", "data": {"file": violation_img_url}}
                ]

                if target_type == "group":
                    await client.api.call_action(
                        "send_group_msg",
                        group_id=target_id,
                        message=message_payload
                    )
                else:
                    await client.api.call_action(
                        "send_private_msg",
                        user_id=target_id,
                        message=message_payload
                    )

            except Exception as e:
                logger.error(f"[ImageGuard] Report failed: {e}")

        # D. 保存审核记录
        try:
            await self._save_audit_record(
                event, violation_img_url, reason,
                recalled, banned, duration, is_group,
            )
        except Exception as e:
            logger.error(f"[ImageGuard] 保存审核记录失败: {e}")

    def _get_report_target(self, group_id: str | None, is_group: bool):
        if is_group and group_id:
            for entry in self.config.get("group_report_targets", []):
                # 兼容旧版字符串格式 "来源群号:type:id"
                if isinstance(entry, str):
                    group_text, _, target_text = entry.partition(":")
                    if not _ or group_text.strip() != str(group_id):
                        continue
                    target_kind, sep, target_id_text = target_text.partition(":")
                    if sep:
                        target_kind = target_kind.strip().lower()
                        target_id = int(target_id_text.strip())
                    else:
                        target_kind = "private"
                        target_id = int(target_text.strip())
                    return target_kind, target_id

                # 新版 template_list 格式
                if entry.get("source_group_id", "").strip() == str(group_id):
                    target_kind = entry.get("target_type", "private").strip().lower()
                    raw_id = entry.get("target_id", "").strip()
                    if raw_id:
                        return target_kind, int(raw_id)

        report_target = self.config.get("report_target_id")
        if report_target:
            return "private", int(str(report_target).strip())

        return None

    # ── 审核历史 ────────────────────────────────────────────────

    async def _save_audit_record(self, event, image_url, reason, recalled, banned, duration, is_group):
        record = {
            "id": datetime.now().strftime("%Y%m%d%H%M%S%f"),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user_id": event.get_sender_id() or "",
            "user_name": event.get_sender_name() or "",
            "group_id": event.get_group_id() or "",
            "image_url": image_url,
            "reason": reason,
            "recalled": recalled,
            "banned": banned,
            "ban_duration": duration,
            "is_group": is_group,
        }
        # 下载原始图片到本地持久化存储（避免 QQ 图片 URL 过期）
        if image_url and not image_url.startswith("data:"):
            local_path = await self._download_and_save_image(image_url, record["id"])
            if local_path:
                record["local_image"] = local_path
        records = await self.get_kv_data("audit_history", [])
        if not isinstance(records, list):
            records = []
        records.append(record)
        # 最多保留 N 条
        max_records = int(self.config.get("audit_history_max_records", 500))
        if max_records > 0 and len(records) > max_records:
            records = records[-max_records:]
        await self.put_kv_data("audit_history", records)

    async def _track_provider_usage(self, provider_name: str):
        """记录供应商调用次数"""
        stats = await self.get_kv_data("provider_stats", {})
        if not isinstance(stats, dict):
            stats = {}
        stats[provider_name] = stats.get(provider_name, 0) + 1
        await self.put_kv_data("provider_stats", stats)

    async def _download_and_save_image(self, image_url: str, record_id: str):
        """下载原始图片并保存到持久化目录，返回本地路径或 None"""
        try:
            AUDIT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                response = await client.get(image_url)
                response.raise_for_status()
                data = response.content
                # 通过文件头魔数检测真实格式，避免 Content-Type 不准确导致扩展名错误
                ext = ".jpg"
                if data[:8] == b'\x89PNG\r\n\x1a\n':
                    ext = ".png"
                elif data[:6] in (b'GIF87a', b'GIF89a'):
                    ext = ".gif"
                elif data[:4] == b'RIFF' and data[8:12] == b'WEBP':
                    ext = ".webp"
                elif data[:2] == b'BM':
                    ext = ".bmp"
                file_path = (AUDIT_IMAGE_DIR / f"{record_id}{ext}").resolve()
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_bytes(data)
                return str(file_path)
        except Exception as e:
            logger.warning(f"[ImageGuard] 下载审核图片失败: {e}")
            return None

    async def _api_audit_image(self):
        """返回本地存储的审核图片"""
        from quart import request, send_file
        record_id = request.args.get("id")
        if not record_id:
            return {"message": "missing id"}, 400
        records = await self.get_kv_data("audit_history", [])
        if not isinstance(records, list):
            records = []
        for r in records:
            if r.get("id") == record_id:
                local_image = r.get("local_image")
                if local_image:
                    path = Path(local_image)
                    if not path.exists():
                        path = Path(local_image).resolve()
                    if path.exists():
                        suffix = path.suffix.lower()
                        mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                                    ".png": "image/png", ".gif": "image/gif",
                                    ".webp": "image/webp", ".bmp": "image/bmp"}
                        mimetype = mime_map.get(suffix, "image/jpeg")
                        logger.info(f"[ImageGuard] 返回本地图片: {path} ({path.stat().st_size} bytes, {mimetype})")
                        return await send_file(str(path), mimetype=mimetype)
                    else:
                        logger.warning(f"[ImageGuard] 图片文件不存在: {local_image}")
                break
        logger.warning(f"[ImageGuard] 未找到图片: record_id={record_id}")
        return {"message": "image not found"}, 404

    async def _api_audit_list(self):
        records = await self.get_kv_data("audit_history", [])
        if not isinstance(records, list):
            records = []
        stats = await self.get_kv_data("provider_stats", {})
        if not isinstance(stats, dict):
            stats = {}
        # 为有本地缓存的记录补充 local_image_url 和文件大小
        for r in records:
            if r.get("local_image"):
                r["local_image_url"] = f"/api/plug/astrbot_plugin_image_guard/audit/image?id={r['id']}"
                path = Path(r["local_image"])
                if path.exists():
                    r["local_image_size"] = path.stat().st_size
        return {"records": records, "provider_stats": stats}

    async def _api_audit_clear(self):
        await self.put_kv_data("audit_history", [])
        return {"message": "ok"}

    async def _api_audit_delete(self):
        from quart import request
        data = await request.get_json(silent=True) or {}
        record_id = request.args.get("id") or data.get("id")
        if not record_id:
            return {"message": "missing id"}, 400
        records = await self.get_kv_data("audit_history", [])
        if not isinstance(records, list):
            records = []
        records = [r for r in records if r.get("id") != record_id]
        await self.put_kv_data("audit_history", records)
        return {"message": "ok"}

    async def _api_audit_config_get(self):
        """返回当前插件配置（排除 KV 类数据）"""
        from astrbot.core.star.star import star_registry
        for plugin_md in star_registry:
            if plugin_md.name == "astrbot_plugin_image_guard":
                if plugin_md.config:
                    return dict(plugin_md.config)
                break
        return dict(self.config)

    async def _api_audit_config_update(self):
        """更新插件配置并重载插件"""
        from quart import request
        from astrbot.core.star.star import star_registry

        new_config = await request.get_json()
        if not new_config:
            return {"message": "empty config"}, 400

        for plugin_md in star_registry:
            if plugin_md.name == "astrbot_plugin_image_guard":
                if plugin_md.config:
                    plugin_md.config.save_config(new_config)
                    # 重载插件使新配置生效
                    if hasattr(self.context, "_star_manager"):
                        await self.context._star_manager.reload("astrbot_plugin_image_guard")
                    return {"message": "ok"}
                break

        return {"message": "plugin config not found"}, 404
