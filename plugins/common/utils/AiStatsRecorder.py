"""AI 使用统计与余额快照的共享记录模块。"""

from __future__ import annotations

import os
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from ncatbot.utils.logger import get_log

from .json_io import atomic_write_json, load_json as _load_json_file, resolve_data_json

_log = get_log()

GROUP_DATA_FILE = resolve_data_json("ai_group_stats.json")
USER_DATA_FILE = resolve_data_json("ai_user_stats.json")
BALANCE_DATA_FILE = resolve_data_json("ai_balance_history.json")

# 调用来源（存储粒度）
SOURCE_ACTIVE = "active"
SOURCE_PASSIVE = "passive"
SOURCE_SUMMARY = "summary"
SOURCE_CALLBACK = "callback"
SOURCE_IMPRESSION = "impression"
SOURCE_VISION = "vision"
SOURCE_TEST = "test"

SOURCE_LABELS: Dict[str, str] = {
    SOURCE_ACTIVE: "主动调用",
    SOURCE_PASSIVE: "被动触发",
    SOURCE_SUMMARY: "群聊总结",
    SOURCE_CALLBACK: "回调跟进",
    SOURCE_IMPRESSION: "用户总结",
    SOURCE_VISION: "图片识别",
    SOURCE_TEST: "测试",
}

# 明细展示时的归类（主动 = @ / 含蓝晴 / 回调 / 识图等对话类）
SOURCE_ROLLUP: Dict[str, tuple[str, List[str]]] = {
    "active": ("主动调用", [SOURCE_ACTIVE, SOURCE_CALLBACK, SOURCE_VISION, SOURCE_TEST]),
    "passive": ("被动触发", [SOURCE_PASSIVE]),
    "summary": ("群聊总结", [SOURCE_SUMMARY]),
    "impression": ("用户总结", [SOURCE_IMPRESSION]),
}

# 明细图固定展示的来源顺序
SOURCE_ROLLUP_ORDER = ["active", "passive", "summary", "impression"]

# deepseek-v4-flash 官方定价（元 / 百万 tokens）
MODEL_PRICING_CNY: Dict[str, Dict[str, float]] = {
    "deepseek-v4-flash": {
        "input_cache_hit": 0.02,
        "input_cache_miss": 1.0,
        "output": 2.0,
    },
    "deepseek-v4-pro": {
        "input_cache_hit": 0.025,
        "input_cache_miss": 3.0,
        "output": 6.0,
    },
    "meta/llama-4-maverick-17b-128e-instruct": {},
}

_save_lock = threading.Lock()


def normalize_source(source: str) -> str:
    """规范化来源标识。"""
    key = (source or SOURCE_ACTIVE).strip().lower()
    if key in SOURCE_LABELS:
        return key
    aliases = {
        "random": SOURCE_PASSIVE,
        "speak": SOURCE_ACTIVE,
    }
    return aliases.get(key, SOURCE_ACTIVE)


def source_label(source: str) -> str:
    return SOURCE_LABELS.get(normalize_source(source), source)


def estimate_cost_cny(
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    prompt_cache_hit_tokens: int = 0,
    total_tokens: int = 0,
    model: str = "deepseek-v4-flash",
) -> float:
    pricing = MODEL_PRICING_CNY.get(model)
    if not pricing:
        return 0.0

    if prompt_tokens == 0 and completion_tokens == 0 and total_tokens > 0:
        prompt_tokens = int(total_tokens * 0.75)
        completion_tokens = total_tokens - prompt_tokens

    if prompt_cache_hit_tokens > prompt_tokens:
        prompt_cache_hit_tokens = prompt_tokens
    prompt_cache_miss_tokens = max(0, prompt_tokens - prompt_cache_hit_tokens)

    cost = (
        prompt_cache_hit_tokens * pricing["input_cache_hit"]
        + prompt_cache_miss_tokens * pricing["input_cache_miss"]
        + completion_tokens * pricing["output"]
    ) / 1_000_000
    return round(cost, 6)


def _empty_source_bucket() -> dict:
    return {"count": 0, "tokens": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}


def _empty_stats() -> dict:
    return {
        "daily_counts": {},
        "daily_tokens": {},
        "daily_prompt_tokens": {},
        "daily_completion_tokens": {},
        "daily_cost": {},
        "daily_by_source": {},
        "last_used": None,
        "total_count": 0,
        "total_tokens": 0,
        "total_cost": 0.0,
    }


