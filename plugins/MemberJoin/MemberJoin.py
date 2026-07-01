from ncatbot.event.qq import NoticeEvent
from ncatbot.types import At, PlainText, MessageArray as MessageChain
from ncatbot.plugin import NcatBotPlugin
from ncatbot.core import registrar
from ncatbot.utils import get_log

_log = get_log()


class MemberJoin(NcatBotPlugin):
    name = "MemberJoin"
    version = "1.0"
    author = "NcatBot"
    description = "监听群成员加入事件并发送欢迎消息"

    # 使用说明
    usage_instructions = """成员加入欢迎功能：
1. 自动监听群成员加入事件
2. 发送欢迎消息
3. 支持邀请人信息显示

功能已启用，会在群成员加入时自动发送欢迎消息
"""

    async def on_load(self):
        """异步加载插件"""
        _log.info(f"开始加载 {self.name} 插件 v{self.version}")

        _log.info(f"{self.name} 插件加载完成")

    @registrar.on_notice()
    async def handle_member_join_notice(self, input: NoticeEvent) -> None:
        """处理群成员加入通知"""
        try:
            # 检查是否是群成员加入事件
            if input.notice_type != "group_increase":
                return

            # 记录事件信息
            _log.info(f"[MemberJoin] 检测到群成员加入事件: {input}")

            # 获取成员ID
            member_id = input.user_id
            if not member_id:
                _log.warning("[MemberJoin] 无法获取成员ID")
                return

            # 获取成员昵称（通过API获取）
            try:
                member_info = await self.api.qq.query.get_group_member_info(
                    group_id=int(input.group_id), user_id=int(member_id)
                )
                member_nickname = (
                    member_info.nickname if member_info else f"用户{member_id}"
                )
            except Exception as e:
                _log.warning(f"[MemberJoin] 获取成员信息失败: {e}")
                member_nickname = f"用户{member_id}"

            # 构建欢迎消息
            welcome_message = f"欢迎 {member_nickname} 小可爱"

            # 判断加入类型并添加邀请人信息
            additional_info = ""
            if input.sub_type == "invite" and input.operator_id:
                # 被邀请加入，获取邀请人信息
                try:
                    invitor_info = await self.api.qq.query.get_group_member_info(
                        group_id=int(input.group_id), user_id=int(input.operator_id)
                    )
                    invitor_nickname = (
                        invitor_info.nickname
                        if invitor_info
                        else f"用户{input.operator_id}"
                    )
                    additional_info = (
                        f"由 {invitor_nickname}（{input.operator_id}）邀请"
                    )
                except Exception as e:
                    _log.warning(f"[MemberJoin] 获取邀请人信息失败: {e}")
                    additional_info = f"由用户{input.operator_id}邀请"

            # 构建完整的欢迎消息
            if additional_info:
                welcome_message += f" {additional_info}"
            welcome_message += " 进群！"

            # 发送欢迎消息
            message_chain = MessageChain(
                [At(user_id=int(member_id)), PlainText(text=f" {welcome_message}")]
            )

            await self.api.qq.post_group_msg(
                group_id=int(input.group_id), rtf=message_chain
            )

            # 记录日志
            _log.info(f"[MemberJoin] 发送欢迎消息: {member_nickname} ({member_id})")

        except Exception as e:
            _log.error(f"[MemberJoin] 处理群成员加入通知失败: {e}")