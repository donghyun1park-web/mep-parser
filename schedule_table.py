# -*- coding: utf-8 -*-
"""부재일람표(MEMBER LIST) 추출 — DXF 의 TEXT 격자를 표로 복원한다.

실무 구조도면은 부재의 **위치**를 도면에, **단면**을 일람표에 나눠 적는다.
도면에서 'RAG11B' 라는 이름만 읽어서는 춤·폭을 알 수 없고, 일람표를 봐야
`400x1800x25x25` 를 얻는다. 종전에는 이 매핑을 사람이 읽어 스크립트에
하드코딩했다 — 층마다 다시, 오타 검증 없이.

이 모듈은 그 표를 결정론적으로 복원한다. AI 없음, 추측 없음.

핵심 관찰(실측 도면):
  · TEXT 는 halign/valign 이 설정돼 있으면 insert 가 아니라 **align_point** 가
    실제 앵커다. 앵커를 쓰면 헤더와 데이터 셀의 x 가 **정확히 일치**해
    열 클러스터링이 오차 없이 끝난다. insert 로 하면 중앙정렬 텍스트가
    길이에 따라 좌우로 흔들려 열이 섞인다.
  · 한 레이어에 표가 여러 개 있다. 실측 도면의 `BEAM_SCHEDULE` 레이어에는
    TEXT 291개가 **표 8개**(STEEL ×4, AU ×3, SRC COLUMN ×1)를 이룬다.
    단일 표 파서는 이들을 조용히 병합해 헤더 행을 데이터로 만든다.
  · 표기 규약이 표마다 다르다. `H 582x300x12/17` 은 **춤×폭**,
    `350x1100x8x8` 은 **폭×춤**이다. 뒤집으면 납작한 보가 나온다.

CLI:
    python schedule_table.py <도면.dxf> --layer BEAM_SCHEDULE
    python schedule_table.py <도면.dxf> --layer BEAM_SCHEDULE --json out.json
"""
import json
import re

# ── 열 라벨 인식 ───────────────────────────────────────────────────────────
# 표마다 헤더 문구가 다르므로 라벨 매칭은 정규식으로 느슨하게, 단 **못 찾으면
# 위치 폴백**(맨 왼쪽 = 이름)을 쓰고 그 사실을 표에 기록한다.
_LBL = {
    "name":     re.compile(r"NAME|명\s*칭|부\s*호|기\s*호|MARK|부\s*재", re.I),
    "size":     re.compile(r"SIZE|단\s*면|치\s*수|SECTION|규\s*격", re.I),
    "material": re.compile(r"MATERIAL|재\s*질|강\s*종|STEEL\s*GRADE", re.I),
    "type":     re.compile(r"^\s*TYPE\s*$|형\s*식|종\s*별", re.I),
}
_TITLE_RE = re.compile(r"MEMBER\s*LIST|일\s*람\s*표|SCHEDULE|LIST\s*$", re.I)


class ScheduleError(ValueError):
    """일람표 구조를 신뢰할 수 없을 때."""


# ── 단면 표기 파싱 ─────────────────────────────────────────────────────────
# 프리픽스가 붙은 H형강 계열은 '춤×폭' 관례(KS/JIS), 프리픽스 없는 순수 숫자는
# 이 도면군의 AU(조립) 표기로 '폭×춤'이다. 이 둘을 뒤집으면 1100 깊이 보가
# 1100 폭 × 350 깊이의 납작한 판이 된다 — 조용히 틀리는 종류의 오류라
# 결과에 `notation` 을 남겨 산출물이 스스로 어떤 관례를 썼는지 말하게 한다.
_DEPTH_FIRST_SHAPES = ("H", "BH", "TH", "RH", "CH", "SH", "WH", "HW", "HN")
_NUM = r"\d+(?:\.\d+)?"
_BODY_RE = re.compile(r"^%s(?:\s*[x/]\s*%s)+$" % (_NUM, _NUM))
_HEAD_RE = re.compile(r"^(?:(\d+)\s*-\s*)?([A-Za-z]{1,3})?\s*(.+)$")


