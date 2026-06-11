import FreeCAD as App

doc = App.ActiveDocument
out = "Object labels in document:\n"
counts = {}
for obj in doc.Objects:
    label = obj.Label
    # count by prefix to see what's in here
    prefix = label.split()[0].split('_')[0] if label else "None"
    counts[prefix] = counts.get(prefix, 0) + 1

for p, c in sorted(counts.items(), key=lambda x: -x[1]):
    out += f"  {p}: {c}\n"

with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w") as f:
    f.write(out)
