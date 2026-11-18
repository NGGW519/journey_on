using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;

namespace JourneyOn.Editor
{
    /// <summary>ShotCapture·DemoCapture 공통: 씬 열기, 좌표 변환, 지면 높이, 렌더 → Texture2D.</summary>
    public static class CaptureCommon
    {
        public static string RepoRoot => Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", ".."));

        public static string Arg(string[] a, string k)
        {
            int i = Array.IndexOf(a, k);
            return i >= 0 && i + 1 < a.Length ? a[i + 1] : null;
        }

        public static bool Flag(string[] a, string k) => Array.IndexOf(a, k) >= 0;

        public class Area
        {
            public AreaManifest m;
            public Dictionary<(int, int), Terrain> terrains = new();

            public Vector3 ToUnity(double e, double n, float y) => new((float)(e - m.origin.easting), y, (float)(n - m.origin.northing));
            public float Ground(float x, float z) => WorldImporter.GroundHeight(m, terrains, x, z);
            public float Ground(Vector3 p) => Ground(p.x, p.z);
        }

        /// <summary>Assets/Scenes/&lt;area&gt;.unity 를 연다(저장하지 않는다).</summary>
        public static Area OpenArea(string area)
        {
            EditorSceneManager.OpenScene($"Assets/Scenes/{area}.unity", OpenSceneMode.Single);
            var a = new Area { m = LoadManifestWithRetry(area) };
            foreach (var t in UnityEngine.Object.FindObjectsByType<Terrain>(FindObjectsSortMode.None))
            {
                var p = t.transform.position;
                a.terrains[((int)Math.Round(p.x + a.m.origin.easting), (int)Math.Round(p.z + a.m.origin.northing))] = t;
            }
            return a;
        }

        /// <summary>파이프라인이 manifest 를 다시 쓰는 중이면 읽기가 실패할 수 있어 몇 번 기다렸다 다시 읽는다.</summary>
        public static AreaManifest LoadManifestWithRetry(string area, int tries = 6, int waitMs = 10000)
        {
            for (int i = 1; ; i++)
            {
                try { return WorldImporter.LoadManifest(WorldImporter.DefaultDataRoot(), area); }
                catch (Exception e) when (i < tries)
                {
                    Debug.LogWarning($"[Capture] manifest 읽기 실패({i}/{tries}): {e.Message} → {waitMs / 1000}s 뒤 다시");
                    System.Threading.Thread.Sleep(waitMs);
                }
            }
        }

        /// <summary>렌더 전 지형 basemap 거리를 바꾼다(씬은 저장하지 않는다). 음수면 그대로.</summary>
        public static void OverrideBasemapDistance(float d)
        {
            if (d < 0f) return;
            foreach (var t in UnityEngine.Object.FindObjectsByType<Terrain>(FindObjectsSortMode.None)) t.basemapDistance = d;
            Debug.Log($"[Capture] Terrain.basemapDistance = {d} (이번 렌더만)");
        }

        public static Camera CreateCamera(Area a, string name, float fov = 60f)
        {
            var go = new GameObject(name);
            var cam = go.AddComponent<Camera>();
            cam.nearClipPlane = 0.3f;
            cam.farClipPlane = WorldImporter.FarClip(a.m, 12000f);
            cam.fieldOfView = fov;
            return cam;
        }

        /// <summary>URP 렌더 요청으로 rt 에 그린 뒤 tex 로 읽는다.</summary>
        public static void Render(Camera cam, RenderTexture rt, Texture2D tex)
        {
            cam.targetTexture = rt;
            var req = new RenderPipeline.StandardRequest { destination = rt };
            if (RenderPipeline.SupportsRenderRequest(cam, req)) RenderPipeline.SubmitRenderRequest(cam, req);
            else cam.Render();
            var prev = RenderTexture.active;
            RenderTexture.active = rt;
            tex.ReadPixels(new Rect(0, 0, rt.width, rt.height), 0, 0);
            tex.Apply(false);
            RenderTexture.active = prev;
        }

        /// <summary>렌더 결과에 회색 대체 셰이더가 찍히지 않게 셰이더를 동기 컴파일한다. 반환값으로 되돌린다.</summary>
        public static bool DisableAsyncShaders()
        {
            bool prev = ShaderUtil.allowAsyncCompilation;
            ShaderUtil.allowAsyncCompilation = false;
            return prev;
        }
    }
}
