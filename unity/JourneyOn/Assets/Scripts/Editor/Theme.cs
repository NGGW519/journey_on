using System;
using System.Collections.Generic;
using System.Text;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

namespace JourneyOn.Editor
{
    /// <summary>
    /// 아트 테마(D-014, docs/ART.md). 겉모습 값을 한곳에 모은다: 팔레트(외벽·지붕 틴트, 대체 텍스처 색), 조명, 안개, 하늘,
    /// 후처리, 생성 텍스처 경로와 타일 크기, HUD, 틸트시프트.
    /// 값의 원본은 이 코드다(05b-나 결정). 임포트와 RefreshLook 이 매번 이 값으로 재질·설정을 덮어쓴다. 손 조정은 여기에 넣는다.
    /// cozy(기본, 아기자기한 미니어처 디오라마) / realistic(06 까지의 모습 그대로).
    /// 명령줄 -theme cozy|realistic. 씬에 적용된 테마는 Look 오브젝트의 <see cref="WorldLook"/> 에 남아 캡처 도구가 읽는다.
    /// 색은 모두 sRGB(재질·RenderSettings 가 선형으로 바꾼다).
    /// </summary>
    public sealed class Theme
    {
        public const string CozyName = "cozy", RealisticName = "realistic";
        public const float TerrainTileM = 1000f;

        public string name;

        // ------------------------------------------------------------ 텍스처(생성 이미지 → 없으면 코드 생성)
        /// <summary>생성 이미지 폴더(Assets 기준). null 이면 코드 생성 텍스처만 쓴다.</summary>
        public string artDir;
        /// <summary>코드 생성 텍스처 하위 폴더. "" = 기존 경로(Assets/World/Generated/...)를 그대로 쓴다(realistic).</summary>
        public string generatedSubdir = "";
        /// <summary>대체 외벽 색(용도별). null 이면 ProceduralTextures 의 기본(realistic) 색.</summary>
        public Dictionary<string, FacadeColors> facadeColors;
        /// <summary>대체 지붕 바탕색(용도별). null 이면 기본.</summary>
        public Dictionary<string, Color> roofColors;
        /// <summary>UV 없는 v1 건물 단색. null 이면 ProceduralTextures.FlatColors.</summary>
        public Dictionary<string, Color> flatColors;
        /// <summary>건물마다 벽·지붕에 곱하는 틴트(_BaseColor). [0] 은 기본 재질 &lt;cls&gt;.mat·roof_&lt;cls&gt;.mat 이다.</summary>
        public Color[] wallTints = { Color.white }, roofTints = { Color.white };
        /// <summary>생성 이미지 외벽·지붕의 매끄러움(JPG 는 알파가 없어 albedo 알파 대신 상수).</summary>
        public float artWallSmoothness = 0.12f, artRoofSmoothness = 0.18f;

        /// <summary>지면 바탕(잔디) 대체 텍스처 색(낮음·높음), 꽃 점 비율. 대체 텍스처가 덮는 넓이는 groundTileFallback.</summary>
        public Color groundLo, groundHi;
        public float groundFlowers;
        public float groundTileArt = 10f, groundTileFallback = 25f;
        /// <summary>도로 레이어 [아스팔트, 보도, 자갈] 타일 크기(m). 생성 이미지 / 대체 텍스처. 모두 1000 m 를 나누어떨어져야 한다.</summary>
        public float[] roadTileArt = { 8f, 4f, 4f }, roadTileFallback = { 5f, 2f, 2.5f };
        /// <summary>
        /// 생성 이미지 지면 레이어에 곱하는 색(TerrainLayer 'Color Tint' = diffuseRemapMax, 선형 알베도에 곱한다).
        /// [바탕 잔디, 아스팔트, 보도, 자갈]. 코드 생성 대체 텍스처와 realistic 은 1(곱하지 않음). 원경 색(GroundAlbedo)도 이 값을 곱한다.
        /// </summary>
        public Vector4[] artLayerTint = { Vector4.one, Vector4.one, Vector4.one, Vector4.one };
        /// <summary>
        /// 생성 이미지 지면 레이어 채도 낮추기(0 = 원본, 1 = 회색). 0 보다 크면 원본 jpg 에서 채도를 낮춘 파생 PNG 를
        /// Generated/&lt;sub&gt;/Derived/ 에 만들어 쓴다(Assets/Art/Cozy 원본은 그대로). URP 지형 레이어는 곱만 되어 채도를 낮출 수 없어서다.
        /// </summary>
        public float[] artLayerDesaturate = { 0f, 0f, 0f, 0f };
        /// <summary>대체 도로 텍스처 색: 아스팔트 회색(sRGB)·색 곱, 보도 블록 두 색·줄눈, 자갈 두 색.</summary>
        public float asphaltGray = 0.41f;
        public Color asphaltMul = new Color(1f, 1f, 1.03f), pavingA, pavingB, pavingJoint, ballastA, ballastB;

