using UnityEngine;

namespace JourneyOn
{
    /// <summary>HUD 클릭 조회 대상(건물·교량)이 공통으로 구현한다.</summary>
    public interface IPickInfo
    {
        string Describe();
    }

    /// <summary>교량 한 개의 속성(계약 C3). WorldImporter 가 tile.json bridges.items 에서 채운다.</summary>
    public class BridgeInfo : MonoBehaviour, IPickInfo
    {
        public string node;           // BR_<osm_id>
        public string osmId;
        public string kind;           // road / foot / rail
        public string bridgeName;
        public float lengthM;
        public float widthM;
        public float deckMinM;
        public float deckMaxM;
        public bool interim = true;

        public string KindKo => kind switch
        {
            "road" => "도로교",
            "foot" => "보행교",
            "rail" => "철도교",
            _ => "교량",
        };

        public string Describe()
        {
            var sb = new System.Text.StringBuilder();
            sb.AppendLine(string.IsNullOrEmpty(bridgeName) ? $"{KindKo} (이름 정보 없음)" : bridgeName);
            sb.AppendLine($"종류: {KindKo}");
            sb.AppendLine($"길이: {lengthM:0} m · 폭 {widthM:0.0} m");
            if (deckMaxM > 0f) sb.AppendLine($"상판 높이: 해발 {deckMinM:0.0}–{deckMaxM:0.0} m");
            sb.Append(interim ? $"출처: OpenStreetMap {osmId} (임시 데이터)" : $"출처: {osmId}");
            return sb.ToString();
        }
    }
}
