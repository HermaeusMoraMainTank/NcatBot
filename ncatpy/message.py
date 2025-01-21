# encoding: utf-8

from .api import BotAPI

class BaseMessage(BotAPI):
    __slots__ = ("self_id",  "time", "post_type")

    def __init__(self, message):
        super().__init__()
        self.self_id = message.get("self_id", None)
        self.time = message.get("time", None)
        self.post_type = message.get("post_type", None)

    def __repr__(self):
        return str({items: str(getattr(self, items)) for items in self.__slots__})
    
    class _Sender:
        def __init__(self, message):
            self.user_id = message.get("user_id", None)
            self.nickname = message.get("nickname", None)
            self.card = message.get("card", None)

        def __repr__(self):
            return str(self.__dict__)
        
    

class GroupMessage(BaseMessage):
    __slots__ = ("group_id", "user_id", "message_type", "sub_type", "raw_message", "font", "sender", "message_id", "message_seq", "real_id", "message", "message_format")

    def __init__(self, message):
        super().__init__(message)
        self.user_id = message.get("user_id", None)
        self.group_id = message.get("group_id", None)
        self.message_type = message.get("message_type", None)
        self.sub_type = message.get("sub_type", None)
        self.raw_message = message.get("raw_message", None)
        self.font = message.get("font", None)
        self.sender = self._Sender(message.get("sender", {}))
        self.message_id = message.get("message_id", None)
        self.message_seq = message.get("message_seq", None)
        self.real_id = message.get("real_id", None)
        self.message = message.get("message", [])
        self.message_format = message.get("message_format", None)

    def __repr__(self):
        return str({items: str(getattr(self, items)) for items in self.__slots__})

    async def reply(self, reply=False):
        if reply:
            self.add_reply(self.message_id)
        return await self.send_group_msg(self.group_id, clear_message=True)


class PrivateMessage(BaseMessage):
    __slots__ = ("message_id", "user_id", "message_seq", "real_id", "message_type", "sender", "raw_message", "font", "sub_type", "message", "message_format", "target_id")

    def __init__(self, message):
        super().__init__(message)
        self.user_id = message.get("user_id", None)
        self.message_id = message.get("message_id", None)
        self.message_seq = message.get("message_seq", None)
        self.real_id = message.get("real_id", None)
        self.message_type = message.get("message_type", None)
        self.sender = self._Sender(message.get("sender", {}))
        self.raw_message = message.get("raw_message", None)
        self.font = message.get("font", None)
        self.sub_type = message.get("sub_type", None)
        self.message = message.get("message", [])
        self.message_format = message.get("message_format", None)
        self.target_id = message.get("target_id", None)

    def __repr__(self):
        return str({items: str(getattr(self, items)) for items in self.__slots__})

    async def reply(self, reply=False):
        if reply:
            self.add_reply(self.message_id)
        return await self.send_private_msg(self.user_id, clear_message=True)


class NoticeMessage(BaseMessage):
    __slots__=("self_id","notice_type","sub_type","target_id","user_id","group_id","raw_info")
    def __init__(self, message):
        super().__init__(message)
        self.user_id=message.get("user_id",None)
        self.self_id=message.get("self_id",None)
        self.notice_type=message.get("notice_type",None)
        self.sub_type=message.get("sub_type",None)
        self.target_id=message.get("target_id",None)
        self.group_id=message.get("group_id",None)
        self.raw_info=message.get("raw_info",None)
    def __repr__(self):
        return str({items: str(getattr(self, items)) for items in self.__slots__})
    async def reply(self, reply=False):
        if(self.group_id!=None):
            return await self.send_group_msg(self.group_id, clear_message=True)
        else:
            return await self.send_private_msg(self.user_id,clear_message=True)
    
class RequestMessage(BaseMessage):
    def __repr__(self):
        return str(self.__dict__)
