using System;

namespace JourneyOn.Editor
{
    // data/out/<area>/manifest.json, tile.json, buildings.json 의 JsonUtility 모델.
    // 스키마: .claude/skills/geo-pipeline/references/manifest-schema.md (manifest_version 1, 2)
    // JsonUtility 는 null 객체를 기본값 인스턴스로 채우므로 file/count/mask 로 존재 여부를 판단한다.
    // 중첩 배열(bridges.items[].footprint_xz)은 JsonUtility 가 못 읽으므로 MiniJson 으로 따로 읽는다.

    [Serializable] public class HeightmapSpec { public int resolution; public float base_m; public float range_m; public string byte_order; public string row_order; }
    [Serializable] public class OriginSpec { public double easting; public double northing; }
    [Serializable] public class TileRef { public string name; public int e0; public int n0; public float[] unity_position; public string dir; }
    [Serializable] public class ZoneSpec { public string name; public float[] bounds; }
    [Serializable] public class StatsSpec { public int tiles; public int buildings; }
    [Serializable] public class SpawnSpec { public float e; public float n; public float ground_m; public float yaw_deg; public float shore_distance_m; }

    /// <summary>용도 5종별 값(JSON 객체 {"residential": 3.0, ...}). 0 이면 값 없음.</summary>
    [Serializable]
    public class ClassValues
    {
        public float residential, commercial, industrial, other;
        public float @public;   // JSON 키 "public"

        public float Get(string cls, float fallback)
        {
            float v = cls switch
            {
                "residential" => residential,
                "commercial" => commercial,
                "public" => @public,
                "industrial" => industrial,
                _ => other,
            };
            return v > 0f ? v : fallback;
        }
    }

    /// <summary>v2 C1: 외벽 텍스처 한 장 = 창 한 칸(bay_m) × 한 층(floor_height_m).</summary>
    [Serializable] public class FacadeSpec { public ClassValues bay_m; public ClassValues floor_height_m; public float roof_tile_m; }

    /// <summary>
    /// (선택, 계약 밖) 수면 메시 설정. 지금 manifest 에는 없다. 있으면 WorldImporter 가 C3 교량 여유(shore_overlap_m + 0.5)에 쓴다.
    /// </summary>
    [Serializable] public class WaterSpec { public float shore_overlap_m; public float shore_clip_above_m; }

    /// <summary>v2 C4: 범위 밖 원경 지형(월드 좌표 glb 하나).</summary>
    [Serializable]
    public class SkirtSpec
    {
        public string file;
        public float[] bounds;
        public float margin_m;
        public float[] spacing_m;
        public float inner_overlap_m;
        public bool interim;
    }

    [Serializable]
    public class AreaManifest
    {
        public int manifest_version;
        public string area;
        public string title;
        public string crs;
        public OriginSpec origin;
        public int tile_size_m;
        public HeightmapSpec heightmap;
        public string axis_test;
        public ZoneSpec[] no_build_zones;
        public TileRef[] tiles;
        public SpawnSpec spawn;   // 파이프라인이 고른 육지 시작점(e == 0 이면 없음)
        public string[] interim_layers;
        public string[] attributions;
        public StatsSpec stats;
        public FacadeSpec facade; // v2 (없으면 기본값)
        public SkirtSpec skirt;   // v2 (file 이 비면 없음)
        public WaterSpec water;   // 선택(지금 없음). water.shore_overlap_m

        public bool HasSkirt => skirt != null && !string.IsNullOrEmpty(skirt.file);
    }

    [Serializable] public class DemInfo { public string file; public float min_m; public float max_m; }
    [Serializable] public class OrthoInfo { public string file; public int px; }
    [Serializable] public class BuildingsInfo { public int count; public string file; public string meta; }
    [Serializable] public class WaterInfo { public int count; public string file; public float level_m; }
    [Serializable] public class RoadsInfo { public string mask; public int segments; public bool interim; }

    [Serializable]
    public class BridgeItem
    {
        public string node, osm_id, kind, name;
        public float length_m, width_m, deck_min_m, deck_max_m;
        public float deck_thickness_m;   // 1차 보완: 상판 밑면 레이캐스트가 실패할 때만 쓴다
        // footprint_xz 는 MiniJson 으로 읽는다(중첩 배열)
    }

    [Serializable] public class BridgesInfo { public string file; public bool interim; public BridgeItem[] items; }

    [Serializable]
    public class TileJson
    {
        public string name;
        public int e0;
        public int n0;
        public int size_m;
        public DemInfo dem;
        public OrthoInfo ortho;
        public BuildingsInfo buildings;
        public WaterInfo water;
        public RoadsInfo roads;       // v2 C2
        public BridgesInfo bridges;   // v2 C3

        public bool HasRoads => roads != null && !string.IsNullOrEmpty(roads.mask);
        public bool HasBridges => bridges != null && !string.IsNullOrEmpty(bridges.file);
    }

    [Serializable]
    public class BuildingMeta
    {
        public string id, node, src_id, usage_class, usage_ko, usage_name, name, floors_source, address;
        public int floors;
        public float height_m, base_m, area_m2;
    }

    [Serializable] public class BuildingMetaList { public BuildingMeta[] items; }

    // ------------------------------------------------------------------ unity_import.json

    [Serializable] public class FacadeUvSample { public string node, usage_class; public int floors; public float height_m, expected_v_max, v_min, v_max; }

