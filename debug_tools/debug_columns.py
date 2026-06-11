import json
import sys
sys.stdout.reconfigure(encoding='utf-8')

with open("temp_geometry.json", "r", encoding="utf-8") as f:
    data = json.load(f)

columns = data.get("elements", {}).get("column", [])
print(f"Total columns: {len(columns)}")

large_cols = []
for i, c in enumerate(columns):
    pts = c.get("points", [])
    layer = c.get("layer", "?")
    if pts:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
    else:
        w = h = 0
    
    if w > 2000 or h > 2000:
        large_cols.append((i, layer, w, h, len(pts), pts))

print(f"\nAbnormally large columns: {len(large_cols)}")
for idx, layer, w, h, npts, pts in large_cols:
    print(f"\n  Col[{idx}]: layer={layer}, bbox={w:.0f}x{h:.0f}, pts={npts}")
    for p in pts:
        print(f"    pt: [{p[0]:.1f}, {p[1]:.1f}]")
