import FreeCAD as App

doc = App.ActiveDocument
out = ""
for obj in doc.Objects:
    if obj.TypeId == "App::FeaturePython" and obj.Name.startswith("Layer"):
        children = []
        if hasattr(obj, "Group"):
            children = obj.Group
        elif hasattr(obj, "OutList"):
            children = obj.OutList
        out += f"Layer {obj.Label} has {len(children)} objects.\n"

with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w", encoding="utf-8") as f:
    f.write(out)
