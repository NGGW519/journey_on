using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace JourneyOn.Editor
{
    /// <summary>
    /// 재질·텍스처(계약 C1 외벽·지붕, C2 도로 지면 레이어, C3 교량). 테마(<see cref="Theme"/>)가 값을 정한다.
    /// 텍스처는 테마의 생성 이미지(Assets/Art/Cozy/*.jpg, docs/ART.md 계약)를 먼저 쓰고, 없으면 코드로 만든다.
    /// 코드 생성 텍스처는 같은 입력이면 같은 PNG 가 나오므로, 내용이 같으면 파일을 다시 쓰지 않는다(불필요한 재임포트 방지).
    /// </summary>
    public static class ProceduralTextures
    {
        public const string Root = "Assets/World/Generated";
        const string LayerDir = Root + "/TerrainLayers";   // TerrainLayer 에셋은 테마와 관계없이 하나(값만 바꾼다, GUID 유지)
        const string MaterialRoot = "Assets/Materials";
        const string VariantDir = MaterialRoot + "/Buildings/Variants";
        public static readonly string[] Classes = { "residential", "commercial", "public", "industrial", "other" };
        public static readonly string[] BridgeKinds = { "road", "foot", "rail" };

        static string FacadeDir(Theme t) => t.GeneratedDir(Root, "Facades");
        static string RoadDir(Theme t) => t.GeneratedDir(Root, "Roads");

        // ================================================================== 생성 이미지(테마 artDir)
        /// <summary>이번 실행에서 쓴 생성 이미지 / 없어서 코드 생성으로 돌아간 파일(로그·unity_import.json 용).</summary>
        public static readonly SortedSet<string> ArtUsed = new(), ArtMissing = new();

        public static void ResetArtLog() { ArtUsed.Clear(); ArtMissing.Clear(); }

        /// <summary>
        /// 테마의 생성 이미지를 읽는다. 파일이 없거나 읽지 못하면 null(→ 코드 생성 텍스처). 임포트 설정(반복·밉맵·이방성·sRGB·최대 1024)은
        /// <see cref="CozyArtImport"/> 가 임포트 때 맞추고, 여기서도 다르면 맞춘 뒤 다시 임포트한다.
        /// </summary>
        public static Texture2D Art(Theme t, string file)
        {
            string p = t.ArtPath(file);
            if (p == null) return null;
            string full = Path.GetFullPath(Path.Combine(Application.dataPath, "..", p));
            if (!File.Exists(full)) { ArtMissing.Add(file); return null; }
            if (!(AssetImporter.GetAtPath(p) is TextureImporter imp))
            {
                AssetDatabase.ImportAsset(p, ImportAssetOptions.ForceSynchronousImport);
                imp = AssetImporter.GetAtPath(p) as TextureImporter;
            }
            if (imp != null && CozyArtImport.Configure(imp)) imp.SaveAndReimport();
            var tex = AssetDatabase.LoadAssetAtPath<Texture2D>(p);
            if (tex == null) { ArtMissing.Add(file + "(읽기 실패)"); return null; }
            ArtUsed.Add(file);
            return tex;
        }

        // 계약 C1 기본값(manifest.facade 가 없을 때). 주거 3.0/2.8, 상업 4.0/3.5, 공공 4.0/3.5, 공업 6.0/3.5, 기타 3.5/3.5
        static readonly Dictionary<string, (float bay, float floor)> DefaultFacade = new()
        {
            ["residential"] = (3.0f, 2.8f),
            ["commercial"] = (4.0f, 3.5f),
            ["public"] = (4.0f, 3.5f),
            ["industrial"] = (6.0f, 3.5f),
            ["other"] = (3.5f, 3.5f),
        };
        public const float DefaultRoofTile = 4f;
        const float WallPxPerM = 64f;   // 외벽 텍스처 해상도
        const float RoofPxPerM = 32f;

        // v1(UV 없는 건물)에 쓰는 단색. 예전 Buildings/<cls>.mat 색과 같다.
        public static readonly Dictionary<string, Color> FlatColors = new()
        {
            ["residential"] = new Color(0.87f, 0.82f, 0.74f),
            ["commercial"] = new Color(0.72f, 0.79f, 0.86f),
            ["public"] = new Color(0.86f, 0.74f, 0.64f),
            ["industrial"] = new Color(0.66f, 0.67f, 0.68f),
            ["other"] = new Color(0.80f, 0.79f, 0.75f),
        };

        public static float Bay(FacadeSpec f, string cls) => f?.bay_m != null ? f.bay_m.Get(cls, DefaultFacade[cls].bay) : DefaultFacade[cls].bay;
        public static float FloorH(FacadeSpec f, string cls) => f?.floor_height_m != null ? f.floor_height_m.Get(cls, DefaultFacade[cls].floor) : DefaultFacade[cls].floor;
        public static float RoofTile(FacadeSpec f) => f != null && f.roof_tile_m > 0f ? f.roof_tile_m : DefaultRoofTile;

        // ================================================================== 재질
        public enum Slot { Wall, Roof, Flat }

        /// <summary>
        /// 건물 재질. wall/roof 는 기본(변형 0) = Buildings/&lt;cls&gt;.mat·roof_&lt;cls&gt;.mat. 변형 k ≥ 1 은 Buildings/Variants/&lt;cls&gt;_k.mat·roof_&lt;cls&gt;_k.mat
        /// (틴트만 다르고 셰이더 변형이 같아 SRP Batcher 가 한 묶음으로 그린다). 건물마다 id 해시로 변형을 고른다(<see cref="Theme.WallVariant"/>).
        /// </summary>
        public class BuildingMaterials
        {
            public Theme theme;
            public readonly Dictionary<string, Material> wall = new(), roof = new(), flat = new();
            public readonly Dictionary<string, Material[]> wallVariants = new(), roofVariants = new();

            public Material For(Slot slot, string cls, string key) => slot switch
            {
                Slot.Roof => roofVariants[cls][theme.RoofVariant(key)],
                Slot.Flat => flat[cls],
                _ => wallVariants[cls][theme.WallVariant(key)],
            };
        }

        /// <summary>
        /// 재질 이름 → (자리, 용도). &lt;cls&gt;, &lt;cls&gt;_k = 벽, roof_&lt;cls&gt;[_k] = 지붕, flat_&lt;cls&gt; = 단색. 모르는 이름이면 false.
        /// RefreshLook 이 씬의 건물 재질을 (glb 재질 이름 없이) 다시 배정할 때 쓴다. 테스트 대상.
        /// </summary>
        public static bool ParseBuildingMaterial(string matName, out Slot slot, out string cls)
        {
            slot = Slot.Wall; cls = null;
            if (string.IsNullOrEmpty(matName)) return false;
            string s = matName;
            if (s.StartsWith("roof_", StringComparison.Ordinal)) { slot = Slot.Roof; s = s.Substring(5); }
            else if (s.StartsWith("flat_", StringComparison.Ordinal)) { slot = Slot.Flat; s = s.Substring(5); }
            int u = s.LastIndexOf('_');
            if (slot != Slot.Flat && u > 0 && int.TryParse(s.Substring(u + 1), out int k) && k > 0) s = s.Substring(0, u);
            if (Array.IndexOf(Classes, s) < 0) return false;
            cls = s;
            return true;
        }

        static Material LoadOrCreate(string path, Shader shader, string name)
        {
            var mat = AssetDatabase.LoadAssetAtPath<Material>(path);
            if (mat == null) { mat = new Material(shader) { name = name }; AssetDatabase.CreateAsset(mat, path); }
            else if (mat.shader != shader) mat.shader = shader;
            return mat;
        }

        /// <summary>
        /// Buildings/&lt;cls&gt;.mat(벽), roof_&lt;cls&gt;.mat(지붕), flat_&lt;cls&gt;.mat(UV 없는 v1 건물), Variants/(틴트 변형).
        /// 텍스처: 테마 생성 이미지 facade_&lt;cls&gt;.jpg·roof_&lt;cls&gt;.jpg(벽 UV 한 칸 × 한 층 = 0..1, 지붕 4 m = 0..1 이라 타일링 1), 없으면 코드 생성.
        /// 05b-나 결정: 재질 에셋이 있어도 코드 값을 매번 덮어쓴다(재임포트 때 코드 변경이 반드시 반영되게). 손 조정은 코드(Theme)에 넣는다.
        /// 에셋은 지우지 않고 값만 바꾸므로 GUID·씬 참조는 유지된다.
        /// </summary>
        public static BuildingMaterials EnsureBuildingMaterials(FacadeSpec facade, Theme theme)
        {
            WorldImporter.EnsureFolder(MaterialRoot + "/Buildings");
            WorldImporter.EnsureFolder(FacadeDir(theme));
            if (theme.wallTints.Length > 1 || theme.roofTints.Length > 1) WorldImporter.EnsureFolder(VariantDir);
            var lit = Shader.Find("Universal Render Pipeline/Lit");
            var res = new BuildingMaterials { theme = theme };
            foreach (var cls in Classes)
            {
                var wallArt = Art(theme, $"facade_{cls}.jpg");
                var roofArt = Art(theme, $"roof_{cls}.jpg");
                Texture2D albedo = wallArt, normal = null;
                if (wallArt == null) (albedo, normal) = EnsureFacadeTextures(cls, Bay(facade, cls), FloorH(facade, cls), theme);
                var roofTex = roofArt != null ? roofArt : EnsureRoofTexture(cls, RoofTile(facade), theme);

                var walls = new Material[theme.wallTints.Length];
                for (int k = 0; k < walls.Length; k++)
                {
                    string n = k == 0 ? cls : $"{cls}_{k}";
                    var m = LoadOrCreate(k == 0 ? $"{MaterialRoot}/Buildings/{n}.mat" : $"{VariantDir}/{n}.mat", lit, n);
                    if (wallArt != null) SetupArt(m, wallArt, theme.artWallSmoothness, theme.wallTints[k]);
                    else SetupTextured(m, albedo, normal, 0.9f, theme.wallTints[k]);
                    EditorUtility.SetDirty(m);
                    walls[k] = m;
                }
                var roofs = new Material[theme.roofTints.Length];
                for (int k = 0; k < roofs.Length; k++)
                {
                    string n = k == 0 ? "roof_" + cls : $"roof_{cls}_{k}";
                    var m = LoadOrCreate(k == 0 ? $"{MaterialRoot}/Buildings/{n}.mat" : $"{VariantDir}/{n}.mat", lit, n);
                    if (roofArt != null) SetupArt(m, roofArt, theme.artRoofSmoothness, theme.roofTints[k]);
                    else SetupTextured(m, roofTex, null, 0f, theme.roofTints[k]);
                    EditorUtility.SetDirty(m);
                    roofs[k] = m;
                }
                res.wall[cls] = walls[0];
                res.roof[cls] = roofs[0];
                res.wallVariants[cls] = walls;
                res.roofVariants[cls] = roofs;

                var flat = LoadOrCreate($"{MaterialRoot}/Buildings/flat_{cls}.mat", lit, "flat_" + cls);
                flat.SetColor("_BaseColor", theme.flatColors != null && theme.flatColors.TryGetValue(cls, out var fc) ? fc : FlatColors[cls]);
                flat.SetFloat("_Smoothness", 0.15f);
                EditorUtility.SetDirty(flat);
                res.flat[cls] = flat;
            }
            return res;
        }

        /// <summary>코드 생성 텍스처용: 매끄러움은 albedo 알파(유리창은 높고 벽은 낮다).</summary>
        static void SetupTextured(Material m, Texture2D albedo, Texture2D normal, float bumpScale, Color tint)
        {
            m.SetTexture("_BaseMap", albedo);
            m.SetTextureScale("_BaseMap", Vector2.one);
            m.SetTextureOffset("_BaseMap", Vector2.zero);
            m.SetColor("_BaseColor", tint);
            m.SetFloat("_Metallic", 0f);
            m.SetFloat("_SmoothnessTextureChannel", 1f);
            m.EnableKeyword("_SMOOTHNESS_TEXTURE_ALBEDO_CHANNEL_A");
            m.SetFloat("_Smoothness", 1f);
            SetNormal(m, normal, bumpScale);
        }

        /// <summary>생성 이미지(JPG, 알파 없음)용: 매끄러움은 상수, 노멀맵 없음, 타일링 1(UV 가 이미 계약 단위).</summary>
        static void SetupArt(Material m, Texture2D albedo, float smoothness, Color tint)
        {
            m.SetTexture("_BaseMap", albedo);
            m.SetTextureScale("_BaseMap", Vector2.one);
            m.SetTextureOffset("_BaseMap", Vector2.zero);
            m.SetColor("_BaseColor", tint);
            m.SetFloat("_Metallic", 0f);
            m.SetFloat("_SmoothnessTextureChannel", 0f);
            m.DisableKeyword("_SMOOTHNESS_TEXTURE_ALBEDO_CHANNEL_A");
            m.SetFloat("_Smoothness", smoothness);
            SetNormal(m, null, 0f);
        }

        static void SetNormal(Material m, Texture2D normal, float bumpScale)
        {
            if (normal != null)
            {
                m.SetTexture("_BumpMap", normal);
                m.SetFloat("_BumpScale", bumpScale);
                m.EnableKeyword("_NORMALMAP");
            }
            else
            {
                m.SetTexture("_BumpMap", null);
                m.DisableKeyword("_NORMALMAP");
            }
        }

        /// <summary>
        /// 계약 C3: 교량 kind 별 재질(road / foot / rail). 값은 매번 덮어쓴다.
        /// 생성 이미지 bridge_&lt;kind&gt;.jpg 가 있으면 텍스처(교량 메시는 UV 가 없어 Unity 가 상자 투영 UV 사본을 만든다,
        /// <see cref="BridgeUv"/>, 4 m = 0..1), 없으면 테마 단색.
        /// </summary>
        public static Dictionary<string, Material> EnsureBridgeMaterials(Theme theme)
        {
            WorldImporter.EnsureFolder(MaterialRoot + "/Bridges");
            var lit = Shader.Find("Universal Render Pipeline/Lit");
            var res = new Dictionary<string, Material>();
            foreach (var kind in BridgeKinds)
            {
                var mat = LoadOrCreate($"{MaterialRoot}/Bridges/bridge_{kind}.mat", lit, "bridge_" + kind);
                var art = Art(theme, $"bridge_{kind}.jpg");
                bool rail = kind == "rail";
                if (art != null)
                {
                    SetupArt(mat, art, rail ? theme.bridgeRailSmoothness : theme.bridgeSmoothness, Color.white);
                    mat.SetFloat("_Metallic", rail ? theme.bridgeRailMetallic : 0f);
                }
                else
                {
                    mat.SetTexture("_BaseMap", null);
                    mat.SetColor("_BaseColor", theme.bridgeColors[kind]);
                    mat.SetFloat("_SmoothnessTextureChannel", 0f);
                    mat.DisableKeyword("_SMOOTHNESS_TEXTURE_ALBEDO_CHANNEL_A");
                    mat.SetFloat("_Smoothness", rail ? theme.bridgeRailSmoothness : theme.bridgeSmoothness);
                    mat.SetFloat("_Metallic", rail ? theme.bridgeRailMetallic : 0f);
                    SetNormal(mat, null, 0f);
                }
                EditorUtility.SetDirty(mat);
                res[kind] = mat;
            }
            return res;
        }

        /// <summary>
        /// 계약 C2: 도로 마스크용 TerrainLayer 3개 [아스팔트(R), 보도 포장(G), 철도 자갈(B)].
        /// 생성 이미지 road_asphalt·ground_paving·rail_ballast 가 있으면 그것(타일 Theme.roadTileArt), 없으면 코드 생성(roadTileFallback).
        /// </summary>
        public static TerrainLayer[] EnsureRoadLayers(Theme theme)
        {
            WorldImporter.EnsureFolder(RoadDir(theme));
            WorldImporter.EnsureFolder(LayerDir);
            // tileSize 는 1000 m 를 나누어떨어지게 해서 타일 경계에서 무늬가 끊기지 않게 한다
            var specs = new (string name, string art, Func<Color[]> pixels)[]
            {
                ("asphalt", "road_asphalt.jpg", () => Asphalt(256, theme)),
                ("paving", "ground_paving.jpg", () => Paving(256, theme)),
                ("ballast", "rail_ballast.jpg", () => Ballast(256, theme)),
            };
            var layers = new TerrainLayer[specs.Length];
            for (int i = 0; i < specs.Length; i++)
            {
                var s = specs[i];
                var art = Art(theme, s.art);
                if (art != null && theme.artLayerDesaturate[i + 1] > 0f) art = Desaturated(theme, s.art, theme.artLayerDesaturate[i + 1]) ?? art;
                var tex = art != null ? art : WriteTexture($"{RoadDir(theme)}/{s.name}.png", 256, 256, s.pixels(), false);
                float tile = art != null ? theme.roadTileArt[i] : theme.roadTileFallback[i];
                string lp = $"{LayerDir}/{s.name}.terrainlayer";
                var layer = AssetDatabase.LoadAssetAtPath<TerrainLayer>(lp);
                if (layer == null) { layer = new TerrainLayer(); AssetDatabase.CreateAsset(layer, lp); }
                // 값은 매번 덮어쓴다(05b-나)
                layer.diffuseTexture = tex;
                layer.tileSize = new Vector2(tile, tile);
                layer.tileOffset = Vector2.zero;
                layer.smoothness = 0f;
                layer.metallic = 0f;
                SetLayerTint(layer, art != null ? theme.artLayerTint[i + 1] : Vector4.one);
                EditorUtility.SetDirty(layer);
                layers[i] = layer;
            }
            return layers;
        }

        /// <summary>
        /// 생성 이미지에서 채도를 낮춘 파생 텍스처(Generated/&lt;sub&gt;/Derived/&lt;이름&gt;_desat&lt;%&gt;.png, 알파 없음 → BC1).
        /// 원본(Assets/Art/Cozy)은 읽기만 한다. 같은 입력이면 같은 PNG 라 다시 쓰지 않는다. 실패하면 null(→ 원본 사용).
        /// </summary>
        static Texture2D Desaturated(Theme t, string file, float amount)
        {
            string full = Path.GetFullPath(Path.Combine(Application.dataPath, "..", t.ArtPath(file)));
            var src = new Texture2D(2, 2, TextureFormat.RGBA32, false);
            try
            {
                if (!src.LoadImage(File.ReadAllBytes(full), false)) return null;
                var px = src.GetPixels();
                for (int i = 0; i < px.Length; i++) px[i] = DesaturatePixel(px[i], amount);
                string dir = $"{Root}/{(string.IsNullOrEmpty(t.generatedSubdir) ? "" : t.generatedSubdir + "/")}Derived";
                WorldImporter.EnsureFolder(dir);
                var tex = WriteTexture($"{dir}/{Path.GetFileNameWithoutExtension(file)}_desat{Mathf.RoundToInt(amount * 100)}.png",
                                       src.width, src.height, px, false, noAlpha: true);
                ArtUsed.Remove(file);
                ArtUsed.Add($"{file}(채도 −{amount * 100:0}%)");
                return tex;
            }
            catch (Exception e) { Debug.LogWarning($"[ProceduralTextures] {file} 채도 낮추기 실패({e.Message}) → 원본"); return null; }
            finally { UnityEngine.Object.DestroyImmediate(src); }
        }

        /// <summary>sRGB 값 그대로 Rec.709 휘도 쪽으로 amount 만큼 섞는다(0 = 원본, 1 = 회색). 알파 1. 테스트 대상.</summary>
        public static Color DesaturatePixel(Color c, float amount)
        {
            float l = 0.2126f * c.r + 0.7152f * c.g + 0.0722f * c.b;
            var g = new Color(l, l, l, 1f);
            var r = Color.Lerp(c, g, Mathf.Clamp01(amount));
            r.a = 1f;
            return r;
        }

        /// <summary>TerrainLayer 색 곱(URP 'Color Tint'): diffuseRemapMax.xyz = 곱, min = 0. w 는 1/0(밀도 옵션 끔).</summary>
        public static void SetLayerTint(TerrainLayer layer, Vector4 tint)
        {
            layer.diffuseRemapMin = Vector4.zero;
            layer.diffuseRemapMax = new Vector4(tint.x, tint.y, tint.z, 1f);
        }

        /// <summary>
        /// 지면 바탕(잔디) 텍스처와 타일 크기·색 곱. 생성 이미지 ground_grass.jpg(Theme.groundTileArt, artLayerTint[0]) → 없으면 코드 생성(groundTileFallback, 곱 1).
        /// realistic 은 예전 Generated/ground.png 를 그대로 쓴다(없을 때만 같은 방식으로 만든다).
        /// 정사영상이 있는 타일은 WorldImporter 가 이 레이어 대신 정사영상을 쓴다(D-014).
        /// </summary>
        public static Texture2D EnsureGroundTexture(Theme theme, out float tile) => EnsureGroundTexture(theme, out tile, out _);

        public static Texture2D EnsureGroundTexture(Theme theme, out float tile, out Vector4 tint)
        {
            var art = Art(theme, "ground_grass.jpg");
            if (art != null) { tile = theme.groundTileArt; tint = theme.artLayerTint[0]; return art; }
            tile = theme.groundTileFallback;
            tint = Vector4.one;
            if (string.IsNullOrEmpty(theme.generatedSubdir)) return LegacyGroundTexture();
            string dir = $"{Root}/{theme.generatedSubdir}";
            WorldImporter.EnsureFolder(dir);
            return WriteTexture($"{dir}/ground.png", 256, 256, Grass(256, theme), false);
        }

        static Texture2D LegacyGroundTexture()
        {
            string p = Root + "/ground.png";
            if (!File.Exists(p))
            {
                const int n = 256;
                var tex = new Texture2D(n, n, TextureFormat.RGB24, true);
                var rnd = new System.Random(7);
                var px = new Color[n * n];
                for (int y = 0; y < n; y++)
                    for (int x = 0; x < n; x++)
                    {
                        float f = Mathf.PerlinNoise(x * 0.05f, y * 0.05f) * 0.6f + Mathf.PerlinNoise(x * 0.21f, y * 0.21f) * 0.4f;
                        float j = (float)rnd.NextDouble() * 0.04f;
                        px[y * n + x] = Color.Lerp(new Color(0.36f, 0.42f, 0.27f), new Color(0.50f, 0.50f, 0.36f), f) + new Color(j, j, j);
                    }
                tex.SetPixels(px);
                File.WriteAllBytes(p, tex.EncodeToPNG());
                AssetDatabase.ImportAsset(p, ImportAssetOptions.ForceSynchronousImport);
            }
            return AssetDatabase.LoadAssetAtPath<Texture2D>(p);
        }

        // ================================================================== 교량 UV(C3 메시에 TEXCOORD_0 이 없다)
        /// <summary>
        /// 교량 메시용 상자 투영 UV(m ÷ tileM). 교량 축은 정점 XZ 주성분(PCA)으로 정한다. 법선이 가장 큰 축으로
        /// 윗면·밑면 = (축 방향, 폭 방향), 옆면 = (축 방향, 높이), 끝면 = (폭 방향, 높이). 파이프라인 메시는 면마다 정점을 따로 가지므로
        /// (사각형 4 정점·6 인덱스) 정점 법선으로 면을 가를 수 있다. 기하는 바꾸지 않는다.
        /// </summary>
        public static Vector2[] BridgeUv(IReadOnlyList<Vector3> v, IReadOnlyList<Vector3> n, float tileM)
        {
            int count = v.Count;
            var uv = new Vector2[count];
            if (count == 0 || tileM <= 0f) return uv;
            double mx = 0, mz = 0;
            for (int i = 0; i < count; i++) { mx += v[i].x; mz += v[i].z; }
            mx /= count; mz /= count;
            double sxx = 0, szz = 0, sxz = 0;
            for (int i = 0; i < count; i++)
            {
                double dx = v[i].x - mx, dz = v[i].z - mz;
                sxx += dx * dx; szz += dz * dz; sxz += dx * dz;
            }
            double ang = 0.5 * Math.Atan2(2 * sxz, sxx - szz);
            float ax = (float)Math.Cos(ang), az = (float)Math.Sin(ang);   // 축 방향(x, z)
            float cx = -az, cz = ax;                                     // 폭 방향
            float inv = 1f / tileM;
            for (int i = 0; i < count; i++)
            {
                var p = v[i];
                var q = n != null && i < n.Count ? n[i] : Vector3.up;
                float px = (float)(p.x - mx), pz = (float)(p.z - mz);
                float along = px * ax + pz * az, across = px * cx + pz * cz;
                float nA = Mathf.Abs(q.x * ax + q.z * az), nC = Mathf.Abs(q.x * cx + q.z * cz), nY = Mathf.Abs(q.y);
                if (nY >= nA && nY >= nC) uv[i] = new Vector2(along * inv, across * inv);
                else if (nC >= nA) uv[i] = new Vector2(along * inv, p.y * inv);
                else uv[i] = new Vector2(across * inv, p.y * inv);
            }
            return uv;
        }

        // ================================================================== 외벽(창 한 칸 × 한 층)
        class FacadeStyle
        {
            public Color wall, band, frame, glassLo, glassHi, sillC;
            public float wallNoise = 0.06f, wallS = 0.08f, glassS = 0.82f;
            public float bandF;            // 바닥 띠 높이(층 높이 비율)
            public float winWF, winHF, sillF;   // 창 폭(칸 비율), 창 높이·창턱(층 비율)
            public float frameW = 0.06f;   // m
            public int mullions;           // 세로 창살 수
            public bool brick, ribs;
            public Color mortar = new Color(0.78f, 0.74f, 0.68f);   // 벽돌 줄눈
        }

        static FacadeStyle Style(string cls) => cls switch
        {
            // 주거: 크림색 미장 + 중간 창 + 흰 창틀
            "residential" => new FacadeStyle
            {
                wall = new Color(0.88f, 0.81f, 0.69f), band = new Color(0.76f, 0.70f, 0.61f), frame = new Color(0.93f, 0.93f, 0.91f),
                glassLo = new Color(0.20f, 0.25f, 0.30f), glassHi = new Color(0.40f, 0.47f, 0.54f), sillC = new Color(0.86f, 0.85f, 0.82f),
                bandF = 0.06f, winWF = 0.50f, winHF = 0.47f, sillF = 0.32f, mullions = 1,
            },
            // 상업: 청록 유리 커튼월 + 짙은 회색 스팬드럴
            "commercial" => new FacadeStyle
            {
                wall = new Color(0.36f, 0.40f, 0.44f), band = new Color(0.30f, 0.33f, 0.37f), frame = new Color(0.72f, 0.74f, 0.76f),
                glassLo = new Color(0.16f, 0.32f, 0.42f), glassHi = new Color(0.42f, 0.60f, 0.70f), sillC = new Color(0.72f, 0.74f, 0.76f),
                bandF = 0.20f, winWF = 0.97f, winHF = 0.74f, sillF = 0.21f, mullions = 1, frameW = 0.05f, wallS = 0.35f, glassS = 0.9f,
            },
            // 공공: 적갈색 벽돌 + 세로로 긴 창 + 석재 띠
            "public" => new FacadeStyle
            {
                wall = new Color(0.63f, 0.36f, 0.28f), band = new Color(0.84f, 0.81f, 0.74f), frame = new Color(0.95f, 0.94f, 0.92f),
                glassLo = new Color(0.18f, 0.23f, 0.28f), glassHi = new Color(0.36f, 0.43f, 0.50f), sillC = new Color(0.86f, 0.83f, 0.77f),
                bandF = 0.09f, winWF = 0.42f, winHF = 0.62f, sillF = 0.23f, mullions = 1, brick = true,
            },
            // 공업: 청회색 골강판 + 위쪽 띠창
            "industrial" => new FacadeStyle
            {
                wall = new Color(0.58f, 0.66f, 0.75f), band = new Color(0.55f, 0.55f, 0.53f), frame = new Color(0.30f, 0.32f, 0.34f),
                glassLo = new Color(0.25f, 0.31f, 0.36f), glassHi = new Color(0.45f, 0.52f, 0.58f), sillC = new Color(0.30f, 0.32f, 0.34f),
                bandF = 0.10f, winWF = 0.70f, winHF = 0.18f, sillF = 0.70f, mullions = 4, ribs = true, wallNoise = 0.03f, wallS = 0.3f,
            },
            // 기타: 연한 석회색(녹회색 기미) + 작은 창 + 짙은 창틀
            _ => new FacadeStyle
            {
                wall = new Color(0.75f, 0.74f, 0.65f), band = new Color(0.66f, 0.65f, 0.58f), frame = new Color(0.34f, 0.34f, 0.33f),
                glassLo = new Color(0.22f, 0.26f, 0.29f), glassHi = new Color(0.40f, 0.45f, 0.49f), sillC = new Color(0.80f, 0.79f, 0.74f),
                bandF = 0.05f, winWF = 0.40f, winHF = 0.40f, sillF = 0.30f, mullions = 0,
            },
        };

        /// <summary>창 배치는 용도별로 같고, 색만 테마(Theme.facadeColors)가 바꾼다.</summary>
        static FacadeStyle Style(string cls, Theme theme)
        {
            var st = Style(cls);
            if (theme.facadeColors != null && theme.facadeColors.TryGetValue(cls, out var fc))
            {
                st.wall = fc.wall; st.band = fc.band; st.frame = fc.frame;
                st.glassLo = fc.glassLo; st.glassHi = fc.glassHi; st.sillC = fc.sill;
                if (cls == "public") st.mortar = Color.Lerp(fc.band, fc.wall, 0.35f);
            }
            return st;
        }

        static (Texture2D albedo, Texture2D normal) EnsureFacadeTextures(string cls, float bay, float floor, Theme theme)
        {
            int w = Round4(bay * WallPxPerM), h = Round4(floor * WallPxPerM);
            var st = Style(cls, theme);
            var col = new Color[w * h];
            var height = new float[w * h];
            var oct = Octaves(StableSeed(cls), 4, 8, 16, 32);
            float winW = Mathf.Min(st.winWF * bay, bay - 0.08f), winH = st.winHF * floor, sill = st.sillF * floor;
            float wx0 = (bay - winW) / 2f, wx1 = wx0 + winW, wy0 = sill, wy1 = Mathf.Min(sill + winH, floor - 0.05f);
            int brickRows = Mathf.Max(1, Mathf.RoundToInt(floor / 0.075f)), brickCols = Mathf.Max(1, Mathf.RoundToInt(bay / 0.24f));
            int ribs = Mathf.Max(1, Mathf.RoundToInt(bay / 0.2f));
            for (int y = 0; y < h; y++)
                for (int x = 0; x < w; x++)
                {
                    float u = (x + 0.5f) / w, v = (y + 0.5f) / h;   // y=0 이 텍스처 아래 = 층 바닥(uv.y 정수)
                    float mx = u * bay, my = v * floor;
                    float n = Fbm(oct, u, v);
                    Color c = st.wall * (1f + (n - 0.5f) * 2f * st.wallNoise);
                    float hh = 0.5f, s = st.wallS;
                    if (st.brick)
                    {
                        int row = Mathf.FloorToInt(v * brickRows);
                        float bu = u * brickCols + (row % 2) * 0.5f;
                        int bcol = Mathf.FloorToInt(bu);
                        float fu = bu - bcol, fv = v * brickRows - row;
                        float mortarU = 0.012f / (bay / brickCols), mortarV = 0.012f / (floor / brickRows);
                        if (fu < mortarU || fv < mortarV) { c = st.mortar; hh = 0.42f; }
                        else c *= 0.88f + 0.24f * Hash01(((bcol % brickCols) + brickCols) % brickCols, row, 11);
                    }
                    if (st.ribs)
                    {
                        float ph = Mathf.Cos(u * ribs * Mathf.PI * 2f);
                        c *= 0.90f + 0.10f * ph;
                        hh = 0.5f + 0.12f * ph;
                    }
                    if (my < st.bandF * floor) { c = st.band * (1f + (n - 0.5f) * 0.08f); hh = 0.56f; s = 0.1f; }

                    bool inWin = mx >= wx0 && mx <= wx1 && my >= wy0 && my <= wy1;
                    if (inWin)
                    {
                        float fw = st.frameW;
                        bool frame = mx - wx0 < fw || wx1 - mx < fw || my - wy0 < fw || wy1 - my < fw;
                        for (int k = 1; k <= st.mullions && !frame; k++)
                        {
                            float mxk = wx0 + winW * k / (st.mullions + 1);
                            if (Mathf.Abs(mx - mxk) < fw * 0.5f) frame = true;
                        }
                        if (frame) { c = st.frame; hh = 0.62f; s = 0.3f; }
                        else
                        {
                            float g = Mathf.InverseLerp(wy0, wy1, my);
                            c = Color.Lerp(st.glassLo, st.glassHi, g * 0.8f + (n - 0.5f) * 0.3f);
                            hh = 0.12f; s = st.glassS;
                        }
                    }
                    else if (!st.ribs && my < wy0 && my > wy0 - 0.06f && mx > wx0 - 0.05f && mx < wx1 + 0.05f)
                    { c = st.sillC; hh = 0.66f; s = 0.15f; }   // 창턱

                    c.a = s;
                    col[y * w + x] = c;
                    height[y * w + x] = hh;
                }
            var albedo = WriteTexture($"{FacadeDir(theme)}/wall_{cls}.png", w, h, col, false);
            var normal = WriteTexture($"{FacadeDir(theme)}/wall_{cls}_n.png", w, h, HeightToNormal(height, w, h, 3f), true);
            return (albedo, normal);
        }

        static Texture2D EnsureRoofTexture(string cls, float tileM, Theme theme)
        {
            int n = Round4(tileM * RoofPxPerM);
            var px = new Color[n * n];
            var oct = Octaves(1000 + StableSeed(cls), 4, 8, 16, 32);
            Color baseC; float s;
            switch (cls)
            {
                case "residential": baseC = new Color(0.33f, 0.50f, 0.40f); s = 0.35f; break;   // 녹색 우레탄 방수 옥상
                case "commercial": baseC = new Color(0.64f, 0.64f, 0.62f); s = 0.12f; break;    // 콘크리트 판
                case "public": baseC = new Color(0.50f, 0.34f, 0.29f); s = 0.15f; break;        // 적갈색
                case "industrial": baseC = new Color(0.42f, 0.53f, 0.65f); s = 0.4f; break;     // 청색 골강판
                default: baseC = new Color(0.56f, 0.56f, 0.54f); s = 0.12f; break;
            }
            if (theme.roofColors != null && theme.roofColors.TryGetValue(cls, out var rc)) baseC = rc;
            Color stain = theme.roofColors != null ? baseC * 0.82f : new Color(0.30f, 0.33f, 0.30f);
            for (int y = 0; y < n; y++)
                for (int x = 0; x < n; x++)
                {
                    float u = (x + 0.5f) / n, v = (y + 0.5f) / n;
                    float f = Fbm(oct, u, v);
                    Color c = baseC * (0.86f + 0.28f * f);
                    float mx = u * tileM, my = v * tileM;
                    if (cls == "commercial" && (Frac(mx) < 0.03f || Frac(my) < 0.03f)) c *= 0.75f;          // 1 m 줄눈
                    if (cls == "public" && (Frac(mx * 2f) < 0.05f || Frac(my * 2f) < 0.05f)) c *= 0.85f;     // 0.5 m 줄
                    if (cls == "industrial") c *= 0.88f + 0.12f * Mathf.Cos(mx / 0.25f * Mathf.PI * 2f);    // 골
                    if (cls == "residential") c = Color.Lerp(c, stain, Mathf.Clamp01((f - 0.62f) * 2.5f)); // 얼룩
                    c.a = s;
                    px[y * n + x] = c;
                }
            return WriteTexture($"{FacadeDir(theme)}/roof_{cls}.png", n, n, px, false);
        }

        // ================================================================== 도로 지면
        /// <summary>잔디(대체 텍스처, cozy): 두 초록 사이 잡음 + 드문 작은 꽃 점(흰·노랑·분홍). 가장자리가 이어진다.</summary>
        static Color[] Grass(int n, Theme t)
        {
            var px = new Color[n * n];
            var oct = Octaves(507, 4, 8, 16, 32);
            var flowers = new[] { new Color(1f, 0.98f, 0.92f), new Color(1f, 0.90f, 0.45f), new Color(1f, 0.74f, 0.80f) };
            for (int y = 0; y < n; y++)
                for (int x = 0; x < n; x++)
                {
                    float u = (x + 0.5f) / n, v = (y + 0.5f) / n;
                    float f = Fbm(oct, u, v);
                    float j = (Hash01(x, y, 31) - 0.5f) * 0.05f;
                    Color c = Color.Lerp(t.groundLo, t.groundHi, Mathf.Clamp01(f * 1.3f - 0.15f)) * (1f + j);
                    float h = Hash01(x, y, 37);
                    if (h < t.groundFlowers) c = Color.Lerp(c, flowers[(int)(Hash01(x, y, 41) * flowers.Length) % flowers.Length], 0.8f);
                    c.a = 1f;
                    px[y * n + x] = c;
                }
            return px;
        }

        static Color[] Asphalt(int n, Theme t)
        {
            var px = new Color[n * n];
            var oct = Octaves(301, 4, 8, 16, 64);
            for (int y = 0; y < n; y++)
                for (int x = 0; x < n; x++)
                {
                    float u = (x + 0.5f) / n, v = (y + 0.5f) / n;
                    float f = Fbm(oct, u, v);
                    float speck = Hash01(x, y, 7);
                    // 05b-나: 너무 검었다(0.20). 밝은 회색 아스팔트(sRGB 평균 약 0.41, 0.38~0.45)로 올린다
                    float g = t.asphaltGray + (f - 0.5f) * 0.05f + (speck - 0.5f) * 0.05f + (speck > 0.96f ? 0.05f : 0f) - (speck < 0.03f ? 0.05f : 0f);
                    px[y * n + x] = new Color(g * t.asphaltMul.r, g * t.asphaltMul.g, g * t.asphaltMul.b, 1f);
                }
            return px;
        }

        static Color[] Paving(int n, Theme t)
        {
            // 0.2 × 0.1 m 블록 엇갈림 쌓기, 2 m 반복(10 × 20 블록)
            const int cols = 10, rows = 20;
            var px = new Color[n * n];
            var oct = Octaves(401, 8, 16, 32);
            for (int y = 0; y < n; y++)
                for (int x = 0; x < n; x++)
                {
                    float u = (x + 0.5f) / n, v = (y + 0.5f) / n;
                    int row = Mathf.FloorToInt(v * rows);
                    float bu = u * cols + (row % 2) * 0.5f;
                    int col = Mathf.FloorToInt(bu);
                    float fu = bu - col, fv = v * rows - row;
                    float r = Hash01(((col % cols) + cols) % cols, row, 5);
                    Color c = Color.Lerp(t.pavingA, t.pavingB, r < 0.3f ? 1f : 0f);
                    c *= 0.9f + 0.18f * Fbm(oct, u, v);
                    if (fu < 0.06f || fv < 0.12f) c = t.pavingJoint;
                    c.a = 1f;
                    px[y * n + x] = c;
                }
            return px;
        }

        static Color[] Ballast(int n, Theme th)
        {
            // 자갈: 타일 반복되는 Worley(셀 40 × 40, 약 6 cm)
            const int g = 40;
            var px = new Color[n * n];
            for (int y = 0; y < n; y++)
                for (int x = 0; x < n; x++)
                {
                    float fx = (x + 0.5f) / n * g, fy = (y + 0.5f) / n * g;
                    int cx = Mathf.FloorToInt(fx), cy = Mathf.FloorToInt(fy);
                    float d1 = 9f, d2 = 9f; int best = 0;
                    for (int oy = -1; oy <= 1; oy++)
                        for (int ox = -1; ox <= 1; ox++)
                        {
                            int gx = cx + ox, gy = cy + oy;
                            int wx = ((gx % g) + g) % g, wy = ((gy % g) + g) % g;
                            float px0 = gx + Hash01(wx, wy, 21), py0 = gy + Hash01(wx, wy, 22);
                            float d = (px0 - fx) * (px0 - fx) + (py0 - fy) * (py0 - fy);
                            if (d < d1) { d2 = d1; d1 = d; best = wy * g + wx; }
                            else if (d < d2) d2 = d;
                        }
                    float t = Hash01(best, 3, 23);
                    Color c = Color.Lerp(th.ballastA, th.ballastB, t);
                    float edge = Mathf.Clamp01((Mathf.Sqrt(d2) - Mathf.Sqrt(d1)) * 4f);
                    c *= 0.55f + 0.45f * edge;
                    c.a = 1f;
                    px[y * n + x] = c;
                }
            return px;
        }

        // ================================================================== 공통
        sealed class TileNoise
        {
            readonly int n;
            readonly float[] v;

            public TileNoise(int n, int seed)
            {
                this.n = n;
                v = new float[n * n];
                var r = new System.Random(seed);
                for (int i = 0; i < v.Length; i++) v[i] = (float)r.NextDouble();
            }

            /// <summary>u, w 는 텍스처 좌표(0..1 주기). 가장자리가 이어진다.</summary>
            public float At(float u, float w)
            {
                float x = u * n, y = w * n;
                int x0 = Mathf.FloorToInt(x), y0 = Mathf.FloorToInt(y);
                float fx = x - x0, fy = y - y0;
                fx = fx * fx * (3f - 2f * fx);
                fy = fy * fy * (3f - 2f * fy);
                int xa = Wrap(x0), xb = Wrap(x0 + 1), ya = Wrap(y0), yb = Wrap(y0 + 1);
                return Mathf.Lerp(Mathf.Lerp(v[ya * n + xa], v[ya * n + xb], fx), Mathf.Lerp(v[yb * n + xa], v[yb * n + xb], fx), fy);
            }

            int Wrap(int i) => ((i % n) + n) % n;
        }

        static TileNoise[] Octaves(int seed, params int[] lattices)
        {
            var o = new TileNoise[lattices.Length];
            for (int i = 0; i < o.Length; i++) o[i] = new TileNoise(lattices[i], seed * 31 + i);
            return o;
        }

        static float Fbm(TileNoise[] oct, float u, float v)
        {
            float sum = 0f, amp = 0.5f, norm = 0f;
            foreach (var o in oct) { sum += o.At(u, v) * amp; norm += amp; amp *= 0.5f; }
            return sum / norm;
        }

        static float Hash01(int x, int y, int seed)
        {
            unchecked
            {
                uint h = (uint)(x * 374761393 + y * 668265263 + seed * 1442695041);
                h = (h ^ (h >> 13)) * 1274126177u;
                h ^= h >> 16;
                return (h & 0xFFFFFF) / 16777215f;
            }
        }

        /// <summary>실행마다 같은 씨앗(string.GetHashCode 는 런타임에 따라 달라질 수 있다).</summary>
        static int StableSeed(string s)
        {
            int h = 17;
            foreach (char c in s) h = unchecked(h * 31 + c);
            return h & 0xffff;
        }

        static float Frac(float x) => x - Mathf.Floor(x);
        static int Round4(float x) => Mathf.Max(4, Mathf.RoundToInt(x / 4f) * 4);

        static Color[] HeightToNormal(float[] h, int w, int hh, float strength)
        {
            var px = new Color[w * hh];
            for (int y = 0; y < hh; y++)
                for (int x = 0; x < w; x++)
                {
                    float l = h[y * w + (x + w - 1) % w], r = h[y * w + (x + 1) % w];
                    float d = h[((y + hh - 1) % hh) * w + x], u = h[((y + 1) % hh) * w + x];
                    var n = new Vector3(-(r - l) * strength, -(u - d) * strength, 1f).normalized;
                    px[y * w + x] = new Color(n.x * 0.5f + 0.5f, n.y * 0.5f + 0.5f, n.z * 0.5f + 0.5f, 1f);
                }
            return px;
        }

        /// <summary>
        /// PNG 로 쓰고 임포트 설정(반복, 밉맵, 이방성)을 맞춘다. 내용이 같으면 다시 쓰지 않는다.
        /// noAlpha: 알파 없음(지형 레이어용 — URP TerrainLit 은 형식에 알파가 있으면 매끄러움을 알파로 고정한다).
        /// </summary>
        static Texture2D WriteTexture(string assetPath, int w, int h, Color[] px, bool normalMap, bool noAlpha = false)
        {
            var tex = new Texture2D(w, h, TextureFormat.RGBA32, false, normalMap);
            tex.SetPixels(px);
            tex.Apply(false);
            byte[] png = tex.EncodeToPNG();
            UnityEngine.Object.DestroyImmediate(tex);
            string full = Path.GetFullPath(Path.Combine(Application.dataPath, "..", assetPath));
            Directory.CreateDirectory(Path.GetDirectoryName(full));
            bool same = File.Exists(full) && SameBytes(File.ReadAllBytes(full), png);
            if (!same)
            {
                File.WriteAllBytes(full, png);
                AssetDatabase.ImportAsset(assetPath, ImportAssetOptions.ForceSynchronousImport);
            }
            var imp = AssetImporter.GetAtPath(assetPath) as TextureImporter;
            if (imp != null)
            {
                bool dirty = false;
                if (normalMap && imp.textureType != TextureImporterType.NormalMap) { imp.textureType = TextureImporterType.NormalMap; dirty = true; }
                if (imp.wrapMode != TextureWrapMode.Repeat) { imp.wrapMode = TextureWrapMode.Repeat; dirty = true; }
                if (imp.npotScale != TextureImporterNPOTScale.None) { imp.npotScale = TextureImporterNPOTScale.None; dirty = true; }
                if (!imp.mipmapEnabled) { imp.mipmapEnabled = true; dirty = true; }
                if (imp.anisoLevel != 8) { imp.anisoLevel = 8; dirty = true; }
                if (imp.filterMode != FilterMode.Trilinear) { imp.filterMode = FilterMode.Trilinear; dirty = true; }
                if (imp.maxTextureSize < Mathf.Max(w, h)) { imp.maxTextureSize = 1024; dirty = true; }
                var alpha = noAlpha ? TextureImporterAlphaSource.None : TextureImporterAlphaSource.FromInput;
                if (!normalMap && imp.alphaSource != alpha) { imp.alphaSource = alpha; dirty = true; }
                if (dirty) imp.SaveAndReimport();
            }
            return AssetDatabase.LoadAssetAtPath<Texture2D>(assetPath);
        }

        static bool SameBytes(byte[] a, byte[] b)
        {
            if (a.Length != b.Length) return false;
            for (int i = 0; i < a.Length; i++) if (a[i] != b[i]) return false;
            return true;
        }
    }
}
