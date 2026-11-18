using System;
using System.Collections.Generic;
using System.Globalization;
using UnityEngine;

namespace JourneyOn
{
    /// <summary>
    /// 스모크 실행 점검. 명령줄에 <c>-smoke &lt;초&gt;</c> 가 있을 때만 켜진다(없으면 아무것도 하지 않는다).
    /// - 5초마다 프레임 수, 평균 fps, 플레이어 위치, 발밑 충돌체를 <c>[Smoke]</c> 접두사로 로그에 남긴다.
    /// - <c>-smokeStart &lt;E&gt;,&lt;N&gt;</c>(EPSG:5186): 시작할 때 그 지점의 최상단 지면(수면 제외) 위로 옮긴다.
    /// - <c>-smokeWalk &lt;E&gt;,&lt;N&gt;[;&lt;E&gt;,&lt;N&gt;…]</c>: 입력 없이 CharacterController.Move 로 경유점을 차례로 걷는다
    ///   (꺾인 보행교처럼 직선으로 갈 수 없는 길). 진행 거리, 경유점 도착, 막힘 여부(막은 충돌체), 밟은 교량을 남긴다.
    /// - <c>-smokeShot &lt;png&gt;</c> [<c>-smokeShotAt &lt;초&gt;</c>, 기본 6]: 그 시각에 가까운 건물(25~150 m)을 조준해 화면 가운데를 조회하고
    ///   (클릭과 같은 HudOverlay.PickAtCenter), 몇 프레임 뒤 HUD 를 포함한 화면을 PNG 로 저장한다(HUD 가독성 확인, 07b).
    /// - 정한 시간이 지나면 요약을 남기고 Application.Quit 을 부른다.
    /// WorldImporter 가 Player 오브젝트에 붙인다(OnControllerColliderHit 를 받으려면 CharacterController 와 같은 오브젝트여야 한다).
    /// </summary>
    public class SmokeProbe : MonoBehaviour
    {
        [Tooltip("manifest.origin — WorldImporter 가 채운다")]
        public double originE = 246000, originN = 580000;
        public float logInterval = 5f;
        public float walkSpeed = 4f;
        public float gravity = -20f;
        [Tooltip("이 시간 동안 이 거리보다 적게 나아가면 막힌 것으로 본다")]
        public float stuckWindow = 3f, stuckDistance = 0.5f;
        [Tooltip("마지막 경유점 도착 반경 / 중간 경유점 통과 반경(꺾인 곳에서 난간을 깎지 않게 작게)")]
        public float arriveRadius = 1.5f, passRadius = 0.6f;

        public const string Tag = "[Smoke]";
        static readonly CultureInfo Inv = CultureInfo.InvariantCulture;

        bool active;
        float duration;
        float t0, lastLog;
        int frame0, lastLogFrame;
        float minFps = float.MaxValue, maxFrameMs;

        // 시작점·걷기
        bool hasStart;
        Vector2 startXZ;
        bool walking, reached, blocked;
        readonly List<Vector2> path = new();
        int wp;
        Vector3 startPos;
        float traveled, vy, stuckSince, minY = float.MaxValue, maxY = float.MinValue;
        Vector3 stuckRef;
        string lastHit;
        readonly HashSet<string> bridgesWalked = new();
        CharacterController cc;

        // 화면 저장(-smokeShot)
        string shotPath;
        float shotAt = 6f;
        int shotStage, shotFrame;

        void Awake()
        {
            var args = Environment.GetCommandLineArgs();
            string s = Arg(args, "-smoke");
            if (s == null) { enabled = false; return; }
            active = true;
            duration = float.TryParse(s, NumberStyles.Float, Inv, out var d) && d > 0f ? d : 40f;
            Application.runInBackground = true;   // 창이 포커스를 잃어도 프레임이 돌게
            cc = GetComponent<CharacterController>();

            string st = Arg(args, "-smokeStart");
            if (!string.IsNullOrEmpty(st))
            {
                if (TryPoint(st, out var p)) { startXZ = p; hasStart = true; }
                else Log($"smokeStart 형식 오류 '{st}' (예: -smokeStart 246300,578400)");
            }

            shotPath = Arg(args, "-smokeShot");
            if (string.IsNullOrEmpty(shotPath)) shotPath = null;
            if (float.TryParse(Arg(args, "-smokeShotAt"), NumberStyles.Float, Inv, out var sa) && sa > 0f) shotAt = sa;

            string w = Arg(args, "-smokeWalk");
            if (!string.IsNullOrEmpty(w))
            {
                foreach (var part in w.Split(';'))
                {
                    if (string.IsNullOrWhiteSpace(part)) continue;
                    if (TryPoint(part, out var p)) path.Add(p);
                    else { Log($"smokeWalk 형식 오류 '{part}' (예: -smokeWalk \"246300,578400;246310,578420\")"); path.Clear(); break; }
                }
                walking = path.Count > 0 && cc != null;
                if (path.Count > 0 && cc == null) Log("smokeWalk 무시: CharacterController 없음");
            }
        }

