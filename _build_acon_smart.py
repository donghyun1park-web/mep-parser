# FreeCAD 콘솔에서:
#   exec(open(r"c:\AI program\3D Modeling\mep-parser\_build_acon_smart.py", encoding="utf-8").read())
# A-CON 레이어를 읽어 닫힌 벽(polygonize) + 열린끝 벽(평행 페어링 복구)을 모두 생성.
import FreeCAD as App
import FreeCADGui as Gui
import Part, Arch, math
from shapely.geometry import LineString
from shapely.ops import polygonize, unary_union

LAYER = "A-CON"
HEIGHT = 3000.0
THICK_CAP = 800.0      # 벽 두께 상한(이상이면 방/외부로 보고 제외)
PAIR_MIN, PAIR_MAX = 30.0, 600.0   # 평행 면선 두께 범위
OVL_MIN = 0.25         # 페어링 겹침 최소 비율

doc = App.ActiveDocument
if not doc:
    print("활성 문서가 없습니다."); raise SystemExit

# ── 1) A-CON 객체/edge 수집 ──────────────────────────────────────
objs = {}
for o in doc.Objects:
    if (LAYER in (o.Label or "")) or (LAYER in (o.Name or "")):
        if hasattr(o, "Group") and o.Group:
            for g in o.Group:
                if getattr(g, "Shape", None) and g.Shape.Edges:
                    objs[g.Name] = g
        elif getattr(o, "Shape", None) and o.Shape.Edges:
            objs[o.Name] = o
objs = list(objs.values())
segs = []
for o in objs:
    for ed in o.Shape.Edges:
        vs = ed.Vertexes
        if len(vs) >= 2:
            a = (round(vs[0].Point.x, 1), round(vs[0].Point.y, 1))
            b = (round(vs[-1].Point.x, 1), round(vs[-1].Point.y, 1))
            if a != b:
                segs.append((a, b))
print(f"[A-CON] 객체 {len(objs)}개 / edge {len(segs)}개")
if not segs:
    print("edge 없음. 레이어명 확인."); raise SystemExit

# ── 빌드 헬퍼 ────────────────────────────────────────────────────
prev_lock = getattr(doc, "RecomputeLocked", False)
try: doc.RecomputeLocked = True
except Exception: pass

def make_wall_face(coords, label):
    vecs = [App.Vector(x, y, 0.0) for x, y in coords]
    if len(vecs) < 4: return False
    try:
        face = Part.Face(Part.makePolygon(vecs))
        if not face.isValid() or face.Area < 1000.0: return False
        feat = doc.addObject("Part::Feature", "WB"); feat.Shape = face
        w = Arch.makeWall(feat, height=HEIGHT); w.Label = label
        return True
    except Exception:
        return False

def make_wall_center(p1, p2, width, label):
    try:
        v = [App.Vector(p1[0], p1[1], 0), App.Vector(p2[0], p2[1], 0)]
        feat = doc.addObject("Part::Feature", "WC"); feat.Shape = Part.makePolygon(v)
        w = Arch.makeWall(feat, width=width, height=HEIGHT, align="Center"); w.Label = label
        return True
    except Exception:
        return False

# ── 2) 닫힌 벽: polygonize ───────────────────────────────────────
regions = list(polygonize(unary_union([LineString([a, b]) for a, b in segs])))
n_closed = 0
for poly in regions:
    area, per = poly.area, poly.length
    if per <= 0 or area < 5000: continue
    if 2 * area / per > THICK_CAP: continue   # 방 제외
    if make_wall_face(list(poly.exterior.coords), f"Wall_ACON_C{n_closed}"):
        n_closed += 1

# ── 3) 누락 복구: 열린끝 면선 평행 페어링 ────────────────────────
# degree 계산 → free end(차수1) 가진 세그먼트만 = polygonize 가 못 닫은 면선
def qkey(p, t=5.0): return (round(p[0] / t), round(p[1] / t))
deg = {}
for a, b in segs:
    deg[qkey(a)] = deg.get(qkey(a), 0) + 1
    deg[qkey(b)] = deg.get(qkey(b), 0) + 1
open_segs = [(a, b) for a, b in segs if deg[qkey(a)] == 1 or deg[qkey(b)] == 1]

def dirv(a, b):
    dx, dy = b[0]-a[0], b[1]-a[1]; n = math.hypot(dx, dy)
    return (dx/n, dy/n) if n else (0, 0)

def pair_geo(s1, s2):
    a, b = s1; c, d = s2
    ux, uy = dirv(a, b)
    if abs(ux*dirv(c, d)[0] + uy*dirv(c, d)[1]) < 0.985: return None  # 평행 아님
    def t(p): return (p[0]-a[0])*ux + (p[1]-a[1])*uy
    ta1, ta2, tc1, tc2 = t(a), t(b), t(c), t(d)
    lo, hi = max(min(ta1, ta2), min(tc1, tc2)), min(max(ta1, ta2), max(tc1, tc2))
    ov = hi - lo
    if ov <= 0: return None
    perp = abs((c[0]-a[0])*(-uy) + (c[1]-a[1])*ux)
    if not (PAIR_MIN <= perp <= PAIR_MAX): return None
    L = min(math.hypot(b[0]-a[0], b[1]-a[1]), math.hypot(d[0]-c[0], d[1]-c[1]))
    if L <= 0 or ov < OVL_MIN * L: return None
    # 중선 겹침구간
    foot = (perp,)
    half = ((c[0]-(a[0]+tc1*ux))*0.5, (c[1]-(a[1]+tc1*uy))*0.5)
    cl_lo = (a[0]+lo*ux + half[0], a[1]+lo*uy + half[1])
    cl_hi = (a[0]+hi*ux + half[0], a[1]+hi*uy + half[1])
    return perp, ov, cl_lo, cl_hi

cands = []
for i in range(len(open_segs)):
    for j in range(i+1, len(open_segs)):
        g = pair_geo(open_segs[i], open_segs[j])
        if g: cands.append((g[0], i, j, g[2], g[3]))
cands.sort(key=lambda x: x[0])
used = set(); n_rec = 0
for perp, i, j, clo, chi in cands:
    if i in used or j in used: continue
    used.add(i); used.add(j)
    if make_wall_center(clo, chi, perp, f"Wall_ACON_R{n_rec}"):
        n_rec += 1

# ── 4) 원본 숨김 + 1회 재계산 ────────────────────────────────────
for o in objs:
    if getattr(o, "ViewObject", None):
        try: o.ViewObject.Visibility = False
        except Exception: pass
try: doc.RecomputeLocked = prev_lock
except Exception: pass
doc.recompute()
try: Gui.updateGui()
except Exception: pass

print(f"✅ 빌드 완료: 닫힌벽 {n_closed}개 + 복구벽 {n_rec}개 = {n_closed+n_rec}개")
print(f"   (열린끝 면선 {len(open_segs)}개 중 {n_rec*2}개를 페어링으로 복구)")
