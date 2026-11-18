using System;
using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

namespace JourneyOn.Editor
{
    /// <summary>
    /// 테마(<see cref="Theme"/>)의 조명·하늘·안개·후처리·렌더러 설정·플레이어 카메라·HUD 를 열린 씬과 프로젝트 에셋에 적용한다.
    /// 임포트와 RefreshLook 이 같은 함수를 쓴다. 값은 매번 코드 값으로 덮어쓴다(05b-나).
    ///  - 씬: Sun(Directional Light), RenderSettings(안개·환경광·하늘), Look(전역 Volume + WorldLook), Player 카메라·HUD
    ///  - 에셋: Materials/Sky.mat, Settings/Look/PostFX_&lt;theme&gt;.asset, 렌더 파이프라인 에셋(그림자 거리)·렌더러(SSAO)
    /// 틸트시프트는 캡처 도구(ShotCapture·DemoCapture)만 쓴다(<see cref="TiltShift"/>).
    /// </summary>
    public static class ThemeApply
    {
        const string MaterialRoot = "Assets/Materials";
        const string LookDir = "Assets/Settings/Look";
        public const string LookObjectName = "Look";
        public const string SkyGradientShader = "JourneyOn/SkyGradient";
        const string TiltShiftShader = "Hidden/JourneyOn/TiltShift";

        // ================================================================== 조명·하늘·안개
        /// <summary>해·안개·환경광·하늘·후처리 Volume·렌더러 설정. 반환 = 안개 밀도.</summary>
        public static float ApplyEnvironment(AreaManifest m, Theme t)
        {
            var sun = UnityEngine.Object.FindObjectsByType<Light>(FindObjectsSortMode.None).FirstOrDefault(l => l.type == LightType.Directional);
            if (sun == null) sun = new GameObject("Sun").AddComponent<Light>();
            sun.type = LightType.Directional;
            sun.name = "Sun";
            sun.transform.rotation = Quaternion.Euler(t.sunEuler);
            sun.intensity = t.sunIntensity;
            sun.color = t.sunColor;
            sun.shadows = LightShadows.Soft;
            sun.shadowStrength = t.shadowStrength;
            EditorUtility.SetDirty(sun);
            RenderSettings.sun = sun;

            RenderSettings.fog = true;
            RenderSettings.fogMode = FogMode.Exponential;
            RenderSettings.fogDensity = WorldImporter.FogDensity(m) * t.fogDensityScale;
            RenderSettings.fogColor = t.fogColor;
            RenderSettings.ambientMode = t.ambientMode;
            RenderSettings.ambientIntensity = t.ambientIntensity;
            if (t.ambientMode == AmbientMode.Trilight)
            {
                RenderSettings.ambientSkyColor = t.ambientSky;
                RenderSettings.ambientEquatorColor = t.ambientEquator;
                RenderSettings.ambientGroundColor = t.ambientGround;
            }
            RenderSettings.defaultReflectionMode = DefaultReflectionMode.Skybox;
            RenderSettings.reflectionIntensity = 1f;
            // 범위 밖 지평선 아래가 기본 갈색으로 보이지 않게, 하늘의 지면색을 안개색 계열로 맞춘다
            RenderSettings.skybox = EnsureSkyMaterial(t, sun);
            DynamicGI.UpdateEnvironment();

            ApplyLookObject(t);
            ApplyRenderPipeline(t);
            return RenderSettings.fogDensity;
        }

        /// <summary>
        /// Materials/Sky.mat. cozy = JourneyOn/SkyGradient(지평선 = 안개색이라 원경이 하늘에 이어진다), realistic = Skybox/Procedural(예전 값).
        /// 셰이더만 바꾸고 에셋은 그대로라 GUID·씬 참조가 유지된다.
        /// </summary>
        static Material EnsureSkyMaterial(Theme t, Light sun)
        {
            WorldImporter.EnsureFolder(MaterialRoot);
            string skyPath = MaterialRoot + "/Sky.mat";
            var shader = Shader.Find(t.gradientSky ? SkyGradientShader : "Skybox/Procedural");
            if (shader == null) throw new Exception($"하늘 셰이더 없음: {(t.gradientSky ? SkyGradientShader : "Skybox/Procedural")}");
            var sky = AssetDatabase.LoadAssetAtPath<Material>(skyPath);
            if (sky == null) { sky = new Material(shader) { name = "Sky" }; AssetDatabase.CreateAsset(sky, skyPath); }
            else if (sky.shader != shader) sky.shader = shader;
            if (t.gradientSky)
            {
                sky.SetColor("_TopColor", t.skyTop);
                sky.SetColor("_MidColor", t.skyMid);
                sky.SetColor("_HorizonColor", t.fogColor);
                sky.SetColor("_GroundColor", t.skyGround);
                sky.SetFloat("_MidHeight", t.skyMidHeight);
                sky.SetColor("_SunColor", t.skySunGlow);
                sky.SetFloat("_SunSize", t.skySunSize);
                sky.SetFloat("_SunGlow", t.skyGlow);
                sky.SetVector("_SunDir", -sun.transform.forward);
            }
            else
            {
                sky.SetColor("_GroundColor", t.skyGround);
                sky.SetColor("_SkyTint", t.skyTint);
                sky.SetFloat("_AtmosphereThickness", t.skyAtmosphere);
                sky.SetFloat("_Exposure", t.skyExposure);
                sky.SetFloat("_SunSize", t.skySunSize);
            }
            EditorUtility.SetDirty(sky);
            return sky;
        }