def parse_section(text):
    """단면 표기 → {b, h, tw, tf, shape, notation, ...}. 못 읽으면 None.

    지원 표기(실측):
        'H 582x300x12/17'          압연 H — 춤×폭×웨브/플랜지
        'BH 900x350x16x30'         조립 H — 춤×폭×웨브×플랜지
        '350x1100x8x8'             AU C형 — 폭×춤×t×t
        '320x800x8'                AU B형 — 폭×춤×t
        '1200x1200 / BH 500x500x15x25'  SRC — RC 외곽 / 내부 철골

    **추측하지 않는다**: 숫자가 하나뿐이거나(Ø500) 형식이 안 맞으면 None 을
    돌려주고, 호출자가 needs_review 로 올린다. 기본값으로 때우지 않는다.
    """
    if not text:
        return None
    t = str(text).strip().replace("×", "x").replace("X", "x")
    if not t or t in ("-", "—"):
        return None

    # ' / ' (공백 있는 슬래시)만 복합단면 구분자다. 'x12/17' 의 슬래시는 아니다.
    parts = re.split(r"\s+/\s+", t)
    main, core_txt = parts[0], (parts[1] if len(parts) > 1 else None)

    m = _HEAD_RE.match(main)
    if not m:
        return None
    body = m.group(3).strip()
    if not _BODY_RE.match(body):
        return None
    nums = [float(x) for x in re.findall(_NUM, body)]
    if len(nums) < 2:
        return None

    shape = (m.group(2) or "").upper() or None
    if shape and shape in _DEPTH_FIRST_SHAPES:
        notation, h, b = "DxB", nums[0], nums[1]
    else:
        notation, b, h = "BxD", nums[0], nums[1]

    out = {"raw": str(text).strip(), "shape": shape, "notation": notation,
           "b": b, "h": h, "nums": nums,
           "mult": int(m.group(1)) if m.group(1) else 1}
    if len(nums) > 2:
        out["tw"] = nums[2]
    if len(nums) > 3:
        out["tf"] = nums[3]
    if core_txt:
        out["core"] = parse_section(core_txt)
    # 폭이 춤보다 크면 표기 관례를 뒤집어 읽었을 가능성 — 기둥은 정상이므로
    # 경고 여부는 호출자가 카테고리를 보고 판단한다.
    out["wide"] = b > h * 1.05
    return out


# ── DXF → 표 ──────────────────────────────────────────────────────────────
def _anchor(e):
    """TEXT/MTEXT 의 실제 앵커점. 정렬이 설정돼 있으면 align_point 가 진짜다."""
    if e.dxftype() == "MTEXT":
        p = e.dxf.insert
        return float(p.x), float(p.y)
    ha = int(getattr(e.dxf, "halign", 0) or 0)
    va = int(getattr(e.dxf, "valign", 0) or 0)
    if ha or va:
        ap = getattr(e.dxf, "align_point", None)
        if ap is not None:
            return float(ap.x), float(ap.y)
    p = e.dxf.insert
    return float(p.x), float(p.y)


def _text_of(e):
    if e.dxftype() == "MTEXT":
        s = e.text
        s = re.sub(r"\\P|\\p[^;]*;", " ", s)        # 문단 구분
        s = re.sub(r"\{|\}|\\[A-Za-z][^;\\]*;?", "", s)   # 서식 코드
        return s.strip()
    return (e.dxf.text or "").strip()


def _median(xs):
    s = sorted(xs)
    n = len(s)
    return 0.0 if not n else (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0)


def collect_cells(msp, layers):
    """지정 레이어의 TEXT/MTEXT → [(x, y, text, height)] (도면 원좌표)."""
    want = set(layers)
    out = []
    for e in msp.query("TEXT MTEXT"):
        if e.dxf.layer not in want:
            continue
        s = _text_of(e)
        if not s:
            continue
        x, y = _anchor(e)
        h = float(getattr(e.dxf, "height", 0) or getattr(e.dxf, "char_height", 0) or 1.0)
        out.append((x, y, s, h))
    return out


