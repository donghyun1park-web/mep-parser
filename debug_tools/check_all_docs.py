import FreeCAD as App

out = "Open Documents:\n"
for doc_name, doc in App.listDocuments().items():
    out += f" - {doc_name} (Active: {doc == App.ActiveDocument})\n"
    counts = {}
    for obj in doc.Objects:
        counts[obj.TypeId] = counts.get(obj.TypeId, 0) + 1
    for t, c in counts.items():
        out += f"    {t}: {c}\n"

with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w") as f:
    f.write(out)