        // ================================================================== 후처리(전역 Volume)
        /// <summary>Look 오브젝트: 전역 Volume(테마 프로필) + WorldLook(테마 이름). realistic 은 Volume 을 끈다.</summary>
        static void ApplyLookObject(Theme t)
        {
            var go = GameObject.Find(LookObjectName) ?? new GameObject(LookObjectName);
            if (!go.TryGetComponent<WorldLook>(out var look)) look = go.AddComponent<WorldLook>();
            look.theme = t.name;
            look.postProcessing = t.post;
            if (!go.TryGetComponent<Volume>(out var vol)) vol = go.AddComponent<Volume>();
            vol.isGlobal = true;
            vol.priority = 0f;
            vol.weight = 1f;
            vol.sharedProfile = t.post ? EnsurePostProfile(t) : null;
            vol.enabled = t.post;
            EditorUtility.SetDirty(look);
            EditorUtility.SetDirty(vol);
        }

        static T Comp<T>(VolumeProfile p) where T : VolumeComponent
        {
            if (!p.TryGet(out T c))
            {
                c = p.Add<T>(false);
                c.name = typeof(T).Name;
                c.hideFlags = HideFlags.HideInInspector | HideFlags.HideInHierarchy;
                AssetDatabase.AddObjectToAsset(c, p);
            }
            c.active = true;
            EditorUtility.SetDirty(c);
            return c;
        }

        /// <summary>
        /// Settings/Look/PostFX_&lt;theme&gt;.asset: 톤매핑, 색 보정(노출·대비·채도), 화이트밸런스(따뜻하게), 약한 블룸, 약한 비네트.
        /// 렌더 파이프라인 기본 프로필(SampleSceneProfile)의 같은 항목을 모두 덮어쓰므로 결과가 기본 프로필에 기대지 않는다.
        /// </summary>
        static VolumeProfile EnsurePostProfile(Theme t)
        {
            WorldImporter.EnsureFolder(LookDir);
            string path = $"{LookDir}/PostFX_{t.name}.asset";
            var p = AssetDatabase.LoadAssetAtPath<VolumeProfile>(path);
            if (p == null) { p = ScriptableObject.CreateInstance<VolumeProfile>(); AssetDatabase.CreateAsset(p, path); }

            var tm = Comp<Tonemapping>(p);
            tm.mode.Override(t.tonemapping);

            var ca = Comp<ColorAdjustments>(p);
            ca.postExposure.Override(t.postExposure);
            ca.contrast.Override(t.contrast);
            ca.saturation.Override(t.saturation);
            ca.colorFilter.Override(Color.white);
            ca.hueShift.Override(0f);

            var wb = Comp<WhiteBalance>(p);
            wb.temperature.Override(t.temperature);
            wb.tint.Override(t.tint);

            var bl = Comp<Bloom>(p);
            bl.threshold.Override(t.bloomThreshold);
            bl.intensity.Override(t.bloomIntensity);
            bl.scatter.Override(t.bloomScatter);
            bl.tint.Override(Color.white);
            bl.clamp.Override(65472f);
            bl.highQualityFiltering.Override(false);
            bl.dirtIntensity.Override(0f);

            var vg = Comp<Vignette>(p);
            vg.color.Override(t.vignetteColor);
            vg.center.Override(new Vector2(0.5f, 0.5f));
            vg.intensity.Override(t.vignette);
            vg.smoothness.Override(t.vignetteSmoothness);
            vg.rounded.Override(false);

            // 피사계 심도는 Volume 으로 걸지 않는다(틸트시프트는 캡처 도구만, 플레이어 카메라 제외)
            if (p.TryGet(out DepthOfField dof)) { dof.active = false; EditorUtility.SetDirty(dof); }
            EditorUtility.SetDirty(p);
            return p;
        }

