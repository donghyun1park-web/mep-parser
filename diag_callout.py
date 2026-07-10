"""창호 콜아웃/레이어 구조 진단 (읽기 전용)."""
import sys, re, collections
import ezdxf

PAT = re.compile(r"\d+\.?\d*\s*[Xx]\s*\d+\.?\d*")
MARKS = ("ADW", "AW", "AD", "WW", "WD")

def scan(path):
    try:
        doc = ezdxf.readfile(path)
    except Exception as e:
        print(f"  !! 읽기 실패: {e}")
        return
    msp = doc.modelspace()

    # 1) 엔티티 타입 × 레이어 분포 (상위 25)
    c = collections.Counter((e.dxftype(), e.dxf.layer) for e in msp)
    print("  [엔티티 타입 × 레이어 상위]")
    for (t, l), n in sorted(c.items(), key=lambda x: -x[1])[:25]:
        print(f"    {n:6d}  {t:12s}  {l}")

    # 2) WIN 관련 레이어
    wins = sorted({l for (_, l) in c if re.search(r"WIN|창|호|WD|CW", l, re.I)})
    print(f"  [창/WIN 추정 레이어] {wins}")

    # 3) 콜아웃 텍스트 후보 (TEXT/MTEXT)
    hits = []
    for e in msp.query("TEXT MTEXT"):
        try:
            s = e.dxf.text if e.dxftype() == "TEXT" else e.text
        except Exception:
            continue
        if PAT.search(s) or any(k in s.upper() for k in MARKS):
            ins = getattr(e.dxf, "insert", None)
            xy = (round(ins.x, 1), round(ins.y, 1)) if ins else None
            hits.append((e.dxftype(), e.dxf.layer, s.strip()[:50], xy))
    print(f"  [콜아웃 TEXT/MTEXT 후보] {len(hits)}건")
    for h in hits[:20]:
        print(f"    {h[0]:6s} {h[1]:14s} {h[2]!r:30s} @{h[3]}")

    # 4) INSERT(블록) + ATTRIB 안에 콜아웃이 있는지
    blk_hits = []
    for e in msp.query("INSERT"):
        name = e.dxf.name
        attribs = []
        for a in (e.attribs if hasattr(e, "attribs") else []):
            try:
                attribs.append(a.dxf.text)
            except Exception:
                pass
        joined = " ".join(attribs)
        if PAT.search(joined) or any(k in joined.upper() for k in MARKS) \
           or any(k in name.upper() for k in MARKS) or re.search(r"WIN|창|호", name, re.I):
            ins = e.dxf.insert
            blk_hits.append((name, e.dxf.layer, attribs[:4], (round(ins.x,1), round(ins.y,1))))
    print(f"  [창호 블록 INSERT 후보] {len(blk_hits)}건")
    for b in blk_hits[:20]:
        print(f"    blk={b[0]!r:20s} layer={b[1]:14s} attribs={b[2]} @{b[3]}")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print("=" * 70)
        print(p)
        scan(p)
