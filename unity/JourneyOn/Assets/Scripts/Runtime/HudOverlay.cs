using UnityEngine;
using UnityEngine.InputSystem;

namespace JourneyOn
{
    /// <summary>
    /// 화면 오버레이: 건물·교량 클릭 조회(화면 중앙 조준), 조작 안내, 데이터 출처 표시(크레딧).
    /// 한글 표시를 위해 OS 글꼴(맑은 고딕)을 쓴다.
    /// 테마(D-014) 값은 WorldImporter 가 채운다: cozy = 둥근 크림색 패널 + 짙은 갈색 글자(가독성 우선), realistic = 예전 흰 글자.
    /// </summary>
    public class HudOverlay : MonoBehaviour
    {
        [TextArea] public string[] credits;
        public bool interimNotice = true;
        public float maxPickDistance = 1500f;

        [Header("테마(WorldImporter 가 채운다)")]
        public bool roundedPanels;
        public Color panelColor = new Color(0f, 0f, 0f, 0f);
        public Color textColor = Color.white;
        public Color accentColor = Color.white;
        public float panelRadius = 10f;

        Camera cam;
        IPickInfo picked;
        MonoBehaviour pickedOwner;   // 파괴 여부 확인용
        float pickedUntil;
        GUIStyle box, small, guide;
        Font font;

        void Awake() => cam = GetComponentInChildren<Camera>() ?? Camera.main;

        void Update()
        {
            var mouse = Mouse.current;
            if (mouse == null || cam == null) return;
            bool locked = Cursor.lockState == CursorLockMode.Locked;
            if ((locked && mouse.leftButton.wasPressedThisFrame) || (!locked && mouse.rightButton.wasPressedThisFrame))
                TryPick(locked ? cam.ViewportPointToRay(new Vector3(0.5f, 0.5f)) : cam.ScreenPointToRay(mouse.position.ReadValue()));
        }

        /// <summary>화면 가운데(조준선)를 클릭한 것처럼 조회한다. 스모크 화면 저장(-smokeShot)이 HUD 가독성 확인에 쓴다.</summary>
        public bool PickAtCenter()
        {
            if (cam == null) cam = GetComponentInChildren<Camera>() ?? Camera.main;
            return cam != null && TryPick(cam.ViewportPointToRay(new Vector3(0.5f, 0.5f)));
        }

        bool TryPick(Ray ray)
        {
            // 수면(레이어 4)은 통과시켜 물 건너 건물·다리도 고를 수 있게 한다
            if (!Physics.Raycast(ray, out var hit, maxPickDistance, Physics.DefaultRaycastLayers & ~(1 << 4), QueryTriggerInteraction.Ignore)) return false;
            // 충돌체가 자식(glTFast 가 프리미티브를 나눈 경우)에 있어도 찾도록 부모까지 본다
            MonoBehaviour owner = hit.collider.GetComponentInParent<BuildingInfo>();
            if (owner == null) owner = hit.collider.GetComponentInParent<BridgeInfo>();
            if (owner is not IPickInfo info) return false;
            picked = info;
            pickedOwner = owner;
            pickedUntil = Time.time + 8f;
            return true;
        }

        const string GuideText = "WASD 이동 · 마우스 시점 · Shift 달리기 · Space 점프 · F 비행(E/Q 상하) · 클릭 건물·교량 정보 · Esc 커서";

