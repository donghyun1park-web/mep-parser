# -*- coding: utf-8 -*-
"""레이어가 **벽처럼 그려졌는가** 는 이름이 아니라 평행 짝이 말한다.

실측(아파트 단위세대 평면도): `A-COL` 이 200mm 내력벽인데 기본 규칙이 기둥으로 보내 미해결 기둥 43개가
검토 대기를 채웠고, 칸막이 `Parti` 는 미매핑으로 조용히 버려졌다. 프로그램이 그 사실을 말하지 않았다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ezdxf
import dxf_parser as P
import verify


def _drawing(tmp_path, name, lines, layer, closed=()):
    doc = ezdxf.new(units=4)
    doc.layers.new(layer)
    ms = doc.modelspace()
    for a, b in lines:
        ms.add_line(a, b, dxfattribs={"layer": layer})
    for pts in closed:
        ms.add_lwpolyline(pts, close=True, dxfattribs={"layer": layer})
    path = tmp_path / name
    doc.saveas(path)
    return str(path)


def _wall_pairs(count, gap, y0=0.0, length=3000.0):
    """간격 `gap` 인 면선 쌍 `count` 개. 벽처럼 그린 레이어다."""
    out = []
    for i in range(count):
        y = y0 + i * 2000.0
        out.append(((0, y), (length, y)))
        out.append(((0, y + gap), (length, y + gap)))
    return out


def _parse(path):
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        return P.parse(path, P.load_layer_map("layer_map.csv"),
                       block_rules=P.load_layer_map("block_map.csv"))


def test_evidence_separates_wall_pairs_from_scattered_lines(tmp_path):
    walls = [{"kind": "polyline", "points": [list(a), list(b)], "layer": "X"}
             for a, b in _wall_pairs(6, 200.0)]
    ev = P.layer_evidence(walls)
    assert ev["wall_like"] and ev["spacing_mm"] == 200
    assert ev["paired"] == ev["lines"] and ev["ratio"] == 1.0
    scattered = [{"kind": "polyline", "points": [[0, i * 777.0], [1000 + i * 13, i * 777.0 + 900]], "layer": "X"}
                 for i in range(12)]
    assert P.layer_evidence(scattered)["wall_like"] is False


def test_evidence_uses_the_partition_guard_so_board_lines_do_not_hide_the_wall(tmp_path):
    """칸막이는 벽면 안쪽 10mm 에 보드선을 더 긋는다 — 전역 최소 간격(1mm)으로 재면 그 짝이 먼저 먹는다.

    실측(`Parti` 74선): 가드 없이는 10mm 42쌍 · 100mm 1쌍이라 '벽 아님' 이 됐다."""
    lines = []
    for i in range(5):
        y = i * 2000.0
        for offset in (0.0, 10.0, 90.0, 100.0):      # 바깥면·보드선 · 보드선·바깥면
            lines.append({"kind": "polyline", "points": [[0, y + offset], [3000, y + offset]], "layer": "P"})
    assert P.layer_evidence(lines, pair_min=1.0)["spacing_mm"] == 10     # 가드가 없으면 보드선이 이긴다
    ev = P.layer_evidence(lines)                                        # 기본 가드 50mm
    # 그리디 페어링은 바깥면(100) 대신 보드선-면선 짝(90)을 고를 수 있다 — 한 벽이 되는 것까지만 보장한다
    # (결정 기록의 칸막이 절). 보드선 간격(10)을 벗어나 벽 두께 범위로 올라온 것이 요점이다.
    assert ev["spacing_mm"] in (90, 100) and ev["wall_like"]


def test_a_few_closed_shapes_do_not_disqualify_a_wall_layer():
    recs = [{"kind": "polyline", "points": [list(a), list(b)], "layer": "P"} for a, b in _wall_pairs(8, 100.0)]
    recs.append({"kind": "polyline", "closed": True, "layer": "P",
                 "points": [[0, 0], [100, 0], [100, 100], [0, 100]]})
    assert P.layer_evidence(recs)["wall_like"]
    columns = [{"kind": "polyline", "closed": True, "layer": "C",
                "points": [[x, 0], [x + 400, 0], [x + 400, 400], [x, 400]]} for x in range(0, 4000, 1000)]
    assert P.layer_evidence(columns)["wall_like"] is False


def test_a_column_layer_drawn_as_walls_says_so_and_offers_the_layer_map_row(tmp_path):
    path = _drawing(tmp_path, "col.dxf", _wall_pairs(6, 200.0), "A-COL")
    data = _parse(path)
    assert data["elements"]["wall"] == []                      # 재분류하지 않는다
    (like,) = data["column_layers_like_wall"]
    assert like["layer"] == "A-COL" and like["spacing_mm"] == 200
    assert like["suggested_row"] == "^A\\-COL$,wall,,2800,"
    assert data["warnings"][0].startswith("[분류 의심]") and "A-COL" in data["warnings"][0]
    assert {r["review_reason"] for r in data["elements"]["column"]} == {"column_layer_looks_like_wall"}
    # 게이트 문구가 원인을 가리킨다 — 종전엔 '기둥 경계' 만 말해 사람이 기둥을 들여다봤다.
    v005 = [f for f in verify.verify_geometry(data).findings if f.id == "V005"]
    assert v005 and "벽처럼 보인다" in v005[0].payload["sample"][0]["why"]
    assert v005[0].payload["by_layer"] == {"A-COL": len(data["elements"]["column"])}


def test_a_real_column_layer_is_left_alone(tmp_path):
    squares = [[(x, 0), (x + 400, 0), (x + 400, 400), (x, 400)] for x in range(0, 4000, 1000)]
    path = _drawing(tmp_path, "real.dxf", [], "A-COL", closed=squares)
    data = _parse(path)
    assert "column_layers_like_wall" not in data
    assert not [w for w in data["warnings"] if "분류 의심" in w]
    assert len(data["elements"]["column"]) == 4


def test_an_unmapped_wall_layer_is_suggested_with_evidence_but_never_applied(tmp_path):
    path = _drawing(tmp_path, "unmapped.dxf", _wall_pairs(6, 100.0), "ZZ9")
    data = _parse(path)
    (sug,) = [s for s in data["suggestions"] if s["layer"] == "ZZ9"]
    assert sug["geom_guess"] == "wall" and sug["evidence"]["spacing_mm"] == 100
    assert "100mm" in sug["geom_reason"] and "짝" in sug["geom_reason"]
    # 자동 적용 문턱은 0.8 **초과** 다 — 증거 제안은 상한이 0.8 이라 절대 자동으로 들어가지 않는다.
    assert sug["geom_confidence"] <= 0.8
    assert data["elements"].get("wall") in (None, [])
    assert data["qa"]["wall_like_unmapped_layers"] == ["ZZ9"]
    assert data["qa"]["wall_like_unmapped_m"] > 0


def test_scattered_lines_are_no_longer_called_walls(tmp_path):
    """종전엔 열린 선이면 전부 `wall(0.45)` 라 마감선·해치·안내선까지 같은 말을 했다."""
    lines = [((0, i * 900.0), (1500 + i * 17, i * 900.0 + 1200)) for i in range(10)]
    path = _drawing(tmp_path, "fin.dxf", lines, "FIN9")
    (sug,) = [s for s in _parse(path)["suggestions"] if s["layer"] == "FIN9"]
    assert sug["geom_guess"] is None and "벽 아님" in sug["geom_reason"]


def test_shapes_that_are_not_geometry_are_counted_once_not_warned_each(tmp_path):
    doc = ezdxf.new(units=4)
    doc.layers.new("Roomtxt9")
    for i in range(5):
        doc.modelspace().add_text("방 %d" % i, dxfattribs={"layer": "Roomtxt9"}).set_placement((0, i * 500))
    path = tmp_path / "txt.dxf"
    doc.saveas(path)
    data = _parse(str(path))
    assert data["unhandled"] == {"TEXT @ Roomtxt9": 5}
    assert len([w for w in data["warnings"] if "unhandled" in w]) == 1


def test_evidence_on_a_huge_layer_is_sampled_and_says_so():
    recs = [{"kind": "polyline", "points": [list(a), list(b)], "layer": "X"}
            for a, b in _wall_pairs(P.WALL_LIKE_MAX_LINES, 200.0)]
    ev = P.layer_evidence(recs)
    assert ev["lines"] == 2 * P.WALL_LIKE_MAX_LINES
    assert ev["sampled_longest"] == P.WALL_LIKE_MAX_LINES      # 전수가 아니라 표본이라고 말한다


def test_a_geometry_only_guess_is_never_auto_applied(tmp_path):
    """'AI auto-classify' 는 AI 분류 보조다 — 기하 투표만인 판정은 검토로 남긴다.

    실측(단위세대 평면도, 체크 ON): 설계변경 표 블록이 '소형 정사각 닫힘폴리 → column 0.85' 로
    문턱 0.8 을 넘어 자동 적용돼 **기둥 43 → 139** 가 됐다. LLM 은 부르지도 않았다
    (`llm_tiebreak_suggestions` 가 geom >= 0.7 을 건너뛴다)."""
    doc = ezdxf.new(units=4)
    doc.layers.new("ZZTABLE9")
    block = doc.blocks.new(name="ZZNOTE9")
    for i in range(6):                       # 표 칸 — 작은 정사각 닫힌 폴리선
        x = i * 500
        block.add_lwpolyline([(x, 0), (x + 400, 0), (x + 400, 350), (x, 350)],
                             close=True, dxfattribs={"layer": "ZZTABLE9"})
    doc.modelspace().add_blockref("ZZNOTE9", (0, 0), dxfattribs={"layer": "ZZTABLE9"})
    path = tmp_path / "note.dxf"
    doc.saveas(path)

    import contextlib
    import io
    rules, brules = P.load_layer_map("layer_map.csv"), P.load_layer_map("block_map.csv")
    with contextlib.redirect_stdout(io.StringIO()):
        data = P.parse(str(path), rules, block_rules=brules, use_ai=True)
    (sug,) = [s for s in data["suggestions"] if s["layer"] == "ZZNOTE9"]
    assert sug["geom_guess"] == "column" and sug["geom_confidence"] > 0.8    # 기하는 확신한다
    assert sug["decided_by"] == "geom" and sug.get("applied") is not True    # 그래도 적용하지 않는다
    assert sug["needs_review"] is True
    assert data["elements"].get("column") in (None, [])
