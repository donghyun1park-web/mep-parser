import FreeCAD as App

doc = App.ActiveDocument
out = f"Doc Name: {doc.Name}\n"
types = {}
for obj in doc.Objects:
    t = obj.TypeId
    types[t] = types.get(t, 0) + 1

out += "Object types in doc:\n"
for t, c in types.items():
    out += f"{t}: {c}\n"

with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w") as f:
    f.write(out)
