"""
Config 安全单元测试

CS-01 ~ CS-05：覆盖 strong_password_check、generate_strong_token、ConfigManager 安全检测。
"""

from ncatbot.utils.config.security import strong_password_check, generate_strong_token


class TestStrongPasswordCheck:
    """CS-01 ~ CS-02: 密码强度验证"""

    def test_cs01_valid_strong_password(self):
        """CS-01: 合法强密码通过检查"""
        assert strong_password_check("Abcdefg1234!")
        assert strong_password_check("MyP@ssw0rd!!XY")

    def test_cs02_weak_passwords_rejected(self):
        """CS-02: 弱密码拒绝 — 缺少各字符类型"""
        # 太短
        assert not strong_password_check("Abc1!")
        # 无数字
        assert not strong_password_check("Abcdefghijk!")
        # 无小写
        assert not strong_password_check("ABCDEFG1234!")
        # 无大写
        assert not strong_password_check("abcdefg1234!")
        # 无特殊字符
        assert not strong_password_check("Abcdefg12345")


class TestGenerateStrongToken:
    """CS-03: token 生成"""

    def test_cs03_generated_token_passes_check(self):
        """CS-03: generate_strong_token 结果始终通过 check"""
        for _ in range(10):
            token = generate_strong_token()
            assert strong_password_check(token), f"Token failed check: {token}"
            assert len(token) == 16


class TestConfigManagerSecurity:
    """CS-04 ~ CS-05: ConfigManager 安全检测与自动修复"""

    def _make_manager(self, ws_token="weak", ws_listen_ip="0.0.0.0"):
        from ncatbot.utils.config.manager import ConfigManager
        from ncatbot.utils.config.models import Config, AdapterEntry

        mgr = ConfigManager.__new__(ConfigManager)
        mgr._storage = None
        mgr._config = Config(
            adapters=[
                AdapterEntry(
                    type="napcat",
                    enabled=True,
                    config={
                        "ws_token": ws_token,
                        "ws_listen_ip": ws_listen_ip,
                        "enable_webui": False,
                    },
                )
            ]
        )
        return mgr

    def test_cs04_detect_weak_token(self):
        """CS-04: get_security_issues 检测弱 token"""
        mgr = self._make_manager(ws_token="weak")
        issues = mgr.get_security_issues(auto_fix=False)
        assert any("令牌强度不足" in i for i in issues)

    def test_cs05_auto_fix_weak_token(self):
        """CS-05: get_security_issues(auto_fix=True) 自动替换弱 token"""
        mgr = self._make_manager(ws_token="weak")
        issues = mgr.get_security_issues(auto_fix=True)
        # auto_fix 模式不返回 issues
        assert len(issues) == 0
        # token 已被替换为强密码
        new_token = mgr.config.adapters[0].config["ws_token"]
        assert strong_password_check(new_token)
