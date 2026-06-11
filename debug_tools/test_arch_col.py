import FreeCAD as App
import Part
import Draft
import Arch

doc = App.newDocument()
pts = [App.Vector(10, 10, 0), App.Vector(20, 10, 0), App.Vector(20, 20, 0), App.Vector(10, 20, 0), App.Vector(10, 10, 0)]
wire = Part.makePolygon(pts)
base = doc.addObject("Part::Feature", "Base")
base.Shape = wire

col = Arch.makeStructure(base, height=3000)
doc.recompute()

out = f"Base shape center: {base.Shape.BoundBox.Center}\n"
out += f"Col shape center: {col.Shape.BoundBox.Center}\n"
out += f"Col placement: {col.Placement.Base}\n"

with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w") as f:
    f.write(out)
