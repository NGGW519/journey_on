using System.Collections.Generic;
using NUnit.Framework;
using UnityEngine;

namespace JourneyOn.Editor.Tests
{
    /// <summary>
    /// ShoreRules(물가 벽 변 규칙) EditMode 테스트. 순수 함수만 본다(물리 없음).
    /// 실행: Unity.exe -batchmode -projectPath … -runTests -testPlatform EditMode -testResults &lt;xml&gt;
    /// </summary>
    public class ShoreRulesTests
    {
        const float Margin = 3.5f;

        /// <summary>z 축 방향 곧은 다리 footprint: 중심선 (cx, z0)→(cx, z1), 폭 w, 간격 step. 파이프라인 footprint_ring 과 같은 순서.</summary>
        internal static List<Vector2> StraightRing(float cx, float z0, float z1, float w, float step = 2.5f)
        {
            var left = new List<Vector2>();
            var right = new List<Vector2>();
            int n = Mathf.Max(1, Mathf.RoundToInt((z1 - z0) / step));
            for (int i = 0; i <= n; i++)
            {
                float z = Mathf.Lerp(z0, z1, i / (float)n);
                left.Add(new Vector2(cx - w / 2f, z));
                right.Add(new Vector2(cx + w / 2f, z));
            }
            right.Reverse();
            left.AddRange(right);
            return left;
        }

        static ShoreRules.Footprint Deck(float cx = 0f, float z0 = 0f, float z1 = 20f, float w = 4f, string node = "BR_test") =>
            new ShoreRules.Footprint(node, StraightRing(cx, z0, z1, w), 0.8f, w);

        // ---------------------------------------------------------------- 타일 경계 변
        [Test]
        public void TileEdge_SegmentLyingOnTileBorder_IsSkipped()
        {
            Assert.IsTrue(ShoreRules.IsTileEdgeSegment(new Vector3(0, 50, 100), new Vector3(0, 50, 200)));
            Assert.IsTrue(ShoreRules.IsTileEdgeSegment(new Vector3(1000, 50, 300), new Vector3(1000, 50, 310)));
            Assert.IsTrue(ShoreRules.IsTileEdgeSegment(new Vector3(100, 50, 0), new Vector3(200, 50, 0)));
            Assert.IsTrue(ShoreRules.IsTileEdgeSegment(new Vector3(400, 50, 1000), new Vector3(410, 50, 1000.02f)));
        }

        [Test]
        public void TileEdge_InteriorOrCornerDiagonal_IsKept()
        {
            Assert.IsFalse(ShoreRules.IsTileEdgeSegment(new Vector3(0, 50, 100), new Vector3(5, 50, 100)), "한 끝만 경계");
            Assert.IsFalse(ShoreRules.IsTileEdgeSegment(new Vector3(0, 50, 5), new Vector3(5, 50, 0)), "모서리를 가로지르는 대각선");
            Assert.IsFalse(ShoreRules.IsTileEdgeSegment(new Vector3(300, 50, 300), new Vector3(310, 50, 300)), "타일 안");
        }

        // ---------------------------------------------------------------- TryRingEnds
        [Test]
        public void TryRingEnds_StraightDeck_FindsBothEndsAndOutwardDirections()
        {
            var f = Deck();
            Assert.AreEqual(2, f.ends.Length);
            var s = f.ends[0];
            var e = f.ends[1];
            Assert.AreEqual("start", s.which);
            Assert.AreEqual(0f, Vector2.Distance(s.pos, new Vector2(0, 0)), 1e-4);
            Assert.AreEqual(0f, Vector2.Distance(s.outDir, new Vector2(0, -1)), 1e-4);
            Assert.AreEqual(0f, Vector2.Distance(e.pos, new Vector2(0, 20)), 1e-4);
            Assert.AreEqual(0f, Vector2.Distance(e.outDir, new Vector2(0, 1)), 1e-4);
            Assert.AreEqual(4f, s.capWidth, 1e-4);
        }

