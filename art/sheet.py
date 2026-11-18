"""검토용 미리보기: 에셋마다 3×3 반복, 전체 모음(외벽·지붕·지면·교량 줄), 원본 모음."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .seamless import tile_preview
from .theme import Theme, find_raw

ROWS = [
    ["facade_residential", "facade_commercial", "facade_public", "facade_industrial", "facade_other"],
    ["roof_residential", "roof_commercial", "roof_public", "roof_industrial", "roof_other"],
    ["ground_grass", "ground_paving", "road_asphalt", "rail_ballast"],
    ["bridge_road", "bridge_foot", "bridge_rail"],
]


def _font(size: int = 22):
    for f in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def label_grid(rows: list[list[Image.Image]], labels: list[str] | list[list[str]], out: Path,
               cell_h: int = 360, pad: int = 12, label_h: int = 34, quality: int = 88) -> Path:
    """이미지 줄들을 같은 높이로 맞춰 한 장에 모은다. labels 가 1차원이면 첫 줄 아래에만 단다."""
    font = _font()
    scaled = [[im.resize((int(im.width * cell_h / im.height), cell_h), Image.Resampling.LANCZOS) for im in r]
              for r in rows]
    width = max(sum(im.width for im in r) + pad * (len(r) + 1) for r in scaled)
    height = sum(cell_h + label_h + pad for _ in scaled) + pad
    sheet = Image.new("RGB", (width, height), (246, 242, 236))
    d = ImageDraw.Draw(sheet)
    y = pad
    for ri, r in enumerate(scaled):
        x = pad
        row_labels = labels[ri] if labels and isinstance(labels[0], list) else (labels if ri == 0 else [])
        for ci, im in enumerate(r):
            sheet.paste(im, (x, y))
            if ci < len(row_labels):
                d.text((x + 4, y + cell_h + 6), row_labels[ci], fill=(60, 60, 60), font=font)
            x += im.width + pad
        y += cell_h + label_h + pad
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, "JPEG", quality=quality)
    return out


def run_sheet(theme: Theme) -> int:
    out_dir = theme.sheet_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    tiled_rows, raw_rows, label_rows = [], [], []
    for row in ROWS:
        tr, rr, lr = [], [], []
        for name in row:
            p = theme.out_dir / f"{name}.jpg"
            if not p.exists():
                continue
            img = Image.open(p).convert("RGB")
            prev = tile_preview(img, 3, 384 if img.width > img.height else 256)
            prev.save(out_dir / f"{name}_3x3.jpg", quality=88)
            tr.append(prev)
            lr.append(name)
            hit = find_raw(theme, name)
            if hit:
                rr.append(Image.open(hit[0]).convert("RGB"))
        if tr:
            tiled_rows.append(tr)
            label_rows.append(lr)
        if rr:
            raw_rows.append(rr)
    if not tiled_rows:
        print("후처리 결과가 없습니다(process 먼저).")
        return 1
    a = label_grid(tiled_rows, label_rows, out_dir / f"{theme.name}_sheet_3x3.jpg", cell_h=380)
    b = label_grid(raw_rows, label_rows, out_dir / f"{theme.name}_sheet_raw.jpg", cell_h=300)
    single = [[Image.open(theme.out_dir / f"{n}.jpg").convert("RGB") for n in r] for r in label_rows]
    c = label_grid(single, label_rows, out_dir / f"{theme.name}_sheet.jpg", cell_h=300)
    for p in (c, a, b):
        print("sheet:", p)
    return 0
