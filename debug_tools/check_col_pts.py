import json
from shapely.geometry import MultiPoint

with open('c:/AI program/3D Modeling/mep-parser/temp_geometry.json', encoding='utf-8') as f:
    data = json.load(f)

cols = data['elements']['column']
print(f"Loaded {len(cols)} columns.")
if cols:
    c = cols[0]
    print(c['points'])
