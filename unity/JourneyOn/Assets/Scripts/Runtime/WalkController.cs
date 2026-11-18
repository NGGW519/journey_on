using UnityEngine;
using UnityEngine.InputSystem;

namespace JourneyOn
{
    /// <summary>
    /// 보행 시점 자유 이동. WASD 이동, 마우스 시점, Shift 달리기, Space 점프.
    /// F 키로 비행 모드(시연·촬영용)를 켜고 끈다. Esc 로 커서 잠금 해제, 클릭으로 다시 잠금.
    /// </summary>
    [RequireComponent(typeof(CharacterController))]
    public class WalkController : MonoBehaviour
    {
        public Transform cameraPivot;
        public float walkSpeed = 4f;
        public float runSpeed = 10f;
        public float flySpeed = 60f;
        public float mouseSensitivity = 0.12f;
        public float jumpSpeed = 5f;
        public float gravity = -20f;
        [Tooltip("비행 모드에서도 벗어나지 않는 범위(X, Z). WorldImporter 가 채운다.")]
        public Vector2 areaMin = new Vector2(-1e6f, -1e6f);
        public Vector2 areaMax = new Vector2(1e6f, 1e6f);
        public float maxFlyHeight = 2500f;

        CharacterController cc;
        float yaw, pitch, vy;
        bool flying;

        public const int WaterLayer = 4;
        string notice;
        float noticeUntil;

        void Awake()
        {
            cc = GetComponent<CharacterController>();
            Physics.IgnoreLayerCollision(gameObject.layer, WaterLayer, true);   // 수면 충돌체는 걷기를 막지 않는다
            if (cameraPivot == null && Camera.main != null) cameraPivot = Camera.main.transform;
            yaw = transform.eulerAngles.y;
        }

        void Start() => SetCursorLocked(true);

        void SetCursorLocked(bool locked)
        {
            Cursor.lockState = locked ? CursorLockMode.Locked : CursorLockMode.None;
            Cursor.visible = !locked;
        }

        void Update()
        {
            var kb = Keyboard.current;
            var mouse = Mouse.current;
            if (kb == null || mouse == null) return;

            if (kb.escapeKey.wasPressedThisFrame) SetCursorLocked(false);
            if (mouse.leftButton.wasPressedThisFrame && Cursor.lockState != CursorLockMode.Locked) SetCursorLocked(true);
            if (kb.fKey.wasPressedThisFrame)
            {
                if (flying && OverWater())
                {
                    // 물 위에서 걷기로 바꾸면 하상으로 떨어져 물가 벽 안에 갇힌다 → 육지에서만 착지
                    notice = "물 위에서는 착지할 수 없습니다. 육지 위에서 F 를 누르세요.";
                    noticeUntil = Time.time + 2.5f;
                }
                else { flying = !flying; vy = 0f; cc.enabled = !flying; }
            }

            if (Cursor.lockState == CursorLockMode.Locked)
            {
                Vector2 d = mouse.delta.ReadValue() * mouseSensitivity;
                yaw += d.x;
                pitch = Mathf.Clamp(pitch - d.y, -85f, 85f);
            }
            transform.rotation = Quaternion.Euler(0f, yaw, 0f);
            if (cameraPivot != null) cameraPivot.localRotation = Quaternion.Euler(pitch, 0f, 0f);

            Vector3 input = Vector3.zero;
            if (kb.wKey.isPressed) input.z += 1;
            if (kb.sKey.isPressed) input.z -= 1;
            if (kb.dKey.isPressed) input.x += 1;
            if (kb.aKey.isPressed) input.x -= 1;
            input = Vector3.ClampMagnitude(input, 1f);
            bool run = kb.leftShiftKey.isPressed;

            if (flying)
            {
                Vector3 dir = cameraPivot != null ? cameraPivot.rotation * input : transform.rotation * input;
                if (kb.eKey.isPressed) dir += Vector3.up;
                if (kb.qKey.isPressed) dir += Vector3.down;
                Vector3 p = transform.position + dir * flySpeed * (run ? 4f : 1f) * Time.deltaTime;
                p.x = Mathf.Clamp(p.x, areaMin.x, areaMax.x);
                p.z = Mathf.Clamp(p.z, areaMin.y, areaMax.y);
                p.y = Mathf.Min(p.y, maxFlyHeight);
                // 지면·건물 아래로 내려가지 않게
                if (Physics.Raycast(new Vector3(p.x, maxFlyHeight + 100f, p.z), Vector3.down, out var hit, maxFlyHeight + 1000f))
                    p.y = Mathf.Max(p.y, hit.point.y + 1.5f);
                transform.position = p;
                return;
            }

            Vector3 move = transform.rotation * input * (run ? runSpeed : walkSpeed);
            Walk(move);
        }

