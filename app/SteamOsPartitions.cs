using System.IO;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace OxpInstaller;

internal static class SteamOsPartitions
{
    private sealed record Role(string name, Guid gptType);
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
                ["isBoot"] = false, ["isSystem"] = false, ["isHidden"] = i >= 3, ["nativeUnrecognizedSafe"] = i >= 3, ["deletionAllowed"] = i >= 3
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
        var parts = disk.GetProperty("partitions").EnumerateArray().OrderBy(p => p.GetProperty("offset").GetInt64()).ToArray();
        for (var start = 0; start <= parts.Length - Roles.Length; start++)
        {
            var matches = true;
            for (var i = 0; i < Roles.Length; i++)
            {
                var part = parts[start + i];
                if (!part.TryGetProperty("nativeGptVerified", out var verified) || verified.ValueKind != JsonValueKind.True
                    || !part.TryGetProperty("gptName", out var name) || name.GetString() != Roles[i].name
                    || !Guid.TryParse(part.GetProperty("gptType").GetString(), out var type) || type != Roles[i].gptType
                    || i > 0 && part.GetProperty("offset").GetInt64() != checked(parts[start + i - 1].GetProperty("offset").GetInt64() + parts[start + i - 1].GetProperty("size").GetInt64()))
                { matches = false; break; }
            }
            if (matches) for (var i = 0; i < Roles.Length; i++) result.Add(parts[start + i].GetProperty("offset").GetInt64(), Roles[i].name);
        }
        return result;
    }
}
