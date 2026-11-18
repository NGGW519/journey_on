using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace JourneyOn.Editor
{
    /// <summary>
    /// JsonUtility 가 읽지 못하는 부분(중첩 배열 footprint_xz, null 과 '키 없음' 구분)만 읽는 작은 JSON 파서.
    /// 결과: Dictionary&lt;string,object&gt; / List&lt;object&gt; / double / string / bool / null.
    /// </summary>
    public static class MiniJson
    {
        public static object Parse(string text)
        {
            int i = 0;
            var v = Value(text, ref i);
            Ws(text, ref i);
            if (i != text.Length) throw new FormatException($"JSON 끝에 남은 문자 위치 {i}");
            return v;
        }

        // ---------------------------------------------------------------- 조회 도우미
        public static Dictionary<string, object> Obj(object o, string key) =>
            o is Dictionary<string, object> d && d.TryGetValue(key, out var v) ? v as Dictionary<string, object> : null;

        public static List<object> Arr(object o, string key) =>
            o is Dictionary<string, object> d && d.TryGetValue(key, out var v) ? v as List<object> : null;

        public static string Str(object o, string key) =>
            o is Dictionary<string, object> d && d.TryGetValue(key, out var v) && v != null ? Convert.ToString(v, CultureInfo.InvariantCulture) : null;

        public static double Num(object o, string key, double fallback = 0) =>
            o is Dictionary<string, object> d && d.TryGetValue(key, out var v) && v is double x ? x : fallback;

        /// <summary>[[x, z], ...] → 점 목록. 형식이 다르면 빈 목록.</summary>
        public static List<UnityEngine.Vector2> Points(object o, string key)
        {
            var res = new List<UnityEngine.Vector2>();
            var arr = Arr(o, key);
            if (arr == null) return res;
            foreach (var p in arr)
                if (p is List<object> xy && xy.Count >= 2 && xy[0] is double x && xy[1] is double z)
                    res.Add(new UnityEngine.Vector2((float)x, (float)z));
            return res;
        }

        // ---------------------------------------------------------------- 파서
        static void Ws(string s, ref int i)
        {
            while (i < s.Length && char.IsWhiteSpace(s[i])) i++;
        }

        static object Value(string s, ref int i)
        {
            Ws(s, ref i);
            if (i >= s.Length) throw new FormatException("JSON 이 중간에 끝남");
            char c = s[i];
            switch (c)
            {
                case '{': return ObjectV(s, ref i);
                case '[': return ArrayV(s, ref i);
                case '"': return StringV(s, ref i);
                case 't': Expect(s, ref i, "true"); return true;
                case 'f': Expect(s, ref i, "false"); return false;
                case 'n': Expect(s, ref i, "null"); return null;
                default: return NumberV(s, ref i);
            }
        }

        static void Expect(string s, ref int i, string word)
        {
            if (string.CompareOrdinal(s, i, word, 0, word.Length) != 0) throw new FormatException($"JSON 위치 {i}: '{word}' 기대");
            i += word.Length;
        }

        static Dictionary<string, object> ObjectV(string s, ref int i)
        {
            var d = new Dictionary<string, object>();
            i++; // {
            Ws(s, ref i);
            if (i < s.Length && s[i] == '}') { i++; return d; }
            while (true)
            {
                Ws(s, ref i);
                string k = StringV(s, ref i);
                Ws(s, ref i);
                if (i >= s.Length || s[i] != ':') throw new FormatException($"JSON 위치 {i}: ':' 기대");
                i++;
                d[k] = Value(s, ref i);
                Ws(s, ref i);
                if (i < s.Length && s[i] == ',') { i++; continue; }
                if (i < s.Length && s[i] == '}') { i++; return d; }
                throw new FormatException($"JSON 위치 {i}: ',' 또는 '}}' 기대");
            }
        }

        static List<object> ArrayV(string s, ref int i)
        {
            var l = new List<object>();
            i++; // [
            Ws(s, ref i);
            if (i < s.Length && s[i] == ']') { i++; return l; }
            while (true)
            {
                l.Add(Value(s, ref i));
                Ws(s, ref i);
                if (i < s.Length && s[i] == ',') { i++; continue; }
                if (i < s.Length && s[i] == ']') { i++; return l; }
                throw new FormatException($"JSON 위치 {i}: ',' 또는 ']' 기대");
            }
        }

        static string StringV(string s, ref int i)
        {
            if (s[i] != '"') throw new FormatException($"JSON 위치 {i}: 문자열 기대");
            i++;
            var sb = new StringBuilder();
            while (i < s.Length)
            {
                char c = s[i++];
                if (c == '"') return sb.ToString();
                if (c != '\\') { sb.Append(c); continue; }
                char e = s[i++];
                switch (e)
                {
                    case 'n': sb.Append('\n'); break;
                    case 't': sb.Append('\t'); break;
                    case 'r': sb.Append('\r'); break;
                    case 'b': sb.Append('\b'); break;
                    case 'f': sb.Append('\f'); break;
                    case 'u': sb.Append((char)Convert.ToInt32(s.Substring(i, 4), 16)); i += 4; break;
                    default: sb.Append(e); break;   // \" \\ \/
                }
            }
            throw new FormatException("JSON 문자열이 닫히지 않음");
        }

        static double NumberV(string s, ref int i)
        {
            int start = i;
            while (i < s.Length && "+-0123456789.eE".IndexOf(s[i]) >= 0) i++;
            if (i == start) throw new FormatException($"JSON 위치 {i}: 값 기대");
            return double.Parse(s.Substring(start, i - start), NumberStyles.Float, CultureInfo.InvariantCulture);
        }
    }
}
