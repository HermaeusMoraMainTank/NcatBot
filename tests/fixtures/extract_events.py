"""
从 logs/ 提取真实事件数据 → tests/fixtures/

Usage:
    python tests/fixtures/extract_events.py

生成:
  - real_events.json         — 各类型精选事件 (已脱敏)
  - real_group_messages.json — 大量群消息 (已脱敏, 去重)
  - bilibili_events.json     — Bilibili 事件夹具 (构造)
  - github_events.json       — GitHub 事件夹具 (构造)

脱敏策略:
  - QQ 号 / user_id / group_id / operator_id / target_id → 递增假 ID
  - nickname / card / 昵称 → TestUser / TestCard
  - URL → https://example.com/...
  - 消息正文保留 (测试段解析) / raw_message 截断

注: Bilibili / GitHub 的原始数据不以 JSON 形式写入日志,
    因此构造符合 parser 输入格式的模拟数据.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

LOGS_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
FIXTURES_DIR = Path(__file__).resolve().parent

# ── 脱敏映射 ──────────────────────────────────────────────────

_ID_MAP: Dict[str, str] = {}
_ID_COUNTER = [100000001]


def _fake_id(real_id: Any) -> str:
    s = str(real_id)
    if not s or s == "0":
        return s
    if s not in _ID_MAP:
        _ID_MAP[s] = str(_ID_COUNTER[0])
        _ID_COUNTER[0] += 1
    return _ID_MAP[s]


def _sanitize(obj: Any) -> Any:
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            if (
                k
                in (
                    "self_id",
                    "user_id",
                    "group_id",
                    "operator_id",
                    "target_id",
                )
                and v
            ):
                result[k] = _fake_id(v)
            elif k == "sender" and isinstance(v, dict):
                s = dict(v)
                if "user_id" in s:
                    s["user_id"] = _fake_id(s["user_id"])
                if "nickname" in s:
                    s["nickname"] = "TestUser"
                if "card" in s and s["card"]:
                    s["card"] = "TestCard"
                result[k] = s
            elif k == "nickname":
                result[k] = "TestUser"
            elif k == "url" and isinstance(v, str) and "qq.com" in v:
                result[k] = "https://example.com/test.png"
            elif k == "uid" and isinstance(v, str) and v.startswith("u_"):
                result[k] = "u_test_uid_" + v[-4:]
            elif k == "raw_message" and isinstance(v, str) and len(v) > 80:
                result[k] = v[:80]
            elif k == "group_name" and isinstance(v, str):
                result[k] = "TestGroup"
            else:
                result[k] = _sanitize(v)
        return result
    elif isinstance(obj, list):
        return [_sanitize(item) for item in obj]
    return obj


# ── 日志提取 ──────────────────────────────────────────────────


def _extract_json_from_line(line: str) -> Optional[Dict[str, Any]]:
    """从日志行中提取 JSON dict"""
    # 新格式: ➜ ... {"time":..., "post_type":...}
    m = re.search(r"(\{.+\})\s*$", line)
    if m:
        try:
            d = json.loads(m.group(1))
            if isinstance(d, dict):
                return d
        except json.JSONDecodeError:
            pass
    # 旧格式: | {'time': ..., 'post_type': ...}
    m2 = re.search(r"\|\s*(\{.+\})\s*$", line)
    if m2:
        try:
            d = ast.literal_eval(m2.group(1))
            if isinstance(d, dict):
                return d
        except (ValueError, SyntaxError):
            pass
    # 截断行修复: 提取 { 起始部分，补齐缺失的括号
    m3 = re.search(r"(\{.+)", line)
    if m3:
        fragment = m3.group(1)
        # 计算未闭合的 {} 和 []
        opens = fragment.count("{") - fragment.count("}")
        open_sq = fragment.count("[") - fragment.count("]")
        if opens > 0 or open_sq > 0:
            repaired = fragment + "]" * max(open_sq, 0) + "}" * max(opens, 0)
            try:
                d = json.loads(repaired)
                if isinstance(d, dict):
                    return d
            except json.JSONDecodeError:
                pass
    return None


def _collect_all_ob11_events() -> Tuple[List[Dict], List[Dict]]:
    """
    从所有日志提取 OB11 事件.

    Returns: (unique_by_type, all_group_messages)
    """
    unique: Dict[Tuple, Dict] = {}
    group_messages: List[Dict] = []
    seen_msg_ids: Set[str] = set()

    log_files = sorted(LOGS_DIR.glob("bot.log*"), key=lambda p: p.stat().st_mtime)
    for logfile in log_files:
        with open(logfile, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "post_type" not in line:
                    continue
                d = _extract_json_from_line(line)
                if d is None or "post_type" not in d:
                    continue

                pt = d.get("post_type")
                sub = d.get(
                    "message_type",
                    d.get(
                        "meta_event_type",
                        d.get("notice_type", d.get("request_type", "")),
                    ),
                )
                st = d.get("sub_type", "")

                # 各类型精选一条
                key = (pt, sub, st)
                if key not in unique:
                    unique[key] = d

                # 群消息全部收集
                if pt == "message" and d.get("message_type") == "group":
                    msg_id = str(d.get("message_id", ""))
                    if msg_id and msg_id not in seen_msg_ids:
                        seen_msg_ids.add(msg_id)
                        group_messages.append(d)

    return list(unique.values()), group_messages


# ── Bilibili 夹具构造 ────────────────────────────────────────


def _build_bilibili_fixtures() -> List[Dict]:
    """构造 Bilibili parser 输入格式的测试事件"""
    fixtures = []

    # 1. 弹幕 DANMU_MSG
    fixtures.append(
        {
            "source_type": "live",
            "raw_data": {
                "type": "DANMU_MSG",
                "room_real_id": "22628755",
                "room_display_id": "22628755",
                "data": {
                    "info": [
                        [0, 1, 25, 16777215, 1700000000, 0, 0, "user-hash", 0],
                        "测试弹幕消息",
                        [10001, "TestUser1", 0, 0, 0, 10000, 1, ""],
                        [
                            5,
                            "TestMedal",
                            "MedalOwner",
                            9999,
                            398668,
                            "",
                            0,
                            398668,
                            398668,
                            0,
                            0,
                            9999,
                            0,
                            "",
                        ],
                    ]
                },
            },
        }
    )

    # 2. 礼物 SEND_GIFT
    fixtures.append(
        {
            "source_type": "live",
            "raw_data": {
                "type": "SEND_GIFT",
                "room_real_id": "22628755",
                "room_display_id": "22628755",
                "data": {
                    "data": {
                        "uid": 10002,
                        "uname": "TestUser2",
                        "face": "https://example.com/face.jpg",
                        "giftName": "辣条",
                        "giftId": 1,
                        "num": 5,
                        "price": 100,
                        "coin_type": "silver",
                    }
                },
            },
        }
    )

    # 3. SC (醒目留言) SUPER_CHAT_MESSAGE
    fixtures.append(
        {
            "source_type": "live",
            "raw_data": {
                "type": "SUPER_CHAT_MESSAGE",
                "room_real_id": "22628755",
                "room_display_id": "22628755",
                "data": {
                    "data": {
                        "uid": 10003,
                        "message": "SC 测试消息",
                        "price": 30,
                        "time": 60,
                        "user_info": {
                            "uname": "TestUser3",
                            "face": "https://example.com/face3.jpg",
                        },
                    }
                },
            },
        }
    )

    # 4. 大航海 GUARD_BUY
    fixtures.append(
        {
            "source_type": "live",
            "raw_data": {
                "type": "GUARD_BUY",
                "room_real_id": "22628755",
                "room_display_id": "22628755",
                "data": {
                    "data": {
                        "uid": 10004,
                        "username": "TestUser4",
                        "guard_level": 3,
                        "price": 198000,
                    }
                },
            },
        }
    )

    # 5. 进入事件 INTERACT_WORD_V2
    fixtures.append(
        {
            "source_type": "live",
            "raw_data": {
                "type": "INTERACT_WORD_V2",
                "room_real_id": "22628755",
                "room_display_id": "22628755",
                "data": {
                    "data": {
                        "uid": 10005,
                        "uname": "TestUser5",
                        "msg_type": 1,
                        "pb_decoded": {
                            "uid": 10005,
                            "uname": "TestUser5",
                            "msg_type": 1,
                        },
                    }
                },
            },
        }
    )

    # 6. 点赞 LIKE_INFO_V3_CLICK
    fixtures.append(
        {
            "source_type": "live",
            "raw_data": {
                "type": "LIKE_INFO_V3_CLICK",
                "room_real_id": "22628755",
                "room_display_id": "22628755",
                "data": {
                    "data": {
                        "uid": 10006,
                        "uname": "TestUser6",
                        "like_count": 42,
                    }
                },
            },
        }
    )

    # 7. 人气 VIEW
    fixtures.append(
        {
            "source_type": "live",
            "raw_data": {
                "type": "VIEW",
                "room_real_id": "22628755",
                "room_display_id": "22628755",
                "data": 12345,
            },
        }
    )

    # 8. 开播 LIVE
    fixtures.append(
        {
            "source_type": "live",
            "raw_data": {
                "type": "LIVE",
                "room_real_id": "22628755",
                "room_display_id": "22628755",
                "data": {},
            },
        }
    )

    # 9. 下播 PREPARING
    fixtures.append(
        {
            "source_type": "live",
            "raw_data": {
                "type": "PREPARING",
                "room_real_id": "22628755",
                "room_display_id": "22628755",
                "data": {},
            },
        }
    )

    # 10. 直播间信息变更 ROOM_CHANGE
    fixtures.append(
        {
            "source_type": "live",
            "raw_data": {
                "type": "ROOM_CHANGE",
                "room_real_id": "22628755",
                "room_display_id": "22628755",
                "data": {
                    "data": {
                        "title": "测试直播间标题",
                        "area_name": "虚拟主播",
                    }
                },
            },
        }
    )

    # 11. 用户被禁言 ROOM_BLOCK_MSG
    fixtures.append(
        {
            "source_type": "live",
            "raw_data": {
                "type": "ROOM_BLOCK_MSG",
                "room_real_id": "22628755",
                "room_display_id": "22628755",
                "data": {
                    "data": {
                        "uid": 10007,
                        "uname": "BadUser",
                    }
                },
            },
        }
    )

    # 12. 全员禁言 ROOM_SILENT_ON
    fixtures.append(
        {
            "source_type": "live",
            "raw_data": {
                "type": "ROOM_SILENT_ON",
                "room_real_id": "22628755",
                "room_display_id": "22628755",
                "data": {
                    "data": {
                        "type": "level",
                        "level": 1,
                        "second": -1,
                    }
                },
            },
        }
    )

    # 13. 全员禁言解除 ROOM_SILENT_OFF
    fixtures.append(
        {
            "source_type": "live",
            "raw_data": {
                "type": "ROOM_SILENT_OFF",
                "room_real_id": "22628755",
                "room_display_id": "22628755",
                "data": {
                    "data": {
                        "type": "",
                        "level": 0,
                        "second": 0,
                    }
                },
            },
        }
    )

    # 14. 观看人数 WATCHED_CHANGE
    fixtures.append(
        {
            "source_type": "live",
            "raw_data": {
                "type": "WATCHED_CHANGE",
                "room_real_id": "22628755",
                "room_display_id": "22628755",
                "data": {
                    "data": {
                        "num": 1234,
                        "text_small": "1234",
                        "text_large": "1234人看过",
                    }
                },
            },
        }
    )

    # 15. 弹幕聚合 DANMU_AGGREGATION
    fixtures.append(
        {
            "source_type": "live",
            "raw_data": {
                "type": "DANMU_AGGREGATION",
                "room_real_id": "22628755",
                "room_display_id": "22628755",
                "data": {
                    "data": {
                        "activity_identity": "test_identity",
                        "msg": "集合弹幕",
                        "aggregation_num": 88,
                    }
                },
            },
        }
    )

    # 16. 进场特效 ENTRY_EFFECT
    fixtures.append(
        {
            "source_type": "live",
            "raw_data": {
                "type": "ENTRY_EFFECT",
                "room_real_id": "22628755",
                "room_display_id": "22628755",
                "data": {
                    "data": {
                        "uid": 10008,
                        "copy_writing_v2": "欢迎 <%TestUser8%> 进入直播间",
                    }
                },
            },
        }
    )

    # 17. 系统连接事件
    fixtures.append(
        {
            "source_type": "live",
            "raw_data": {
                "type": "VERIFICATION_SUCCESSFUL",
                "room_real_id": "22628755",
                "room_display_id": "22628755",
                "data": {},
            },
        }
    )

    # 18. 私信
    fixtures.append(
        {
            "source_type": "session",
            "raw_data": {
                "sender_uid": 20001,
                "msg_type": "1",
                "content": "你好，这是一条测试私信",
                "msg_key": "key_12345",
                "msg_seqno": 100,
                "receiver_id": 99999,
                "timestamp": 1700000000,
            },
        }
    )

    # 19. 私信撤回
    fixtures.append(
        {
            "source_type": "session",
            "raw_data": {
                "sender_uid": 20001,
                "msg_type": "5",
                "msg_key": "key_12345",
                "timestamp": 1700000010,
            },
        }
    )

    # 20. 评论
    fixtures.append(
        {
            "source_type": "comment",
            "raw_data": {
                "resource_id": "BV1Z7BeBpEnF",
                "resource_type": "video",
                "reply": {
                    "rpid": 88001,
                    "root": 0,
                    "parent": 0,
                    "like": 15,
                    "ctime": 1700000020,
                    "member": {
                        "mid": 30001,
                        "uname": "CommentUser",
                        "avatar": "https://example.com/avatar.jpg",
                    },
                    "content": {
                        "message": "测试评论内容",
                    },
                },
            },
        }
    )

    return fixtures


# ── GitHub 夹具构造 ───────────────────────────────────────────


def _build_github_fixtures() -> List[Dict]:
    """构造 GitHub parser 输入格式的 webhook payload"""
    _repo = {
        "id": 123456,
        "full_name": "testowner/testrepo",
        "html_url": "https://github.com/testowner/testrepo",
        "description": "Test repo",
        "private": False,
        "default_branch": "main",
    }
    _sender = {
        "id": 10001,
        "login": "testuser",
        "avatar_url": "https://example.com/avatar.png",
        "html_url": "https://github.com/testuser",
        "type": "User",
    }

    fixtures = []

    # 1. issues (opened)
    fixtures.append(
        {
            "event_type": "issues",
            "payload": {
                "action": "opened",
                "repository": _repo,
                "sender": _sender,
                "issue": {
                    "number": 42,
                    "title": "Test issue title",
                    "body": "This is a test issue body.",
                    "state": "open",
                    "html_url": "https://github.com/testowner/testrepo/issues/42",
                    "labels": [{"name": "bug"}, {"name": "critical"}],
                    "assignees": [{"login": "testuser"}],
                },
            },
        }
    )

    # 2. issue_comment (created)
    fixtures.append(
        {
            "event_type": "issue_comment",
            "payload": {
                "action": "created",
                "repository": _repo,
                "sender": _sender,
                "comment": {
                    "id": 999001,
                    "body": "Looks like a valid issue, I'll investigate.",
                    "html_url": "https://github.com/testowner/testrepo/issues/42#issuecomment-999001",
                },
                "issue": {
                    "number": 42,
                    "title": "Test issue title",
                },
            },
        }
    )

    # 3. pull_request (opened)
    fixtures.append(
        {
            "event_type": "pull_request",
            "payload": {
                "action": "opened",
                "repository": _repo,
                "sender": _sender,
                "pull_request": {
                    "number": 99,
                    "title": "Fix the bug in parser",
                    "body": "This PR fixes #42.",
                    "state": "open",
                    "html_url": "https://github.com/testowner/testrepo/pull/99",
                    "head": {"ref": "fix/parser-bug"},
                    "base": {"ref": "main"},
                    "merged": False,
                    "draft": False,
                },
            },
        }
    )

    # 4. pull_request_review_comment (created)
    fixtures.append(
        {
            "event_type": "pull_request_review_comment",
            "payload": {
                "action": "created",
                "repository": _repo,
                "sender": _sender,
                "comment": {
                    "id": 999002,
                    "body": "This line could use better error handling.",
                    "html_url": "https://github.com/testowner/testrepo/pull/99#discussion_r999002",
                    "diff_hunk": "@@ -10,3 +10,5 @@\n+    try:\n+        result = parse(data)",
                    "path": "src/parser.py",
                },
                "pull_request": {
                    "number": 99,
                },
            },
        }
    )

    # 5. push
    fixtures.append(
        {
            "event_type": "push",
            "payload": {
                "action": "created",
                "ref": "refs/heads/main",
                "before": "abc1234000000000000000000000000000000000",
                "after": "def5678000000000000000000000000000000000",
                "repository": _repo,
                "sender": _sender,
                "pusher": {
                    "name": "testuser",
                    "email": "test@example.com",
                },
                "commits": [
                    {
                        "id": "def5678000000000000000000000000000000000",
                        "message": "fix: resolve parser regression",
                        "author": {
                            "name": "testuser",
                            "email": "test@example.com",
                        },
                        "url": "https://github.com/testowner/testrepo/commit/def5678",
                        "timestamp": "2025-12-01T10:00:00Z",
                        "added": ["src/new_file.py"],
                        "removed": [],
                        "modified": ["src/parser.py"],
                    }
                ],
                "head_commit": {
                    "id": "def5678000000000000000000000000000000000",
                    "message": "fix: resolve parser regression",
                    "author": {
                        "name": "testuser",
                        "email": "test@example.com",
                    },
                    "url": "https://github.com/testowner/testrepo/commit/def5678",
                    "timestamp": "2025-12-01T10:00:00Z",
                    "added": ["src/new_file.py"],
                    "removed": [],
                    "modified": ["src/parser.py"],
                },
            },
        }
    )

    # 6. watch (starred)
    fixtures.append(
        {
            "event_type": "watch",
            "payload": {
                "action": "started",
                "repository": _repo,
                "sender": _sender,
            },
        }
    )

    # 7. fork
    fixtures.append(
        {
            "event_type": "fork",
            "payload": {
                "action": "created",
                "repository": _repo,
                "sender": _sender,
                "forkee": {
                    "full_name": "forker/testrepo",
                    "html_url": "https://github.com/forker/testrepo",
                    "owner": {"login": "forker"},
                    "description": "Forked repo",
                },
            },
        }
    )

    # 8. release (published)
    fixtures.append(
        {
            "event_type": "release",
            "payload": {
                "action": "published",
                "repository": _repo,
                "sender": _sender,
                "release": {
                    "id": 88001,
                    "tag_name": "v1.0.0",
                    "name": "Release v1.0.0",
                    "body": "First stable release.\n\n## Changes\n- Feature A\n- Fix B",
                    "prerelease": False,
                    "draft": False,
                    "html_url": "https://github.com/testowner/testrepo/releases/tag/v1.0.0",
                },
            },
        }
    )

    # 9. ping
    fixtures.append(
        {
            "event_type": "ping",
            "payload": {
                "action": "created",
                "zen": "Responsive is better than fast.",
                "hook_id": 12345,
                "repository": _repo,
                "sender": _sender,
            },
        }
    )

    return fixtures


# ── 主函数 ────────────────────────────────────────────────────


def main() -> None:
    print("=== 提取 OB11 事件 ===")
    unique_events, group_messages = _collect_all_ob11_events()
    print(f"  唯一事件类型: {len(unique_events)}")
    print(f"  群消息总数:   {len(group_messages)}")

    # 脱敏
    sanitized_unique = [_sanitize(e) for e in unique_events]
    sanitized_group = [_sanitize(e) for e in group_messages]

    # 统计群消息中各类 segment 类型分布
    seg_types: Dict[str, int] = {}
    for msg in group_messages:
        for seg in msg.get("message", []):
            t = seg.get("type", "unknown")
            seg_types[t] = seg_types.get(t, 0) + 1
    print("  消息段分布:", dict(sorted(seg_types.items(), key=lambda x: -x[1])))

    # 写出 OB11 夹具
    with open(FIXTURES_DIR / "real_events.json", "w", encoding="utf-8") as f:
        json.dump(sanitized_unique, f, ensure_ascii=False, indent=2)
    print(f"  → real_events.json ({len(sanitized_unique)} 条)")

    with open(FIXTURES_DIR / "real_group_messages.json", "w", encoding="utf-8") as f:
        json.dump(sanitized_group, f, ensure_ascii=False, indent=2)
    print(f"  → real_group_messages.json ({len(sanitized_group)} 条)")

    # Bilibili / GitHub 夹具
    print("\n=== 构造 Bilibili 夹具 ===")
    bili_fixtures = _build_bilibili_fixtures()
    with open(FIXTURES_DIR / "bilibili_events.json", "w", encoding="utf-8") as f:
        json.dump(bili_fixtures, f, ensure_ascii=False, indent=2)
    print(f"  → bilibili_events.json ({len(bili_fixtures)} 条)")

    print("\n=== 构造 GitHub 夹具 ===")
    gh_fixtures = _build_github_fixtures()
    with open(FIXTURES_DIR / "github_events.json", "w", encoding="utf-8") as f:
        json.dump(gh_fixtures, f, ensure_ascii=False, indent=2)
    print(f"  → github_events.json ({len(gh_fixtures)} 条)")

    print(f"\n=== 完成, 共 {len(_ID_MAP)} 个 ID 已脱敏 ===")


if __name__ == "__main__":
    main()
