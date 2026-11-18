// 파스텔 그라데이션 하늘(D-014 cozy 테마). 지평선 색 = 안개색이라 원경 지형이 하늘에 이어진다.
// 값은 WorldImporter/ThemeApply 가 Materials/Sky.mat 에 매번 덮어쓴다(손으로 고치지 않는다).
Shader "JourneyOn/SkyGradient"
{
    Properties
    {
        _TopColor ("Zenith", Color) = (0.55, 0.74, 0.95, 1)
        _MidColor ("Mid", Color) = (0.76, 0.87, 0.97, 1)
        _HorizonColor ("Horizon (= fog)", Color) = (0.90, 0.91, 0.91, 1)
        _GroundColor ("Below horizon", Color) = (0.90, 0.91, 0.91, 1)
        _MidHeight ("Mid height (sin elevation)", Range(0.02, 1)) = 0.22
        _SunDir ("Direction to sun (world)", Vector) = (0.4, 0.64, 0.6, 0)
        _SunColor ("Sun glow", Color) = (1, 0.9, 0.76, 1)
        _SunSize ("Sun disc radius (rad)", Range(0, 0.2)) = 0.03
        _SunGlow ("Sun glow strength", Range(0, 2)) = 0.35
    }
    SubShader
    {
        Tags { "Queue" = "Background" "RenderType" = "Background" "PreviewType" = "Skybox" }
        Cull Off
        ZWrite Off

        Pass
        {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"

            half4 _TopColor, _MidColor, _HorizonColor, _GroundColor, _SunColor;
            float4 _SunDir;
            float _MidHeight, _SunSize, _SunGlow;

            struct appdata { float4 vertex : POSITION; UNITY_VERTEX_INPUT_INSTANCE_ID };
            struct v2f { float4 pos : SV_POSITION; float3 dir : TEXCOORD0; UNITY_VERTEX_OUTPUT_STEREO };

            v2f vert(appdata v)
            {
                v2f o;
                UNITY_SETUP_INSTANCE_ID(v);
                UNITY_INITIALIZE_VERTEX_OUTPUT_STEREO(o);
                o.pos = UnityObjectToClipPos(v.vertex);
                o.dir = v.vertex.xyz;
                return o;
            }

            half4 frag(v2f i) : SV_Target
            {
                float3 d = normalize(i.dir);
                float y = d.y;
                half3 c;
                if (y >= 0)
                {
                    half3 low = lerp(_HorizonColor.rgb, _MidColor.rgb, smoothstep(0.0, _MidHeight, y));
                    c = lerp(low, _TopColor.rgb, smoothstep(_MidHeight * 0.6, 1.0, y));
                }
                else
                {
                    c = lerp(_HorizonColor.rgb, _GroundColor.rgb, smoothstep(0.0, 0.2, -y));
                }
                float3 s = normalize(_SunDir.xyz);
                float cs = saturate(dot(d, s));
                // 넓고 따뜻한 빛무리 + 작은 해(블룸이 부드럽게 번진다)
                c += _SunColor.rgb * _SunGlow * (0.18 * pow(cs, 6.0) + 0.5 * pow(cs, 48.0));
                float disc = smoothstep(cos(_SunSize * 1.25), cos(_SunSize), cs);
                c = lerp(c, _SunColor.rgb * 2.2, disc);
                return half4(c, 1);
            }
            ENDCG
        }
    }
    Fallback Off
}