        // ================================================================== 렌더 파이프라인 에셋·렌더러(SSAO)
        /// <summary>
        /// 쓰는 URP 에셋(기본 + 현재 품질 단계)의 그림자 거리와, 그 렌더러들의 SSAO 기능(없으면 넣는다) 값을 테마 값으로 맞춘다.
        /// realistic = URP 템플릿 값(그림자 50 m, SSAO 0.4/0.3 m).
        /// </summary>
        static void ApplyRenderPipeline(Theme t)
        {
            var assets = new HashSet<UniversalRenderPipelineAsset>();
            if (GraphicsSettings.defaultRenderPipeline is UniversalRenderPipelineAsset d) assets.Add(d);
            if (QualitySettings.renderPipeline is UniversalRenderPipelineAsset q) assets.Add(q);
            foreach (var a in assets)
            {
                a.shadowDistance = t.shadowDistance;
                EditorUtility.SetDirty(a);
                foreach (var rd in a.rendererDataList.ToArray())
                    if (rd != null) EnsureSsao(rd, t);
            }
        }

        static void EnsureSsao(ScriptableRendererData rd, Theme t)
        {
            var ssao = rd.rendererFeatures.OfType<ScreenSpaceAmbientOcclusion>().FirstOrDefault();
            if (ssao == null)
            {
                ssao = ScriptableObject.CreateInstance<ScreenSpaceAmbientOcclusion>();
                ssao.name = "ScreenSpaceAmbientOcclusion";
                AssetDatabase.AddObjectToAsset(ssao, rd);
                rd.rendererFeatures.Add(ssao);
                var rso = new SerializedObject(rd);
                var map = rso.FindProperty("m_RendererFeatureMap");
                if (map != null && AssetDatabase.TryGetGUIDAndLocalFileIdentifier(ssao, out _, out long id))
                {
                    map.arraySize++;
                    map.GetArrayElementAtIndex(map.arraySize - 1).longValue = id;
                    rso.ApplyModifiedPropertiesWithoutUndo();
                }
                Debug.Log($"[ThemeApply] {rd.name} 에 SSAO 렌더러 기능을 넣었다");
            }
            ssao.SetActive(t.ssao);
            var so = new SerializedObject(ssao);
            void F(string n, float v) { var sp = so.FindProperty("m_Settings." + n); if (sp != null) sp.floatValue = v; }
            void I(string n, int v) { var sp = so.FindProperty("m_Settings." + n); if (sp != null) sp.intValue = v; }
            void B(string n, bool v) { var sp = so.FindProperty("m_Settings." + n); if (sp != null) sp.boolValue = v; }
            F("Intensity", t.ssaoIntensity);
            F("Radius", t.ssaoRadius);
            F("Falloff", t.ssaoFalloff);
            F("DirectLightingStrength", t.ssaoDirect);
            I("Source", 1);         // DepthNormals
            I("NormalSamples", 1);  // Medium
            I("Samples", 1);        // Medium(8)
            I("BlurQuality", 0);    // High(양방향)
            I("AOMethod", 0);       // BlueNoise
            B("Downsample", false);
            B("AfterOpaque", false);
            so.ApplyModifiedPropertiesWithoutUndo();
            EditorUtility.SetDirty(ssao);
            EditorUtility.SetDirty(rd);
        }

        // ================================================================== 카메라·HUD
        /// <summary>Player 카메라: 후처리(테마), 안티에일리어싱(SMAA), 디더링. HudOverlay: 둥근 파스텔 패널·글자색.</summary>
        public static void ApplyPlayerLook(Theme t)
        {
            var player = GameObject.Find("Player");
            if (player == null) return;
            var cam = player.GetComponentInChildren<Camera>(true);
            if (cam != null)
            {
                var data = cam.GetUniversalAdditionalCameraData();
                data.renderPostProcessing = t.post;
                data.antialiasing = t.post ? t.playerAntialiasing : AntialiasingMode.None;
                data.antialiasingQuality = AntialiasingQuality.Medium;
                data.dithering = t.post;
                data.renderShadows = true;
                EditorUtility.SetDirty(data);
                EditorUtility.SetDirty(cam);
            }
            if (player.TryGetComponent<HudOverlay>(out var hud))
            {
                hud.roundedPanels = t.hudRounded;
                hud.panelColor = t.hudPanel;
                hud.textColor = t.hudText;
                hud.accentColor = t.hudAccent;
                hud.panelRadius = t.hudRadius;
                EditorUtility.SetDirty(hud);
            }
        }

        /// <summary>캡처 카메라(ShotCapture·DemoCapture): 씬 테마에 맞춰 후처리를 켠다. 안티에일리어싱은 RT MSAA 가 맡는다.</summary>
        public static void ConfigureCaptureCamera(Camera cam, Theme t)
        {
            var data = cam.GetUniversalAdditionalCameraData();
            data.renderPostProcessing = t.post;
            data.dithering = t.post;
            data.antialiasing = AntialiasingMode.None;
            data.renderShadows = true;
        }

        // ================================================================== 틸트시프트(캡처 전용)
        static Material tiltMat;

