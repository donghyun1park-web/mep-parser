import FreeCAD as App
import Part
import Draft
import Arch
import math

doc = App.newDocument()
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

out = "Structure Properties:\n"
for prop in col.PropertiesList:
    out += f"{prop}: {getattr(col, prop)}\n"

with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w") as f:
    f.write(out)