        /// <summary>교량 kind 별 단색(생성 이미지가 없을 때), 생성 이미지 타일(m).</summary>
        public Dictionary<string, Color> bridgeColors;
        public float bridgeTileM = 4f;
        public float bridgeRailMetallic = 0.4f, bridgeRailSmoothness = 0.3f, bridgeSmoothness = 0.12f;

        // ------------------------------------------------------------ 수면
        public Color waterColor;   // 알파 = 불투명도
        public float waterSmoothness;
        /// <summary>해 반짝임(직접광 반사). false 면 하늘 반사만 남긴다(07b: 작은 물웅덩이가 하얗게 타는 것을 막는다).</summary>
        public bool waterSunGlint = true;

        // ------------------------------------------------------------ 조명·하늘·안개
        public Vector3 sunEuler;
        public Color sunColor;
        public float sunIntensity, shadowStrength = 1f;
        /// <summary>URP 그림자 거리(m). 렌더 파이프라인 에셋에 쓴다.</summary>
        public float shadowDistance = 50f;
        public AmbientMode ambientMode = AmbientMode.Skybox;
        public Color ambientSky, ambientEquator, ambientGround;
        public float ambientIntensity = 1f;
        /// <summary>true = JourneyOn/SkyGradient(파스텔 그라데이션, 지평선 = 안개색), false = Skybox/Procedural.</summary>
        public bool gradientSky;
        public Color skyTop, skyMid, skyGround, skySunGlow;
        public float skyMidHeight = 0.22f, skySunSize = 0.035f, skyGlow = 0.35f, skyExposure = 1.15f, skyAtmosphere = 0.9f;
        public Color skyTint;   // Procedural
        public Color fogColor;
        public float fogDensityScale = 1f;

        // ------------------------------------------------------------ 후처리(URP)
        public bool post;
        public TonemappingMode tonemapping = TonemappingMode.Neutral;
        public float postExposure, contrast, saturation, temperature, tint;
        public float bloomThreshold = 1f, bloomIntensity, bloomScatter = 0.6f;
        public float vignette, vignetteSmoothness = 0.4f;
        public Color vignetteColor = Color.black;
        public AntialiasingMode playerAntialiasing = AntialiasingMode.None;
        // SSAO(렌더러 기능). realistic = URP 템플릿 값(06 까지 켜져 있던 값)
        public bool ssao = true;
        public float ssaoIntensity = 0.4f, ssaoRadius = 0.3f, ssaoFalloff = 100f, ssaoDirect = 0.25f;

        // ------------------------------------------------------------ 틸트시프트(조감 스크린샷·시연 영상만, 플레이어 카메라 제외)
        public bool tiltShift;
        /// <summary>초점 띠 반높이, 흐림이 최대가 되기까지의 거리(화면 높이 비율), 최대 흐림 반경(화면 높이 비율).</summary>
        public float tiltBand = 0.1f, tiltRamp = 0.32f, tiltMaxRadius = 0.0075f;
        /// <summary>눈 높이(지면 위 m)가 tiltMinAgl 이하면 끄고 tiltFullAgl 이상이면 다 건다(보행 높이 시점은 그대로).</summary>
        public float tiltMinAgl = 8f, tiltFullAgl = 30f;
        /// <summary>
        /// 아주 높은 조감에서는 미니어처 느낌보다 뿌옇게 보이므로 줄인다: tiltFadeStartAgl 까지 1, tiltFadeEndAgl 이상 tiltHighStrength(07b).
        /// </summary>
        public float tiltFadeStartAgl = 400f, tiltFadeEndAgl = 1200f, tiltHighStrength = 0.3f;
        /// <summary>시연 영상은 조감 구간에만 건다: 지형 위 tiltVideoMinAgl 이하 0, tiltVideoFullAgl 이상 1(저공 16~22 m 구간은 선명, 07b).</summary>
        public float tiltVideoMinAgl = 40f, tiltVideoFullAgl = 120f;

