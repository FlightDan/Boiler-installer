using System.IO;
using System.Security.Cryptography;
using System.Text.Json;

namespace OxpInstaller;

internal static class FirmwareDownloads
{
    internal static void ValidatePlan(string plan, string selection, byte[] catalog)
    {
        using var parsed = JsonDocument.Parse(plan);
        var root = parsed.RootElement;
        if (root.GetProperty("schema").GetInt32() != 1
            || root.GetProperty("catalog_sha256").GetString() != Convert.ToHexString(SHA256.HashData(catalog)).ToLowerInvariant()
            || !System.Text.Json.Nodes.JsonNode.DeepEquals(System.Text.Json.Nodes.JsonNode.Parse(selection),
                System.Text.Json.Nodes.JsonNode.Parse(root.GetProperty("selection").GetRawText())))
            throw new IOException("Firmware plan differs from the selected options or current catalogue.");
        var slots = root.GetProperty("slots").EnumerateObject().Select(slot => slot.Name).ToArray();
        if (Selected(selection) && (slots.Length != 2 || !slots.ToHashSet().SetEquals(["A", "B"])))
            throw new IOException("Both firmware slot plans are required.");
        if (!Selected(selection) && slots.Length != 0) throw new IOException("Unexpected firmware plan for an empty selection.");
    }
    internal static bool Selected(string selection)
    {
        using var doc = JsonDocument.Parse(selection);
        if (doc.RootElement.TryGetProperty("graphics_profile", out var graphics) && graphics.ValueKind == JsonValueKind.String && !string.IsNullOrWhiteSpace(graphics.GetString())) return true;
        return new[] { "repairs", "wireless" }.Any(key => doc.RootElement.TryGetProperty(key, out var values)
            && values.ValueKind == JsonValueKind.Array && values.EnumerateArray().Any(value =>
                value.ValueKind == JsonValueKind.String && (InstallerOptions.WirelessComponents.Contains(value.GetString()!)
                    || key == "wireless" && value.GetString() is "intel" or "realtek" or "mediatek")));
    }

    internal static async Task<string> PrepareAsync(string image, string session, string selection, Action<string> log, CancellationToken token)
    {
        Directory.CreateDirectory(session);
        var catalog = Path.Combine(Backend.Root, "cache/integration/manifest.json");
        var planPath = Path.Combine(session, "firmware-plan.json");
        string plan;
        if (Selected(selection))
        {
            log(PartitionInventory.L("正在只读检查所选固件的缺项…\n", "Checking missing files for the selected firmware read-only…\n"));
            var output = await Backend.GuestAsync("firmware-plan", image, false, log, token, selection: selection);
            var lines = output.Split('\n').Select(line => line.Trim()).Where(line => line.StartsWith("BOILER_FIRMWARE_PLAN_JSON=", StringComparison.Ordinal)).ToArray();
            if (lines.Length != 1) throw new IOException("A unique firmware download plan is required.");
            plan = lines[0]["BOILER_FIRMWARE_PLAN_JSON=".Length..];
        }
        else
        {
            using var choices = JsonDocument.Parse(selection);
            plan = JsonSerializer.Serialize(new { schema = 1, catalog_sha256 = Convert.ToHexString(SHA256.HashData(await File.ReadAllBytesAsync(catalog, token))).ToLowerInvariant(),
                selection = choices.RootElement.Clone(), slots = new Dictionary<string, object>() });
        }
        ValidatePlan(plan, selection, await File.ReadAllBytesAsync(catalog, token));
        await File.WriteAllTextAsync(planPath, plan, token);
        var destination = Path.Combine(session, "firmware-payload");
        log(PartitionInventory.L("正在按计划获取所选组件；已有固件不下载。\n", "Fetching only the planned component files; existing firmware is kept.\n"));
        if (Directory.Exists(destination)) throw new IOException("Private firmware output already exists.");
        try
        {
            await Backend.ProcessAsync(Path.Combine(Backend.Root, "runtime/python/python.exe"),
                ["-B", Path.Combine(Backend.Root, "scripts/download-firmware.py"), "--catalog", catalog, "--plan", planPath,
                    "--output", destination, "--core-payload", Path.Combine(Backend.Root, "cache/integration"),
                    "--cache", Path.Combine(InstallerWorkspace.DirectoryPath, "firmware-content-cache"),
                    "--locks", Path.Combine(Backend.Root, "components/locks")], log, token);
        }
        catch
        {
            // ProcessAsync kills the owned downloader on cancellation. Clean its
            // private unpublished output here even when Python cannot run finally.
            var root = Path.GetFullPath(session).TrimEnd(Path.DirectorySeparatorChar);
            foreach (var path in Directory.EnumerateDirectories(root).Where(path => Path.GetFileName(path) == "firmware-payload"
                || Path.GetFileName(path).StartsWith(".firmware-payload-", StringComparison.Ordinal)))
            {
                var absolute = Path.GetFullPath(path);
                if (!string.Equals(Path.GetDirectoryName(absolute), root, StringComparison.OrdinalIgnoreCase)) throw new IOException("Firmware cleanup escapes its private session.");
                MediaSource.Ordinary(absolute, root, false);
                Directory.Delete(absolute, recursive: true);
            }
            throw;
        }
        return destination;
    }
}
