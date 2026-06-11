import FreeCAD as App

doc = App.ActiveDocument
out = ""
for obj in doc.Objects:
    if obj.TypeId == "App::FeaturePython" and obj.Name.startswith("Layer") and "#CHK" in obj.Label.upper():
        children = []
        if hasattr(obj, "Group"):
            children = obj.Group
        elif hasattr(obj, "OutList"):
            children = obj.OutList
            
        out += f"Layer {obj.Label} has {len(children)} children:\n"
        for c in children[:10]:
            out += f"  - {c.Name} ({c.TypeId}) / {c.Label}\n"

with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w", encoding="utf-8") as f:
    f.write(out)
