using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using UnityEditor;
using UnityEngine;
using Debug = UnityEngine.Debug;

namespace JourneyOn.Editor
{
    /// <summary>
    /// 시연 영상. 카메라 경로 키프레임(EPSG:5186 + 지면 위 높이 + 바라볼 점)을 Catmull-Rom 으로 보간해
    /// 1920×1080, 30fps 프레임 PNG 를 렌더링하고(ShotCapture 와 같은 URP 렌더 요청), ffmpeg 로 H.264 mp4 를 만든다.
    ///
    /// 배치 모드(-nographics 없이, -quit 없이):
    ///   -executeMethod JourneyOn.Editor.DemoCapture.CaptureFromCommandLine -area phase1
    ///   [-out &lt;mp4&gt;] [-framesDir &lt;dir&gt;] [-frames &lt;n&gt;] [-t0 &lt;초&gt;] [-t1 &lt;초&gt;] [-path &lt;keys.json&gt;]
    ///   [-fps 30] [-width 1920] [-height 1080] [-noCredits] [-noVideo] [-noTiltShift] [-ffmpeg &lt;ffmpeg.exe&gt;]
    /// 씬 테마(WorldLook)대로 찍는다: cozy 는 후처리 + 조감 구간 틸트시프트(지형 위 40 m 이하 0, 120 m 이상 최대), 자막 색도 테마를 따른다.
    /// -frames n: [t0, t1] 를 n 장으로 고르게 찍은 짧은 미리보기(기본 출력 이름 *_preview.mp4).
    /// 기본 출력: 프레임 _workspace/demo_frames/frame_00000.png…, 영상 dist/demo_yyyyMMdd/JourneyOn_&lt;area&gt;_demo.mp4,
    /// 경로 기록 _workspace/demo_frames/path.csv(프레임별 눈 위치·지면 여유·들어 올린 양).
    /// </summary>
    public static class DemoCapture
    {
        [Serializable]
        public class Key
        {
            public float t;              // 초
            public double e, n;          // 눈 EPSG:5186 (spawn=true 면 manifest.spawn)
            public float agl;            // 눈: 그 점 최상단 표면(지형·수면·지붕·다리) 위 m
            public double look_e, look_n;
            public float look_agl;       // 바라볼 점: 지면 위 m
            public float min_agl = -1f;  // 눈 아래 최소 여유(건물·다리·수면 포함). 음수면 min(agl, 8)
            public bool spawn;
        }

        [Serializable] class KeyFile { public Key[] keys; }

        static Key K(float t, double e, double n, float agl, double le, double ln, float lagl, float minAgl = -1f, bool spawn = false) =>
            new Key { t = t, e = e, n = n, agl = agl, look_e = le, look_n = ln, look_agl = lagl, min_agl = minAgl, spawn = spawn };

        /// <summary>
        /// 기본 경로(75초): 남이섬 선착장 스폰 → 북한강 수면 저공(자라섬 서쪽 수로) → 자라섬 상공 → 자라섬 보행교 → 가평역 앞 → 전체 조감.
        /// 수면 저공 구간 좌표는 OSM 수면 폴리곤 안(수로 중앙)으로 골랐다.
        /// </summary>
        static Key[] DefaultPath() => new[]
        {
            K(0f, 0, 0, 1.7f, 246291, 577019, 30f, 1.2f, spawn: true),          // 스폰: 남이섬을 본다
            K(4f, 246286, 578302, 2.2f, 246291, 577019, 30f, 1.2f),           // 천천히 앞으로
            K(9f, 246360, 578330, 12f, 247300, 578250, 0f),                    // 떠오르며 동쪽 강 건너로 고개
            K(15f, 246480, 578560, 15f, 246560, 579200, 0f),                   // 북쪽 상류로
            K(21f, 246520, 578880, 16f, 246470, 579500, 0f),                   // 자라섬 서쪽 수로 입구
            K(26f, 246380, 579250, 18f, 246300, 579800, 0f),                   // 수로 저공
            K(32f, 246420, 579520, 90f, 246600, 579800, 0f),                   // 자라섬 위로 상승
            K(38f, 246350, 579760, 170f, 246650, 579600, 0f),                  // 자라섬 상공 부감
            K(44f, 245990, 579990, 60f, 245768, 579938, 0f),                   // 서쪽으로 내려가며 보행교
            // 05c 미리보기: 보행교가 117 m 밖에서 작게 보여, 수로 위 동쪽 70 m 로 다가가 옆으로 흐르며 본다
            K(49f, 245835, 579900, 16f, 245760, 579912, 1f),                   // 자라섬 보행교 근접(수로 위)
            K(52f, 245815, 579862, 15f, 245757, 579905, 1f),                   // 옆으로 흐르며 계속 본다
            K(57f, 245380, 579690, 45f, 244974, 579535, 5f),                   // 가평역 쪽으로
            K(61f, 245085, 579440, 20f, 244974, 579535, 6f),                   // 가평역 앞
            K(64f, 245045, 579405, 18f, 244974, 579535, 6f),                   // 잠시 머묾
            K(70f, 244600, 578500, 350f, 245700, 579300, 0f),                  // 뒤로 빠지며 상승
            K(75f, 244300, 577600, 850f, 246000, 579000, 0f),                  // 전체 조감
        };

