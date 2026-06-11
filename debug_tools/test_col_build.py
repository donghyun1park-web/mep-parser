import sys
sys.path.append("c:/AI program/3D Modeling/mep-parser")
import FreeCAD as App
import Part
import Draft
import Arch
import json
from freecad_builder import make_wire

doc = App.newDocument()

with open('c:/AI program/3D Modeling/mep-parser/temp_geometry.json', encoding='utf-8') as f:
    data = json.load(f)

cols = data['elements']['column']
out = ""

# Find a rotated column
rotated_col = None
for c in cols:
    pts = c['points']
    xs = set(p[0] for p in pts)
    ys = set(p[1] for p in pts)
    if len(xs) > 3 or len(ys) > 3:
        rotated_col = c
        break

if rotated_col:
    pts = rotated_col['points']
    out += f"Rotated column points: {pts}\n"
    base = make_wire(pts, True, doc)
    col = Arch.makeStructure(base, height=3000)
    doc.recompute()
    
    out += f"Base Shape BoundBox: {base.Shape.BoundBox}\n"
    out += f"Col Shape BoundBox: {col.Shape.BoundBox}\n"
    out += f"Col Placement: {col.Placement.Rotation.Angle}, Axis: {col.Placement.Rotation.Axis}\n"
    
    if hasattr(col, 'Align'):
        out += f"Align: {col.Align}\n"
    if hasattr(col, 'PlacementOffset'):
        out += f"PlacementOffset: {col.PlacementOffset}\n"

with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w") as f:
    f.write(out)
