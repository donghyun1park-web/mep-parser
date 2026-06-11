import FreeCAD as App

doc = App.ActiveDocument
names = []
for obj in doc.Objects:
    if "Line" in obj.Name or "Polyline" in obj.Name or "Circle" in obj.Name or "Arc" in obj.Name or "Block" in obj.Name:
        # It's an imported geometry or block
        names.append(f"{obj.Name} / {obj.Label}")

with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(names[:100]))
    f.write(f"\nTotal imported objects checked: {len(names)}\n")
