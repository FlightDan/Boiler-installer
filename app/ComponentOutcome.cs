using System.IO;
using System.Text.Json;

namespace OxpInstaller;

internal static class ComponentOutcome
{
    internal static HashSet<string> Expected(SelectedOptions options)
    {
        var expected = options.Repairs.Concat(options.Plugins).ToHashSet();
        foreach (var vendor in options.Wireless ?? []) expected.Add(vendor.StartsWith("wifi:") ? vendor : "wifi:" + vendor);
        var selected = options.DeviceComponents ?? [];
        if (selected.Length == 0) return expected;
        if (options.DevicePackage == null || options.DeviceManifestSha256 == null) throw new IOException("Device package identity is missing.");
        using var doc = JsonDocument.Parse(File.ReadAllText(Path.Combine(BoilerProtocol.PackageRoot, options.DevicePackage, "manifest.json")));
        var components = doc.RootElement.GetProperty("components").EnumerateArray().ToDictionary(c => c.GetProperty("id").GetString()!);
        var visiting = new HashSet<string>();
        void Add(string id)
        {
            if (expected.Contains(id)) return;
            if (!visiting.Add(id) || !components.TryGetValue(id, out var component)) throw new IOException("Invalid device dependency graph.");
            if (component.TryGetProperty("requires", out var requires)) foreach (var dependency in requires.EnumerateArray()) Add(dependency.GetString()!);
            visiting.Remove(id); expected.Add(id);
        }
        foreach (var id in selected) Add(id);
        return expected;
    }
    internal static void Validate(JsonElement[] results, SelectedOptions options)
    {
        var expected = Expected(options);
        foreach (var slot in new[] { "A", "B" })
        {
            var records = results.Where(x => x.GetProperty("slot").GetString() == slot).ToArray();
            var ids = records.Select(x => x.GetProperty("id").GetString()!).ToArray();
            if (ids.Length != expected.Count || !expected.SetEquals(ids))
                throw new IOException("组件报告缺失或重复，不能继续安装。 / Missing or duplicate component results.");
            foreach (var record in records)
                if (record.GetProperty("status").GetString() != "files_installed")
                    throw new IOException("所选组件未完成，不能继续写盘。 / A selected component did not complete; deployment is blocked.");
        }
        if (results.Length != expected.Count * 2) throw new IOException("Unexpected component result slot.");
    }
}