def _ensure_stats_fields(stats: dict) -> dict:
    for key, default in (
        ("daily_prompt_tokens", {}),
        ("daily_completion_tokens", {}),
        ("daily_cost", {}),
        ("daily_by_source", {}),
        ("total_cost", 0.0),
    ):
        stats.setdefault(key, default if not isinstance(default, dict) else {})
    return stats


def _load_json(path: str, default: dict) -> dict:
    resolved = resolve_data_json(path) if not os.path.isabs(path) else path
    try:
        return _load_json_file(resolved, default)
    except Exception as e:
        _log.warning(f"[AiStatsRecorder] 读取 {resolved} 失败: {e}")
        return default.copy()


def _save_json(path: str, data: dict) -> bool:
    resolved = resolve_data_json(path) if not os.path.isabs(path) else path
    ok = atomic_write_json(resolved, data)
    if not ok:
        _log.error(f"[AiStatsRecorder] 保存 {resolved} 失败")
    return ok


def record_from_response(
    group_id: str,
    user_id: Optional[str],
    source: str,
    ai_response: Optional[dict],
    model: str = "deepseek-v4-flash",
) -> None:
    """从 AI 响应 dict 中提取 usage 并记录。"""
    if not ai_response or not isinstance(ai_response, dict):
        return
    usage = ai_response.get("usage") or {}
    record_ai_usage(
        group_id=group_id,
        user_id=user_id,
        tokens=int(usage.get("total_tokens", 0) or 0),
        prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
        completion_tokens=int(usage.get("completion_tokens", 0) or 0),
        prompt_cache_hit_tokens=int(usage.get("prompt_cache_hit_tokens", 0) or 0),
        model=ai_response.get("model", model),
        source=source,
    )


def record_ai_usage(
    group_id: str,
    user_id: Optional[str] = None,
    tokens: int = 0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    prompt_cache_hit_tokens: int = 0,
    model: str = "deepseek-v4-flash",
    source: str = SOURCE_ACTIVE,
    trigger_type: str = None,
) -> None:
    """记录一次 AI 调用（群组 + 可选用户维度，按来源分类）。"""
    if trigger_type is not None:
        source = trigger_type
    source = normalize_source(source)

    now = datetime.now()
    today = now.date().isoformat()
    cost = estimate_cost_cny(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        prompt_cache_hit_tokens=prompt_cache_hit_tokens,
        total_tokens=tokens,
        model=model,
    )
    if tokens <= 0 and (prompt_tokens or completion_tokens):
        tokens = prompt_tokens + completion_tokens

    with _save_lock:
        if group_id:
            _update_stats_file(
                GROUP_DATA_FILE,
                root_key="group_stats",
                entity_id=str(group_id),
                today=today,
                now=now,
                tokens=tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost=cost,
                source=source,
            )
        if user_id:
            _update_user_stats_file(
                USER_DATA_FILE,
                group_id=str(group_id),
                user_id=str(user_id),
                today=today,
                now=now,
                tokens=tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost=cost,
                source=source,
            )


def _increment_stats(
    stats: dict,
    today: str,
    now: datetime,
    tokens: int,
    prompt_tokens: int,
    completion_tokens: int,
    cost: float,
    source: str,
) -> None:
    stats = _ensure_stats_fields(stats)
    for daily_key in (
        "daily_counts",
        "daily_tokens",
        "daily_prompt_tokens",
        "daily_completion_tokens",
        "daily_cost",
    ):
        stats.setdefault(daily_key, {})
        if today not in stats[daily_key]:
            stats[daily_key][today] = 0 if daily_key != "daily_cost" else 0.0

    stats["daily_counts"][today] += 1
    stats["daily_tokens"][today] += tokens
    stats["daily_prompt_tokens"][today] += prompt_tokens
    stats["daily_completion_tokens"][today] += completion_tokens
    stats["daily_cost"][today] = round(stats["daily_cost"][today] + cost, 6)
    stats["total_count"] += 1
    stats["total_tokens"] += tokens
    stats["total_cost"] = round(stats.get("total_cost", 0.0) + cost, 6)
    stats["last_used"] = now.isoformat()

    stats["daily_by_source"].setdefault(today, {})
    bucket = stats["daily_by_source"][today].setdefault(source, _empty_source_bucket())
    bucket["count"] += 1
    bucket["tokens"] += tokens
    bucket["prompt_tokens"] += prompt_tokens
    bucket["completion_tokens"] += completion_tokens
    bucket["cost"] = round(bucket["cost"] + cost, 6)