def group_rows(cells, tol=None):
    """y 앵커로 행 묶기. tol 미지정이면 글자높이의 0.4배."""
    if not cells:
        return []
    if tol is None:
        tol = max(1e-6, 0.4 * _median([c[3] for c in cells]))
    rows, cur, cur_y = [], [], None
    for c in sorted(cells, key=lambda c: (-c[1], c[0])):
        if cur_y is None or abs(cur_y - c[1]) <= tol:
            cur.append(c)
            cur_y = c[1] if cur_y is None else cur_y
        else:
            rows.append(sorted(cur, key=lambda c: c[0]))
            cur, cur_y = [c], c[1]
    if cur:
        rows.append(sorted(cur, key=lambda c: c[0]))
    return rows


def _label_map(header_cells):
    """헤더 셀 → {역할: 열인덱스}. 라벨을 못 읽으면 위치 폴백(맨 왼쪽=이름)."""
    roles, guessed = {}, []
    for i, (_x, _y, txt, _h) in enumerate(header_cells):
        for role, rx in _LBL.items():
            if role not in roles and rx.search(txt):
                roles[role] = i
                break
    if "name" not in roles:
        roles["name"] = 0
        guessed.append("name")
    if "size" not in roles:
        # 이름 다음 열 중 가장 오른쪽 = 대개 SIZE. 못 찾으면 없는 대로 둔다.
        cand = [i for i in range(len(header_cells)) if i != roles["name"]]
        if cand:
            roles["size"] = cand[-1]
            guessed.append("size")
    return roles, guessed


def _assign(cells, col_x):
    """데이터 셀 → 가장 가까운 헤더 열. (values, max_dist)"""
    vals = [None] * len(col_x)
    worst = 0.0
    for x, _y, txt, _h in cells:
        j = min(range(len(col_x)), key=lambda k: abs(col_x[k] - x))
        worst = max(worst, abs(col_x[j] - x))
        vals[j] = txt if vals[j] is None else (vals[j] + " " + txt)
    return vals, worst


def extract_tables(msp, layers, row_tol=None):
    """레이어의 TEXT 격자 → 표 목록.

    표 분리는 **제목 행 우선**이다: 셀이 1개뿐이고 다음 행이 2개 이상이면 제목으로
    보고 새 표를 연다. y 간격으로 자르면 표 중간의 여백 한 줄에도 표가 갈라지고,
    그러면 뒷조각이 헤더를 잃어 데이터 행이 통째로 밀린다.
    제목이 하나도 없는 레이어에서만 간격 기준으로 폴백한다.
    """
    if isinstance(layers, str):
        layers = [layers]
    rows = group_rows(collect_cells(msp, layers), row_tol)
    if not rows:
        return []

    ys = [r[0][1] for r in rows]
    gaps = [ys[i] - ys[i + 1] for i in range(len(ys) - 1)]
    pitch = _median([g for g in gaps if g > 0]) or 1.0
    has_title = any(len(rows[i]) == 1 and i + 1 < len(rows) and len(rows[i + 1]) >= 2
                    for i in range(len(rows)))

    tables, cur = [], None

    def _open(title):
        t = {"title": title, "layer": list(layers), "columns": [], "roles": {},
             "guessed_roles": [], "rows": [], "warnings": []}
        tables.append(t)
        return t

    for i, row in enumerate(rows):
        texts = [c[2] for c in row]
        is_title = (len(row) == 1 and i + 1 < len(rows) and len(rows[i + 1]) >= 2)
        big_gap = (i > 0 and (ys[i - 1] - ys[i]) > 2.5 * pitch)

        if is_title:
            cur = _open(texts[0])
            continue
        if cur is None or (not has_title and big_gap):
            cur = _open(None)

        if not cur["columns"]:                      # 표를 연 뒤 첫 다중셀 행 = 헤더
            if len(row) < 2:
                continue
            cur["columns"] = [{"label": c[2], "x": c[0]} for c in row]
            cur["roles"], cur["guessed_roles"] = _label_map(row)
            continue

        col_x = [c["x"] for c in cur["columns"]]
        labels = [c["label"] for c in cur["columns"]]
        # 헤더가 반복되면(연속 표) 새 표를 여는 대신 열 정의를 갱신한다.
        if len(row) == len(labels) and sum(
                1 for a, b in zip(texts, labels) if a.strip() == b.strip()) >= 2:
            cur = _open(cur["title"])
            cur["columns"] = [{"label": c[2], "x": c[0]} for c in row]
            cur["roles"], cur["guessed_roles"] = _label_map(row)
            continue

        vals, worst = _assign(row, col_x)
        if len(col_x) > 1:
            spacing = min(col_x[k + 1] - col_x[k] for k in range(len(col_x) - 1))
            if worst > spacing * 0.5:
                cur["warnings"].append(
                    f"열 배정이 불확실한 행: {texts} (최대 이탈 {worst:.0f}, 열간격 {spacing:.0f})")
        cur["rows"].append(vals)

    for t in tables:
        t["kind"] = _table_kind(t)
    return [t for t in tables if t["columns"] and t["rows"]]


