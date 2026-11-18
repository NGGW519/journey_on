using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;

namespace JourneyOn.Editor
{
    /// <summary>
    /// data/out/&lt;area&gt;/manifest.json 을 읽어 World/Tile_E_N/{Terrain,Buildings,Bridges,Water} 와 World/Skirt 를 만들고
    /// Assets/Scenes/&lt;area&gt;.unity 로 저장한다. 손으로 씬을 고치지 않고 이 스크립트를 다시 돌린다.
    /// manifest_version 1 과 2(도로 마스크·교량·원경·외벽 UV, manifest-schema.md C1~C4)를 모두 읽는다.
    /// 배치 모드: -executeMethod JourneyOn.Editor.WorldImporter.ImportFromCommandLine -area phase1 [-dataRoot path]
    /// </summary>
    public static class WorldImporter
    {
        const string TileAssetRoot = "Assets/World/Tiles";
        const string GeneratedRoot = "Assets/World/Generated";
        const string MaterialRoot = "Assets/Materials";
        public const int WaterLayer = 4;
        const int IgnoreRaycastLayer = 2;

        /// <summary>
        /// Terrain LOD 허용 오차(화면 픽셀). 계약 C3 의 교량 끝 ±0.3 m 맞춤과 C2 도로 마스크가 LOD 로 어긋나지 않게
        /// 기본값 5(v1 은 3)보다 낮춘다. 충돌(TerrainCollider)은 LOD 와 무관하게 원 해상도다.
        /// </summary>
        public const float HeightmapPixelError = 1f;

        // 스폰 위치는 manifest.spawn(파이프라인이 물가에서 떨어진 육지로 고른 점)을 쓴다.
        // manifest 에 없을 때만 남이섬 선착장(가평 측) 근처 육지 좌표로 대신한다.
        static readonly Vector2 FallbackSpawnEN = new Vector2(246285f, 578310f);

        static List<string> warnings;
        /// <summary>이번 실행의 아트 테마(D-014). Import·RefreshLook 시작 때 정한다(명령줄 -theme, 기본 cozy).</summary>
        static Theme theme = Theme.Cozy;
        /// <summary>계약 C3 교량 여유(= water.shore_overlap_m + 0.5). Import 시작 때 정한다.</summary>
        static float bridgeMargin = ShoreRules.BridgeMargin;
        /// <summary>착지·진입 검사의 묻힌 수면 허용치(= manifest water.shore_clip_above_m, 없으면 0.3). Import 시작 때 정한다.</summary>
        static float landingTolerance = WalkController.DefaultBuriedWaterTolerance;