        [Test]
        public void TryRingEnds_OddPointCountOrCapWidthMismatch_Fails()
        {
            var ring = StraightRing(0, 0, 20, 4);
            var odd = new List<Vector2>(ring);
            odd.RemoveAt(3);
            Assert.IsFalse(ShoreRules.TryRingEnds(odd, 4f, out _), "점 수가 홀수");
            Assert.IsFalse(ShoreRules.TryRingEnds(ring, 10f, out _), "끝 단면 4 m 인데 폭 10 m");
            Assert.IsTrue(ShoreRules.TryRingEnds(ring, 4.5f, out _), "±35% 안");
            var f = new ShoreRules.Footprint("BR_bad", odd, 0.8f, 4f);
            Assert.AreEqual(0, f.ends.Length, "끝을 못 찾으면 빈 배열(→ 옆면 규칙으로 막는 쪽)");
        }

        // ---------------------------------------------------------------- 끝 구역(선분 교차)·옆면 유지
        [Test]
        public void Zone_EdgeAcrossEndCorridor_IsEnd()
        {
            var fps = new[] { Deck() };
            // 끝(z=20) 앞 3 m 를 가로지르는 변
            Assert.AreEqual(ShoreRules.BridgeZone.End, ShoreRules.Zone(new Vector2(-3, 23), new Vector2(3, 23), fps, Margin, out var foot, out var end));
            Assert.AreSame(fps[0], foot);
            Assert.AreEqual("end", end.which);
            // 여유 + 0.5 = 4.0 m 까지는 끝 구역, 그 밖은 아님(중점도 3.5 m 밖이라 None)
            Assert.AreEqual(ShoreRules.BridgeZone.End, ShoreRules.Zone(new Vector2(-3, 23.9f), new Vector2(3, 23.9f), fps, Margin, out _, out _));
            Assert.AreEqual(ShoreRules.BridgeZone.None, ShoreRules.Zone(new Vector2(-3, 24.3f), new Vector2(3, 24.3f), fps, Margin, out _, out _));
        }

        [Test]
        public void Zone_LongObliqueEdgeWithMidpointOutsideCorridor_IsEnd()
        {
            var fps = new[] { Deck() };
            // 중점 (4, 23.5)은 끝 구역 옆 폭(2 m) 밖이지만 선분이 구역을 지난다(05d w1289824710 같은 경우)
            Assert.AreEqual(ShoreRules.BridgeZone.End, ShoreRules.Zone(new Vector2(-1, 21), new Vector2(9, 26), fps, Margin, out _, out var end));
            Assert.AreEqual("end", end.which);
        }

        [Test]
        public void Zone_EdgeBesideDeckSide_IsSideAndNotEnd()
        {
            var fps = new[] { Deck() };
            // 상판 옆면(x=2)에서 2.5 m 떨어진 나란한 변: 여유 안이지만 끝 구역이 아니다 → 기본 높이 유지 대상
            Assert.AreEqual(ShoreRules.BridgeZone.Side, ShoreRules.Zone(new Vector2(4.5f, 8), new Vector2(4.5f, 12), fps, Margin, out var foot, out _));
            Assert.AreSame(fps[0], foot);
            // 끝 쪽이어도 옆 폭(2 m) 밖에서 끝 방향과 나란히 있으면 옆면
            Assert.AreEqual(ShoreRules.BridgeZone.Side, ShoreRules.Zone(new Vector2(4.5f, 18), new Vector2(4.5f, 21), fps, Margin, out _, out _));
            // 여유 밖은 교량과 무관
            Assert.AreEqual(ShoreRules.BridgeZone.None, ShoreRules.Zone(new Vector2(8f, 8), new Vector2(8f, 12), fps, Margin, out _, out _));
        }

        [Test]
        public void Zone_DeckWithUnknownEnds_NeverEnd()
        {
            var ring = StraightRing(0, 0, 20, 4);
            ring.RemoveAt(3);   // 끝을 못 찾는 footprint
            var fps = new[] { new ShoreRules.Footprint("BR_bad", ring, 0.8f, 4f) };
            Assert.AreEqual(ShoreRules.BridgeZone.Side, ShoreRules.Zone(new Vector2(-3, 22), new Vector2(3, 22), fps, Margin, out _, out _));
        }

