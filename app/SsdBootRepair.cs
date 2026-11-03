using System.IO;
using System.Security.Cryptography;
using System.Text.Json;

namespace OxpInstaller;

// Facts derived by the installer from its reviewed physical selection. They
// travel in the bound helper request with the prepared source identity.
public sealed record SsdRepairBinding(string Bus, string Model, string Serial, string Disposition);

internal static class SsdBootRepair
{
    internal const string Option = "ssd:gm7-boot-repair";
    internal static bool IsProvider(JsonElement component)
    {
        if (!component.TryGetProperty("ssd_boot_policy", out var policy) || policy.ValueKind != JsonValueKind.Object
            || policy.EnumerateObject().Count() != 2 || !policy.TryGetProperty("id", out var id) || id.GetString() != Option
            || !policy.TryGetProperty("version", out var version) || !version.TryGetInt32(out var n) || n != 1
            || component.TryGetProperty("available", out var available) && available.ValueKind != JsonValueKind.True
            || !component.TryGetProperty("minimum_engine_version", out var minimum) || minimum.ValueKind != JsonValueKind.String
            || !Version.TryParse(minimum.GetString(), out var minimumVersion) || minimumVersion < new Version(0, 1, 3)
            || !component.TryGetProperty("optional", out var optional) || optional.ValueKind != JsonValueKind.True
            || component.TryGetProperty("dynamic_handler", out _)
            || component.TryGetProperty("retain_owned_paths", out var retain) && retain.ValueKind == JsonValueKind.True)
            return false;
        if (component.TryGetProperty("compatibility", out var compatibility)
            && (compatibility.ValueKind != JsonValueKind.Object || compatibility.EnumerateObject().Any())) return false;
        foreach (var name in new[] { "files", "requires", "conflicts", "hooks" })
            if (component.TryGetProperty(name, out var value) && (name != "hooks" ? value.ValueKind != JsonValueKind.Array || value.GetArrayLength() != 0
                    : value.ValueKind != JsonValueKind.Object || value.EnumerateObject().Any())) return false;
        return true;
    }
    internal static string? Provider(SelectedOptions options, bool selectedOnly)
    {
        if (options.DevicePackage == null) return null;
        var path = Path.Combine(BoilerProtocol.PackageRoot, options.DevicePackage, "manifest.json");
        if (new FileInfo(path).Length > 2 * 1024 * 1024) throw new IOException("Oversized device package manifest.");
        var data = File.ReadAllBytes(path);
        if (Convert.ToHexString(SHA256.HashData(data)).ToLowerInvariant() != options.DeviceManifestSha256)
            throw new IOException("Selected device package manifest changed.");
        using var doc = JsonDocument.Parse(data);
        if (doc.RootElement.GetProperty("components").EnumerateArray().Any(IsProvider)
            && (!doc.RootElement.TryGetProperty("minimum_engine_version", out var minimum) || minimum.ValueKind != JsonValueKind.String
                || !Version.TryParse(minimum.GetString(), out var version) || version < new Version(0, 1, 3)))
            throw new IOException("SSD package provider requires engine 0.1.3 or newer.");
        var providers = doc.RootElement.GetProperty("components").EnumerateArray().Where(IsProvider)
            .Where(c => !selectedOnly || (options.DeviceComponents ?? []).Contains(c.GetProperty("id").GetString()!))
            .Select(c => c.GetProperty("id").GetString()!).ToArray();
        if (providers.Length > 1) throw new IOException("Multiple SSD boot providers require review.");
        return providers.SingleOrDefault();
    }
    internal static bool SupportsFixedOxp3(SelectedOptions options)
    {
        if (options.DevicePackage == null || options.DeviceManifestSha256 == null) return false;
        var path = Path.Combine(BoilerProtocol.PackageRoot, options.DevicePackage, "manifest.json");
        if (new FileInfo(path).Length > 2 * 1024 * 1024) throw new IOException("Oversized device package manifest.");
        var bytes = File.ReadAllBytes(path);
        if (Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant() != options.DeviceManifestSha256)
            throw new IOException("Selected device package manifest changed.");
        using var doc = JsonDocument.Parse(bytes); var package = doc.RootElement;
        if (package.GetProperty("id").GetString() != "device:oxp3"
            || !package.TryGetProperty("oxp3_efi_contract", out var contract) || contract.ValueKind != JsonValueKind.Object
            || contract.EnumerateObject().Count() != 2 || contract.GetProperty("kind").GetString() != "oxp3-split-lock-efi-v1"
            || contract.GetProperty("component").GetString() != "oxp3:legacy-adaptation"
            || !(options.DeviceComponents ?? []).Contains("oxp3:legacy-adaptation")) return false;
        return package.TryGetProperty("legacy_capabilities", out var capabilities) && capabilities.ValueKind == JsonValueKind.Array
            && capabilities.EnumerateArray().Count(c => c.GetProperty("id").GetString() == "oxp3:gm7-resume"
                && c.TryGetProperty("available", out var available) && available.ValueKind == JsonValueKind.False) == 1;
    }
    private static string Normalize(string value)
    {
        if (string.IsNullOrWhiteSpace(value) || value.Length > 128 || value.Any(c => c < 32 || c > 126))
            throw new IOException("Reviewed SSD identity is missing or malformed.");
        return string.Join(" ", value.Split(' ', StringSplitOptions.RemoveEmptyEntries)).ToUpperInvariant();
    }
    internal static SsdRepairBinding FromSelection(PartitionSelection selection)
    {
        using var snapshot = JsonDocument.Parse(selection.Snapshot);
        var disk = snapshot.RootElement.EnumerateArray().Single(d => d.GetProperty("number").GetInt32() == selection.Disk);
        string Field(string name) => disk.TryGetProperty(name, out var field) && field.ValueKind == JsonValueKind.String ? field.GetString() ?? "" : "";
        var bus = Field("bus"); var model = Field("model"); var serial = Field("serial");
        if (bus is not ("NVMe" or "SATA" or "USB" or "SD" or "MMC")) throw new IOException("Reviewed SSD bus is unavailable.");
        var normalized = Normalize(model); _ = Normalize(serial);
        return new(bus, model, serial, bus == "NVMe" && normalized == "PREDATOR SSD GM7 1TB" ? "match" : "not_applicable");
    }
    // Preference identity is distinct from the complete reviewed snapshot used for writes.
    internal sealed record PreferenceKey(string Bus, string Model, string Serial, Guid DiskId, long DiskSize,
                                         long Start, long Capacity, bool AlignRight, string Policy);
    internal static PreferenceKey PreferenceTarget(PartitionSelection selection)
    {
        var binding = FromSelection(selection);
        var target = selection.Target();
        using var snapshot = JsonDocument.Parse(selection.Snapshot);
        var disk = snapshot.RootElement.EnumerateArray().Single(d => d.GetProperty("number").GetInt32() == selection.Disk);
        if (!disk.TryGetProperty("guid", out var guid) || guid.ValueKind != JsonValueKind.String
            || !Guid.TryParse(guid.GetString(), out var diskId) || diskId == Guid.Empty)
            throw new IOException("Reviewed disk identity is unavailable.");
        return new(binding.Bus.ToUpperInvariant(), Normalize(binding.Model), Normalize(binding.Serial), diskId,
                   target.DiskSize, target.Start, selection.Capacity, selection.AlignRight, Option);
    }
    internal static void Require(SsdRepairBinding binding, PartitionSelection selection, NativeDiskInfo native)
    {
        if (binding != FromSelection(selection)) throw new IOException("SSD repair binding differs from the reviewed destination.");
        var bus = binding.Bus switch { "NVMe" => 17u, "SATA" => 11u, "USB" => 7u, "SD" => 12u, "MMC" => 13u, _ => 0u };
        if (native.Bus != bus || Normalize(native.Product) != Normalize(binding.Model)
            || Normalize(native.Serial) != Normalize(binding.Serial))
            throw new IOException("Native SSD identity differs from the reviewed repair destination.");
    }
}
