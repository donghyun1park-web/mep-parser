import sys
import os

# Ensure the parent directory is in sys.path so FreeCAD can find mep_macro
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from mep_macro.ui import install_cli_macro

if __name__ == "__main__":
    install_cli_macro()
