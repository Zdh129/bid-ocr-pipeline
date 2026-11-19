"""几何小工具（本地补测用）。"""


def ov(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = (a[2] - a[0]) * (a[3] - a[1]) or 1.0
    return ix * iy / area


def outside_ellipse(box, seal, n=24):
    """块框内网格点落在印章框内切椭圆之外的比例。"""
    cx, cy = (seal[0] + seal[2]) / 2, (seal[1] + seal[3]) / 2
    rx, ry = max(1e-6, (seal[2] - seal[0]) / 2), max(1e-6, (seal[3] - seal[1]) / 2)
    tot = out = 0
    for i in range(n):
        for j in range(n):
            x = box[0] + (box[2] - box[0]) * (i + 0.5) / n
            y = box[1] + (box[3] - box[1]) * (j + 0.5) / n
            tot += 1
            out += ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 > 1
    return out / tot