def _update_stats_file(
    path: str,
    root_key: str,
    entity_id: str,
    today: str,
    now: datetime,
    tokens: int,
    prompt_tokens: int,
    completion_tokens: int,
    cost: float,
    source: str,
) -> None:
    data = _load_json(path, {root_key: {}})
    data.setdefault(root_key, {})
    if entity_id not in data[root_key]:
        data[root_key][entity_id] = _empty_stats()
    _increment_stats(
        data[root_key][entity_id],
        today,
        now,
        tokens,
        prompt_tokens,
        completion_tokens,
        cost,
        source,
    )
    _save_json(path, data)


def _update_user_stats_file(
    path: str,
    group_id: str,
    user_id: str,
    today: str,
    now: datetime,
    tokens: int,
    prompt_tokens: int,
    completion_tokens: int,
    cost: float,
    source: str,
) -> None:
    data = _load_json(path, {"user_stats": {}})
    data.setdefault("user_stats", {})
    data["user_stats"].setdefault(group_id, {})
    if user_id not in data["user_stats"][group_id]:
        data["user_stats"][group_id][user_id] = _empty_stats()
    _increment_stats(
        data["user_stats"][group_id][user_id],
        today,
        now,
        tokens,
        prompt_tokens,
        completion_tokens,
        cost,
        source,
    )
    _save_json(path, data)


def _dates_in_range(daily_counts: dict, days: Optional[int]) -> set:
    if days is None:
        return set(daily_counts.keys())
    cutoff = datetime.now().date()
    dates = set()
    for date_str in daily_counts.keys():
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
            if (cutoff - d).days < days:
                dates.add(date_str)
        except ValueError:
            continue
    return dates


def _sum_by_source(daily_by_source: dict, days: Optional[int]) -> Dict[str, dict]:
    """汇总各来源的 count/tokens/cost。"""
    result: Dict[str, dict] = {}
    dates = set()
    if daily_by_source:
        if days is None:
            dates = set(daily_by_source.keys())
        else:
            cutoff = datetime.now().date()
            for date_str in daily_by_source.keys():
                try:
                    d = datetime.strptime(date_str, "%Y-%m-%d").date()
                    if (cutoff - d).days < days:
                        dates.add(date_str)
                except ValueError:
                    continue

    for date_str in dates:
        for src, bucket in (daily_by_source.get(date_str) or {}).items():
            if src not in result:
                result[src] = _empty_source_bucket()
            for key in ("count", "tokens", "prompt_tokens", "completion_tokens"):
                result[src][key] += int(bucket.get(key, 0))
            result[src]["cost"] = round(result[src]["cost"] + float(bucket.get("cost", 0)), 6)
    return result


def get_rollup_breakdown(stats: dict, days: Optional[int], include_zero: bool = False) -> List[dict]:
    """按展示归类汇总来源统计。"""
    daily_by_source = stats.get("daily_by_source") or {}
    raw = _sum_by_source(daily_by_source, days)
    rows = []
    order = SOURCE_ROLLUP_ORDER if include_zero else list(SOURCE_ROLLUP.keys())
    for rollup_key in order:
        if rollup_key not in SOURCE_ROLLUP:
            continue
        label, sources = SOURCE_ROLLUP[rollup_key]
        row = {"key": rollup_key, "label": label, **_empty_source_bucket()}
        for src in sources:
            bucket = raw.get(src)
            if not bucket:
                continue
            for key in ("count", "tokens", "prompt_tokens", "completion_tokens"):
                row[key] += bucket[key]
            row["cost"] = round(row["cost"] + bucket["cost"], 6)
        if include_zero or row["count"] > 0:
            rows.append(row)
    return rows


def _parse_balance_amount(balance_data: dict) -> Optional[tuple[float, str]]:
    if "error" in balance_data:
        return None
    balance_infos = balance_data.get("balance_infos") or []
    if not balance_infos:
        return None
    for info in balance_infos:
        if info.get("currency") == "CNY":
            return float(info.get("total_balance", 0)), "CNY"
    info = balance_infos[0]
    return float(info.get("total_balance", 0)), info.get("currency", "CNY")


