import random
from ncatbot.core import NoticeEvent, At, Text, MessageChain
from ncatbot.plugin_system import NcatBotPlugin, on_notice
from ncatbot.utils.logger import get_log

_log = get_log()


class Poke(NcatBotPlugin):
    name = "Poke"
    version = "1.0"
    author = "NcatBot"
    description = "监听戳一戳事件并随机回复，30%概率戳回去"

    # 使用说明
    usage_instructions = """戳一戳功能：
1. 自动监听戳一戳事件
2. 随机回复预设的回复内容
3. 30%概率戳回去
4. 支持替换用户名变量

功能已启用，会在被戳时自动回复
"""

    # 戳一戳回复内容数组
    nudge_responses = [
        "不许戳！",
        "再这样我要叫警察叔叔啦",
        "讨厌没有边界感的人类",
        "戳牛魔戳",
        "再戳我就要戳回去啦",
        "呜......戳坏了",
        "放手啦，不给戳QAQ",
        "(。´・ω・)ん?",
        "请不要戳 >_<",
        "这里是蓝晴(っ●ω●)っ",
        "啾咪~",
        "userName有什么吩咐吗",
        "ん？",
        "蓝晴不在",
        "厨房有煤气灶自己拧着玩",
        "操作太快了，等会再试试吧",
    ]

    async def on_load(self):
        """异步加载插件"""
        _log.info(f"开始加载 {self.name} 插件 v{self.version}")

        _log.info(f"{self.name} 插件加载完成")

    @on_notice
    async def handle_nudge_notice(self, input: NoticeEvent) -> None:
        """处理戳一戳通知"""

        try:
            # 检查是否是戳一戳事件
            if input.notice_type != "notify" or input.sub_type != "poke":
                return

            # 检查是否戳的是机器人自己
            if str(input.target_id) != "3555202423":
                return

            # 记录事件信息
            _log.info(f"[Poke] 检测到戳一戳事件: {input}")

            # 获取戳一戳的用户ID
            user_id = input.user_id
            if not user_id:
                _log.warning("[Poke] 无法获取用户ID")
                return

            # 获取群ID（如果是群聊戳一戳）
            group_id = input.group_id

            # 获取用户昵称
            try:
                if group_id:
                    # 群聊戳一戳，获取群成员信息
                    member_info = await self.api.get_group_member_info(
                        group_id=int(group_id), user_id=int(user_id)
                    )
                    user_nickname = (
                        member_info.nickname if member_info else f"用户{user_id}"
                    )
                else:
                    # 私聊戳一戳，获取好友信息
                    friend_info = await self.api.get_friend_info(user_id=int(user_id))
                    user_nickname = (
                        friend_info.nickname if friend_info else f"用户{user_id}"
                    )
            except Exception as e:
                _log.warning(f"[Poke] 获取用户信息失败: {e}")
                user_nickname = f"用户{user_id}"

            # 随机选择回复内容
            selected_response = random.choice(self.nudge_responses)
            # 替换用户名变量
            response_text = selected_response.replace("userName", user_nickname)

            # 30%概率戳回去
            should_poke_back = random.random() < 0.3

            if should_poke_back:
                try:
                    # 戳回去
                    if group_id:
                        # 群聊戳一戳
                        await self.api.send_poke(
                            user_id=int(user_id), group_id=int(group_id)
                        )
                        _log.info(f"[Poke] 在群 {group_id} 中戳回去给用户 {user_id}")
                    else:
                        # 私聊戳一戳
                        await self.api.send_poke(user_id=int(user_id))
                        _log.info(f"[Poke] 私聊戳回去给用户 {user_id}")
                except Exception as e:
                    _log.error(f"[Poke] 戳回去失败: {e}")

            # 发送回复消息
            if group_id:
                # 群聊回复
                message_chain = MessageChain(
                    [At(int(user_id)), Text(f" {response_text}")]
                )
                await self.api.post_group_msg(group_id=int(group_id), rtf=message_chain)
                _log.info(f"[Poke] 在群 {group_id} 中回复: {response_text}")
            else:
                # 私聊回复
                await self.api.post_private_msg(
                    user_id=int(user_id), text=response_text
                )
                _log.info(f"[Poke] 私聊回复: {response_text}")

        except Exception as e:
            _log.error(f"[Poke] 处理戳一戳通知失败: {e}")
