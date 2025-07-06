# coding: utf-8

from typing import List, Optional, Callable
import asyncio

class nsfwdb:
    """NSFW配置管理类"""
    
    def __init__(self):
        self.data = {
            "global_config": {},
            "groups": {},
            "group_notifiers": {},
            "group_admins": {},
            "group_thresholds": {}
        }
        self._nsfwpy_type = 'd'
        self._threshold = 0.85
        self._config_dirty = False
        self._save_callback = None  # 保存回调函数
        self._refresh_callback = None  # 刷新回调函数
        self._save_task = None  # 延迟保存任务

    def init_data(self, plugin_data: Optional[dict]):
        """初始化插件数据"""
        if plugin_data is None:
            plugin_data = {}
        
        self.data.update(plugin_data)
        
        # 确保基础数据结构存在
        self.data.setdefault("global_config", {})
        self.data.setdefault("groups", {})
        self.data.setdefault("group_notifiers", {})
        self.data.setdefault("group_admins", {})
        self.data.setdefault("group_thresholds", {})
        
        self._nsfwpy_type = self.data["global_config"].get('nsfwpy_type', 'd')
        self._threshold = float(self.data["global_config"].get('threshold', 0.85))

    def set_callbacks(self, save_callback: Callable, refresh_callback: Callable):
        """设置保存和刷新回调函数"""
        self._save_callback = save_callback
        self._refresh_callback = refresh_callback

    def _mark_dirty_and_schedule_save(self, immediate: bool = False, delay: float = 2.0):
        """标记配置已修改并安排保存"""
        self._config_dirty = True
        
        if immediate and self._save_callback:
            self._save_callback()
            self._config_dirty = False
        elif self._save_callback:
            # 延迟保存
            if self._save_task:
                self._save_task.cancel()
            
            async def delayed_save():
                await asyncio.sleep(delay)
                if self._config_dirty and self._save_callback:
                    self._save_callback()
                    self._config_dirty = False
            
            self._save_task = asyncio.create_task(delayed_save())

    def _trigger_refresh(self):
        """触发配置刷新"""
        if self._refresh_callback:
            self._refresh_callback()

    def manual_save(self):
        """手动保存配置"""
        if self._config_dirty and self._save_callback:
            self._save_callback()
            self._config_dirty = False
            return True
        return False

    def manual_refresh(self):
        """手动刷新配置"""
        self._trigger_refresh()

    @property
    def nsfwpy_type(self) -> str:
        return self._nsfwpy_type
    
    @nsfwpy_type.setter
    def nsfwpy_type(self, value: str):
        self._nsfwpy_type = value
        self.data["global_config"]['nsfwpy_type'] = value
        self._mark_dirty_and_schedule_save(immediate=True)  # 重要配置立即保存
        self._trigger_refresh()  # 触发刷新以重置NSFW实例
    
    @property
    def threshold(self) -> float:
        return self._threshold
    
    @threshold.setter 
    def threshold(self, value: float):
        if 0 <= value <= 1:
            self._threshold = value
            self.data["global_config"]['threshold'] = str(value)
            self._mark_dirty_and_schedule_save(immediate=True)  # 重要配置立即保存
            self._trigger_refresh()
        else:
            raise ValueError("阈值必须在0到1之间")
    
    def get_group_threshold(self, group_id: int) -> float:
        """获取群组阈值，如果群组没有设置则返回全局阈值"""
        group_id_str = str(group_id)
        group_threshold = self.data["group_thresholds"].get(group_id_str)
        if group_threshold is not None:
            return float(group_threshold)
        return self._threshold
    
    def set_group_threshold(self, group_id: int, threshold: float):
        """设置群组阈值"""
        if 0 <= threshold <= 1:
            group_id_str = str(group_id)
            self.data["group_thresholds"][group_id_str] = str(threshold)
            self._mark_dirty_and_schedule_save()
        else:
            raise ValueError("阈值必须在0到1之间")
    
    def remove_group_threshold(self, group_id: int):
        """移除群组阈值设置，恢复使用全局阈值"""
        group_id_str = str(group_id)
        if group_id_str in self.data["group_thresholds"]:
            del self.data["group_thresholds"][group_id_str]
            self._mark_dirty_and_schedule_save()
    
    def has_group_threshold(self, group_id: int) -> bool:
        """检查群组是否设置了独立阈值"""
        return str(group_id) in self.data["group_thresholds"]
    
    def get_global_admins(self) -> List[int]:
        """获取全局管理员列表"""
        admin_ids = self.data["global_config"].get('admin_users', '')
        return [int(x) for x in admin_ids.split(',') if x]
    
    def add_global_admin(self, admin_id: int):
        """添加全局管理员"""
        admins = self.get_global_admins()
        if admin_id not in admins:
            admins.append(admin_id)
            self.data["global_config"]['admin_users'] = ','.join(map(str, admins))
            self._mark_dirty_and_schedule_save()

    def remove_global_admin(self, admin_id: int):
        """移除全局管理员"""
        admins = self.get_global_admins()
        if admin_id in admins:
            admins.remove(admin_id)
            self.data["global_config"]['admin_users'] = ','.join(map(str, admins))
            self._mark_dirty_and_schedule_save()
    
    def has_global_admins(self) -> bool:
        """检查是否存在全局管理员"""
        return bool(self.get_global_admins())
    
    def is_group_check_enabled(self, group_id: int) -> bool:
        """检查群聊是否启用NSFW检测"""
        return self.data["groups"].get(str(group_id), False)
    
    def update_group_settings(self, group_id: int, notifiers: List[int], check: bool):
        """更新群聊设置"""
        group_id_str = str(group_id)
        self.data["groups"][group_id_str] = check
        self.data["group_notifiers"][group_id_str] = notifiers
        self._mark_dirty_and_schedule_save()
    
    def get_group_notifiers(self, group_id: int) -> List[int]:
        """获取群聊通知人列表"""
        return self.data["group_notifiers"].get(str(group_id), [])
    
    def add_group_notifier(self, group_id: int, notifier_id: int):
        """添加群聊通知人"""
        group_id_str = str(group_id)
        notifiers = self.get_group_notifiers(group_id)
        if notifier_id not in notifiers:
            notifiers.append(notifier_id)
            self.data["group_notifiers"][group_id_str] = notifiers
            self._mark_dirty_and_schedule_save()

    def remove_group_notifier(self, group_id: int, notifier_id: int):
        """移除群聊通知人"""
        group_id_str = str(group_id)
        notifiers = self.get_group_notifiers(group_id)
        if notifier_id in notifiers:
            notifiers.remove(notifier_id)
            self.data["group_notifiers"][group_id_str] = notifiers
            self._mark_dirty_and_schedule_save()

    def get_monitor_groups(self) -> List[int]:
        """获取启用NSFW检测的群聊列表"""
        return [int(group_id) for group_id, enabled in self.data["groups"].items() if enabled]
    
    def get_group_admins(self, group_id: Optional[int] = None) -> List[int]:
        """获取群组管理员列表，如果未指定群组则返回全局管理员"""
        if group_id is None:
            return self.get_global_admins()
        
        group_id_str = str(group_id)
        group_admins = self.data["group_admins"].get(group_id_str, [])
        
        # 如果群组没有设置专门的管理员，则返回全局管理员
        if not group_admins:
            return self.get_global_admins()
        
        return group_admins
    
    def add_group_admin(self, group_id: int, admin_id: int):
        """添加群组管理员"""
        group_id_str = str(group_id)
        admins = self.data["group_admins"].get(group_id_str, [])
        if admin_id not in admins:
            admins.append(admin_id)
            self.data["group_admins"][group_id_str] = admins
            self._mark_dirty_and_schedule_save()
    
    def remove_group_admin(self, group_id: int, admin_id: int):
        """移除群组管理员"""
        group_id_str = str(group_id)
        admins = self.data["group_admins"].get(group_id_str, [])
        if admin_id in admins:
            admins.remove(admin_id)
            self.data["group_admins"][group_id_str] = admins
            self._mark_dirty_and_schedule_save()
    
    def can_manage_notifiers(self, user_id: int, group_id: int) -> bool:
        """检查用户是否可以管理通知人（全局管理员或群组管理员）"""
        return user_id in self.get_global_admins() or user_id in self.get_group_admins(group_id)
    
    def can_manage_settings(self, user_id: int, group_id: int) -> bool:
        """检查用户是否可以管理群组设置（全局管理员或群组管理员）"""
        return user_id in self.get_global_admins() or user_id in self.get_group_admins(group_id)
    
    def is_global_admin(self, user_id: int) -> bool:
        """检查用户是否为全局管理员"""
        return user_id in self.get_global_admins()
    
    async def cleanup(self):
        """清理资源"""
        if self._save_task:
            self._save_task.cancel()
        if self._config_dirty and self._save_callback:
            self._save_callback()