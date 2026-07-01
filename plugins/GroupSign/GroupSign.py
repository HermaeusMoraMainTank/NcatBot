import asyncio
import random

from ncatbot.event.qq import GroupMessageEvent as GroupMessage
from ncatbot.plugin import NcatBotPlugin
from ncatbot.core import registrar
from ncatbot.utils import get_log

from common.constants.HMMT import HMMT

_log = get_log("GroupSign")

_HELP_TEXT = """📋 群打卡插件

每天 0 点自动在指定群执行打卡。

可用命令：
• 群打卡 — 显示此帮助
• 群打卡 列表 — 查看打卡群列表
• 群打卡 本群 — 将当前群加入打卡列表
• 群打卡 添加 <群号> — 添加指定群
• 群打卡 移除 <群号> — 移除指定群
• 群打卡 清空 — 清空打卡列表
• 群打卡 立即 — 立刻执行一次打卡（测试用）

以上管理命令需要管理员权限。"""


class GroupSign(NcatBotPlugin):
    name = "GroupSign"
    version = "2.0.0"
    author = "物起"
    description = "自动在指定群进行每日打卡"

    async def on_load(self) -> None:
        self.init_defaults({"group_list": []})
        self.add_permission("groupsign.manage")
        self.add_scheduled_task(
            "daily_group_sign", "00:00", callback=self._do_group_sign
        )
        _log.info("GroupSign 每日 0 点打卡任务已注册")

    def _get_group_list(self) -> list[str]:
        raw = self.get_config("group_list", [])
        if not isinstance(raw, list):
            return []
        return [str(g).strip() for g in raw if str(g).strip()]

    def _set_group_list(self, group_list: list[str]) -> None:
        self.set_config("group_list", group_list)

    async def _require_admin(self, event: GroupMessage) -> bool:
        uid = str(event.sender.user_id)
        if uid == HMMT.HMMT_ID or self.check_permission(uid, "groupsign.manage"):
            return True
        await self.api.qq.post_group_msg(
            group_id=event.group_id,
            text="只有管理员才能使用群打卡管理命令",
            reply=event.message_id,
        )
        return False

    async def _reply(self, event: GroupMessage, text: str) -> None:
        await self.api.qq.post_group_msg(
            group_id=event.group_id,
            text=text,
            reply=event.message_id,
        )

    async def _do_group_sign(self) -> None:
        group_list = self._get_group_list()
        if not group_list:
            _log.info("打卡列表为空，跳过")
            return

        delay = random.uniform(0.1, 0.8)
        _log.info(f"开始执行群打卡任务，随机延迟 {delay:.2f} 秒")
        await asyncio.sleep(delay)

        for group_id in group_list:
            try:
                _log.info(f"在群 {group_id} 进行打卡")
                await self.api.qq.manage.set_group_sign(group_id)
            except Exception as e:
                _log.error(f"群 {group_id} 打卡失败: {e}")

    async def _handle_add(self, event: GroupMessage, group_id: str) -> None:
        if not await self._require_admin(event):
            return

        group_id = group_id.strip()
        if not group_id.isdigit():
            await self._reply(event, "群号格式不正确，请输入纯数字群号")
            return

        group_list = self._get_group_list()
        if group_id in group_list:
            await self._reply(event, f"群 {group_id} 已在打卡列表中")
            return

        group_list.append(group_id)
        self._set_group_list(group_list)
        _log.info(f"已添加群 {group_id} 到打卡列表")
        await self._reply(
            event, f"已添加群 {group_id}\n当前打卡列表：{', '.join(group_list)}"
        )

    async def _handle_remove(self, event: GroupMessage, group_id: str) -> None:
        if not await self._require_admin(event):
            return

        group_id = group_id.strip()
        group_list = self._get_group_list()
        if group_id not in group_list:
            await self._reply(event, f"群 {group_id} 不在打卡列表中")
            return

        group_list.remove(group_id)
        self._set_group_list(group_list)
        _log.info(f"已从打卡列表中移除群 {group_id}")
        await self._reply(
            event,
            f"已移除群 {group_id}\n当前打卡列表：{', '.join(group_list) or '（空）'}",
        )

    async def _handle_list(self, event: GroupMessage) -> None:
        if not await self._require_admin(event):
            return

        group_list = self._get_group_list()
        if not group_list:
            await self._reply(event, "当前打卡列表为空")
            return
        await self._reply(event, f"当前打卡列表：\n{', '.join(group_list)}")

    async def _handle_clear(self, event: GroupMessage) -> None:
        if not await self._require_admin(event):
            return

        self._set_group_list([])
        _log.info("已清空打卡列表")
        await self._reply(event, "已清空打卡列表")

    async def _handle_sign_now(self, event: GroupMessage) -> None:
        if not await self._require_admin(event):
            return

        group_list = self._get_group_list()
        if not group_list:
            await self._reply(event, "打卡列表为空，请先添加群")
            return

        await self._reply(event, f"开始手动打卡，共 {len(group_list)} 个群…")
        await self._do_group_sign()
        await self._reply(event, "手动打卡已完成")

    @registrar.qq.on_group_message()
    async def handle_group_sign(self, input: GroupMessage) -> None:
        message = input.raw_message.strip()
        if not message.startswith("群打卡"):
            return

        cmd = message[3:].strip()  # 去掉「群打卡」

        if not cmd or cmd == "帮助":
            await self._reply(input, _HELP_TEXT)
        elif cmd in ("列表", "查看"):
            await self._handle_list(input)
        elif cmd == "本群":
            await self._handle_add(input, str(input.group_id))
        elif cmd == "清空":
            await self._handle_clear(input)
        elif cmd == "立即":
            await self._handle_sign_now(input)
        elif cmd.startswith("添加"):
            group_id = cmd[2:].strip()
            if not group_id:
                await self._reply(input, "用法：群打卡 添加 <群号>")
                return
            await self._handle_add(input, group_id)
        elif cmd.startswith("移除"):
            group_id = cmd[2:].strip()
            if not group_id:
                await self._reply(input, "用法：群打卡 移除 <群号>")
                return
            await self._handle_remove(input, group_id)
        else:
            await self._reply(input, "未知命令，发送「群打卡」查看帮助")
