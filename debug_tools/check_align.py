import FreeCAD as App

doc = App.ActiveDocument
out = ""
aligned_objs = []
for obj in doc.Objects:
    if hasattr(obj, "Align"):
        aligned_objs.append(obj)

if aligned_objs:
    w = aligned_objs[0]
    out += f"Total objects with Align: {len(aligned_objs)}\n"
    out += f"Obj 0 Name: {w.Name}, Label: {w.Label}\n"
    out += f"Obj 0 Type: {w.TypeId}\n"
    out += f"Obj 0 Align: {w.Align}\n"
else:
    out += "No objects with Align property found\n"
    
with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w") as f:
    f.write(out)
