using System.IO;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace OxpInstaller;

internal static class SteamOsPartitions
{
    private sealed record Role(string name, Guid gptType, long bootSize = 0);
    private static readonly Role[] Roles = LoadRoles();
    private static Role[] LoadRoles()
    {
        var roles = JsonSerializer.Deserialize<Role[]>(File.ReadAllText(Path.Combine(Backend.Root, "scripts/steam-partition-roles.json")))!;
        if (!roles.Select(r => r.name).SequenceEqual(PreparedDisk.Roles) || roles.Any(r => r.gptType == Guid.Empty))
            throw new IOException("Invalid SteamOS role policy.");
        return roles;
    }
    internal static string ExampleJson()
    {
        var root = JsonNode.Parse(PartitionInventory.ExampleJson)!.AsArray();
        root[0]!["serial"] = "synthetic-steamos-reinstall";
        var gap = PartitionInventory.Read(root.ToJsonString()).Single().Rows.Single(r => r.Space != null).Space!;
        var layout = JsonSerializer.SerializeToElement(DiskPlanning.Layout(gap with { Size = 96 * PartitionInventory.GiB }));
        for (var i = 0; i < 8; i++)
        {
            var part = layout[i]; var role = Roles[i];
            root[0]!["partitions"]!.AsArray().Add(new JsonObject {
                ["number"] = 10 + i, ["guid"] = Guid.NewGuid().ToString(), ["offset"] = part.GetProperty("start").GetInt64(), ["size"] = part.GetProperty("size").GetInt64(),
                ["gptType"] = role.gptType.ToString("D"), ["gptName"] = role.name, ["gptAttributes"] = "0", ["nativeGptVerified"] = true,
                ["type"] = i == 0 ? "System" : "Unknown", ["filesystem"] = i < 3 ? "FAT32" : "", ["letter"] = "", ["label"] = "",
                // Synthetic result of the role-aware live policy; native/host
                // inspection is exercised separately by the PowerShell checks.
                ["isBoot"] = false, ["isSystem"] = false, ["isHidden"] = i >= 1, ["nativeUnrecognizedSafe"] = true, ["deletionAllowed"] = true,
                ["deletionBlockReason"] = ""
            });
        }
        // Native metadata is available for retained Windows partitions too.
        foreach (var part in root[0]!["partitions"]!.AsArray().Where(p => p!["gptName"] == null))
        { part!["gptName"] = ""; part["gptAttributes"] = "0"; part["nativeGptVerified"] = true; }
        return root.ToJsonString();
    }
    internal static Dictionary<long, string> Detect(JsonElement disk)
    {
        var result = new Dictionary<long, string>();
        bool False(string key) => disk.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.False;
        if (disk.GetProperty("style").GetString() != "GPT" || disk.GetProperty("bus").GetString() is not ("NVMe" or "SATA" or "USB" or "SD" or "MMC")
            || !False("isOffline") || !False("isReadOnly") || disk.GetProperty("sectorSize").GetInt32() != 512
            || disk.TryGetProperty("partitionReadError", out var error) && !string.IsNullOrEmpty(error.GetString())) return result;
        // Recognition contract: exact role names, a contiguous run, and nothing
        // else. Partition type, size and native-attribute proof are no longer
        // ownership gates; the live deletion policy still binds GUID, offset and
        // size and refuses partitions that carry another operating system's boot
        // files.
        var parts = disk.GetProperty("partitions").EnumerateArray().OrderBy(p => p.GetProperty("offset").GetInt64()).ToArray();
        for (var start = 0; start <= parts.Length - Roles.Length; start++)
        {
            var matches = true;
            for (var i = 0; i < Roles.Length; i++)
            {
                var part = parts[start + i];
                if (!part.TryGetProperty("gptName", out var name) || name.GetString() != Roles[i].name
                    || i > 0 && part.GetProperty("offset").GetInt64() != checked(parts[start + i - 1].GetProperty("offset").GetInt64() + parts[start + i - 1].GetProperty("size").GetInt64()))
                { matches = false; break; }
            }
            if (matches) for (var i = 0; i < Roles.Length; i++) result.Add(parts[start + i].GetProperty("offset").GetInt64(), Roles[i].name);
        }
        return result;
    }

    // A residual boot trio is only an ownership candidate. The live deletion
    // policy must additionally bind its native range and exclude another
    // operating system's boot content.
    internal static Dictionary<long, string> DeletionCandidates(JsonElement disk)
    {
        var result = Detect(disk);
        if (disk.GetProperty("style").GetString() != "GPT" || disk.GetProperty("sectorSize").GetInt32() != 512
            || !disk.TryGetProperty("isOffline", out var offline) || offline.ValueKind != JsonValueKind.False
            || !disk.TryGetProperty("isReadOnly", out var readOnly) || readOnly.ValueKind != JsonValueKind.False
            || disk.GetProperty("bus").GetString() is not ("NVMe" or "SATA" or "USB" or "SD" or "MMC")
            || disk.TryGetProperty("partitionReadError", out var error) && !string.IsNullOrEmpty(error.GetString())) return result;
        var remaining = disk.GetProperty("partitions").EnumerateArray()
            .Where(p => !result.ContainsKey(p.GetProperty("offset").GetInt64())).OrderBy(p => p.GetProperty("offset").GetInt64()).ToArray();
        var named = remaining.Where(p => p.TryGetProperty("gptName", out var n) && Roles.Any(r => r.name == n.GetString())).ToArray();
        if (named.Length != 3) return result;
        for (var i = 0; i < 3; i++)
        {
            var part = named[i];
            if (part.GetProperty("gptName").GetString() != Roles[i].name
                || i > 0 && part.GetProperty("offset").GetInt64() != checked(named[i - 1].GetProperty("offset").GetInt64() + named[i - 1].GetProperty("size").GetInt64())) return result;
        }
        for (var i = 0; i < 3; i++) result.Add(named[i].GetProperty("offset").GetInt64(), Roles[i].name);
        return result;
    }
}
