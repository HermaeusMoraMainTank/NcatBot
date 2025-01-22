import logging
from ncatpy.message import GroupMessage
from ncatpy.message import NoticeMessage
import time
import threading
log = logging.getLogger(__name__)
class Group_recall:
    def __init__(self):
        self.msgMap={}  #初始化字典
        self.lock = threading.Lock()  # 线程锁
        self.expiration_time=3600*24  #一天
        self.cleanup_thread = threading.Thread(target=self.cleanTask, daemon=True)
        self.cleanup_thread.start()  #启动定时线程
        pass
    async def cleanTask(self):  #定时任务
        while True:
            log.debug(f"消息储存量{len(self.msgMap)}")  #用于检测
            time.sleep(60)  #一分钟执行一次
            self.clean()
            pass
    async def clean(self):
        current_time = time.time()
        self.lock.acquire()  #对map访问的时候设置锁
        try:
            for k,v in self.msgMap.items():
                if(current_time-v[1]>self.expiration_time):
                    del self.msgMap[k]
        finally:
            self.lock.release()
    async def handle_notice(self,input: NoticeMessage):
        if(input.notice_type=="group_recall"):
            if(self.msgMap[input.message_id]):
                msg=self.msgMap[input.message_id][0]
                msg.add_text("↑本小可爱帮你复现了喵↑")
                await msg.reply()
                msg.messages=msg.messages+msg.message
                await msg.reply()
                self.lock.acquire()
                try:
                    del(self.msgMap[input.message_id])
                finally:
                    self.lock.release()
    async def handle_Group(self,input:GroupMessage):
        msgtime=time.time()  #消息时间 (程序内部时间 就用time表示)
        msgid=input.message_id
        msg=input
        log.info("添加消息")
        self.lock.acquire()
        try:
            self.msgMap[msgid]=[msg,msgtime]
        finally:
            self.lock.release()