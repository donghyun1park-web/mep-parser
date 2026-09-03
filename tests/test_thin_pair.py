# -*- coding: utf-8 -*-
"""얇은 오결합 감지 — 조용히 틀린 두께를 검토 대상으로 올린다.

실측(지하3층 A-CON): 두께 50mm 짜리 벽 55개가 paired·confidence 0.9·
needs_review=False 로 나갔다. 참값은 250~450mm. 페어링이 수직거리가 가까운 쌍부터
구간을 선점하므로, 벽면 옆 마감선과 이룬 가짜 쌍이 진짜 두께를 막은 것이다.

★ 절대 임계가 아니라 레이어 중앙값 대비여야 한다. 그래야 A-STEEL 처럼 레이어
  전체가 얇은 경우를 오탐하지 않는다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dxf_parser as dp


def _walls(widths, layer="A-CON"):
    return [{"kind": "polyline", "closed": False, "layer": layer,
             "pairing": "paired", "width_detected": float(w), "needs_review": False,
             "points": [[0, i * 1000], [5000, i * 1000]],
             "centerline": [[0, i * 1000], [5000, i * 1000]]}
            for i, w in enumerate(widths)]


def _run(walls):
    """parse() 의 감지 블록만 떼어 돌리는 대신, 같은 규칙을 여기서 재현하지 않고
    실제 파서 경로를 쓰기 위해 최소 result 를 만들어 통과시킨다."""
    import io
    import contextlib
    import ezdxf
    import tempfile
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    for w in walls:                      # 양면 2선으로 그려 페어링이 실제로 일어나게
        (x0, y0), (x1, _y1) = w["points"]
        t = w["width_detected"]
        msp.add_line((x0, y0), (x1, y0), dxfattribs={"layer": w["layer"]})
        msp.add_line((x0, y0 + t), (x1, y0 + t), dxfattribs={"layer": w["layer"]})
    p = os.path.join(tempfile.gettempdir(), "_thin_test.dxf")
    doc.saveas(p)
    rules = [("^" + walls[0]["layer"] + "$", "wall", {})]
    with contextlib.redirect_stdout(io.StringIO()):
        return dp.parse(p, rules, block_rules=[])


def test_minority_thin_pair_is_flagged():
    d = _run(_walls([250] * 8 + [50, 50]))
    thin = [w for w in d["elements"]["wall"] if w.get("review_reason") == "thin_pair"]
    assert len(thin) == 2, [w.get("width_detected") for w in d["elements"]["wall"]]
    assert all(w["needs_review"] for w in thin)
    assert d["thin_pairs"] == {"A-CON": 2}


def test_uniformly_thin_layer_is_not_flagged():
    """A-STEEL 은 전부 30mm 다 — 중앙값도 30 이라 하나도 안 걸려야 한다.
    절대 임계로 만들었으면 27개가 통째로 오탐이었다."""
    d = _run(_walls([30] * 10, layer="A-STEEL"))
    assert "thin_pairs" not in d, d.get("thin_pairs")
    assert not any(w.get("review_reason") == "thin_pair"
                   for w in d["elements"]["wall"])


def test_small_sample_is_not_judged():
    """표본이 적으면 중앙값을 믿지 않는다(레이어에 벽 3개뿐인 경우 등)."""
    d = _run(_walls([250, 250, 50]))
    assert "thin_pairs" not in d, d.get("thin_pairs")


def test_warning_names_the_layer_and_the_remedy():
    d = _run(_walls([250] * 8 + [50, 50]))
    w = [x for x in d["warnings"] if "얇은 오결합" in x]
    assert w and "A-CON" in w[0] and "pair_min=" in w[0], d["warnings"]
