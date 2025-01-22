from ncatpy.message import GroupMessage


class Meme:
    def __init__(self):
        pass

    async def handle_meme(self, input: GroupMessage):
        if input.raw_message.startswith('meme'):
            return await input.add_text("抱 抓 拍 顶 咬 指 笑 嗨 甩 打").reply()
        action_urls = {
            '抱': 'https://api.xingzhige.com/API/baororo/?qq=',
            '抓': 'https://api.xingzhige.com/API/grab/?qq=',
            '拍': 'https://api.xingzhige.com/API/paigua/?qq=',
            '顶': 'https://api.xingzhige.com/API/dingqiu/?qq=',
            '咬': 'https://api.xingzhige.com/API/bite/?qq=',
            '指': 'https://api.xingzhige.com/API/Lookatthis/?qq=',
            '笑': 'https://api.xingzhige.com/API/LaughTogether/?qq=',
            '嗨': 'https://api.xingzhige.com/API/FortuneCat/?qq=',
            '甩': 'https://api.xingzhige.com/API/DanceChickenLeg/?qq=',
            '打': 'https://api.xingzhige.com/API/pound/?qq=',
            '爬':'https://api.xingzhige.com/API/pa/?qq='
        }

        for action, url in action_urls.items():
            if input.raw_message.startswith(action):
                for isAt in input.message:
                    if isAt.get('type') == 'at':
                        return await input.add_image(url + str(isAt.get('data').get('qq'))).reply()
                return await input.add_image(url + str(input.sender.user_id)).reply()