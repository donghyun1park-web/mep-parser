import FreeCAD as App
import Part
import Draft
import Arch
import math

doc = App.newDocument()

# Rotated rectangle
pts = []
angle = math.radians(45)
for dx, dy in [(0, 0), (10, 0), (10, 20), (0, 20), (0, 0)]:
    x = dx * math.cos(angle) - dy * math.sin(angle)
    y = dx * math.sin(angle) + dy * math.cos(angle)
    pts.append(App.Vector(x, y, 0))

wire = Part.makePolygon(pts)
base = doc.addObject("Part::Feature", "Base")
base.Shape = wire

col = Arch.makeStructure(base, height=3000)
doc.recompute()

out = f"Col shape type: {col.Shape.ShapeType}\n"
out += f"Col placement: {col.Placement.Rotation.Angle}\n"
out += f"Base BBox XLength: {base.Shape.BoundBox.XLength}\n"
out += f"Col BBox XLength: {col.Shape.BoundBox.XLength}\n"

out += f"Has Length: {hasattr(col, 'Length')}\n"
if hasattr(col, 'Length'):
    out += f"Length: {col.Length}, Width: {col.Width}\n"

with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w") as f:
    f.write(out)
