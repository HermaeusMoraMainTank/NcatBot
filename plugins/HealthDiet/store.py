from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .calculations import sum_items

_BMI_BAN_THRESHOLD = 80.0
_ITEM_CALORIES_BAN_THRESHOLD = 10_000.0
_DAILY_CALORIES_BAN_THRESHOLD = 50_000.0
_ITEM_GRAMS_BAN_THRESHOLD = 50_000.0
_COUNT_QUANTITY_BAN_THRESHOLD = 100.0
_COUNT_UNITS = {"个", "只", "块", "杯", "听", "碗", "份", "包", "瓶", "斤", "条"}
_ABNORMAL_TEXT_MARKERS = (
    "铀",
    "钚",
    "农药",
    "毒药",
    "毒物",
    "炸药",
    "鲨鱼",
    "白龙马",
    "齐天大圣",
    "猪八戒",
    "唐僧",
    "拉拉肥",
    "拉蜜图",
    "猪小仙",
    "野生狗奶",
    "狗奶",
    "非洲之心",
    "大洲",
    "母肥",
)


class HealthStore:
    def __init__(self, path: Path):
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(str(path), check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    user_id TEXT PRIMARY KEY, display_name TEXT NOT NULL DEFAULT '', sex TEXT NOT NULL,
                    age REAL NOT NULL, height_cm REAL NOT NULL, weight_kg REAL NOT NULL,
                    occupation TEXT NOT NULL DEFAULT '', activity_level TEXT NOT NULL DEFAULT 'sedentary',
                    activity_description TEXT NOT NULL DEFAULT '', commute_km REAL NOT NULL DEFAULT 0,
                    goal TEXT NOT NULL DEFAULT '维持', target_kcal REAL NOT NULL DEFAULT 0,
                    bmr_kcal REAL NOT NULL DEFAULT 0, tdee_kcal REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS profile_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
                    effective_date TEXT NOT NULL, profile_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS meals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
                    local_date TEXT NOT NULL, recorded_at TEXT NOT NULL,
                    meal_type TEXT NOT NULL, raw_text TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'ai'
                );
                CREATE TABLE IF NOT EXISTS meal_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, meal_id INTEGER NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
                    name TEXT NOT NULL, quantity REAL NOT NULL, unit TEXT NOT NULL, grams REAL NOT NULL,
                    calories_kcal REAL NOT NULL, protein_g REAL NOT NULL, fat_g REAL NOT NULL,
                    carbs_g REAL NOT NULL, confidence TEXT NOT NULL, assumptions TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS permanent_bans (
                    user_id TEXT PRIMARY KEY, reason TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_meals_user_date ON meals(user_id, local_date);
                CREATE INDEX IF NOT EXISTS idx_meals_user_recorded ON meals(user_id, recorded_at);
                """
            )

    def is_banned(self, user_id: str) -> bool:
        with self._lock:
            row = self.connection.execute("SELECT 1 FROM permanent_bans WHERE user_id = ?", (str(user_id),)).fetchone()
        return row is not None

    def permanent_ban(self, user_id: str, reason: str) -> bool:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._lock, self.connection:
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO permanent_bans (user_id, reason, created_at) VALUES (?, ?, ?)",
                (str(user_id), str(reason), now),
            )
        return cursor.rowcount > 0

    def unban(self, user_id: str) -> bool:
        with self._lock, self.connection:
            cursor = self.connection.execute(
                "DELETE FROM permanent_bans WHERE user_id = ?", (str(user_id),)
            )
        return cursor.rowcount > 0

    def audit_abnormal_users(self) -> dict[str, list[str]]:
        """Permanently block users whose stored health data is clearly impossible."""
        reasons: dict[str, list[str]] = {}
        with self._lock:
            profiles = self.connection.execute("SELECT user_id, height_cm, weight_kg FROM profiles").fetchall()
            for row in profiles:
                height = float(row["height_cm"] or 0)
                weight = float(row["weight_kg"] or 0)
                reason = self.profile_abnormal_reason({"height_cm": height, "weight_kg": weight})
                if reason:
                    reasons.setdefault(str(row["user_id"]), []).append(reason)

            item_rows = self.connection.execute(
                """
                SELECT m.user_id, m.local_date, m.raw_text, i.quantity, i.unit, i.grams, i.calories_kcal
                FROM meals AS m JOIN meal_items AS i ON i.meal_id = m.id
                """
            ).fetchall()
            daily_kcal: dict[tuple[str, str], float] = {}
            for row in item_rows:
                user_id = str(row["user_id"])
                calories = float(row["calories_kcal"] or 0)
                daily_key = (user_id, str(row["local_date"]))
                daily_kcal[daily_key] = daily_kcal.get(daily_key, 0.0) + calories
                quantity = float(row["quantity"] or 0)
                unit = str(row["unit"] or "")
                reason = self.meal_abnormal_reason(str(row["raw_text"] or ""), [{"quantity": quantity, "unit": unit, "grams": row["grams"], "calories_kcal": calories}])
                if reason:
                    reasons.setdefault(user_id, []).append(reason)
            for (user_id, local_date), calories in daily_kcal.items():
                if calories >= _DAILY_CALORIES_BAN_THRESHOLD:
                    reasons.setdefault(user_id, []).append(f"{local_date} 单日热量 {calories:g} kcal 明显异常")

        for user_id, user_reasons in reasons.items():
            self.permanent_ban(user_id, "；".join(dict.fromkeys(user_reasons)))
        return reasons

    @staticmethod
    def profile_abnormal_reason(profile: dict[str, Any]) -> str | None:
        height = float(profile.get("height_cm") or 0)
        weight = float(profile.get("weight_kg") or 0)
        bmi = weight / ((height / 100) ** 2) if height > 0 else 0
        if bmi >= _BMI_BAN_THRESHOLD:
            return f"档案 BMI={bmi:.1f} 明显异常"
        return None

    @staticmethod
    def meal_abnormal_reason(raw_text: str, items: list[dict[str, Any]]) -> str | None:
        text = str(raw_text or "")
        for marker in _ABNORMAL_TEXT_MARKERS:
            if marker in text:
                return f"饮食描述包含明显异常内容：{marker}"
        for item in items:
            quantity = float(item.get("quantity") or 0)
            unit = str(item.get("unit") or "")
            grams = float(item.get("grams") or 0)
            calories = float(item.get("calories_kcal") or 0)
            if quantity >= _COUNT_QUANTITY_BAN_THRESHOLD and unit in _COUNT_UNITS:
                return f"单项食物数量 {quantity:g}{unit} 明显异常"
            if grams >= _ITEM_GRAMS_BAN_THRESHOLD:
                return f"单项食物重量 {grams:g} g 明显异常"
            if calories >= _ITEM_CALORIES_BAN_THRESHOLD:
                return f"单项食物热量 {calories:g} kcal 明显异常"
        return None

    def profile(self, user_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.connection.execute("SELECT * FROM profiles WHERE user_id = ?", (str(user_id),)).fetchone()
        return dict(row) if row else None

    def save_profile(self, user_id: str, profile: dict[str, Any], effective_date: str) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        keys = ("display_name", "sex", "age", "height_cm", "weight_kg", "occupation", "activity_level", "activity_description", "commute_km", "goal", "target_kcal", "bmr_kcal", "tdee_kcal")
        values = [profile.get(key, "") for key in keys]
        with self._lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO profiles (user_id, display_name, sex, age, height_cm, weight_kg, occupation,
                    activity_level, activity_description, commute_km, goal, target_kcal, bmr_kcal, tdee_kcal, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET display_name=excluded.display_name, sex=excluded.sex,
                    age=excluded.age, height_cm=excluded.height_cm, weight_kg=excluded.weight_kg,
                    occupation=excluded.occupation, activity_level=excluded.activity_level,
                    activity_description=excluded.activity_description, commute_km=excluded.commute_km,
                    goal=excluded.goal, target_kcal=excluded.target_kcal, bmr_kcal=excluded.bmr_kcal,
                    tdee_kcal=excluded.tdee_kcal, updated_at=excluded.updated_at
                """,
                [str(user_id), *values, now],
            )
            self.connection.execute(
                "INSERT INTO profile_snapshots (user_id, effective_date, profile_json, created_at) VALUES (?, ?, ?, ?)",
                (str(user_id), effective_date, json.dumps(profile, ensure_ascii=False), now),
            )

    def add_meal(self, user_id: str, local_date: str, meal_type: str, raw_text: str, items: list[dict[str, Any]], recorded_at: str | None = None) -> int:
        recorded_at = recorded_at or datetime.now(UTC).isoformat(timespec="seconds")
        with self._lock, self.connection:
            cursor = self.connection.execute(
                "INSERT INTO meals (user_id, local_date, recorded_at, meal_type, raw_text) VALUES (?, ?, ?, ?, ?)",
                (str(user_id), local_date, recorded_at, meal_type, raw_text),
            )
            meal_id = int(cursor.lastrowid)
            self.connection.executemany(
                """
                INSERT INTO meal_items (meal_id, name, quantity, unit, grams, calories_kcal, protein_g, fat_g, carbs_g, confidence, assumptions)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [(meal_id, item["name"], item["quantity"], item["unit"], item["grams"], item["calories_kcal"], item["protein_g"], item["fat_g"], item["carbs_g"], item["confidence"], item["assumptions"]) for item in items],
            )
        return meal_id

    def _meal_rows(self, user_id: str, where: str = "", params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._lock:
            meals = self.connection.execute(f"SELECT * FROM meals WHERE user_id = ? {where} ORDER BY recorded_at, id", (str(user_id), *params)).fetchall()
            result = []
            for meal in meals:
                items = self.connection.execute("SELECT * FROM meal_items WHERE meal_id = ? ORDER BY id", (meal["id"],)).fetchall()
                item_dicts = [dict(item) for item in items]
                result.append({**dict(meal), "items": item_dicts, "totals": sum_items(item_dicts)})
        return result

    def meals_for_date(self, user_id: str, local_date: str) -> list[dict[str, Any]]:
        return self._meal_rows(user_id, "AND local_date = ?", (local_date,))

    def last_meal(self, user_id: str) -> dict[str, Any] | None:
        rows = self._meal_rows(user_id)
        return rows[-1] if rows else None

    def delete_meal(self, user_id: str, meal_id: int) -> bool:
        with self._lock, self.connection:
            cursor = self.connection.execute("DELETE FROM meals WHERE id = ? AND user_id = ?", (meal_id, str(user_id)))
        return cursor.rowcount > 0

    def totals_for_date(self, user_id: str, local_date: str) -> dict[str, float]:
        return sum_items([item for meal in self.meals_for_date(user_id, local_date) for item in meal["items"]])

    def daily_rows(self, user_id: str, dates: list[str]) -> list[dict[str, Any]]:
        return [{"local_date": local_date, **self.totals_for_date(user_id, local_date)} for local_date in dates]

    def close(self) -> None:
        with self._lock:
            self.connection.close()