        public static string DefaultDataRoot() =>
            Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "..", "data", "out"));

        [MenuItem("JourneyOn/Import Area phase1")]
        static void MenuImportPhase1() => Import("phase1", DefaultDataRoot(), Theme.Cozy);

        [MenuItem("JourneyOn/Refresh Look phase1 (cozy)")]
        static void MenuRefreshLook()
        {
            if (!EditorSceneManager.SaveCurrentModifiedScenesIfUserWantsTo()) return;
            RefreshLook("phase1", Theme.Cozy);
        }

        [MenuItem("JourneyOn/Refresh Look phase1 (realistic)")]
        static void MenuRefreshLookRealistic()
        {
            if (!EditorSceneManager.SaveCurrentModifiedScenesIfUserWantsTo()) return;
            RefreshLook("phase1", Theme.Realistic);
        }

        /// <summary>
        /// 배치 모드: -executeMethod JourneyOn.Editor.WorldImporter.RefreshLookFromCommandLine -area phase1 [-theme cozy|realistic]
        /// data/out 타일을 다시 읽지 않고(파이프라인이 다시 쓰는 중이어도 된다) 기존 씬의 겉모습만 테마(기본 cozy) 코드 값으로 다시 맞춘다:
        /// 텍스처(생성 이미지 → 없으면 코드 생성)·재질(건물 틴트 변형 재배정·도로 레이어·지면 바탕 레이어·교량 UV 사본·수면·하늘),
        /// 조명·안개·후처리 Volume·SSAO·그림자 거리, 플레이어 카메라·HUD, 원경 정점색 재칠.
        /// manifest 는 facade 값만 읽는다(실패 시 몇 번 다시 시도). 임포트와 같은 함수를 쓰므로 다음 재임포트 결과와 같다.
        /// unity_import.json 은 쓰지 않는다.
        /// </summary>
        public static void RefreshLookFromCommandLine()
        {
            var args = Environment.GetCommandLineArgs();
            string area = Arg(args, "-area") ?? "phase1";
            try { RefreshLook(area, Theme.FromArgs(args)); EditorApplication.Exit(0); }
            catch (Exception e) { Debug.LogException(e); EditorApplication.Exit(1); }
        }

        public static void RefreshLook(string area, Theme t)
        {
            warnings = new List<string>();
            theme = t;
            ProceduralTextures.ResetArtLog();
            AssetDatabase.Refresh();   // 생성 이미지가 새로 들어왔으면 임포트(CozyArtImport 가 설정을 맞춘다)
            var scene = EditorSceneManager.OpenScene($"Assets/Scenes/{area}.unity", OpenSceneMode.Single);
            var m = CaptureCommon.LoadManifestWithRetry(area);
            var bmats = ProceduralTextures.EnsureBuildingMaterials(m.facade, t);
            var roadLayers = ProceduralTextures.EnsureRoadLayers(t);
            var bridgeMats = ProceduralTextures.EnsureBridgeMaterials(t);
            var waterMat = EnsureWaterMaterial(t);
            var report = new ImportReport { area = area, manifest_version = m.manifest_version, theme = t.name };
            report.fog_density = ThemeApply.ApplyEnvironment(m, t);
            var terrains = UnityEngine.Object.FindObjectsByType<Terrain>(FindObjectsSortMode.None);
            int grounds = ApplyGroundLayers(terrains, t);
            int buildings = ApplyBuildingLook(bmats);
            int bridges = ApplyBridgeLook(area, bridgeMats);
            ThemeApply.ApplyPlayerLook(t);
            var skirt = GameObject.Find("World/Skirt");
            if (skirt != null) ApplySkirtLook(m, area, skirt, waterMat, terrains, report);
            EditorSceneManager.MarkSceneDirty(scene);
            EditorSceneManager.SaveScene(scene);
            AssetDatabase.SaveAssets();
            Debug.Log($"[WorldImporter] RefreshLook 완료(테마 {t.name}): 생성 이미지 {ProceduralTextures.ArtUsed.Count}개 사용 [{string.Join(", ", ProceduralTextures.ArtUsed)}], " +
                      $"없어서 코드 생성 {ProceduralTextures.ArtMissing.Count}개 [{string.Join(", ", ProceduralTextures.ArtMissing)}]; " +
                      $"지면 바탕 레이어 {grounds}/{terrains.Length}, 건물 렌더러 {buildings}, 교량 메시 {bridges}, 안개 {report.fog_density:0.000000}; " +
                      $"지면 알베도(선형) {Fmt(report.ground_albedo_linear)}, 덮임 {Fmt(report.ground_layer_coverage)}, " +
                      $"원경 원래 평균 {Fmt(report.skirt_source_mean_linear)} → {Fmt(report.skirt_recolor_target_linear)} (변화 {report.skirt_recolor_variation}), " +
                      $"원경 {report.skirt_nodes}+수면 {report.skirt_water_nodes}, 경고 {warnings.Count} {string.Join(" / ", warnings)}");
        }

        /// <summary>
        /// 지형 바탕 레이어(타일마다 layer.terrainlayer)를 테마 잔디 텍스처·타일 크기로 맞춘다. 정사영상을 쓰는 타일은 그대로 둔다(D-014).
        /// 도로 레이어(공용 에셋)는 EnsureRoadLayers 가 바꾸고, 여기서는 basemap 만 다시 만들게 표시한다.
        /// </summary>
        static int ApplyGroundLayers(IEnumerable<Terrain> terrains, Theme t)
        {
            var tex = ProceduralTextures.EnsureGroundTexture(t, out float tile, out Vector4 tint);
            int n = 0;
            foreach (var terr in terrains)
            {
                var td = terr.terrainData;
                var layers = td.terrainLayers;
                if (layers == null || layers.Length == 0 || layers[0] == null) continue;
                var l0 = layers[0];
                bool ortho = l0.diffuseTexture != null && AssetDatabase.GetAssetPath(l0.diffuseTexture).EndsWith("/ortho.jpg", StringComparison.Ordinal);
                if (!ortho)
                {
                    l0.diffuseTexture = tex;
                    l0.tileSize = new Vector2(tile, tile);
                    l0.tileOffset = Vector2.zero;
                    l0.smoothness = 0f;
                    l0.metallic = 0f;
                    ProceduralTextures.SetLayerTint(l0, tint);
                    EditorUtility.SetDirty(l0);
                    n++;
                }
                td.terrainLayers = layers;
                td.SetBaseMapDirty();
                EditorUtility.SetDirty(td);
            }
            return n;
        }

        /// <summary>
        /// 씬의 건물 렌더러(World/Tile_*/Buildings 아래)에 테마 재질을 다시 배정한다. 자리(벽·지붕·단색)는 지금 재질 이름으로 알아내고
        /// (<see cref="ProceduralTextures.ParseBuildingMaterial"/>, 모르면 submesh 1 = 지붕), 용도는 BuildingInfo, 틴트 변형은 건물 id 해시로 정한다.
        /// </summary>
        static int ApplyBuildingLook(ProceduralTextures.BuildingMaterials mats)
        {
            int n = 0;
            foreach (var mr in UnityEngine.Object.FindObjectsByType<MeshRenderer>(FindObjectsInactive.Include, FindObjectsSortMode.None))
            {
                if (!UnderWrapper(mr.transform, "Buildings")) continue;
                var bi = mr.GetComponentInParent<BuildingInfo>(true);
                string cls = bi != null && mats.wall.ContainsKey(bi.usageClass ?? "") ? bi.usageClass : "other";
                string key = bi != null && !string.IsNullOrEmpty(bi.id) ? bi.id : mr.gameObject.name;
                var cur = mr.sharedMaterials;
                var next = new Material[cur.Length];
                for (int i = 0; i < cur.Length; i++)
                {
                    var slot = cur[i] != null && ProceduralTextures.ParseBuildingMaterial(cur[i].name, out var s, out _) ? s
                             : i == 1 ? ProceduralTextures.Slot.Roof : ProceduralTextures.Slot.Wall;
                    next[i] = mats.For(slot, cls, key);
                }
                mr.sharedMaterials = next;
                n++;
            }
            return n;
        }

        static bool UnderWrapper(Transform t, string wrapper)
        {
            for (var cur = t; cur != null; cur = cur.parent)
                if (cur.name == wrapper && cur.parent != null && cur.parent.name.StartsWith("Tile_", StringComparison.Ordinal)) return true;
            return false;
        }

        /// <summary>씬의 교량(World/Tile_*/Bridges 아래)에 kind 별 테마 재질과 UV 사본 메시를 다시 배정한다(충돌체는 원본 메시 그대로).</summary>
        static int ApplyBridgeLook(string area, Dictionary<string, Material> mats)
        {
            int n = 0;
            var world = GameObject.Find("World");
            if (world == null) return 0;
            foreach (Transform tile in world.transform)
            {
                var br = tile.Find("Bridges");
                if (br == null) continue;
                string glb = $"{TileAssetRoot}/{area}/{tile.name}/bridges.glb";
                var originals = AssetDatabase.LoadAllAssetsAtPath(glb).OfType<Mesh>().GroupBy(x => x.name).ToDictionary(g => g.Key, g => g.First());
                foreach (var mf in br.GetComponentsInChildren<MeshFilter>(true))
                {
                    var mr = mf.GetComponent<MeshRenderer>();
                    var bi = mf.GetComponentInParent<BridgeInfo>(true);
                    string kind = bi != null && mats.ContainsKey(bi.kind ?? "") ? bi.kind : "road";
                    if (mr != null) mr.sharedMaterial = mats[kind];
                    var cur = mf.sharedMesh;
                    if (cur == null) continue;
                    // 원본(glb) 메시: 노드 이름(= 파이프라인 메시 이름) → UV 사본 이름에서 접두·접미사를 뗀 이름. 못 찾으면 건너뛴다(사본의 사본을 만들지 않는다)
                    Mesh src = null;
                    if (bi != null && !string.IsNullOrEmpty(bi.node)) originals.TryGetValue(bi.node, out src);
                    if (src == null) originals.TryGetValue(BridgeBaseName(cur.name), out src);
                    if (src == null)
                    {
                        warnings.Add($"{tile.name}: 교량 메시 {cur.name} 의 glb 원본을 찾지 못함 → UV 사본 그대로");
                        continue;
                    }
                    mf.sharedMesh = EnsureBridgeUvMesh(src, $"{TileAssetRoot}/{area}/{tile.name}");
                    n++;
                }
            }
            return n;
        }

        const string UvSuffix = "_uv";

        /// <summary>
        /// 교량 메시 사본에 상자 투영 UV(<see cref="ProceduralTextures.BridgeUv"/>, Theme.bridgeTileM)를 넣어 저장한다. 같은 경로면 덮어써 GUID 유지.
        /// 파일 이름 = 메시 이름(&lt;BR_…&gt;_uv.asset): Unity 는 새 에셋의 메시 이름을 파일 이름으로 바꾸므로, 둘이 다르면 다음 실행에서 원본을 못 찾는다(07 에서 발견).
        /// </summary>
        static Mesh EnsureBridgeUvMesh(Mesh src, string assetDir)
        {
            var copy = UnityEngine.Object.Instantiate(src);
            copy.name = src.name + UvSuffix;
            copy.SetUVs(0, ProceduralTextures.BridgeUv(src.vertices, src.normals, theme.bridgeTileM));
            return SaveMeshAsset(copy, $"{assetDir}/{src.name}{UvSuffix}.asset");
        }

        /// <summary>UV 사본 메시 이름 → glb 원본 이름. 예전 이름(bridge_&lt;BR_…&gt;_uv, 겹친 접두·접미사)도 되돌린다. 테스트용으로 internal.</summary>
        internal static string BridgeBaseName(string meshName)
        {
            string n = meshName ?? "";
            while (n.EndsWith(UvSuffix, StringComparison.Ordinal)) n = n.Substring(0, n.Length - UvSuffix.Length);
            while (n.StartsWith("bridge_", StringComparison.Ordinal)) n = n.Substring("bridge_".Length);
            return n;
        }

        static string Fmt(float[] v) => v == null || v.Length == 0 ? "-" : "(" + string.Join(", ", v.Select(x => x.ToString("0.000", System.Globalization.CultureInfo.InvariantCulture))) + ")";

        public static void ImportFromCommandLine()
        {
            var args = Environment.GetCommandLineArgs();
            string area = Arg(args, "-area") ?? "phase1";
            string root = Arg(args, "-dataRoot") ?? DefaultDataRoot();
            try
            {
                Import(area, root, Theme.FromArgs(args));
                EditorApplication.Exit(0);
            }
            catch (Exception e)
            {
                Debug.LogException(e);
                EditorApplication.Exit(1);
            }
        }

        static string Arg(string[] args, string key)
        {
            int i = Array.IndexOf(args, key);
            return i >= 0 && i + 1 < args.Length ? args[i + 1] : null;
        }

        public static AreaManifest LoadManifest(string dataRoot, string area)
        {
            string manifestPath = Path.Combine(dataRoot, area, "manifest.json");
            if (!File.Exists(manifestPath)) throw new FileNotFoundException("manifest 없음: " + manifestPath);
            var m = JsonUtility.FromJson<AreaManifest>(File.ReadAllText(manifestPath));
            if (m.manifest_version < 1 || m.manifest_version > 2) throw new Exception($"지원하지 않는 manifest_version {m.manifest_version}");
            return m;
        }

        class PendingTile
        {
            public TileRef t;
            public TileJson tj;
            public string tileJsonText, tileDir, assetDir;
            public GameObject go;
            public Dictionary<string, (BridgeItem item, List<Vector2> fp)> bridges = new();
        }

        public static ImportReport Import(string area, string dataRoot) => Import(area, dataRoot, Theme.Cozy);

        public static ImportReport Import(string area, string dataRoot, Theme look)
        {
            warnings = new List<string>();
            theme = look;
            ProceduralTextures.ResetArtLog();
            string areaDir = Path.Combine(dataRoot, area);
            var m = LoadManifest(dataRoot, area);
            Debug.Log($"[WorldImporter] {m.title} — manifest v{m.manifest_version}, 타일 {m.tiles.Length}개, 테마 {look.name}");

            EnsureFolder(TileAssetRoot + "/" + area);
            EnsureFolder(GeneratedRoot);
            var bmats = ProceduralTextures.EnsureBuildingMaterials(m.facade, look);
            var waterMat = EnsureWaterMaterial(look);
            Dictionary<string, Material> bridgeMats = null;
            TerrainLayer[] roadLayers = null;

            var scene = EditorSceneManager.NewScene(NewSceneSetup.DefaultGameObjects, NewSceneMode.Single);
            foreach (var cam in UnityEngine.Object.FindObjectsByType<Camera>(FindObjectsSortMode.None))
                UnityEngine.Object.DestroyImmediate(cam.gameObject);

            var report = new ImportReport { area = area, manifest_version = m.manifest_version, manifest_tiles = m.tiles.Length, theme = look.name };
            float overlap = ShoreOverlap(m, File.ReadAllText(Path.Combine(areaDir, "manifest.json")), out string overlapSrc);
            if (m.water != null) report.water_shore_clip_above_m = m.water.shore_clip_above_m;
            // 착지 허용치 = 수면 메시 자르기 기준(같지 않으면 메시 안 그 차이 띠에서 착지가 허용된다, 3차-나)
            landingTolerance = m.water != null && m.water.shore_clip_above_m > 0f ? m.water.shore_clip_above_m : WalkController.DefaultBuriedWaterTolerance;
            report.landing_tolerance_m = landingTolerance;
            bridgeMargin = overlap + ShoreRules.BridgeMarginExtra;
            report.bridge_margin_m = bridgeMargin;
            report.bridge_margin_source = $"{overlapSrc} {overlap:0.0###} + {ShoreRules.BridgeMarginExtra}";
            originE = m.origin.easting;
            originN = m.origin.northing;
            report.fog_density = ThemeApply.ApplyEnvironment(m, look);
            var world = new GameObject("World");
            var terrains = new Dictionary<(int, int), Terrain>();
            int metaTotal = 0;

            AssetDatabase.StartAssetEditing();
            var pending = new List<PendingTile>();
            try
            {
                foreach (var t in m.tiles)
                {
                    string tileDir = Path.Combine(areaDir, t.dir);
                    string text = File.ReadAllText(Path.Combine(tileDir, "tile.json"));
                    var tj = ParseTileJson(text, t.name);
                    string assetDir = $"{TileAssetRoot}/{area}/{t.name}";
                    EnsureFolder(assetDir);
                    var tileGo = new GameObject(t.name);
                    tileGo.transform.SetParent(world.transform, false);
                    tileGo.transform.localPosition = new Vector3(t.unity_position[0], 0f, t.unity_position[2]);
                    tileGo.isStatic = true;
                    // glb·영상 파일은 먼저 복사만 하고, 임포트는 한 번에
                    if (tj.buildings != null && tj.buildings.count > 0) CopyIn(Path.Combine(tileDir, tj.buildings.file), assetDir + "/buildings.glb");
                    if (tj.water != null && tj.water.count > 0) CopyIn(Path.Combine(tileDir, tj.water.file), assetDir + "/water.glb");
                    if (tj.ortho != null && !string.IsNullOrEmpty(tj.ortho.file)) CopyIn(Path.Combine(tileDir, tj.ortho.file), assetDir + "/ortho.jpg");
                    if (tj.HasBridges) CopyIn(Path.Combine(tileDir, tj.bridges.file), assetDir + "/bridges.glb");
                    pending.Add(new PendingTile { t = t, tj = tj, tileJsonText = text, tileDir = tileDir, assetDir = assetDir, go = tileGo });
                }
                CopyIn(Path.Combine(areaDir, m.axis_test), $"{TileAssetRoot}/{area}/axis_test.glb");
                if (m.HasSkirt) CopyIn(Path.Combine(areaDir, m.skirt.file), $"{TileAssetRoot}/{area}/skirt.glb");
            }
            finally
            {
                AssetDatabase.StopAssetEditing();
            }
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);

            // C3: 물가 벽을 만들기 전에 모든 타일의 교량 상판 외곽(월드 좌표)을 모은다
            var footprints = new List<ShoreRules.Footprint>();
            foreach (var p in pending)
            {
                if (!p.tj.HasBridges) continue;
                ReadBridgeItems(p);
                foreach (var kv in p.bridges)
                    if (kv.Value.fp.Count >= 3) footprints.Add(new ShoreRules.Footprint(kv.Key, kv.Value.fp, kv.Value.item.deck_thickness_m, kv.Value.item.width_m));
                    else warnings.Add($"{p.t.name}: {kv.Key} footprint_xz 점 {kv.Value.fp.Count}개(3개 미만) → 물가 벽 낮춤 대상 아님");
                report.bridge_items += p.bridges.Count;
            }
            report.bridge_footprints = footprints.Count;

            var groundTex = ProceduralTextures.EnsureGroundTexture(look, out float groundTile, out Vector4 groundTint);
            var uvCheck = new FacadeUvAccumulator(m.facade);
            foreach (var p in pending)
            {
                var tj = p.tj;
                if (tj.dem != null && !string.IsNullOrEmpty(tj.dem.file))
                {
                    if (tj.HasRoads && roadLayers == null) roadLayers = ProceduralTextures.EnsureRoadLayers(look);
                    var terr = BuildTerrain(m, tj, p.tileDir, p.assetDir, p.go, groundTex, groundTile, groundTint, roadLayers, report);
                    terrains[(p.t.e0, p.t.n0)] = terr;
                    report.terrains++;
                }
                if (tj.buildings != null && tj.buildings.count > 0)
                {
                    var (nodes, withInfo, meta) = BuildBuildings(tj, p.tileDir, p.assetDir, p.go, bmats, uvCheck);
                    report.building_nodes += nodes;
                    report.buildings_with_info += withInfo;
                    metaTotal += meta;
                }
                if (tj.HasBridges)
                {
                    bridgeMats ??= ProceduralTextures.EnsureBridgeMaterials(look);
                    report.bridge_nodes += BuildBridges(p, bridgeMats);
                }
            }
            // C3: 물가 벽 윗면을 상판 밑면에 맞추려면 모든 타일의 교량 충돌체가 먼저 있어야 한다
            // (교량은 중심이 든 타일에만 있으므로 옆 타일 수면이 먼저 처리될 수 있다)
            Physics.SyncTransforms();
            foreach (var p in pending)
            {
                if (p.tj.water == null || p.tj.water.count <= 0) continue;
                BuildWater(p.assetDir, p.go, waterMat, footprints, report);
                report.water_tiles++;
            }
            if (footprints.Count > 0)
            {
                report.bridge_access = CheckBridgeAccess(footprints, bridgeMargin, originE, originN, landingTolerance);
                if (report.bridge_access.ends_blocked > 0)
                    warnings.Add($"교량 끝 진입 막힘 {report.bridge_access.ends_blocked}/{report.bridge_access.ends_land}곳(물가 벽 {report.bridge_access.ends_blocked_wall}, 높이 단 {report.bridge_access.ends_blocked_step}) → unity_import.json bridge_access");
            }
            if (report.shore_edges_bridge_unresolved > 0)
                warnings.Add($"교량 변 {report.shore_edges_bridge_unresolved}개: 상판 충돌체를 찾지 못해 물가 벽을 기본 높이로 둠");
            report.building_meta = metaTotal;
            report.facade_uv_check = uvCheck.Finish();
            if (report.facade_uv_check.status == "FAIL")
                warnings.Add($"facade_uv_check FAIL: {report.facade_uv_check.bad}/{report.facade_uv_check.@checked}동의 벽 uv.y 범위가 [0, height_m ÷ floor_height_m] 와 다름(계약 C1, 파이프라인 확인 필요)");
            if (report.bridge_nodes != report.bridge_items)
                warnings.Add($"교량 glb 노드 {report.bridge_nodes} ≠ tile.json items {report.bridge_items}");

            if (m.HasSkirt) BuildSkirt(m, area, world, waterMat, terrains, report);

            int ts = m.tile_size_m;
            foreach (var kv in terrains)
            {
                var (e, n) = kv.Key;
                terrains.TryGetValue((e - ts, n), out var left);
                terrains.TryGetValue((e + ts, n), out var right);
                terrains.TryGetValue((e, n + ts), out var top);
                terrains.TryGetValue((e, n - ts), out var bottom);
                kv.Value.SetNeighbors(left, top, right, bottom);
            }

            CheckAxis(area, report);
            BuildBounds(m);
            BuildPlayer(m, terrains, report);

            string scenePath = $"Assets/Scenes/{area}.unity";
            EnsureFolder("Assets/Scenes");
            EditorSceneManager.SaveScene(scene, scenePath);
            EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(scenePath, true) };
            AssetDatabase.SaveAssets();

            report.heightmap_pixel_error = HeightmapPixelError;
            report.art_used = ProceduralTextures.ArtUsed.ToArray();
            report.art_missing = ProceduralTextures.ArtMissing.ToArray();
            report.scene = scenePath;
            report.warnings = warnings.ToArray();
            report.imported_at = DateTime.Now.ToString("s");
            string reportDir = Path.Combine(areaDir, "reports");
            Directory.CreateDirectory(reportDir);
            File.WriteAllText(Path.Combine(reportDir, "unity_import.json"), JsonUtility.ToJson(report, true));
            Debug.Log($"[WorldImporter] 완료: 지형 {report.terrains}(도로 마스크 {report.roads_tiles}), 건물 노드 {report.building_nodes}/메타 {report.building_meta}, " +
                      $"외벽 UV {report.facade_uv_check.status}({report.facade_uv_check.ok}/{report.facade_uv_check.@checked}), " +
                      $"교량 {report.bridge_nodes}/{report.bridge_items}(여유 {report.bridge_margin_m:0.0} m; 물가 벽 변 {report.shore_edges_total} 중 교량 {report.shore_edges_bridge_near}: " +
                      $"낮춤 {report.shore_edges_bridge_lowered}, 뺌 {report.shore_edges_bridge_removed}, 끝 구역 {report.shore_edges_bridge_end}, 옆면 유지 {report.shore_edges_bridge_side_kept}, 미확인 {report.shore_edges_bridge_unresolved}; " +
                      $"끝 진입 통과 {report.bridge_access.ends_passed}/{report.bridge_access.ends_land}, 막힘 {report.bridge_access.ends_blocked}(벽 {report.bridge_access.ends_blocked_wall}·단 {report.bridge_access.ends_blocked_step}), 물 위 {report.bridge_access.ends_water}, 연결부 {report.bridge_access.ends_joint}, 건물 {report.bridge_access.ends_building}), " +
                      $"수면 타일 {report.water_tiles}, 원경 {report.skirt_nodes}+수면 {report.skirt_water_nodes}, 축 {(report.axis_ok ? "OK" : "FAIL")}, " +
                      $"테마 {look.name}(생성 이미지 {report.art_used.Length}개, 코드 생성 대체 {report.art_missing.Length}개), 경고 {warnings.Count}");
            return report;
        }

        // ------------------------------------------------------------------ 지형(+C2 도로 마스크)
        static Terrain BuildTerrain(AreaManifest m, TileJson tj, string tileDir, string assetDir, GameObject tileGo, Texture2D groundTex, float groundTile, Vector4 groundTint,
                                    TerrainLayer[] roadLayers, ImportReport report)
        {
            var hm = m.heightmap;
            int res = hm.resolution;
            byte[] raw = File.ReadAllBytes(Path.Combine(tileDir, tj.dem.file));
            if (raw.Length != res * res * 2) throw new Exception($"{tj.name} RAW 크기 {raw.Length}");
            var heights = new float[res, res];
            for (int z = 0; z < res; z++)               // 첫 행 = 남쪽(row_order south_to_north)
                for (int x = 0; x < res; x++)
                {
                    int i = (z * res + x) * 2;
                    heights[z, x] = (ushort)(raw[i] | (raw[i + 1] << 8)) / 65535f;
                }

            var td = new TerrainData { heightmapResolution = res };
            td.size = new Vector3(m.tile_size_m, hm.range_m, m.tile_size_m);
            td.SetHeights(0, 0, heights);

            var layer = new TerrainLayer();
            string orthoPath = assetDir + "/ortho.jpg";
            var ortho = File.Exists(orthoPath) ? AssetDatabase.LoadAssetAtPath<Texture2D>(orthoPath) : null;
            if (ortho != null)
            {
                var imp = (TextureImporter)AssetImporter.GetAtPath(orthoPath);
                if (imp.maxTextureSize < tj.ortho.px || imp.wrapMode != TextureWrapMode.Clamp)
                {
                    imp.maxTextureSize = Mathf.Max(2048, tj.ortho.px);
                    imp.wrapMode = TextureWrapMode.Clamp;
                    imp.SaveAndReimport();
                }
                layer.diffuseTexture = ortho;
                layer.tileSize = new Vector2(m.tile_size_m, m.tile_size_m);   // 타일 전체에 한 장
                report.ortho_used = true;
            }
            else
            {
                layer.diffuseTexture = groundTex;   // 기획서 대비책: 정사영상 전에는 테마 잔디 재질(D-014)
                layer.tileSize = new Vector2(groundTile, groundTile);   // 1000 m 를 나누어떨어지게(타일 경계 무늬 이음)
                ProceduralTextures.SetLayerTint(layer, groundTint);
            }
            layer.tileOffset = Vector2.zero;
            layer.smoothness = 0f;
            layer.metallic = 0f;
            ReplaceAsset(layer, assetDir + "/layer.terrainlayer");
            td.terrainLayers = new[] { layer };
            // 알파맵 텍스처가 하위 에셋으로 저장되도록 TerrainData 를 먼저 에셋으로 만든 뒤 레이어·알파맵을 바꾼다
            ReplaceAsset(td, assetDir + "/terrain.asset");

            if (tj.HasRoads && roadLayers != null)
            {
                string maskPath = Path.Combine(tileDir, tj.roads.mask);
                if (!File.Exists(maskPath)) warnings.Add($"{tj.name}: roads_mask 없음 {maskPath} → ground 만 사용");
                else if (ApplyRoadMask(td, layer, roadLayers, maskPath, tj.name)) report.roads_tiles++;
            }

            var go = Terrain.CreateTerrainGameObject(td);
            go.name = "Terrain";
            go.transform.SetParent(tileGo.transform, false);
            go.transform.localPosition = new Vector3(0f, hm.base_m, 0f);
            go.isStatic = true;
            var terr = go.GetComponent<Terrain>();
            terr.heightmapPixelError = HeightmapPixelError;
            terr.basemapDistance = 1500f;
            terr.drawInstanced = true;
            terr.groupingID = 1;
            terr.allowAutoConnect = true;
            return terr;
        }

        /// <summary>
        /// 계약 C2: roads_mask.png(R 차도, G 보행·자전거, B 철도, 0~255 덮임 비율)를 alphamap [ground, 아스팔트, 보도, 자갈] 로 넣는다.
        /// ground = 1 − (R+G+B), 합이 1 을 넘으면 R·G·B 를 합으로 나눈다.
        /// 방향: PNG 행 0 = 북쪽. Texture2D.LoadImage 는 Unity 텍스처 규약대로 PNG 맨 아래 행을 y = 0 에 둔다
        /// (y = H−1−r). alphamap[z, x] 도 z = 0 이 남쪽이므로 픽셀 (x, y) 를 alphamap[y, x] 에 그대로 넣는 것이 계약의 '행 뒤집기'다.
        /// </summary>
        static bool ApplyRoadMask(TerrainData td, TerrainLayer ground, TerrainLayer[] roadLayers, string maskPath, string tileName)
        {
            var tex = new Texture2D(2, 2, TextureFormat.RGBA32, false, true);
            try
            {
                if (!tex.LoadImage(File.ReadAllBytes(maskPath), false)) { warnings.Add($"{tileName}: roads_mask 읽기 실패"); return false; }
                int mw = tex.width, mh = tex.height;
                int res = Mathf.Clamp(Mathf.ClosestPowerOfTwo(Mathf.Max(mw, mh)), 16, 4096);
                if (mw != 1024 || mh != 1024) warnings.Add($"{tileName}: roads_mask 크기 {mw}x{mh}(계약 1024x1024) → {res} 로 보간");
                td.terrainLayers = new[] { ground, roadLayers[0], roadLayers[1], roadLayers[2] };
                td.alphamapResolution = res;
                var px = tex.GetPixels32();
                bool direct = mw == res && mh == res;
                var a = new float[res, res, 4];
                for (int z = 0; z < res; z++)
                    for (int x = 0; x < res; x++)
                    {
                        float r, g, b;
                        if (direct)
                        {
                            var c = px[z * mw + x];
                            r = c.r / 255f; g = c.g / 255f; b = c.b / 255f;
                        }
                        else
                        {
                            var c = tex.GetPixelBilinear((x + 0.5f) / res, (z + 0.5f) / res);
                            r = c.r; g = c.g; b = c.b;
                        }
                        float sum = r + g + b;
                        if (sum > 1f) { r /= sum; g /= sum; b /= sum; sum = 1f; }
                        a[z, x, 0] = 1f - sum;
                        a[z, x, 1] = r;
                        a[z, x, 2] = g;
                        a[z, x, 3] = b;
                    }
                td.SetAlphamaps(0, 0, a);
                foreach (var at in td.alphamapTextures)
                    if (at != null && !AssetDatabase.Contains(at)) AssetDatabase.AddObjectToAsset(at, td);
                EditorUtility.SetDirty(td);
                return true;
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(tex);
            }
        }

        // ------------------------------------------------------------------ 건물(+C1 외벽·지붕 재질)
        static (int nodes, int withInfo, int meta) BuildBuildings(TileJson tj, string tileDir, string assetDir, GameObject tileGo,
                                                                  ProceduralTextures.BuildingMaterials mats, FacadeUvAccumulator uvCheck)
        {
            var root = InstantiateGlb(assetDir + "/buildings.glb", "Buildings", tileGo);
            if (root == null) return (0, 0, 0);
            var metaText = File.ReadAllText(Path.Combine(tileDir, tj.buildings.meta));
            var list = JsonUtility.FromJson<BuildingMetaList>("{\"items\":" + metaText + "}").items ?? new BuildingMeta[0];
            var byNode = list.ToDictionary(b => b.node, b => b);
            var nodes = new HashSet<Transform>();
            int withInfo = 0;
            foreach (var mf in root.GetComponentsInChildren<MeshFilter>(true))
            {
                var go = mf.gameObject;
                var (nodeT, meta) = FindMeta(go.transform, byNode);
                nodeT ??= go.transform;
                nodes.Add(nodeT);
                string cls = meta != null && mats.wall.ContainsKey(meta.usage_class ?? "") ? meta.usage_class : "other";
                string key = meta != null && !string.IsNullOrEmpty(meta.id) ? meta.id : go.name;   // 틴트 변형 해시 키(RefreshLook 과 같다)
                var mesh = mf.sharedMesh;
                var mr = go.GetComponent<MeshRenderer>();
                if (mr != null && mesh != null) mr.sharedMaterials = PickBuildingMaterials(mesh, mr, cls, key, mats);
                if (mesh != null) uvCheck.Add(mesh, meta, nodeT.name, cls);
                var mc = go.AddComponent<MeshCollider>();
                mc.sharedMesh = mesh;
                go.isStatic = true;
                if (meta == null || nodeT.GetComponent<BuildingInfo>() != null) continue;
                var bi = nodeT.gameObject.AddComponent<BuildingInfo>();   // 보통 충돌체와 같은 오브젝트(클릭 조회)
                bi.id = meta.id; bi.sourceId = meta.src_id; bi.usageClass = meta.usage_class; bi.usageKo = meta.usage_ko;
                bi.usageName = meta.usage_name; bi.buildingName = meta.name; bi.floors = meta.floors;
                bi.floorsSource = meta.floors_source; bi.heightM = meta.height_m; bi.baseM = meta.base_m;
                bi.address = meta.address; bi.areaM2 = meta.area_m2;
                withInfo++;
            }
            if (nodes.Count != list.Length) warnings.Add($"{tj.name}: glb 건물 노드 {nodes.Count} ≠ 메타 {list.Length}");
            return (nodes.Count, withInfo, list.Length);
        }

        /// <summary>
        /// 계약 C1: submesh 2개 = [벽, 지붕] → [&lt;cls&gt;.mat, roof_&lt;cls&gt;.mat]. 1개(v1) = 벽 재질만.
        /// UV(TEXCOORD_0)가 없는 v1 메시는 텍스처 한 점만 찍히므로 같은 색의 단색 재질(flat_&lt;cls&gt;.mat)을 쓴다.
        /// glTFast 가 정점 형식이 다른 프리미티브를 자식 메시로 나눈 경우에는 glb 재질 이름 M_roof 로 지붕을 알아본다.
        /// 벽·지붕은 테마 틴트 변형 중 건물 키(id) 해시로 고른다(D-014, 매번 같다).
        /// </summary>
        static Material[] PickBuildingMaterials(Mesh mesh, MeshRenderer mr, string cls, string key, ProceduralTextures.BuildingMaterials mats)
        {
            bool hasUv = mesh.HasVertexAttribute(VertexAttribute.TexCoord0);
            int sub = mesh.subMeshCount;
            var wall = mats.For(ProceduralTextures.Slot.Wall, cls, key);
            var roof = mats.For(ProceduralTextures.Slot.Roof, cls, key);
            if (!hasUv) return Enumerable.Repeat(mats.flat[cls], Mathf.Max(1, sub)).ToArray();
            if (sub >= 2)
            {
                var arr = new Material[sub];
                for (int i = 0; i < sub; i++) arr[i] = i == 1 ? roof : wall;
                return arr;
            }
            bool isRoof = mr.sharedMaterial != null && mr.sharedMaterial.name.StartsWith("M_roof", StringComparison.Ordinal);
            return new[] { isRoof ? roof : wall };
        }

        static (Transform node, BuildingMeta meta) FindMeta(Transform t, Dictionary<string, BuildingMeta> byNode)
        {
            for (var cur = t; cur != null; cur = cur.parent)
                if (byNode.TryGetValue(cur.name, out var meta)) return (cur, meta);
            // 노드가 하나뿐인 glb 는 노드 이름이 에셋 이름으로 바뀐다 → 메시 이름(= 노드 이름)으로 찾고 이름을 되돌린다
            var mesh = t.GetComponent<MeshFilter>()?.sharedMesh;
            if (mesh != null && byNode.TryGetValue(mesh.name, out var m2)) { t.name = mesh.name; return (t, m2); }
            return (null, null);
        }

        /// <summary>
        /// 계약 C1 확인: 벽 submesh(0)의 uv.y 범위가 건물마다 [0, height_m ÷ floor_height_m(용도별)] 인지.
        /// floor_height_m 은 manifest.facade(없으면 계약 기본값)에서, 용도는 재질 배정과 같은 cls(모르는 용도는 other)를 쓴다.
        /// </summary>
        class FacadeUvAccumulator
        {
            readonly FacadeUvCheck r = new FacadeUvCheck();
            readonly List<FacadeUvSample> bad = new();
            readonly List<Vector2> uvs = new();
            readonly FacadeSpec facade;

            public FacadeUvAccumulator(FacadeSpec facade) => this.facade = facade;

            public void Add(Mesh mesh, BuildingMeta meta, string node, string cls)
            {
                if (!mesh.HasVertexAttribute(VertexAttribute.TexCoord0)) { r.no_uv++; return; }
                if (mesh.subMeshCount < 2) { r.single_submesh++; return; }
                r.two_submesh++;
                if (meta == null) return;
                mesh.GetUVs(0, uvs);
                var idx = mesh.GetIndices(0);
                if (idx.Length == 0 || uvs.Count == 0) return;
                float vmin = float.MaxValue, vmax = float.MinValue;
                foreach (int i in idx)
                {
                    float v = uvs[i].y;
                    if (v < vmin) vmin = v;
                    if (v > vmax) vmax = v;
                }
                r.@checked++;
                float expected = meta.height_m / ProceduralTextures.FloorH(facade, cls);
                float emin = Mathf.Abs(vmin), emax = Mathf.Abs(vmax - expected);
                r.max_err_min = Mathf.Max(r.max_err_min, emin);
                r.max_err_max = Mathf.Max(r.max_err_max, emax);
                if (emin <= r.tolerance && emax <= r.tolerance) r.ok++;
                else
                {
                    r.bad++;
                    if (bad.Count < 20)
                        bad.Add(new FacadeUvSample { node = node, usage_class = cls, floors = meta.floors, height_m = meta.height_m, expected_v_max = expected, v_min = vmin, v_max = vmax });
                }
            }

            public FacadeUvCheck Finish()
            {
                r.status = r.two_submesh == 0 ? "SKIPPED" : r.bad == 0 && r.@checked > 0 ? "PASS" : "FAIL";
                r.bad_samples = bad.ToArray();
                return r;
            }
        }

        /// <summary>
        /// tile.json → TileJson. 중첩 배열(footprint_xz)은 모델에 없으므로 JsonUtility 가 건너뛴다.
        /// 혹시 JsonUtility 가 실패하면 footprint_xz 배열을 비운 사본으로 한 번 더 읽는다(footprint 는 MiniJson 으로 따로 읽는다).
        /// </summary>
        static TileJson ParseTileJson(string text, string tileName)
        {
            try { return JsonUtility.FromJson<TileJson>(text); }
            catch (Exception e)
            {
                warnings.Add($"{tileName}: tile.json JsonUtility 실패({e.Message}) → footprint_xz 를 빼고 다시 읽음");
                return JsonUtility.FromJson<TileJson>(StripArrays(text, "\"footprint_xz\""));
            }
        }

        static string StripArrays(string text, string key)
        {
            var sb = new System.Text.StringBuilder(text.Length);
            int i = 0;
            while (true)
            {
                int k = text.IndexOf(key, i, StringComparison.Ordinal);
                if (k < 0) { sb.Append(text, i, text.Length - i); break; }
                int open = text.IndexOf('[', k);
                if (open < 0) { sb.Append(text, i, text.Length - i); break; }
                int depth = 0, j = open;
                for (; j < text.Length; j++)
                {
                    if (text[j] == '[') depth++;
                    else if (text[j] == ']' && --depth == 0) break;
                }
                sb.Append(text, i, open - i).Append("[]");
                i = Math.Min(j + 1, text.Length);
            }
            return sb.ToString();
        }

        // ------------------------------------------------------------------ 교량(C3)
        static void ReadBridgeItems(PendingTile p)
        {
            object root;
            try { root = MiniJson.Parse(p.tileJsonText); }
            catch (Exception e) { warnings.Add($"{p.t.name}: tile.json 파싱 실패({e.Message}) → 교량 footprint 없음"); return; }
            var items = MiniJson.Arr(MiniJson.Obj(root, "bridges"), "items");
            if (items == null) return;
            for (int i = 0; i < items.Count; i++)
            {
                var it = items[i];
                string node = MiniJson.Str(it, "node");
                if (string.IsNullOrEmpty(node)) { warnings.Add($"{p.t.name}: bridges.items[{i}] node 없음"); continue; }
                // JsonUtility 대신 MiniJson 으로 읽는다(osm_id 가 숫자로 와도 문자열로 받게)
                var item = new BridgeItem
                {
                    node = node, osm_id = MiniJson.Str(it, "osm_id"), kind = MiniJson.Str(it, "kind"), name = MiniJson.Str(it, "name"),
                    length_m = (float)MiniJson.Num(it, "length_m"), width_m = (float)MiniJson.Num(it, "width_m"),
                    deck_min_m = (float)MiniJson.Num(it, "deck_min_m"), deck_max_m = (float)MiniJson.Num(it, "deck_max_m"),
                    deck_thickness_m = (float)MiniJson.Num(it, "deck_thickness_m"),
                };
                if (p.bridges.ContainsKey(node)) warnings.Add($"{p.t.name}: 교량 노드 {node} 중복");
                p.bridges[node] = (item, MiniJson.Points(it, "footprint_xz"));
            }
        }

        static int BuildBridges(PendingTile p, Dictionary<string, Material> mats)
        {
            var root = InstantiateGlb(p.assetDir + "/bridges.glb", "Bridges", p.go);
            if (root == null) return 0;
            var nodes = new HashSet<Transform>();
            foreach (var mf in root.GetComponentsInChildren<MeshFilter>(true))
            {
                var nodeT = FindNode(mf, "BR_") ?? mf.transform;
                p.bridges.TryGetValue(nodeT.name, out var rec);
                var mr = mf.GetComponent<MeshRenderer>();
                string kind = rec.item?.kind;
                if (string.IsNullOrEmpty(kind) && mr != null && mr.sharedMaterial != null && mr.sharedMaterial.name.StartsWith("M_bridge_", StringComparison.Ordinal))
                    kind = mr.sharedMaterial.name.Substring("M_bridge_".Length).Split(' ')[0];
                if (string.IsNullOrEmpty(kind) || !mats.ContainsKey(kind)) kind = "road";
                if (mr != null) { mr.sharedMaterial = mats[kind]; mr.shadowCastingMode = ShadowCastingMode.On; }
                mf.gameObject.layer = 0;   // 걷기·착지·클릭 레이캐스트 모두 맞아야 한다
                mf.gameObject.AddComponent<MeshCollider>().sharedMesh = mf.sharedMesh;   // 충돌체는 glb 원본 메시
                if (mf.sharedMesh != null) mf.sharedMesh = EnsureBridgeUvMesh(mf.sharedMesh, p.assetDir);   // 보이는 메시만 UV 사본(D-014)
                mf.gameObject.isStatic = true;
                if (!nodes.Add(nodeT)) continue;
                if (rec.item == null) warnings.Add($"{p.t.name}: 교량 노드 {nodeT.name} 이 tile.json bridges.items 에 없음");
                var bi = nodeT.gameObject.AddComponent<BridgeInfo>();
                bi.node = nodeT.name;
                bi.kind = kind;
                if (rec.item != null)
                {
                    bi.osmId = rec.item.osm_id; bi.bridgeName = rec.item.name; bi.lengthM = rec.item.length_m; bi.widthM = rec.item.width_m;
                    bi.deckMinM = rec.item.deck_min_m; bi.deckMaxM = rec.item.deck_max_m;
                }
                else bi.osmId = nodeT.name.StartsWith("BR_", StringComparison.Ordinal) ? nodeT.name.Substring(3) : nodeT.name;
                bi.interim = p.tj.bridges.interim;
            }
            return nodes.Count;
        }

        static Transform FindPrefixed(Transform t, string prefix)
        {
            for (var cur = t; cur != null; cur = cur.parent)
                if (cur.name.StartsWith(prefix, StringComparison.Ordinal)) return cur;
            return null;
        }

        /// <summary>
        /// 접두사로 노드를 찾는다. 못 찾으면 메시 이름(파이프라인은 메시 이름 = 노드 이름으로 쓴다)을 보고,
        /// 맞으면 그 오브젝트 이름을 노드 이름으로 되돌린다(노드가 하나뿐인 glb 는 glTFast 가 루트 이름을 에셋 이름으로 바꾼다).
        /// </summary>
        static Transform FindNode(MeshFilter mf, string prefix)
        {
            var t = FindPrefixed(mf.transform, prefix);
            if (t != null) return t;
            var mesh = mf.sharedMesh;
            if (mesh == null || !mesh.name.StartsWith(prefix, StringComparison.Ordinal)) return null;
            mf.gameObject.name = mesh.name;
            return mf.transform;
        }

        // ------------------------------------------------------------------ 수면
        static void BuildWater(string assetDir, GameObject tileGo, Material waterMat, List<ShoreRules.Footprint> footprints, ImportReport report)
        {
            var root = InstantiateGlb(assetDir + "/water.glb", "Water", tileGo);
            if (root == null) return;
            var walls = new List<Mesh>();
            foreach (var mf in root.GetComponentsInChildren<MeshFilter>(true))
            {
                var mr = mf.GetComponent<MeshRenderer>();
                if (mr != null) { mr.sharedMaterial = waterMat; mr.shadowCastingMode = ShadowCastingMode.Off; }
                mf.gameObject.isStatic = true;
                // 수면은 Water 레이어(4) 충돌체: 플레이어는 통과하고(WalkController 가 충돌 무시),
                // 비행 고도 레이캐스트와 '물 위 착지 금지' 판정에만 쓰인다
                mf.gameObject.layer = WaterLayer;
                mf.gameObject.AddComponent<MeshCollider>().sharedMesh = mf.sharedMesh;
                var wall = ShoreWall(mf.sharedMesh, mf.transform, root.transform, footprints, report);
                if (wall != null) walls.Add(wall);
            }
            if (walls.Count == 0) return;
            var combined = new Mesh { indexFormat = IndexFormat.UInt32, name = "ShoreWall" };
            combined.CombineMeshes(walls.Select(w => new CombineInstance { mesh = w, transform = Matrix4x4.identity }).ToArray(), true, true);
            ReplaceAsset(combined, assetDir + "/shorewall.asset");
            var wgo = new GameObject("ShoreWall");
            wgo.layer = IgnoreRaycastLayer;   // 걷기는 막고, 비행 고도·건물 클릭 레이캐스트에는 걸리지 않게
            wgo.transform.SetParent(root.transform, false);
            wgo.AddComponent<MeshCollider>().sharedMesh = combined;   // 보이지 않는 경계(수면 진입 차단)
            wgo.isStatic = true;
        }

        /// <summary>
        /// 수면 메시의 바깥 테두리(삼각형 하나에만 속한 변)를 따라 수면−1m ~ +4m 의 벽을 만든다.
        /// 타일 경계에서 잘린 변은 만들지 않는다.
        /// 계약 C3(2차-라): 중점이 교량 상판 외곽 + 여유(water.shore_overlap_m + 0.5 m) 안인 변 중 상판 밑·끝 구역 변은 윗면을 상판 밑면 − 0.1 m 로 낮추고 옆면 변은 기본 높이로 둔다
        /// (다리 위는 지나가고, 물가를 걷다 다리 밑으로 하상에 들어가는 길은 막는다). 낮춘 윗면이 수면 + 0.3 m 이하이면 그 변만 뺀다.
        /// 밑면은 교량 충돌체로 위쪽 레이캐스트해서 구하고, 실패하면 '위에서 찾은 상판 윗면 − deck_thickness_m' 를 쓴다.
        /// 둘 다 실패하면 기본 높이로 두고 unresolved 로 센다(새는 것보다 막는 쪽).
        /// </summary>
        static Mesh ShoreWall(Mesh mesh, Transform mt, Transform root, List<ShoreRules.Footprint> footprints, ImportReport report)
        {
            var v = mesh.vertices;
            var tris = mesh.triangles;
            var toRoot = root.worldToLocalMatrix * mt.localToWorldMatrix;
            var rootToWorld = root.localToWorldMatrix;
            float rootY = rootToWorld.MultiplyPoint3x4(Vector3.zero).y;   // 루트는 회전·축척 없이 평행이동만 한다
            Vector3 P(int i) => toRoot.MultiplyPoint3x4(v[i]);
            (long, long) Key(Vector3 p) => (Mathf.RoundToInt(p.x * 20f), Mathf.RoundToInt(p.z * 20f));
            var count = new Dictionary<((long, long), (long, long)), (Vector3 a, Vector3 b, int n)>();
            for (int i = 0; i < tris.Length; i += 3)
                for (int k = 0; k < 3; k++)
                {
                    var a = P(tris[i + k]); var b = P(tris[i + (k + 1) % 3]);
                    var ka = Key(a); var kb = Key(b);
                    var key = ka.CompareTo(kb) < 0 ? (ka, kb) : (kb, ka);
                    count[key] = count.TryGetValue(key, out var c) ? (c.a, c.b, c.n + 1) : (a, b, 1);
                }
            var verts = new List<Vector3>();
            var idx = new List<int>();
            foreach (var e in count.Values)
            {
                if (e.n != 1) continue;
                // 타일 경계에서 잘린 변(강 한가운데)은 물가가 아니므로 벽을 세우지 않는다
                if (ShoreRules.IsTileEdgeSegment(e.a, e.b)) continue;
                report.shore_edges_total++;
                float topA = e.a.y + ShoreRules.WallAboveWater, topB = e.b.y + ShoreRules.WallAboveWater;
                var aW = rootToWorld.MultiplyPoint3x4(e.a);
                var bW = rootToWorld.MultiplyPoint3x4(e.b);
                var mid = (aW + bW) * 0.5f;
                var zone = ShoreRules.Zone(new Vector2(aW.x, aW.z), new Vector2(bW.x, bW.z), footprints, bridgeMargin, out var zfoot, out var end);
                if (zone != ShoreRules.BridgeZone.None)
                {
                    report.shore_edges_bridge_near++;
                    // 1) 변이 상판 밑에 있으면 그 자리 밑면  2) 상판 밖이면 끝 구역(선분 교차)만 그 끝의 밑면  3) 옆면 바깥은 기본 높이
                    float under = UndersideOverEdge(aW, bW, zfoot, out bool usedThickness);
                    if (float.IsNaN(under))
                    {
                        if (zone == ShoreRules.BridgeZone.End)
                        {
                            report.shore_edges_bridge_end++;
                            under = UndersideAtEnd(end, zfoot, out usedThickness);
                            if (float.IsNaN(under)) report.shore_edges_bridge_unresolved++;
                        }
                        else report.shore_edges_bridge_side_kept++;
                    }
                    if (!float.IsNaN(under))
                    {
                        if (usedThickness) report.shore_edges_bridge_thickness_fallback++;
                        var kind = ShoreRules.BridgeWallTop(aW.y, bW.y, under, out float tA, out float tB);
                        if (kind == ShoreRules.BridgeEdge.Removed) { report.shore_edges_bridge_removed++; continue; }
                        if (kind == ShoreRules.BridgeEdge.Lowered) report.shore_edges_bridge_lowered++;
                        else report.shore_edges_bridge_high++;
                        topA = tA - rootY;
                        topB = tB - rootY;
                    }
                }
                int s = verts.Count;
                verts.Add(e.a + Vector3.down); verts.Add(e.b + Vector3.down);
                verts.Add(new Vector3(e.b.x, topB, e.b.z)); verts.Add(new Vector3(e.a.x, topA, e.a.z));
                idx.AddRange(new[] { s, s + 1, s + 2, s, s + 2, s + 3, s, s + 2, s + 1, s, s + 3, s + 2 });
            }
            if (verts.Count == 0) return null;
            var wall = new Mesh { indexFormat = IndexFormat.UInt32 };
            wall.SetVertices(verts);
            wall.SetTriangles(idx, 0);
            return wall;
        }

        /// <summary>
        /// 물가 벽 변(월드 aW–bW) 바로 위 상판 밑면 높이. 변 위 5점에서 위로 쏜 레이가 맞힌 교량 면 중 가장 낮은 값
        /// (변 전체에서 상판을 뚫지 않게 최소). 위쪽 레이가 빗나가도 아래로 쏴서 상판이 그 위에 있으면 '윗면 − deck_thickness_m'.
        /// 변 위에 상판이 없으면 NaN(→ 끝 구역/옆면 판정으로 넘어간다).
        /// </summary>
        static float UndersideOverEdge(Vector3 aW, Vector3 bW, ShoreRules.Footprint fp, out bool usedThickness)
        {
            usedThickness = false;
            float originY = Mathf.Min(aW.y, bW.y) - 2f;   // 벽 아래끝(수면 − 1 m)보다 아래에서 쏜다
            var samples = new Vector2[5];
            for (int k = 0; k <= 4; k++) { var p = Vector3.Lerp(aW, bW, k / 4f); samples[k] = new Vector2(p.x, p.z); }
            float best = float.MaxValue;
            foreach (var q in samples) best = Mathf.Min(best, UpwardBridgeHit(q, originY));
            if (best < float.MaxValue) return best;
            float top = float.MaxValue;
            foreach (var q in samples) top = Mathf.Min(top, DownwardBridgeTop(q));
            if (top == float.MaxValue) return float.NaN;
            usedThickness = true;
            return top - (fp.thickness > 0f ? fp.thickness : ShoreRules.DefaultDeckThickness);
        }

        /// <summary>
        /// 끝 구역 변: 그 끝 단면 바로 안쪽(끝점에서 안으로 0.3 m, 단면 폭 방향 3점)의 상판 밑면 최저값.
        /// 위쪽 레이가 빗나가면 '윗면 − deck_thickness_m'. 둘 다 없으면 NaN.
        /// </summary>
        static float UndersideAtEnd(ShoreRules.BridgeEnd end, ShoreRules.Footprint fp, out bool usedThickness)
        {
            usedThickness = false;
            Vector2 inPt = end.pos - end.outDir * 0.3f, perp = new Vector2(-end.outDir.y, end.outDir.x);
            float half = 0.5f * (fp.width > 0f ? fp.width : end.capWidth) * 0.6f;
            var samples = new[] { inPt, inPt + perp * half, inPt - perp * half };
            float top = float.MaxValue;
            foreach (var q in samples) top = Mathf.Min(top, DownwardBridgeTop(q));
            if (top == float.MaxValue) return float.NaN;
            float best = float.MaxValue;
            foreach (var q in samples) best = Mathf.Min(best, UpwardBridgeHit(q, top - 50f));
            if (best < float.MaxValue && best < top) return best;
            usedThickness = true;
            return top - (fp.thickness > 0f ? fp.thickness : ShoreRules.DefaultDeckThickness);
        }

        /// <summary>
        /// 수면 메시 확장 폭(water.shore_overlap_m). manifest 의 water.shore_overlap_m 키가 있으면(값이 0 이어도) 그 값,
        /// 없을 때만 config/project.yaml water 블록, 그것도 없으면 기본 3.0. 출처는 unity_import.json bridge_margin_source.
        /// 계약 C3 여유 = 이 값 + 0.5.
        /// </summary>
        internal static float ShoreOverlap(AreaManifest m, string manifestText, out string source)
        {
            try
            {
                var w = MiniJson.Obj(MiniJson.Parse(manifestText), "water");
                if (w != null && w.TryGetValue("shore_overlap_m", out var v) && v is double d)
                { source = "manifest.water.shore_overlap_m"; return (float)d; }
            }
            catch (Exception e) { warnings?.Add($"manifest water 읽기 실패({e.Message}) → config 로 돌아감"); }
            string cfg = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "..", "config", "project.yaml"));
            try
            {
                if (File.Exists(cfg) && TryReadYamlFloat(File.ReadAllLines(cfg), "water", "shore_overlap_m", out var yv))
                { source = "config/project.yaml water.shore_overlap_m(manifest 에 없음)"; return yv; }
            }
            catch (Exception e) { warnings?.Add($"config/project.yaml 읽기 실패({e.Message}) → shore_overlap_m 기본값"); }
            source = "기본값 3.0(manifest·config 에 없음)";
            return ShoreRules.BridgeMargin - ShoreRules.BridgeMarginExtra;
        }

        /// <summary>최상위 블록 block 아래 들여쓴 key: 숫자 를 읽는다(주석·다른 블록 무시). 테스트용으로 internal.</summary>
        internal static bool TryReadYamlFloat(IEnumerable<string> lines, string block, string key, out float value)
        {
            value = 0f;
            bool inBlock = false;
            var rx = new System.Text.RegularExpressions.Regex(@"^\s+" + System.Text.RegularExpressions.Regex.Escape(key) + @":\s*(-?[0-9.]+)");
            foreach (var line in lines)
            {
                if (line.Length > 0 && !char.IsWhiteSpace(line[0]) && !line.StartsWith("#", StringComparison.Ordinal))
                    inBlock = line.TrimEnd() == block + ":";
                else if (inBlock)
                {
                    var mt = rx.Match(line);
                    if (mt.Success && float.TryParse(mt.Groups[1].Value, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out value))
                        return true;
                }
            }
            return false;
        }

        // ------------------------------------------------------------------ 교량 끝 진입 검사(2차-라, 3차-가 보강)
        /// <summary>Player CharacterController.stepOffset. 진입 검사의 높이 단 한계와 같다.</summary>
        public const float PlayerStepOffset = 0.5f;
        const float AccessOutside = 5f, AccessInside = 1f, AccessRadius = 0.3f, AccessLow = 0.5f, AccessHigh = 1.5f, AccessStep = 0.25f;
        const int ShoreWallLayerMask = 1 << IgnoreRaycastLayer;

        /// <summary>
        /// 교량 끝마다 '육지 5 m 밖 → 상판 1 m 안' 선분을 0.25 m 간격으로 따라간다. 접근 방향은 끝 축과 ±30° 셋이고,
        /// 하나라도 바깥 점이 육지면 육지 끝, 그중 하나라도 막힘 없이 통과하면 통과다.
        ///  - 분류(검사에서 뺌): 끝점이 다른 footprint 1 m 안이거나 바깥 점이 다른 상판 위 → joint(연결부),
        ///    바깥 점이 건물 위 → building(지붕, 육지 아님), 바깥 점이 물 위(LandingAllowed 거짓) → water.
        ///  - 막힘: 지면 위 0.5~1.5 m 반지름 0.3 m 캡슐이 물가 벽(레이어 2)과 겹치거나 부딪히면 wall,
        ///    이웃 표본의 지면 높이 차가 stepOffset(0.5 m)을 넘으면 step.
        /// </summary>
        internal static BridgeAccess CheckBridgeAccess(IReadOnlyList<ShoreRules.Footprint> footprints, float margin, double oE = 0, double oN = 0,
                                                       float landingTol = WalkController.DefaultBuriedWaterTolerance)
        {
            Physics.SyncTransforms();
            var r = new BridgeAccess { items = footprints.Count, step_limit_m = PlayerStepOffset };
            var blocked = new List<BridgeAccessEnd>();
            var water = new List<string>();
            var joint = new List<string>();
            var building = new List<string>();
            var unknown = new List<string>();
            foreach (var f in footprints)
            {
                if (f.ends.Length != 2) { unknown.Add(f.node); r.ends_unknown += 2; r.ends_total += 2; continue; }
                foreach (var e in f.ends)
                {
                    r.ends_total++;
                    string id = $"{f.node}:{e.which}";
                    if (ShoreRules.AttachedTo(e, f, footprints) != null) { joint.Add(id); r.ends_joint++; continue; }
                    Vector2 inner = e.pos - e.outDir * AccessInside;
                    // 접근 방향: 끝 축(0°)과 ±30°. 축 앞이 건물·물이어도 비스듬히 육지에서 오를 수 있으면 육지 끝이다(3차-나).
                    // ±30° 는 끝 단면을 걷는 폭 안(중심에서 약 0.5 m)으로 지나므로 난간을 넘지 않는다.
                    string axialClass = null;
                    bool anyLand = false, passed = false;
                    Vector3 hit = default; string kind = null; float step = 0f;
                    foreach (float ang in AccessAngles)
                    {
                        Vector2 dir = Rotate(e.outDir, ang);
                        Vector2 outer = e.pos + dir * AccessOutside;
                        string cls = ClassifyOuter(outer, landingTol);
                        if (ang == 0f) axialClass = cls;
                        if (cls != "land") continue;
                        anyLand = true;
                        if (!SweepAccess(outer, inner, out var h, out var k, out var st)) { passed = true; break; }
                        if (kind == null || ang == 0f) { hit = h; kind = k; step = st; }   // 막힘 기록은 축 방향을 우선
                    }
                    if (!anyLand)
                    {
                        switch (axialClass)
                        {
                            case "joint": joint.Add(id); r.ends_joint++; break;
                            case "building": building.Add(id); r.ends_building++; break;
                            case "water": water.Add(id); r.ends_water++; break;
                            default: unknown.Add(id); r.ends_unknown++; break;
                        }
                        continue;
                    }
                    r.ends_land++;
                    if (!passed)
                    {
                        var h2 = new Vector2(hit.x, hit.z);
                        float d = ShoreRules.PointInPolygon(h2, f.pts) ? 0f : ShoreRules.DistanceToRing(h2, f.pts);
                        string cause = kind == "step"
                            ? $"step: 높이 단 {step:+0.00;-0.00} m(한계 {PlayerStepOffset:0.0} m), 상판 외곽에서 {d:0.0} m"
                            : d > margin + 0.5f ? $"data: 벽이 상판에서 {d:0.0} m(끝 구역 {margin + 0.5f:0.0} m 밖, 수면 메시가 더 넓다)"
                                                : $"rule: 끝 구역 안 벽({d:0.0} m, 옆면으로 분류 또는 높은 상판)";
                        blocked.Add(new BridgeAccessEnd
                        {
                            node = f.node, which = e.which,
                            e = (float)(e.pos.x + oE), n = (float)(e.pos.y + oN),
                            hit_e = (float)(hit.x + oE), hit_n = (float)(hit.z + oN), hit_y = hit.y,
                            hit_dist_to_deck_m = d, kind = kind, step_m = step, cause = cause,
                        });
                        r.ends_blocked++;
                        if (kind == "step") r.ends_blocked_step++; else r.ends_blocked_wall++;
                    }
                    else r.ends_passed++;
                }
            }
            r.blocked = blocked.ToArray();
            r.water_ends = water.ToArray();
            r.joint_ends = joint.ToArray();
            r.building_ends = building.ToArray();
            r.unknown_nodes = unknown.ToArray();
            return r;
        }

        static readonly float[] AccessAngles = { 0f, 30f, -30f };

        static Vector2 Rotate(Vector2 v, float deg)
        {
            float r = deg * Mathf.Deg2Rad, c = Mathf.Cos(r), s = Mathf.Sin(r);
            return new Vector2(c * v.x - s * v.y, s * v.x + c * v.y);
        }

        /// <summary>바깥 점의 분류: joint(다른 상판 위) / building(건물 위) / water(물 위) / land / none(충돌체 없음).</summary>
        static string ClassifyOuter(Vector2 outer, float landingTol)
        {
            if (!GroundTop(outer, out var g)) return "none";
            if (g.collider.GetComponentInParent<BridgeInfo>() != null) return "joint";
            if (g.collider.GetComponentInParent<BuildingInfo>() != null) return "building";
            if (!WalkController.LandingAllowed(new Vector3(outer.x, g.point.y + 5f, outer.y), null, landingTol)) return "water";
            return "land";
        }

        static double originE, originN;   // CheckBridgeAccess 보고용(Import 에서 채운다)

        /// <summary>(x, z) 위 최상단 지면(수면 4·Ignore Raycast 2 제외, 교량·건물 포함).</summary>
        static bool GroundTop(Vector2 xz, out RaycastHit hit) =>
            Physics.Raycast(new Vector3(xz.x, 5000f, xz.y), Vector3.down, out hit, 10000f, Physics.DefaultRaycastLayers & ~(1 << WaterLayer), QueryTriggerInteraction.Ignore);

        /// <summary>
        /// outer → inner 를 따라가며 막히는 첫 지점. kind = "wall"(물가 벽) 또는 "step"(이웃 표본 지면 차 > stepOffset).
        /// step = 단 높이(+ = inner 쪽으로 오르막).
        /// </summary>
        static bool SweepAccess(Vector2 outer, Vector2 inner, out Vector3 hitPoint, out string kind, out float step)
        {
            hitPoint = default; kind = null; step = 0f;
            int n = Mathf.Max(1, Mathf.CeilToInt(Vector2.Distance(outer, inner) / AccessStep));
            Vector3 prev = default;
            float lastY = 0f;
            for (int i = 0; i <= n; i++)
            {
                var q = Vector2.Lerp(outer, inner, i / (float)n);
                float y = GroundTop(q, out var g) ? g.point.y : lastY;
                var cur = new Vector3(q.x, y, q.y);
                if (i > 0 && ShoreRules.IsStepTooHigh(lastY, y, PlayerStepOffset))
                { hitPoint = cur; kind = "step"; step = y - lastY; return true; }
                lastY = y;
                Vector3 c1 = cur + Vector3.up * (AccessLow + AccessRadius), c2 = cur + Vector3.up * (AccessHigh - AccessRadius);
                if (Physics.OverlapCapsule(c1, c2, AccessRadius, ShoreWallLayerMask, QueryTriggerInteraction.Ignore).Length > 0)
                { hitPoint = cur + Vector3.up * AccessLow; kind = "wall"; return true; }
                if (i > 0)
                {
                    var d = cur - prev;
                    if (d.sqrMagnitude > 1e-6f && Physics.CapsuleCast(prev + Vector3.up * (AccessLow + AccessRadius), prev + Vector3.up * (AccessHigh - AccessRadius),
                            AccessRadius, d.normalized, out var h, d.magnitude, ShoreWallLayerMask, QueryTriggerInteraction.Ignore))
                    { hitPoint = h.point; kind = "wall"; return true; }
                }
                prev = cur;
            }
            return false;
        }

        /// <summary>(x, originY, z)에서 위로 쏜 레이가 맞힌 교량 면 중 가장 낮은 높이. 없으면 float.MaxValue.</summary>
        static float UpwardBridgeHit(Vector2 xz, float originY)
        {
            float best = float.MaxValue;
            foreach (var h in Physics.RaycastAll(new Vector3(xz.x, originY, xz.y), Vector3.up, 1000f, Physics.AllLayers, QueryTriggerInteraction.Ignore))
                if (h.point.y < best && h.collider.GetComponentInParent<BridgeInfo>() != null) best = h.point.y;
            return best;
        }

        /// <summary>위에서 아래로 쏜 레이가 맞힌 교량 면 중 가장 낮은 높이(= 상판 윗면, 난간 윗면은 더 높다). 없으면 float.MaxValue.</summary>
        static float DownwardBridgeTop(Vector2 xz)
        {
            float best = float.MaxValue;
            foreach (var h in Physics.RaycastAll(new Vector3(xz.x, 5000f, xz.y), Vector3.down, 10000f, Physics.AllLayers, QueryTriggerInteraction.Ignore))
                if (h.point.y < best && h.collider.GetComponentInParent<BridgeInfo>() != null) best = h.point.y;
            return best;
        }

        // ------------------------------------------------------------------ 원경 스커트(C4)
        static void BuildSkirt(AreaManifest m, string area, GameObject world, Material waterMat, Dictionary<(int, int), Terrain> terrains, ImportReport report)
        {
            var root = InstantiateGlb($"{TileAssetRoot}/{area}/skirt.glb", "Skirt", world);
            if (root == null) return;
            root.transform.localPosition = Vector3.zero;   // 좌표가 이미 Unity 월드 좌표(타일 로컬이 아님)
            root.isStatic = true;
            foreach (var mf in root.GetComponentsInChildren<MeshFilter>(true))
            {
                mf.gameObject.isStatic = true;
                foreach (var col in mf.GetComponents<Collider>()) UnityEngine.Object.DestroyImmediate(col);   // 충돌체 없음
            }
            ApplySkirtLook(m, area, root, waterMat, terrains.Values, report);
        }

        /// <summary>원경 정점색 밝기 변화를 얼마나 남길지(0 = 범위 안 지면 단색, 1 = 원래 밝기 비 그대로).</summary>
        public const float SkirtVariation = 0.35f;
        const string RecolorSuffix = "_recolor";

        /// <summary>
        /// 원경 재질·색. SKW_* = Water.mat. SK_* 는 COLOR_0 이 있으면 정점색을 범위 안 지면 색조로 다시 칠한 메시 사본
        /// (Assets/World/Tiles/&lt;area&gt;/skirt_&lt;node&gt;_recolor.asset)과 SkirtVertexColor.mat, 없으면 같은 색의 Skirt.mat.
        /// 임포트와 RefreshLook 이 같이 쓴다. 원본 색은 언제나 skirt.glb 의 메시에서 읽으므로 여러 번 돌려도 누적되지 않는다.
        /// </summary>
        static void ApplySkirtLook(AreaManifest m, string area, GameObject root, Material waterMat, IEnumerable<Terrain> terrains, ImportReport report)
        {
            string glbPath = $"{TileAssetRoot}/{area}/skirt.glb";
            var originals = AssetDatabase.LoadAllAssetsAtPath(glbPath).OfType<Mesh>().GroupBy(x => x.name).ToDictionary(g => g.Key, g => g.First());
            var (ground, coverage) = GroundAlbedo(terrains);
            report.ground_albedo_linear = new[] { ground.r, ground.g, ground.b };
            report.ground_layer_coverage = coverage;
            report.skirt_recolor_variation = SkirtVariation;

            Material vertexColor = null, flat = null;
            var sk = new HashSet<Transform>();
            var skw = new HashSet<Transform>();
            var colored = new List<(MeshFilter mf, Mesh src)>();
            foreach (var mf in root.GetComponentsInChildren<MeshFilter>(true))
            {
                var mr = mf.GetComponent<MeshRenderer>();
                if (mr == null) continue;
                var waterNode = FindNode(mf, "SKW_");
                if (waterNode != null)
                {
                    skw.Add(waterNode);
                    mr.sharedMaterial = waterMat;
                    mr.shadowCastingMode = ShadowCastingMode.Off;
                    continue;
                }
                sk.Add(FindNode(mf, "SK_") ?? mf.transform);
                var cur = mf.sharedMesh;
                string baseName = cur == null ? null
                    : cur.name.EndsWith(RecolorSuffix, StringComparison.Ordinal) ? cur.name.Substring(0, cur.name.Length - RecolorSuffix.Length) : cur.name;
                var src = baseName != null && originals.TryGetValue(baseName, out var o) ? o : cur;
                bool hasColor = src != null && src.HasVertexAttribute(VertexAttribute.Color);
                if (hasColor) vertexColor ??= EnsureSkirtVertexColorMaterial(mr.sharedMaterial);
                if (hasColor && vertexColor != null)
                {
                    colored.Add((mf, src));
                    mr.sharedMaterial = vertexColor;
                    report.skirt_vertex_color = true;
                }
                else mr.sharedMaterial = flat ??= EnsureSkirtGroundMaterial(ground);
                mr.shadowCastingMode = ShadowCastingMode.Off;   // 그림자 받기만
                mr.receiveShadows = true;
            }
            report.skirt_nodes = sk.Count;
            report.skirt_water_nodes = skw.Count;
            if (colored.Count > 0) RecolorSkirt(m, area, colored, ground, report);
        }

        /// <summary>
        /// 원경 정점색을 범위 안 지면 색조로 다시 칠한다(05b 결정: 범위 사각형이 드러나지 않게).
        /// 새 색 = 지면 평균 알베도 × clamp(1 + v·(휘도(원래색) ÷ 경계 1.5 km 평균 휘도 − 1), 0.5, 1.6), v = SkirtVariation.
        /// 색조는 지면을 따르고, 산림·저지대·물 밑의 밝기 차만 약하게 남긴다.
        /// </summary>
        static void RecolorSkirt(AreaManifest m, string area, List<(MeshFilter mf, Mesh src)> colored, Color ground, ImportReport report)
        {
            var (minX, minZ, maxX, maxZ) = AreaExtent(m);
            static float Lum(Color c) => 0.2126f * c.r + 0.7152f * c.g + 0.0722f * c.b;
            double sr = 0, sg = 0, sb = 0, sl = 0; long n = 0;
            var cols = new List<Color>();
            var verts = new List<Vector3>();
            foreach (var (mf, src) in colored)
            {
                src.GetColors(cols);
                src.GetVertices(verts);
                var l2w = mf.transform.localToWorldMatrix;
                for (int i = 0; i < verts.Count && i < cols.Count; i++)
                {
                    var p = l2w.MultiplyPoint3x4(verts[i]);
                    float dx = Mathf.Max(minX - p.x, p.x - maxX, 0f), dz = Mathf.Max(minZ - p.z, p.z - maxZ, 0f);
                    float d = Mathf.Sqrt(dx * dx + dz * dz);
                    if (d <= 0f || d > 1500f) continue;
                    sr += cols[i].r; sg += cols[i].g; sb += cols[i].b; sl += Lum(cols[i]); n++;
                }
            }
            if (n == 0 || ground.maxColorComponent <= 0f) { warnings.Add("원경 색: 경계 근처 정점색 또는 지면 알베도를 구하지 못함 → 원래 정점색 사용"); return; }
            float lumMean = (float)(sl / n);
            report.skirt_source_mean_linear = new[] { (float)(sr / n), (float)(sg / n), (float)(sb / n) };
            report.skirt_recolor_target_linear = new[] { ground.r, ground.g, ground.b };
            foreach (var (mf, src) in colored)
            {
                src.GetColors(cols);
                for (int i = 0; i < cols.Count; i++)
                {
                    float k = Mathf.Clamp(1f + SkirtVariation * (Lum(cols[i]) / Mathf.Max(lumMean, 1e-5f) - 1f), 0.5f, 1.6f);
                    cols[i] = new Color(ground.r * k, ground.g * k, ground.b * k, 1f);
                }
                var copy = UnityEngine.Object.Instantiate(src);
                copy.name = src.name + RecolorSuffix;
                copy.SetColors(cols);
                mf.sharedMesh = SaveMeshAsset(copy, $"{TileAssetRoot}/{area}/skirt_{src.name}{RecolorSuffix}.asset");
            }
        }

        /// <summary>같은 경로에 메시 에셋이 있으면 내용을 덮어써 GUID 를 유지한다.</summary>
        static Mesh SaveMeshAsset(Mesh mesh, string path)
        {
            var existing = AssetDatabase.LoadAssetAtPath<Mesh>(path);
            if (existing == null) { AssetDatabase.CreateAsset(mesh, path); return mesh; }
            EditorUtility.CopySerialized(mesh, existing);
            existing.name = mesh.name;
            EditorUtility.SetDirty(existing);
            UnityEngine.Object.DestroyImmediate(mesh);
            return existing;
        }

        /// <summary>
        /// 멀리서 본 범위 안 지면의 평균 알베도(선형): 타일마다 레이어 텍스처 평균색 × 레이어 색 곱(diffuseRemapMax)을 알파맵 덮임 비율로 가중해 평균한다.
        /// basemap 거리(1500 m) 안팎으로 색이 바뀌지 않음을 05b-나에서 확인했다. coverage = [바탕, 아스팔트, 보도, 자갈] 평균 비율.
        /// </summary>
        public static (Color albedo, float[] coverage) GroundAlbedo(IEnumerable<Terrain> terrains)
        {
            var cache = new Dictionary<string, Color>();
            double r = 0, g = 0, b = 0;
            var cov = new double[4];
            int nt = 0;
            foreach (var t in terrains)
            {
                var td = t.terrainData;
                var layers = td.terrainLayers;
                if (layers == null || layers.Length == 0) continue;
                var w = new double[layers.Length];
                if (layers.Length == 1) w[0] = 1;
                else
                {
                    int res = td.alphamapResolution;
                    var a = td.GetAlphamaps(0, 0, res, res);
                    int step = Mathf.Max(1, res / 256);
                    long cnt = 0;
                    for (int z = 0; z < res; z += step)
                        for (int x = 0; x < res; x += step)
                        {
                            for (int i = 0; i < layers.Length; i++) w[i] += a[z, x, i];
                            cnt++;
                        }
                    for (int i = 0; i < w.Length; i++) w[i] /= cnt;
                }
                nt++;
                for (int i = 0; i < layers.Length; i++)
                {
                    if (i < 4) cov[i] += w[i];
                    var tex = layers[i] != null ? layers[i].diffuseTexture : null;
                    if (tex == null) continue;
                    string path = AssetDatabase.GetAssetPath(tex);
                    if (!cache.TryGetValue(path, out var c)) cache[path] = c = AverageLinear(new[] { path });
                    var tint = layers[i].diffuseRemapMax;   // 레이어 색 곱(URP Color Tint, 선형) — 테마가 생성 이미지에 건다
                    r += w[i] * c.r * tint.x; g += w[i] * c.g * tint.y; b += w[i] * c.b * tint.z;
                }
            }
            if (nt == 0) return (Color.black, new float[4]);
            return (new Color((float)(r / nt), (float)(g / nt), (float)(b / nt)), cov.Select(v => (float)(v / nt)).ToArray());
        }

        /// <summary>텍스처 파일들의 평균색(선형). 파일을 직접 읽으므로 임포트 설정(읽기 가능 여부)과 무관하다.</summary>
        static Color AverageLinear(IEnumerable<string> assetPaths)
        {
            double r = 0, g = 0, b = 0; long n = 0;
            foreach (var ap in assetPaths)
            {
                if (string.IsNullOrEmpty(ap)) continue;
                string full = Path.GetFullPath(Path.Combine(Application.dataPath, "..", ap));
                if (!File.Exists(full)) continue;
                var tex = new Texture2D(2, 2, TextureFormat.RGBA32, false);
                try
                {
                    if (!tex.LoadImage(File.ReadAllBytes(full), false)) continue;
                    var px = tex.GetPixels32();
                    int step = Mathf.Max(1, px.Length / 262144);
                    for (int i = 0; i < px.Length; i += step)
                    {
                        r += Mathf.GammaToLinearSpace(px[i].r / 255f);
                        g += Mathf.GammaToLinearSpace(px[i].g / 255f);
                        b += Mathf.GammaToLinearSpace(px[i].b / 255f);
                        n++;
                    }
                }
                finally { UnityEngine.Object.DestroyImmediate(tex); }
            }
            return n == 0 ? Color.black : new Color((float)(r / n), (float)(g / n), (float)(b / n));
        }

        /// <summary>COLOR_0 이 없는 원경용 단색 재질. 값은 매번 덮어쓴다(색 = 범위 안 지면 평균 알베도).</summary>
        static Material EnsureSkirtGroundMaterial(Color groundLinear)
        {
            string p = MaterialRoot + "/Skirt.mat";
            var mat = AssetDatabase.LoadAssetAtPath<Material>(p);
            if (mat == null) { mat = new Material(Shader.Find("Universal Render Pipeline/Lit")) { name = "Skirt" }; AssetDatabase.CreateAsset(mat, p); }
            mat.SetColor("_BaseColor", groundLinear.maxColorComponent > 0f ? groundLinear.gamma : new Color(0.45f, 0.48f, 0.33f));
            mat.SetFloat("_Smoothness", 0f);
            EditorUtility.SetDirty(mat);
            return mat;
        }

        /// <summary>
        /// COLOR_0 이 있는 원경 메시용. URP Lit 은 정점색을 쓰지 않으므로 glTFast 가 만든 재질의 셰이더(glTF-pbrMetallicRoughness,
        /// 정점색을 곱한다)를 이어받는다. 바탕색 흰색·거칠기 1·금속 0 을 매번 덮어쓴다(색은 메시 정점색이 맡는다).
        /// 셰이더를 못 찾으면 null(단색 Skirt.mat 사용).
        /// </summary>
        static Material EnsureSkirtVertexColorMaterial(Material imported)
        {
            string p = MaterialRoot + "/SkirtVertexColor.mat";
            var mat = AssetDatabase.LoadAssetAtPath<Material>(p);
            if (mat == null)
            {
                if (imported == null || imported.shader == null || !imported.shader.name.Contains("glTF"))
                {
                    warnings.Add($"원경 정점색 재질: glTF 셰이더를 찾지 못함({(imported != null && imported.shader != null ? imported.shader.name : "null")}) → 단색 Skirt.mat");
                    return null;
                }
                mat = new Material(imported) { name = "SkirtVertexColor" };
                AssetDatabase.CreateAsset(mat, p);
            }
            if (mat.HasProperty("baseColorFactor")) mat.SetVector("baseColorFactor", (Vector4)Color.white);
            if (mat.HasProperty("baseColorTexture")) mat.SetTexture("baseColorTexture", null);
            if (mat.HasProperty("metallicFactor")) mat.SetFloat("metallicFactor", 0f);
            if (mat.HasProperty("roughnessFactor")) mat.SetFloat("roughnessFactor", 1f);
            EditorUtility.SetDirty(mat);
            return mat;
        }

        // ------------------------------------------------------------------ glb 공통
        static GameObject InstantiateGlb(string assetPath, string name, GameObject parent)
        {
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(assetPath);
            if (prefab == null) { warnings.Add($"glb 임포트 실패: {assetPath}"); return null; }
            var inst = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
            PrefabUtility.UnpackPrefabInstance(inst, PrefabUnpackMode.Completely, InteractionMode.AutomatedAction);
            // glTFast(편집 시 임포트)는 SceneObjectCreation.WhenMultipleRootNodes 라서, 루트 노드가 하나뿐인 glb 는
            // 그 노드(B_/BR_/W_…)가 곧 프리팹 루트가 된다. 이름을 바꾸면 노드 이름(메타 연결 키)이 사라지므로 그때는 감싼다.
            bool rootIsNode = inst.GetComponent<MeshFilter>() != null || HasNodePrefix(inst.name);
            GameObject root = inst;
            if (rootIsNode)
            {
                root = new GameObject(name);
                root.transform.SetParent(parent.transform, false);
                inst.transform.SetParent(root.transform, false);   // 노드 로컬 변환(있으면)은 그대로 둔다
            }
            else
            {
                inst.name = name;
                inst.transform.SetParent(parent.transform, false);
            }
            root.transform.localPosition = Vector3.zero;
            root.transform.localRotation = Quaternion.identity;
            root.transform.localScale = Vector3.one;
            return root;
        }

        static bool HasNodePrefix(string n) =>
            n.StartsWith("B_", StringComparison.Ordinal) || n.StartsWith("BR_", StringComparison.Ordinal) || n.StartsWith("W_", StringComparison.Ordinal) ||
            n.StartsWith("SK_", StringComparison.Ordinal) || n.StartsWith("SKW_", StringComparison.Ordinal) || n.StartsWith("AXIS_", StringComparison.Ordinal);

        // ------------------------------------------------------------------ 축 검증(D-005)
        static void CheckAxis(string area, ImportReport report)
        {
            var root = InstantiateGlb($"{TileAssetRoot}/{area}/axis_test.glb", "AxisTest", new GameObject("AxisTestRoot"));
            if (root == null) return;
            var east = root.GetComponentsInChildren<MeshRenderer>(true).FirstOrDefault(r => r.name.Contains("AXIS_East100"));
            if (east == null) { warnings.Add("axis_test: AXIS_East100 없음"); }
            else
            {
                var b = east.bounds;
                report.axis_east_extent_x = b.max.x - root.transform.position.x;
                report.axis_east_extent_z = b.max.z - root.transform.position.z;
                // 동쪽 팔은 +X 로 100m, +Z 로 8m 여야 한다
                report.axis_ok = Mathf.Abs(report.axis_east_extent_x - 100f) < 1f && Mathf.Abs(report.axis_east_extent_z - 8f) < 1f && b.min.x > -1f;
            }
            UnityEngine.Object.DestroyImmediate(root.transform.parent.gameObject);
        }

        // ------------------------------------------------------------------ 경계·플레이어·환경
        static void BuildBounds(AreaManifest m)
        {
            var (minX, minZ, maxX, maxZ) = AreaExtent(m);
            var go = new GameObject("Bounds");
            void Wall(string n, Vector3 c, Vector3 s)
            {
                var w = new GameObject(n);
                w.transform.SetParent(go.transform, false);
                w.transform.position = c;
                w.AddComponent<BoxCollider>().size = s;
                w.isStatic = true;
            }
            float h = 3000f, t = 10f, cx = (minX + maxX) / 2, cz = (minZ + maxZ) / 2, w2 = maxX - minX, d2 = maxZ - minZ;
            Wall("West", new Vector3(minX - t / 2, h / 2, cz), new Vector3(t, h, d2 + 2 * t));
            Wall("East", new Vector3(maxX + t / 2, h / 2, cz), new Vector3(t, h, d2 + 2 * t));
            Wall("South", new Vector3(cx, h / 2, minZ - t / 2), new Vector3(w2 + 2 * t, h, t));
            Wall("North", new Vector3(cx, h / 2, maxZ + t / 2), new Vector3(w2 + 2 * t, h, t));
        }

        /// <summary>구현 범위(타일 합집합)의 Unity X·Z 범위.</summary>
        public static (float minX, float minZ, float maxX, float maxZ) AreaExtent(AreaManifest m) =>
            (m.tiles.Min(t => t.unity_position[0]), m.tiles.Min(t => t.unity_position[2]),
             m.tiles.Max(t => t.unity_position[0]) + m.tile_size_m, m.tiles.Max(t => t.unity_position[2]) + m.tile_size_m);

        static float HalfDiagonal(AreaManifest m)
        {
            var (minX, minZ, maxX, maxZ) = AreaExtent(m);
            return 0.5f * Mathf.Sqrt((maxX - minX) * (maxX - minX) + (maxZ - minZ) * (maxZ - minZ));
        }

        /// <summary>
        /// 카메라 far clip. 원경 스커트가 있으면 margin + max(4000, 범위 반대각선) 이상(계약 C4: margin 보다 크게).
        /// 없으면 v1 값(최소 <paramref name="v1Default"/>).
        /// </summary>
        public static float FarClip(AreaManifest m, float v1Default = 9000f) =>
            m.HasSkirt ? Mathf.Max(v1Default, m.skirt.margin_m + Mathf.Max(4000f, HalfDiagonal(m))) : v1Default;

        /// <summary>지수 안개 밀도. 원경이 있으면 원경 바깥 끝(약 margin + 반대각선)에서 약 90% 가 안개에 묻히게 한다.</summary>
        public static float FogDensity(AreaManifest m) =>
            m.HasSkirt ? Mathf.Clamp(2.3f / (m.skirt.margin_m + HalfDiagonal(m)), 0.00008f, 0.00018f) : 0.00018f;

        static void BuildPlayer(AreaManifest m, Dictionary<(int, int), Terrain> terrains, ImportReport report)
        {
            bool hasSpawn = m.spawn != null && m.spawn.e > 0f;
            Vector2 en = hasSpawn ? new Vector2(m.spawn.e, m.spawn.n) : FallbackSpawnEN;
            if (!hasSpawn) warnings.Add("manifest.spawn 없음 → 대체 스폰 좌표 사용");
            float x = en.x - (float)m.origin.easting, z = en.y - (float)m.origin.northing;
            float y = GroundHeight(m, terrains, x, z) + 0.3f;
            var player = new GameObject("Player");
            player.transform.position = new Vector3(x, y, z);
            player.transform.rotation = Quaternion.Euler(0f, hasSpawn ? m.spawn.yaw_deg : 180f, 0f);   // 남이섬 쪽을 본다
            var cc = player.AddComponent<CharacterController>();
            cc.height = 1.7f; cc.radius = 0.3f; cc.center = new Vector3(0, 0.85f, 0); cc.stepOffset = PlayerStepOffset; cc.slopeLimit = 50f;
            var camGo = new GameObject("PlayerCamera");
            camGo.tag = "MainCamera";
            camGo.transform.SetParent(player.transform, false);
            camGo.transform.localPosition = new Vector3(0, 1.6f, 0);
            var cam = camGo.AddComponent<Camera>();
            cam.nearClipPlane = 0.2f; cam.farClipPlane = FarClip(m); cam.fieldOfView = 65f;
            report.camera_far_clip = cam.farClipPlane;
            camGo.AddComponent<AudioListener>();
            var wc = player.AddComponent<WalkController>();
            wc.cameraPivot = camGo.transform;
            wc.buriedWaterTolerance = landingTolerance;
            // 비행 모드도 구현 범위 안에 머물게 한다(경계 벽은 비행 중 충돌이 꺼지므로)
            var (minX, minZ, maxX, maxZ) = AreaExtent(m);
            wc.areaMin = new Vector2(minX + 5f, minZ + 5f);
            wc.areaMax = new Vector2(maxX - 5f, maxZ - 5f);
            var hud = player.AddComponent<HudOverlay>();
            hud.credits = m.attributions;
            hud.interimNotice = m.interim_layers != null && m.interim_layers.Length > 0;
            // 스모크 실행 점검(-smoke <초> 가 있을 때만 동작)
            var smoke = player.AddComponent<SmokeProbe>();
            smoke.originE = m.origin.easting;
            smoke.originN = m.origin.northing;
            ThemeApply.ApplyPlayerLook(theme);   // 카메라 후처리·안티에일리어싱, HUD 테마
        }

        public static float GroundHeight(AreaManifest m, Dictionary<(int, int), Terrain> terrains, float x, float z)
        {
            int e0 = Mathf.FloorToInt((x + (float)m.origin.easting) / m.tile_size_m) * m.tile_size_m;
            int n0 = Mathf.FloorToInt((z + (float)m.origin.northing) / m.tile_size_m) * m.tile_size_m;
            if (!terrains.TryGetValue((e0, n0), out var terr)) return 60f;
            return terr.SampleHeight(new Vector3(x, 0, z)) + terr.transform.position.y;
        }

        // ------------------------------------------------------------------ 재질·텍스처(값은 매번 덮어쓴다, 05b-나)
        // 조명·하늘·안개·후처리는 ThemeApply.ApplyEnvironment, 지면·도로·건물·교량 텍스처는 ProceduralTextures(테마 값)
        static Material EnsureWaterMaterial(Theme t)
        {
            EnsureFolder(MaterialRoot);
            string wp = $"{MaterialRoot}/Water.mat";
            var water = AssetDatabase.LoadAssetAtPath<Material>(wp);
            if (water == null) { water = new Material(Shader.Find("Universal Render Pipeline/Lit")) { name = "Water" }; AssetDatabase.CreateAsset(water, wp); }
            water.SetFloat("_Surface", 1f);       // Transparent
            water.SetFloat("_Blend", 0f);
            water.SetColor("_BaseColor", t.waterColor);
            water.SetFloat("_Smoothness", t.waterSmoothness);
            water.SetFloat("_SpecularHighlights", t.waterSunGlint ? 1f : 0f);   // 해 반짝임(끄면 하늘 반사만)
            if (t.waterSunGlint) water.DisableKeyword("_SPECULARHIGHLIGHTS_OFF"); else water.EnableKeyword("_SPECULARHIGHLIGHTS_OFF");
            water.SetOverrideTag("RenderType", "Transparent");
            water.SetInt("_SrcBlend", (int)BlendMode.SrcAlpha);
            water.SetInt("_DstBlend", (int)BlendMode.OneMinusSrcAlpha);
            water.SetInt("_ZWrite", 0);
            water.EnableKeyword("_SURFACE_TYPE_TRANSPARENT");
            water.renderQueue = (int)RenderQueue.Transparent;
            EditorUtility.SetDirty(water);
            return water;
        }

        // ------------------------------------------------------------------ 유틸
        static void CopyIn(string src, string assetPath)
        {
            if (!File.Exists(src)) { warnings.Add("파일 없음: " + src); return; }
            string dst = Path.GetFullPath(Path.Combine(Application.dataPath, "..", assetPath));
            Directory.CreateDirectory(Path.GetDirectoryName(dst));
            if (File.Exists(dst) && new FileInfo(dst).Length == new FileInfo(src).Length &&
                File.GetLastWriteTimeUtc(dst) >= File.GetLastWriteTimeUtc(src)) return;
            File.Copy(src, dst, true);
        }

        static void ReplaceAsset(UnityEngine.Object obj, string path)
        {
            if (AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path) != null) AssetDatabase.DeleteAsset(path);
            AssetDatabase.CreateAsset(obj, path);
        }

        public static void EnsureFolder(string path)
        {
            var parts = path.Split('/');
            string cur = parts[0];
            for (int i = 1; i < parts.Length; i++)
            {
                string next = cur + "/" + parts[i];
                if (!AssetDatabase.IsValidFolder(next)) AssetDatabase.CreateFolder(cur, parts[i]);
                cur = next;
            }
        }
    }
}
