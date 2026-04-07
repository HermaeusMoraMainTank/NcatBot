"""配置安全工具 — 密码校验与 token 生成。"""

import random
import string
from re import search

URI_SPECIAL_CHARS = "-_.~!()*"


def strong_password_check(password: str) -> bool:
    """检查密码强度：>=12 位，包含数字、大小写字母、特殊符号。"""
    patterns = [r"\d", "[a-z]", "[A-Z]"]
    return (
        len(password) >= 12
        and all(search(p, password) for p in patterns)
        and any(c in URI_SPECIAL_CHARS for c in password)
    )


def generate_strong_token(length: int = 16) -> str:
    """生成满足强度策略的随机 token。"""
    all_chars = string.ascii_letters + string.digits + URI_SPECIAL_CHARS
    while True:
        token = "".join(random.choice(all_chars) for _ in range(length))
        if strong_password_check(token):
            return token
