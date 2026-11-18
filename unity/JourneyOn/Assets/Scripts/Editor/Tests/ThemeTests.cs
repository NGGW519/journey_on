using System;
using System.Linq;
using NUnit.Framework;
using UnityEngine;

namespace JourneyOn.Editor.Tests
{
    /// <summary>
    /// 아트 테마(D-014) 순수 함수: 건물 틴트 변형 해시, 테마 선택, 타일 크기, 틸트시프트 세기, 재질 이름 해석, 교량 상자 투영 UV.
    /// </summary>
    public class ThemeTests
    {
        [Test]
        public void StableHash_IsFnv1a()
        {
            Assert.AreEqual(2166136261u, Theme.StableHash(""));
            Assert.AreEqual(0xE40C292Cu, Theme.StableHash("a"));
            Assert.AreEqual(0x1A47E90Bu, Theme.StableHash("abc"));
        }

        [Test]
        public void VariantIndex_SameKeySameIndex_AndInRange()
        {
            for (int i = 0; i < 200; i++)
            {
                string id = $"B_{i:000000}";
                int a = Theme.Cozy.WallVariant(id), b = Theme.Cozy.WallVariant(id);
                Assert.AreEqual(a, b);
                Assert.That(a, Is.InRange(0, Theme.Cozy.wallTints.Length - 1));
                Assert.That(Theme.Cozy.RoofVariant(id), Is.InRange(0, Theme.Cozy.roofTints.Length - 1));
            }
        }

        [Test]
        public void VariantIndex_SpreadsOverPalette()
        {
            const int n = 3000, k = 6;
            var counts = new int[k];
            for (int i = 0; i < n; i++) counts[Theme.VariantIndex($"B_{i:000000}", k, Theme.WallSalt)]++;
            foreach (int c in counts) Assert.That(c, Is.InRange(n / k * 0.8, n / k * 1.2), string.Join(",", counts));
        }

        [Test]
        public void WallAndRoofVariants_AreNotLockedTogether()
        {
            int same = 0;
            const int n = 1200;
            for (int i = 0; i < n; i++)
            {
                string id = $"B_{i:000000}";
                if (Theme.VariantIndex(id, 6, Theme.WallSalt) == Theme.VariantIndex(id, 6, Theme.RoofSalt)) same++;
            }
            // 따로 놀면 약 1/6 이 같다
            Assert.That(same, Is.InRange(n / 6 * 0.6, n / 6 * 1.4));
        }

        [Test]
        public void Realistic_HasSingleVariant()
        {
            Assert.AreEqual(1, Theme.Realistic.wallTints.Length);
            Assert.AreEqual(1, Theme.Realistic.roofTints.Length);
            Assert.AreEqual(0, Theme.Realistic.WallVariant("B_000123"));
            Assert.AreEqual(0, Theme.VariantIndex("x", 1, Theme.RoofSalt));
            Assert.AreEqual(Color.white, Theme.Realistic.wallTints[0]);
        }

        [Test]
        public void TintPalettes_FirstIsWhiteAndAllAreGentleMultipliers()
        {
            foreach (var t in Theme.All)
                foreach (var c in t.wallTints.Concat(t.roofTints))
                {
                    Assert.LessOrEqual(c.maxColorComponent, 1f);
                    Assert.GreaterOrEqual(Mathf.Min(c.r, Mathf.Min(c.g, c.b)), 0.7f, $"{t.name} 틴트 {c} 가 너무 진하다(탁해짐)");
                }
            Assert.AreEqual(Color.white, Theme.Cozy.wallTints[0]);
            Assert.AreEqual(Color.white, Theme.Cozy.roofTints[0]);
            // 지형 레이어 색 곱: [잔디, 아스팔트, 보도, 자갈] 4개, 0 < 곱 ≤ 1(밝게 하지 않는다), realistic 은 1
            foreach (var t in Theme.All)
            {
                Assert.AreEqual(4, t.artLayerTint.Length, t.name);
                foreach (var v in t.artLayerTint)
                    for (int k = 0; k < 3; k++) Assert.That(v[k], Is.GreaterThan(0f).And.LessThanOrEqualTo(1f), $"{t.name} {v}");
            }
            Assert.IsTrue(Theme.Realistic.artLayerTint.All(v => v == Vector4.one));
        }

        [Test]
        public void FromArgs_DefaultCozy_ExplicitRealistic_UnknownThrows()
        {
            Assert.AreSame(Theme.Cozy, Theme.FromArgs(new[] { "Unity.exe", "-area", "phase1" }));
            Assert.AreSame(Theme.Realistic, Theme.FromArgs(new[] { "-theme", "realistic" }));
            Assert.AreSame(Theme.Cozy, Theme.FromArgs(new[] { "-theme", "COZY" }));
            Assert.Throws<ArgumentException>(() => Theme.FromArgs(new[] { "-theme", "neon" }));
        }

