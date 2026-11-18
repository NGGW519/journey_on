using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace JourneyOn.Editor
{
    /// <summary>
    /// 외부 이미지 없이 코드로 만드는 텍스처·재질(계약 C1 외벽·지붕, C2 도로 지면 레이어).
    /// 같은 입력이면 같은 PNG 가 나오므로, 내용이 같으면 파일을 다시 쓰지 않는다(불필요한 재임포트 방지).
    /// </summary>
    public static class ProceduralTextures
    {
        public const string Root = "Assets/World/Generated";
        const string FacadeDir = Root + "/Facades";
        const string RoadDir = Root + "/Roads";
        const string LayerDir = Root + "/TerrainLayers";
        const string MaterialRoot = "Assets/Materials";
        public static readonly string[] Classes = { "residential", "commercial", "public", "industrial", "other" };

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
        public class BuildingMaterials
        {
            public readonly Dictionary<string, Material> wall = new(), roof = new(), flat = new();
        }

        /// <summary>
        /// Buildings/&lt;cls&gt;.mat(벽), Buildings/roof_&lt;cls&gt;.mat(지붕), Buildings/flat_&lt;cls&gt;.mat(UV 없는 v1 건물).
        /// 05b-나 결정: 재질 에셋이 있어도 코드 값을 매번 덮어쓴다(재임포트 때 코드 변경이 반드시 반영되게). 손 조정은 코드에 넣는다.
        /// 에셋은 지우지 않고 값만 바꾸므로 GUID·씬 참조는 유지된다.
        /// </summary>
        public static BuildingMaterials EnsureBuildingMaterials(FacadeSpec facade)
        {
            WorldImporter.EnsureFolder(MaterialRoot + "/Buildings");
            WorldImporter.EnsureFolder(FacadeDir);
            var lit = Shader.Find("Universal Render Pipeline/Lit");
            var res = new BuildingMaterials();
            foreach (var cls in Classes)
            {
                var (albedo, normal) = EnsureFacadeTextures(cls, Bay(facade, cls), FloorH(facade, cls));
                var roofTex = EnsureRoofTexture(cls, RoofTile(facade));

                string wp = $"{MaterialRoot}/Buildings/{cls}.mat";
                var wall = AssetDatabase.LoadAssetAtPath<Material>(wp);
                if (wall == null) { wall = new Material(lit) { name = cls }; AssetDatabase.CreateAsset(wall, wp); }
                SetupTextured(wall, albedo, normal, 0.9f);
                EditorUtility.SetDirty(wall);
                res.wall[cls] = wall;

                string rp = $"{MaterialRoot}/Buildings/roof_{cls}.mat";
                var roof = AssetDatabase.LoadAssetAtPath<Material>(rp);
                if (roof == null) { roof = new Material(lit) { name = "roof_" + cls }; AssetDatabase.CreateAsset(roof, rp); }
                SetupTextured(roof, roofTex, null, 0f);
                EditorUtility.SetDirty(roof);
                res.roof[cls] = roof;

                string fp = $"{MaterialRoot}/Buildings/flat_{cls}.mat";
                var flat = AssetDatabase.LoadAssetAtPath<Material>(fp);
                if (flat == null) { flat = new Material(lit) { name = "flat_" + cls }; AssetDatabase.CreateAsset(flat, fp); }
                flat.SetColor("_BaseColor", FlatColors[cls]);
                flat.SetFloat("_Smoothness", 0.15f);
                EditorUtility.SetDirty(flat);
                res.flat[cls] = flat;
            }
            return res;
        }

        static void SetupTextured(Material m, Texture2D albedo, Texture2D normal, float bumpScale)
        {
            m.SetTexture("_BaseMap", albedo);
            m.SetTextureScale("_BaseMap", Vector2.one);
            m.SetColor("_BaseColor", Color.white);
            m.SetFloat("_Metallic", 0f);
            // 매끄러움은 albedo 알파(유리창은 높고 벽은 낮다)
            m.SetFloat("_SmoothnessTextureChannel", 1f);
            m.EnableKeyword("_SMOOTHNESS_TEXTURE_ALBEDO_CHANNEL_A");
            m.SetFloat("_Smoothness", 1f);
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

        /// <summary>계약 C3: 교량 kind 별 재질(road / foot / rail). 값은 매번 덮어쓴다.</summary>
        public static Dictionary<string, Material> EnsureBridgeMaterials()
        {
            WorldImporter.EnsureFolder(MaterialRoot + "/Bridges");
            var lit = Shader.Find("Universal Render Pipeline/Lit");
            var colors = new Dictionary<string, Color>
            {
                ["road"] = new Color(0.64f, 0.63f, 0.60f),   // 콘크리트
                ["foot"] = new Color(0.72f, 0.60f, 0.47f),   // 목재 데크 계열
                ["rail"] = new Color(0.36f, 0.34f, 0.33f),   // 강재·도장
            };
            var res = new Dictionary<string, Material>();
            foreach (var kv in colors)
            {
                string p = $"{MaterialRoot}/Bridges/bridge_{kv.Key}.mat";
                var mat = AssetDatabase.LoadAssetAtPath<Material>(p);
                if (mat == null) { mat = new Material(lit) { name = "bridge_" + kv.Key }; AssetDatabase.CreateAsset(mat, p); }
                mat.SetColor("_BaseColor", kv.Value);
                mat.SetFloat("_Smoothness", kv.Key == "rail" ? 0.3f : 0.12f);
                mat.SetFloat("_Metallic", kv.Key == "rail" ? 0.4f : 0f);
                EditorUtility.SetDirty(mat);
                res[kv.Key] = mat;
            }
            return res;
        }

        /// <summary>계약 C2: 도로 마스크용 TerrainLayer 3개 [아스팔트(R), 보도 포장(G), 철도 자갈(B)].</summary>
        public static TerrainLayer[] EnsureRoadLayers()
        {
            WorldImporter.EnsureFolder(RoadDir);
            WorldImporter.EnsureFolder(LayerDir);
            // tileSize 는 1000 m 를 나누어떨어지게 해서 타일 경계에서 무늬가 끊기지 않게 한다
            var specs = new (string name, float tile, Func<Color[]> pixels)[]
            {
                ("asphalt", 5f, () => Asphalt(256)),
                ("paving", 2f, () => Paving(256)),
                ("ballast", 2.5f, () => Ballast(256)),
            };
            var layers = new TerrainLayer[specs.Length];
            for (int i = 0; i < specs.Length; i++)
            {
                var s = specs[i];
                var tex = WriteTexture($"{RoadDir}/{s.name}.png", 256, 256, s.pixels(), false);
                string lp = $"{LayerDir}/{s.name}.terrainlayer";
                var layer = AssetDatabase.LoadAssetAtPath<TerrainLayer>(lp);
                if (layer == null) { layer = new TerrainLayer(); AssetDatabase.CreateAsset(layer, lp); }
                // 값은 매번 덮어쓴다(05b-나)
                layer.diffuseTexture = tex;
                layer.tileSize = new Vector2(s.tile, s.tile);
                layer.tileOffset = Vector2.zero;
                layer.smoothness = 0f;
                layer.metallic = 0f;
                EditorUtility.SetDirty(layer);
                layers[i] = layer;
            }
            return layers;
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

        static (Texture2D albedo, Texture2D normal) EnsureFacadeTextures(string cls, float bay, float floor)
        {
            int w = Round4(bay * WallPxPerM), h = Round4(floor * WallPxPerM);
            var st = Style(cls);
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
                        if (fu < mortarU || fv < mortarV) { c = new Color(0.78f, 0.74f, 0.68f); hh = 0.42f; }
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
            var albedo = WriteTexture($"{FacadeDir}/wall_{cls}.png", w, h, col, false);
            var normal = WriteTexture($"{FacadeDir}/wall_{cls}_n.png", w, h, HeightToNormal(height, w, h, 3f), true);
            return (albedo, normal);
        }

        static Texture2D EnsureRoofTexture(string cls, float tileM)
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
                    if (cls == "residential") c = Color.Lerp(c, new Color(0.30f, 0.33f, 0.30f), Mathf.Clamp01((f - 0.62f) * 2.5f)); // 얼룩
                    c.a = s;
                    px[y * n + x] = c;
                }
            return WriteTexture($"{FacadeDir}/roof_{cls}.png", n, n, px, false);
        }

        // ================================================================== 도로 지면
        static Color[] Asphalt(int n)
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
                    float g = 0.41f + (f - 0.5f) * 0.05f + (speck - 0.5f) * 0.05f + (speck > 0.96f ? 0.05f : 0f) - (speck < 0.03f ? 0.05f : 0f);
                    px[y * n + x] = new Color(g, g, g * 1.03f, 1f);
                }
            return px;
        }

        static Color[] Paving(int n)
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
                    Color c = Color.Lerp(new Color(0.62f, 0.58f, 0.55f), new Color(0.70f, 0.55f, 0.50f), r < 0.3f ? 1f : 0f);
                    c *= 0.9f + 0.18f * Fbm(oct, u, v);
                    if (fu < 0.06f || fv < 0.12f) c = new Color(0.40f, 0.38f, 0.36f);
                    c.a = 1f;
                    px[y * n + x] = c;
                }
            return px;
        }

        static Color[] Ballast(int n)
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
                    Color c = Color.Lerp(new Color(0.44f, 0.42f, 0.39f), new Color(0.58f, 0.53f, 0.47f), t);
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

        /// <summary>PNG 로 쓰고 임포트 설정(반복, 밉맵, 이방성)을 맞춘다. 내용이 같으면 다시 쓰지 않는다.</summary>
        static Texture2D WriteTexture(string assetPath, int w, int h, Color[] px, bool normalMap)
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
                if (!normalMap && imp.alphaSource != TextureImporterAlphaSource.FromInput) { imp.alphaSource = TextureImporterAlphaSource.FromInput; dirty = true; }
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