def _table_kind(t):
    """제목/헤더로 표의 성격 추정. 표기 관례 경고 판단에만 쓴다."""
    blob = " ".join([t.get("title") or ""] + [c["label"] for c in t["columns"]]).upper()
    if "COLUMN" in blob or "기둥" in blob:
        return "column"
    if "BEAM" in blob or "GIRDER" in blob or "보" in blob:
        return "beam"
    return "unknown"


# ── 표 → 부재 인덱스 ───────────────────────────────────────────────────────
def _norm(name):
    return re.sub(r"[^0-9A-Z가-힣]", "", (name or "").upper())


def build_member_index(tables):
    """표 목록 → ({이름: 항목}, 경고목록).

    같은 이름이 여러 표에 나오는 것 자체는 정상이다(SB0 처럼 층마다 반복).
    **값이 다를 때만** 충돌로 보고한다 — 그때는 사람이 결정할 문제지
    파서가 마지막 것으로 덮어쓸 문제가 아니다.
    """
    index, warns = {}, []
    for ti, t in enumerate(tables):
        roles = t["roles"]
        ni, si = roles.get("name"), roles.get("size")
        if ni is None or si is None:
            warns.append(f"표 {ti}({t.get('title')!r}): 이름/단면 열을 찾지 못해 건너뜀")
            continue
        for vals in t["rows"]:
            name = (vals[ni] or "").strip()
            size = (vals[si] or "").strip()
            if not name or _TITLE_RE.search(name):
                continue
            ent = {"name": name, "size": size, "table": ti, "title": t.get("title"),
                   "kind": t["kind"], "section": parse_section(size),
                   "raw": {c["label"]: v for c, v in zip(t["columns"], vals) if v}}
            for role in ("material", "type"):
                j = roles.get(role)
                if j is not None and vals[j]:
                    ent[role] = vals[j].strip()
            key = _norm(name)
            prev = index.get(key)
            if prev and prev["size"] != ent["size"]:
                warns.append(f"부재 {name!r} 단면 충돌: {prev['size']!r}(표{prev['table']}) "
                             f"vs {ent['size']!r}(표{ti}) — 앞의 것을 유지")
                index[key]["conflict"] = True
                continue
            if prev:
                continue
            index[key] = ent
    return index, warns


