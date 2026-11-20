"""本地表格线检测（300 DPI 渲染图，OpenCV 形态学）。只读扫描 PDF，结果缓存到 output/版面约束/lines_cache/。坐标一律换成 pt（左上原点）。"""
import json, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / 'deps'))
import cv2, numpy as np
D = Path(__file__).parent
DPI = 300.0; S = DPI / 72.0
def render(pdf, page, out):
    if not out.exists():
        stem = out.with_suffix('')
        subprocess.run(['pdftoppm', '-r', str(int(DPI)), '-f', str(page), '-l', str(page), '-singlefile', '-png', str(pdf), str(stem)], check=True)
    return out
def detect(pdf, setname, page):
    cache = D / 'lines_cache' / f'{setname}_p{page:02d}.json'
    if cache.exists(): return json.loads(cache.read_text())
    img = render(pdf, page, D / 'renders300' / f'{setname}_p{page:02d}.png')
    g = cv2.imread(str(img), cv2.IMREAD_GRAYSCALE); H, W = g.shape
    _, bw = cv2.threshold(g, 200, 255, cv2.THRESH_BINARY_INV)   # 固定阈值 200（背景约 234，框线 110～180）
    out = {'W_pt': W / S, 'H_pt': H / S, 'h': [], 'v': []}
    for key, ker in (('h', (max(40, W // 25), 1)), ('v', (1, max(40, H // 40)))):
        m = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, ker))
        n, lab, st, _ = cv2.connectedComponentsWithStats(m)
        for i in range(1, n):
            x, y, w, h, a = st[i]
            if key == 'h' and h <= 40 and w >= W / 8: ys = np.where(lab[y:y + h, x:x + w] == i)[0]; out['h'].append([x / S, (y + ys.mean()) / S, (x + w) / S])          # [x0, y, x1]
            if key == 'v' and w <= 40 and h >= H / 40: xs = np.where(lab[y:y + h, x:x + w] == i)[1]; out['v'].append([(x + xs.mean()) / S, y / S, (y + h) / S])          # [x, y0, y1]
    cache.parent.mkdir(exist_ok=True); cache.write_text(json.dumps(out))
    return out