        const float SampleRadius = 4f;      // 눈 둘레 이 반경의 최고 표면을 본다
        const float LiftMaxWindow = 1.5f;   // 들어 올림 최대값 필터 반폭(초) — 장애물 전에 미리 올라가게
        const float LiftAvgWindow = 1.0f;   // 이동 평균 반폭(초, ≤ 최대값 필터 반폭이어야 요구 높이를 보장)

        class Options
        {
            public string area = "phase1", outPath, framesDir, pathFile, ffmpeg;
            public int fps = 30, width = 1920, height = 1080, frames;
            public float t0 = float.NaN, t1 = float.NaN;
            public bool credits = true, video = true, noTilt;
        }

        public static void CaptureFromCommandLine()
        {
            var a = Environment.GetCommandLineArgs();
            var o = new Options();
            string S(string k) => CaptureCommon.Arg(a, k);
            float F(string k, float d) => float.TryParse(S(k), NumberStyles.Float, CultureInfo.InvariantCulture, out var v) ? v : d;
            int I(string k, int d) => int.TryParse(S(k), out var v) ? v : d;
            o.area = S("-area") ?? "phase1";
            o.outPath = S("-out");
            o.framesDir = S("-framesDir");
            o.pathFile = S("-path");
            o.ffmpeg = S("-ffmpeg");
            o.fps = I("-fps", 30);
            o.width = I("-width", 1920);
            o.height = I("-height", 1080);
            o.frames = I("-frames", 0);
            o.t0 = F("-t0", float.NaN);
            o.t1 = F("-t1", float.NaN);
            o.credits = !CaptureCommon.Flag(a, "-noCredits");
            o.video = !CaptureCommon.Flag(a, "-noVideo");
            o.noTilt = CaptureCommon.Flag(a, "-noTiltShift");
            try { Capture(o); EditorApplication.Exit(0); }
            catch (Exception e) { Debug.LogException(e); EditorApplication.Exit(1); }
        }

        [MenuItem("JourneyOn/Demo Video Preview (90 frames)")]
        static void MenuPreview()
        {
            if (!UnityEditor.SceneManagement.EditorSceneManager.SaveCurrentModifiedScenesIfUserWantsTo()) return;
            Capture(new Options { frames = 90 });
        }

        static void Capture(Options o)
        {
            var area = CaptureCommon.OpenArea(o.area);
            // 경계 벽(높이 3000 m)·플레이어 충돌체가 높이 판정에 끼지 않게 잠시 끈다. 씬은 저장하지 않고, 끝나면 되돌린다.
            var hidden = new List<GameObject>();
            foreach (var name in new[] { "Bounds", "Player" })
            {
                var go = GameObject.Find(name);
                if (go != null && go.activeSelf) { go.SetActive(false); hidden.Add(go); }
            }
            Physics.SyncTransforms();
            try { CaptureInArea(o, area); }
            finally
            {
                foreach (var go in hidden) if (go != null) go.SetActive(true);
            }
        }

