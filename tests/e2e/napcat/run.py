"""
NapCat E2E 引导式测试

直接使用 NcatBot 框架管理会话，不依赖 pytest。
通过 BotClient + NapCatAdapter 建立真实连接，顺序执行测试场景。

用法:
    # 需要先配置 config.yaml 中的 napcat 连接信息
    # 设置环境变量指定测试目标:
    #   NAPCAT_TEST_GROUP  测试群号 (必填)
    #   NAPCAT_TEST_USER   测试用户 QQ (必填)

    python tests/e2e/napcat/run.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import traceback
from dataclasses import dataclass, field

from ncatbot.app import BotClient
from ncatbot.api.client import BotAPIClient
from ncatbot.utils import ncatbot_config


# ── 测试结果 ──────────────────────────────────────────────────


@dataclass
class TestResult:
    name: str
    spec_id: str
    passed: bool
    elapsed: float = 0.0
    error: str = ""


@dataclass
class TestReport:
    results: list[TestResult] = field(default_factory=list)

    def add(self, result: TestResult) -> None:
        self.results.append(result)
        mark = "PASS" if result.passed else "FAIL"
        print(f"  [{mark}] {result.spec_id}: {result.name} ({result.elapsed:.2f}s)")
        if result.error:
            for line in result.error.strip().splitlines():
                print(f"         {line}")

    def summary(self) -> None:
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        failed = total - passed
        print(f"\n{'=' * 60}")
        print(f"  总计: {total}  通过: {passed}  失败: {failed}")
        if failed:
            print("\n  失败项:")
            for r in self.results:
                if not r.passed:
                    print(f"    - {r.spec_id}: {r.name}")
        print(f"{'=' * 60}")


# ── 测试场景 ──────────────────────────────────────────────────


async def scenario_basic_info(api: BotAPIClient, report: TestReport, **_) -> None:
    """场景 1: 基础信息查询 (只读)"""
    print("\n── 场景 1: 基础信息查询 ──")

    # NC-01: get_login_info
    await _run(
        report,
        "NC-01",
        "get_login_info 返回 user_id + nickname",
        async_fn=async_test(
            lambda: api.info.get_login_info(),
            lambda r: (
                r is not None
                and ("user_id" in r if isinstance(r, dict) else hasattr(r, "user_id"))
            ),
        ),
    )

    # NC-02: get_friend_list
    await _run(
        report,
        "NC-02",
        "get_friend_list 返回列表",
        async_fn=async_test(
            lambda: api.info.get_friend_list(),
            lambda r: isinstance(r, list),
        ),
    )

    # NC-03: get_group_list
    await _run(
        report,
        "NC-03",
        "get_group_list 返回列表",
        async_fn=async_test(
            lambda: api.info.get_group_list(),
            lambda r: isinstance(r, list),
        ),
    )


async def scenario_basic_info_with_targets(
    api: BotAPIClient,
    report: TestReport,
    *,
    group_id: int,
    user_id: int,
) -> None:
    """场景 1 续: 需要目标参数的查询"""
    # NC-04: get_group_info
    await _run(
        report,
        "NC-04",
        "get_group_info 返回指定群信息",
        async_fn=async_test(
            lambda: api.info.get_group_info(group_id=group_id),
            lambda r: r is not None,
        ),
    )

    # NC-05: get_group_member_list
    await _run(
        report,
        "NC-05",
        "get_group_member_list 返回成员列表",
        async_fn=async_test(
            lambda: api.info.get_group_member_list(group_id=group_id),
            lambda r: isinstance(r, list),
        ),
    )

    # NC-06: get_stranger_info
    await _run(
        report,
        "NC-06",
        "get_stranger_info 返回用户信息",
        async_fn=async_test(
            lambda: api.info.get_stranger_info(user_id=user_id),
            lambda r: r is not None,
        ),
    )


async def scenario_group_msg(
    api: BotAPIClient,
    report: TestReport,
    *,
    group_id: int,
    **_,
) -> None:
    """场景 2: 群消息操作 (发送 → 查询 → 撤回)"""
    print("\n── 场景 2: 群消息操作 ──")

    # NC-10: 发送群消息
    message_id = None

    async def send_and_capture():
        nonlocal message_id
        result = await api.send_group_msg(
            group_id=group_id,
            message=[
                {"type": "text", "data": {"text": "[E2E] 生命周期测试 - 即将撤回"}}
            ],
        )
        assert result is not None, "发送返回 None"
        message_id = result.get("message_id") if isinstance(result, dict) else result
        assert message_id, "未获取到 message_id"

    await _run(report, "NC-10", "发送群文本消息", async_fn=send_and_capture)

    if message_id is None:
        print("  [SKIP] NC-11, NC-12: 依赖 NC-10 的 message_id")
        return

    # NC-11: 查询消息详情
    await _run(
        report,
        "NC-11",
        "查询消息详情",
        async_fn=async_test(
            lambda: api.info.get_msg(message_id=int(message_id)),
            lambda r: r is not None,
        ),
    )

    # NC-12: 撤回消息
    async def delete_msg():
        await api.delete_msg(message_id=int(message_id))

    await _run(report, "NC-12", "撤回消息", async_fn=delete_msg)

    # NC-13: 发送简单文本
    await _run(
        report,
        "NC-13",
        "发送群文本消息 (简单)",
        async_fn=async_test(
            lambda: api.send_group_msg(
                group_id=group_id,
                message=[{"type": "text", "data": {"text": "[E2E] 文本消息测试"}}],
            ),
            lambda r: r is not None,
        ),
    )


async def scenario_friend(
    api: BotAPIClient,
    report: TestReport,
    *,
    user_id: int,
    **_,
) -> None:
    """场景 3: 好友互动"""
    print("\n── 场景 3: 好友互动 ──")

    # NC-20: 发送私聊消息
    await _run(
        report,
        "NC-20",
        "发送私聊文本消息",
        async_fn=async_test(
            lambda: api.send_private_msg(
                user_id=user_id,
                message=[{"type": "text", "data": {"text": "[E2E] 私聊消息测试"}}],
            ),
            lambda r: r is not None,
        ),
    )


# ── 工具函数 ──────────────────────────────────────────────────


def async_test(call, check=None):
    """构造一个 async 测试函数：call() 获取结果，check(result) 断言"""

    async def _test():
        result = await call()
        if check is not None:
            assert check(result), f"断言失败: result={result!r}"

    return _test


async def _run(report: TestReport, spec_id: str, name: str, async_fn) -> None:
    """执行单个测试，捕获异常并记录结果"""
    t0 = time.monotonic()
    try:
        await asyncio.wait_for(async_fn(), timeout=30.0)
        report.add(
            TestResult(
                name=name, spec_id=spec_id, passed=True, elapsed=time.monotonic() - t0
            )
        )
    except Exception as e:
        tb = traceback.format_exception_only(type(e), e)
        report.add(
            TestResult(
                name=name,
                spec_id=spec_id,
                passed=False,
                elapsed=time.monotonic() - t0,
                error="".join(tb),
            )
        )


# ── 主入口 ──────────────────────────────────────────────────


async def main() -> int:
    group_id_str = os.environ.get("NAPCAT_TEST_GROUP", "")
    user_id_str = os.environ.get("NAPCAT_TEST_USER", "")

    if not group_id_str or not user_id_str:
        print("错误: 请设置环境变量 NAPCAT_TEST_GROUP 和 NAPCAT_TEST_USER")
        print("  例: $env:NAPCAT_TEST_GROUP='123456'; $env:NAPCAT_TEST_USER='654321'")
        return 1

    group_id = int(group_id_str)
    user_id = int(user_id_str)

    # 强制 skip_setup，假定 NapCat 服务已在运行
    ncatbot_config.update_napcat(skip_setup=True)

    print("NapCat E2E 测试")
    print(f"  WS:    {ncatbot_config.napcat.ws_uri}")
    print(f"  群号:  {group_id}")
    print(f"  用户:  {user_id}")
    print(f"{'=' * 60}")

    bot = BotClient()
    report = TestReport()

    try:
        await bot.run_async()
        api = bot.api

        await scenario_basic_info(api, report)
        await scenario_basic_info_with_targets(
            api, report, group_id=group_id, user_id=user_id
        )
        await scenario_group_msg(api, report, group_id=group_id)
        await scenario_friend(api, report, user_id=user_id)
    except Exception as e:
        print(f"\n启动失败: {e}")
        traceback.print_exc()
        return 1
    finally:
        await bot.shutdown()

    report.summary()
    return 0 if all(r.passed for r in report.results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
