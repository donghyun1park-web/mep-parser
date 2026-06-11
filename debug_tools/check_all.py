import FreeCAD as App
doc = App.ActiveDocument
out = f"Doc: {doc.Name}\n"
names = [obj.Name for obj in doc.Objects]
labels = [obj.Label for obj in doc.Objects]
out += f"Total objects: {len(names)}\n"
out += f"Wall objects by name: {[n for n in names if 'Wall' in n]}\n"
out += f"Wall objects by label: {[l for l in labels if 'Wall' in l]}\n"

with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w") as f:
    f.write(out)
