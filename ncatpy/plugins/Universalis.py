import logging
from dataclasses import dataclass
from ncatpy.message import GroupMessage
import json
import os
import requests
# 定义数据类
@dataclass
class Item:
    Data:dict


# 日志配置
log = logging.getLogger(__name__)
class Universalis:
    def __init__(self):
        try:
            with open(os.path.join(os.getcwd(), "data/json/Item.json"), "r", encoding="utf-8") as f:
                data = json.load(f)
                self.item=Item(data)
                # print(self.item.Data["data"]["金币"])
        except json.JSONDecodeError as e:
            print("JSON 解码错误:", e)
            exit(1)
        except FileNotFoundError:
            print(os.path.join(os.getcwd(), "data/json/Item.json"))
            print("文件未找到:",)
            exit(1)
        except Exception as e:
            print("发生错误:", e)
            exit(1)
        self.word={
            "猫":"猫小胖",
            "狗":"豆豆柴",
            "鸟":"陆行鸟",
            "猪":"莫古力"
        }
        self.Apiurl = "https://universalis.app/api/v2/"
        self.getdata("猫","5597")
    async def handle_Universalis(self, input: GroupMessage):
        """
        处理物价查询
        """
        key=input.raw_message.split()
        if len(key)==3 and key[0]=="物品查询" and key[1] in self.word and self.getitemid(key[2])>0:
            data=self.getdata(key[1],self.getitemid(key[2]))
            if (len(data)<1):
                await input.add_text("没有找到数据喵").reply()
                return
            if(len(data["listings"])<1):
                await input.add_text("没有数据喵").reply()
                return
            qqmes=key[2]+"\n"
            for v in data["listings"]:
                s= "{} 数量{} 价格{}\n".format(v["worldName"], v["quantity"], self.toc(v["pricePerUnit"]))
                qqmes+=str(s)
            await input.add_text(qqmes).reply()
        else :
            log.info("格式错误",key)    
    def getitemid(self,str):
        if(str in self.item.Data["data"]):
            log.debug(self.item.Data["data"][str])
            return self.item.Data["data"][str]
        else:
            return 0
    def getdata(self,word,id):
        geturl=self.Apiurl+self.word[word]+"/"+str(id)+"?"+"listings=5"
        log.debug(geturl)
        res=requests.get(geturl)
        print(geturl)
        if res.status_code==200:
            data=res.json()
            return data
        else:
            return {}      
    def toc(self,num):
        str_num = str(num)
        if len(str_num) < 5:
            return str_num
        if len(str_num) < 9:
            a = str_num[:-4]
            b = str_num[-4:]
            return a + "万" + b
        a = str_num[:-8]
        b = str_num[-8:]
        c = b[:-4]
        d = b[-4:]
        return a + "亿" + c + "万" + d 