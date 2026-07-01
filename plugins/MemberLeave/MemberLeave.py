from ncatbot.event.qq import NoticeEvent
from ncatbot.types import PlainText, MessageArray as MessageChain
from ncatbot.plugin import NcatBotPlugin
from ncatbot.core import registrar
from ncatbot.utils import get_log

_log = get_log()


class MemberLeave(NcatBotPlugin):
    name = "MemberLeave"
    version = "1.0"
    author = "NcatBot"
    description = "监听群成员离开事件并发送离开消息"

    # 使用说明
    usage_instructions = """成员离开通知功能：
1. 自动监听群成员离开事件
2. 发送离开消息
3. 支持踢人操作者信息显示

功能已启用，会在群成员离开时自动发送离开消息
"""

    async def on_load(self):
        """异步加载插件"""
        _log.info(f"开始加载 {self.name} 插件 v{self.version}")

        _log.info(f"{self.name} 插件加载完成")

    @registrar.on_notice()
    async def handle_member_leave_notice(self, input: NoticeEvent) -> None:
        """处理群成员离开通知"""
        try:
            # 检查是否是群成员离开事件
            if input.notice_type != "group_decrease":
                return

            # 记录事件信息
            _log.info(f"[MemberLeave] 检测到群成员离开事件: {input}")

            # 获取成员ID
            member_id = input.user_id
            if not member_id:
                _log.warning("[MemberLeave] 无法获取成员ID")
                return

            # 直接使用用户ID，不尝试获取昵称（因为成员已离开群）

            # 判断离开事件的类型
            leave_message = ""
            is_kick = input.sub_type == "kick"

            if is_kick:
                # 被踢出群，获取操作者信息
                if input.operator_id:
                    try:
                        operator_info = await self.api.qq.query.get_group_member_info(
                            group_id=int(input.group_id), user_id=int(input.operator_id)
                        )
                        operator_nickname = (
                            operator_info.nickname
                            if operator_info
                            else f"用户{input.operator_id}"
                        )
                        leave_message = (
                            f"被 {operator_nickname}（{input.operator_id}）踢出群"
                        )
                    except Exception as e:
                        _log.warning(f"[MemberLeave] 获取操作者信息失败: {e}")
                        leave_message = f"被用户{input.operator_id}踢出群"
                else:
                    leave_message = "被踢出群"
            elif input.sub_type == "leave":
                # 主动退群
                leave_message = "退群"
            else:
                # 其他类型的离开
                leave_message = "离开本群"

            # 构建完整的离开消息
            full_message = f"{member_id}已{leave_message}"

            # 发送离开消息
            message_chain = MessageChain([PlainText(text=full_message)])

            await self.api.qq.post_group_msg(
                group_id=int(input.group_id), rtf=message_chain
            )

            # 记录日志
            _log.info(f"[MemberLeave] 发送离开消息: {member_id}")

        except Exception as e:
            _log.error(f"[MemberLeave] 处理群成员离开通知失败: {e}")