        [Test]
        public void TerrainTileSizes_DivideTerrainTile()
        {
            Assert.IsTrue(Theme.DividesTerrainTile(25f));
            Assert.IsTrue(Theme.DividesTerrainTile(2.5f));
            Assert.IsFalse(Theme.DividesTerrainTile(3f));
            Assert.IsFalse(Theme.DividesTerrainTile(0f));
            foreach (var t in Theme.All)
            {
                Assert.IsTrue(Theme.DividesTerrainTile(t.groundTileArt), $"{t.name} ground art {t.groundTileArt}");
                Assert.IsTrue(Theme.DividesTerrainTile(t.groundTileFallback), $"{t.name} ground {t.groundTileFallback}");
                foreach (var s in t.roadTileArt.Concat(t.roadTileFallback)) Assert.IsTrue(Theme.DividesTerrainTile(s), $"{t.name} road {s}");
            }
        }

        [Test]
        public void TiltStrength_OffAtWalkingHeight_FullWhenAerial_Monotonic()
        {
            var c = Theme.Cozy;
            Assert.AreEqual(0f, c.TiltStrength(1.7f));
            Assert.AreEqual(1f, c.TiltStrength(38f));
            Assert.AreEqual(1f, c.TiltStrength(300f));                      // 자라섬·남이섬 상공(260~300 m)은 다 건다
            Assert.AreEqual(c.tiltHighStrength, c.TiltStrength(1400f), 1e-5f); // 1.4 km 조감은 줄인다(07b)
            float prev = 0f;
            for (float h = 0f; h <= 400f; h += 0.5f) { float s = c.TiltStrength(h); Assert.GreaterOrEqual(s, prev); prev = s; }
            for (float h = 400f; h <= 2000f; h += 5f) { float s = c.TiltStrength(h); Assert.LessOrEqual(s, prev + 1e-6f); prev = s; }
            Assert.AreEqual(0f, Theme.Realistic.TiltStrength(300f));
            // 시연 영상: 저공(16~22 m) 구간은 0, 조감(170 m)은 1
            Assert.AreEqual(0f, c.TiltStrengthVideo(22f));
            Assert.AreEqual(1f, c.TiltStrengthVideo(170f));
            Assert.Less(c.TiltStrengthVideo(60f), c.TiltStrength(60f));
        }

        [Test]
        public void ParseBuildingMaterial_WallRoofFlatAndVariants()
        {
            Assert.IsTrue(ProceduralTextures.ParseBuildingMaterial("residential", out var s, out var cls));
            Assert.AreEqual((ProceduralTextures.Slot.Wall, "residential"), (s, cls));
            Assert.IsTrue(ProceduralTextures.ParseBuildingMaterial("commercial_3", out s, out cls));
            Assert.AreEqual((ProceduralTextures.Slot.Wall, "commercial"), (s, cls));
            Assert.IsTrue(ProceduralTextures.ParseBuildingMaterial("roof_public", out s, out cls));
            Assert.AreEqual((ProceduralTextures.Slot.Roof, "public"), (s, cls));
            Assert.IsTrue(ProceduralTextures.ParseBuildingMaterial("roof_industrial_5", out s, out cls));
            Assert.AreEqual((ProceduralTextures.Slot.Roof, "industrial"), (s, cls));
            Assert.IsTrue(ProceduralTextures.ParseBuildingMaterial("flat_other", out s, out cls));
            Assert.AreEqual((ProceduralTextures.Slot.Flat, "other"), (s, cls));
            Assert.IsFalse(ProceduralTextures.ParseBuildingMaterial("M_roof", out _, out _));
            Assert.IsFalse(ProceduralTextures.ParseBuildingMaterial("bridge_road", out _, out _));
            Assert.IsFalse(ProceduralTextures.ParseBuildingMaterial(null, out _, out _));
        }

