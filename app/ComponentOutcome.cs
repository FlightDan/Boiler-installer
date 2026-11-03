using System.IO;
using System.Text.Json;

namespace OxpInstaller;

internal static class ComponentOutcome
{
    internal static HashSet<string> Expected(SelectedOptions options)
    {
        var expected = options.Repairs.Concat(options.Plugins).ToHashSet();
        if (options.SetDeckPassword || options.EnableSsh || options.KdeLanguage != "system") expected.Add("core:system-presets");
        if (options.GraphicsProfile != null) expected.Add(options.GraphicsProfile);
        foreach (var vendor in options.Wireless ?? []) expected.Add(vendor.Contains(':') ? vendor : "wifi:" + vendor);
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
    internal static void Validate(JsonElement[] results, SelectedOptions options, SsdRepairBinding? ssdBinding = null, string? ssdIdentity = null)
    {
        var expected = Expected(options);
        var ssdSelected = ssdIdentity != null;
        if (options.Repairs.Contains(SsdBootRepair.Option) && ssdIdentity != SsdBootRepair.Option)
            throw new IOException("SSD repair component identity differs from the selected option.");
        if (ssdSelected != (ssdBinding != null) || ssdBinding != null && ssdBinding.Disposition != "match")
            throw new IOException("The selected SSD repair lacks reviewed matching destination evidence.");
        var partitionIds = new HashSet<Guid>();
        foreach (var slot in new[] { "A", "B" })
        {
            var records = results.Where(x => x.GetProperty("slot").GetString() == slot).ToArray();
            var ids = records.Select(x => x.GetProperty("id").GetString()!).ToArray();
            if (ids.Length != expected.Count || !expected.SetEquals(ids))
                throw new IOException("组件报告缺失或重复，不能继续安装。 / Missing or duplicate component results.");
            foreach (var record in records)
            {
                if (record.GetProperty("status").GetString() != "files_installed")
                    throw new IOException("所选组件未完成，不能继续写盘。 / A selected component did not complete; deployment is blocked.");
                if (record.GetProperty("id").GetString() == ssdIdentity)
                {
                    var target = record.GetProperty("target"); var physical = target.GetProperty("physical");
                    var changed = record.GetProperty("operation").GetProperty("changed").GetBoolean();
                    if (record.GetProperty("activation").GetString() != "first_boot_required"
                        || record.GetProperty("operation").GetProperty("ok").GetBoolean() != true
                        || record.GetProperty("operation").GetProperty("action").GetString() != "apply"
                        || record.GetProperty("outcome").GetString() != (changed ? "applied" : "already_configured")
                        || target.GetProperty("slot").GetString() != slot || target.GetProperty("disposition").GetString() != "match"
                        || physical.GetProperty("kind").GetString() != "physical" || physical.GetProperty("bus").GetString() != ssdBinding!.Bus
                        || physical.GetProperty("model").GetString() != ssdBinding.Model || physical.GetProperty("serial").GetString() != ssdBinding.Serial
                        || physical.GetProperty("disposition").GetString() != ssdBinding.Disposition)
                        throw new IOException("SSD repair result differs from the reviewed physical destination.");
                    foreach (var property in new[] { "root_partuuid", "esp_partuuid" })
                        if (!Guid.TryParse(target.GetProperty(property).GetString(), out var id) || id == Guid.Empty || !partitionIds.Add(id))
                            throw new IOException("SSD repair slot partition evidence is missing or duplicated.");
                }
            }
        }
        if (results.Length != expected.Count * 2) throw new IOException("Unexpected component result slot.");
    }

    internal static void ValidateSsdLayout(JsonElement[] results, PreparedDisk layout, string ssdIdentity)
    {
        foreach (var slot in new[] { "A", "B" })
        {
            var record = results.Single(r => r.GetProperty("slot").GetString() == slot && r.GetProperty("id").GetString() == ssdIdentity);
            var target = record.GetProperty("target");
            foreach (var (name, role) in new[] { ("root_partuuid", "rootfs-" + slot), ("esp_partuuid", "efi-" + slot) })
                if (Guid.Parse(target.GetProperty(name).GetString()!) != layout.System.Single(p => p.Name == role).Id)
                    throw new IOException("SSD repair evidence differs from the final prepared image layout.");
        }
    }
}
