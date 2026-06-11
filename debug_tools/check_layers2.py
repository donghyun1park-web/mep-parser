import FreeCAD as App

doc = App.ActiveDocument
out = "FeaturePython objects:\n"
for obj in doc.Objects:
    if obj.TypeId == "App::FeaturePython" or "Layer" in str(obj.__class__):
        out += f"{obj.TypeId}: {obj.Name} / {obj.Label}\n"

with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w", encoding="utf-8") as f:
    f.write(out)