        bool TryPoint(string s, out Vector2 xz)
        {
            xz = default;
            var parts = s.Trim().Split(',');
            if (parts.Length != 2 || !double.TryParse(parts[0], NumberStyles.Float, Inv, out var e) || !double.TryParse(parts[1], NumberStyles.Float, Inv, out var n))
                return false;
            xz = new Vector2((float)(e - originE), (float)(n - originN));
            return true;
        }

        static string Arg(string[] a, string k)
        {
            int i = Array.IndexOf(a, k);
            if (i < 0) return null;
            return i + 1 < a.Length && !a[i + 1].StartsWith("-", StringComparison.Ordinal) ? a[i + 1] : "";
        }

        void Start()
        {
            if (!active) return;
            if (hasStart) Teleport(startXZ);
            t0 = lastLog = Time.realtimeSinceStartup;
            frame0 = lastLogFrame = Time.frameCount;
            startPos = stuckRef = transform.position;
            stuckSince = t0;
            Log($"시작: duration={duration:0}s scene={UnityEngine.SceneManagement.SceneManager.GetActiveScene().name} gfx={SystemInfo.graphicsDeviceType} " +
                $"screen={Screen.width}x{Screen.height} quality={QualitySettings.names[QualitySettings.GetQualityLevel()]} pos={Fmt(transform.position)} EN={EN(transform.position)}{GroundCheck()}");
            if (walking)
            {
                var wc = GetComponent<WalkController>();
                if (wc != null) wc.enabled = false;   // 스크립트 이동과 중력을 SmokeProbe 가 맡는다
                if (!cc.enabled) cc.enabled = true;
                float len = Vector2.Distance(XZ(transform.position), path[0]);
                for (int i = 1; i < path.Count; i++) len += Vector2.Distance(path[i - 1], path[i]);
                Log($"걷기 시작: 경유점 {path.Count}개, 끝 EN={ENxz(path[path.Count - 1])}, 경로 길이 {len:0.0} m, 속도 {walkSpeed:0.0} m/s");
            }
        }

        /// <summary>(x, z) 의 최상단 지면(수면·Ignore Raycast 제외, 교량 상판 포함) 위로 옮긴다.</summary>
        void Teleport(Vector2 xz)
        {
            float y = transform.position.y;
            if (TopSurface(new Vector3(xz.x, 0f, xz.y), out var hit)) y = hit.point.y + 0.05f;
            else Log($"smokeStart: 지면을 찾지 못함 EN={ENxz(xz)} → 높이 유지");
            bool was = cc != null && cc.enabled;
            if (cc != null) cc.enabled = false;
            transform.position = new Vector3(xz.x, y, xz.y);
            if (cc != null) cc.enabled = was;
            Physics.SyncTransforms();
            Log($"시작점 이동: EN={ENxz(xz)} y={y:0.00} 지면={(hit.collider != null ? hit.collider.name : "-")}");
        }

        void Update()
        {
            if (!active) return;
            float now = Time.realtimeSinceStartup;
            float dt = Time.unscaledDeltaTime;
            if (Time.frameCount > frame0 + 2 && dt > 0f)
            {
                minFps = Mathf.Min(minFps, 1f / dt);
                maxFrameMs = Mathf.Max(maxFrameMs, dt * 1000f);
            }

            if (walking && !reached && !blocked) StepWalk(now);
            if (shotPath != null) StepShot(now);

            if (now - lastLog >= logInterval)
            {
                int frames = Time.frameCount - lastLogFrame;
                float fps = frames / Mathf.Max(1e-3f, now - lastLog);
                float avg = (Time.frameCount - frame0) / Mathf.Max(1e-3f, now - t0);
                Log($"t={now - t0:0.0}s frames={Time.frameCount - frame0} fps={fps:0.0} avgFps={avg:0.0} pos={Fmt(transform.position)} EN={EN(transform.position)}{WalkState()}{GroundCheck()} landing={(WalkController.LandingAllowed(transform.position + Vector3.up * 5f, transform, LandingTolerance()) ? "OK" : "BLOCKED")}");
                lastLog = now;
                lastLogFrame = Time.frameCount;
            }

            if (now - t0 >= duration)
            {
                int frames = Time.frameCount - frame0;
                float avg = frames / Mathf.Max(1e-3f, now - t0);
                string walk = walking
                    ? $" walk: start={EN(startPos)} end={EN(transform.position)} traveled={traveled:0.0}m waypoints={Mathf.Min(wp, path.Count)}/{path.Count} remain={Remain():0.0}m " +
                      $"reached={reached} blocked={blocked}{(blocked ? " lastHit=" + lastHit : "")} y={minY:0.00}..{maxY:0.00} bridges=[{string.Join(",", bridgesWalked)}]"
                    : "";
                Log($"종료: elapsed={now - t0:0.0}s frames={frames} avgFps={avg:0.0} minFps={(minFps == float.MaxValue ? 0f : minFps):0.0} maxFrameMs={maxFrameMs:0.0}{walk}");
                active = false;
                Application.Quit();
            }
        }