        // ------------------------------------------------------------ HUD·자막
        public bool hudRounded;
        public Color hudPanel = new Color(0f, 0f, 0f, 0f), hudText = Color.white, hudAccent = Color.white;
        public float hudRadius = 10f;
        /// <summary>시연 영상 출처 자막(ffmpeg drawtext 색 표기).</summary>
        public string creditFontColor = "white", creditBoxColor = "black@0.45";

        public sealed class FacadeColors
        {
            public Color wall, band, frame, glassLo, glassHi, sill;
        }

        public bool IsCozy => name == CozyName;
        public override string ToString() => name;

        // ================================================================== 테마 정의
        static Color C(float r, float g, float b, float a = 1f) => new Color(r, g, b, a);

        /// <summary>06 까지의 모습. 값은 테마 구조 이전 코드 값과 같다(같은 생성 텍스처 파일을 그대로 쓴다).</summary>
        public static readonly Theme Realistic = new Theme
        {
            name = RealisticName,
            artDir = null,
            generatedSubdir = "",
            groundTileFallback = 25f,
            roadTileFallback = new[] { 5f, 2f, 2.5f },
            asphaltGray = 0.41f, asphaltMul = C(1f, 1f, 1.03f),
            pavingA = C(0.62f, 0.58f, 0.55f), pavingB = C(0.70f, 0.55f, 0.50f), pavingJoint = C(0.40f, 0.38f, 0.36f),
            ballastA = C(0.44f, 0.42f, 0.39f), ballastB = C(0.58f, 0.53f, 0.47f),
            bridgeColors = new Dictionary<string, Color>
            {
                ["road"] = C(0.64f, 0.63f, 0.60f),
                ["foot"] = C(0.72f, 0.60f, 0.47f),
                ["rail"] = C(0.36f, 0.34f, 0.33f),
            },
            bridgeRailMetallic = 0.4f, bridgeRailSmoothness = 0.3f, bridgeSmoothness = 0.12f,
            waterColor = C(0.16f, 0.33f, 0.42f, 0.88f), waterSmoothness = 0.92f,
            sunEuler = new Vector3(42f, -35f, 0f), sunColor = C(1f, 0.96f, 0.9f), sunIntensity = 1.6f, shadowStrength = 1f,
            shadowDistance = 50f,
            ambientMode = AmbientMode.Skybox, ambientIntensity = 1f,
            gradientSky = false,
            skyGround = C(0.62f, 0.68f, 0.70f), skyTint = C(0.50f, 0.55f, 0.62f), skyAtmosphere = 0.9f, skyExposure = 1.15f, skySunSize = 0.035f,
            fogColor = C(0.74f, 0.81f, 0.88f), fogDensityScale = 1f,
            post = false,
            ssao = true, ssaoIntensity = 0.4f, ssaoRadius = 0.3f, ssaoFalloff = 100f, ssaoDirect = 0.25f,
            playerAntialiasing = AntialiasingMode.None,
            tiltShift = false,
            hudRounded = false, hudText = Color.white,
            creditFontColor = "white", creditBoxColor = "black@0.45",
        };

