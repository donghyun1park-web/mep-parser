import FreeCAD as App

doc = App.ActiveDocument
if doc:
    objs_to_delete = [obj for obj in doc.Objects if "Wall" in obj.Name or "Wall" in obj.Label or "Column" in obj.Name or "Column" in obj.Label or "ColBase" in obj.Name or "WallAxis" in obj.Name]
    for obj in objs_to_delete:
        try:
            doc.removeObject(obj.Name)
        except Exception:
            pass
    doc.recompute()
    out = f"Deleted {len(objs_to_delete)} objects."
else:
    out = "No active document."

with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w") as f:
    f.write(out)
