from ncatpy.message import GroupMessage


class SendLike:
    def __init__(self):
        pass

    async def handle_send_like(self, input: GroupMessage):
        if input.raw_message == "赞我":
            await input.send_like(input.user_id, 10)
            await input.add_at(input.user_id).add_text("\n给你赞了10下哦，记得回我~ (如赞失败请添加好友)").add_image(
                "https://api.xingzhige.com/API/dingqiu/?qq=" + str(input.user_id)).reply()
            return