        // ---------------------------------------------------------------- 윗면 결정
        [Test]
        public void BridgeWallTop_HighLoweredRemoved()
        {
            Assert.AreEqual(ShoreRules.BridgeEdge.High, ShoreRules.BridgeWallTop(50f, 50f, 60f, out var a, out var b));
            Assert.AreEqual(54f, a, 1e-4); Assert.AreEqual(54f, b, 1e-4);
            Assert.AreEqual(ShoreRules.BridgeEdge.Lowered, ShoreRules.BridgeWallTop(50f, 50.2f, 52f, out a, out b));
            Assert.AreEqual(51.9f, a, 1e-4); Assert.AreEqual(51.9f, b, 1e-4);
            Assert.AreEqual(ShoreRules.BridgeEdge.Removed, ShoreRules.BridgeWallTop(50f, 50f, 50.35f, out _, out _), "밑면 − 0.1 ≤ 수면 + 0.3");
        }

        // ---------------------------------------------------------------- 보조
        [Test]
        public void SegmentHitsRect_Basics()
        {
            Assert.IsTrue(ShoreRules.SegmentHitsRect(new Vector2(-5, 0), new Vector2(5, 0), -1, 1, -1, 1));
            Assert.IsFalse(ShoreRules.SegmentHitsRect(new Vector2(-5, 2), new Vector2(5, 2), -1, 1, -1, 1));
            Assert.IsTrue(ShoreRules.SegmentHitsRect(new Vector2(0, 0), new Vector2(0.1f, 0.1f), -1, 1, -1, 1), "안에 있는 짧은 선분");
        }

        [Test]
        public void AttachedTo_ChainJointAndSideStair()
        {
            var a = Deck(0, 0, 20, 4, "BR_a");
            var b = Deck(0, 20, 40, 4, "BR_b");            // a 의 끝에 이어진 사슬
            var stair = new ShoreRules.Footprint("BR_stair", StraightRing(10, -8, 10, 2), 0.8f, 2f);   // 끝(z=10)이 다른 상판 c 옆면에 붙음
            var c = new ShoreRules.Footprint("BR_c", StraightRing(10, 10, 30, 6), 0.8f, 6f);
            var fps = new[] { a, b, stair, c };
            Assert.AreSame(b, ShoreRules.AttachedTo(a.ends[1], a, fps));
            Assert.IsNull(ShoreRules.AttachedTo(a.ends[0], a, fps));
            Assert.AreSame(a, ShoreRules.AttachedTo(b.ends[0], b, fps));
            Assert.AreSame(c, ShoreRules.AttachedTo(stair.ends[1], stair, fps));
        }

        [Test]
        public void IsStepTooHigh_UsesStepOffset()
        {
            Assert.IsFalse(ShoreRules.IsStepTooHigh(50f, 50.49f, 0.5f));
            Assert.IsFalse(ShoreRules.IsStepTooHigh(50f, 50.5f, 0.5f));
            Assert.IsTrue(ShoreRules.IsStepTooHigh(50f, 50.6f, 0.5f));
            Assert.IsTrue(ShoreRules.IsStepTooHigh(50f, 49.3f, 0.5f), "내리막 단도 센다(되돌아 오를 수 없다)");
        }

        [Test]
        public void TryReadYamlFloat_ReadsOnlyInsideBlock()
        {
            var lines = new[] { "# 주석", "dem:", "  shore_overlap_m: 9.0", "water:", "  level_percentile: 5", "  shore_overlap_m: 3.0      # 넓힘", "roads:", "  lane_m: 3.0" };
            Assert.IsTrue(WorldImporter.TryReadYamlFloat(lines, "water", "shore_overlap_m", out var v));
            Assert.AreEqual(3.0f, v, 1e-6);
            Assert.IsFalse(WorldImporter.TryReadYamlFloat(lines, "skirt", "shore_overlap_m", out _));
        }

        [Test]
        public void ShoreOverlap_ManifestKeyWinsOverConfig()
        {
            var m = new AreaManifest();
            float v = WorldImporter.ShoreOverlap(m, "{\"water\": {\"shore_overlap_m\": 2.5, \"shore_clip_above_m\": 0.3}}", out var src);
            Assert.AreEqual(2.5f, v, 1e-6);
            StringAssert.StartsWith("manifest.water.shore_overlap_m", src);
            float c = WorldImporter.ShoreOverlap(m, "{\"area\": \"phase1\"}", out var src2);
            Assert.That(src2, Does.StartWith("config/project.yaml").Or.StartWith("기본값"));
            Assert.Greater(c, 0f);
        }
    }
}
