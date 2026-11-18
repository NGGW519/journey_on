using System.Collections.Generic;
using UnityEngine;

namespace JourneyOn.Editor
{
    /// <summary>
    /// 물가 벽(ShoreWall) 변 규칙. WorldImporter 가 쓰고, EditMode 테스트로 따로 확인할 수 있게 순수 함수로 둔다.
    /// 계약 C3(2차-라): 중점이 교량 footprint + 여유(= water.shore_overlap_m + 0.5 m, 기본 3.5 m) 안인 변이 '교량 변'이다.
    ///  - 상판 밑에 있는 변: 윗면을 그 자리 상판 밑면 − 0.1 m 로 낮춘다.
    ///  - 상판 밖이고 상판 끝 앞(끝 구역)인 변: 그 끝의 상판 밑면 − 0.1 m 로 낮춘다(육지에서 다리로 오르는 길).
    ///  - 상판 밖이고 옆면 바깥인 변: 기본 높이(수면 + 4 m)를 유지한다(다리 옆으로 하상에 내려가는 틈 방지).
    /// 낮춘 윗면이 수면 + 0.3 m 이하이면 그 변만 뺀다.
    /// </summary>
    public static class ShoreRules
    {
        /// <summary>
        /// 계약 C3 여유의 기본값(water.shore_overlap_m 3.0 + 0.5). 실제 값은 WorldImporter 가 manifest·config 에서 읽어 계산한다.
        /// </summary>
        public const float BridgeMargin = 3.5f;
        /// <summary>여유 = 수면 메시 확장 폭(water.shore_overlap_m) + 이 값.</summary>
        public const float BridgeMarginExtra = 0.5f;
        /// <summary>끝 구역 옆 폭 = 상판 폭/2 + 이 값. 0: 끝 단면 폭 그대로(선분 교차라 걸친 변 전체가 낮아지므로 걷는 폭은 확보된다, 2차-라).</summary>
        public const float EndZoneLateralExtra = 0f;
        /// <summary>교량 변의 윗면 = 상판 밑면 − 이 값.</summary>
        public const float UndersideGap = 0.1f;
        /// <summary>낮춘 윗면이 수면 + 이 값 이하이면 변을 뺀다.</summary>
        public const float MinAboveWater = 0.3f;
        /// <summary>기본 물가 벽 높이(수면 위).</summary>
        public const float WallAboveWater = 4f;
        /// <summary>items 에 deck_thickness_m 가 없을 때 쓰는 상판 두께.</summary>
        public const float DefaultDeckThickness = 1f;

        public enum BridgeEdge { Normal, High, Lowered, Removed }

        /// <summary>
        /// 교량 변의 처리. waterA·B = 변 양 끝 수면 높이, underside = 그 자리 상판 밑면 높이(같은 좌표계).
        /// 반환: 윗면 높이(양 끝)와 분류. Removed 이면 벽을 만들지 않는다.
        /// </summary>
        public static BridgeEdge BridgeWallTop(float waterA, float waterB, float underside, out float topA, out float topB)
        {
            float top = underside - UndersideGap;
            topA = Mathf.Min(waterA + WallAboveWater, top);
            topB = Mathf.Min(waterB + WallAboveWater, top);
            if (top <= Mathf.Max(waterA, waterB) + MinAboveWater) return BridgeEdge.Removed;
            return top < Mathf.Max(waterA, waterB) + WallAboveWater ? BridgeEdge.Lowered : BridgeEdge.High;
        }

        public class Footprint
        {
            public string node;
            public Vector2[] pts;   // Unity 월드 (x, z), 닫지 않은 링
            public Rect box;
            public float thickness; // deck_thickness_m (0 이면 없음)
            public float width;     // width_m (0 이면 없음)
            public BridgeEnd[] ends = new BridgeEnd[0];   // 링 구조에서 찾은 양 끝(못 찾으면 빈 배열)

