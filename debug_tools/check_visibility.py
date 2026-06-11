import FreeCAD as App

doc = App.ActiveDocument
out = "DXF Lines in document:\n"
visible_count = 0
hidden_count = 0
total_lines = 0

for obj in doc.Objects:
    if "Line" in obj.Name or "Polyline" in obj.Name or "Arc" in obj.Name:
        total_lines += 1
        if hasattr(obj, "ViewObject") and obj.ViewObject:
            if obj.ViewObject.Visibility:
                visible_count += 1
            else:
                hidden_count += 1

out += f"Total DXF-like entities: {total_lines}\n"
out += f"Visible: {visible_count}\n"
out += f"Hidden: {hidden_count}\n"

with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w") as f:
    f.write(out)