        static void CaptureInArea(Options o, CaptureCommon.Area area)
        {
            var m = area.m;
            var keys = LoadKeys(o.pathFile);
            if (keys.Length < 2) throw new Exception("키프레임이 2개 이상 있어야 합니다");
            for (int i = 1; i < keys.Length; i++)
                if (keys[i].t <= keys[i - 1].t) throw new Exception($"키프레임 시간이 증가하지 않음: [{i - 1}] {keys[i - 1].t} → [{i}] {keys[i].t}");

            // 키프레임 → Unity 절대 좌표
            int nk = keys.Length;
            var T = keys.Select(k => k.t).ToArray();
            var eye = new Vector3[nk];
            var look = new Vector3[nk];
            var minAgl = new float[nk];
            for (int i = 0; i < nk; i++)
            {
                var k = keys[i];
                double e = k.e, n = k.n;
                if (k.spawn && m.spawn != null && m.spawn.e > 0f) { e = m.spawn.e; n = m.spawn.n; }
                eye[i] = area.ToUnity(e, n, 0f);
                eye[i].y = SurfaceTop(area, eye[i]) + k.agl;        // 눈: 그 점의 최상단 표면(수면·지붕 포함) 위
                look[i] = area.ToUnity(k.look_e, k.look_n, 0f);
                look[i].y = area.Ground(look[i]) + k.look_agl;      // 바라볼 점: 지형 위(건물 안 점이면 정면 높이)
                minAgl[i] = k.min_agl >= 0f ? k.min_agl : Mathf.Min(k.agl, 8f);
            }
            float tStart = float.IsNaN(o.t0) ? T[0] : Mathf.Clamp(o.t0, T[0], T[nk - 1]);
            float tEnd = float.IsNaN(o.t1) ? T[nk - 1] : Mathf.Clamp(o.t1, tStart, T[nk - 1]);

            // 지면·건물 여유: 전체 경로를 30fps 격자로 먼저 계산(미리보기와 본편이 같은 높이를 쓰게)
            float dtGrid = 1f / o.fps;
            int nGrid = Mathf.CeilToInt((T[nk - 1] - T[0]) / dtGrid) + 1;
            var lift = new float[nGrid];
            for (int g = 0; g < nGrid; g++)
            {
                float t = Mathf.Min(T[0] + g * dtGrid, T[nk - 1]);
                var p = Hermite(eye, T, t);
                float need = SurfaceMax(area, p) + Linear(minAgl, T, t);
                lift[g] = Mathf.Max(0f, need - p.y);
            }
            var smooth = MovingAverage(MaxFilter(lift, Mathf.RoundToInt(LiftMaxWindow * o.fps)), Mathf.RoundToInt(LiftAvgWindow * o.fps));
            float LiftAt(float t)
            {
                float gi = (t - T[0]) / dtGrid;
                int g0 = Mathf.Clamp(Mathf.FloorToInt(gi), 0, nGrid - 1), g1 = Mathf.Min(g0 + 1, nGrid - 1);
                return Mathf.Lerp(smooth[g0], smooth[g1], gi - g0);
            }

            // 프레임 시각
            bool preview = o.frames > 0;
            List<float> times = new();
            if (preview)
                for (int i = 0; i < o.frames; i++) times.Add(o.frames == 1 ? tStart : Mathf.Lerp(tStart, tEnd, i / (float)(o.frames - 1)));
            else
                for (float t = tStart; t <= tEnd + 1e-4f; t = tStart + times.Count * dtGrid) times.Add(t);

            string framesDir = o.framesDir ?? Path.Combine(CaptureCommon.RepoRoot, "_workspace", "demo_frames");
            Directory.CreateDirectory(framesDir);
            foreach (var f in Directory.GetFiles(framesDir, "frame_*.png")) File.Delete(f);
            string outPath = o.outPath ?? Path.Combine(CaptureCommon.RepoRoot, "dist", $"demo_{DateTime.Now:yyyyMMdd}",
                $"JourneyOn_{o.area}_demo{(preview ? "_preview" : "")}.mp4");
            Debug.Log($"[DemoCapture] 키 {nk}개, 경로 {T[0]:0.0}–{T[nk - 1]:0.0}s, 렌더 {tStart:0.0}–{tEnd:0.0}s, 프레임 {times.Count}장 {o.width}x{o.height}@{o.fps} → {framesDir}");

            bool prevAsync = CaptureCommon.DisableAsyncShaders();
            var cam = CaptureCommon.CreateCamera(area, "Demo_Camera", 60f);
            var rt = new RenderTexture(o.width, o.height, 24, RenderTextureFormat.ARGB32) { antiAliasing = 4 };
            var tex = new Texture2D(o.width, o.height, TextureFormat.RGB24, false);
            var writes = new Queue<Task>();
            var csv = new StringBuilder("frame,t,E,N,y,agl_terrain,clear_surface,lift,look_E,look_N,look_y\n");
            float minClear = float.MaxValue;
            var sw = Stopwatch.StartNew();
            try
            {
                for (int i = 0; i < times.Count; i++)
                {
                    float t = times[i];
                    var p = Hermite(eye, T, t);
                    float l = LiftAt(t);
                    p.y += l;
                    var target = Hermite(look, T, t);
                    if ((target - p).sqrMagnitude < 1f) target = p + cam.transform.forward * 10f;
                    cam.transform.position = p;
                    cam.transform.LookAt(target, Vector3.up);

                    float clear = p.y - SurfaceMax(area, p);
                    minClear = Mathf.Min(minClear, clear);
                    // 틸트시프트(테마): 조감 구간에만. 지형 위 높이로 세기를 정한다(40 m 이하 0, 120 m 이상 최대, 1.2 km 이상 줄임). 초점 = 바라보는 점
                    float tilt = o.noTilt ? 0f : area.theme.TiltStrengthVideo(p.y - area.Ground(p));
                    csv.AppendLine(string.Format(CultureInfo.InvariantCulture, "{0},{1:0.000},{2:0.0},{3:0.0},{4:0.0},{5:0.0},{6:0.0},{7:0.0},{8:0.0},{9:0.0},{10:0.0}",
                        i, t, p.x + m.origin.easting, p.z + m.origin.northing, p.y, p.y - area.Ground(p), clear, l,
                        target.x + m.origin.easting, target.z + m.origin.northing, target.y));

                    CaptureCommon.Render(cam, rt, tex, area.theme, tilt, target);
                    byte[] png = tex.EncodeToPNG();
                    string file = Path.Combine(framesDir, $"frame_{i:00000}.png");
                    writes.Enqueue(Task.Run(() => File.WriteAllBytes(file, png)));
                    while (writes.Count > 8) writes.Dequeue().Wait();
                    if (i % 150 == 0 || i == times.Count - 1)
                        Debug.Log($"[DemoCapture] {i + 1}/{times.Count} t={t:0.0}s eye=({p.x:0},{p.y:0},{p.z:0}) lift={l:0.0} clear={clear:0.0} ({sw.Elapsed.TotalSeconds:0}s)");
                }
                while (writes.Count > 0) writes.Dequeue().Wait();
            }
            finally
            {
                cam.targetTexture = null;
                UnityEngine.Object.DestroyImmediate(rt);
                UnityEngine.Object.DestroyImmediate(tex);
                UnityEngine.Object.DestroyImmediate(cam.gameObject);
                ShaderUtil.allowAsyncCompilation = prevAsync;
            }
            File.WriteAllText(Path.Combine(framesDir, "path.csv"), csv.ToString(), new UTF8Encoding(false));
            int lifted = smooth.Count(v => v > 0.01f);
            Debug.Log($"[DemoCapture] 렌더 완료 {times.Count}장 {sw.Elapsed.TotalSeconds:0}s, 최소 표면 여유 {minClear:0.0} m, 들어 올린 격자 {lifted}/{nGrid}(최대 {smooth.Max():0.0} m)");

            if (!o.video) return;
            string ff = FindFfmpeg(o.ffmpeg) ?? throw new Exception("ffmpeg 를 찾지 못함(-ffmpeg <경로> 로 지정)");
            Directory.CreateDirectory(Path.GetDirectoryName(outPath));
            string vf = null;
            if (o.credits && m.attributions != null && m.attributions.Length > 0)
            {
                var lines = new List<string> { "데이터 출처:" };
                foreach (var c in m.attributions) lines.AddRange(Wrap(c, 130));
                if (m.interim_layers != null && m.interim_layers.Length > 0) lines.Add("※ 시연용 임시 데이터 포함(공식 국토정보 데이터로 교체 예정)");
                File.WriteAllText(Path.Combine(framesDir, "credits.txt"), string.Join("\n", lines), new UTF8Encoding(false));
                int fs = Mathf.RoundToInt(o.height / 60f);
                // 자막 색은 씬 테마(cozy: 크림색 상자 + 짙은 갈색 글자, realistic: 반투명 검정 + 흰 글자)
                vf = $"drawtext=fontfile='C\\:/Windows/Fonts/malgun.ttf':textfile=credits.txt:expansion=none:fontsize={fs}:fontcolor={area.theme.creditFontColor}:" +
                     $"box=1:boxcolor={area.theme.creditBoxColor}:boxborderw={fs / 2}:line_spacing={fs / 3}:x={fs}:y=h-th-{fs}";
            }
            string Args(string filter) =>
                $"-y -hide_banner -loglevel error -framerate {o.fps} -i frame_%05d.png " +
                (filter != null ? $"-vf \"{filter}\" " : "") +
                $"-c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p -movflags +faststart \"{outPath}\"";
            int code = RunFfmpeg(ff, Args(vf), framesDir, out string err);
            if (code != 0 && vf != null)
            {
                Debug.LogWarning($"[DemoCapture] 크레딧 자막 실패(ffmpeg {code}): {err.Trim()} → 자막 없이 다시 만든다");
                code = RunFfmpeg(ff, Args(null), framesDir, out err);
            }
            if (code != 0) throw new Exception($"ffmpeg 실패({code}): {err}");
            Debug.Log($"[DemoCapture] 영상 {outPath} ({new FileInfo(outPath).Length / (1024 * 1024)} MB, {times.Count / (float)o.fps:0.0}s)");
        }

