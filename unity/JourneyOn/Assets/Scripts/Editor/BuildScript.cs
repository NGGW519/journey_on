using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

namespace JourneyOn.Editor
{
    /// <summary>
    /// Windows 64-bit 스탠드얼론 빌드.
    /// 배치 모드: -executeMethod JourneyOn.Editor.BuildScript.BuildWindows [-out &lt;exe 경로&gt;]
    /// 기본 출력: &lt;repo&gt;/dist/JourneyOn_yyyyMMdd/JourneyOn.exe
    /// </summary>
    public static class BuildScript
    {
        [MenuItem("JourneyOn/Build Windows")]
        public static void MenuBuild() => Build(DefaultOut());

        public static void BuildWindows()
        {
            var args = Environment.GetCommandLineArgs();
            int i = Array.IndexOf(args, "-out");
            string outPath = i >= 0 && i + 1 < args.Length ? args[i + 1] : DefaultOut();
            bool ok = Build(outPath);
            EditorApplication.Exit(ok ? 0 : 1);
        }

        static string DefaultOut() => Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "..", "dist",
            $"JourneyOn_{DateTime.Now:yyyyMMdd}", "JourneyOn.exe"));

        public static bool Build(string outPath)
        {
            var scenes = EditorBuildSettings.scenes.Where(s => s.enabled).Select(s => s.path).ToArray();
            if (scenes.Length == 0) { Debug.LogError("빌드할 씬이 없습니다. 먼저 WorldImporter 를 실행하세요."); return false; }
            PlayerSettings.companyName = "NGGW";
            PlayerSettings.productName = "Journey On Gapyeong";
            PlayerSettings.fullScreenMode = FullScreenMode.Windowed;
            PlayerSettings.defaultScreenWidth = 1600;
            PlayerSettings.defaultScreenHeight = 900;
            PlayerSettings.resizableWindow = true;
            Directory.CreateDirectory(Path.GetDirectoryName(outPath));
            var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
            {
                scenes = scenes,
                locationPathName = outPath,
                target = BuildTarget.StandaloneWindows64,
                options = BuildOptions.None,
            });
            var s = report.summary;
            Debug.Log($"[BuildScript] {s.result} — {outPath}, {s.totalSize / (1024 * 1024)} MB, {s.totalTime.TotalSeconds:0}s, 오류 {s.totalErrors}");
            return s.result == BuildResult.Succeeded;
        }
    }
}
