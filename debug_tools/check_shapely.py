try:
    from shapely.geometry import MultiPoint
    pts = [[0,0], [1,0], [1,1], [0,1]]
    hull = MultiPoint(pts).convex_hull
    print("MultiPoint Success:", hull.geom_type)
except Exception as e:
    print("MultiPoint Error:", type(e).__name__, e)
