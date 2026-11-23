"""T-23 新增的通用规则（不看参照、不按文件名或页码；只用本页输出、本页已付费的高精度行框、本地红章框）。
规则3 长单元格拆行：表格段里某一行是“单个超长单元格”（去空白后 ≥L 字、行内没有制表符），说明表格识别把一大片正文压进了一格。
       找出文字被这一格包含的行框（字二元组覆盖率 ≥0.8），若这些行框覆盖本格 ≥cover 的字，就用这些行框按版面 XY 切分顺序
       （与旋转页规则同一个切分函数：先竖向空白带分栏、再横向分块，块内按物理行、行内左→右）重写这一格，改成正文行。
       覆盖不够就不动。只换这一格，其它行不动。
规则4 重叠行框重复去重：两行输出文字相同（去掉末尾冒号等标点后），且各自对上的行框在页面上重叠 ≥50%，说明同一处字被输出了两次，删掉后一次。
规则5 孤立碎字：正文里单独成行、只有 1 个字符、且不是汉字也不是数字（如 V、×、e、¥）的行，若对不上行框或行框置信度 <conf，就删掉。"""
import re
from copy import deepcopy
import 版面规则 as R, 旋转规则 as RR
CJK = re.compile(r'[\u4e00-\u9fff]')
def _bg(s): return R._bg(s)
def split_long_cell(pg, units, boxes, norm, L=150, cover=0.7):
    if not units or not pg.get('sections'): return pg, None
    rows = R._rows(pg); out, hit = [], []
    for lab, l in rows:
        n = norm(l)
        if lab == 'table' and '\t' not in l and len(n) >= L:
            nb = _bg(n); cand = []
            for u in units:
                if u['vert'] or len(u['t']) < 2: continue
                ub = _bg(u['t'])
                if ub and len(ub & nb) / len(ub) >= 0.8: cand.append(u)
            cov = sum(len(u['t']) for u in cand) / max(1, len(n))
            if cand and cov >= cover:
                us = [{'t': u['t'], 'b': (u['x'], u['y'], u['x'] + u['w'], u['y'] + u['h']),
                       'seal': any(R._inside(u, b) for b in boxes or [])} for u in cand]
                for row in RR.order(us):
                    out.append(('body', ''.join(us[i]['t'] for i in row)))
                hit.append({'原格字数': len(n), '行框数': len(cand), '覆盖': round(cov, 2)}); continue
        out.append((lab, l))
    if not hit: return pg, None
    return R._regroup(out, pg), hit
_P = re.compile(r'[：:；;，,。.\s]+$')
def _iou_small(a, b):
    ix = max(0, min(a['x'] + a['w'], b['x'] + b['w']) - max(a['x'], b['x'])); iy = max(0, min(a['y'] + a['h'], b['y'] + b['h']) - max(a['y'], b['y']))
    return ix * iy / max(1, min(a['w'] * a['h'], b['w'] * b['h']))
def dedup_overlap(pg, units, norm, ov=0.5, minlen=3):
    if not units or not pg.get('sections'): return pg, None
    rows = R._rows(pg); m = R.match(rows, units, norm); seen, keep, drop = [], [], []
    for (lab, l), i in zip(rows, m):
        k = _P.sub('', norm(l))
        if i is not None and len(k) >= minlen and any(k == k2 and (i == j or _iou_small(units[i], units[j]) >= ov) for k2, j in seen):
            drop.append(l); continue
        if i is not None: seen.append((k, i))
        keep.append((lab, l))
    if not drop: return pg, None
    return R._regroup(keep, pg), drop
def drop_stray(pg, units, norm, conf=0.9):
    if not pg.get('sections'): return pg, None
    rows = R._rows(pg); m = R.match(rows, units or [], norm) if units else [None] * len(rows); keep, drop = [], []
    for (lab, l), i in zip(rows, m):
        n = norm(l)
        if lab != 'table' and len(n) == 1 and not CJK.match(n) and not n.isdigit():
            p = units[i]['prob'] if (i is not None and units[i]['prob'] is not None) else None
            if p is None or p < conf: drop.append(l); continue
        keep.append((lab, l))
    if not drop: return pg, None
    return R._regroup(keep, pg), drop
