import ezdxf
import sys
from collections import defaultdict

doc = ezdxf.readfile("temp_export.dxf")
msp = doc.modelspace()

layer_stats = defaultdict(lambda: {"count": 0, "types": defaultdict(int)})

for e in msp:
    layer = e.dxf.layer
    etype = e.dxftype()
    layer_stats[layer]["count"] += 1
    layer_stats[layer]["types"][etype] += 1

lines = []
lines.append(f"Total layers: {len(layer_stats)}")
lines.append(f"Total entities: {sum(s['count'] for s in layer_stats.values())}")
lines.append("")

# Only show layers with >1 entity or interesting names
lines.append("=== Layers with >1 entity ===")
for layer, stats in sorted(layer_stats.items(), key=lambda x: -x[1]["count"]):
    if stats["count"] < 2:
        continue
    types_str = ", ".join(f"{t}:{c}" for t, c in sorted(stats["types"].items()))
    safe_layer = layer.encode('ascii', errors='replace').decode('ascii')
    lines.append(f"  [{stats['count']:5d}] {safe_layer:50s}  ({types_str})")

lines.append("")
lines.append("=== Layer name pattern summary ===")
# Group by prefix pattern
prefix_counts = defaultdict(int)
for layer in layer_stats:
    safe = layer.encode('ascii', errors='replace').decode('ascii')
    # Extract prefix (before first digit sequence or underscore+digit)
    parts = safe.split('_')
    if len(parts) > 1:
        prefix = parts[0]
    else:
        # Remove trailing digits
        prefix = safe.rstrip('0123456789')
        if not prefix:
            prefix = safe
    prefix_counts[prefix] += 1

for prefix, cnt in sorted(prefix_counts.items(), key=lambda x: -x[1]):
    if cnt >= 2:
        lines.append(f"  {prefix:30s} x{cnt}")

with open("_layer_analysis.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("Done. See _layer_analysis.txt")
