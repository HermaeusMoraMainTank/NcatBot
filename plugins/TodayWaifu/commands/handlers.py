"""命令处理函数集合。"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Optional

from common.utils.CommonUtil import CommonUtil
from ncatbot.types import At, MessageArray

from ..core import (
    display_name,
    force_cd_remaining,
    format_duration,
    get_daily_limit,
    get_propose_cd_seconds,
    is_cd_exempt,
    normalize_id_set,
    propose_cd_remaining,
    today_str,
    upsert_draw_record,
    user_draw_count,
)
from ..render import render_rbq_ranking, render_relation_graph

if TYPE_CHECKING:
    from ..TodayWaifu import TodayWaifu


def extract_at_user_id(event) -> Optional[str]:
    self_id = str(getattr(event, "self_id", "") or "")
    for seg in event.message or []:
        if isinstance(seg, At):
            uid = str(seg.user_id)
            if uid and uid != "all" and uid != self_id:
                return uid
    return None


async def _member_map(plugin: "TodayWaifu", group_id: str) -> dict[str, Any]:
    members_response = await plugin.api.qq.query.get_group_member_list(
        group_id=group_id
    )
    members = CommonUtil.parse_group_member_list(members_response)
    return {str(m.user_id): m for m in members}


async def _send_wife_result(
    plugin: "TodayWaifu",
    event,
    *,
    user_id: str,
    wife_id: str,
    wife_name: str,
    prefix: str,
) -> None:
    avatar = await CommonUtil.get_avatar_async(wife_id)
    msg = MessageArray()
    msg.add_at(user_id)
    msg.add_text(prefix)
    msg.add_image(avatar)
    msg.add_text(f" {wife_name}({wife_id})")
    if plugin.cfg_bool("at_waifu"):
        msg.add_text(" ")
        msg.add_at(wife_id)
    await plugin.api.qq.post_group_msg(group_id=event.group_id, rtf=msg)


async def cmd_draw(plugin: "TodayWaifu", event) -> None:
    group_id = str(event.group_id)
    user_id = str(event.sender.user_id)
    cfg = plugin.cfg_dict()
    limit = get_daily_limit(cfg)
    used = user_draw_count(plugin.store, group_id, user_id)

    if (not is_cd_exempt(user_id)) and used >= limit:
        records = plugin.store.get_user_today_records(group_id, user_id, today_str())
        last = records[-1] if records else None
        if last:
            await _send_wife_result(
                plugin,
                event,
                user_id=user_id,
                wife_id=str(last["wife_id"]),
                wife_name=str(last["wife_name"]),
                prefix=" 你今天的群友老婆是：",
            )
        else:
            await plugin.api.qq.post_group_msg(
                group_id=event.group_id,
                text=f"今天抽取次数已用完（{limit}次）~",
            )
        return

    # 小概率整活
    import random

    rv = random.random()
    if 0.05 <= rv <= 0.12:
        await plugin.api.qq.post_group_msg(group_id=event.group_id, text="今*老婆")
        return
    if rv < 0.05:
        await plugin.api.qq.post_group_msg(
            group_id=event.group_id, text="你快醒醒 你没有老婆"
        )
        return

    wife = await plugin.pick_wife(event, exclude_ids={user_id})
    if wife is None:
        await plugin.api.qq.post_group_msg(
            group_id=event.group_id,
            text="活跃群友不够抽了……先多聊聊天再来吧~",
        )
        return

    wife_id = str(wife.user_id)
    wife_name = display_name(wife, wife_id)
    upsert_draw_record(plugin.store, cfg, group_id, user_id, wife_id, wife_name, False)

    if plugin.cfg_bool("auto_set_other_half"):
        other_used = user_draw_count(plugin.store, group_id, wife_id)
        if other_used < limit:
            me_name = display_name(event.sender, user_id)
            upsert_draw_record(
                plugin.store, cfg, group_id, wife_id, user_id, me_name, False
            )

    await _send_wife_result(
        plugin,
        event,
        user_id=user_id,
        wife_id=wife_id,
        wife_name=wife_name,
        prefix=" 你今天的群友老婆是：",
    )


async def cmd_history(plugin: "TodayWaifu", event) -> None:
    group_id = str(event.group_id)
    user_id = str(event.sender.user_id)
    cfg = plugin.cfg_dict()
    limit = get_daily_limit(cfg)
    records = plugin.store.get_user_today_records(group_id, user_id, today_str())
    remain = max(0, limit - len(records))
    if not records:
        await plugin.api.qq.post_group_msg(
            group_id=event.group_id,
            text=f"你今天还没抽过老婆哦~ 剩余次数：{remain}/{limit}",
        )
        return
    lines = [f"今日记录（剩余 {remain}/{limit}）："]
    for i, r in enumerate(records, 1):
        tag = "强娶" if r.get("forced") else "抽中"
        lines.append(f"{i}. [{tag}] {r['wife_name']}({r['wife_id']})")
    await plugin.api.qq.post_group_msg(group_id=event.group_id, text="\n".join(lines))


async def cmd_force_marry(plugin: "TodayWaifu", event) -> None:
    group_id = str(event.group_id)
    user_id = str(event.sender.user_id)
    cfg = plugin.cfg_dict()
    target_id = extract_at_user_id(event)
    if not target_id:
        await plugin.api.qq.post_group_msg(
            group_id=event.group_id,
            text="请艾特要强娶的对象：强娶 @对方",
        )
        return
    if target_id == user_id:
        await plugin.api.qq.post_group_msg(
            group_id=event.group_id, text="不能强娶自己哦"
        )
        return

    excluded = normalize_id_set(cfg.get("force_marry_excluded_users"))
    if target_id in excluded:
        await plugin.api.qq.post_group_msg(
            group_id=event.group_id, text="对方在强娶保护名单中，无法强娶"
        )
        return

    remain = force_cd_remaining(plugin.store, group_id, user_id, cfg)
    if remain:
        await plugin.api.qq.post_group_msg(
            group_id=event.group_id,
            text=f"强娶冷却中，还需 {format_duration(remain)}",
        )
        return

    limit = get_daily_limit(cfg)
    if (not is_cd_exempt(user_id)) and user_draw_count(
        plugin.store, group_id, user_id
    ) >= limit:
        await plugin.api.qq.post_group_msg(
            group_id=event.group_id,
            text=f"今天抽取/强娶次数已用完（{limit}次）",
        )
        return

    members = await _member_map(plugin, group_id)
    target = members.get(target_id)
    if target is None:
        await plugin.api.qq.post_group_msg(
            group_id=event.group_id, text="找不到对方，确认还在群里吗？"
        )
        return
    if (not plugin.cfg_bool("allow_marry_bot")) and (
        target_id == str(plugin.bot_id) or bool(getattr(target, "is_robot", False))
    ):
        await plugin.api.qq.post_group_msg(
            group_id=event.group_id, text="不可以强娶机器人哦"
        )
        return

    wife_name = display_name(target, target_id)
    upsert_draw_record(plugin.store, cfg, group_id, user_id, target_id, wife_name, True)
    if not is_cd_exempt(user_id):
        plugin.store.set_force_cd(group_id, user_id, time.time())
    plugin.store.add_rbq(group_id, target_id, time.time())

    await _send_wife_result(
        plugin,
        event,
        user_id=user_id,
        wife_id=target_id,
        wife_name=wife_name,
        prefix=" 强娶成功！今日老婆是：",
    )


async def cmd_propose(plugin: "TodayWaifu", event) -> None:
    group_id = str(event.group_id)
    user_id = str(event.sender.user_id)
    cfg = plugin.cfg_dict()
    target_id = extract_at_user_id(event)
    if not target_id:
        await plugin.api.qq.post_group_msg(
            group_id=event.group_id, text="请艾特求婚对象：求婚 @对方"
        )
        return
    if target_id == user_id:
        await plugin.api.qq.post_group_msg(
            group_id=event.group_id, text="不能向自己求婚哦"
        )
        return

    remain = propose_cd_remaining(plugin.store, group_id, user_id)
    if remain:
        await plugin.api.qq.post_group_msg(
            group_id=event.group_id,
            text=f"求婚冷却中，还需 {format_duration(remain)}",
        )
        return

    timeout = int(cfg.get("propose_timeout_seconds", 30) or 30)
    plugin.create_propose(group_id, user_id, target_id, timeout)
    msg = MessageArray()
    msg.add_at(target_id)
    msg.add_text(
        f" 收到来自 {display_name(event.sender, user_id)} 的求婚！\n"
        f"{timeout} 秒内回复「同意」或「拒绝」"
    )
    await plugin.api.qq.post_group_msg(group_id=event.group_id, rtf=msg)


async def handle_propose_reply(plugin: "TodayWaifu", event, text: str) -> bool:
    """处理同意/拒绝/强娶确认。命中返回 True。"""
    group_id = str(event.group_id)
    user_id = str(event.sender.user_id)
    text = text.strip()

    # 拒绝后的强娶确认（仅在明确回复时消费）
    if text in ("强娶", "确认强娶", "是"):
        confirm = plugin.pop_force_confirm(group_id, user_id)
        if not confirm:
            return False
        event_like_target = confirm["target_id"]
        cfg = plugin.cfg_dict()
        remain = force_cd_remaining(plugin.store, group_id, user_id, cfg)
        if remain:
            await plugin.api.qq.post_group_msg(
                group_id=event.group_id,
                text=f"强娶冷却中，还需 {format_duration(remain)}",
            )
            return True
        members = await _member_map(plugin, group_id)
        target = members.get(event_like_target)
        wife_name = display_name(target, event_like_target)
        upsert_draw_record(
            plugin.store, cfg, group_id, user_id, event_like_target, wife_name, True
        )
        if not is_cd_exempt(user_id):
            plugin.store.set_force_cd(group_id, user_id, time.time())
        plugin.store.add_rbq(group_id, event_like_target, time.time())
        await _send_wife_result(
            plugin,
            event,
            user_id=user_id,
            wife_id=event_like_target,
            wife_name=wife_name,
            prefix=" 强娶成功！今日老婆是：",
        )
        return True

    if text not in ("同意", "拒绝"):
        return False

    req = plugin.get_propose_for_target(group_id, user_id)
    if not req:
        return False

    proposer_id = req["proposer_id"]
    plugin.clear_propose(group_id, user_id)

    if text == "同意":
        cfg = plugin.cfg_dict()
        members = await _member_map(plugin, group_id)
        a = members.get(proposer_id)
        b = members.get(user_id)
        a_name = display_name(a, proposer_id)
        b_name = display_name(b, user_id)
        upsert_draw_record(
            plugin.store, cfg, group_id, proposer_id, user_id, b_name, False
        )
        upsert_draw_record(
            plugin.store, cfg, group_id, user_id, proposer_id, a_name, False
        )
        cd = get_propose_cd_seconds(cfg)
        if cd > 0:
            expire = time.time() + cd
            if not is_cd_exempt(proposer_id):
                plugin.store.set_propose_cd(
                    group_id, proposer_id, expire, user_id, "proposer"
                )
            if not is_cd_exempt(user_id):
                plugin.store.set_propose_cd(
                    group_id, user_id, expire, proposer_id, "accepter"
                )
        await plugin.api.qq.post_group_msg(
            group_id=event.group_id,
            text=f"恭喜！{a_name} 与 {b_name} 求婚成功 💍",
        )
        return True

    # 拒绝 → 提示可转强娶
    plugin.create_force_confirm(group_id, proposer_id, user_id, 30)
    msg = MessageArray()
    msg.add_at(proposer_id)
    msg.add_text(" 对方拒绝了求婚。30秒内回复「强娶」可转入强娶流程。")
    await plugin.api.qq.post_group_msg(group_id=event.group_id, rtf=msg)
    return True


async def cmd_graph(plugin: "TodayWaifu", event) -> None:
    group_id = str(event.group_id)
    records = plugin.store.get_today_records(group_id, today_str())
    if not records:
        await plugin.api.qq.post_group_msg(
            group_id=event.group_id, text="今日还没有老婆记录，先抽一个吧~"
        )
        return

    group_name = str(group_id)
    try:
        info = await plugin.api.qq.query.get_group_info(group_id)
        group_name = getattr(info, "group_name", None) or group_name
    except Exception:
        pass

    members = await _member_map(plugin, group_id)
    user_map = {uid: display_name(m, uid) for uid, m in members.items()}
    for r in records:
        user_map.setdefault(str(r["user_id"]), str(r["user_id"]))
        user_map.setdefault(str(r["wife_id"]), str(r.get("wife_name") or r["wife_id"]))

    path = await render_relation_graph(
        group_id=group_id,
        group_name=group_name,
        records=records,
        user_map=user_map,
        iterations=int(plugin.cfg_dict().get("iterations", 140) or 140),
        out_dir=plugin.output_dir,
    )
    if not path:
        await plugin.api.qq.post_group_msg(
            group_id=event.group_id,
            text="关系图渲染失败，请确认已安装 playwright chromium",
        )
        return
    msg = MessageArray()
    msg.add_image(str(path))
    await plugin.api.qq.post_group_msg(group_id=event.group_id, rtf=msg)


async def cmd_rbq(plugin: "TodayWaifu", event) -> None:
    group_id = str(event.group_id)
    ranking_raw = plugin.store.rbq_ranking(group_id, days=30, limit=10)
    if not ranking_raw:
        await plugin.api.qq.post_group_msg(
            group_id=event.group_id, text="近30天还没有被强娶记录哦~"
        )
        return
    members = await _member_map(plugin, group_id)
    ranking = []
    for i, (uid, count) in enumerate(ranking_raw, 1):
        ranking.append(
            {
                "rank": i,
                "name": display_name(members.get(uid), uid),
                "count": count,
                "avatar": f"https://q4.qlogo.cn/headimg_dl?dst_uin={uid}&spec=100",
            }
        )
    path = await render_rbq_ranking(
        title="近30天被强娶排行",
        ranking=ranking,
        out_dir=plugin.output_dir,
    )
    if not path:
        # 文本回退
        lines = ["近30天被强娶排行："]
        for row in ranking:
            lines.append(f"#{row['rank']} {row['name']} — {row['count']} 次")
        await plugin.api.qq.post_group_msg(
            group_id=event.group_id, text="\n".join(lines)
        )
        return
    msg = MessageArray()
    msg.add_image(str(path))
    await plugin.api.qq.post_group_msg(group_id=event.group_id, rtf=msg)


async def cmd_help(plugin: "TodayWaifu", event) -> None:
    text = (
        "🌸 抽老婆帮助\n"
        "今日老婆 / jrlp / 抽老婆 — 抽取今日老婆\n"
        "我的老婆 / wdlp — 查看今日记录与剩余次数\n"
        "强娶 @用户 / qiangqu — 强娶（有冷却）\n"
        "求婚 @用户 / qh — 求婚，对方同意/拒绝\n"
        "关系图 / gxt — 今日羁绊关系图\n"
        "rbq排行 / rbqph — 近30天被强娶排行\n"
        "重置记录 / 重置强娶时间 / 重置求婚时间 — 管理员\n\n"
        "说明：默认「各自记录」——每人独立一条箭头，"
        "同一人可被多人抽中，关系图才会交错好看；"
        "不是一夫一妻独占。"
    )
    await plugin.api.qq.post_group_msg(group_id=event.group_id, text=text)


async def cmd_reset_records(plugin: "TodayWaifu", event) -> None:
    if not plugin.is_admin(str(event.sender.user_id)):
        await plugin.api.qq.post_group_msg(
            group_id=event.group_id, text="需要管理员权限"
        )
        return
    plugin.store.clear_today_records(str(event.group_id))
    await plugin.api.qq.post_group_msg(
        group_id=event.group_id, text="已清空本群今日抽取记录"
    )


async def cmd_reset_force_cd(plugin: "TodayWaifu", event) -> None:
    if not plugin.is_admin(str(event.sender.user_id)):
        await plugin.api.qq.post_group_msg(
            group_id=event.group_id, text="需要管理员权限"
        )
        return
    plugin.store.clear_force_cd(str(event.group_id))
    await plugin.api.qq.post_group_msg(
        group_id=event.group_id, text="已重置本群强娶冷却"
    )


async def cmd_reset_propose_cd(plugin: "TodayWaifu", event) -> None:
    if not plugin.is_admin(str(event.sender.user_id)):
        await plugin.api.qq.post_group_msg(
            group_id=event.group_id, text="需要管理员权限"
        )
        return
    plugin.store.clear_propose_cd(str(event.group_id))
    await plugin.api.qq.post_group_msg(
        group_id=event.group_id, text="已重置本群求婚冷却"
    )
