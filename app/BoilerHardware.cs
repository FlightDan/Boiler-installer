using Microsoft.Win32;
using System.Security;
using System.Text.Json;

namespace OxpInstaller;

// Recommendations only. This class never chooses a target or reads network adapters.
internal static class BoilerHardware
{
    internal sealed record Identity(string Manufacturer, string Model);

    private static string? Field(string? value) => value is { Length: > 0 and <= 256 }
        && !value.Any(char.IsControl) && !string.IsNullOrWhiteSpace(value) ? value.Trim() : null;

    internal static Identity? ReadThisComputer()
    {
        if (!OperatingSystem.IsWindows()) return null;
        try
        {
            using var machine = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64);
            using var bios = machine.OpenSubKey(@"HARDWARE\DESCRIPTION\System\BIOS", writable: false);
            var manufacturer = Field(bios?.GetValue("SystemManufacturer") as string);
            var model = Field(bios?.GetValue("SystemProductName") as string);
            return manufacturer != null && model != null ? new(manufacturer, model) : null;
        }
        catch (Exception error) when (error is SecurityException or UnauthorizedAccessException or System.IO.IOException)
        { return null; }
    }

    internal static bool Matches(JsonElement entry, Identity computer)
    {
        if (Field(computer.Manufacturer) is not string manufacturer || Field(computer.Model) is not string model
            || !entry.TryGetProperty("hardware_matching", out var matching) || matching.ValueKind != JsonValueKind.Object
            || !matching.TryGetProperty("mode", out var mode) || mode.ValueKind != JsonValueKind.String || mode.GetString() != "advisory-exact-dmi-v1"
            || !matching.TryGetProperty("pairs", out var pairs) || pairs.ValueKind != JsonValueKind.Array
            || pairs.GetArrayLength() is < 1 or > 32) return false;
        foreach (var pair in pairs.EnumerateArray())
        {
            if (pair.ValueKind != JsonValueKind.Object || !pair.TryGetProperty("manufacturer", out var maker)
                || maker.ValueKind != JsonValueKind.String || !pair.TryGetProperty("model", out var product)
                || product.ValueKind != JsonValueKind.String) continue;
            if (Field(maker.GetString()) is string wantedMaker && Field(product.GetString()) is string wantedModel
                && string.Equals(manufacturer, wantedMaker, StringComparison.OrdinalIgnoreCase)
                && string.Equals(model, wantedModel, StringComparison.OrdinalIgnoreCase)) return true;
        }
        return false;
    }
}
