import random
from ncatpy.message import NoticeMessage


class NudgeEvent:
    def __init__(self):
        self.nudge = [
            "喂(#`O′)，戳我干什么",
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
            "操作太快了，等会再试试吧"
        ]

    async def handle_nudge(self, input: NoticeMessage):
        if (input.target_id ==input.self_id) and input.sub_type == "poke":  #替换为自己
            userinfo = await input.get_group_member_info(input.group_id, input.user_id)
            if userinfo["data"]["card"] != "":
                username = userinfo["data"]["card"]
            else:
                username = userinfo["data"]["nickname"]
            input.add_at(input.user_id)
            i = random.randint(0, len(self.nudge))
            input.add_reply(input.message_id)
            await input.add_text(self.nudge[i].replace("userName", username)).reply()