            public Footprint(string node, IList<Vector2> pts, float thickness = 0f, float width = 0f)
            {
                this.node = node;
                this.thickness = thickness;
                this.width = width;
                this.pts = new Vector2[pts.Count];
                pts.CopyTo(this.pts, 0);
                float x0 = float.MaxValue, z0 = float.MaxValue, x1 = float.MinValue, z1 = float.MinValue;
                foreach (var p in this.pts) { x0 = Mathf.Min(x0, p.x); z0 = Mathf.Min(z0, p.y); x1 = Mathf.Max(x1, p.x); z1 = Mathf.Max(z1, p.y); }
                box = Rect.MinMaxRect(x0, z0, x1, z1);
                if (TryRingEnds(this.pts, width, out var e)) ends = e;
            }
        }

        /// <summary>교량 끝: 중심선 끝점(Unity x, z)과 바깥(육지 쪽) 방향, 끝 단면 폭.</summary>
        public struct BridgeEnd
        {
            public Vector2 pos, outDir;
            public float capWidth;
            public string which;   // "start" / "end"
        }

        /// <summary>
        /// footprint_xz 링에서 양 끝을 찾는다. 파이프라인 footprint_ring 은 [왼쪽 선 앞으로 n점 + 오른쪽 선 뒤로 n점]이라
        /// 끝 단면은 (n−1 → n)과 (2n−1 → 0) 변이다. 단면 길이가 width_m 의 ±35% 안일 때만 믿는다(아니면 false).
        /// </summary>
        public static bool TryRingEnds(IReadOnlyList<Vector2> ring, float width, out BridgeEnd[] ends)
        {
            ends = new BridgeEnd[0];
            int m = ring.Count;
            if (m < 4 || m % 2 != 0) return false;
            int n = m / 2;
            Vector2 s0 = ring[0], s1 = ring[m - 1], e0 = ring[n - 1], e1 = ring[n];
            float ws = Vector2.Distance(s0, s1), we = Vector2.Distance(e0, e1);
            if (width > 0f && (Mathf.Abs(ws - width) > 0.35f * width || Mathf.Abs(we - width) > 0.35f * width)) return false;
            Vector2 start = (s0 + s1) * 0.5f, startNext = (ring[1] + ring[m - 2]) * 0.5f;
            Vector2 end = (e0 + e1) * 0.5f, endPrev = (ring[n - 2] + ring[n + 1]) * 0.5f;
            Vector2 os = start - startNext, oe = end - endPrev;
            if (os.sqrMagnitude < 1e-6f || oe.sqrMagnitude < 1e-6f) return false;
            ends = new[]
            {
                new BridgeEnd { pos = start, outDir = os.normalized, capWidth = ws, which = "start" },
                new BridgeEnd { pos = end, outDir = oe.normalized, capWidth = we, which = "end" },
            };
            return true;
        }

        /// <summary>
        /// 변(a–b, Unity x·z)이 어느 교량 끝의 '끝 구역' 직사각형(끝 단면 앞, 바깥 방향 −0.5 ~ margin + 0.5 m, 옆으로 폭/2 + 1 m)을
        /// 지나면 true. 중점이 아니라 선분 교차로 본다(비스듬한 긴 변, 여유 경계에 걸친 변도 잡도록, 2차-라).
        /// 중점 여유 검사(FindNearFootprint)와 별개로 모든 footprint 의 끝을 본다.
        /// </summary>
        public static bool FindEndZone(Vector2 a, Vector2 b, IReadOnlyList<Footprint> fps, float margin, out Footprint foot, out BridgeEnd end)
        {
            foot = null; end = default;
            if (fps == null) return false;
            float reach = margin + 0.5f;
            foreach (var f in fps)
            {
                if (f.ends.Length == 0) continue;
                float pad = reach + 0.5f * Mathf.Max(f.width, 1f) + EndZoneLateralExtra;
                if (Mathf.Max(a.x, b.x) < f.box.xMin - pad || Mathf.Min(a.x, b.x) > f.box.xMax + pad ||
                    Mathf.Max(a.y, b.y) < f.box.yMin - pad || Mathf.Min(a.y, b.y) > f.box.yMax + pad) continue;
                foreach (var e in f.ends)
                {
                    float halfW = 0.5f * (f.width > 0f ? f.width : e.capWidth) + EndZoneLateralExtra;
                    Vector2 perp = new Vector2(-e.outDir.y, e.outDir.x);
                    Vector2 la = new Vector2(Vector2.Dot(a - e.pos, e.outDir), Vector2.Dot(a - e.pos, perp));
                    Vector2 lb = new Vector2(Vector2.Dot(b - e.pos, e.outDir), Vector2.Dot(b - e.pos, perp));
                    if (SegmentHitsRect(la, lb, -0.5f, reach, -halfW, halfW)) { foot = f; end = e; return true; }
                }
            }
            return false;
        }

