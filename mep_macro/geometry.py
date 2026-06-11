import math

def dv(a, b):
    """
    Returns the unit vector from a to b.
    If length is 0, returns (0, 0).
    """
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n else (0, 0)

def pair_rect(s1, s2, pair_min=30, pair_max=800, ovl_min=0.1):
    """
    Checks if two line segments s1=((ax,ay), (bx,by)) and s2=((cx,cy), (dx,dy))
    can form a parallel wall segment. If so, returns the 4 corner points of the wall.
    Includes corner filling logic.
    """
    a, b = s1
    c, d = s2
    ux, uy = dv(a, b)
    cx_dir, cy_dir = dv(c, d)
    
    # Check if lines are parallel (within threshold)
    if abs(ux * cx_dir + uy * cy_dir) < 0.985:
        return None
        
    def t(p):
        return (p[0] - a[0]) * ux + (p[1] - a[1]) * uy
        
    min_all = min(min(t(a), t(b)), min(t(c), t(d)))
    max_all = max(max(t(a), t(b)), max(t(c), t(d)))
    lo = max(min(t(a), t(b)), min(t(c), t(d)))
    hi = min(max(t(a), t(b)), max(t(c), t(d)))
    ov = hi - lo
    if ov <= 0:
        return None
        
    perp = abs((c[0] - a[0]) * (-uy) + (c[1] - a[1]) * ux)
    if not (pair_min <= perp <= pair_max):
        return None
        
    L = min(math.hypot(b[0] - a[0], b[1] - a[1]), math.hypot(d[0] - c[0], d[1] - c[1]))
    if L <= 0 or ov < ovl_min * L:
        return None
        
    # Corner-gap filling: extend rect corners if close to end points
    new_lo = min_all if (lo - min_all <= perp * 3.0) else lo
    new_hi = max_all if (max_all - hi <= perp * 3.0) else hi
    ov_ext = new_hi - new_lo
    
    p1 = (a[0] + new_lo * ux, a[1] + new_lo * uy)
    p2 = (a[0] + new_hi * ux, a[1] + new_hi * uy)
    
    def tc(p):
        return (p[0] - c[0]) * ux + (p[1] - c[1]) * uy
        
    bo = tc((a[0] + new_lo * ux, a[1] + new_lo * uy))
    q1 = (c[0] + bo * ux, c[1] + bo * uy)
    q2 = (c[0] + (bo + ov_ext) * ux, c[1] + (bo + ov_ext) * uy)
    
    return [p1, p2, q2, q1]
