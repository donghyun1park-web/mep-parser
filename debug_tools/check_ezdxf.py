import sys
try:
    import ezdxf
    print("ezdxf is available in FreeCAD!")
except ImportError:
    print("ezdxf is NOT available in FreeCAD.")