        /// <summary>
        /// 아기자기한 미니어처 디오라마. 파스텔이지만 탁하지 않게: 밝은 크림·민트·하늘색 벽, 코랄·청록 지붕, 싱그러운 잔디,
        /// 따뜻한 햇빛과 밝은 그늘, 파스텔 하늘·안개, 가벼운 톤매핑·채도·블룸·비네트, SSAO, 조감 틸트시프트.
        /// </summary>
        public static readonly Theme Cozy = new Theme
        {
            name = CozyName,
            artDir = "Assets/Art/Cozy",
            generatedSubdir = "Cozy",
            facadeColors = new Dictionary<string, FacadeColors>
            {
                ["residential"] = new FacadeColors { wall = C(0.97f, 0.91f, 0.80f), band = C(0.90f, 0.80f, 0.70f), frame = C(1f, 0.99f, 0.97f), glassLo = C(0.40f, 0.58f, 0.70f), glassHi = C(0.70f, 0.84f, 0.92f), sill = C(1f, 0.98f, 0.95f) },
                ["commercial"] = new FacadeColors { wall = C(0.84f, 0.92f, 0.90f), band = C(0.98f, 0.82f, 0.68f), frame = C(1f, 0.99f, 0.97f), glassLo = C(0.42f, 0.64f, 0.74f), glassHi = C(0.74f, 0.88f, 0.94f), sill = C(1f, 0.99f, 0.97f) },
                ["public"] = new FacadeColors { wall = C(0.92f, 0.66f, 0.56f), band = C(0.99f, 0.95f, 0.88f), frame = C(1f, 0.99f, 0.97f), glassLo = C(0.40f, 0.58f, 0.70f), glassHi = C(0.70f, 0.84f, 0.92f), sill = C(0.99f, 0.95f, 0.88f) },
                ["industrial"] = new FacadeColors { wall = C(0.74f, 0.85f, 0.93f), band = C(0.88f, 0.88f, 0.86f), frame = C(0.56f, 0.64f, 0.74f), glassLo = C(0.44f, 0.60f, 0.70f), glassHi = C(0.72f, 0.84f, 0.90f), sill = C(0.56f, 0.64f, 0.74f) },
                ["other"] = new FacadeColors { wall = C(0.94f, 0.89f, 0.78f), band = C(0.86f, 0.79f, 0.68f), frame = C(0.64f, 0.52f, 0.43f), glassLo = C(0.42f, 0.58f, 0.68f), glassHi = C(0.70f, 0.82f, 0.88f), sill = C(0.97f, 0.94f, 0.88f) },
            },
            roofColors = new Dictionary<string, Color>
            {
                ["residential"] = C(0.86f, 0.50f, 0.42f),   // 코랄 기와
                ["commercial"] = C(0.82f, 0.84f, 0.80f),    // 밝은 옥상
                ["public"] = C(0.42f, 0.68f, 0.70f),        // 청록
                ["industrial"] = C(0.58f, 0.72f, 0.86f),    // 하늘색 골강판
                ["other"] = C(0.72f, 0.64f, 0.58f),         // 따뜻한 회갈색
            },
            flatColors = new Dictionary<string, Color>
            {
                ["residential"] = C(0.97f, 0.91f, 0.80f),
                ["commercial"] = C(0.84f, 0.92f, 0.90f),
                ["public"] = C(0.92f, 0.66f, 0.56f),
                ["industrial"] = C(0.74f, 0.85f, 0.93f),
                ["other"] = C(0.94f, 0.89f, 0.78f),
            },
            // 건물마다 살짝 다른 색: 흰색 + 파스텔 5색(곱하는 값이라 1 근처로 둔다). 지붕은 조금 더 넓게
            wallTints = new[] { C(1f, 1f, 1f), C(1f, 0.93f, 0.87f), C(0.91f, 0.96f, 1f), C(0.93f, 1f, 0.92f), C(1f, 0.97f, 0.85f), C(0.97f, 0.92f, 1f) },
            roofTints = new[] { C(1f, 1f, 1f), C(1f, 0.84f, 0.78f), C(0.80f, 0.90f, 1f), C(0.84f, 1f, 0.86f), C(1f, 0.94f, 0.74f), C(0.90f, 0.84f, 1f) },
            artWallSmoothness = 0.12f, artRoofSmoothness = 0.18f,
            groundLo = C(0.47f, 0.66f, 0.33f), groundHi = C(0.62f, 0.76f, 0.40f), groundFlowers = 0.006f,
            // 타일 크기(1000 m 를 나누어떨어짐): 잔디 10 m(꽃 한 송이 약 15 cm, 장난감 크기; 보행 높이에서 반복이 덜 보임),
            // 아스팔트 8 m(무늬 없는 잡음), 보도 5 m(한 줄 8장 → 벽돌 약 0.6 m, 번잡함을 줄이려 크게), 자갈 2.5 m(침목 3개 → 간격 약 0.8 m, 실제 0.6 m 근처)
            groundTileArt = 10f, groundTileFallback = 25f,
            roadTileArt = new[] { 8f, 5f, 2.5f }, roadTileFallback = new[] { 5f, 2f, 2.5f },
            artLayerTint = new[] { new Vector4(0.58f, 0.66f, 0.80f, 1f), new Vector4(0.92f, 0.92f, 0.94f, 1f), new Vector4(0.86f, 0.85f, 0.85f, 1f), new Vector4(0.90f, 0.90f, 0.90f, 1f) },
            // 07b: 보도 채도 −55%(사용자 지적 "번잡함"), 나머지는 원본
            artLayerDesaturate = new[] { 0f, 0f, 0.55f, 0f },
            asphaltGray = 0.60f, asphaltMul = C(1f, 0.99f, 1.02f),
            pavingA = C(0.94f, 0.83f, 0.78f), pavingB = C(0.90f, 0.86f, 0.76f), pavingJoint = C(0.78f, 0.73f, 0.69f),
            ballastA = C(0.66f, 0.63f, 0.59f), ballastB = C(0.80f, 0.75f, 0.68f),
            bridgeColors = new Dictionary<string, Color>
            {
                ["road"] = C(0.84f, 0.83f, 0.80f),   // 밝은 콘크리트
                ["foot"] = C(0.86f, 0.68f, 0.50f),   // 나무 데크
                ["rail"] = C(0.74f, 0.75f, 0.78f),   // 콘크리트
            },
            bridgeRailMetallic = 0f, bridgeRailSmoothness = 0.15f, bridgeSmoothness = 0.1f,
            waterColor = C(0.38f, 0.76f, 0.78f, 0.86f), waterSmoothness = 0.82f, waterSunGlint = false,
            sunEuler = new Vector3(40f, -35f, 0f), sunColor = C(1f, 0.93f, 0.83f), sunIntensity = 1.15f, shadowStrength = 0.55f,
            shadowDistance = 350f,
            ambientMode = AmbientMode.Trilight,
            ambientSky = C(0.52f, 0.58f, 0.70f), ambientEquator = C(0.58f, 0.55f, 0.51f), ambientGround = C(0.36f, 0.38f, 0.31f),
            gradientSky = true,
            skyTop = C(0.40f, 0.63f, 0.95f), skyMid = C(0.62f, 0.80f, 0.97f), skyGround = C(0.78f, 0.86f, 0.94f), skySunGlow = C(1f, 0.90f, 0.76f),
            skyMidHeight = 0.16f, skySunSize = 0.03f, skyGlow = 0.3f,
            // 07b: 안개 −22%, 지평선 연무 띠 −27%(MidHeight 0.22 → 0.16), 안개색을 조금 더 파랗게
            fogColor = C(0.78f, 0.86f, 0.94f), fogDensityScale = 0.78f,
            post = true,
            tonemapping = TonemappingMode.Neutral,
            // 07b: 대비 +10 → +17, 노출 −0.1
            postExposure = -0.1f, contrast = 17f, saturation = 20f, temperature = 3f, tint = 0f,
            // 07b: 우윳빛 막을 줄이려 블룸 0.12 → 0.07, 산란 0.5 → 0.35
            bloomThreshold = 1.2f, bloomIntensity = 0.07f, bloomScatter = 0.35f,
            vignette = 0.15f, vignetteSmoothness = 0.4f, vignetteColor = C(0.12f, 0.08f, 0.10f),
            playerAntialiasing = AntialiasingMode.SubpixelMorphologicalAntiAliasing,
            ssao = true, ssaoIntensity = 0.8f, ssaoRadius = 0.8f, ssaoFalloff = 300f, ssaoDirect = 0.3f,
            tiltShift = true, tiltBand = 0.1f, tiltRamp = 0.32f, tiltMaxRadius = 0.0075f, tiltMinAgl = 8f, tiltFullAgl = 30f,
            hudRounded = true, hudPanel = C(1f, 0.975f, 0.93f, 0.88f), hudText = C(0.27f, 0.22f, 0.20f), hudAccent = C(0.88f, 0.52f, 0.46f), hudRadius = 10f,
            creditFontColor = "0x45372F", creditBoxColor = "0xFFF7EA@0.82",
        };

