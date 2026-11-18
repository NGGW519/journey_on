using UnityEngine;
using UnityEngine.InputSystem;

namespace JourneyOn
{
    /// <summary>
    /// 화면 오버레이: 건물·교량 클릭 조회(화면 중앙 조준), 조작 안내, 데이터 출처 표시(크레딧).
    /// 한글 표시를 위해 OS 글꼴(맑은 고딕)을 쓴다.
    /// </summary>
    public class HudOverlay : MonoBehaviour
    {
        [TextArea] public string[] credits;
        public bool interimNotice = true;
        public float maxPickDistance = 1500f;

        Camera cam;
        IPickInfo picked;
        MonoBehaviour pickedOwner;   // 파괴 여부 확인용
        float pickedUntil;
        GUIStyle box, small;
        Font font;

        void Awake() => cam = GetComponentInChildren<Camera>() ?? Camera.main;

        void Update()
        {
            var mouse = Mouse.current;
            if (mouse == null || cam == null) return;
            bool locked = Cursor.lockState == CursorLockMode.Locked;
            if ((locked && mouse.leftButton.wasPressedThisFrame) || (!locked && mouse.rightButton.wasPressedThisFrame))
            {
                Ray ray = locked ? cam.ViewportPointToRay(new Vector3(0.5f, 0.5f)) : cam.ScreenPointToRay(mouse.position.ReadValue());
                // 수면(레이어 4)은 통과시켜 물 건너 건물·다리도 고를 수 있게 한다
                if (Physics.Raycast(ray, out var hit, maxPickDistance, Physics.DefaultRaycastLayers & ~(1 << 4), QueryTriggerInteraction.Ignore))
                {
                    // 충돌체가 자식(glTFast 가 프리미티브를 나눈 경우)에 있어도 찾도록 부모까지 본다
                    MonoBehaviour owner = hit.collider.GetComponentInParent<BuildingInfo>();
                    if (owner == null) owner = hit.collider.GetComponentInParent<BridgeInfo>();
                    if (owner is IPickInfo info)
                    {
                        picked = info;
                        pickedOwner = owner;
                        pickedUntil = Time.time + 8f;
                    }
                }
            }
        }

        void EnsureStyles()
        {
            if (box != null) return;
            font = Font.CreateDynamicFontFromOSFont(new[] { "Malgun Gothic", "맑은 고딕", "Arial" }, 16);
            box = new GUIStyle(GUI.skin.box) { font = font, fontSize = 16, alignment = TextAnchor.UpperLeft, wordWrap = true, padding = new RectOffset(12, 12, 10, 10) };
            box.normal.textColor = Color.white;
            small = new GUIStyle(GUI.skin.label) { font = font, fontSize = 12, wordWrap = true };
            small.normal.textColor = new Color(1, 1, 1, 0.85f);
        }

        void OnGUI()
        {
            EnsureStyles();
            float w = Screen.width, h = Screen.height;
            if (Cursor.lockState == CursorLockMode.Locked)
                GUI.Label(new Rect(w / 2 - 6, h / 2 - 12, 20, 24), "+", small);

            if (picked != null && pickedOwner != null && Time.time < pickedUntil)
                GUI.Box(new Rect(w - 380, 20, 360, 170), picked.Describe(), box);

            GUI.Label(new Rect(16, 12, 720, 40),
                "WASD 이동 · 마우스 시점 · Shift 달리기 · Space 점프 · F 비행(E/Q 상하) · 클릭 건물·교량 정보 · Esc 커서", small);

            if (credits != null && credits.Length > 0)
            {
                string text = "데이터 출처: " + string.Join(" / ", credits);
                if (interimNotice) text += "\n※ 시연용 임시 데이터 포함(공식 국토정보 데이터로 교체 예정)";
                GUI.Label(new Rect(16, h - 64, w - 32, 60), text, small);
            }
        }
    }
}
