import json

with open('c:/AI program/3D Modeling/mep-parser/temp_geometry.json', encoding='utf-8') as f:
    data = json.load(f)

cols = data['elements']['column']
for c in cols:
    pts = c['points']
    xs = set(p[0] for p in pts)
    ys = set(p[1] for p in pts)
    if len(xs) <= 3 and len(ys) <= 3:
        # AABB!
        # Let's check how many points it has
        print(f"Layer: {c['layer']}, points count: {len(pts)}, coords: {pts[0]}")
