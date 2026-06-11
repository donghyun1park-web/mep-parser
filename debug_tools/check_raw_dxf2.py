import ezdxf

doc = ezdxf.readfile("temp_export_fixed.dxf")
msp = doc.modelspace()

layer_pts = {}
for e in msp:
    if e.dxf.layer == "Block_C_600X800":
        print(f"Layer: {e.dxf.layer}, Entity type: {e.dxftype()}")
        if e.dxftype() == 'LINE':
            print(f"LINE: {e.dxf.start} to {e.dxf.end}")
