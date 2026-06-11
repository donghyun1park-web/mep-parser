import ezdxf

doc = ezdxf.readfile("c:/AI program/3D Modeling/mep-parser/temp_export.dxf")
msp = doc.modelspace()

layer_counts = {}
for e in msp:
    layer = e.dxf.layer
    if "Block_C" in layer:
        layer_counts[layer] = layer_counts.get(layer, 0) + 1

out = "Counts by layer:\n"
for l, c in layer_counts.items():
    out += f"{l}: {c}\n"

with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w") as f:
    f.write(out)
