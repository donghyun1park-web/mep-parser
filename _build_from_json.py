# FreeCAD 콘솔:
#   exec(open(r"c:\AI program\3D Modeling\mep-parser\_build_from_json.py", encoding="utf-8").read())
# DXF에서 미리 추출한 edge JSON 으로 벽 빌드(폴리곤화 + 열린끝 페어링 복구).
# FreeCAD import 객체 구조에 의존하지 않음 → 헤드리스와 동일 결과 보장.
import FreeCAD as App, FreeCADGui as Gui, Part, Arch, math, json
from shapely.geometry import LineString
from shapely.ops import polygonize, unary_union

JSON = r"C:/AI program/architectural_timelapse_phase16/input/drawings/_acon_edges.json"
LABEL = "ACON"
HEIGHT = 3000.0
THICK_CAP = 800.0
PAIR_MIN, PAIR_MAX = 30.0, 600.0
OVL_MIN = 0.25
HIDE_ORIGINALS = False   # 원본 2D 선 유지(비교용)

segs = [(tuple(a), tuple(b)) for a, b in json.load(open(JSON))]
print(f"[{LABEL}] edge {len(segs)}개 로드")

doc = App.ActiveDocument or App.newDocument("Build")

def mface(coords, label):
    v = [App.Vector(x, y, 0) for x, y in coords]
    if len(v) < 4: return False
    try:
        f = Part.Face(Part.makePolygon(v))
        if not f.isValid() or f.Area < 1000: return False
        ft = doc.addObject("Part::Feature", "WB"); ft.Shape = f
        w = Arch.makeWall(ft, height=HEIGHT); w.Label = label
        return True
    except Exception:
        return False

def mcent(p1, p2, width, label):
    try:
        ft = doc.addObject("Part::Feature", "WC")
        ft.Shape = Part.makePolygon([App.Vector(p1[0], p1[1], 0), App.Vector(p2[0], p2[1], 0)])
        w = Arch.makeWall(ft, width=width, height=HEIGHT, align="Center"); w.Label = label
        return True
    except Exception:
        return False

# 1) 닫힌 벽
regions = list(polygonize(unary_union([LineString([a, b]) for a, b in segs])))
nc = 0
for poly in regions:
    a, p = poly.area, poly.length
    if p <= 0 or a < 5000 or 2*a/p > THICK_CAP: continue
    if mface(list(poly.exterior.coords), f"Wall_{LABEL}_C{nc}"): nc += 1

# 2) 열린끝 면선 ↔ 모든 면선 페어링 복구
def qk(p, t=5.0): return (round(p[0]/t), round(p[1]/t))
deg = {}
for a, b in segs:
    deg[qk(a)] = deg.get(qk(a), 0)+1; deg[qk(b)] = deg.get(qk(b), 0)+1
open_idx = [i for i, (a, b) in enumerate(segs) if deg[qk(a)] == 1 or deg[qk(b)] == 1]

def dv(a, b):
    dx, dy = b[0]-a[0], b[1]-a[1]; n = math.hypot(dx, dy)
    return (dx/n, dy/n) if n else (0, 0)

def pg(s1, s2):
    a, b = s1; c, d = s2; ux, uy = dv(a, b)
    if abs(ux*dv(c, d)[0] + uy*dv(c, d)[1]) < 0.985: return None
    def t(p): return (p[0]-a[0])*ux + (p[1]-a[1])*uy
    lo = max(min(t(a), t(b)), min(t(c), t(d))); hi = min(max(t(a), t(b)), max(t(c), t(d)))
    ov = hi - lo
    if ov <= 0: return None
    perp = abs((c[0]-a[0])*(-uy) + (c[1]-a[1])*ux)
    if not (PAIR_MIN <= perp <= PAIR_MAX): return None
    L = min(math.hypot(b[0]-a[0], b[1]-a[1]), math.hypot(d[0]-c[0], d[1]-c[1]))
    if L <= 0 or ov < OVL_MIN*L: return None
    tc1 = t(c); half = ((c[0]-(a[0]+tc1*ux))*0.5, (c[1]-(a[1]+tc1*uy))*0.5)
    return perp, (a[0]+lo*ux+half[0], a[1]+lo*uy+half[1]), (a[0]+hi*ux+half[0], a[1]+hi*uy+half[1])

cands = []
for i in open_idx:
    for j in range(len(segs)):
        if j == i: continue
        g = pg(segs[i], segs[j])
        if g: cands.append((g[0], i, j, g[1], g[2]))
cands.sort(key=lambda x: x[0])
used = set(); nr = 0
for perp, i, j, clo, chi in cands:
    if i in used or j in used: continue
    used.add(i); used.add(j)
    if mcent(clo, chi, perp, f"Wall_{LABEL}_R{nr}"): nr += 1

doc.recompute()
try: Gui.updateGui()
except Exception: pass
try:
    print(f"[OK] {LABEL}: closed {nc} + recovered {nr} = {nc+nr} walls (open-ends {len(open_idx)})")
except Exception:
    pass
