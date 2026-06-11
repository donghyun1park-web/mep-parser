import ezdxf

doc = ezdxf.readfile("temp_export_fixed.dxf")
msp = doc.modelspace()

layer_pts = {}
for e in msp:
    if "Block_C_600X800" in e.dxf.layer:
        if e.dxftype() == 'LINE':
            layer_pts.setdefault(e.dxf.layer, []).extend([(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)])

print("Points in some Block_C:")
for layer in layer_pts:
    if "Block_C" in layer:
        print(layer)
        for pt in layer_pts[layer]:
            print(pt)
        break
