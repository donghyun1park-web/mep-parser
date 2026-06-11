from shapely.geometry import MultiPoint
import math

# Create 4 points of a rotated rectangle
angle = math.radians(45)
cx, cy = 10, 10
w, h = 2, 4
pts = []
for dx, dy in [(-w, -h), (w, -h), (w, h), (-w, h)]:
    x = cx + dx * math.cos(angle) - dy * math.sin(angle)
    y = cy + dx * math.sin(angle) + dy * math.cos(angle)
    pts.append((x, y))

# Also add the X points (diagonals)
pts.append((pts[0][0], pts[0][1]))
pts.append((pts[2][0], pts[2][1]))
pts.append((pts[1][0], pts[1][1]))
pts.append((pts[3][0], pts[3][1]))

hull = MultiPoint(pts).convex_hull
coords = list(hull.exterior.coords)
print("Points:", len(coords), coords)
