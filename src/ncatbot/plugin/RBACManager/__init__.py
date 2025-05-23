# -------------------------
# @Author       : Fish-LP fish.zh@outlook.com
# @Date         : 2025-02-24 21:59:13
# @LastEditors  : Fish-LP fish.zh@outlook.com
# @LastEditTime : 2025-03-15 17:46:15
# @Description  : 喵喵喵, 我还没想好怎么介绍文件喵
# @Copyright (c) 2025 by Fish-LP, Fcatbot使用许可协议
# -------------------------
from ncatbot.plugin.RBACManager.permission_trie import Trie
from ncatbot.plugin.RBACManager.RBAC_Manager import PermissionPath, RBACManager

"""
* 基本概念
* RBAC(Role-Based Access Control)基于角色的访问控制, 是一种权限管理模型, 通过为用户分配角色, 再为角色分配权限, 实现对系统资源的访问控制。
用户 : 使用者, 代表一种使用职责或职能的个体。
角色 : 定义一组权限的集合, 代表一种职责或职能, 如管理员、编辑、访客等。
权限 : 允许或拒绝访问特定资源或执行特定操作的能力, 如读取、写入、删除文件等。
资源 : 系统中需要保护的对象, 如文件、数据库、Web页面、服务等。

* 结构原理
用户—角色关系 : 用户与角色之间是多对多的关系, 一个用户可以拥有多个角色, 一个角色也可以分配给多个用户。
    例如, 一个员工可以同时是 "内容编辑" 角色和 "内容审核" 角色的成员。

角色—权限关系 : 角色与权限之间也是多对多的关系。一个角色可以包含多个权限, 一个权限也可以被分配给多个角色。
    例如, "内容编辑" 角色拥有对文档的编辑、删除权限, 而"内容审核" 角色拥有审核文档和编辑权限。

* 角色继承 :
角色之间可以形成层级关系, 允许父角色的权限被子角色继承。这种继承关系可以简化权限管理
    例如, 管理员角色可以继承一般用户的权限, 并在其基础上增加系统管理相关的权限。

* 权限路径
    描述系统中访问资源或操作的权限序列。
    例如
    具体权限 : "插件.插件功能1" 表示 "插件" 下的 "插件功能1" 权限。
    通配符 "*" : "插件.*" 代表 "插件" 下的所有权限。
    通配符 "**" : "插件.**" 代表 "插件" 下的所有权限,包括子权限。

! 警告: 没有进行完全的安全检查
"""
__all__ = [
    "RBACManager",
    "PermissionPath",
    "Trie",
]