        // ------------------------------------------------------------------ 경로
        static Key[] LoadKeys(string pathFile)
        {
            if (string.IsNullOrEmpty(pathFile)) return DefaultPath();
            var kf = JsonUtility.FromJson<KeyFile>(File.ReadAllText(pathFile));
            if (kf?.keys == null || kf.keys.Length == 0) throw new Exception("경로 파일에 keys 가 없음: " + pathFile);
            Debug.Log($"[DemoCapture] 경로 파일 {pathFile} ({kf.keys.Length}개)");
            return kf.keys;
        }

        /// <summary>
        /// 시간 간격이 고르지 않은 Catmull-Rom(접선 = (P[i+1]−P[i−1]) / (T[i+1]−T[i−1]))의 3차 에르미트 보간.
        /// 양 끝 접선은 0(서서히 출발·정지). 머묾·급감속 구간에서 되돌아가지 않도록 축마다 Fritsch–Carlson 제한을 건다.
        /// </summary>
        public static Vector3 Hermite(Vector3[] P, float[] T, float t)
        {
            int n = P.Length;
            if (t <= T[0]) return P[0];
            if (t >= T[n - 1]) return P[n - 1];
            int i = 0;
            while (i < n - 2 && t >= T[i + 1]) i++;
            float h = T[i + 1] - T[i];
            float s = (t - T[i]) / h;
            Vector3 m0 = Tangent(P, T, i), m1 = Tangent(P, T, i + 1);
            float s2 = s * s, s3 = s2 * s;
            return (2 * s3 - 3 * s2 + 1) * P[i] + (s3 - 2 * s2 + s) * h * m0 + (-2 * s3 + 3 * s2) * P[i + 1] + (s3 - s2) * h * m1;
        }

