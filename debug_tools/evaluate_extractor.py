import sys
import os
import matplotlib.pyplot as plt
import matplotlib.patches as patches

# Add mep-parser to sys.path
sys.path.insert(0, r"c:\AI program\3D Modeling\mep-parser")

from extractors import WallExtractor

# Create a robust yaml config
config_path = r"c:\AI program\3D Modeling\mep-parser\layers_config.yaml"
extractor = WallExtractor(config_path)

dxfs = [
    r"c:\AI program\3D Modeling\mep-parser\sample_walls.dxf",
    r"c:\AI program\3D Modeling\mep-parser\sample_plan.dxf",
    r"c:\AI program\3D Modeling\mep-parser\debug_tools\temp_export.dxf"
]

out_dir = r"C:\Users\User\.gemini\antigravity\brain\6e4fe4a2-ce22-427c-ac50-faf852c0b2e6"

for dxf_path in dxfs:
    if not os.path.exists(dxf_path):
        continue
    
    print(f"--- Processing {os.path.basename(dxf_path)} ---")
    
    # Run extractor
    wall_data = extractor.extract_from_dxf(dxf_path)
    print(f"Extracted {len(wall_data)} centerlines.")
    
    # Get original lines to compare
    import ezdxf
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    
    fig, ax = plt.subplots(figsize=(12, 12))
    ax.set_aspect('equal')
    
    total_lines = 0
    thicknesses = []
    
    for layer_name in extractor.wall_mappings.keys():
        lines = extractor._get_lines_from_layer(msp, layer_name)
        total_lines += len(lines)
        for line in lines:
            ax.plot([line[0][0], line[1][0]], [line[0][1], line[1][1]], color='lightgrey', linewidth=1, alpha=0.5)

    print(f"Total original lines on wall layers: {total_lines}")
    if total_lines > 0:
        print(f"Estimated match rate: {(len(wall_data)*2)/total_lines*100:.1f}%")
        
    for w in wall_data:
        coords = list(w['centerline'].coords)
        thicknesses.append(w['thickness'])
        if len(coords) >= 2:
            ax.plot([coords[0][0], coords[-1][0]], [coords[0][1], coords[-1][1]], color='red', linewidth=2)
            
    # Calculate some thickness stats
    thicknesses.sort()
    if thicknesses:
        print(f"Thicknesses: Min={thicknesses[0]:.1f}, Median={thicknesses[len(thicknesses)//2]:.1f}, Max={thicknesses[-1]:.1f}")
        
    ax.set_title(f"{os.path.basename(dxf_path)} - extracted centerlines")
    
    out_file = os.path.join(out_dir, f"plot_{os.path.basename(dxf_path)}.png")
    plt.savefig(out_file, dpi=150)
    plt.close()
    
    print(f"Saved plot to {out_file}\n")
