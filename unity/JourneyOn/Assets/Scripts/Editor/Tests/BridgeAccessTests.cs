using System.Collections.Generic;
using NUnit.Framework;
using UnityEngine;

namespace JourneyOn.Editor.Tests
{
    /// <summary>
    /// 교량 끝 진입 검사(WorldImporter.CheckBridgeAccess) EditMode 테스트. 합성 충돌체로 분류(육지·연결부·건물·물 위)와
    /// 막힘(물가 벽, 높이 단 > stepOffset)을 본다. 다른 씬 물체와 겹치지 않게 (50000, 0, 50000) 근처에 만든다.
    /// </summary>
    public class BridgeAccessTests
    {
        static readonly Vector3 O = new Vector3(50000f, 0f, 50000f);
        const float Margin = 3.5f;
        GameObject root;

        [SetUp]
        public void SetUp()
        {
            root = new GameObject("BridgeAccessTestRoot");
            Box("Ground", new Vector3(0, -0.5f, 10), new Vector3(80, 1, 100), 0);   // 지면 윗면 y = 0
        }

        [TearDown]
        public void TearDown()
        {
            if (root != null) Object.DestroyImmediate(root);
        }

        GameObject Box(string name, Vector3 center, Vector3 size, int layer)
        {
            var go = new GameObject(name) { layer = layer };
            go.transform.SetParent(root.transform, false);
            go.transform.position = O + center;
            go.AddComponent<BoxCollider>().size = size;
            return go;
        }

        /// <summary>z0..z1, 폭 w 의 상판(윗면 top, 두께 0.8)과 같은 footprint.</summary>
        ShoreRules.Footprint Deck(string node, float z0, float z1, float top, float w = 4f, float cx = 0f)
        {
            var go = Box(node, new Vector3(cx, top - 0.4f, (z0 + z1) / 2f), new Vector3(w, 0.8f, z1 - z0), 0);
            go.AddComponent<BridgeInfo>().node = node;
            var ring = ShoreRulesTests.StraightRing(O.x + cx, O.z + z0, O.z + z1, w);
            return new ShoreRules.Footprint(node, ring, 0.8f, w);
        }

        static BridgeAccess Run(params ShoreRules.Footprint[] fps) => WorldImporter.CheckBridgeAccess(new List<ShoreRules.Footprint>(fps), Margin);

        [Test]
        public void LowDeckOnLand_BothEndsPass()
        {
            var r = Run(Deck("BR_a", 0, 20, 0.3f));
            Assert.AreEqual(2, r.ends_land);
            Assert.AreEqual(2, r.ends_passed);
            Assert.AreEqual(0, r.ends_blocked);
        }

        [Test]
        public void ShoreWallAcrossApproach_BlocksAsWall()
        {
            var f = Deck("BR_a", 0, 20, 0.3f);
            Box("ShoreWall", new Vector3(0, 1.5f, -3), new Vector3(12, 5, 0.2f), 2);   // 시작 끝 앞 3 m 물가 벽(레이어 2)
            var r = Run(f);
            Assert.AreEqual(2, r.ends_land);
            Assert.AreEqual(1, r.ends_blocked_wall);
            Assert.AreEqual(1, r.ends_passed);
            Assert.AreEqual("start", r.blocked[0].which);
            Assert.AreEqual("wall", r.blocked[0].kind);
        }

        [Test]
        public void LowWallBelowWalkingBand_DoesNotBlock()
        {
            var f = Deck("BR_a", 0, 20, 0.3f);
            Box("ShoreWallLow", new Vector3(0, 0.15f, -3), new Vector3(12, 0.3f, 0.2f), 2);   // 윗면 0.3 m(< 지면 + 0.5) → 넘는다
            var r = Run(f);
            Assert.AreEqual(0, r.ends_blocked);
        }

        [Test]
        public void DeckEndAboveStepOffset_BlocksAsStep()
        {
            var r = Run(Deck("BR_high", 0, 20, 0.9f));
            Assert.AreEqual(2, r.ends_land);
            Assert.AreEqual(2, r.ends_blocked_step);
            Assert.AreEqual("step", r.blocked[0].kind);
            Assert.AreEqual(0.9f, r.blocked[0].step_m, 0.05f);
        }

        [Test]
        public void OuterPointOnBuildingRoof_IsBuildingNotLand()
        {
            var f = Deck("BR_a", 0, 20, 0.3f);
            var b = Box("B_roof", new Vector3(0, 2.5f, -6), new Vector3(10, 5, 6), 0);   // 시작 끝 바깥 5 m(0°, ±30°)가 모두 지붕 위
            b.AddComponent<BuildingInfo>();
            var r = Run(f);
            Assert.AreEqual(1, r.ends_building);
            Assert.AreEqual(1, r.ends_land);
            CollectionAssert.Contains(r.building_ends, "BR_a:start");
        }

        [Test]
        public void BuildingStraightAhead_LandAt30Degrees_IsLandAndPasses()
        {
            var f = Deck("BR_stair", 0, 20, 0.3f);
            var b = Box("B_ahead", new Vector3(0, 2.5f, -6), new Vector3(2.5f, 5, 4), 0);   // 축 앞 5 m 만 건물, ±30° 바깥 점은 땅
            b.AddComponent<BuildingInfo>();
            var r = Run(f);
            Assert.AreEqual(0, r.ends_building);
            Assert.AreEqual(2, r.ends_land);
            Assert.AreEqual(2, r.ends_passed);
        }

        [Test]
        public void ChainJointAndAttachedEnd_AreJointNotLand()
        {
            var a = Deck("BR_a", 0, 20, 0.3f);
            var b = Deck("BR_b", 20, 40, 0.3f);                 // a 의 끝에 이어진 사슬(바깥 5 m 가 상판이 아닌 방향이어도 끝점이 붙어 있다)
            var r = Run(a, b);
            Assert.AreEqual(2, r.ends_joint);
            Assert.AreEqual(2, r.ends_land);
            CollectionAssert.Contains(r.joint_ends, "BR_a:end");
            CollectionAssert.Contains(r.joint_ends, "BR_b:start");
        }

        [Test]
        public void OuterPointOverWater_IsWaterNotLand()
        {
            var f = Deck("BR_a", 0, 20, 0.6f);
            Box("Water", new Vector3(0, 0.25f, -10), new Vector3(20, 0.5f, 18), WorldImporter.WaterLayer);   // 수면 0.5 m > 지면 0
            var r = Run(f);
            Assert.AreEqual(1, r.ends_water);
            CollectionAssert.Contains(r.water_ends, "BR_a:start");
        }
    }
}
