"""开箱借贷与背包排序（LN 系列）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "plugins"))

from CaseOpening.store_cs2 import CaseStore
from common.case_data.loan import MAX_LOAN, LoanStore


# LN-01 单次上限；未还清不能借第二笔；还清/重生后可再借
def test_borrow_limits(tmp_path: Path):
    loan = LoanStore(tmp_path / "loan.db")
    assert MAX_LOAN == 1_000_000
    assert loan.borrow("1", MAX_LOAN + 1)["ok"] is False
    first = loan.borrow("1", 500_000)
    assert first["ok"] and first["debt"] == 500_000 and first["credit"] == 500_000
    assert loan.status("1")["can_borrow"] is False

    # 有欠款时不能再借
    second = loan.borrow("1", 1_000)
    assert second["ok"] is False and "还没还清" in second["reason"]

    # 还清后可以再借
    loan.repay("1", 500_000)
    assert loan.status("1")["can_borrow"] is True
    assert loan.borrow("1", 10_000)["ok"] is True

    # 重生后也能再借
    loan.record_rebirth("1")
    assert loan.status("1")["can_borrow"] is True
    loan.close()


# LN-02 额度只能开销：花掉额度不减债务，还款先抵消未花额度
def test_spend_and_repay(tmp_path: Path):
    loan = LoanStore(tmp_path / "loan.db")
    loan.borrow("1", 30_000)
    assert loan.spend_credit("1", 12_000) == 12_000
    status = loan.status("1")
    assert status["credit"] == 18_000 and status["debt"] == 30_000

    result = loan.repay("1", 20_000)
    assert result["paid"] == 20_000
    # 未花额度跟随债务一起收缩（credit 不能大于 debt）
    assert result["debt"] == 10_000 and result["credit"] == 10_000
    loan.close()


# LN-03 额度花不完时按实际额度抵扣
def test_spend_more_than_credit(tmp_path: Path):
    loan = LoanStore(tmp_path / "loan.db")
    loan.borrow("1", 5_000)
    assert loan.spend_credit("1", 8_000) == 5_000
    assert loan.status("1")["credit"] == 0


# LN-04 重生清账
def test_clear(tmp_path: Path):
    loan = LoanStore(tmp_path / "loan.db")
    loan.borrow("1", 10_000)
    loan.clear("1")
    status = loan.status("1")
    assert status["debt"] == 0 and status["credit"] == 0


# LN-05 背包按价格从高到低，序号与展示一致
def test_inventory_sorted_by_price(tmp_path: Path):
    store = CaseStore(tmp_path / "cs.db")
    for price, name in ((1.0, "便宜"), (50.0, "贵"), (10.0, "中")):
        store.add_item(
            "1",
            "9",
            skin_id="s",
            name=name,
            name_cn=name,
            wear="Field-Tested",
            float_value=0.2,
            stattrak=False,
            rarity="Mil-Spec Grade",
            color="#4b69ff",
            market_hash_name=name,
            price_cny=price,
        )
    ordered = store.items_sorted("1")
    assert [row["name"] for row in ordered] == ["贵", "中", "便宜"]
    assert store.clear_user("1") == 3
    assert store.count("1") == 0
    store.close()


# LN-06 历史欠款与重生次数留痕
def test_history_and_rebirth_counter(tmp_path: Path):
    loan = LoanStore(tmp_path / "loan.db")
    loan.borrow("1", 40_000)
    loan.repay("1", 10_000)
    first = loan.record_rebirth("1")
    assert first["debt_before"] == 30_000 and first["rebirths"] == 1

    status = loan.status("1")
    assert status["total_borrowed"] == 40_000
    assert status["total_debt"] == 40_000
    assert status["total_repaid"] == 10_000
    assert status["peak_debt"] == 40_000
    assert status["loan_count"] == 1
    assert status["rebirths"] == 1 and status["last_rebirth_at"] > 0
    assert status["debt"] == 0 and status["credit"] == 0

    kinds = [row["kind"] for row in loan.history("1")]
    assert kinds == ["rebirth", "repay", "borrow"]
    loan.close()


# LN-07 变卖：CS2 背包按回收价折算并清空
def test_cs2_liquidate_clears_items(tmp_path: Path):
    from CaseOpening.cs2 import Cs2Case

    class _NoPrices:
        def sell_price(self, name, fee):
            return None

    module = Cs2Case.__new__(Cs2Case)
    module.store = CaseStore(tmp_path / "cs.db")
    module.prices = _NoPrices()
    for price in (5.0, 10.0):
        module.store.add_item(
            "1",
            "9",
            skin_id="s",
            name="AK-47 | Redline",
            name_cn="AK-47 | 红线",
            wear="Field-Tested",
            float_value=0.2,
            stattrak=False,
            rarity="Covert",
            color="#eb4b4b",
            market_hash_name="AK-47 | Redline (Field-Tested)",
            price_cny=price,
        )
    coins, count = module.liquidate("1")
    assert count == 2
    assert coins == 0  # 无行情时不计价，但物品必须清空
    assert module.store.count("1") == 0
    module.store.close()
