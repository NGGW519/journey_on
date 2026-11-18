"""자동 검사. 항목별 PASS/WARN/FAIL 을 출력하고 reports/validate.json 에 남긴다. FAIL 이 있으면 종료 코드 1."""
from __future__ import annotations

import re
from typing import Any

import numpy as np
import rasterio
import trimesh

from . import sources
from .config import area_cfg, area_paths, read_json, write_json
from .grid import area_bounds, area_tiles
from .heightmap import decode
from .meshio import from_gltf_axes, from_gltf_uv, read_glb_nodes
from .terrain import raw_bilinear  # noqa: F401  (예전 import 경로 호환)

FORBIDDEN = re.compile(r"googleapis|google\.[a-z.]+/maps|kakao|daum|naver|pstatic", re.I)
WATER_LEVEL_RANGE = (25.0, 70.0)  # 가평 북한강(청평호 상류) 상식 범위
RISE_WARN_M = 0.3                 # 물줄기 중심선 종단에서 상류 최저값보다 이만큼 넘게 높아지면 역경사
HIDDEN_WARN = 0.05                # 수면체 표본 중 지형에 가려진(지형 > 수면+0.05 m) 비율 경고 기준
NOBUILD_LOWER_M = 0.5             # 금지 구역에서 원 DSM 보다 이만큼 넘게 낮아지면 평탄화로 본다


def footprint_grid(V: np.ndarray, F: np.ndarray, spacing: float = 1.0, level: str = "bottom"):
    """건물 메시(로컬 E, N, 표고)의 바닥면(level="bottom") 또는 지붕면("top") 삼각형 → 외곽선 폴리곤과
    내부 격자 표본(x, y). 꼭짓점도 포함. v2 glb 는 바닥면이 없으므로 지붕 프리미티브를 "top" 으로 넘긴다."""
    from shapely import contains_xy
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    ref = V[:, 2].min() if level == "bottom" else V[:, 2].max()
    bf = F[np.all(np.abs(V[F][:, :, 2] - ref) < 1e-3, axis=1)]
    tris = [Polygon(V[f][:, :2]) for f in bf]
    foot = unary_union([t for t in tris if t.area > 0]).buffer(0) if tris else None
    xs, ys = V[:, 0], V[:, 1]
    if foot is not None and not foot.is_empty:
        minx, miny, maxx, maxy = foot.bounds
        gx, gy = np.meshgrid(np.arange(minx + spacing / 2, maxx, spacing), np.arange(miny + spacing / 2, maxy, spacing))
        gx, gy = gx.ravel(), gy.ravel()
        m = contains_xy(foot, gx, gy)
        xs, ys = np.concatenate([gx[m], xs]), np.concatenate([gy[m], ys])
    return foot, xs, ys


def mesh_boundary_edges(V: np.ndarray, F: np.ndarray, tile_size: float) -> np.ndarray:
    """한 면만 쓰는 변(물가) 중 타일 경계선 위의 변을 뺀 것. (n, 2, 3) 로컬 좌표."""
    e = np.sort(np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]), axis=1)
    u, cnt = np.unique(e, axis=0, return_counts=True)
    be = u[cnt == 1]
    A, B = V[be[:, 0]], V[be[:, 1]]

    def on_edge(p):
        return ((np.abs(p[:, 0]) < 0.05) | (np.abs(p[:, 0] - tile_size) < 0.05)
                | (np.abs(p[:, 1]) < 0.05) | (np.abs(p[:, 1] - tile_size) < 0.05))
    skip = on_edge(A) & on_edge(B) & ((np.abs(A[:, 0] - B[:, 0]) < 0.05) | (np.abs(A[:, 1] - B[:, 1]) < 0.05))
    return np.stack([A[~skip], B[~skip]], axis=1)


def point_in_triangles(P: np.ndarray, x: float, y: float) -> bool:
    """P: (n, 3, 2) 삼각형. 점이 하나라도 안(경계 포함)에 있으면 True."""
    a, b, c = P[:, 0], P[:, 1], P[:, 2]

    def cr(p, q):
        return (q[:, 0] - p[:, 0]) * (y - p[:, 1]) - (q[:, 1] - p[:, 1]) * (x - p[:, 0])
    d1, d2, d3 = cr(a, b), cr(b, c), cr(c, a)
    return bool((((d1 >= 0) & (d2 >= 0) & (d3 >= 0)) | ((d1 <= 0) & (d2 <= 0) & (d3 <= 0))).any())