        static Vector3 Tangent(Vector3[] P, float[] T, int i)
        {
            int n = P.Length;
            if (i == 0 || i == n - 1) return Vector3.zero;
            Vector3 cr = (P[i + 1] - P[i - 1]) / (T[i + 1] - T[i - 1]);
            Vector3 dPrev = (P[i] - P[i - 1]) / (T[i] - T[i - 1]);
            Vector3 dNext = (P[i + 1] - P[i]) / (T[i + 1] - T[i]);
            return new Vector3(Limit(cr.x, dPrev.x, dNext.x), Limit(cr.y, dPrev.y, dNext.y), Limit(cr.z, dPrev.z, dNext.z));
        }

        static float Limit(float m, float d0, float d1)
        {
            if (d0 * d1 <= 0f) return 0f;   // 축 방향이 바뀌는 점은 평평하게(넘침 방지)
            float lim = 3f * Mathf.Min(Mathf.Abs(d0), Mathf.Abs(d1));
            return Mathf.Sign(d0) * Mathf.Min(Mathf.Abs(m), lim);
        }

        static float Linear(float[] v, float[] T, float t)
        {
            int n = v.Length;
            if (t <= T[0]) return v[0];
            if (t >= T[n - 1]) return v[n - 1];
            int i = 0;
            while (i < n - 2 && t >= T[i + 1]) i++;
            return Mathf.Lerp(v[i], v[i + 1], (t - T[i]) / (T[i + 1] - T[i]));
        }