        public static readonly Theme[] All = { Cozy, Realistic };

        /// <summary>이름으로 찾는다(대소문자 무시). 없으면 null.</summary>
        public static Theme Get(string themeName)
        {
            if (string.IsNullOrWhiteSpace(themeName)) return null;
            foreach (var t in All)
                if (string.Equals(t.name, themeName.Trim(), StringComparison.OrdinalIgnoreCase)) return t;
            return null;
        }

        /// <summary>명령줄 -theme cozy|realistic. 없으면 cozy. 모르는 이름이면 예외(조용히 다른 테마로 가지 않는다).</summary>
        public static Theme FromArgs(string[] args)
        {
            int i = Array.IndexOf(args, "-theme");
            if (i < 0 || i + 1 >= args.Length) return Cozy;
            return Get(args[i + 1]) ?? throw new ArgumentException($"알 수 없는 테마 '{args[i + 1]}' (cozy | realistic)");
        }

        /// <summary>열린 씬에 적용된 테마(Look 오브젝트의 WorldLook). 없으면(테마 구조 이전 씬) realistic.</summary>
        public static Theme FromScene()
        {
            var wl = UnityEngine.Object.FindFirstObjectByType<WorldLook>(FindObjectsInactive.Include);
            return wl != null ? Get(wl.theme) ?? Realistic : Realistic;
        }

