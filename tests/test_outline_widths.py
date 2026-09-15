"""외곽선 폭 재기 — 중심선 + 외곽선 2줄에서 폭별 규칙을 제안한다. 폭만 — 단면 형태·높이는 사람이 채운다."""
import ezdxf

from mep_profile import measure_outline_widths, split_rule_by_outline_widths


def _drawing(tmp_path):
    doc = ezdxf.new(units=4)
    msp = doc.modelspace()

    def duct(a, b, offsets):
        center = msp.add_line(a, b, dxfattribs={"layer": "SA", "color": 1})
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = (dx * dx + dy * dy) ** 0.5
        nx, ny = -dy / length, dx / length
        for off in offsets:
            msp.add_line((a[0] + nx * off, a[1] + ny * off), (b[0] + nx * off, b[1] + ny * off), dxfattribs={"layer": "SA"})
        return center.dxf.handle

    h = {"a": duct((0, 0), (2000, 0), (55, -55)), "b": duct((3000, 0), (3000, 2000), (55, -55)),
         "c": duct((0, 1000), (2000, 1000), (102, -102)), "asym": duct((0, 3000), (2000, 3000), (55, -80)),
         "bare": duct((0, 5000), (2000, 5000), ())}
    msp.add_line((0, 1050), (2000, 1050), dxfattribs={"layer": "RA"})   # 다른 레이어의 선은 그 덕트의 외곽선이 아니다
    path = tmp_path / "vent.dxf"
    doc.saveas(path)
    return str(path), h


def test_centerlines_group_by_the_spacing_of_their_two_outlines(tmp_path):
    path, h = _drawing(tmp_path)
    m = measure_outline_widths(path, {"pattern": "^SA$", "color": 1})
    assert [(g["width_mm"], sorted(r["handle"] for r in g["source_refs"])) for g in m["groups"]] == [
        (110, sorted([h["a"], h["b"]])), (204, [h["c"]])]
    assert {u["source_ref"]["handle"]: u["status"] for u in m["unmeasured"]} == {h["asym"]: "asymmetric", h["bare"]: "no_outline"}


def test_splitting_keeps_the_rule_and_leaves_unmeasured_sources_in_their_own_rule(tmp_path):
    path, h = _drawing(tmp_path)
    row = {"pattern": "^SA$", "category": "duct", "system": "SA", "color": 1, "height_mm": 54.0}
    rules = split_rule_by_outline_widths(row, measure_outline_widths(path, row))
    assert [(r.get("width_mm"), r["source_handles"]) for r in rules] == [
        (110.0, sorted([h["a"], h["b"]])), (204.0, [h["c"]]), (None, sorted([h["asym"], h["bare"]]))]
    assert all(r["color"] == 1 and r["height_mm"] == 54.0 and r["system"] == "SA" for r in rules)


def test_a_round_rule_takes_the_measured_width_as_its_diameter():
    measurement = {"groups": [{"width_mm": 100, "source_refs": [{"handle": "1a", "type": "LINE", "insert_path": []}]}],
                   "unmeasured": []}
    (rule,) = split_rule_by_outline_widths({"pattern": "^RA$", "category": "duct", "system": "RA",
                                            "section_shape": "round", "source_handles": ["FF"]}, measurement)
    assert (rule["diameter_mm"], rule["source_handles"], "width_mm" in rule) == (100.0, ["1A"], False)
