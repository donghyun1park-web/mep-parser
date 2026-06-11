import FreeCAD as App
import Part
import Arch

doc = App.newDocument("Test")
pts = [App.Vector(0,0,0), App.Vector(100,0,0), App.Vector(100,100,0), App.Vector(0,100,0), App.Vector(0,0,0)]
wire = Part.makePolygon(pts)
feat = doc.addObject("Part::Feature", "Base")
feat.Shape = wire
struct = Arch.makeStructure(feat, height=2000)

out = "Properties of Arch.Structure:\n"
for prop in struct.PropertiesList:
    out += f"  {prop}: {getattr(struct, prop)}\n"

with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w") as f:
    f.write(out)