        /// <summary>manifest 에 water.shore_clip_above_m 이 없을 때 쓰는 기본 허용치.</summary>
        public const float DefaultBuriedWaterTolerance = 0.3f;

        [Tooltip("수면이 첫 지면보다 이만큼 넘게 낮으면 육지로 본다. WorldImporter 가 manifest water.shore_clip_above_m(수면 메시 자르기 기준)으로 채운다")]
        public float buriedWaterTolerance = DefaultBuriedWaterTolerance;

        /// <summary>착지 금지 판정(비행 중 F). <see cref="LandingAllowed"/> 의 반대.</summary>
        public bool OverWater() => !LandingAllowed(transform.position + Vector3.up * 5f, transform, buriedWaterTolerance);

        /// <summary>
        /// 높이 비교 착지 판정. origin 에서 아래로:
        /// 1) 일반 레이어(수면 4·Ignore Raycast 2 제외)의 첫 지면·건물·교량 높이 g,
        /// 2) 수면 레이어(4)만의 수면 높이 w.
        /// - w 가 없으면 허용(수면 메시 밖 = 물가 벽 고리 밖).
        /// - g 가 교량(BridgeInfo)이면 허용(상판 위).
        /// - g 가 건물(BuildingInfo)이면 막는다(수면 메시 위 건물 = 수상 건물 지붕, 내리면 물가 벽 고리 안, 3차-나).
        /// - w &lt; g − tolerance 이면 허용(수면이 지형 아래). tolerance 는 수면 메시를 자른 기준(manifest water.shore_clip_above_m)과
        ///   같게 둔다. 그러면 메시 안(지형 − 수면 &lt; 기준)에서는 언제나 막힌다.
        /// self 아래 충돌체(플레이어 캡슐)는 무시한다. SmokeProbe 로그와 교량 진입 검사도 같은 함수를 쓴다.
        /// </summary>
        public static bool LandingAllowed(Vector3 origin, Transform self = null, float tolerance = DefaultBuriedWaterTolerance)
        {
            int groundMask = Physics.DefaultRaycastLayers & ~(1 << WaterLayer);   // DefaultRaycastLayers 는 이미 레이어 2 제외
            bool hasG = FirstHit(origin, groundMask, self, out var g);
            bool hasW = FirstHit(origin, 1 << WaterLayer, self, out var w);
            if (!hasW) return true;
            if (!hasG) return false;
            if (g.collider.GetComponentInParent<BridgeInfo>() != null) return true;
            if (g.collider.GetComponentInParent<BuildingInfo>() != null) return false;
            return w.point.y < g.point.y - tolerance;
        }

        static bool FirstHit(Vector3 origin, int mask, Transform self, out RaycastHit best)
        {
            best = default;
            float bestDist = float.MaxValue;
            foreach (var h in Physics.RaycastAll(origin, Vector3.down, 5000f, mask, QueryTriggerInteraction.Ignore))
            {
                if (self != null && h.collider.transform.IsChildOf(self)) continue;
                if (h.distance < bestDist) { best = h; bestDist = h.distance; }
            }
            return bestDist < float.MaxValue;
        }

        void OnGUI()
        {
            if (notice != null && Time.time < noticeUntil)
                GUI.Label(new Rect(Screen.width / 2 - 220, Screen.height * 0.7f, 440, 30), notice);
        }

        void Walk(Vector3 move)
        {
            if (cc.isGrounded)
            {
                vy = -1f;
                if (Keyboard.current != null && Keyboard.current.spaceKey.wasPressedThisFrame) vy = jumpSpeed;
            }
            vy += gravity * Time.deltaTime;
            move.y = vy;
            cc.Move(move * Time.deltaTime);
        }
    }
}