        /// <summary>-smokeShot: 조준(0→1) → 3프레임 뒤 조회(1→2) → 3프레임 뒤 저장(2→3).</summary>
        void StepShot(float now)
        {
            if (shotStage == 0 && now - t0 >= shotAt)
            {
                var wc = GetComponent<WalkController>();
                if (wc != null) wc.enabled = false;   // 마우스가 조준을 되돌리지 않게
                AimAtBuilding();
                shotStage = 1; shotFrame = Time.frameCount;
            }
            else if (shotStage == 1 && Time.frameCount >= shotFrame + 3)
            {
                var hud = GetComponent<HudOverlay>();
                bool ok = hud != null && hud.PickAtCenter();
                Log($"화면 저장 준비: 조회 {(ok ? "OK" : "없음")}");
                shotStage = 2; shotFrame = Time.frameCount;
            }
            else if (shotStage == 2 && Time.frameCount >= shotFrame + 3)
            {
                ScreenCapture.CaptureScreenshot(shotPath);
                Log($"화면 저장: {shotPath} screen={Screen.width}x{Screen.height}");
                shotStage = 3;
            }
        }

        /// <summary>카메라에서 25~150 m 안 건물 중 60 m 에 가장 가까운 것의 벽 가운데를 화면 가운데에 둔다.</summary>
        void AimAtBuilding()
        {
            var cam = GetComponentInChildren<Camera>();
            if (cam == null) { Log("화면 저장: 카메라 없음"); return; }
            Vector3 eye = cam.transform.position, best = Vector3.zero;
            string bestName = null;
            float bestScore = float.MaxValue;
            foreach (var bi in FindObjectsByType<BuildingInfo>(FindObjectsSortMode.None))
            {
                var r = bi.GetComponentInChildren<Renderer>();
                if (r == null) continue;
                var b = r.bounds;
                var target = new Vector3(b.center.x, Mathf.Lerp(b.min.y, b.center.y, 0.6f), b.center.z);
                float d = Vector3.Distance(eye, target);
                if (d < 25f || d > 150f) continue;
                // 가는 길에 다른 것이 가리면 건너뛴다
                if (Physics.Raycast(eye, (target - eye).normalized, out var h, d + 5f, Physics.DefaultRaycastLayers & ~(1 << WalkController.WaterLayer), QueryTriggerInteraction.Ignore)
                    && h.collider.GetComponentInParent<BuildingInfo>() != bi) continue;
                float score = Mathf.Abs(d - 60f);
                if (score < bestScore) { bestScore = score; best = target; bestName = bi.name; }
            }
            if (bestName == null) { Log("화면 저장: 25~150 m 안에 보이는 건물 없음 → 그대로 찍음"); return; }
            var dir = best - eye;
            transform.rotation = Quaternion.Euler(0f, Mathf.Atan2(dir.x, dir.z) * Mathf.Rad2Deg, 0f);
            float pitch = -Mathf.Atan2(dir.y, new Vector2(dir.x, dir.z).magnitude) * Mathf.Rad2Deg;
            cam.transform.localRotation = Quaternion.Euler(pitch, 0f, 0f);
            Log($"화면 저장 조준: {bestName} 거리 {dir.magnitude:0.0} m");
        }

        string WalkState() => walking
            ? $" walk: traveled={traveled:0.0}m wp={Mathf.Min(wp, path.Count)}/{path.Count} remain={Remain():0.0}m{(reached ? " REACHED" : "")}{(blocked ? " BLOCKED" : "")}"
            : "";

        /// <summary>남은 경로 길이(지금 위치 → 다음 경유점 → … → 끝).</summary>
        float Remain()
        {
            if (!walking || wp >= path.Count) return 0f;
            float r = Vector2.Distance(XZ(transform.position), path[wp]);
            for (int i = wp + 1; i < path.Count; i++) r += Vector2.Distance(path[i - 1], path[i]);
            return r;
        }

