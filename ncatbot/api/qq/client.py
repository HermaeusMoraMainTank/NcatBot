"""QQAPIClient — QQ 平台对外 API 门面

通过 ``api.qq`` 访问，组合四个功能分组 + sugar 方法。

调用示例::

    await api.qq.messaging.send_group_msg(group_id, message)
    await api.qq.manage.set_group_ban(group_id, user_id, 600)
    await api.qq.query.get_group_member_list(group_id)
    await api.qq.file.upload_group_file(group_id, file, name)
    await api.qq.send_group_text(group_id, "hello")  # sugar
"""

from __future__ import annotations

from .interface import IQQAPIClient
from .proxy import QQLoggingProxy
from .sugar import QQMessageSugarMixin
from .messaging import QQMessaging
from .manage import QQManage
from .query import QQQuery
from .file import QQFile


class QQAPIClient(QQMessageSugarMixin):
    """QQ 平台 API 客户端

    组合四个功能分组，继承 sugar 方法。
    """

    def __init__(self, raw_api: IQQAPIClient) -> None:
        self._api = QQLoggingProxy.wrap(raw_api)
        self.messaging = QQMessaging(self._api)
        self.manage = QQManage(self._api)
        self.query = QQQuery(self._api)
        self.file = QQFile(self._api)
