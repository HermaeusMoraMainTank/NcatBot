import hashlib
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from typing import List, Optional
import json
import httpx
import os

from ncatpy.message import GroupMessage

@dataclass_json
@dataclass
class ParamsType:
    min_images: int
    max_images: int
    min_texts: int
    max_texts: int
    default_texts: List[str]
    args_type: Optional[None]  # 如果 args_type 可能会有其他类型，可以适当修改
@dataclass_json
@dataclass
class DataStructure:
    key: str
    params_type: ParamsType
    keywords: List[str]
    shortcuts: List[str]
    tags: List[str]
    date_created: str  # 如果需要日期对象，可以使用 datetime.date
    date_modified: str  # 同上
class Pet:
    def __init__(self):
        pass
        self.baseurl = "http://127.0.0.1:2233"
        self.keylist = []
        self.keywordslist: dict[str,DataStructure]={}
        self.client=httpx.Client(base_url=self.baseurl)
        self.getkeylist()
        self.getkeyinfo()
    def getkeylist(self):
        res =self. client.get("/memes/keys")
        self.keylist = json.loads(res.text)
    async def handle_Pet(self,input: GroupMessage):
        if input.message[0]["type"]=="text":
            com=input.message[0]["data"]["text"].strip()  #去除空格
            coms=str(com).split(" ")  #
            if coms[0] in self.keywordslist:
                print(self.keywordslist[coms[0]])
                if self.keywordslist[coms[0]].params_type.max_images==1 and self.keywordslist[coms[0]].params_type.max_texts==0:#这里只有一张图片
                    if len(input.message)==1: #传自己的头像
                        me=self.getqqimg(input.user_id)
                        files=[("images",me)]
                        path=self.getingpath(self.keywordslist[coms[0]].key,data=None,flie=files)
                        input.add_image(path)
                        print(path)
                        await input.reply()
                        return
                    if len(input.message)==2: #传@别人的头像
                        if input.message[1]["type"] != "at":
                            return
                        Target=self.getqqimg(input.messages[1]["data"]["qq"])
                        files=[("images",Target)]
                        path=self.getingpath(self.keywordslist[coms[0]].key,data=None,flie=files)
                        input.add_image(path)
                        await input.reply()
                        return
                        pass
                    pass
                if self.keywordslist[coms[0]].params_type.max_images==2 and self.keywordslist[coms[0]].params_type.max_texts==0:  #传2张图片的
                    if input.message[1]["type"]!="at":
                        return
                    me=self.getqqimg(input.user_id)
                    Target=self.getqqimg(input.messages[1]["data"]["qq"])
                    pass
    def getkeyinfo(self):
        for item in self.keylist:
            res = self.client.get(f"/memes/{item}/info")
            data=DataStructure.from_json(res.text)
            for item in data.keywords:
                if data.params_type.args_type!=None or data.params_type.max_images>2 or data.params_type.max_texts>3:
                    continue
                self.keywordslist[item] = data
    def getqqimg(self,qq :[int,str])->bytes:
        res = httpx.get("http://q.qlogo.cn/headimg_dl?dst_uin="+str(qq)+"&spec=640&img_type=jpg")
        return res.content
    def getingpath(self,url,data,flie):
        res = self.client.post("/memes/"+url+"/",data=data,files=flie,timeout=1000)
        if res.status_code == 200:
            md5_hash = hashlib.md5()
            md5_hash.update(res.text.encode('utf-8'))
            with open(os.getcwd()+md5_hash.hexdigest()+".jpg", "wb") as f:
                f.write(res.content)
                return os.getcwd()+md5_hash.hexdigest()+".jpg"
        return ""