        /// <summary>
        /// 화면 띠 틸트시프트: 초점 높이(바라보는 점의 화면 y)에서 band 안은 선명, 그 밖은 ramp 동안 흐림이 커져 최대 반경에 이른다.
        /// 가로·세로 두 번 흐린 임시 RT 를 돌려준다(호출자가 RenderTexture.ReleaseTemporary). strength ≤ 0 이면 null.
        /// URP 피사계 심도(Bokeh)는 최대 반경이 14 px 로 묶이고 초점 거리 수 km 에서 거의 흐려지지 않아 조감에 맞지 않는다.
        /// </summary>
        public static RenderTexture TiltShift(RenderTexture src, Theme t, float strength, float focusY)
        {
            if (strength <= 0.001f || !t.tiltShift) return null;
            if (tiltMat == null)
            {
                var sh = Shader.Find(TiltShiftShader);
                if (sh == null) { Debug.LogWarning($"[ThemeApply] {TiltShiftShader} 없음 → 틸트시프트 생략"); return null; }
                tiltMat = new Material(sh) { hideFlags = HideFlags.HideAndDontSave };
            }
            tiltMat.SetVector("_Tilt", new Vector4(focusY, t.tiltBand, t.tiltRamp, t.tiltMaxRadius * src.height * strength));
            var a = RenderTexture.GetTemporary(src.width, src.height, 0, src.format, RenderTextureReadWrite.Default);
            var b = RenderTexture.GetTemporary(src.width, src.height, 0, src.format, RenderTextureReadWrite.Default);
            Graphics.Blit(src, a, tiltMat, 0);
            Graphics.Blit(a, b, tiltMat, 1);
            RenderTexture.ReleaseTemporary(a);
            return b;
        }
    }

    /// <summary>
    /// 생성 이미지(Assets/Art/Cozy/*.jpg·png) 임포트 설정: 반복, 밉맵, 이방성 8, 삼선형, sRGB, 최대 1024, 알파 없음,
    /// 압축 = 지형 레이어 BC1(알파 없는 형식) / 나머지 고품질(BC7).
    /// 파일은 다른 에이전트가 쓰므로 Unity 는 임포트 설정(.meta)만 맞춘다.
    /// </summary>
    public class CozyArtImport : AssetPostprocessor
    {
        public static bool IsArtTexture(string assetPath) =>
            assetPath != null && assetPath.StartsWith("Assets/Art/Cozy/", StringComparison.Ordinal) &&
            (assetPath.EndsWith(".jpg", StringComparison.OrdinalIgnoreCase) || assetPath.EndsWith(".png", StringComparison.OrdinalIgnoreCase));

        void OnPreprocessTexture()
        {
            if (IsArtTexture(assetPath) && assetImporter is TextureImporter ti) Configure(ti);
        }

        /// <summary>설정을 맞추고, 바꾼 것이 있으면 true.</summary>
        public static bool Configure(TextureImporter ti)
        {
            bool dirty = false;
            if (ti.textureType != TextureImporterType.Default) { ti.textureType = TextureImporterType.Default; dirty = true; }
            if (!ti.sRGBTexture) { ti.sRGBTexture = true; dirty = true; }
            if (ti.wrapModeU != TextureWrapMode.Repeat || ti.wrapModeV != TextureWrapMode.Repeat) { ti.wrapMode = TextureWrapMode.Repeat; dirty = true; }
            if (!ti.mipmapEnabled) { ti.mipmapEnabled = true; dirty = true; }
            if (ti.anisoLevel != 8) { ti.anisoLevel = 8; dirty = true; }
            if (ti.filterMode != FilterMode.Trilinear) { ti.filterMode = FilterMode.Trilinear; dirty = true; }
            if (ti.maxTextureSize != 1024) { ti.maxTextureSize = 1024; dirty = true; }
            // 지형 레이어(ground_·road_·rail_)는 알파 없는 형식(BC1)이어야 한다: URP TerrainLit 은 확산 텍스처 형식에 알파가 있으면
            // 매끄러움을 알파(=1)로 고정해 지면이 거울처럼 번들거린다(07 1차에서 발견). 외벽·지붕·교량은 BC7(고품질).
            string file = System.IO.Path.GetFileName(ti.assetPath);
            bool terrainLayer = file.StartsWith("ground_", StringComparison.Ordinal) || file.StartsWith("road_", StringComparison.Ordinal) ||
                                file.StartsWith("rail_", StringComparison.Ordinal);
            var compression = terrainLayer ? TextureImporterCompression.Compressed : TextureImporterCompression.CompressedHQ;
            if (ti.textureCompression != compression) { ti.textureCompression = compression; dirty = true; }
            if (ti.alphaSource != TextureImporterAlphaSource.None) { ti.alphaSource = TextureImporterAlphaSource.None; dirty = true; }
            return dirty;
        }
    }
}
