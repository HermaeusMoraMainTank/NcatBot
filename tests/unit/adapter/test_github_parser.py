"""
GitHub 事件解析器测试

数据来源: tests/fixtures/github_events.json (构造数据, 符合 GitHubEventParser 输入格式)

规范:
  GH-01: issues 事件解析 (opened)
  GH-02: issue_comment 事件解析
  GH-03: pull_request 事件解析
  GH-04: pull_request_review_comment 事件解析
  GH-05: push 事件解析 (含 commits)
  GH-06: watch (star) 事件解析
  GH-07: fork 事件解析
  GH-08: release 事件解析
  GH-09: ping 事件解析
  GH-10: HMAC-SHA256 签名验证
  GH-11: 全量夹具一致性 — 全部事件可解析且非 None
"""

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from ncatbot.adapter.github.parser import GitHubEventParser
from ncatbot.types.github.events import (
    GitHubEventData,
    GitHubForkEventData,
    GitHubIssueCommentEventData,
    GitHubIssueEventData,
    GitHubPREventData,
    GitHubPRReviewCommentEventData,
    GitHubPushEventData,
    GitHubReleaseEventData,
    GitHubStarEventData,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent.parent / "fixtures" / "github_events.json"
)


@pytest.fixture(scope="module")
def fixtures() -> List[Dict[str, Any]]:
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def parser() -> GitHubEventParser:
    return GitHubEventParser(self_id="test_gh")


def _get(fixtures, event_type):
    for fix in fixtures:
        if fix["event_type"] == event_type:
            return fix
    pytest.skip(f"夹具中不存在 event_type={event_type}")


class TestIssue:
    """GH-01: issues 事件"""

    def test_gh01_issue_opened(self, parser, fixtures):
        fix = _get(fixtures, "issues")
        result = parser.parse(fix["event_type"], fix["payload"])
        assert isinstance(result, GitHubIssueEventData)
        assert result.issue_number == 42
        assert result.issue_title == "Test issue title"
        assert result.action == "opened"
        assert "bug" in result.labels
        assert result.repo.full_name == "testowner/testrepo"
        assert result.sender.login == "testuser"


class TestIssueComment:
    """GH-02: issue_comment 事件"""

    def test_gh02_issue_comment(self, parser, fixtures):
        fix = _get(fixtures, "issue_comment")
        result = parser.parse(fix["event_type"], fix["payload"])
        assert isinstance(result, GitHubIssueCommentEventData)
        assert result.issue_number == 42
        assert "investigate" in result.comment_body


class TestPullRequest:
    """GH-03: pull_request 事件"""

    def test_gh03_pr_opened(self, parser, fixtures):
        fix = _get(fixtures, "pull_request")
        result = parser.parse(fix["event_type"], fix["payload"])
        assert isinstance(result, GitHubPREventData)
        assert result.pr_number == 99
        assert result.head_ref == "fix/parser-bug"
        assert result.base_ref == "main"
        assert result.merged is False


class TestPRReviewComment:
    """GH-04: pull_request_review_comment 事件"""

    def test_gh04_pr_review_comment(self, parser, fixtures):
        fix = _get(fixtures, "pull_request_review_comment")
        result = parser.parse(fix["event_type"], fix["payload"])
        assert isinstance(result, GitHubPRReviewCommentEventData)
        assert result.pr_number == 99
        assert result.path == "src/parser.py"
        assert "error handling" in result.comment_body


class TestPush:
    """GH-05: push 事件"""

    def test_gh05_push(self, parser, fixtures):
        fix = _get(fixtures, "push")
        result = parser.parse(fix["event_type"], fix["payload"])
        assert isinstance(result, GitHubPushEventData)
        assert result.ref == "refs/heads/main"
        assert len(result.commits) == 1
        assert result.commits[0].message == "fix: resolve parser regression"
        assert result.head_commit is not None
        assert result.pusher_name == "testuser"


class TestStar:
    """GH-06: watch (star) 事件"""

    def test_gh06_star(self, parser, fixtures):
        fix = _get(fixtures, "watch")
        result = parser.parse(fix["event_type"], fix["payload"])
        assert isinstance(result, GitHubStarEventData)
        assert result.action == "started"


class TestFork:
    """GH-07: fork 事件"""

    def test_gh07_fork(self, parser, fixtures):
        fix = _get(fixtures, "fork")
        result = parser.parse(fix["event_type"], fix["payload"])
        assert isinstance(result, GitHubForkEventData)
        assert result.forkee.full_name == "forker/testrepo"


class TestRelease:
    """GH-08: release 事件"""

    def test_gh08_release(self, parser, fixtures):
        fix = _get(fixtures, "release")
        result = parser.parse(fix["event_type"], fix["payload"])
        assert isinstance(result, GitHubReleaseEventData)
        assert result.release.tag_name == "v1.0.0"
        assert result.release.prerelease is False


class TestPing:
    """GH-09: ping 事件"""

    @pytest.mark.xfail(reason="GitHubAction 枚举缺少 'ping' 值, 框架 bug")
    def test_gh09_ping(self, parser, fixtures):
        fix = _get(fixtures, "ping")
        result = parser.parse(fix["event_type"], fix["payload"])
        assert isinstance(result, GitHubEventData)
        assert result.platform == "github"


class TestSignatureVerification:
    """GH-10: HMAC-SHA256 签名验证"""

    def test_gh10_valid_signature(self):
        payload = b'{"action": "opened"}'
        secret = "test_secret_123"
        import hashlib
        import hmac as _hmac

        sig = (
            "sha256=" + _hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        )
        assert GitHubEventParser.verify_signature(payload, secret, sig) is True

    def test_gh10_invalid_signature(self):
        payload = b'{"action": "opened"}'
        assert (
            GitHubEventParser.verify_signature(payload, "secret", "sha256=bad") is False
        )

    def test_gh10_empty_secret_allows_all(self):
        assert GitHubEventParser.verify_signature(b"data", "", "anything") is True


class TestBulkConsistency:
    """GH-11: 全量一致性"""

    def test_gh11_all_fixtures_parse(self, parser, fixtures):
        """全部夹具事件可解析且非 None (排除 ping — GitHubAction 枚举缺 'ping')"""
        failed = []
        for i, fix in enumerate(fixtures):
            if fix["event_type"] == "ping":
                continue
            try:
                result = parser.parse(fix["event_type"], fix["payload"])
                if result is None:
                    failed.append((i, fix["event_type"]))
            except Exception as exc:
                failed.append((i, fix["event_type"], str(exc)[:60]))
        assert not failed, f"解析失败或返回 None: {failed}"
