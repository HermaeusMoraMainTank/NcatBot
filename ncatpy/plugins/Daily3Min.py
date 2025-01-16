from ncatpy.message import GroupMessage


class Daily3Min:
    def __init__(self):
        pass

    async def handle_daily3min(self, input: GroupMessage):
        if input.raw_message in ['每天3分钟', '每天三分钟', '每日3分钟', '每日三分钟', '每天60秒', '每天六十秒',
                                 '每日60秒', '每日六十秒', '每天1分钟', '每天一分钟', '每日1分钟', '每日一分钟']:
            await input.add_image('https://api.03c3.cn/api/zb').reply()