def profile_rises(F: np.ndarray, owner: np.ndarray, T, lines, step: float = 2.0, min_samples: int = 10):
    """물줄기 선(흐름 방향)을 step 간격으로 따라가며 수면체별 최대 역상승(상류 최저값 대비)을 잰다.

    돌려주는 값: {수면체 id: (최대 역상승 m, E, N, 선 osm_id)} — 수면체마다 가장 나쁜 선.
    """
    import shapely

    inv = ~T
    H, W = F.shape
    out: dict[int, tuple[float, float, float, int]] = {}
    for oid, line in lines:
        n = int(line.length // step) + 1
        pts = shapely.line_interpolate_point(line, np.minimum(np.arange(n) * step, line.length))
        xs, ys = shapely.get_x(pts), shapely.get_y(pts)
        c, r = inv @ (xs, ys)
        r, c = np.round(r).astype(int), np.round(c).astype(int)
        ok = (r >= 0) & (r < H) & (c >= 0) & (c < W)
        r, c, xs, ys = r[ok], c[ok], xs[ok], ys[ok]
        b = owner[r, c]
        f = F[r, c]
        for body in np.unique(b[b >= 0]):
            sel = np.nonzero(b == body)[0]
            if len(sel) < min_samples:
                continue
            fv = f[sel]
            rise = fv - np.minimum.accumulate(fv)
            k = int(np.argmax(rise))
            if int(body) not in out or rise[k] > out[int(body)][0]:
                out[int(body)] = (float(rise[k]), float(xs[sel[k]]), float(ys[sel[k]]), int(oid))
    return out


class Report:
    def __init__(self):
        self.items: list[dict[str, Any]] = []

    def add(self, stage: str, check: str, status: str, detail: str = ""):
        self.items.append({"stage": stage, "check": check, "status": status, "detail": detail})

    def ok(self, stage, check, cond, detail="", fail="FAIL"):
        self.add(stage, check, "PASS" if cond else fail, detail)

    @property
    def worst(self) -> str:
        s = {i["status"] for i in self.items}
        return "FAIL" if "FAIL" in s else "WARN" if "WARN" in s else "PASS"


def check_sources(cfg, area, R: Report):
    m = sources.load(cfg)
    for layer, entries in m["layers"].items():
        for e in entries:
            p = sources.abspath(e)
            R.ok("sources", f"{layer} 파일 존재", p.exists() and p.stat().st_size > 0, e["path"])
            bad = FORBIDDEN.search(" ".join(str(e.get(k, "")) for k in ("url", "source", "path")))
            R.ok("sources", f"{layer} 금지 소스 아님", not bad, bad.group(0) if bad else "")
            if not e.get("license"):
                R.add("sources", f"{layer} 이용조건 기록", "FAIL", e["path"])
    for layer in ("dem", "ortho", "buildings", "water_areas"):
        sel = sources.select(cfg, layer)
        if not sel:
            R.add("sources", f"{layer} 원천", "WARN", "없음")
        elif any(e.get("interim") for e in sel):
            R.add("sources", f"{layer} 원천", "WARN", "interim(공개 대체 데이터) 사용 중")
        else:
            R.add("sources", f"{layer} 원천", "PASS", "공식")


def check_dem(cfg, area, R: Report):
    ap = area_paths(cfg, area)
    res = cfg["heightmap"]["resolution"]
    tiles = area_tiles(cfg, area)
    hm = {}
    for t in tiles:
        p = ap.tile_dir(t.name) / "heightmap.raw"
        if not p.exists():
            R.add("dem", f"{t.name} RAW", "FAIL", "없음")
            continue
        raw = p.read_bytes()
        if len(raw) != res * res * 2:
            R.add("dem", f"{t.name} RAW 크기", "FAIL", f"{len(raw)} bytes")
            continue
        q = np.frombuffer(raw, dtype="<u2")
        sat = int((q == 0).sum() + (q == 65535).sum())
        if sat:
            R.add("dem", f"{t.name} 포화", "FAIL", f"{sat}개 표본이 0 또는 65535")
        hm[t.name] = (t, decode(raw, cfg))
    R.ok("dem", "타일 수", len(hm) == len(tiles), f"{len(hm)}/{len(tiles)}")
    by_pos = {(t.e0, t.n0): z for t, z in hm.values()}
    worst = 0.0
    for (e0, n0), z in by_pos.items():
        east = by_pos.get((e0 + 1000, n0))
        north = by_pos.get((e0, n0 + 1000))
        if east is not None:
            worst = max(worst, float(np.abs(z[:, -1] - east[:, 0]).max()))
        if north is not None:  # 첫 행 = 남쪽 → 내 마지막 행 = 북쪽 이웃의 첫 행
            worst = max(worst, float(np.abs(z[-1, :] - north[0, :]).max()))
    R.ok("dem", "이음매(공유 가장자리 차)", worst < 1e-6, f"최대 {worst:.6f} m")
    if hm:
        zs = np.concatenate([z.ravel() for _, z in hm.values()])
        R.add("dem", "표고 범위", "PASS", f"{zs.min():.1f} ~ {zs.max():.1f} m")
    check_no_build_flatten(cfg, area, R)


def check_ortho(cfg, area, R: Report):
    ap = area_paths(cfg, area)
    tiles = area_tiles(cfg, area)
    have = 0
    from PIL import Image
    for t in tiles:
        tj = read_json(ap.tile_dir(t.name) / "tile.json", {}) or {}
        o = tj.get("ortho")
        if not o:
            continue
        im = Image.open(ap.tile_dir(t.name) / o["file"])
        R.ok("ortho", f"{t.name} 크기", im.size == (o["px"], o["px"]), f"{im.size}")
        have += 1
    if have == 0:
        R.add("ortho", "정사영상", "WARN", "없음 → Unity 단색 재질(기획서 대비책)")


class FacadeCheck:
    """계약 C1: 노드마다 프리미티브 2개([0] 벽 M_<cls>_wall, [1] 지붕 M_roof), 같은 속성 구성(POSITION·NORMAL·TEXCOORD_0),
    벽 v(Unity, 1 − glb v) 범위 = [0, height_m ÷ floor_height_m[cls]], 벽 u 는 정수 칸(변마다 n ≥ 1)."""
    TOL = 0.02

    def __init__(self, cfg):
        self.fh = cfg["buildings"]["floor_height_m"]
        self.n = self.two = self.attr_ok = self.mat_ok = self.v_ok = self.u_ok = 0
        self.bad: list[str] = []
        self.err_min = self.err_max = 0.0

    def add(self, node: dict, meta: dict | None):
        self.n += 1
        prims = node["primitives"]
        if len(prims) != 2:
            self.bad.append(f"{node['name']} 프리미티브 {len(prims)}개")
            return
        self.two += 1
        a0, a1 = (set(p["attributes"]) for p in prims)
        if a0 == a1 == {"POSITION", "NORMAL", "TEXCOORD_0"}:
            self.attr_ok += 1
        elif len(self.bad) < 20:
            self.bad.append(f"{node['name']} 속성 {sorted(a0)} / {sorted(a1)}")
        if meta is None:
            return
        cls = meta["usage_class"]
        if prims[0]["material"] == f"M_{cls}_wall" and prims[1]["material"] == "M_roof":
            self.mat_ok += 1
        if "TEXCOORD_0" not in prims[0]["attributes"]:
            return
        uv = from_gltf_uv(prims[0]["attributes"]["TEXCOORD_0"])
        idx = np.unique(prims[0]["indices"])
        v = uv[idx, 1]
        exp = meta["height_m"] / float(self.fh.get(cls, self.fh["other"]))
        e0, e1 = abs(float(v.min())), abs(float(v.max()) - exp)
        self.err_min, self.err_max = max(self.err_min, e0), max(self.err_max, e1)
        if e0 <= self.TOL and e1 <= self.TOL:
            self.v_ok += 1
        elif len(self.bad) < 20:
            self.bad.append(f"{node['name']} v [{v.min():.3f}, {v.max():.3f}] 기대 [0, {exp:.3f}]")
        u = uv[:, 0].reshape(-1, 4) if len(uv) % 4 == 0 else None    # 벽 사각형마다 (U0, U0+n, U0+n, U0)
        if u is not None and np.all(np.abs(u - np.round(u)) < 1e-3) and np.all(u[:, 1] - u[:, 0] >= 1 - 1e-3):
            self.u_ok += 1
        elif len(self.bad) < 20:
            self.bad.append(f"{node['name']} u 가 정수 칸이 아님")

    def report(self, R: "Report"):
        ok = self.n > 0 and self.two == self.attr_ok == self.v_ok == self.u_ok == self.mat_ok == self.n
        R.ok("buildings", "외벽 UV(C1: 프리미티브 [벽, 지붕]·같은 속성·벽 v ∈ [0, height_m ÷ 층고]·u 정수 칸)", ok,
             f"{self.n}동: 프리미티브 2개 {self.two}, 속성 같음 {self.attr_ok}, 재질 이름 {self.mat_ok}, v 범위 {self.v_ok}, "
             f"u 정수 칸 {self.u_ok}; 최대 오차 v_min {self.err_min:.4f}, v_max {self.err_max:.4f}(허용 {self.TOL})"
             + (f". 예: {'; '.join(self.bad[:5])}" if self.bad else ""))


def check_buildings(cfg, area, R: Report):
    a = area_cfg(cfg, area)
    ap = area_paths(cfg, area)
    total, floating, nonpos, in_zone, buried, buried_strict, half = 0, 0, 0, 0, 0, 0, 0
    worst: list[tuple[float, str]] = []
    facade = FacadeCheck(cfg)
    from shapely import contains_xy
    from .zones import no_build_polygons
    zones = no_build_polygons(cfg, area)
    lvl_path = ap.interim / "water_level.tif"
    lvl = rasterio.open(lvl_path) if lvl_path.exists() else None
    step = cfg["tile_size_m"] / (cfg["heightmap"]["resolution"] - 1)
    for t in area_tiles(cfg, area):
        d = ap.tile_dir(t.name)
        tj = read_json(d / "tile.json", {}) or {}
        b = tj.get("buildings")
        if not b:
            continue
        meta = read_json(d / b["meta"], [])
        nodes = [n for n in read_glb_nodes(d / b["file"]) if n["name"].startswith("B_")]
        R.ok("buildings", f"{t.name} 노드 수 = 메타 수", len(nodes) == len(meta) == b["count"],
             f"glb {len(nodes)}, json {len(meta)}, tile.json {b['count']}")
        by_node = {m["node"]: m for m in meta}
        for m in meta:
            total += 1
            if m["height_m"] <= 0:
                nonpos += 1
        for n in nodes:
            facade.add(n, by_node.get(n["name"]))
        raw = d / "heightmap.raw"
        if not raw.exists() or not meta:
            continue
        Zs = decode(raw.read_bytes(), cfg)
        # 전수 검사(Unity 와 같은 지형: 타일 RAW 이중선형). 외곽선 1 m 내부 격자 + 꼭짓점 표본.
        for n in nodes:
            name = n["name"]
            prims = n["primitives"]
            v = np.concatenate([from_gltf_axes(p["attributes"]["POSITION"]) for p in prims])
            bottom, top = float(v[:, 2].min()), float(v[:, 2].max())
            if len(prims) >= 2:      # v2: 바닥면이 없으므로 지붕 프리미티브로 외곽선을 만든다
                rv = from_gltf_axes(prims[1]["attributes"]["POSITION"])
                foot, xs, ys = footprint_grid(rv, prims[1]["indices"], level="top")
                xs, ys = np.concatenate([xs, v[:, 0]]), np.concatenate([ys, v[:, 1]])
            else:
                foot, xs, ys = footprint_grid(v, prims[0]["indices"])
            gz = raw_bilinear(Zs, xs, ys, step)
            rp = foot.representative_point() if foot is not None and not foot.is_empty else None
            cx, cy = (rp.x + t.e0, rp.y + t.n0) if rp is not None else (xs.mean() + t.e0, ys.mean() + t.n0)
            on_water = lvl is not None and not np.isnan(next(lvl.sample([(cx, cy)]))[0])
            if bottom > gz.min() + 0.5 and not on_water:
                floating += 1
            if top <= gz.max():
                buried_strict += 1
            if top < gz.max() - 0.5:
                buried += 1
                worst.append((top - float(gz.max()), name))
            if gz.max() - bottom > (top - bottom) / 2:
                half += 1
            for _, zg in zones:
                if contains_xy(zg, cx, cy):
                    in_zone += 1
    if total == 0:
        R.add("buildings", "건물", "WARN", "없음")
        return
    facade.report(R)
    R.ok("buildings", "높이 > 0", nonpos == 0, f"{nonpos}동 이상")
    R.ok("buildings", "떠 있는 건물(바닥 > 외곽선 안 최저 지면+0.5m, 수상 제외)", floating == 0,
         f"{floating}동(전수, 외곽선 1 m 내부 격자·RAW 이중선형)")
    worst.sort()
    R.add("buildings", "묻힌 건물(지붕 < 외곽선 안 최고 지면−0.5m)", "WARN" if buried else "PASS",
          f"{buried}동(허용 없이 {buried_strict}동, 높이 절반 넘게 묻힘 {half}동; 외곽선 1 m 내부 격자·RAW 이중선형)"
          + (f". 최악 {', '.join(f'{n} {d:+.2f} m' for d, n in worst[:3])}" if worst else "")
          + ". DSM interim 이면 일부 불가피(D-009)")
    R.ok("buildings", "건물 금지 구역(남이섬)", in_zone == 0, f"{in_zone}동")
    st = read_json(ap.reports / "buildings_stats.json", {})
    ratio = st.get("floors_default_ratio", 0) or 0
    R.add("buildings", "층수 기본값 비율", "WARN" if ratio > 0.3 else "PASS",
          f"{ratio:.0%} (목록: reports/floors_missing.csv)")
    R.add("buildings", "건물 수", "PASS", f"{total}동")


def check_water(cfg, area, R: Report):
    ap = area_paths(cfg, area)
    bodies = read_json(ap.interim / "water_bodies.json", [])
    if not bodies:
        R.add("water", "수면체", "WARN", "없음")
        return
    big = max(bodies, key=lambda b: b["area_m2"])
    lo, hi = WATER_LEVEL_RANGE
    R.ok("water", "주 수면 높이 상식 범위", lo <= big["level_m"] <= hi,
         f"{big['level_m']} m ({big['kind']}, {big['area_m2']/1e6:.2f} km²)", fail="WARN")
    kinds = {k: sum(1 for b in bodies if b["kind"] == k) for k in ("flat", "sloped")}
    # 평평한 수면인데 표고 범위가 큰 것은 도랑(trench)을 만든다
    trench = [b for b in bodies if b["kind"] == "flat" and b["range_m"] > 6.0 and b.get("compactness", 1) < 0.25]
    R.add("water", "수면체 분류", "WARN" if trench else "PASS",
          f"{len(bodies)}개 {kinds}; 도랑 의심(flat·범위>6m·가늘고 김) {len(trench)}개")
    n = 0
    for t in area_tiles(cfg, area):
        tj = read_json(ap.tile_dir(t.name) / "tile.json", {}) or {}
        if tj.get("water"):
            n += 1
    R.add("water", "수면 타일", "PASS" if n else "WARN", f"{n}개 타일")
    # 물가 급경사: 수면(넓힌 띠) 바깥 0~15m 안에서 경사 45° 초과 표본 비율
    dem = ap.interim / "dem_samples.tif"
    lvlp = ap.interim / "water_level.tif"
    if dem.exists() and lvlp.exists():
        from scipy import ndimage
        with rasterio.open(dem) as a_, rasterio.open(lvlp) as b_:
            z = a_.read(1, out_shape=(a_.height // 2, a_.width // 2))
            wmask = ~np.isnan(b_.read(1, out_shape=(b_.height // 2, b_.width // 2)))
            px = a_.res[0] * 2
        dist = ndimage.distance_transform_edt(~wmask) * px
        band = (dist > 0) & (dist <= 15)
        gy, gx = np.gradient(z, px)
        steep = band & (np.hypot(gx, gy) > 1.0)
        ratio = steep.sum() / max(1, band.sum())
        R.add("water", "물가 급경사(0~15m, >45°)", "WARN" if ratio > 0.02 else "PASS", f"{ratio:.2%} ({int(steep.sum())}표본, 1/2 해상도)")
        # 수면 계단: 이웃한 수면 표본 사이 높이 차(수면 메시가 이 높이장을 따른다)
        with rasterio.open(lvlp) as b_:
            f = b_.read(1)
        dx = np.abs(np.diff(f, axis=1))
        dy = np.abs(np.diff(f, axis=0))
        step = float(max(np.nanmax(dx) if np.isfinite(dx).any() else 0, np.nanmax(dy) if np.isfinite(dy).any() else 0))
        R.add("water", "수면 계단(이웃 표본 최대 높이 차)", "WARN" if step > 0.3 else "PASS", f"{step:.2f} m")
        del dx, dy
    owner_p = ap.interim / "water_body_id.tif"
    if dem.exists() and lvlp.exists() and owner_p.exists():
        check_water_bodies(cfg, area, R, bodies, dem, lvlp, owner_p)
        check_water_mesh(cfg, area, R, dem, lvlp, owner_p)
    elif dem.exists() and lvlp.exists():
        R.add("water", "수면체 번호 래스터", "WARN", "water_body_id.tif 없음 → dem 단계를 다시 실행")


WALL_ABOVE_WATER_M = 4.0     # Unity ShoreRules.WallAboveWater(물가 벽 윗면 = 수면 + 4 m)
STEP_OFFSET_M = 0.5          # Unity CharacterController.stepOffset
UNITY_KEY_M = 0.05           # Unity 물가 벽 변 찾기: 정점 (x, z) 를 5 cm 격자로 반올림해 묶는다


def water_glb_nodes(cfg, area) -> list[tuple[Any, str, np.ndarray, np.ndarray]]:
    """타일 water.glb 노드 → [(타일, 노드 이름, 로컬 정점 (n, 3) E·N·Up, 면)]."""
    ap = area_paths(cfg, area)
    out = []
    for t in area_tiles(cfg, area):
        tj = read_json(ap.tile_dir(t.name) / "tile.json", {}) or {}
        if not tj.get("water"):
            continue
        sc = trimesh.load(ap.tile_dir(t.name) / tj["water"]["file"], force="scene")
        for name, g in sc.geometry.items():
            out.append((t, str(name), from_gltf_axes(np.asarray(g.vertices)), np.asarray(g.faces, dtype=np.int64)))
    return out


def unity_boundary_edges(V: np.ndarray, F: np.ndarray, tile_size: float = 1000.0):
    """Unity WorldImporter.ShoreWall 과 같은 규칙: (x, z) 를 5 cm 격자 키로 묶어 한 번만 쓰인 변, 타일 경계 위 변 제외.
    돌려주는 값: (A (n, 3), B (n, 3), C (n, 3) 그 변을 가진 삼각형의 남은 꼭짓점) — 로컬 좌표."""
    V32 = V.astype(np.float32).astype(np.float64)
    k = np.rint(V32[:, :2] / UNITY_KEY_M).astype(np.int64)
    key = k[:, 0] * (1 << 32) + k[:, 1]
    e = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    opp = np.concatenate([F[:, 2], F[:, 0], F[:, 1]])
    ka, kb = key[e[:, 0]], key[e[:, 1]]
    pair = np.stack([np.minimum(ka, kb), np.maximum(ka, kb)], axis=1)
    _, first, inv, cnt = np.unique(pair, axis=0, return_index=True, return_inverse=True, return_counts=True)
    one = cnt[inv.ravel()] == 1
    A, B, C = V32[e[one, 0]], V32[e[one, 1]], V32[opp[one]]
    eps = 0.05

    def on(p):
        return (np.abs(p[:, 0]) < eps) | (np.abs(p[:, 0] - tile_size) < eps) | (np.abs(p[:, 1]) < eps) | (np.abs(p[:, 1] - tile_size) < eps)
    tile_cut = on(A) & on(B) & ((np.abs(A[:, 0] - B[:, 0]) < eps) | (np.abs(A[:, 1] - B[:, 1]) < eps))
    keep = ~tile_cut & (np.hypot(*(A[:, :2] - B[:, :2]).T) > 1e-6)
    return A[keep], B[keep], C[keep]


def check_water_mesh(cfg, area, R: Report, dem_p, lvl_p, owner_p):
    """water.glb 보이는 수면 자르기(D-007, 06a) 검사. Unity 물가 벽(윗면 = 수면 + 4 m)이 서는 메시 경계 변 기준.

    1) 메시 경계 지형 − 수면 분포(잘린 경계 / 넓힌 띠 경계)와 잘린 경계의 틈(수면 가장자리가 지형 위 = 지형 − 수면 < 0).
    2) 묻힌 수면 옆 물가 벽 돌출: 경계 변 안쪽 0.5·1·2 m 수면이 모두 지형에 가려졌는데(지형 > 수면 + 0.05) 벽 윗면이 지면보다
       stepOffset(0.5 m) 넘게 솟은 변. 5차 QA 기준(가장 가까운 폴리곤 안 수면 표본이 0.3 m 넘게 묻힘)도 함께 적는다.
    3) 수면체별 메시 넓이(자르기 전 → 뒤)와 잃은 '보이는 수면' 넓이.
    4) 교량 상판 밑: 물에 닿는 다리 중심선 중 수면 폴리곤 안 표본이 water.glb 에 덮이는가(빠진 곳은 묻힌 수면이어야 한다)."""
    import shapely
    from scipy.spatial import cKDTree

    from .bridges import load_bridges
    from .dem import bridge_zones, water_polygons
    from .surface import TriSurface
    from .terrain import TerrainSampler
    from .water import _fill_level, body_areas, clip_params, mesh_footprint, tile_water_meshes
    cp = clip_params(cfg)
    clip = cp["above_m"]
    nodes = water_glb_nodes(cfg, area)
    if not nodes:
        R.add("water", "수면 메시(water.glb)", "WARN", "없음")
        return
    terrain = TerrainSampler(cfg, area)
    with rasterio.open(lvl_p) as s:
        Fr = s.read(1)
        T = s.transform
    with rasterio.open(dem_p) as s:
        Zr = s.read(1)
    with rasterio.open(owner_p) as s:
        own = s.read(1)
    bounds = area_bounds(cfg, area)
    polys = water_polygons(cfg, area, bounds)
    full = tile_water_meshes(cfg, area, polys, _fill_level(Fr.astype(np.float64)), T)
    fp0 = mesh_footprint(full)
    surf0 = TriSurface.concat([(np.column_stack([P, z]), F) for ms in full.values() for _, P, F, z in ms])
    parts, glb_meshes = [], {}
    for t, name, V, F in nodes:
        Vw = V + [t.e0, t.n0, 0.0]
        parts.append((Vw, F))
        glb_meshes.setdefault(t.name, []).append((name, Vw[:, :2], F, Vw[:, 2]))
    surf = TriSurface.concat(parts)

    # 1) 경계 변
    As, Bs, Cs = [], [], []
    for t, name, V, F in nodes:
        A, B, C = unity_boundary_edges(V, F, float(cfg["tile_size_m"]))
        off = np.array([t.e0, t.n0, 0.0])
        As.append(A + off); Bs.append(B + off); Cs.append(C + off)
    A, B, C = np.concatenate(As), np.concatenate(Bs), np.concatenate(Cs)
    M = (A + B) / 2.0
    L = np.hypot(*(A[:, :2] - B[:, :2]).T)
    tm = terrain.height(M[:, 0], M[:, 1])
    dm = tm - M[:, 2]
    dmin = np.full(len(M), np.inf)                         # 변 위 5점(양 끝·¼·½·¾) 지형 − 수면 최소
    for f in (0.0, 0.25, 0.5, 0.75, 1.0):
        Q = A + (B - A) * f
        dmin = np.fmin(dmin, terrain.height(Q[:, 0], Q[:, 1]) - Q[:, 2])
    bnd0 = shapely.boundary(fp0)
    shapely.prepare(bnd0)
    tol = 0.1                                              # 짧은 변 접기(water.CLIP_MIN_SEP_M 0.08 m)로 옮겨진 원래 경계 정점 허용
    on0 = (shapely.dwithin(bnd0, shapely.points(M[:, :2]), tol) & shapely.dwithin(bnd0, shapely.points(A[:, :2]), tol)
           & shapely.dwithin(bnd0, shapely.points(B[:, :2]), tol))
    cut = ~on0                                             # 잘린 경계(넓힌 띠 경계가 아닌 변)
    ok = np.isfinite(dm) & np.isfinite(dmin)

    def pct(x):
        x = x[np.isfinite(x)]
        return "없음" if not x.size else (f"p1 {np.percentile(x, 1):+.2f}, p5 {np.percentile(x, 5):+.2f}, 중앙 {np.median(x):+.2f}, "
                                           f"p95 {np.percentile(x, 95):+.2f}, 최소 {x.min():+.2f}, 최대 {x.max():+.2f} m")
    gap = cut & ok & (dmin < -0.05)
    gap0 = cut & ok & (dmin < 0.0)
    R.ok("water", f"잘린 수면 경계 틈 없음(지형 − 수면 ≥ −0.05 m, 변마다 5점, 기준 {clip:g} m)", not gap.any(),
         f"경계 변 {ok.sum():,}개 {L[ok].sum():,.0f} m 중 잘린 경계 {int((cut & ok).sum()):,}개 {L[cut & ok].sum():,.0f} m: 지형 − 수면(중점) "
         f"{pct(dm[cut & ok])}, 5점 최소 {pct(dmin[cut & ok])}; 0 m 미만 {int(gap0.sum())}개 {L[gap0].sum():.1f} m, −0.05 m 미만 {int(gap.sum())}개 {L[gap].sum():.1f} m"
         + (f"(최악 E{M[gap, 0][np.argmin(dmin[gap])]:.1f} N{M[gap, 1][np.argmin(dmin[gap])]:.1f} {dmin[gap].min():+.2f} m)" if gap.any() else "")
         + f". 넓힌 띠 경계(자르기 전과 같음) {int((~cut & ok).sum()):,}개 {L[~cut & ok].sum():,.0f} m: 지형 − 수면 {pct(dm[~cut & ok])}", fail="WARN")

    # 2) 묻힌 수면 옆 돌출 벽: 변 안쪽 0.5·1 m 수면이 둘 다 자르기 기준 + 0.1 m 넘게 묻혔는데 벽이 지면 위로 솟음
    d2 = B[:, :2] - A[:, :2]
    nrm = np.column_stack([-d2[:, 1], d2[:, 0]]) / np.maximum(L, 1e-9)[:, None]
    side = np.sign(((C[:, :2] - M[:, :2]) * nrm).sum(1))
    nrm *= np.where(side == 0, 1.0, side)[:, None]          # 메시 안쪽을 향하게
    din = {}
    for dd in (0.5, 1.0, 2.0):
        q = M[:, :2] + nrm * dd
        din[dd] = terrain.height(q[:, 0], q[:, 1]) - surf.height(q[:, 0], q[:, 1])
    inner = np.fmin(din[0.5], din[1.0])                      # 안쪽 면이 없으면(NaN) 다른 점으로
    vis2 = (din[0.5] <= 0.05) | (din[1.0] <= 0.05) | (din[2.0] <= 0.05)
    protr = M[:, 2] + WALL_ABOVE_WATER_M - tm
    bad = ok & np.isfinite(inner) & (inner > clip + 0.1) & (protr > STEP_OFFSET_M)
    cl = cut & ok
    # 5차 QA 기준: 변 중점에서 가장 가까운 수면 폴리곤 안 표본(래스터)이 0.3 m 넘게 묻힘
    wet = np.isfinite(Fr)
    rr, cc = np.nonzero(wet)
    xs, ys = T * (cc + 0.5, rr + 0.5)
    _, j = cKDTree(np.column_stack([xs, ys])).query(M[:, :2])
    inner_bur = (Zr[rr[j], cc[j]] - Fr[rr[j], cc[j]]) > 0.3
    bad_q5 = ok & inner_bur & (protr > STEP_OFFSET_M)
    bad_q5c = ok & ((Zr[rr[j], cc[j]] - Fr[rr[j], cc[j]]) > clip + 0.1) & (protr > STEP_OFFSET_M)
    body = own[rr[j], cc[j]]

    def by_body(sel):
        if not sel.any():
            return ""
        rows = []
        for bd in sorted(set(body[sel].tolist())):
            k = sel & (body == bd)
            rows.append(f"수면체 {bd} {int(k.sum())}변 {L[k].sum():.0f} m(최대 {protr[k].max():.2f} m)")
        return ": " + ", ".join(rows)
    R.ok("water", f"묻힌 수면 옆 물가 벽 없음(변 안쪽 0.5·1 m 수면이 모두 > {clip + 0.1:g} m 묻힘 & 벽 윗면 = 수면 + {WALL_ABOVE_WATER_M:g} m 가 지면 위 > {STEP_OFFSET_M:g} m)",
         not bad.any(), f"{int(bad.sum())}변 {L[bad].sum():.1f} m" + by_body(bad)
         + f". 잘린 경계 {L[cl].sum():,.0f} m 중 안쪽 2 m 안에 보이는 수면(지형 ≤ 수면 + 0.05 m)이 있는 변 {L[cl & vis2].sum() / max(L[cl].sum(), 1):.1%}"
         + f"(벽과 보이는 물가선 사이 띠 = 지형이 수면보다 0~{clip:g} m 높은 물가)"
         + f". 참고 5차 QA 기준(변 중점에서 가장 가까운 폴리곤 안 래스터 표본 묻힘 > 0.3 m): {int(bad_q5.sum())}변 {L[bad_q5].sum():.0f} m"
         + f"(> {clip + 0.1:g} m로: {int(bad_q5c.sum())}변 {L[bad_q5c].sum():.0f} m) — 잘린 경계는 폴리곤 안에 있어 가장 가까운 표본이 뭍 쪽에 떨어지므로"
         + " 자른 뒤에는 이 기준이 보이는 물가의 벽도 센다", fail="WARN")

    # 3) 수면체별 넓이
    b0 = body_areas(cfg, area, full, own, T, polys)
    b1 = body_areas(cfg, area, glb_meshes, own, T, polys)
    tot0 = sum(b0.values())
    tot1 = sum(b1.values())
    rows = []
    for k in sorted(set(b0) | set(b1)):
        a0, a1 = b0.get(k, 0.0), b1.get(k, 0.0)
        if a0 - a1 > 50.0 or (a0 > 0 and a1 / a0 < 0.9):
            rows.append(f"{k}: {a0:,.0f} → {a1:,.0f} m²({(a1 / a0 if a0 else 0):.0%})")
    # 잃은 '보이는 수면'(자르기 전 메시가 덮고 지형 − 수면 < 0 인데 지금 메시가 없는 곳), 2 m 격자
    minx, miny, maxx, maxy = shapely.bounds(fp0)
    gx, gy = np.meshgrid(np.arange(minx + 1.0, maxx, 2.0), np.arange(miny + 1.0, maxy, 2.0))
    gx, gy = gx.ravel(), gy.ravel()
    inn = shapely.contains_xy(fp0, gx, gy)
    gx, gy = gx[inn], gy[inn]
    w0 = np.full(len(gx), np.nan)
    w1 = np.full(len(gx), np.nan)
    for a in range(0, len(gx), 300_000):
        w0[a:a + 300_000] = surf0.height(gx[a:a + 300_000], gy[a:a + 300_000])
        w1[a:a + 300_000] = surf.height(gx[a:a + 300_000], gy[a:a + 300_000])
    tg = terrain.height(gx, gy)
    visible0 = np.isfinite(w0) & (tg < w0)
    lost = visible0 & ~np.isfinite(w1)
    lost_m2, vis_m2 = float(lost.sum()) * 4.0, float(visible0.sum()) * 4.0
    hidden_left = np.isfinite(w1) & (tg > w1 + 0.05)
    R.ok("water", "수면 메시 넓이(자르기 전 → 뒤, 수면체별)·잃은 보이는 수면(< 0.5%)", lost_m2 <= 0.005 * max(vis_m2, 1.0),
         f"전체 {tot0:,.0f} → {tot1:,.0f} m²({tot1 / max(tot0, 1):.1%}); 10% 넘게 또는 50 m² 넘게 준 수면체: " + ("; ".join(rows) if rows else "없음")
         + f". 보이는 수면(지형 < 수면) {vis_m2:,.0f} m² 중 잃은 곳 {lost_m2:,.0f} m²({lost_m2 / max(vis_m2, 1):.2%}, 2 m 격자)"
         + f"; 남은 메시 중 지형에 가려진 곳(> 수면 + 0.05 m) {float(hidden_left.sum()) * 4.0:,.0f} m²"
         + f"({float(hidden_left.sum()) / max(1, int(np.isfinite(w1).sum())):.2%}, 자르기 기준 {clip:g} m 안의 물가 띠)", fail="WARN")

    # 4) 교량 상판 밑 끊김
    _, _, over = bridge_zones(load_bridges(cfg, area), polys, Fr.shape, T, abs(T.a), 0.0)
    wu = shapely.union_all(polys)
    shapely.prepare(wu)
    n_in = n_miss = n_miss_vis = 0
    miss_w = []
    for b in over:
        g = b.geometry
        s_ = np.arange(0.5, g.length, 1.0)
        if not len(s_):
            continue
        pts = shapely.line_interpolate_point(g, s_)
        px_, py_ = shapely.get_x(pts), shapely.get_y(pts)
        inside = shapely.contains_xy(wu, px_, py_)
        if not inside.any():
            continue
        px_, py_ = px_[inside], py_[inside]
        n_in += len(px_)
        w1b = surf.height(px_, py_)
        miss = ~np.isfinite(w1b)
        if miss.any():
            w0b = surf0.height(px_[miss], py_[miss])
            tb_ = terrain.height(px_[miss], py_[miss])
            visb = ~(tb_ - w0b >= clip - 0.1)
            n_miss += int(miss.sum())
            n_miss_vis += int(visb.sum())
            if visb.any():
                miss_w.append(f"w{b.osm_id} {int(visb.sum())}표본")
    R.ok("water", "교량 상판 밑 수면 끊김 없음(물에 닿는 다리 중심선, 수면 폴리곤 안 1 m 표본)", n_miss_vis == 0,
         f"다리 {len(over)}개, 폴리곤 안 표본 {n_in:,}개 중 water.glb 없음 {n_miss}개"
         + f"(그중 묻히지 않은 곳 {n_miss_vis}개{': ' + ', '.join(miss_w[:6]) if miss_w else ''}; 나머지는 지형 − 수면 ≥ {clip - 0.1:g} m 묻힌 곳)",
         fail="WARN")


def check_water_bodies(cfg, area, R: Report, bodies, dem_p, lvl_p, owner_p):
    """수면체별 검사: 하류 역경사, 지형에 가려진 수면 비율, 다리 자리 지형 돌출."""
    from .dem import flow_lines
    with rasterio.open(lvl_p) as s:
        F = s.read(1)
        T = s.transform
    with rasterio.open(dem_p) as s:
        Z = s.read(1)
    with rasterio.open(owner_p) as s:
        own = s.read(1)
    kinds = {b["id"]: b["kind"] for b in bodies}

    # 1) 하류 역경사: OSM 물줄기 선(흐름 방향)을 2 m 간격으로 따라가며 상류 최저값보다 높아진 양
    rises = profile_rises(F, own, T, flow_lines(cfg, area))
    bad = {k: v for k, v in rises.items() if v[0] > RISE_WARN_M}
    shown = {k: v for k, v in rises.items() if kinds.get(k) == "sloped" or k in bad}
    detail = "; ".join(f"수면체 {k}({kinds.get(k, '?')}) {v[0]:.2f} m" + (f" @E{v[1]:.0f} N{v[2]:.0f}" if v[0] > RISE_WARN_M else "")
                       for k, v in sorted(shown.items()))
    R.add("water", f"하류 역경사(물줄기 중심선 종단, >{RISE_WARN_M} m)", "WARN" if bad else "PASS",
          detail or "물줄기 선이 지나는 경사 수면 없음")

    # 2) 수면체별 가려진 수면 비율(최종 지형 > 수면 + 0.05 m)
    wet = own >= 0
    idx = own[wet].astype(np.int64)
    hidden = (Z[wet] > F[wet] + 0.05).astype(np.float64)
    n = np.bincount(idx)
    h = np.bincount(idx, weights=hidden, minlength=len(n))
    rows = [(h[i] / n[i], i) for i in range(len(n)) if n[i] >= 100]
    warn = [(r, i) for r, i in rows if r > HIDDEN_WARN]
    listed = sorted((r, i) for r, i in rows if kinds.get(i) == "sloped" or r > HIDDEN_WARN)
    R.add("water", f"지형에 가려진 수면(수면체별, >{HIDDEN_WARN:.0%} 경고)", "WARN" if warn else "PASS",
          "; ".join(f"수면체 {i}({kinds.get(i, '?')}) {r:.2%}" for r, i in sorted(listed, key=lambda x: x[1]))
          + f". 전체 {float(h.sum() / max(1, n.sum())):.2%}")
    del hidden, idx

    # 3) 다리 자리: 수면 위 다리 footprint(+DSM 여유폭) 안의 지형은 수면보다 낮아야 한다(D-007)
    from .bridges import load_bridges
    from .dem import bridge_zones, dem_is_dsm, water_polygons
    from .grid import area_bounds
    margin = float(cfg.get("dem", {}).get("dsm_bridge_margin_m", 0.0)) if dem_is_dsm(cfg) else 0.0
    polys = water_polygons(cfg, area, area_bounds(cfg, area))
    foot, _, over = bridge_zones(load_bridges(cfg, area), polys, F.shape, T, abs(T.a), margin)
    fw = foot & wet
    poke = fw & (Z > F - 0.25)
    R.ok("water", "다리 자리 지형(수면 위 footprint 안 지형 < 수면)", int(poke.sum()) == 0,
         f"물에 닿는 다리 {len(over)}개, 수면 위 다리 자리 {int(fw.sum()):,}표본 중 수면−0.25 m 보다 높은 지형 {int(poke.sum())}표본",
         fail="WARN")


def check_no_build_flatten(cfg, area, R: Report):
    """금지 구역(남이섬) 안에서 DSM 건물 평탄화가 일어나지 않았는가(원 DSM 대비, 깎기 구역 제외)."""
    from rasterio.features import rasterize
    from scipy import ndimage

    from .dem import build_mosaic, dem_is_dsm
    from .grid import area_bounds
    from .zones import no_build_polygons
    ap = area_paths(cfg, area)
    zones = no_build_polygons(cfg, area)
    if not zones:
        R.add("dem", "금지 구역 평탄화 없음", "PASS", "금지 구역 없음")
        return
    if not (dem_is_dsm(cfg) and cfg.get("dem", {}).get("dsm_flatten_buildings")):
        R.add("dem", "금지 구역 평탄화 없음", "PASS", "DSM 평탄화 꺼짐(공식 DEM)")
        return
    dem_p, lvl_p = ap.interim / "dem_samples.tif", ap.interim / "water_level.tif"
    if not dem_p.exists() or not lvl_p.exists():
        R.add("dem", "금지 구역 평탄화 없음", "WARN", "dem_samples.tif / water_level.tif 없음")
        return
    z0, T, _ = build_mosaic(cfg, area_bounds(cfg, area))
    with rasterio.open(dem_p) as s:
        Z = s.read(1)
    with rasterio.open(lvl_p) as s:
        wet = ~np.isnan(s.read(1))
    px = abs(T.a)
    reach = cfg["water"].get("shore_ramp_m", 20.0) + 3.0
    zmask = rasterize([(g, 1) for _, g in zones], out_shape=Z.shape, transform=T, fill=0, dtype="uint8").astype(bool)
    rows, cols = np.nonzero(zmask)
    m_ = int(reach / px) + 4
    sl = (slice(max(0, rows.min() - m_), rows.max() + m_ + 1), slice(max(0, cols.min() - m_), cols.max() + m_ + 1))
    near_water = ndimage.distance_transform_edt(~wet[sl]) * px <= reach        # 하상 깎기 구역은 제외
    low = zmask[sl] & ~near_water & ((z0[sl] - Z[sl]) > NOBUILD_LOWER_M)
    lab, n = ndimage.label(low, structure=np.ones((3, 3)))
    depth = float((z0[sl] - Z[sl])[zmask[sl] & ~near_water].max()) if (zmask[sl] & ~near_water).any() else 0.0
    R.ok("dem", "금지 구역(남이섬) 평탄화 없음(원 DSM 대비, 물가 깎기 구역 제외)", n == 0,
         f"{NOBUILD_LOWER_M} m 넘게 낮아진 곳 {n}곳 {low.sum() * px * px:,.0f} m², 최대 낮아짐 {max(0.0, depth):.2f} m",
         fail="WARN")


def check_export(cfg, area, R: Report):
    ap = area_paths(cfg, area)
    m = read_json(ap.out / "manifest.json", None)
    if m is None:
        R.add("export", "manifest", "FAIL", "없음")
        return
    R.ok("export", "manifest 타일 수", len(m["tiles"]) == len(area_tiles(cfg, area)), f"{len(m['tiles'])}")
    from .export import MANIFEST_VERSION
    fac = m.get("facade") or {}
    v2 = (m.get("manifest_version") == MANIFEST_VERSION == 2 and set(fac) >= {"bay_m", "roof_tile_m", "floor_height_m"}
          and "skirt" in m)
    R.ok("export", "manifest_version 2(facade·skirt 최상위, 타일 roads·bridges)", v2,
         f"version {m.get('manifest_version')}, facade bay_m {fac.get('bay_m')}, roof_tile_m {fac.get('roof_tile_m')}, "
         f"skirt {'있음' if m.get('skirt') else 'null'}")
    from .export import water_spec
    wm = m.get("water")
    R.ok("export", "manifest water(shore_overlap_m·shore_clip_above_m = config, Unity 가 config 를 읽지 않게)",
         isinstance(wm, dict) and wm == water_spec(cfg), f"manifest {wm}, config {water_spec(cfg)}")
    pv = m.get("provenance") or {}
    R.ok("export", "provenance 에 수면 흐름 방향 원천(waterways)", bool(pv.get("waterways")) or not sources.select(cfg, "waterways"),
         f"waterways {'있음' if pv.get('waterways') else '없음'}, 레이어 {sorted(pv)}")
    sk = m.get("skirt")
    if sk:
        R.ok("export", "skirt 파일·필드(계약 C4)", (ap.out / sk["file"]).exists()
             and set(sk) >= {"file", "bounds", "margin_m", "spacing_m", "inner_overlap_m", "interim"},
             f"{sk['file']} margin {sk['margin_m']} spacing {sk['spacing_m']} overlap {sk['inner_overlap_m']}")
    keys_missing = []
    for t in area_tiles(cfg, area):
        tj = read_json(ap.tile_dir(t.name) / "tile.json", {}) or {}
        if "roads" not in tj or "bridges" not in tj:
            keys_missing.append(t.name)
    R.ok("export", "tile.json roads·bridges 키(없으면 null)", not keys_missing, f"키 없는 타일 {keys_missing[:5]}")
    R.ok("export", "원점 일치", m["origin"] == cfg["origin"], str(m["origin"]))
    ax = trimesh.load(ap.out / m["axis_test"], force="scene")
    east = ax.geometry.get("AXIS_East100")
    if east is None:
        R.add("export", "axis_test", "FAIL", "AXIS_East100 없음")
    else:
        v = from_gltf_axes(east.vertices)
        R.ok("export", "axis_test 동쪽 팔 +E", v[:, 0].max() > 95 and v[:, 1].max() < 10,
             f"E {v[:,0].min():.0f}~{v[:,0].max():.0f}, N {v[:,1].min():.0f}~{v[:,1].max():.0f}")
    sp = m.get("spawn")
    if not sp:
        R.add("export", "스폰 위치", "FAIL", "manifest.spawn 없음")
    else:
        R.ok("export", "스폰 위치(육지·물가에서 떨어짐)", sp["shore_distance_m"] >= cfg["spawn"]["min_shore_distance_m"],
             f"E{sp['e']} N{sp['n']} 지면 {sp['ground_m']}m, 물가까지 {sp['shore_distance_m']}m, 기준점에서 {sp['from_near_m']}m")
        check_spawn_mesh(cfg, area, R, sp)
    if m.get("interim_layers"):
        R.add("export", "interim 레이어", "WARN", ", ".join(m["interim_layers"]))


def check_spawn_mesh(cfg, area, R: Report, sp):
    """스폰 지점을 실제 water.glb 삼각형과 대조: 수면 메시 위가 아니고, 물가(메시 끝 = Unity 물가 벽)에서 떨어져 있는가."""
    ap = area_paths(cfg, area)
    E, N = float(sp["e"]), float(sp["n"])
    ts = cfg["tile_size_m"]
    inside, best, best_tile = False, np.inf, None
    for t in area_tiles(cfg, area):
        tj = read_json(ap.tile_dir(t.name) / "tile.json", {}) or {}
        if not tj.get("water"):
            continue
        scene = trimesh.load(ap.tile_dir(t.name) / tj["water"]["file"], force="scene")
        for g in scene.geometry.values():
            V = from_gltf_axes(g.vertices)
            Vu, inv = np.unique(np.round(V, 3), axis=0, return_inverse=True)   # 같은 위치 정점 용접
            Fw = inv.ravel()[np.asarray(g.faces)]
            P = Vu[Fw][:, :, :2] + [t.e0, t.n0]
            if point_in_triangles(P, E, N):
                inside = True
            edges = mesh_boundary_edges(Vu, Fw, ts)
            if len(edges) == 0:
                continue
            A = edges[:, 0, :2] + [t.e0, t.n0]
            B = edges[:, 1, :2] + [t.e0, t.n0]
            AB, AP = B - A, np.array([E, N]) - A
            u = np.clip((AP * AB).sum(1) / np.maximum((AB * AB).sum(1), 1e-9), 0, 1)
            d = float(np.hypot(*(A + AB * u[:, None] - [E, N]).T).min())
            if d < best:
                best, best_tile = d, t.name
    need = cfg["spawn"]["min_shore_distance_m"] - 1.0      # 래스터(약 1 m) ↔ 메시 이산화 차 허용
    if inside:
        R.add("export", "스폰 ↔ water.glb", "FAIL", f"스폰 E{E} N{N} 이 수면 삼각형 안")
    else:
        R.ok("export", "스폰 ↔ water.glb(수면 삼각형 밖, 물가 벽까지 거리)", best >= need,
             f"수면 삼각형 밖, 가장 가까운 물가(메시 끝) {best:.1f} m ({best_tile}), 기준 {need:.0f} m 이상", fail="WARN")



# --------------------------------------------------------------------------- v2: 도로 마스크(C2)·교량(C3)
def top_surface_at(V: np.ndarray, F: np.ndarray, x: np.ndarray, y: np.ndarray, min_nz: float = 0.5,
                   lowest: bool = True) -> np.ndarray:
    """위를 향한 삼각형(법선 z > min_nz) 중 점 (x, y) 를 덮는 것의 높이(없으면 NaN). V 는 로컬 E, N, Up.
    lowest=True 면 가장 낮은 면(교량: 상판 윗면. 난간 윗면은 1 m 위라서 급히 꺾인 곳에서 겹쳐도 상판을 고른다)."""
    out = np.full(len(x), np.nan)
    A, B, C = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    n = np.cross(B - A, C - A)
    nz = n[:, 2] / np.maximum(np.linalg.norm(n, axis=1), 1e-12)
    up = nz > min_nz
    A, B, C = A[up], B[up], C[up]
    if not len(A):
        return out
    det = (B[:, 1] - C[:, 1]) * (A[:, 0] - C[:, 0]) + (C[:, 0] - B[:, 0]) * (A[:, 1] - C[:, 1])
    ok = np.abs(det) > 1e-12
    A, B, C, det = A[ok], B[ok], C[ok], det[ok]
    tmin = np.minimum(np.minimum(A[:, :2], B[:, :2]), C[:, :2])
    tmax = np.maximum(np.maximum(A[:, :2], B[:, :2]), C[:, :2])
    for k in range(len(x)):
        px, py = x[k], y[k]
        m = (tmin[:, 0] <= px + 1e-6) & (tmax[:, 0] >= px - 1e-6) & (tmin[:, 1] <= py + 1e-6) & (tmax[:, 1] >= py - 1e-6)
        if not m.any():
            continue
        a, b, c, d = A[m], B[m], C[m], det[m]
        l1 = ((b[:, 1] - c[:, 1]) * (px - c[:, 0]) + (c[:, 0] - b[:, 0]) * (py - c[:, 1])) / d
        l2 = ((c[:, 1] - a[:, 1]) * (px - c[:, 0]) + (a[:, 0] - c[:, 0]) * (py - c[:, 1])) / d
        l3 = 1 - l1 - l2
        inside = (l1 >= -1e-6) & (l2 >= -1e-6) & (l3 >= -1e-6)
        if inside.any():
            z = l1 * a[:, 2] + l2 * b[:, 2] + l3 * c[:, 2]
            out[k] = float(z[inside].min() if lowest else z[inside].max())
    return out


def check_roads(cfg, area, R: Report):
    check_roads_mask(cfg, area, R)
    check_bridges(cfg, area, R)


def check_roads_mask(cfg, area, R: Report):
    """C2: 마스크 형식, 타일별 덮임 비율(래스터 ↔ 면 넓이), 행·열 방향(알려진 도로 점이 켜져 있는가)."""
    from PIL import Image
    from shapely import STRtree
    from shapely.geometry import box
    from shapely.ops import unary_union
    from pyproj import Transformer

    from .roads import mask_features, world_to_pixel
    ap = area_paths(cfg, area)
    px = int(cfg["roads"].get("mask_px", 1024))
    ts = float(cfg["tile_size_m"])
    feats, _ = mask_features(cfg, area)
    tree = STRtree([f.polygon for f in feats])
    n_tiles = fmt_bad = 0
    worst_rel, worst_tile = 0.0, ""
    worst_abs = 0.0
    hits = flips = flipc = tested = 0
    missing = []
    tot_cov = np.zeros(3)
    for t in area_tiles(cfg, area):
        d = ap.tile_dir(t.name)
        tj = read_json(d / "tile.json", {}) or {}
        tb = box(*t.bounds)
        cand = [feats[i] for i in tree.query(tb, predicate="intersects")]
        r = tj.get("roads")
        if not r:
            if cand:
                missing.append(t.name)
            continue
        n_tiles += 1
        im = Image.open(d / r["mask"])
        if im.size != (px, px) or im.mode != "RGB":
            fmt_bad += 1
            continue
        a = np.asarray(im)
        for ch in range(3):
            geoms = [f.polygon for f in cand if f.channel == ch]
            exp = unary_union(geoms).intersection(tb).area / (ts * ts) if geoms else 0.0
            got = float(a[:, :, ch].mean() / 255.0)
            tot_cov[ch] += got
            diff = abs(got - exp)
            rel = diff / exp if exp > 1e-4 else (0.0 if got < 1e-4 else 1.0)
            if diff > 2e-4 and rel > worst_rel:
                worst_rel, worst_tile = rel, f"{t.name} {'RGB'[ch]} 래스터 {got:.4f} / 면 {exp:.4f}"
            worst_abs = max(worst_abs, diff)
        # 방향: 차도 중심선 중간점(폭 6 m 이상, 타일 가장자리 10 m 밖)이 R 로 켜져 있는가, 뒤집은 행·열은?
        for f in cand:
            if f.channel != 0 or f.width_m < 6.0 or f.line.geom_type != "LineString":
                continue
            p = f.line.interpolate(0.5, normalized=True)
            if not (t.e0 + 10 <= p.x <= t.e0 + ts - 10 and t.n0 + 10 <= p.y <= t.n0 + ts - 10):
                continue
            c, rr = world_to_pixel(t.e0, t.n0, p.x, p.y, ts, px)
            tested += 1
            hits += int(a[rr, c, 0] >= 200)
            flips += int(a[px - 1 - rr, c, 0] >= 200)
            flipc += int(a[rr, px - 1 - c, 0] >= 200)
    R.ok("roads", "도로 마스크 타일(도로가 지나는 타일 = roads 있음, 1024² RGB)", not missing and fmt_bad == 0 and n_tiles > 0,
         f"{n_tiles}타일, 빠진 타일 {missing[:5]}, 형식 오류 {fmt_bad}")
    from .roads import width_outliers
    wo = width_outliers(cfg, area)
    ratio = float(cfg["roads"].get("width_outlier_ratio", 3.0))
    R.add("roads", f"도로·교량 폭 이상값 거름(width·lanes 폭 > 등급 기본값 × {ratio:g} → 다음 규칙)", "WARN" if wo else "PASS",
          f"{len(wo)}건" + (": " + ", ".join(f"{w['osm_id']}({w['class']}) {w['rejected']} → {w['width_used_m']} m @E{w['e']:.0f} N{w['n']:.0f}"
                                              for w in wo[:6]) + ". 원천 오기 확인 필요(reports/width_outliers.csv)" if wo else ""))
    R.ok("roads", "도로 마스크 덮임 비율(래스터 평균 ↔ 면 넓이)", worst_abs < 0.002,
         f"최대 차 {worst_abs:.4%}(타일 넓이 대비)" + (f", 상대 최대 {worst_rel:.2%} ({worst_tile})" if worst_tile else "")
         + f"; 전체 평균 R {tot_cov[0] / max(1, n_tiles):.2%} · G {tot_cov[1] / max(1, n_tiles):.2%} · B {tot_cov[2] / max(1, n_tiles):.2%}",
         fail="WARN")
    rate = hits / max(1, tested)
    R.ok("roads", "도로 마스크 방향(차도 중간점이 R 로 켜짐, 행 0 = 북)", tested > 0 and rate >= 0.97,
         f"{hits}/{tested} = {rate:.1%}; 행을 뒤집으면 {flips / max(1, tested):.1%}, 열을 뒤집으면 {flipc / max(1, tested):.1%}")
    # 알려진 지점: 가평역 앞에서 가장 가까운 차도
    tr = Transformer.from_crs("EPSG:4326", cfg["crs"], always_xy=True)
    ex, ny = tr.transform(127.51076, 37.81452)
    from shapely.geometry import Point
    car = [f for f in feats if f.channel == 0]
    if car:
        f = min(car, key=lambda f: f.line.distance(Point(ex, ny)))
        q = f.line.interpolate(f.line.project(Point(ex, ny)))
        t = next(t for t in area_tiles(cfg, area) if t.e0 <= q.x < t.e0 + ts and t.n0 <= q.y < t.n0 + ts)
        a = np.asarray(Image.open(ap.tile_dir(t.name) / "roads_mask.png"))
        c, rr = world_to_pixel(t.e0, t.n0, q.x, q.y, ts, px)
        R.ok("roads", "도로 마스크 알려진 지점(가평역 앞 차도)", a[rr, c, 0] >= 128,
             f"w{f.osm_id}({f.cls}) E{q.x:.1f} N{q.y:.1f} → {t.name} 픽셀(열 {c}, 행 {rr}) R={a[rr, c, 0]}, 뒤집은 행 R={a[px - 1 - rr, c, 0]}")


def _deck_surfaces(V: np.ndarray, F: np.ndarray):
    """교량 노드 메시 → 위를 향한 면 표본기(TriSurface). mode="min" = 상판 윗면(난간 윗면은 1 m 위)."""
    from .surface import TriSurface
    A, B, C = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    n = np.cross(B - A, C - A)
    nz = n[:, 2] / np.maximum(np.linalg.norm(n, axis=1), 1e-12)
    return TriSurface(V, F[nz > 0.5], cell_m=2.0)


def check_bridges(cfg, area, R: Report):
    """C3: glb 노드 = items, 양 끝 상판 윗면 ↔ 지형(−0.3 ~ +0.5 m), 수면 + 0.2 m 바닥(water.glb 직접),
    수면 위 여유, 경사 상한, 상판 밑 지형 관통, 나란한 다리 높이 공유, 다리끼리 겹침."""
    from shapely import contains_xy
    from shapely.geometry import LineString, Polygon

    from .bridges import load_bridges, offset_lines
    from .dem import deck_allowance
    from .terrain import TerrainSampler
    from .water import water_surface_from_tiles
    ap = area_paths(cfg, area)
    rc = cfg["roads"]
    rep = read_json(ap.reports / "bridges.json", None)
    if rep is None:
        R.add("roads", "교량", "WARN", "reports/bridges.json 없음(roads 단계)")
        return
    ox, oy = cfg["origin"]["easting"], cfg["origin"]["northing"]
    geo = {f"w{b.osm_id}": b for b in load_bridges(cfg, area)}
    terrain = TerrainSampler(cfg, area)
    water = water_surface_from_tiles(cfg, area)
    mesh: dict[str, tuple] = {}          # osm_id → (V 월드, F, item, footprint, 윗면 표본기)
    n_items = n_nodes = 0
    mismatch = []
    for t in area_tiles(cfg, area):
        tj = read_json(ap.tile_dir(t.name) / "tile.json", {}) or {}
        b = tj.get("bridges")
        if not b:
            continue
        items = {it["node"]: it for it in b["items"]}
        nodes = read_glb_nodes(ap.tile_dir(t.name) / b["file"])
        n_items += len(items)
        n_nodes += len(nodes)
        names = {n["name"] for n in nodes}
        if names != set(items):
            mismatch.append(t.name)
        for n in nodes:
            it = items.get(n["name"])
            p = n["primitives"][0]
            V = from_gltf_axes(p["attributes"]["POSITION"]) + [t.e0, t.n0, 0.0]
            if it is not None:
                ok_mat = p["material"] == f"M_bridge_{it['kind']}" and len(n["primitives"]) == 1
                if not ok_mat:
                    mismatch.append(n["name"])
                fp = np.asarray(it["footprint_xz"]) + [ox, oy]
                mesh[it["osm_id"]] = (V, p["indices"], it, fp, _deck_surfaces(V, p["indices"]))
    R.ok("roads", "교량 glb 노드 = tile.json items(프리미티브 1개, 재질 M_bridge_<kind>)", not mismatch and n_nodes == n_items,
         f"노드 {n_nodes}, items {n_items}, 어긋남 {mismatch[:5]}")
    if not mesh:
        return
    clear = rc["bridge_clearance_m"]
    grade = rc["bridge_max_grade"]
    gap_min = float(rc.get("bridge_min_water_gap_m", 0.2))
    rmax = float(rc.get("bridge_end_raise_max_m", 0.5))
    pt_ = float(rc.get("parapet_thickness_m", 0.25))
    end_bad, end_warn, end_floor, end_steep, n_end = [], [], [], [], 0
    from .heightmap import sample_spacing
    steep_reach = float(np.sqrt(2.0)) * sample_spacing(cfg)
    short, steep, floor_bad = [], [], []
    floor_n = 0
    floor_min = np.inf
    for ch in rep["chains"]:
        mem = [m for m in ch["members"] if m in mesh]
        if not mem:
            continue
        # 양 끝: 사슬 끝 점에서 0.1 m 안쪽 상판 윗면 ↔ 그 점의 지형(−0.3 ~ +0.5 m; 넘는 위쪽은 수면 바닥 때문이면 WARN)
        for end in ch["ends"]:
            e, nn = end["e"], end["n"]
            best = None
            for m in mem:
                g = geo[m].geometry
                for q in (g.coords[0], g.coords[-1]):
                    if abs(q[0] - e) < 0.01 and abs(q[1] - nn) < 0.01:
                        best = (m, g, 0.1 if q == g.coords[0] else g.length - 0.1)
            if best is None:
                continue
            m, g, sx = best
            pnt = g.interpolate(max(0.0, min(g.length, sx)))
            top = mesh[m][4].height([pnt.x], [pnt.y], mode="min")[0]
            tz = terrain.height(pnt.x, pnt.y)[0]
            n_end += 1
            rule = end.get("rule", "")
            if not np.isfinite(top):
                end_bad.append(f"{m} 끝 상판 못 찾음")
                continue
            if rule.startswith("bridge"):
                continue                           # 다른 다리 상판에 붙은 끝(지형 비교 대상 아님)
            dz = float(top - tz)
            if -0.3 <= dz <= rmax:
                continue
            txt = f"{m} {rule} {dz:+.2f} m @E{pnt.x:.0f} N{pnt.y:.0f}"
            # (1) 수면 바닥: 끝 단면 걷는 폭 안 수면 메시 최대 + 0.2 m 가 곧 상판 윗면(끝을 바닥까지 올렸다)
            gq = np.asarray(g.coords)[:, :2]
            d_ = (gq[1] - gq[0]) if sx < g.length / 2 else (gq[-1] - gq[-2])
            nrm = np.array([-d_[1], d_[0]]) / max(float(np.hypot(*d_)), 1e-9)
            half_top = max(mesh[m][2]["width_m"] / 2.0 - pt_ - 0.01, 0.0)
            qq = np.array([[pnt.x, pnt.y]]) + np.outer(np.linspace(-1, 1, 5) * half_top, nrm)
            wmax = float(np.nanmax(np.r_[water.height(qq[:, 0], qq[:, 1]), -np.inf]))
            g_forced = float(ch.get("grade_forced", 0.0))
            if dz > rmax and np.isfinite(wmax) and top <= wmax + gap_min + 0.02:
                end_floor.append(txt + f"(끝 지형 {tz:.2f}, 걷는 폭 안 수면 최대 {wmax:.2f})")
            # (2) 경사 상한을 크게 넘은 상판의 내려가는 끝: 관통을 막으려면 이중선형 칸(√2·간격) 안에서
            #     경사 × √2·간격 만큼 끝 표본을 낮춰야 한다 → 그 경사가 (0.5 − 0.05) ÷ (√2·간격) 를 넘으면 불가피
            elif dz > rmax and g_forced > (rmax - 0.05) / steep_reach:
                end_steep.append(txt + f"(경사 {g_forced:.0%})")
            elif rule == "shared" or rule.startswith("end_terrain"):
                end_bad.append(txt)                # 육지 끝(접속 길 공유·접속 길 없는 끝 = 끝 지형 고정)
            else:
                end_warn.append(txt)               # 물 위 끝(선착장·잔교: 수면 + 여유) 등
        # 종단: 각 way 중심선 2 m 간격 상판 윗면(경사), 0.5 m 간격·걷는 폭 안 수면 바닥, 바깥 폭 수면 여유
        for m in mem:
            g = geo[m].geometry
            V, F, it, _, surf = mesh[m]
            L = g.length
            xs = np.linspace(0.1, L - 0.1, max(2, int(L // 2) + 1)) if L > 0.4 else np.array([L / 2])
            P = np.array([g.interpolate(x).coords[0][:2] for x in xs])
            top = surf.height(P[:, 0], P[:, 1], mode="min")
            ok = np.isfinite(top)
            if ok.sum() >= 2:
                xo, to = xs[ok], top[ok]
                base = min(10.0, xo[-1] - xo[0])
                if base > 0.5:
                    j = np.searchsorted(xo, xo + base)
                    sel = j < len(xo)
                    gmax = float(np.max(np.abs(to[j[sel]] - to[sel]) / np.maximum(xo[j[sel]] - xo[sel], 1e-6))) if sel.any() else 0.0
                    cap = float(grade.get(it["kind"], 0.08))
                    if gmax > cap + 0.005:
                        steep.append((m, gmax, cap, "grade_forced" in ch or "floor_over_grade_m" in ch))
            xf = np.linspace(0.05, L - 0.05, max(2, int(L / 0.5) + 1)) if L > 0.2 else np.array([L / 2])
            Pf = np.array([g.interpolate(x).coords[0][:2] for x in xf])
            if len(Pf) >= 2:
                Lp, _, _ = offset_lines(Pf, 1.0)
                nv = Lp - Pf
            else:
                nv = np.zeros_like(Pf)
            half_top = max(it["width_m"] / 2.0 - pt_ - 0.01, 0.0)
            gaps_f, gaps_c = [], []
            t_mid = surf.height(Pf[:, 0], Pf[:, 1], mode="min")           # 단면은 평평하다(가운데 = 단면 윗면)
            for f_ in (-1.0, -0.5, 0.0, 0.5, 1.0):
                q = Pf + nv * (half_top * f_)                              # 바닥: 걷는 윗면 위 그 점의 수면
                gaps_f.append(surf.height(q[:, 0], q[:, 1], mode="min") - water.height(q[:, 0], q[:, 1]))
                q2 = Pf + nv * (it["width_m"] / 2.0 * f_)                  # 여유 목표: 바깥 폭 안 수면
                gaps_c.append(t_mid - water.height(q2[:, 0], q2[:, 1]))
            gf = np.concatenate(gaps_f)
            gf = gf[np.isfinite(gf)]
            if gf.size:
                floor_n += gf.size
                floor_min = min(floor_min, float(gf.min()))
                if gf.min() < gap_min - 0.01:
                    floor_bad.append((m, float(gf.min())))
            gc = np.concatenate(gaps_c)
            gc = gc[np.isfinite(gc)]
            cl = float(clear.get(it["kind"], 3.0))
            if gc.size and gc.min() < cl - 0.05:
                short.append((m, float(gc.min()), cl))
    R.ok("roads", f"교량 양 끝 상판 윗면 ↔ 지형(−0.3 ~ +{rmax:g} m, 육지 끝: 접속 길 공유·접속 길 없는 끝)", not end_bad,
         f"끝 {n_end}곳 검사, 벗어남 {len(end_bad)}" + (f": {', '.join(end_bad[:5])}" if end_bad else "")
         + (f"; 따로 WARN: 수면 바닥 때문 {len(end_floor)}곳, 급경사(> {(rmax - 0.05) / steep_reach:.0%}) 내려가는 끝 {len(end_steep)}곳"
            if end_floor or end_steep else ""))
    if end_floor:
        R.add("roads", f"교량 끝이 수면 + {gap_min:g} m 바닥 때문에 지형보다 +{rmax:g} m 넘게 높음", "WARN",
              f"{len(end_floor)}곳: {', '.join(end_floor[:6])}. 원천 불일치(끝 지형이 그 자리 수면 메시보다 낮거나 0.3 m 안)")
    if end_steep:
        R.add("roads", "교량 끝이 지형보다 +0.5 m 넘게 높음(경사 상한을 크게 넘은 상판의 내려가는 끝)", "WARN",
              f"{len(end_steep)}곳: {', '.join(end_steep[:6])}. 양 끝 지형 차가 길이에 비해 커서(원천 불일치) 상판 관통을 막느라 끝 지형을 낮췄다")
    if end_warn:
        R.add("roads", "교량 물 위 끝(접속 길 없음, 선착장·잔교 = 수면 + 여유) ↔ 끝 지형(깎은 하상)", "WARN",
              f"{len(end_warn)}곳: {', '.join(end_warn[:6])}. 걸어 들어오는 육지 끝이 아니다(D-012)")
    R.ok("roads", f"교량 상판 윗면 ≥ 수면 + {gap_min:g} m(water.glb 직접, 0.5 m 간격·걷는 폭 안 5줄)", not floor_bad,
         (f"수면 위 표본 {floor_n:,}개, 최소 여유 {floor_min:.3f} m" if floor_n else "수면 위 상판 없음")
         + (f"; 어긋남 {len(floor_bad)}개: " + ", ".join(f"{m} {g_:+.3f} m" for m, g_ in floor_bad[:6]) if floor_bad else ""))
    R.add("roads", "교량 수면 위 여유 목표(도로 3·보행 1.5·철도 4 m, water.glb)", "WARN" if short else "PASS",
          f"모자란 way {len(short)}개" + (": " + ", ".join(f"{m} {g:.2f}/{c:g} m" for m, g, c in sorted(short, key=lambda x: x[1] - x[2])[:8]) if short else "")
          + ". 양 끝 지형·경사 상한 안에서 못 맞춘 것(reports/bridges.json warnings). 바닥(+0.2 m)은 위 항목")
    forced = [x for x in steep if x[3]]
    other = [x for x in steep if not x[3]]
    R.add("roads", "교량 경사 상한(도로·보행 8%, 철도 3%, 10 m 기준선)", "WARN" if steep else "PASS",
          f"넘는 way {len(steep)}개(양 끝 높이 차·수면 바닥 때문 {len(forced)}, 그 밖 {len(other)}"
          + (": " + ", ".join(f"{m} {g:.1%}/{c:.0%}" for m, g, c, _ in other) if other else "") + ")"
          + (". 큰 것: " + ", ".join(f"{m} {g:.1%}/{c:.0%}" for m, g, c, _ in sorted(forced, key=lambda x: -x[1])[:8]) if forced else ""))
    _check_under_deck(cfg, area, R, mesh, geo, terrain, deck_allowance, LineString, Polygon, contains_xy)
    _check_parallel(cfg, area, R, mesh)
    # 다리끼리 겹침(같은 사슬 제외): 상판 외곽이 겹치고 높이 차가 두께 + 난간 안이면 서로 뚫는다
    chain_of = {m: i for i, ch in enumerate(rep["chains"]) for m in ch["members"]}
    polys = {m: Polygon(v[3]).buffer(0) for m, v in mesh.items()}
    keys = sorted(polys)
    lim = float(rc.get("deck_thickness_m", 0.8)) + float(rc.get("parapet_height_m", 1.0))
    pairs = []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if chain_of.get(a) == chain_of.get(b):
                continue
            inter = polys[a].intersection(polys[b])
            if inter.area < 1.0:
                continue
            minx, miny, maxx, maxy = inter.bounds
            gx, gy = np.meshgrid(np.arange(minx + 0.25, maxx, 0.5), np.arange(miny + 0.25, maxy, 0.5))
            m_ = contains_xy(inter, gx.ravel(), gy.ravel())
            qx, qy = gx.ravel()[m_], gy.ravel()[m_]
            za = mesh[a][4].height(qx, qy, mode="min")
            zb = mesh[b][4].height(qx, qy, mode="min")
            dz = np.abs(za - zb)
            dz = dz[np.isfinite(dz)]
            if dz.size:
                pairs.append((a, b, inter.area, float(dz.min()), float(dz.max())))
    same = [x for x in pairs if x[4] <= 0.05]                       # 같은 높이로 겹침(나란한 상판 공유)
    cross = [x for x in pairs if x[3] < lim and x[4] > 0.05]
    stack = [x for x in pairs if lim <= x[3] < 5.0]            # 5 m 넘게 떨어진 것은 정상 입체 교차(철교 아래 도로교 등)
    cross_ok = [x for x in pairs if x[3] >= 5.0]

    def _fmt(xs):
        return "; ".join(f"{a}×{b} {ar:.0f} m² Δ{lo:.2f}~{hi:.2f} m" for a, b, ar, lo, hi in sorted(xs, key=lambda x: -x[2])[:8])
    R.add("roads", f"교량끼리 상판이 겹침(다른 사슬; 높이 차 < {lim:g} m 면 서로 뚫음)", "WARN" if cross or stack else "PASS",
          f"뚫음 {len(cross)}쌍" + (f": {_fmt(cross)}" if cross else "") + f" / {lim:g}~5 m 위아래로 겹침 {len(stack)}쌍"
          + (f": {_fmt(stack)}" if stack else "") + f" / 같은 높이로 겹침(공유, Δ ≤ 0.05 m) {len(same)}쌍"
          + (f": {_fmt(same)}" if same else "") + f" / 5 m 넘게 떨어진 입체 교차 {len(cross_ok)}쌍"
          + ". 보고만 함(같은 다리를 이룬 OSM way 들)")


def _check_under_deck(cfg, area, R: Report, mesh, geo, terrain, deck_allowance, LineString, Polygon, contains_xy):
    """상판 밑 지형(D-012): 상판 외곽 안 0.5 m 격자에서 (1) 지형 > 상판 윗면 = 관통(FAIL),
    (2) 땅에 닿는 끝 구간(under_deck_end_m + 1.5 m)을 뺀 곳에서 지형 > 밑면 − under_deck_clear_m = 여유 미달(WARN)."""
    ap = area_paths(cfg, area)
    rc = cfg["roads"]
    dk = read_json(ap.interim / "decks.json", None)
    if dk is None:
        R.add("roads", "상판 밑 지형 관통", "WARN", "decks.json 없음(dem 단계)")
        return
    clear = float(rc.get("under_deck_clear_m", 0.3))
    end_m = float(rc.get("under_deck_end_m", 3.0))
    from .heightmap import sample_spacing
    px = sample_spacing(cfg)
    n_pts = 0
    poke, short = [], []
    worst_poke, worst_short = -np.inf, -np.inf
    for c in dk["chains"]:
        line = LineString(c["line"])
        L = line.length
        taper = tuple(c["taper"])
        for mm in c["members"]:
            key = f"w{mm['osm_id']}"
            if key not in mesh:
                continue
            V, F, it, fp, surf = mesh[key]
            poly = Polygon(fp).buffer(0)
            minx, miny, maxx, maxy = poly.bounds
            gx, gy = np.meshgrid(np.arange(minx + 0.25, maxx, 0.5), np.arange(miny + 0.25, maxy, 0.5))
            gx, gy = gx.ravel(), gy.ravel()
            sel = contains_xy(poly, gx, gy)
            gx, gy = gx[sel], gy[sel]
            if not gx.size:
                continue
            top = surf.height(gx, gy, mode="min")
            tz = terrain.height(gx, gy)
            ok = np.isfinite(top) & np.isfinite(tz)
            gx, gy, top, tz = gx[ok], gy[ok], top[ok], tz[ok]
            n_pts += len(gx)
            d1 = tz - top
            k1 = d1 > 0.02
            if d1.size:
                worst_poke = max(worst_poke, float(d1.max()))
            if k1.any():
                i = int(np.argmax(d1))
                poke.append((key, int(k1.sum()), float(d1.max()), gx[i], gy[i]))
            import shapely as _sh
            s = _sh.line_locate_point(line, _sh.points(gx, gy))
            inner = np.ones(len(s), dtype=bool)
            if taper[0]:
                inner &= s >= end_m + 1.5 * px
            if taper[1]:
                inner &= s <= L - end_m - 1.5 * px
            d2 = tz - (top - float(it["deck_thickness_m"]) - clear)
            d2 = np.where(inner, d2, -np.inf)
            if inner.any():
                worst_short = max(worst_short, float(d2.max()))
            k2 = d2 > 0.03
            if k2.any():
                i = int(np.argmax(d2))
                short.append((key, int(k2.sum()), float(d2.max()), gx[i], gy[i]))
    R.ok("roads", "상판 밑 지형 관통 0(상판 외곽 안 0.5 m 격자, 지형 > 상판 윗면 + 0.02 m)", not poke,
         f"표본 {n_pts:,}개, 관통 {sum(p[1] for p in poke)}개, 지형 − 윗면 최대 {worst_poke:+.3f} m"
         + (": " + ", ".join(f"{k} {n}개 최대 {h:.2f} m @E{x:.0f} N{y:.0f}" for k, n, h, x, y in sorted(poke, key=lambda p: -p[2])[:6]) if poke else ""))
    R.add("roads", f"상판 밑 지형 ≤ 밑면 − {clear:g} m(땅에 닿는 끝 {end_m:g} m 구간 제외)", "WARN" if short else "PASS",
          f"넘는 표본 {sum(p[1] for p in short)}개, 지형 − (밑면 − {clear:g}) 최대 {worst_short:+.3f} m"
          + (": " + ", ".join(f"{k} {n}개 최대 {h:.2f} m @E{x:.0f} N{y:.0f}" for k, n, h, x, y in sorted(short, key=lambda p: -p[2])[:6]) if short else ""))


def _check_parallel(cfg, area, R: Report, mesh):
    """나란한 다리(중심선 간격 ≤ bridge_parallel_m) 같은 단면 상판 높이 차를 glb 에서 직접 잰다.
    가운데는 공유(차 ≤ 0.05 m), 남는 단은 양 끝(지형에 고정)에서 경사 상한으로 오르는 구간뿐이어야 한다."""
    from .bridges import load_bridges
    from .roads import _coords, load_decks, parallel_partners
    ap = area_paths(cfg, area)
    dk = read_json(ap.interim / "decks.json", None)
    if dk is None:
        return
    bridges = load_bridges(cfg, area)
    chains = load_decks(dk, bridges)
    partners = parallel_partners(chains, bridges, cfg["roads"])
    if not partners:
        R.add("roads", "나란한 다리 상판 높이 공유", "PASS", "나란한 쌍 없음")
        return

    def deck_top(ci, P):
        out = np.full(len(P), np.nan)
        for j, _ in chains[ci].members:
            k = f"w{bridges[j].osm_id}"
            if k in mesh:
                out = np.fmax(out, mesh[k][4].height(P[:, 0], P[:, 1], mode="min"))
        return out
    seen, rows, mid_bad = set(), [], []
    worst = 0.0
    for p in partners:
        key = (min(p.a, p.b), max(p.a, p.b))
        if key in seen:
            continue
        seen.add(key)
        A, B = chains[p.a], chains[p.b]
        PA = _coords(A.line, A.x[p.ia])
        PB = _coords(B.line, p.sb)
        dz = deck_top(p.a, PA) - deck_top(p.b, PB)
        okm = np.isfinite(dz)
        if not okm.any():
            continue
        dz = dz[okm]
        big = np.abs(dz) > 0.05
        # 남은 단(0.05 m 넘는 연속 구간)이 어느 쪽 사슬 끝(2 m 안)에 닿아 있으면 '끝 고정 구간', 아니면 가운데 단
        sa, sb = A.x[p.ia][okm], p.sb[okm]
        at_end = (sa <= 2.0) | (sa >= A.line.length - 2.0) | (sb <= 2.0) | (sb >= B.line.length - 2.0)
        idx = np.nonzero(big)[0]
        mid_m = 0
        if idx.size:
            for run in np.split(idx, np.nonzero(np.diff(idx) > 1)[0] + 1):
                if not at_end[run].any():
                    mid_m += len(run)
        if mid_m:
            mid_bad.append(f"{_lab(A, bridges)}∥{_lab(B, bridges)} 가운데 {mid_m} m")
        worst = max(worst, float(np.abs(dz).max()))
        rows.append(f"{_lab(A, bridges)}∥{_lab(B, bridges)} {len(dz)} m: 최대 차 {np.abs(dz).max():.2f} m, 0.05 m 넘는 곳 {int(big.sum())} m")
    R.add("roads", "나란한 다리 상판 높이 공유(같은 단면 높이 차, glb)", "WARN" if mid_bad or worst > 0.5 else "PASS",
          f"{len(rows)}쌍: " + "; ".join(rows) + ". 남은 단은 모두 사슬 끝에 붙은 구간(끝은 접속 지형에 고정, 경사 상한으로 오름)"
          if not mid_bad else f"{len(rows)}쌍: " + "; ".join(rows)
          + (f". 끝 구간 밖에 남은 단: {', '.join(mid_bad)}" if mid_bad else ""))


def _lab(ch, bridges):
    return "+".join(f"w{bridges[j].osm_id}" for j, _ in ch.members)


# --------------------------------------------------------------------------- v2: 원경 스커트(C4)
def check_skirt(cfg, area, R: Report):
    """C4: 덩어리 정점 수·속성, 겹침 띠가 지형 아래(정점 ≥ 2 m, 띠 표면이 지형 위로 솟지 않음), 경계 높이 차, 범위 밖 수면."""
    from .terrain import TerrainSampler
    ap = area_paths(cfg, area)
    sk = cfg["skirt"]
    st = read_json(ap.reports / "skirt.json", None)
    path = ap.out / "skirt" / "skirt.glb"
    if st is None or not path.exists():
        R.add("skirt", "원경 스커트", "WARN", "skirt.glb 없음(skirt 단계)")
        return
    nodes = read_glb_nodes(path)
    O = np.array([cfg["origin"]["easting"], cfg["origin"]["northing"], 0.0])
    sk_nodes = [n for n in nodes if n["name"].startswith("SK_")]
    skw = [n for n in nodes if n["name"].startswith("SKW_")]
    maxv = int(sk.get("max_vertices", 65000))
    counts = [len(n["primitives"][0]["attributes"]["POSITION"]) for n in sk_nodes]
    attrs = {tuple(sorted(p["attributes"])) for n in sk_nodes for p in n["primitives"]}
    R.ok("skirt", f"덩어리 SK_<i> 정점 ≤ {maxv:,}·속성 구성 같음", sk_nodes and max(counts) <= maxv and len(attrs) == 1,
         f"{len(sk_nodes)}덩어리 {counts}, 속성 {sorted(attrs)}; 범위 밖 수면 SKW {len(skw)}개")
    V = np.concatenate([from_gltf_axes(n["primitives"][0]["attributes"]["POSITION"]) + O for n in sk_nodes])
    offs = np.concatenate([[0], np.cumsum(counts)])
    F = np.concatenate([n["primitives"][0]["indices"] + offs[i] for i, n in enumerate(sk_nodes)])
    b = area_bounds(cfg, area)
    M = float(sk["margin_m"])
    ext = (V[:, 0].min(), V[:, 1].min(), V[:, 0].max(), V[:, 1].max())
    R.ok("skirt", "범위(경계 + margin_m, 안으로 inner_overlap_m)", np.allclose(ext, (b[0] - M, b[1] - M, b[2] + M, b[3] + M), atol=0.05),
         f"E {ext[0]:.0f}~{ext[2]:.0f}, N {ext[1]:.0f}~{ext[3]:.0f}")
    terrain = TerrainSampler(cfg, area)
    eps = 0.05
    inside = (V[:, 0] > b[0] + eps) & (V[:, 0] < b[2] - eps) & (V[:, 1] > b[1] + eps) & (V[:, 1] < b[3] - eps)
    tz = terrain.height(V[inside, 0], V[inside, 1])
    gap_v = tz - V[inside, 2]
    drop = float(sk["overlap_drop_m"])
    R.ok("skirt", f"겹침 띠 정점이 지형보다 {drop:g} m 이상 아래", inside.any() and float(gap_v.min()) >= drop - 1e-3,
         f"범위 안 정점 {int(inside.sum())}개, 지형 − 스커트 최소 {gap_v.min():.2f} m, 중앙 {np.median(gap_v):.2f} m")
    # 띠 삼각형 표면이 지형 위로 솟는가(경계 줄은 지형과 같으므로 경계에서 0, 안쪽으로 갈수록 내려감)
    band = F[inside[F].any(axis=1)]
    w = np.array([(i, j, 6 - i - j) for i in range(7) for j in range(7 - i)], dtype=np.float64) / 6.0
    Q = np.einsum("kw,tkc->twc", w.T, V[band]).reshape(-1, 3)   # (삼각형 × 28, 3)
    qin = (Q[:, 0] > b[0]) & (Q[:, 0] < b[2]) & (Q[:, 1] > b[1]) & (Q[:, 1] < b[3])
    Q = Q[qin]
    tq = terrain.height(Q[:, 0], Q[:, 1])
    poke = Q[:, 2] - tq
    dq = np.minimum(np.minimum(Q[:, 0] - b[0], b[2] - Q[:, 0]), np.minimum(Q[:, 1] - b[1], b[3] - Q[:, 1]))
    bad = poke > 0.05
    k = int(np.argmax(poke)) if len(poke) else 0
    R.ok("skirt", "겹침 띠 표면이 지형 위로 솟지 않음(> 0.05 m)", not (poke > 0.3).any(),
         f"띠 표본 {len(poke):,}개 중 {int(bad.sum())}개({bad.mean():.2%}) 솟음, 최대 {poke.max():.2f} m"
         f"(E{Q[k, 0]:.0f} N{Q[k, 1]:.0f}, 경계에서 {dq[k]:.1f} m 안)", fail="WARN")
    # 경계 높이 차(겹침 띠 바깥 첫 줄 = 경계 줄): 1 m 간격, 스커트 경계 변(선형) ↔ Terrain(RAW 이중선형)
    onb = ((np.abs(V[:, 0] - b[0]) < 1e-3) | (np.abs(V[:, 0] - b[2]) < 1e-3)) & (V[:, 1] >= b[1] - 1e-3) & (V[:, 1] <= b[3] + 1e-3) \
        | ((np.abs(V[:, 1] - b[1]) < 1e-3) | (np.abs(V[:, 1] - b[3]) < 1e-3)) & (V[:, 0] >= b[0] - 1e-3) & (V[:, 0] <= b[2] + 1e-3)
    BV = np.unique(np.round(V[onb], 3), axis=0)
    gaps = []
    for side in range(4):
        if side in (0, 1):     # 남·북 변: N 고정, E 따라
            nfix = b[1] if side == 0 else b[3]
            sv = BV[np.abs(BV[:, 1] - nfix) < 1e-3]
            sv = sv[np.argsort(sv[:, 0])]
            xs = np.arange(b[0], b[2] + 1e-6, 1.0)
            zs = np.interp(xs, sv[:, 0], sv[:, 2])
            gaps.append(zs - terrain.height(xs, np.full(len(xs), nfix)))
        else:
            efix = b[0] if side == 2 else b[2]
            sv = BV[np.abs(BV[:, 0] - efix) < 1e-3]
            sv = sv[np.argsort(sv[:, 1])]
            ys = np.arange(b[1], b[3] + 1e-6, 1.0)
            zs = np.interp(ys, sv[:, 1], sv[:, 2])
            gaps.append(zs - terrain.height(np.full(len(ys), efix), ys))
    g = np.concatenate(gaps)
    g = g[np.isfinite(g)]
    raw = st.get("boundary_cop_minus_terrain_m", {})
    R.ok("skirt", "경계 높이 차(스커트 경계 줄 ↔ Terrain 경계, 1 m 간격)", float(np.abs(g).max()) <= 0.5,
         f"{len(g):,}점: |차| 중앙 {np.median(np.abs(g)):.3f} m, p95 {np.percentile(np.abs(g), 95):.3f} m, 최대 {np.abs(g).max():.3f} m"
         f"(스커트가 낮은 곳 최대 {max(0.0, -g.min()):.3f} m). 원천 Copernicus 그대로였다면 |차| p95 {raw.get('p95_abs')} m, 최대 {raw.get('max_abs')} m",
         fail="WARN")
    if skw:
        lvp = ap.interim / "water_level.tif"
        Wv = np.concatenate([from_gltf_axes(n["primitives"][0]["attributes"]["POSITION"]) + O for n in skw])
        onbw = ((np.abs(Wv[:, 0] - b[0]) < 0.01) | (np.abs(Wv[:, 0] - b[2]) < 0.01) | (np.abs(Wv[:, 1] - b[1]) < 0.01)
                | (np.abs(Wv[:, 1] - b[3]) < 0.01))
        detail = f"SKW {len(skw)}노드 정점 {len(Wv):,}, 수면 {len(st.get('water', []))}폴리곤"
        if onbw.any() and lvp.exists():
            from .roads import _sample_nearest, _water_level
            wl, wlT = _water_level(ap, float(cfg["water"].get("shore_overlap_m", 3.0)))
            ce = np.clip(Wv[onbw, 0], b[0] + 0.5, b[2] - 0.5)
            cn = np.clip(Wv[onbw, 1], b[1] + 0.5, b[3] - 0.5)
            lin = _sample_nearest(wl, wlT, ce, cn)
            dd = Wv[onbw, 2] - lin
            dd = dd[np.isfinite(dd)]
            if dd.size:
                detail += f"; 경계 위 정점 {dd.size}개 범위 안 수면과 차 최대 {np.abs(dd).max():.3f} m"
        R.add("skirt", "범위 밖 수면(SKW, interim OSM)", "PASS", detail)
        # SKW 아래 원경 지면(경계 줄 제외): 그 자리 SKW 면보다 water_drop_m 이상 아래(같은 높이면 깜빡임)
        from .surface import TriSurface
        wdrop = float(sk.get("water_drop_m", 2.0))
        Wf = np.concatenate([n["primitives"][0]["indices"] + o for n, o in
                             zip(skw, np.concatenate([[0], np.cumsum([len(n["primitives"][0]["attributes"]["POSITION"]) for n in skw])])[:-1])])
        ws = TriSurface(Wv, Wf, cell_m=120.0)
        outside = (V[:, 0] < b[0] - 1e-3) | (V[:, 0] > b[2] + 1e-3) | (V[:, 1] < b[1] - 1e-3) | (V[:, 1] > b[3] + 1e-3)
        idx = np.nonzero(outside)[0]
        wz = ws.height(V[idx, 0], V[idx, 1])
        under = np.isfinite(wz)
        gapw = wz[under] - V[idx[under], 2]
        R.ok("skirt", f"SKW 아래 원경 지면이 수면보다 {wdrop:g} m 이상 아래(경계 줄 제외)", gapw.size == 0 or float(gapw.min()) >= wdrop - 1e-3,
             f"SKW 아래 원경 정점 {gapw.size:,}개, 수면 − 지면 최소 {gapw.min():.2f} m, 중앙 {np.median(gapw):.2f} m" if gapw.size else "SKW 아래 정점 없음")
    else:
        R.add("skirt", "범위 밖 수면(SKW)", "WARN", "없음(skirt_water_areas 원천 없음 또는 받기 실패)")


CHECKS = {"sources": check_sources, "dem": check_dem, "ortho": check_ortho,
          "buildings": check_buildings, "water": check_water, "roads": check_roads,
          "skirt": check_skirt, "export": check_export}


def run(cfg: dict[str, Any], area: str, args=None) -> int:
    R = Report()
    stage = getattr(args, "stage", None)
    for name, fn in CHECKS.items():
        if stage and name != stage:
            continue
        try:
            fn(cfg, area, R)
        except Exception as e:  # 검사 자체 실패도 FAIL 로 남긴다
            R.add(name, "검사 실행", "FAIL", f"{type(e).__name__}: {e}")
    ap = area_paths(cfg, area).ensure()
    write_json(ap.reports / "validate.json", {"area": area, "stage": stage, "overall": R.worst, "items": R.items})
    for i in R.items:
        if i["status"] != "PASS" or not stage:
            print(f"  [{i['status']:4s}] {i['stage']:9s} {i['check']} — {i['detail']}")
    print(f"전체: {R.worst}")
    return 1 if R.worst == "FAIL" else 0
