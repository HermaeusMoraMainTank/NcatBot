"""
RBAC 权限混入类

代理 RBACService 的高频接口，简化插件中的权限管理。
"""

from typing import Optional, TYPE_CHECKING

from ncatbot.utils import get_log

if TYPE_CHECKING:
    from ncatbot.service import ServiceManager, RBACService

LOG = get_log("RBACMixin")


class RBACMixin:
    """
    RBAC 权限混入类

    使用示例::

        class MyPlugin(NcatBotPlugin):
            async def on_load(self):
                self.add_permission("my_plugin.admin")
                self.add_role("my_plugin_admin")
                self.assign_role_permission("my_plugin_admin", "my_plugin.admin")

            async def handle(self, event):
                if self.check_permission(str(event.user_id), "my_plugin.admin"):
                    ...
    """

    services: "ServiceManager"

    @property
    def rbac(self) -> Optional["RBACService"]:
        """获取 RBAC 服务实例"""
        if not hasattr(self, "services"):
            return None
        svc = self.services.get("rbac")
        return svc  # type: ignore[return-value]

    def check_permission(self, user: str, permission: str) -> bool:
        """检查用户是否拥有指定权限。

        Args:
            user: 用户标识
            permission: 权限路径

        Returns:
            是否拥有权限，RBAC 服务不可用时返回 False
        """
        service = self.rbac
        if service is None:
            LOG.warning("RBAC 服务不可用")
            return False
        return service.check(user, permission)

    def add_permission(self, path: str) -> None:
        """注册权限路径。

        Args:
            path: 权限路径，如 "plugin_name.feature"
        """
        service = self.rbac
        if service is not None:
            service.add_permission(path)

    def remove_permission(self, path: str) -> None:
        """移除权限路径。"""
        service = self.rbac
        if service is not None:
            service.remove_permission(path)

    def add_role(self, role: str, exist_ok: bool = True) -> None:
        """创建角色。

        Args:
            role: 角色名称
            exist_ok: 角色已存在时是否忽略
        """
        service = self.rbac
        if service is not None:
            service.add_role(role, exist_ok=exist_ok)

    def user_has_role(self, user: str, role: str) -> bool:
        """检查用户是否拥有指定角色。"""
        service = self.rbac
        if service is None:
            return False
        return service.user_has_role(user, role)