        void StepWalk(float now)
        {
            bool last = wp == path.Count - 1;
            Vector2 to = path[wp] - XZ(transform.position);
            if (to.magnitude <= (last ? arriveRadius : passRadius))
            {
                if (last)
                {
                    reached = true;
                    Log($"도착: EN={EN(transform.position)} y={transform.position.y:0.00} traveled={traveled:0.0}m bridges=[{string.Join(",", bridgesWalked)}]");
                    wp++;
                    return;
                }
                Log($"경유점 {wp + 1}/{path.Count} 통과: EN={EN(transform.position)} y={transform.position.y:0.00}");
                wp++;
                to = path[wp] - XZ(transform.position);
            }
            Vector3 dir = new Vector3(to.x, 0f, to.y).normalized;
            transform.rotation = Quaternion.LookRotation(dir, Vector3.up);
            vy = cc.isGrounded ? -1f : vy + gravity * Time.deltaTime;
            Vector3 before = transform.position;
            cc.Move((dir * walkSpeed + Vector3.up * vy) * Time.deltaTime);
            traveled += Vector2.Distance(XZ(before), XZ(transform.position));
            minY = Mathf.Min(minY, transform.position.y);
            maxY = Mathf.Max(maxY, transform.position.y);
            if (TopSurface(transform.position, out var g) && Mathf.Abs(g.point.y - transform.position.y) < 0.6f)
            {
                var br = g.collider.GetComponentInParent<BridgeInfo>();
                if (br != null && bridgesWalked.Add(br.node)) Log($"교량 위: {br.node} ({br.bridgeName}) EN={EN(transform.position)} y={transform.position.y:0.00}");
            }

            if (Vector2.Distance(XZ(transform.position), XZ(stuckRef)) >= stuckDistance)
            {
                stuckRef = transform.position;
                stuckSince = now;
            }
            else if (now - stuckSince >= stuckWindow)
            {
                blocked = true;
                Log($"막힘: EN={EN(transform.position)} y={transform.position.y:0.00} wp={wp + 1}/{path.Count} {stuckWindow:0}s 동안 {stuckDistance:0.0} m 미만 이동, collisionFlags={cc.collisionFlags}, lastHit={lastHit ?? "-"}");
            }
        }

        void OnControllerColliderHit(ControllerColliderHit hit)
        {
            if (!active || hit.collider == null) return;
            // 바닥(지형·상판)과의 접촉은 제외하고 옆에서 막은 것만 기록
            if (hit.normal.y > 0.7f) return;
            lastHit = $"{hit.collider.name}(layer {hit.collider.gameObject.layer}) at {Fmt(hit.point)}";
        }

        /// <summary>(x, z) 위에서 아래로 쏜 첫 충돌(수면 4·Ignore Raycast 2·자기 캡슐 제외).</summary>
        bool TopSurface(Vector3 p, out RaycastHit best)
        {
            best = default;
            int mask = Physics.DefaultRaycastLayers & ~(1 << WalkController.WaterLayer);
            float bd = float.MaxValue;
            foreach (var h in Physics.RaycastAll(new Vector3(p.x, 3000f, p.z), Vector3.down, 6000f, mask, QueryTriggerInteraction.Ignore))
                if (h.collider != cc && h.distance < bd) { best = h; bd = h.distance; }
            return bd < float.MaxValue;
        }

        /// <summary>발밑 최상단 충돌체와 높이. 지면 아래로 빠졌으면 WARN.</summary>
        string GroundCheck()
        {
            var p = transform.position;
            if (!TopSurface(p, out var hit)) return " ground=none";
            float below = hit.point.y - p.y;
            return below > 2f ? $" WARN below_surface={below:0.0}m({hit.collider.name})" : $" agl={p.y - hit.point.y:0.00} on={hit.collider.name}";
        }

        float LandingTolerance()
        {
            var wc = GetComponent<WalkController>();
            return wc != null ? wc.buriedWaterTolerance : WalkController.DefaultBuriedWaterTolerance;
        }

        Vector2 XZ(Vector3 p) => new Vector2(p.x, p.z);
        string EN(Vector3 p) => string.Format(Inv, "({0:0.0},{1:0.0})", p.x + originE, p.z + originN);
        string ENxz(Vector2 xz) => string.Format(Inv, "({0:0.0},{1:0.0})", xz.x + originE, xz.y + originN);
        static string Fmt(Vector3 p) => string.Format(Inv, "({0:0.0},{1:0.0},{2:0.0})", p.x, p.y, p.z);
        static void Log(string msg) => Debug.Log($"{Tag} {msg}");
    }
}