        /// <summary>한 점의 최상단 표면(지형·건물·교량·수면). 충돌체가 없으면 지형 높이.</summary>
        static float SurfaceTop(CaptureCommon.Area a, Vector3 p)
        {
            var q = new Vector3(p.x, 5000f, p.z);
            return Physics.Raycast(q, Vector3.down, out var hit, 10000f, Physics.DefaultRaycastLayers, QueryTriggerInteraction.Ignore)
                ? hit.point.y : a.Ground(p.x, p.z);
        }

        /// <summary>눈 둘레(반경 4 m, 9점)에서 가장 높은 표면(지형·건물·교량·수면). 물가 벽(레이어 2)은 제외.</summary>
        static float SurfaceMax(CaptureCommon.Area a, Vector3 p)
        {
            float best = float.MinValue;
            for (int k = 0; k < 9; k++)
            {
                Vector3 o = k == 0 ? Vector3.zero : Quaternion.Euler(0f, 45f * k, 0f) * Vector3.forward * SampleRadius;
                var q = new Vector3(p.x + o.x, 5000f, p.z + o.z);
                if (Physics.Raycast(q, Vector3.down, out var hit, 10000f, Physics.DefaultRaycastLayers, QueryTriggerInteraction.Ignore))
                    best = Mathf.Max(best, hit.point.y);
                else
                    best = Mathf.Max(best, a.Ground(q.x, q.z));
            }
            return best;
        }

        static float[] MaxFilter(float[] v, int half)
        {
            var r = new float[v.Length];
            for (int i = 0; i < v.Length; i++)
            {
                float mx = 0f;
                for (int j = Math.Max(0, i - half); j <= Math.Min(v.Length - 1, i + half); j++) mx = Mathf.Max(mx, v[j]);
                r[i] = mx;
            }
            return r;
        }

        static float[] MovingAverage(float[] v, int half)
        {
            var r = new float[v.Length];
            for (int i = 0; i < v.Length; i++)
            {
                float sum = 0f; int c = 0;
                for (int j = Math.Max(0, i - half); j <= Math.Min(v.Length - 1, i + half); j++) { sum += v[j]; c++; }
                r[i] = sum / c;
            }
            return r;
        }

        // ------------------------------------------------------------------ ffmpeg
        static IEnumerable<string> Wrap(string text, int width)
        {
            while (text.Length > width)
            {
                int cut = text.LastIndexOf(' ', width);
                if (cut <= 0) cut = width;
                yield return text.Substring(0, cut).TrimEnd();
                text = "  " + text.Substring(cut).TrimStart();
            }
            yield return text;
        }

        static string FindFfmpeg(string explicitPath)
        {
            if (!string.IsNullOrEmpty(explicitPath) && File.Exists(explicitPath)) return explicitPath;
            foreach (var dir in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator))
            {
                try
                {
                    var p = Path.Combine(dir.Trim('"'), "ffmpeg.exe");
                    if (File.Exists(p)) return p;
                }
                catch (ArgumentException) { }
            }
            string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            string link = Path.Combine(local, "Microsoft", "WinGet", "Links", "ffmpeg.exe");
            if (File.Exists(link)) return link;
            string pkgs = Path.Combine(local, "Microsoft", "WinGet", "Packages");
            if (Directory.Exists(pkgs))
                foreach (var d in Directory.GetDirectories(pkgs, "Gyan.FFmpeg*"))
                {
                    var hit = Directory.GetFiles(d, "ffmpeg.exe", SearchOption.AllDirectories).FirstOrDefault();
                    if (hit != null) return hit;
                }
            return null;
        }

        static int RunFfmpeg(string exe, string args, string workDir, out string stderr)
        {
            Debug.Log($"[DemoCapture] {exe} {args}");
            var psi = new ProcessStartInfo(exe, args)
            {
                WorkingDirectory = workDir,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardError = true,
                RedirectStandardOutput = true,
                StandardErrorEncoding = Encoding.UTF8,
                StandardOutputEncoding = Encoding.UTF8,
            };
            using var p = Process.Start(psi);
            var outTask = p.StandardOutput.ReadToEndAsync();
            stderr = p.StandardError.ReadToEnd();
            p.WaitForExit();
            outTask.Wait();
            return p.ExitCode;
        }
    }
}