        // ================================================================== 순수 함수(EditMode 테스트 대상)
        /// <summary>FNV-1a 32비트(UTF-8). 실행·런타임과 관계없이 같은 값(string.GetHashCode 는 바뀔 수 있다).</summary>
        public static uint StableHash(string s)
        {
            unchecked
            {
                uint h = 2166136261u;
                foreach (byte b in Encoding.UTF8.GetBytes(s ?? "")) { h ^= b; h *= 16777619u; }
                return h;
            }
        }

        /// <summary>murmur3 마무리 섞기. 같은 해시에서 벽·지붕 번호가 따로 놀게 소금과 함께 쓴다.</summary>
        static uint Mix(uint h)
        {
            unchecked
            {
                h ^= h >> 16; h *= 0x85ebca6bu;
                h ^= h >> 13; h *= 0xc2b2ae35u;
                h ^= h >> 16;
                return h;
            }
        }

        public const uint WallSalt = 0x57A11u, RoofSalt = 0x4F0F5u;

        /// <summary>건물 키(id) → 0..count−1. count ≤ 1 이면 0. 같은 키는 언제나 같은 번호.</summary>
        public static int VariantIndex(string key, int count, uint salt)
        {
            if (count <= 1) return 0;
            return (int)(Mix(StableHash(key) ^ salt) % (uint)count);
        }

        public int WallVariant(string key) => VariantIndex(key, wallTints.Length, WallSalt);
        public int RoofVariant(string key) => VariantIndex(key, roofTints.Length, RoofSalt);

        /// <summary>타일 크기가 지형 타일(1000 m)을 나누어떨어지는지(타일 경계에서 무늬가 끊기지 않게).</summary>
        public static bool DividesTerrainTile(float size, float tile = TerrainTileM)
        {
            if (size <= 0f) return false;
            float q = tile / size;
            return Mathf.Abs(q - Mathf.Round(q)) < 1e-3f;
        }

        /// <summary>
        /// 틸트시프트 세기 0..1. 보행 높이에서는 0, 조감 높이(30~400 m)에서는 1, 사이는 부드럽게.
        /// 그보다 높으면(1.2 km 이상) tiltHighStrength 까지 줄인다.
        /// </summary>
        public float TiltStrength(float eyeAgl) => TiltStrength(eyeAgl, tiltMinAgl, tiltFullAgl);

        /// <summary>시연 영상용 세기(조감 구간만): <see cref="tiltVideoMinAgl"/>~<see cref="tiltVideoFullAgl"/>, 높은 곳 감쇠는 같다.</summary>
        public float TiltStrengthVideo(float eyeAgl) => TiltStrength(eyeAgl, tiltVideoMinAgl, tiltVideoFullAgl);

        float TiltStrength(float eyeAgl, float minAgl, float fullAgl)
        {
            if (!tiltShift) return 0f;
            static float Smooth(float t) => t * t * (3f - 2f * t);
            float rise = Smooth(Mathf.InverseLerp(minAgl, fullAgl, eyeAgl));
            float fall = Mathf.Lerp(1f, tiltHighStrength, Smooth(Mathf.InverseLerp(tiltFadeStartAgl, tiltFadeEndAgl, eyeAgl)));
            return rise * fall;
        }

        /// <summary>생성 이미지 경로(Assets 기준). 테마에 생성 이미지 폴더가 없으면 null.</summary>
        public string ArtPath(string file) => string.IsNullOrEmpty(artDir) ? null : artDir + "/" + file;

        /// <summary>코드 생성 텍스처 폴더(root 아래). realistic 은 기존 경로를 그대로 쓴다.</summary>
        public string GeneratedDir(string root, string sub) =>
            string.IsNullOrEmpty(generatedSubdir) ? $"{root}/{sub}" : $"{root}/{generatedSubdir}/{sub}";
    }
}
