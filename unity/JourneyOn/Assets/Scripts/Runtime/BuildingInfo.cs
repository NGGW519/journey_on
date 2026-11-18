using UnityEngine;

namespace JourneyOn
{
    /// <summary>건물 한 동의 속성(용도·층수·주소). WorldImporter 가 buildings.json 에서 채운다.</summary>
    public class BuildingInfo : MonoBehaviour, IPickInfo
    {
        public string id;
        public string sourceId;
        public string usageClass;     // residential / commercial / public / industrial / other
        public string usageKo;        // 주거 / 상업·업무 / 공공 / 공업 / 기타
        public string usageName;      // 원천 용도명(예: 공동주택, house)
        public string buildingName;
        public int floors;
        public string floorsSource;   // source / default / invalid
        public float heightM;
        public float baseM;
        public string address;
        public float areaM2;

        public string Describe()
        {
            var sb = new System.Text.StringBuilder();
            if (!string.IsNullOrEmpty(buildingName)) sb.AppendLine(buildingName);
            sb.AppendLine($"용도: {usageKo} ({usageName})");
            sb.AppendLine(floorsSource == "source" ? $"층수: {floors}층" : $"층수: {floors}층 (정보 없음, 기본값)");
            sb.AppendLine($"높이: {heightM:0.0} m · 바닥 해발 {baseM:0.0} m");
            if (!string.IsNullOrEmpty(address)) sb.AppendLine($"주소: {address}");
            sb.Append($"면적: {areaM2:0} m²");
            return sb.ToString();
        }
    }
}
