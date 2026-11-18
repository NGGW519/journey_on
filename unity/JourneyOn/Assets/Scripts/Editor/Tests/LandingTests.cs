using NUnit.Framework;
using UnityEngine;

namespace JourneyOn.Editor.Tests
{
    /// <summary>
    /// 착지 판정(WalkController.LandingAllowed) EditMode 테스트. 합성 충돌체를 (60000, 0, 60000) 근처에 만든다.
    /// 3차-나: 허용치를 수면 메시 자르기 기준(manifest water.shore_clip_above_m 0.4)과 맞추고, 수면 위 건물 지붕 착지를 막는다.
    /// </summary>
    public class LandingTests
    {
        static readonly Vector3 O = new Vector3(60000f, 0f, 60000f);
        GameObject root;

        [SetUp] public void SetUp() => root = new GameObject("LandingTestRoot");
        [TearDown] public void TearDown() { if (root != null) Object.DestroyImmediate(root); }

        GameObject Box(string name, Vector3 center, Vector3 size, int layer)
        {
            var go = new GameObject(name) { layer = layer };
            go.transform.SetParent(root.transform, false);
            go.transform.position = O + center;
            go.AddComponent<BoxCollider>().size = size;
            return go;
        }

        /// <summary>수면 윗면 y = 0(레이어 4), 그 위 x 에 따라 다른 것을 둔다.</summary>
        void Water() => Box("Water", new Vector3(0, -0.25f, 0), new Vector3(200, 0.5f, 20), WorldImporter.WaterLayer);

        static bool At(float x, float tol) { Physics.SyncTransforms(); return WalkController.LandingAllowed(O + new Vector3(x, 30f, 0), null, tol); }

        [Test]
        public void NoWaterMesh_Allowed()
        {
            Box("Ground", new Vector3(0, -1f, 0), new Vector3(10, 2, 10), 0);
            Assert.IsTrue(At(0, 0.4f));
        }

        [Test]
        public void BandBetween03And04_BlockedOnlyWhenToleranceMatchesClip()
        {
            Water();
            Box("Bank", new Vector3(0, 0.35f - 1f, 0), new Vector3(10, 2, 10), 0);   // 지면 = 수면 + 0.35 m(메시 안 0.3~0.4 m 띠)
            Assert.IsTrue(At(0, 0.3f), "예전 허용치 0.3: 띠에서 착지 허용(물가 벽 고리 안)");
            Assert.IsFalse(At(0, 0.4f), "자르기 기준 0.4 와 같게: 막힘");
        }

        [Test]
        public void HighGroundAboveClip_Allowed()
        {
            Water();
            Box("Hill", new Vector3(0, 1f - 1f, 0), new Vector3(10, 2, 10), 0);   // 지면 = 수면 + 1 m(자른 메시라면 원래 없을 곳)
            Assert.IsTrue(At(0, 0.4f));
        }

        [Test]
        public void BuildingRoofOverWater_Blocked()
        {
            Water();
            Box("Bed", new Vector3(0, -3f, 0), new Vector3(200, 2, 20), 0);   // 하상
            var b = Box("B_floating", new Vector3(30, 1.5f, 0), new Vector3(8, 5, 8), 0);   // 지붕 4 m(수면 위 4 m)
            b.AddComponent<BuildingInfo>();
            Assert.IsFalse(At(30, 0.4f), "수상 건물 지붕: 내리면 물가 벽 고리 안");
        }

        [Test]
        public void BridgeDeckOverWater_Allowed()
        {
            Water();
            Box("Bed", new Vector3(0, -3f, 0), new Vector3(200, 2, 20), 0);
            var d = Box("BR_deck", new Vector3(-30, 2f, 0), new Vector3(4, 0.8f, 20), 0);
            d.AddComponent<BridgeInfo>();
            Assert.IsTrue(At(-30, 0.4f));
            Assert.IsFalse(At(60, 0.4f), "상판 밖 하상 위는 막힘");
        }
    }
}
