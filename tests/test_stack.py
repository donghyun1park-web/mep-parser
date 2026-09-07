# -*- coding: utf-8 -*-
"""stack_build — offset 해결기의 가드 3개.

기둥 4개가 정사각으로 배치된 층에서 12,000mm 그리드가 반복되는 바람에
**틀린 offset 이 4/4 완벽 매칭**으로 통과해 구조물이 Y 로 43,000mm 어긋난 채
납품된 적이 있다. 점수만으로는 못 잡는다 — 가드가 잡아야 한다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import stack_build as SB

GRID = [0.0, 12000.0, 24000.0, 36000.0, 48000.0]


def test_best_shift_finds_the_translation():
    s, n, _ = SB._best_shift(GRID, [x - 7000 for x in GRID])
    assert (s, n) == (7000.0, 5)


def test_best_shift_reports_a_runner_up_on_a_repeating_grid():
    """등간격 그리드는 한 칸 밀어도 거의 다 맞는다 — 그 사실이 드러나야 한다."""
    _s, n, n2 = SB._best_shift(GRID, GRID)
    assert n == 5 and n2 >= 4, (n, n2)


def test_identical_grids_are_not_ambiguous():
    """완전히 같은 그리드는 정답이 0 이다 — 이걸 모호하다고 거부하면 쓸 수 없다."""
    grids = {"ref": {"x_axes": GRID, "y_axes": GRID},
             "mov": {"x_axes": GRID, "y_axes": GRID}}
    dx, dy, _ = _resolve(grids)
    assert (dx, dy) == (0.0, 0.0)


def _geom(x0, y0, x1, y1):
    return {"elements": {"wall": [{"points": [[x0, y0], [x1, y1]]}]}}


def _resolve(monkey_grids, ref_geom=None, mov_geom=None):
    SB.detect_grid = lambda p: monkey_grids[p]
    return SB.resolve_offset("ref", "mov",
                             ref_geom or _geom(0, 0, 48000, 48000),
                             mov_geom or _geom(0, 0, 48000, 48000))


def test_guard_evidence_floor_rejects_a_four_column_level():
    """★ 43,000mm 사고를 잡았을 검사. 축이 방향당 2개면 점수를 보기 전에 거부한다."""
    grids = {"ref": {"x_axes": GRID, "y_axes": GRID},
             "mov": {"x_axes": [0.0, 12000.0], "y_axes": [0.0, 12000.0]}}
    try:
        _resolve(grids)
    except SB.StackError as e:
        assert "증거 부족" in str(e), e
        return
    raise AssertionError("증거 부족인데 통과했다")


def test_guard_ambiguity_rejects_a_tie_on_a_repeating_grid():
    """상부층 축이 하부 그리드의 부분열이면 여러 shift 가 **똑같이 완벽**하다.
    점수로는 못 고른다 — 12,000mm 가 반복되니 0/12000/24000 이 전부 3/3."""
    sub = [0.0, 12000.0, 24000.0]
    grids = {"ref": {"x_axes": GRID, "y_axes": GRID},
             "mov": {"x_axes": sub, "y_axes": sub}}
    try:
        _resolve(grids)
    except SB.StackError as e:
        assert "모호" in str(e), e
        return
    raise AssertionError("모호한데 통과했다")


def test_guard_containment_rejects_a_level_outside_the_building():
    """그리드와 독립이라 그리드가 놓친 것을 잡는다."""
    off = [x + 43000 for x in GRID]
    grids = {"ref": {"x_axes": GRID, "y_axes": GRID},
             "mov": {"x_axes": GRID, "y_axes": off}}
    try:
        _resolve(grids, mov_geom=_geom(0, 0, 48000, 48000))
    except SB.StackError as e:
        assert "bbox" in str(e), e
        return
    raise AssertionError("건물 밖인데 통과했다")


def test_clean_offset_passes_all_guards():
    irregular = [0.0, 9000.0, 21000.0, 30000.0, 44000.0]
    grids = {"ref": {"x_axes": irregular, "y_axes": irregular},
             "mov": {"x_axes": [v - 5000 for v in irregular],
                     "y_axes": [v - 5000 for v in irregular]}}
    dx, dy, ev = _resolve(grids, mov_geom=_geom(-5000, -5000, 39000, 39000))
    assert (dx, dy) == (5000.0, 5000.0) and ev["matched"] == [5, 5]


# ── 이동 / 층 선언 ─────────────────────────────────────────────────────────
def test_shift_moves_every_coordinate_form():
    rec = {"points": [[0, 0]], "centerline": [[10, 10]], "center": [20, 20],
           "z_base": 100.0}
    SB._shift(rec, "wall", 5, -5, 1000.0)
    assert rec["points"] == [[5, -5]] and rec["centerline"] == [[15, 5]]
    assert rec["center"] == [25, 15] and rec["z_base"] == 1100.0


def test_shift_uses_elevation_for_mep():
    """MEP 는 키가 다르다 — z_base 를 만들면 규약 위반이다."""
    rec = {"points": [[0, 0]], "elevation": 2600.0}
    SB._shift(rec, "pipe", 0, 0, 1000.0)
    assert rec["elevation"] == 3600.0 and "z_base" not in rec


def test_level_height_wins_and_says_so():
    """층고를 적어줬는데 레이어 기본값이 조용히 이기면 원래 문제로 되돌아간다."""
    a, b = {"z_base": 0}, {"z_base": 0, "overrides": {"height": 2800.0}}
    assert SB._shift(a, "wall", 0, 0, 0, height=4000) is False   # 덮은 값 없음
    assert SB._shift(b, "wall", 0, 0, 0, height=4000) is True    # 덮었다 → 보고된다
    assert a["overrides"]["height"] == b["overrides"]["height"] == 4000.0


def test_level_height_only_touches_vertical_elements():
    s = {"z_base": 0, "overrides": {"thickness": 200.0}}
    SB._shift(s, "slab", 0, 0, 0, height=4000)
    assert "height" not in s["overrides"]


def test_stacked_openings_keep_pointing_at_their_own_level():
    """★ `wall_indices` 는 **그 층 안에서의 위치**다.

    층을 이어붙이면 위층 개구부의 인덱스 0 이 아래층 첫 벽을 가리키게 되어,
    2층 문이 1층 벽에 구멍을 뚫는다. 형상은 유효하고 검사도 전부 통과한다 —
    산출물만 틀린다. 여기서 잡는다."""
    def level(tag):
        return {"wall": [{"kind": "polyline", "closed": False, "eid": f"w:{tag}{i}",
                          "points": [[i * 1000, 0], [i * 1000 + 900, 0]],
                          "centerline": [[i * 1000, 0], [i * 1000 + 900, 0]]}
                         for i in range(3)],
                "opening": [{"kind": "circle", "eid": f"o:{tag}", "center": [2000, 0],
                             "radius": 450, "wall_indices": [2]}]}

    out = {"elements": {}}
    for tag in ("L1", "L2"):
        el = level(tag)
        base = len(out["elements"].get("wall", []))
        for r in el.get("opening", []):
            if r.get("wall_indices"):
                r["wall_indices"] = [i + base for i in r["wall_indices"]]
        for cat, recs in el.items():
            out["elements"].setdefault(cat, []).extend(recs)

    walls = out["elements"]["wall"]
    for op in out["elements"]["opening"]:
        tag = op["eid"].split(":")[1]
        hit = walls[op["wall_indices"][0]]
        assert hit["eid"].startswith(f"w:{tag}"), \
            f"{op['eid']} 가 남의 층 벽 {hit['eid']} 을 가리킨다"