# ── 도면 레코드에 조인 ─────────────────────────────────────────────────────
def join_members(elements, index, categories=None):
    """`member_name` 을 가진 레코드에 일람표 단면을 부여한다.

    일람표는 레이어 기본치수보다 **구체적**이므로 매칭되면 이긴다.
    매칭 실패는 조용한 기본값으로 때우지 않고 needs_review 로 올린다
    (틀린 치수의 보가 말없이 나오는 것이 이 프로젝트에서 가장 비쌌던 실패다).

    반환: {"matched", "unmatched", "unparsed", "names_unmatched", "warnings"}
    """
    cats = categories or ("beam", "wall", "slab", "column")
    st = {"matched": 0, "unmatched": 0, "unparsed": 0,
          "names_unmatched": {}, "warnings": []}
    wide_seen = set()
    for cat in cats:
        for rec in elements.get(cat, []):
            name = rec.get("member_name")
            if not name:
                continue
            ent = index.get(_norm(name))
            if ent is None:
                st["unmatched"] += 1
                st["names_unmatched"][name] = st["names_unmatched"].get(name, 0) + 1
                rec["needs_review"] = True
                rec["schedule_match"] = "unmatched"
                continue
            sec = ent.get("section")
            if not sec:
                st["unparsed"] += 1
                rec["needs_review"] = True
                rec["schedule_match"] = "size_unparsed"
                rec["section"] = {"name": ent["name"], "size": ent["size"]}
                continue
            ov = rec.setdefault("overrides", {})
            ov["width"] = sec["b"]          # 폭
            ov["thickness"] = sec["h"]      # 춤 — beam/slab 은 z 기준면이 상단
            rec["section"] = {"name": ent["name"], "size": ent["size"],
                              "shape": sec["shape"], "notation": sec["notation"],
                              "b": sec["b"], "h": sec["h"],
                              "material": ent.get("material"), "type": ent.get("type")}
            rec["schedule_match"] = "ok"
            st["matched"] += 1
            if sec.get("wide") and cat != "column" and ent["kind"] != "column":
                wide_seen.add(f"{ent['name']}={ent['size']}")
    if wide_seen:
        st["warnings"].append(
            "폭>춤 단면 %d건 — 표기 관례(폭×춤/춤×폭)를 뒤집어 읽었을 수 있다: %s"
            % (len(wide_seen), ", ".join(sorted(wide_seen)[:5])))
    return st


# ── CLI ───────────────────────────────────────────────────────────────────
def _main():
    import argparse
    import ezdxf

    ap = argparse.ArgumentParser(description="DXF 부재일람표 추출")
    ap.add_argument("dxf")
    ap.add_argument("--layer", action="append", required=True, help="일람표 레이어(복수 가능)")
    ap.add_argument("--json", default=None, help="표+인덱스를 JSON 으로 저장")
    a = ap.parse_args()

    doc = ezdxf.readfile(a.dxf)
    tables = extract_tables(doc.modelspace(), a.layer)
    index, warns = build_member_index(tables)

    print(f"표 {len(tables)}개, 부재 {len(index)}개")
    for i, t in enumerate(tables):
        print(f"\n[{i}] {t.get('title') or '(제목없음)'}  종류={t['kind']}  행={len(t['rows'])}")
        print("     열: " + " | ".join(c["label"] for c in t["columns"])
              + (f"   (라벨추정: {','.join(t['guessed_roles'])})" if t["guessed_roles"] else ""))
        for w in t["warnings"][:3]:
            print("     [!] " + w)
    print()
    n_bad = sum(1 for e in index.values() if not e["section"])
    print(f"단면 파싱: 성공 {len(index) - n_bad} / 실패 {n_bad}")
    for e in sorted(index.values(), key=lambda e: e["name"]):
        s = e["section"]
        d = (f"b={s['b']:.0f} h={s['h']:.0f} ({s['notation']}"
             + (f",{s['shape']}" if s["shape"] else "") + ")") if s else "  ** 파싱실패 **"
        print(f"  {e['name']:<12} {e['size']:<32} {d}")
    for w in warns:
        print("  [!] " + w)

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"tables": tables, "members": list(index.values())},
                      f, ensure_ascii=False, indent=2)
        print(f"\n저장 → {a.json}")


if __name__ == "__main__":
    _main()
