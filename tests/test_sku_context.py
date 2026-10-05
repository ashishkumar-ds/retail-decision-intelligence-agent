"""Tests for the store×SKU extension point (tools/sku_context.py)."""
import pytest

from tools.sku_context import annotate_sku_context, flag_sku_gaps, load_sku_rollup


def _sku(sku="A1", dept="GROCERY", base=100.0, recent=0.0, sales=5000.0, on_hand=None):
    return {"store_id": 317, "sku": sku, "department": dept, "units_56d": base,
            "sales_value_56d": sales, "units_14d": recent, "on_hand": on_hand}


def test_top_seller_stockout_flags_supply_gap():
    skus = [_sku(sku=f"S{i}", base=100.0, recent=50.0, sales=1000.0) for i in range(10)]
    skus.append(_sku(sku="STAR", base=500.0, recent=0.0, sales=50000.0, on_hand=0))
    block = annotate_sku_context(317, skus)
    assert block["demand_vs_supply"] == "POSSIBLE_SUPPLY_GAP"
    assert block["suspected_stockout_skus"][0]["sku"] == "STAR"
    assert block["sku_coverage_ratio"] == pytest.approx(10 / 11, abs=1e-3)


def test_healthy_shelves_read_demand():
    skus = [_sku(sku=f"S{i}", base=100.0, recent=40.0) for i in range(5)]
    block = annotate_sku_context(317, skus)
    assert block["demand_vs_supply"] == "DEMAND"
    assert block["suspected_stockout_skus"] == []


def test_zero_on_hand_confirms_unverified_otherwise():
    confirmed = flag_sku_gaps([_sku(base=50.0, recent=0.0, on_hand=0)])
    assert len(confirmed["suspected_stockouts"]) == 1
    unverified = flag_sku_gaps([_sku(base=50.0, recent=0.0, on_hand=None)])
    assert len(unverified["unverified_gaps"]) == 1
    assert unverified["suspected_stockouts"] == []


def test_no_data_is_none_and_loader_fails_open(tmp_path):
    assert annotate_sku_context(317, None) is None
    assert annotate_sku_context(317, []) is None
    assert load_sku_rollup(tmp_path / "missing.csv") is None
    assert load_sku_rollup(None) is None


def test_loader_reads_valid_rollup(tmp_path):
    path = tmp_path / "rollup.csv"
    path.write_text(
        "STORE_ID,SKU,DEPARTMENT,units_56d,sales_value_56d,units_14d,on_hand\n"
        "317,A1,GROCERY,100,5000,40,12\n"
        "317,A2,GROCERY,80,3000,0,0\n"
        "BAD,ROW\n"
    )
    by_store = load_sku_rollup(path)
    assert len(by_store[317]) == 2
    with pytest.raises(TypeError):
        from tools.sku_context import get_store_skus
        get_store_skus("317")