def record_balance_snapshot(balance_data: dict) -> dict:
    parsed = _parse_balance_amount(balance_data)
    if parsed is None:
        return balance_data

    balance, currency = parsed
    now = datetime.now()
    today = now.date().isoformat()

    with _save_lock:
        data = _load_json(
            BALANCE_DATA_FILE,
            {"snapshots": [], "daily_account": {}, "last_snapshot": None},
        )
        data.setdefault("snapshots", [])
        data.setdefault("daily_account", {})

        snapshot = {
            "timestamp": now.isoformat(),
            "total_balance": balance,
            "currency": currency,
            "is_available": balance_data.get("is_available"),
            "balance_infos": balance_data.get("balance_infos", []),
        }

        data["snapshots"].append(snapshot)
        if len(data["snapshots"]) > 500:
            data["snapshots"] = data["snapshots"][-500:]

        daily = data["daily_account"].setdefault(
            today,
            {
                "currency": currency,
                "opening_balance": balance,
                "closing_balance": balance,
                "actual_spend": 0.0,
                "snapshot_count": 0,
            },
        )

        if daily["snapshot_count"] == 0:
            daily["opening_balance"] = balance
        daily["closing_balance"] = balance
        daily["actual_spend"] = round(max(0.0, daily["opening_balance"] - balance), 4)
        daily["snapshot_count"] += 1
        daily["last_updated"] = now.isoformat()

        data["last_snapshot"] = snapshot
        _save_json(BALANCE_DATA_FILE, data)

    return balance_data


def get_balance_summary() -> Dict[str, Any]:
    data = _load_json(BALANCE_DATA_FILE, {})
    today = datetime.now().date().isoformat()
    return {
        "last_snapshot": data.get("last_snapshot"),
        "today_account": (data.get("daily_account") or {}).get(today, {}),
        "daily_account": data.get("daily_account", {}),
    }


def _sum_in_range(
    daily_cost: dict,
    daily_tokens: dict,
    daily_prompt: dict,
    daily_completion: dict,
    daily_counts: dict,
    days: Optional[int],
) -> tuple[float, int, int, int, int]:
    dates = _dates_in_range(daily_counts, days)
    cost = sum(float(daily_cost.get(d, 0)) for d in dates)
    tokens = sum(int(daily_tokens.get(d, 0)) for d in dates)
    prompt = sum(int(daily_prompt.get(d, 0)) for d in dates)
    completion = sum(int(daily_completion.get(d, 0)) for d in dates)
    count = sum(int(daily_counts.get(d, 0)) for d in dates)
    return round(cost, 4), tokens, prompt, completion, count


def _dominant_source_label(by_source: Dict[str, dict]) -> str:
    if not by_source:
        return ""
    best_src = max(by_source.items(), key=lambda x: x[1].get("tokens", 0))[0]
    return source_label(best_src)


def get_user_cost_ranking(
    user_stats: dict,
    group_id: str,
    days: Optional[int],
) -> list[dict]:
    group_users = user_stats.get(group_id, {})
    result = []
    for user_id, stats in group_users.items():
        if isinstance(stats, dict):
            daily_cost = stats.get("daily_cost", {})
            daily_tokens = stats.get("daily_tokens", {})
            daily_prompt = stats.get("daily_prompt_tokens", {})
            daily_completion = stats.get("daily_completion_tokens", {})
            daily_counts = stats.get("daily_counts", {})
            daily_by_source = stats.get("daily_by_source", {})
        else:
            daily_cost = getattr(stats, "daily_cost", {})
            daily_tokens = getattr(stats, "daily_tokens", {})
            daily_prompt = getattr(stats, "daily_prompt_tokens", {})
            daily_completion = getattr(stats, "daily_completion_tokens", {})
            daily_counts = getattr(stats, "daily_counts", {})
            daily_by_source = getattr(stats, "daily_by_source", {})

        cost, tokens, prompt, completion, count = _sum_in_range(
            daily_cost, daily_tokens, daily_prompt, daily_completion, daily_counts, days
        )
        by_source = _sum_by_source(daily_by_source, days)
        if count > 0:
            result.append(
                {
                    "user_id": user_id,
                    "count": count,
                    "tokens": tokens,
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "cost": cost,
                    "by_source": by_source,
                    "dominant_source": _dominant_source_label(by_source),
                }
            )
    result.sort(key=lambda x: (x["cost"], x["tokens"]), reverse=True)
    return result