        /// <summary>선분 p–q 가 축 정렬 직사각형 [x0,x1]×[y0,y1] 과 만나는지(Liang–Barsky).</summary>
        public static bool SegmentHitsRect(Vector2 p, Vector2 q, float x0, float x1, float y0, float y1)
        {
            float t0 = 0f, t1 = 1f, dx = q.x - p.x, dy = q.y - p.y;
            bool Clip(float den, float num)
            {
                if (Mathf.Abs(den) < 1e-9f) return num >= 0f;   // 평행: 판(slab) 안쪽이면 계속(06 테스트로 부호 오류 발견·수정)
                float t = num / den;
                if (den < 0f) { if (t > t1) return false; if (t > t0) t0 = t; }
                else { if (t < t0) return false; if (t < t1) t1 = t; }
                return true;
            }
            return Clip(-dx, p.x - x0) && Clip(dx, x1 - p.x) && Clip(-dy, p.y - y0) && Clip(dy, y1 - p.y) && t0 <= t1;
        }

        /// <summary>교량 근처 물가 벽 변의 구역(물리 판정 전). WorldImporter 와 EditMode 테스트가 같이 쓴다.</summary>
        public enum BridgeZone
        {
            None,   // 교량과 무관: 기본 벽
            Side,   // 중점이 상판 + 여유 안이지만 끝 구역은 아니다: 상판 밑이 아니면 기본 높이 유지(옆면 유지)
            End,    // 선분이 어느 끝의 끝 구역을 지난다: 상판 밑이 아니면 그 끝의 밑면 − 0.1 로 낮춘다
        }

        /// <summary>
        /// 변 a–b(Unity x·z)의 구역. 끝 구역(선분 교차)을 먼저 보고, 아니면 중점이 상판 + 여유 안이면 Side.
        /// foot = 해당 footprint(End 는 끝의 footprint, Side 는 중점이 가까운 footprint), end = End 일 때의 끝.
        /// 상판 바로 밑인지는 물리 레이캐스트로 따로 본다(밑이면 구역과 상관없이 그 자리 밑면으로 낮춘다).
        /// </summary>
        public static BridgeZone Zone(Vector2 a, Vector2 b, IReadOnlyList<Footprint> fps, float margin, out Footprint foot, out BridgeEnd end)
        {
            if (FindEndZone(a, b, fps, margin, out foot, out end)) return BridgeZone.End;
            foot = FindNearFootprint((a + b) * 0.5f, fps, margin);
            return foot != null ? BridgeZone.Side : BridgeZone.None;
        }

        /// <summary>
        /// 끝 e(footprint self 의 끝)가 다른 교량 상판에 붙어 있는지: 끝점이 다른 footprint 안이거나 경계에서 tol 안.
        /// 사슬 이음(같은 다리의 이어진 way), 다른 다리에 붙은 계단 끝 등. 진입 검사에서 '연결부'로 따로 센다.
        /// </summary>
        public static Footprint AttachedTo(BridgeEnd e, Footprint self, IReadOnlyList<Footprint> fps, float tol = 1.0f)
        {
            if (fps == null) return null;
            foreach (var f in fps)
            {
                if (ReferenceEquals(f, self) || f.pts.Length < 3) continue;
                if (e.pos.x < f.box.xMin - tol || e.pos.x > f.box.xMax + tol || e.pos.y < f.box.yMin - tol || e.pos.y > f.box.yMax + tol) continue;
                if (PointInPolygon(e.pos, f.pts) || DistanceToRing(e.pos, f.pts) <= tol) return f;
            }
            return null;
        }

