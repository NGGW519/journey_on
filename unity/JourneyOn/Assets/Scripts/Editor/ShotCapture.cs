using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace JourneyOn.Editor
{
    /// <summary>
    /// 검수용 스크린샷. 정해진 시점(EPSG:5186 좌표 기준)에서 씬을 렌더링해 PNG 로 저장한다.
    /// 배치 모드(-nographics 없이): -executeMethod JourneyOn.Editor.ShotCapture.CaptureFromCommandLine -area phase1
    ///   [-outDir &lt;dir&gt;] [-prefix &lt;파일 접두사, 기본 area&gt;] [-views name1,name2] [-basemapDistance &lt;m&gt;(진단용, 저장 안 함)]
    ///   [-showShoreWalls(진단용: 물가 벽을 반투명 빨강으로, 저장 안 함)]
    /// </summary>
    public static class ShotCapture
    {
        class View
        {
            public string name;
            public double ex, ny;      // 눈 E, N
            public float eyeAgl;       // 지면 위 높이 m
            public double tx, ty;      // 목표 E, N
            public float tz;           // 목표 높이: targetAgl 이면 지면 위 m, 아니면 해발 m
            public bool targetAgl;
        }

        static View Fixed(string name, double ex, double ny, float eyeAgl, double tx, double ty, float tzAbs) =>
            new View { name = name, ex = ex, ny = ny, eyeAgl = eyeAgl, tx = tx, ty = ty, tz = tzAbs };

        /// <summary>근접 시점: 목표에서 방위 azDeg(북=0, 동=90) 쪽으로 dist m 떨어져 지면 위 eyeAgl m 에서 목표 지면(+1 m)을 내려다본다.</summary>
        static View CloseUp(string name, double e, double n, float azDeg, float dist = 80f, float eyeAgl = 38f)
        {
            float az = azDeg * Mathf.Deg2Rad;
            return new View { name = name, ex = e + dist * Mathf.Sin(az), ny = n + dist * Mathf.Cos(az), eyeAgl = eyeAgl, tx = e, ty = n, tz = 1f, targetAgl = true };
        }

        static readonly View[] Views =
        {
            Fixed("overview_from_south", 246000, 574200, 1400f, 246000, 578800, 60f),
            Fixed("jarasum_aerial", 245600, 578600, 260f, 246400, 579700, 50f),
            Fixed("gapyeong_station_front", 244974, 579455, 1.7f, 245900, 579650, 62f),   // 역사 남쪽 광장(역 노드는 건물 안)
            Fixed("spawn_view", 0, 0, 1.7f, 246291, 577019, 55f),   // 눈 위치 = manifest.spawn(플레이어 시작점)
            Fixed("nami_from_air", 245200, 576400, 300f, 246291, 577019, 50f),
            // 근접(04 verdict 권고). 방위는 OSM 선형 방향을 보고 다리·물줄기를 옆에서 보게 골랐다
            CloseUp("dalcheon_bridge", 245325, 579843, 240f),      // 달전천 다리(남북 방향 도로교·자전거교)를 서남서에서
            CloseUp("main_junction", 245997, 580258, 160f),        // 경춘선 철교(66°) 옆 합류부를 남남동에서
            CloseUp("west_tributary", 244955, 579826, 110f, 80f, 36f), // 서쪽 지류(동서 방향) 상류를 동남동에서
            CloseUp("sagmak_upper", 244372, 580271, 240f, 80f, 36f),   // 사그막천 상부 뜬 수면 가장자리를 서남서에서
            CloseUp("jarasum_footbridge", 245768, 579938, 244f, 80f, 35f), // 자라섬 보행교(154°)를 옆(서남서)에서
            // 도로 마스크 행 방향 확인용 수직 부감(화면 위 = 북). 지면 약 513 × 289 m, Tile_244_579 안. -views 로 따로 찍는다
            Fixed("roadmask_topdown", 244740, 579480, 250f, 244740, 579480.5, 0f),
            // 05b 파이프라인 확인 요청 6항(교량 겹침·상하행 단, 범위 경계 강 수면 이음)
            CloseUp("gapyeonggyo_rail_w", 245355, 579842, 150f, 70f, 25f),
            CloseUp("gapyeonggyo_rail_e", 246019, 580281, 156f, 70f, 25f),
            CloseUp("gyeonggang_bridge", 246200, 580940, 200f, 120f, 30f),
            CloseUp("river_north_edge", 246440, 582000, 180f, 300f, 120f),
            CloseUp("river_south_edge", 247415, 575000, 0f, 300f, 120f),
            // 5차 지적 '묻힌 수면 옆 투명 벽' 확인 지점(-showShoreWalls 와 함께 찍는다)
            CloseUp("wall_check_sagmak", 244325, 580334, 200f, 60f, 25f),
            CloseUp("wall_check_dalcheon", 245285, 579812, 200f, 60f, 25f),
        };

        /// <summary>기본 실행(-views 없음)에서 빼는 보조 시점.</summary>
        static readonly string[] OptionalViews =
            { "roadmask_topdown", "gapyeonggyo_rail_w", "gapyeonggyo_rail_e", "gyeonggang_bridge", "river_north_edge", "river_south_edge",
              "wall_check_sagmak", "wall_check_dalcheon" };

        /// <summary>
        /// 진단용(-showShoreWalls): 보이지 않는 물가 벽(ShoreWall MeshCollider)을 반투명 빨강으로 그린다. 씬은 저장하지 않는다.
        /// </summary>
        static void ShowShoreWalls()
        {
            var mat = new Material(Shader.Find("Universal Render Pipeline/Unlit")) { name = "ShoreWallDebug" };
            mat.SetColor("_BaseColor", new Color(1f, 0.1f, 0.1f, 0.45f));
            mat.SetFloat("_Surface", 1f);
            mat.SetFloat("_Cull", 0f);
            mat.SetOverrideTag("RenderType", "Transparent");
            mat.SetInt("_SrcBlend", (int)UnityEngine.Rendering.BlendMode.SrcAlpha);
            mat.SetInt("_DstBlend", (int)UnityEngine.Rendering.BlendMode.OneMinusSrcAlpha);
            mat.SetInt("_ZWrite", 0);
            mat.EnableKeyword("_SURFACE_TYPE_TRANSPARENT");
            mat.renderQueue = (int)UnityEngine.Rendering.RenderQueue.Transparent + 10;
            int n = 0;
            foreach (var mc in UnityEngine.Object.FindObjectsByType<MeshCollider>(FindObjectsSortMode.None))
            {
                if (mc.gameObject.name != "ShoreWall" || mc.sharedMesh == null) continue;
                if (!mc.TryGetComponent<MeshFilter>(out var mf)) mf = mc.gameObject.AddComponent<MeshFilter>();
                mf.sharedMesh = mc.sharedMesh;
                if (!mc.TryGetComponent<MeshRenderer>(out var mr)) mr = mc.gameObject.AddComponent<MeshRenderer>();
                mr.sharedMaterial = mat;
                mr.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
                n++;
            }
            Debug.Log($"[ShotCapture] 물가 벽 표시 {n}개(진단용, 저장 안 함)");
        }

        public static void CaptureFromCommandLine()
        {
            var args = Environment.GetCommandLineArgs();
            string area = CaptureCommon.Arg(args, "-area") ?? "phase1";
            string outDir = CaptureCommon.Arg(args, "-outDir") ?? Path.Combine(CaptureCommon.RepoRoot, "_workspace", "screens");
            string prefix = CaptureCommon.Arg(args, "-prefix") ?? area;
            string only = CaptureCommon.Arg(args, "-views");
            float basemap = float.TryParse(CaptureCommon.Arg(args, "-basemapDistance"), System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture, out var bd) ? bd : -1f;
            bool showWalls = CaptureCommon.Flag(args, "-showShoreWalls");
            try { Capture(area, outDir, prefix, only, basemap, showWalls); EditorApplication.Exit(0); }
            catch (Exception e) { Debug.LogException(e); EditorApplication.Exit(1); }
        }

        [MenuItem("JourneyOn/Capture QA Shots")]
        static void Menu()
        {
            if (!UnityEditor.SceneManagement.EditorSceneManager.SaveCurrentModifiedScenesIfUserWantsTo()) return;
            Capture("phase1", Path.Combine(CaptureCommon.RepoRoot, "_workspace", "screens"), "phase1", null, -1f);
        }

        public static void Capture(string area, string outDir, string prefix, string only, float basemapDistance = -1f, bool showShoreWalls = false)
        {
            var a = CaptureCommon.OpenArea(area);
            CaptureCommon.OverrideBasemapDistance(basemapDistance);
            if (showShoreWalls) ShowShoreWalls();
            var m = a.m;
            Directory.CreateDirectory(outDir);
            var names = string.IsNullOrEmpty(only) ? null : new System.Collections.Generic.HashSet<string>(only.Split(',').Select(s => s.Trim()));
            bool prevAsync = CaptureCommon.DisableAsyncShaders();
            var cam = CaptureCommon.CreateCamera(a, "QA_Camera");
            const int W = 1600, H = 900;
            var rt = new RenderTexture(W, H, 24, RenderTextureFormat.ARGB32) { antiAliasing = 4 };
            var tex = new Texture2D(W, H, TextureFormat.RGB24, false);
            try
            {
                foreach (var v in Views)
                {
                    if (names != null ? !names.Contains(v.name) : Array.IndexOf(OptionalViews, v.name) >= 0) continue;
                    double vx = v.ex, vy = v.ny;
                    if (v.name == "spawn_view" && m.spawn != null && m.spawn.e > 0f) { vx = m.spawn.e; vy = m.spawn.n; }
                    var eye = a.ToUnity(vx, vy, 0f);
                    eye.y = a.Ground(eye) + v.eyeAgl;
                    var target = a.ToUnity(v.tx, v.ty, v.tz);
                    if (v.targetAgl) target.y = a.Ground(target) + v.tz;
                    cam.transform.position = eye;
                    cam.transform.LookAt(target);
                    CaptureCommon.Render(cam, rt, tex);
                    string file = Path.Combine(outDir, $"{prefix}_{v.name}.png");
                    File.WriteAllBytes(file, tex.EncodeToPNG());
                    Debug.Log($"[ShotCapture] {file} eye=({eye.x:0},{eye.y:0},{eye.z:0}) EN=({vx:0},{vy:0}) target=({target.x:0},{target.y:0},{target.z:0}) far={cam.farClipPlane:0}");
                }
            }
            finally
            {
                cam.targetTexture = null;
                UnityEngine.Object.DestroyImmediate(rt);
                UnityEngine.Object.DestroyImmediate(tex);
                UnityEngine.Object.DestroyImmediate(cam.gameObject);
                ShaderUtil.allowAsyncCompilation = prevAsync;
            }
        }
    }
}
