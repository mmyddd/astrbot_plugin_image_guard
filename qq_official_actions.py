"""QQ 官方机器人（botpy / API v2）的判罚动作。

AstrBot 的 `qq_official` 与 `qq_official_webhook` 两个适配器都基于 botpy 的
`Client`，事件的 `event.bot` 就是它。它没有 OneBot 的 `api.call_action`，
所以撤回、禁言、战报都必须走 API v2 的 REST 路由：

.. code-block:: text

    DELETE /v2/groups/{group_openid}/messages/{message_id}      撤回群聊消息
    DELETE /v2/users/{user_openid}/messages/{message_id}        撤回单聊消息
    POST   /v2/groups/{group_openid}/restrict_chat_setting      设置群成员禁言
    POST   /v2/groups/{group_openid}/files                      群聊富媒体上传
    POST   /v2/users/{openid}/files                             单聊富媒体上传
    POST   /v2/groups/{group_openid}/messages                   发送群聊消息
    POST   /v2/users/{openid}/messages                          发送单聊消息

官方限制（文档原文）：
- 撤回：消息发送超过 2 分钟不可撤回；机器人需为群管理员才能撤回他人消息。
- 禁言：机器人需拥有群管理员身份，单次最长 30 天，且只能操作普通成员。
- 发送：被动回复有效期 5 分钟，主动消息受配额限制。

`botpy` 只在装了 QQ 官方适配器时才存在，因此这里的导入全部是惰性的。
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from astrbot.api import logger

from .image_processor import compress_image_with_result

OFFICIAL_MUTE_MAX_SECONDS = 30 * 24 * 60 * 60
"""官方禁言接口的单次上限：30 天。"""

OFFICIAL_RECALL_WINDOW_SECONDS = 115
"""官方撤回窗口是 2 分钟，这里留 5 秒网络余量，超时就不再发无效请求。"""

OFFICIAL_IMAGE_FILE_TYPE = 1
"""富媒体上传的 file_type：1 = 图片。"""

OFFICIAL_MSG_TYPE_TEXT = 0
OFFICIAL_MSG_TYPE_MEDIA = 7

OFFICIAL_REPORT_IMAGE_MAX_BYTES = 1024 * 1024
"""战报附图压缩上限，复用审核链路的压缩器，避免原图过大传不上去。"""

_CST = timezone(timedelta(hours=8))
"""官方禁言接口要求 RFC3339 时间，用 +08:00 偏移最贴近文档示例。"""


def is_official_client(bot: Any) -> bool:
    """判断事件上的客户端是不是 QQ 官方机器人（botpy Client）。

    两个官方适配器的 `botClient` 都继承自 `botpy.Client`，所以按类型判定
    比按平台名判定更稳（用户可能给平台实例起任意名字）。
    """
    if bot is None or not hasattr(bot, "api"):
        return False
    try:
        from botpy import Client as _BotpyClient
    except Exception:  # botpy 未安装（没装 QQ 官方适配器）
        return False
    return isinstance(bot, _BotpyClient)


def _route(method: str, path: str, **parameters: Any) -> Any:
    """构造 botpy 路由对象（惰性导入 botpy）。"""
    from botpy.http import Route

    return Route(method, path, **parameters)


async def _request(
    bot: Any,
    method: str,
    path: str,
    *,
    payload: dict | None = None,
    **parameters: Any,
) -> Any:
    """发起一次 API v2 请求。

    botpy 在非 2xx 时会抛出 `ServerError`/`HttpErrorDict` 里对应的异常，
    异常消息就是官方返回的错误描述；超时和连接中断则返回 `None`。
    这里不吞异常，交给调用方决定怎么记日志。
    """
    api = getattr(bot, "api", None)
    http = getattr(api, "_http", None)
    if http is None or not hasattr(http, "request"):
        raise RuntimeError("botpy HTTP 客户端不可用，无法调用 QQ 官方接口")

    route = _route(method, path, **parameters)
    if payload is None:
        return await http.request(route)
    return await http.request(route, json=payload)


async def recall_message(
    bot: Any,
    *,
    message_id: str,
    group_id: str = "",
    user_openid: str = "",
) -> bool:
    """撤回群聊 / 单聊消息。

    Args:
        bot: 事件的 `event.bot`（botpy Client）。
        message_id: 消息 ID，取 `event.message_obj.message_id`（即事件里的 `d.id`）。
        group_id: 群 OpenID；群聊撤回时必填。
        user_openid: 用户 OpenID；单聊撤回时必填。
    """
    message_id = str(message_id or "").strip()
    if not message_id:
        return False
    if group_id:
        await _request(
            bot,
            "DELETE",
            "/v2/groups/{group_openid}/messages/{message_id}",
            group_openid=str(group_id),
            message_id=message_id,
        )
        return True
    if user_openid:
        await _request(
            bot,
            "DELETE",
            "/v2/users/{openid}/messages/{message_id}",
            openid=str(user_openid),
            message_id=message_id,
        )
        return True
    return False


async def mute_group_member(
    bot: Any,
    *,
    group_id: str,
    member_openid: str,
    duration_seconds: int,
) -> bool:
    """禁言群成员。

    `ban_duration` 是秒；官方接口要的是「禁言到期时间」（RFC3339）。
    超过 30 天按 30 天算，因为这是接口硬上限。
    """
    group_id = str(group_id or "").strip()
    member_openid = str(member_openid or "").strip()
    if not group_id or not member_openid:
        return False

    seconds = max(1, min(int(duration_seconds), OFFICIAL_MUTE_MAX_SECONDS))
    expire_at = (datetime.now(_CST) + timedelta(seconds=seconds)).isoformat(
        timespec="seconds"
    )
    payload = {
        "members": [
            {
                "op": "add",
                "member_openid": member_openid,
                "mute_expire_at": expire_at,
            }
        ]
    }
    await _request(
        bot,
        "POST",
        "/v2/groups/{group_openid}/restrict_chat_setting",
        payload=payload,
        group_openid=group_id,
    )
    return True


async def upload_image(
    bot: Any,
    *,
    image_base64: str,
    group_id: str = "",
    openid: str = "",
) -> str:
    """上传图片，返回发消息时可用的 `file_info`。"""
    payload = {
        "file_data": image_base64,
        "file_type": OFFICIAL_IMAGE_FILE_TYPE,
        "srv_send_msg": False,
    }
    if group_id:
        resp = await _request(
            bot,
            "POST",
            "/v2/groups/{group_openid}/files",
            payload=payload,
            group_openid=str(group_id),
        )
    elif openid:
        resp = await _request(
            bot,
            "POST",
            "/v2/users/{openid}/files",
            payload=payload,
            openid=str(openid),
        )
    else:
        raise ValueError("上传图片需要 group_id 或 openid")

    file_info = ""
    if isinstance(resp, dict):
        file_info = str(resp.get("file_info") or "")
    if not file_info:
        raise RuntimeError(f"上传图片未返回 file_info: {resp!r}")
    return file_info


async def send_report(
    bot: Any,
    *,
    text: str,
    image_base64: str = "",
    group_id: str = "",
    openid: str = "",
    reply_msg_id: str = "",
) -> bool:
    """发送战报（文本 + 可选图片）。

    有图片时走「先上传再发 media 消息」两步，上传失败就退回纯文本，
    不让一张图把整条战报弄丢。
    """
    if not group_id and not openid:
        return False

    file_info = ""
    if image_base64:
        try:
            file_info = await upload_image(
                bot, image_base64=image_base64, group_id=group_id, openid=openid
            )
        except Exception as exc:
            logger.warning(f"[ImageGuard] 官方接口上传战报图片失败，改为只发文本: {exc}")
            file_info = ""

    payload: dict[str, Any] = {"content": text or ""}
    if file_info:
        payload["msg_type"] = OFFICIAL_MSG_TYPE_MEDIA
        payload["media"] = {"file_info": file_info}
    else:
        payload["msg_type"] = OFFICIAL_MSG_TYPE_TEXT

    if reply_msg_id:
        # 被动回复：必须在收到消息 5 分钟内，且与 msg_id 属于同一会话
        payload["msg_id"] = str(reply_msg_id)
        payload["msg_seq"] = 1

    if group_id:
        await _request(
            bot,
            "POST",
            "/v2/groups/{group_openid}/messages",
            payload=payload,
            group_openid=str(group_id),
        )
    else:
        await _request(
            bot,
            "POST",
            "/v2/users/{openid}/messages",
            payload=payload,
            openid=str(openid),
        )
    return True


def official_image_base64(
    path_or_url: str,
    max_bytes: int = OFFICIAL_REPORT_IMAGE_MAX_BYTES,
) -> str:
    """把本地图片压成纯 base64（不含 `data:` 前缀）。

    官方上传图片接口吃纯 base64；战报用的是原图，可能有好几 MB，
    所以复用审核链路的压缩器先压到 1 MB 以内。
    """
    raw_path = str(path_or_url or "").replace("file:///", "")
    if not raw_path:
        return ""
    path = Path(raw_path)
    if not path.exists():
        return ""
    try:
        data = path.read_bytes()
    except OSError as exc:
        logger.warning(f"[ImageGuard] 读取战报图片失败 {path}: {exc}")
        return ""

    data_url = ""
    try:
        data_url = compress_image_with_result(data, max_bytes).data_url or ""
    except Exception as exc:
        logger.warning(f"[ImageGuard] 压缩战报图片失败 {path}: {exc}")

    if data_url:
        _, _, encoded = data_url.partition(",")
        return encoded
    if len(data) > max_bytes:
        logger.warning(
            f"[ImageGuard] 战报图片 {len(data)} 字节超过 {max_bytes} 且压缩失败，跳过图片"
        )
        return ""
    return base64.b64encode(data).decode("ascii")
