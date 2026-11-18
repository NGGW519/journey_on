// 틸트시프트(화면 띠 흐림). 검수 스크린샷·시연 영상 캡처(ThemeApply.TiltShift)만 쓴다. 플레이어 카메라에는 쓰지 않는다.
// _Tilt = (초점 화면 y 0..1, 선명한 띠 반높이, 흐림이 최대가 되기까지의 거리, 최대 흐림 반경 px)
Shader "Hidden/JourneyOn/TiltShift"
{
    Properties { _MainTex ("Source", 2D) = "white" {} }
    SubShader
    {
        Cull Off ZWrite Off ZTest Always

        CGINCLUDE
        #include "UnityCG.cginc"
        sampler2D _MainTex;
        float4 _MainTex_TexelSize;
        float4 _Tilt;

        float Amount(float y)
        {
            float d = abs(y - _Tilt.x);
            return smoothstep(_Tilt.y, _Tilt.y + _Tilt.z, d);
        }

        float4 Blur(float2 uv, float2 dir)
        {
            float r = Amount(uv.y) * _Tilt.w;
            if (r < 0.05) return tex2D(_MainTex, uv);
            float4 acc = 0;
            float wsum = 0;
            [unroll] for (int k = -7; k <= 7; k++)
            {
                float t = k / 7.0;
                float w = exp(-t * t * 2.5);
                acc += tex2D(_MainTex, uv + dir * _MainTex_TexelSize.xy * (t * r)) * w;
                wsum += w;
            }
            return acc / wsum;
        }
        ENDCG

        Pass
        {
            CGPROGRAM
            #pragma vertex vert_img
            #pragma fragment frag
            float4 frag(v2f_img i) : SV_Target { return Blur(i.uv, float2(1, 0)); }
            ENDCG
        }
        Pass
        {
            CGPROGRAM
            #pragma vertex vert_img
            #pragma fragment frag
            float4 frag(v2f_img i) : SV_Target { return Blur(i.uv, float2(0, 1)); }
            ENDCG
        }
    }
    Fallback Off
}