        /// <summary>오른·내리막 단 |dy| 가 걸음 한계(CharacterController.stepOffset)를 넘는지.</summary>
        public static bool IsStepTooHigh(float y0, float y1, float stepOffset) => Mathf.Abs(y1 - y0) > stepOffset + 0.01f;

        /// <summary>타일 경계에서 잘린 변(강 한가운데)인지. 로컬 좌표(타일 0..size).</summary>
        public static bool IsTileEdgeSegment(Vector3 a, Vector3 b, float size = 1000f, float eps = 0.05f)
        {
            bool On(Vector3 p) => Mathf.Abs(p.x) < eps || Mathf.Abs(p.x - size) < eps || Mathf.Abs(p.z) < eps || Mathf.Abs(p.z - size) < eps;
            return On(a) && On(b) && (Mathf.Abs(a.x - b.x) < eps || Mathf.Abs(a.z - b.z) < eps);
        }

        public static bool NearAnyFootprint(Vector2 p, IReadOnlyList<Footprint> fps, float margin = BridgeMargin) =>
            FindNearFootprint(p, fps, margin) != null;

        /// <summary>p 가 안에 있거나 경계에서 margin 안인 첫 footprint. 없으면 null.</summary>
        public static Footprint FindNearFootprint(Vector2 p, IReadOnlyList<Footprint> fps, float margin = BridgeMargin)
        {
            if (fps == null) return null;
            foreach (var f in fps)
            {
                if (f.pts.Length < 3) continue;
                if (p.x < f.box.xMin - margin || p.x > f.box.xMax + margin || p.y < f.box.yMin - margin || p.y > f.box.yMax + margin) continue;
                if (PointInPolygon(p, f.pts) || DistanceToRing(p, f.pts) <= margin) return f;
            }
            return null;
        }

        /// <summary>p 가 안에 있으면 p, 밖이면 가장 가까운 경계점에서 안쪽으로 inset 만큼 들어간 점(상판 밑을 다시 볼 자리).</summary>
        public static Vector2 NearestInside(Vector2 p, IReadOnlyList<Vector2> ring, float inset = 0.3f)
        {
            if (PointInPolygon(p, ring)) return p;
            float best = float.MaxValue;
            Vector2 c = p;
            for (int i = 0, j = ring.Count - 1; i < ring.Count; j = i++)
            {
                Vector2 a = ring[j], b = ring[i], ab = b - a;
                float len2 = ab.sqrMagnitude;
                float t = len2 > 1e-9f ? Mathf.Clamp01(Vector2.Dot(p - a, ab) / len2) : 0f;
                var q = a + ab * t;
                float d = Vector2.Distance(p, q);
                if (d < best) { best = d; c = q; }
            }
            Vector2 dir = c - p;
            if (dir.sqrMagnitude < 1e-8f)
            {
                Vector2 centroid = Vector2.zero;
                foreach (var r in ring) centroid += r;
                dir = centroid / ring.Count - c;
            }
            return c + dir.normalized * inset;
        }

        public static bool PointInPolygon(Vector2 p, IReadOnlyList<Vector2> ring)
        {
            bool inside = false;
            for (int i = 0, j = ring.Count - 1; i < ring.Count; j = i++)
            {
                var a = ring[i]; var b = ring[j];
                if ((a.y > p.y) != (b.y > p.y) && p.x < (b.x - a.x) * (p.y - a.y) / (b.y - a.y) + a.x) inside = !inside;
            }
            return inside;
        }

        public static float DistanceToRing(Vector2 p, IReadOnlyList<Vector2> ring)
        {
            float best = float.MaxValue;
            for (int i = 0, j = ring.Count - 1; i < ring.Count; j = i++)
                best = Mathf.Min(best, DistanceToSegment(p, ring[j], ring[i]));
            return best;
        }

        public static float DistanceToSegment(Vector2 p, Vector2 a, Vector2 b)
        {
            Vector2 ab = b - a;
            float len2 = ab.sqrMagnitude;
            float t = len2 > 1e-9f ? Mathf.Clamp01(Vector2.Dot(p - a, ab) / len2) : 0f;
            return Vector2.Distance(p, a + ab * t);
        }
    }
}
