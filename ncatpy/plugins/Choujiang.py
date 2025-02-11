from ncatpy.message import GroupMessage
import random
class Choujiang:
    def __init__(self):
        self.map={}
        pass
    async def handle_choujiang(self,input:GroupMessage):
        if input.group_id not in self.map:
            self._initmap(input.group_id)
        print(len(self.map[input.group_id]["list"]))
        i=random.randint(0,len(self.map[input.group_id]["list"])-1)
        popdata=self.map[input.group_id]["list"].pop(i)
        if popdata==self.map[input.group_id]["prize"]:
            await input.set_group_ban(input.group_id,input.user_id,120)
            input.add_reply(input.message_id)
            #intput.add_image("")
            input.add_text("恭喜你中奖了,奖励你一张肥肥图\n")
            input.add_text(f"当前概率 {len(self.map[input.group_id]['list']) / 200 * 100}%")

            await input.reply(input.group_id)
            self._initmap(input.group_id)  #重新初始化
            pass
            #禁言初始化
        pass
    def _initmap(self,id):
        l=[]
        for i in range(200):
            l.append(i)
        self.map[id]={"prize":random.randint(0,199),"list":l}