    /// <summary>계약 C1: 벽 submesh 의 uv.y 범위가 건물마다 [0, height_m ÷ floor_height_m(용도별)] 인지.</summary>
    [Serializable]
    public class FacadeUvCheck
    {
        public string status = "SKIPPED";   // PASS / FAIL / SKIPPED(벽·지붕 2 submesh 건물 없음, v1)
        public float tolerance = 0.02f;
        public int two_submesh, single_submesh, no_uv;
        public int @checked, ok, bad;
        public float max_err_min, max_err_max;
        public FacadeUvSample[] bad_samples = new FacadeUvSample[0];   // 최대 20개
    }

    /// <summary>2차-라: 교량 끝 진입 검사에서 물가 벽(레이어 2)에 막힌 끝 하나.</summary>
    [Serializable]
    public class BridgeAccessEnd
    {
        public string node, which;           // BR_…, start/end
        public float e, n;                   // 끝점 EPSG:5186
        public float hit_e, hit_n, hit_y;    // 막은 지점
        public float hit_dist_to_deck_m;     // 막은 지점 ↔ 상판 외곽 거리
        public string kind;                  // wall(물가 벽 레이어 2) / step(높이 단 > stepOffset)
        public float step_m;                 // step 일 때 단 높이(+ = 상판 쪽으로 오르막)
        public string cause;                 // wall: data(여유 밖 벽) / rule(여유 안 벽), step: 단 위치·높이
    }

    /// <summary>2차-라: 교량 끝마다 '육지 5 m 밖 → 상판 1 m 안'을 보행 높이(지면 + 0.5~1.5 m, 반지름 0.3 m)로 훑은 결과.</summary>
    [Serializable]
    public class BridgeAccess
    {
        public int items, ends_total, ends_land, ends_passed, ends_blocked, ends_blocked_wall, ends_blocked_step,
                   ends_water, ends_joint, ends_building, ends_unknown;
        public float step_limit_m;                       // = Player CharacterController.stepOffset
        public BridgeAccessEnd[] blocked = new BridgeAccessEnd[0];
        public string[] water_ends = new string[0];     // 바깥 5 m 점이 물 위(선착장 등) → 검사 제외
        public string[] joint_ends = new string[0];     // 다른 상판에 붙은 끝(끝점이 다른 footprint 1 m 안 또는 바깥 5 m 점이 다른 상판) → 연결부, 검사 제외
        public string[] building_ends = new string[0];  // 바깥 5 m 점이 건물(지붕) 위 → 육지 아님, 검사 제외
        public string[] unknown_nodes = new string[0];  // footprint 링에서 끝을 못 찾음
    }

    [Serializable]
    public class ImportReport
    {
        public string area;
        public int manifest_version;
        public int manifest_tiles, terrains, building_nodes, building_meta, buildings_with_info, water_tiles;
        public bool ortho_used;
        public float axis_east_extent_x, axis_east_extent_z;
        public bool axis_ok;
        // v2
        public int roads_tiles;
        public int bridge_nodes, bridge_items, bridge_footprints;
        // 물가 벽(C3): total = 타일 경계 변을 뺀 바깥 테두리 변. bridge_near = 중점이 교량 footprint + 1.5 m 안인 변.
        // 그중 lowered = 윗면을 상판 밑면 − 0.1 m 로 낮춘 변, removed = 낮춘 윗면이 수면 + 0.3 m 이하라 뺀 변,
        // high = 밑면이 높아 기본 높이(수면 + 4 m)를 그대로 둔 변, unresolved = 교량 충돌체를 못 찾아 기본 높이로 둔 변.
        // thickness_fallback = 위쪽 레이캐스트가 실패해 '윗면 − deck_thickness_m' 로 밑면을 정한 변(lowered·removed·high 에 함께 셈).
        public int shore_edges_total, shore_edges_bridge_near, shore_edges_bridge_lowered, shore_edges_bridge_removed,
                   shore_edges_bridge_high, shore_edges_bridge_unresolved, shore_edges_bridge_thickness_fallback;
        // 2차-라: 상판 밖 교량 변 중 끝 구역(낮춤 대상) / 옆면(기본 높이 유지) 수, 교량 여유와 그 출처
        public int shore_edges_bridge_end, shore_edges_bridge_side_kept;
        public float bridge_margin_m;
        public string bridge_margin_source;
        public float water_shore_clip_above_m;   // manifest water.shore_clip_above_m(있을 때)
        public float landing_tolerance_m;        // WalkController.buriedWaterTolerance = shore_clip_above_m(없으면 0.3)
        public BridgeAccess bridge_access = new BridgeAccess();
        public int skirt_nodes, skirt_water_nodes;
        public bool skirt_vertex_color;
        // 원경 색(05b-나): 정점색을 범위 안 지면 평균 알베도 색조로 다시 칠한다. 모두 선형 RGB.
        public float[] ground_albedo_linear = new float[0];          // 범위 안 지면 평균 알베도(레이어 텍스처 평균 × 알파맵 덮임)
        public float[] ground_layer_coverage = new float[0];         // [바탕, 아스팔트, 보도, 자갈] 평균 덮임
        public float[] skirt_source_mean_linear = new float[0];      // 경계 밖 0~1.5 km 원래 정점색 평균
        public float[] skirt_recolor_target_linear = new float[0];   // 다시 칠한 색조(= ground_albedo_linear)
        public float skirt_recolor_variation;                        // 밝기 변화 유지 비율
        public FacadeUvCheck facade_uv_check = new FacadeUvCheck();
        public float heightmap_pixel_error, camera_far_clip, fog_density;
        public string[] warnings;
        public string scene;
        public string imported_at;
    }
}
