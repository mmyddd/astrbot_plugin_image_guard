"""会话 ID 与群名之间的解析。

AstrBot 的「高级路由」页面能把 UMO 显示成群名（例如 `TTenYX的戴森球1号矩阵研究站`），
靠的是三份数据：

1. `umo_aliases` 表（v4.29+）：`auto_name` 是框架从入站消息里自动记下的名字，
   `user_alias` 是用户用 `/name` 手动起的别名。
2. 平台的群信息接口：`event.get_group()` 会在 aiocqhttp 上调 `get_group_info`，
   在 QQ 官方机器人上调 `/v2/groups/{group_openid}/info`。
3. 入站消息自带的 `message_obj.group.group_name`（aiocqhttp 的群消息段里就有）。

插件的作用域配置只认 ID（OneBot 是群号，官方机器人是群 OpenID），让用户手敲 32 位
OpenID 既容易出错也没法核对。这里把上面三份数据复用起来，把 ID 换回可读的群名。

所有对框架的调用都做了存在性判断：用户在旧版 AstrBot（没有 umo_aliases 表、
没有 get_group）上装插件时，这里退化成只用插件自己的缓存，不会抛异常。
"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger

# 群里能看到的会话类型：不同版本/平台写法不一致，统一按小写前缀匹配
_GROUP_MESSAGE_TYPES = ("groupmessage", "group")

MAX_GROUP_NAME_LENGTH = 80
"""群名长度上限，防止异常数据把配置页撑爆。"""


def normalize_group_name(value: Any) -> str:
    """清洗群名：去空白、压掉换行、截断到合理长度。"""
    if value is None:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return text[:MAX_GROUP_NAME_LENGTH]


def group_id_from_umo(umo: str) -> str:
    """从 UMO 里取出群号 / 群 OpenID。

    UMO 的格式是 `平台:消息类型:会话 ID`。会话 ID 与群 ID 并不总是相等：
    OneBot 开启 `unique_session` 后，会话 ID 会变成 `群号_机器人号`（
    例如 `1604552042_904527337`），必须去掉后缀才能和 `event.get_group_id()`
    对上；QQ 官方机器人的会话 ID 就是群 OpenID，不带下划线。
    """
    if not isinstance(umo, str):
        return ""
    parts = umo.split(":", 2)
    if len(parts) != 3:
        return ""
    message_type = parts[1].strip().lower()
    if not message_type.startswith(_GROUP_MESSAGE_TYPES):
        return ""
    session_id = parts[2].strip()
    if not session_id:
        return ""
    return session_id.split("_", 1)[0]


async def load_alias_names(context: Any) -> dict[str, str]:
    """从 AstrBot 的 umo_aliases 表读出「群 ID -> 群名」映射。

    旧版框架没有这张表或没有这个方法，此时返回空字典，由插件缓存兜底。
    """
    db = None
    try:
        get_db = getattr(context, "get_db", None)
        if callable(get_db):
            db = get_db()
    except Exception as exc:  # pragma: no cover - 取决于框架版本
        logger.debug(f"[ImageGuard] 获取 AstrBot 数据库失败: {exc}")
        return {}

    if db is None or not hasattr(db, "get_umo_aliases"):
        return {}

    try:
        aliases = await db.get_umo_aliases()
    except Exception as exc:
        logger.debug(f"[ImageGuard] 读取会话别名失败: {exc}")
        return {}

    names: dict[str, str] = {}
    for alias in aliases or []:
        umo = getattr(alias, "umo", "") or ""
        group_id = group_id_from_umo(umo)
        if not group_id:
            continue
        # 用户手动起的别名优先，其次是框架自动记下的群名
        name = normalize_group_name(getattr(alias, "user_alias", "")) or normalize_group_name(
            getattr(alias, "auto_name", "")
        )
        if name:
            names[group_id] = name
    return names


def merge_group_names(*sources: dict[str, str]) -> dict[str, str]:
    """合并多份「ID -> 群名」，靠后的来源优先。"""
    merged: dict[str, str] = {}
    for source in sources:
        for group_id, name in (source or {}).items():
            key = str(group_id or "").strip()
            cleaned = normalize_group_name(name)
            if key and cleaned:
                merged[key] = cleaned
    return merged




async def group_name_from_event(event: Any) -> str:
    """从入站事件里取群名，取不到返回空串。

    优先级与 AstrBot 自己在高级路由页里用的一致：

    1. ``message_obj.group.group_name``：aiocqhttp 的群消息段自带，零成本。
    2. ``event.get_group()``：QQ 官方机器人走 ``/v2/groups/{group_openid}/info``，
       aiocqhttp 走 ``get_group_info``。官方接口只对白名单机器人开放，失败时
       会抛异常，这里吞掉并返回空串，让调用方继续走后面的兜底。
    """
    group_id = ""
    try:
        group_id = str(event.get_group_id() or "")
    except Exception:
        group_id = ""
    if not group_id:
        return ""

    message_obj = getattr(event, "message_obj", None)
    group = getattr(message_obj, "group", None)
    name = normalize_group_name(getattr(group, "group_name", ""))
    if name:
        return name

    getter = getattr(event, "get_group", None)
    if not callable(getter):
        return ""
    try:
        fetched = await getter()
    except Exception as exc:
        logger.debug(f"[ImageGuard] 拉取群 {group_id} 的信息失败: {exc}")
        return ""
    return normalize_group_name(getattr(fetched, "group_name", ""))


def display_group_label(group_id: str, names: dict[str, str] | None = None) -> str:
    """配置页和记录页统一的展示文案：有名字就 `名字 (ID)`，没有就只剩 ID。"""
    gid = str(group_id or "").strip()
    if not gid:
        return "私聊"
    name = normalize_group_name((names or {}).get(gid, ""))
    return f"{name} ({gid})" if name else gid
