# 字面常量
from enum import Enum

REQUEST_SUCCESS = "ok"

OFFICIAL_GROUP_MESSAGE_EVENT = "ncatbot.group_message_event"
OFFICIAL_PRIVATE_MESSAGE_EVENT = "ncatbot.private_message_event"
OFFICIAL_MESSAGE_SENT_EVENT = "ncatbot.message_sent_event"
OFFICIAL_REQUEST_EVENT = "ncatbot.request_event"
OFFICIAL_NOTICE_EVENT = "ncatbot.notice_event"
OFFICIAL_STARTUP_EVENT = "ncatbot.startup_event"
OFFICIAL_SHUTDOWN_EVENT = "ncatbot.shutdown_event"
OFFICIAL_HEARTBEAT_EVENT = "ncatbot.heartbeat_event"

PLUGIN_BROKEN_MARK = "插件已损坏"


class PermissionGroup(Enum):
    ROOT = "root"
    ADMIN = "admin"
    USER = "user"


class DefaultPermission(Enum):
    ACCESS = "access"
    SETADMIN = "setadmin"


EVENT_QUEUE_MAX_SIZE = 64
PLUGINS_DIR = "plugins"
META_CONFIG_PATH = None
PERSISTENT_DIR = "data"

__all__ = [
    "REQUEST_SUCCESS",
    "OFFICIAL_GROUP_MESSAGE_EVENT",
    "OFFICIAL_PRIVATE_MESSAGE_EVENT",
    "OFFICIAL_MESSAGE_SENT_EVENT",
    "OFFICIAL_REQUEST_EVENT",
    "OFFICIAL_NOTICE_EVENT",
    "OFFICIAL_STARTUP_EVENT",
    "OFFICIAL_SHUTDOWN_EVENT",
    "OFFICIAL_HEARTBEAT_EVENT",
    "PLUGIN_BROKEN_MARK",
    "PermissionGroup",
    "DefaultPermission",
    "EVENT_QUEUE_MAX_SIZE",
    "PLUGINS_DIR",
    "META_CONFIG_PATH",
    "PERSISTENT_DIR",
]
