"""dashboard_index の前日比ヘルパー `_latest_change` の単体テスト。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.dashboard import _latest_change


def main() -> None:
    # データ不足
    assert _latest_change([]) == (None, None)
    assert _latest_change([{"close": 100}]) == (None, None)

    # 正常系: 末尾2件から (prev_close, change_pct)
    prev, chg = _latest_change([{"close": 100}, {"close": 103}])
    assert prev == 100.0
    assert chg is not None and abs(chg - 0.03) < 1e-9

    # 3件以上でも末尾2件を使う
    prev, chg = _latest_change([{"close": 200}, {"close": 100}, {"close": 103}])
    assert prev == 100.0

    # 欠損・ゼロ割り・bool 混入は (None, None)
    assert _latest_change([{"close": None}, {"close": 103}]) == (None, None)
    assert _latest_change([{"close": 100}, {"close": None}]) == (None, None)
    assert _latest_change([{"close": 0}, {"close": 103}]) == (None, None)
    assert _latest_change([{"close": True}, {"close": 103}]) == (None, None)
    assert _latest_change([{}, {"close": 103}]) == (None, None)

    # last 側の異常値も対称に弾く
    assert _latest_change([{"close": 100}, {"close": True}]) == (None, None)
    assert _latest_change([{"close": 100}, {}]) == (None, None)

    # 非有限値 (NaN/inf) も (None, None)
    assert _latest_change([{"close": float("nan")}, {"close": 103}]) == (None, None)
    assert _latest_change([{"close": 100}, {"close": float("inf")}]) == (None, None)

    print("OK: test_dashboard_index_change")


if __name__ == "__main__":
    main()
