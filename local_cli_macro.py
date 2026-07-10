import sys
import os
import importlib

# Ensure the parent directory is in sys.path so FreeCAD can find mep_macro
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# Proactively reload submodules to bypass FreeCAD Python caching
for module_name in ["mep_macro.geometry", "mep_macro.freecad_utils", "mep_macro.commands", "mep_macro.ui"]:
    if module_name in sys.modules:
        try:
            importlib.reload(sys.modules[module_name])
        except Exception:
            pass

from mep_macro.ui import install_cli_macro

if __name__ == "__main__":
    install_cli_macro()