        /// <summary>30° 기운 20 m × 4 m 상판(윗면 + 북쪽 옆면 + 동쪽 끝면). 면마다 정점을 따로 둔다(파이프라인 메시와 같다).</summary>
        [Test]
        public void BridgeUv_BoxProjectionFollowsDeckAxis()
        {
            float ang = 30f * Mathf.Deg2Rad;
            var a = new Vector3(Mathf.Cos(ang), 0f, Mathf.Sin(ang));
            var c = new Vector3(-a.z, 0f, a.x);
            var o = new Vector3(100f, 50f, -40f);
            Vector3 P(float along, float across, float y) => o + a * along + c * across + Vector3.up * y;
            var v = new[]
            {
                // 윗면(y = 0)
                P(-10, -2, 0), P(10, -2, 0), P(10, 2, 0), P(-10, 2, 0),
                // 밑면(y = -1)
                P(-10, -2, -1), P(10, -2, -1), P(10, 2, -1), P(-10, 2, -1),
                // 옆면(폭 방향 +2)
                P(-10, 2, -1), P(10, 2, -1), P(10, 2, 0), P(-10, 2, 0),
                // 끝면(축 방향 +10)
                P(10, -2, -1), P(10, 2, -1), P(10, 2, 0), P(10, -2, 0),
                // 반대 옆면·끝면(상자가 닫혀 있어야 정점 주성분이 상판 축과 같다)
                P(-10, -2, -1), P(10, -2, -1), P(10, -2, 0), P(-10, -2, 0),
                P(-10, -2, -1), P(-10, 2, -1), P(-10, 2, 0), P(-10, -2, 0),
            };
            var n = new Vector3[v.Length];
            for (int i = 0; i < 4; i++) { n[i] = Vector3.up; n[4 + i] = Vector3.down; n[8 + i] = c; n[12 + i] = a; n[16 + i] = -c; n[20 + i] = -a; }
            var uv = ProceduralTextures.BridgeUv(v, n, 4f);
            float Span(int s, Func<Vector2, float> f) => Enumerable.Range(s, 4).Max(i => f(uv[i])) - Enumerable.Range(s, 4).Min(i => f(uv[i]));
            // 윗면: 축 20 m → 5, 폭 4 m → 1 (축 부호는 PCA 방향에 따라 바뀔 수 있어 범위만 본다)
            Assert.AreEqual(5f, Span(0, p => p.x), 1e-3f);
            Assert.AreEqual(1f, Span(0, p => p.y), 1e-3f);
            // 옆면: 축 20 m → 5, 높이 1 m → 0.25
            Assert.AreEqual(5f, Span(8, p => p.x), 1e-3f);
            Assert.AreEqual(0.25f, Span(8, p => p.y), 1e-3f);
            // 끝면: 폭 4 m → 1, 높이 → 0.25
            Assert.AreEqual(1f, Span(12, p => p.x), 1e-3f);
            Assert.AreEqual(0.25f, Span(12, p => p.y), 1e-3f);
            // 옆면 높이 방향 v 는 위로 갈수록 커진다(텍스처가 뒤집히지 않음)
            Assert.Greater(uv[10].y, uv[9].y);
        }

        [Test]
        public void BridgeBaseName_RecoversGlbMeshName()
        {
            Assert.AreEqual("BR_w879488267", WorldImporter.BridgeBaseName("BR_w879488267_uv"));
            Assert.AreEqual("BR_w879488267", WorldImporter.BridgeBaseName("BR_w879488267"));
            Assert.AreEqual("BR_w879488267", WorldImporter.BridgeBaseName("bridge_BR_w879488267_uv"));
            Assert.AreEqual("BR_w879488267", WorldImporter.BridgeBaseName("bridge_bridge_bridge_BR_w879488267_uv_uv_uv"));
        }

        [Test]
        public void DesaturatePixel_KeepsLuminanceAndReducesChroma()
        {
            var c = new Color(0.9f, 0.5f, 0.4f, 0.3f);
            float l = 0.2126f * c.r + 0.7152f * c.g + 0.0722f * c.b;
            Assert.AreEqual(c.r, ProceduralTextures.DesaturatePixel(c, 0f).r, 1e-6f);
            var g = ProceduralTextures.DesaturatePixel(c, 1f);
            Assert.AreEqual(l, g.r, 1e-5f); Assert.AreEqual(l, g.g, 1e-5f); Assert.AreEqual(l, g.b, 1e-5f);
            Assert.AreEqual(1f, g.a);   // 지형 레이어는 알파를 쓰지 않는다
            var h = ProceduralTextures.DesaturatePixel(c, 0.55f);
            Assert.AreEqual((c.r - c.b) * 0.45f, h.r - h.b, 1e-5f);
            foreach (var t in Theme.All)
            {
                Assert.AreEqual(4, t.artLayerDesaturate.Length);
                foreach (var a in t.artLayerDesaturate) Assert.That(a, Is.InRange(0f, 1f));
            }
            Assert.IsTrue(Theme.Realistic.artLayerDesaturate.All(a => a == 0f));
        }

        [Test]
        public void GeneratedDir_RealisticKeepsLegacyPaths()
        {
            Assert.AreEqual("Assets/World/Generated/Facades", Theme.Realistic.GeneratedDir("Assets/World/Generated", "Facades"));
            Assert.AreEqual("Assets/World/Generated/Cozy/Roads", Theme.Cozy.GeneratedDir("Assets/World/Generated", "Roads"));
            Assert.IsNull(Theme.Realistic.ArtPath("ground_grass.jpg"));
            Assert.AreEqual("Assets/Art/Cozy/ground_grass.jpg", Theme.Cozy.ArtPath("ground_grass.jpg"));
        }
    }
}
