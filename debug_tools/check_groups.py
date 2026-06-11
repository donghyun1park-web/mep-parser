import FreeCAD as App

doc = App.ActiveDocument
out = "Groups in document:\n"
for obj in doc.Objects:
    if obj.TypeId in ["App::DocumentObjectGroup", "App::DocumentObjectGroupPython"]:
        out += f"Group: {obj.Name} / {obj.Label} -> {len(obj.Group)} objects\n"

with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w", encoding="utf-8") as f:
    f.write(out)