        void EnsureStyles()
        {
            if (box != null) return;
            font = Font.CreateDynamicFontFromOSFont(new[] { "Malgun Gothic", "맑은 고딕", "Arial" }, 16);
            if (roundedPanels)
            {
                // 둥근 패널은 직접 그리므로 글자 스타일은 배경 없이 여백만 둔다
                box = new GUIStyle(GUIStyle.none) { font = font, fontSize = 16, alignment = TextAnchor.UpperLeft, wordWrap = true, padding = new RectOffset(18, 14, 12, 12) };
                box.normal.textColor = textColor;
                guide = new GUIStyle(GUIStyle.none) { font = font, fontSize = 14, wordWrap = false, padding = new RectOffset(14, 14, 7, 7) };
                guide.normal.textColor = textColor;
                small = new GUIStyle(GUIStyle.none) { font = font, fontSize = 12, wordWrap = true, padding = new RectOffset(12, 12, 6, 6) };
                small.normal.textColor = textColor;
            }
            else
            {
                box = new GUIStyle(GUI.skin.box) { font = font, fontSize = 16, alignment = TextAnchor.UpperLeft, wordWrap = true, padding = new RectOffset(12, 12, 10, 10) };
                box.normal.textColor = Color.white;
                small = new GUIStyle(GUI.skin.label) { font = font, fontSize = 12, wordWrap = true };
                small.normal.textColor = new Color(1, 1, 1, 0.85f);
                guide = small;
            }
        }

        void Panel(Rect r, Color c, float radius) =>
            GUI.DrawTexture(r, Texture2D.whiteTexture, ScaleMode.StretchToFill, true, 0f, c, 0f, radius);

        void OnGUI()
        {
            EnsureStyles();
            if (roundedPanels) DrawRounded();
            else DrawPlain();
        }

        void DrawPlain()
        {
            float w = Screen.width, h = Screen.height;
            if (Cursor.lockState == CursorLockMode.Locked)
                GUI.Label(new Rect(w / 2 - 6, h / 2 - 12, 20, 24), "+", small);

            if (picked != null && pickedOwner != null && Time.time < pickedUntil)
                GUI.Box(new Rect(w - 380, 20, 360, 170), picked.Describe(), box);

            GUI.Label(new Rect(16, 12, 720, 40), GuideText, small);

            string credit = CreditText();
            if (credit != null) GUI.Label(new Rect(16, h - 64, w - 32, 60), credit, small);
        }

        /// <summary>cozy: 둥근 크림색 패널 위에 짙은 글자. 패널 크기는 글자에 맞춘다.</summary>
        void DrawRounded()
        {
            float w = Screen.width, h = Screen.height;
            if (Cursor.lockState == CursorLockMode.Locked)
            {
                Panel(new Rect(w / 2 - 5, h / 2 - 5, 10, 10), new Color(panelColor.r, panelColor.g, panelColor.b, 0.95f), 5f);
                Panel(new Rect(w / 2 - 2.5f, h / 2 - 2.5f, 5, 5), textColor, 2.5f);
            }

            if (picked != null && pickedOwner != null && Time.time < pickedUntil)
            {
                var content = new GUIContent(picked.Describe());
                const float bw = 360f;
                float bh = box.CalcHeight(content, bw);
                var r = new Rect(w - bw - 20, 20, bw, bh);
                Panel(r, panelColor, panelRadius);
                Panel(new Rect(r.x + 7, r.y + 12, 4, r.height - 24), accentColor, 2f);
                GUI.Label(r, content, box);
            }

            var g = new GUIContent(GuideText);
            var gs = guide.CalcSize(g);
            var gr = new Rect(14, 12, Mathf.Min(gs.x, w - 28), gs.y);
            Panel(gr, panelColor, Mathf.Min(panelRadius, gs.y / 2));
            GUI.Label(gr, g, guide);

            string credit = CreditText();
            if (credit != null)
            {
                var c = new GUIContent(credit);
                float cw = Mathf.Min(small.CalcSize(c).x, w - 28);
                float ch = small.CalcHeight(c, cw);
                var cr = new Rect(14, h - ch - 12, cw, ch);
                Panel(cr, panelColor, panelRadius);
                GUI.Label(cr, c, small);
            }
        }

        string CreditText()
        {
            if (credits == null || credits.Length == 0) return null;
            string text = "데이터 출처: " + string.Join(" / ", credits);
            if (interimNotice) text += "\n※ 시연용 임시 데이터 포함(공식 국토정보 데이터로 교체 예정)";
            return text;
        }
    }
}
