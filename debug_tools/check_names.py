import FreeCAD as App

doc = App.ActiveDocument
out = ""
if doc:
    objs = [obj for obj in doc.Objects if hasattr(obj, "Shape") and "Wall" not in obj.Name and "Wall" not in obj.Label]
    names = list(set([obj.Name for obj in objs]))
    out += f"Unique names: {names[:20]}\n"

with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w") as f:
    f.write(out)
