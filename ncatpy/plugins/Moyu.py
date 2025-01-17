from ncatpy.message import GroupMessage


class Moyu:
    def __init__(self):
        pass

    async def handle_moyu(self, input: GroupMessage):
        if input.raw_message in ['摸鱼', 'moyu']:
            return await input.add_image("https://api.vvhan.com/api/moyu").reply()
