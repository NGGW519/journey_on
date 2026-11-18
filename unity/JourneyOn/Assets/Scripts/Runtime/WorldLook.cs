using UnityEngine;

namespace JourneyOn
{
    /// <summary>
    /// 씬에 적용된 아트 테마 표시(D-014). WorldImporter 가 Look 오브젝트(전역 후처리 Volume 과 같은 오브젝트)에 기록하고,
    /// 검수 스크린샷·시연 영상 캡처가 읽어 같은 테마(후처리·틸트시프트·자막 색)로 찍는다. 실행 중 동작은 없다.
    /// </summary>
    public class WorldLook : MonoBehaviour
    {
        public string theme = "realistic";
        public bool postProcessing;
    }
}
