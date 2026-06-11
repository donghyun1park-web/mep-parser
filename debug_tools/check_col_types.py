import json

with open('c:/AI program/3D Modeling/mep-parser/temp_geometry.json', encoding='utf-8') as f:
    data = json.load(f)

cols = data['elements']['column']
num_aabb = 0
num_rotated = 0
for c in cols:
    pts = c['points']
    xs = set(p[0] for p in pts)
    ys = set(p[1] for p in pts)
    if len(xs) <= 3 and len(ys) <= 3:
        num_aabb += 1
    else:
        num_rotated += 1

print(f"Total: {len(cols)}")
print(f"AABB: {num_aabb}")
print(f"Rotated: {num_rotated}")
