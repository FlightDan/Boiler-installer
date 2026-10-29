using Microsoft.Win32;
using System.Security;
using System.Text.RegularExpressions;

namespace OxpInstaller;

// Read-only local GPU identity hints for profile selection. This does not inspect a
// target disk, choose a profile on the user's behalf, or install/download anything.
internal static class BoilerGraphicsHardware
{
    internal sealed record Detection(string[] Profiles, string[] Devices, int Unrecognized);

    internal static readonly Guid DisplayClass = new("4d36e968-e325-11ce-bfc1-08002be10318");

    private static readonly Regex PciHardwareId = new(
        @"^PCI\\VEN_([0-9A-F]{4})&DEV_([0-9A-F]{4})(?:&[A-Z0-9_]+)*$",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant | RegexOptions.Compiled);
    private static readonly Regex PciPrefix = new(
        @"^PCI\\",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant | RegexOptions.Compiled);
    private static readonly Regex RadeonMobileModel = new(
        @"\bRadeon(?:\s*\(TM\))?\s*(?<model>[0-9]{3,4}M)\b",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant | RegexOptions.Compiled);
    private static readonly Regex GenericRadeonGraphics = new(
        @"^\s*(?:AMD\s+)?Radeon(?:\s*\(TM\))?\s+Graphics\s*$",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant | RegexOptions.Compiled);
    private static readonly Regex DiscreteAmdGpuName = new(
        @"\b(?:RX|PRO|DISCRETE|EXTERNAL|DGPU|EGPU)\b",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant | RegexOptions.Compiled);
    private static readonly Regex RyzenZProcessor = new(
        @"^\s*(?:AMD\s+)?Ryzen\s+(?:(?<ai>AI)\s+)?Z(?<generation>[12])(?:\s+(?<variant>Extreme|Go|A))?(?:\s+(?:with\s+|w/\s*)Radeon\s+Graphics)?\s*$",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant | RegexOptions.Compiled);
    private static readonly Regex IntelArcG3Extreme = new(
        @"\bArc\s+G3\s+Extreme\b|\bG3E\b|\bB390\b",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant | RegexOptions.Compiled);
    private static readonly Regex IntelArcG3 = new(
        @"\bArc\s+G3\b(?!\s+Extreme)|\bB370\b",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant | RegexOptions.Compiled);
    private static readonly Regex OtherIntelGpuFamily = new(
        @"\b(?:Iris|UHD\s+Graphics|HD\s+Graphics)\b",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant | RegexOptions.Compiled);
    private static readonly Regex NvidiaGpuBrand = new(
        @"\b(?:NVIDIA|GeForce|Quadro)\b",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant | RegexOptions.Compiled);
    private static readonly Regex IntelGpuBrand = new(
        @"\b(?:Intel|Arc|Iris|UHD\s+Graphics)\b",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant | RegexOptions.Compiled);
    private static readonly Regex AmdGpuBrand = new(
        @"\b(?:AMD|Radeon|Advanced\s+Micro\s+Devices)\b",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant | RegexOptions.Compiled);

    private sealed record PciIdentity(string Vendor, string Device);
    private sealed record DisplayDevice(
        string Fingerprint,
        string Name,
        PciIdentity[] PciIds,
        bool HasMalformedPciId);

    internal static Detection Match(IEnumerable<BoilerWirelessHardware.Device> devices, string? processorName = null)
    {
        ArgumentNullException.ThrowIfNull(devices);

        // Device records do not contain an instance path, so collapse exact duplicate
        // inventory records using their displayed identity and hardware IDs.
        var displayDevices = new Dictionary<string, DisplayDevice>(StringComparer.OrdinalIgnoreCase);
        foreach (var device in devices)
        {
            if (device is null || device.ClassGuid != DisplayClass
                || !SafeText(device.Name) || !SafeText(device.Manufacturer)
                || device.HardwareIds is null || device.HardwareIds.Length is 0 or > 256
                || device.CompatibleIds is null || device.CompatibleIds.Length > 256)
                continue;

            var parsedIds = new List<PciIdentity>();
            var malformedPci = false;
            var rawIds = new List<string>();
            foreach (var rawId in device.HardwareIds)
            {
                if (!SafeText(rawId))
                {
                    malformedPci = true;
                    continue;
                }

                var id = rawId.Trim();
                rawIds.Add(id.ToUpperInvariant());
                if (!PciPrefix.IsMatch(id)) continue;
                var match = PciHardwareId.Match(id);
                if (!match.Success)
                {
                    malformedPci = true;
                    continue;
                }

                parsedIds.Add(new(match.Groups[1].Value.ToUpperInvariant(), match.Groups[2].Value.ToUpperInvariant()));
            }

            // Compatible IDs, class names, and manufacturer branding alone are not
            // hardware identity. Require a well-formed PCI hardware ID.
            if (parsedIds.Count == 0) continue;

            var name = device.Name.Trim();
            var manufacturer = device.Manufacturer.Trim();
            var fingerprint = string.Join("\u001f", new[]
            {
                DisplayClass.ToString("D"),
                name.ToUpperInvariant(),
                manufacturer.ToUpperInvariant(),
                string.Join("\u001e", rawIds.Distinct(StringComparer.Ordinal).Order(StringComparer.Ordinal))
            });
            displayDevices.TryAdd(fingerprint, new(fingerprint,
                name.Length == 0 ? "Unnamed PCI display device" : name,
                parsedIds.Distinct().ToArray(), malformedPci));
        }

        var profiles = new HashSet<string>(StringComparer.Ordinal);
        var recognizedDeviceKeys = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var recognizedNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var zProfile = GetRyzenZProfile(processorName);
        var intelProcessorTiers = GetIntelArcTiers(processorName);
        var amdDisplayDevices = new List<DisplayDevice>();

        foreach (var candidate in displayDevices.Values)
        {
            var vendors = candidate.PciIds.Select(id => id.Vendor).Distinct(StringComparer.Ordinal).ToArray();
            if (candidate.HasMalformedPciId || vendors.Length != 1) continue;

            var vendor = vendors[0];
            var name = candidate.Name;
            var profile = vendor switch
            {
                // An explicitly named Z processor takes precedence over a generic
                // Radeon 780M/880M label; the CPU/device pair is resolved below.
                "1002" => zProfile is null ? MatchAmdDisplay(name) : null,
                "8086" => MatchIntelDisplay(candidate, name, intelProcessorTiers),
                _ => null
            };
            if (profile is not null)
            {
                profiles.Add(profile);
                recognizedDeviceKeys.Add(candidate.Fingerprint);
                recognizedNames.Add(candidate.Name);
            }

            if (vendor == "1002")
                amdDisplayDevices.Add(candidate);
        }

        // The CPU name alone is not enough: only a conservative integrated-device
        // name or one of the documented CPU/GPU pairings can support a Z suggestion.
        // This is a profile suggestion only; it infers no ASIC, architecture, or firmware.
        DisplayDevice[] zMatchedDevices = zProfile is null
            ? Array.Empty<DisplayDevice>()
            : MatchAmdDevicesForZProfile(zProfile, amdDisplayDevices);
        if (zProfile is not null && zMatchedDevices.Length > 0)
        {
            profiles.Add(zProfile);
            foreach (var candidate in zMatchedDevices)
            {
                recognizedDeviceKeys.Add(candidate.Fingerprint);
                recognizedNames.Add(candidate.Name);
            }
        }

        var orderedNames = recognizedNames.Order(StringComparer.OrdinalIgnoreCase)
            .ThenBy(name => name, StringComparer.Ordinal).ToArray();
        return new(
            profiles.Order(StringComparer.Ordinal).ToArray(),
            orderedNames,
            displayDevices.Count - recognizedDeviceKeys.Count);
    }

    internal static string? ReadProcessorName()
    {
        if (!OperatingSystem.IsWindows()) return null;
        try
        {
            using var machine = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64);
            using var processor = machine.OpenSubKey(
                @"HARDWARE\DESCRIPTION\System\CentralProcessor\0", writable: false);
            var value = processor?.GetValue("ProcessorNameString", null,
                RegistryValueOptions.DoNotExpandEnvironmentNames) as string;
            return SafeText(value) && !string.IsNullOrWhiteSpace(value) ? value.Trim() : null;
        }
        catch (Exception error) when (error is SecurityException or UnauthorizedAccessException or System.IO.IOException)
        {
            return null;
        }
    }

    internal static Detection ReadThisComputer() =>
        Match(BoilerWirelessHardware.ReadPresentDevices(), ReadProcessorName());

    private static string? MatchAmdDisplay(string name)
    {
        return GetAmdGraphicsIdentity(name) switch
        {
            "780M" => "graphics:amd-780m",
            "880M" => "graphics:amd-880m",
            _ => null
        };
    }

    private static DisplayDevice[] MatchAmdDevicesForZProfile(
        string zProfile,
        IEnumerable<DisplayDevice> candidates)
    {
        var expectedModel = zProfile switch
        {
            "graphics:amd-z1-extreme" or "graphics:amd-z2" => "780M",
            "graphics:amd-z2-extreme" or "graphics:amd-ai-z2-extreme" => "890M",
            _ => null
        };
        var matched = new List<DisplayDevice>();

        foreach (var candidate in candidates)
        {
            var identity = GetAmdGraphicsIdentity(candidate.Name);
            if (identity == "generic")
            {
                matched.Add(candidate);
                continue;
            }

            // Separate discrete GPUs do not establish an integrated Ryzen Z match.
            if (identity == "discrete" || identity is null) continue;

            // A different named Radeon model, PRO/discrete branding, or conflicting
            // model name makes the CPU/GPU pairing ambiguous, so leave it unknown.
            if (identity is "conflict" or "other-model") return [];
            if (identity == expectedModel && expectedModel is not null)
            {
                matched.Add(candidate);
                continue;
            }

            if (identity is "780M" or "880M" or "890M") return [];
        }

        return matched.ToArray();
    }

    private static string? GetAmdGraphicsIdentity(string name)
    {
        if (HasContradictoryGpuBrand(name, "1002")) return "conflict";
        if (DiscreteAmdGpuName.IsMatch(name)) return "discrete";

        var models = RadeonMobileModel.Matches(name)
            .Select(match => match.Groups["model"].Value.ToUpperInvariant())
            .Distinct(StringComparer.Ordinal)
            .ToArray();
        if (models.Length > 1) return "conflict";
        if (models.Length == 1)
            return models[0] is "780M" or "880M" or "890M" ? models[0] : "other-model";
        return GenericRadeonGraphics.IsMatch(name) ? "generic" : null;
    }

    private static string? MatchIntelDisplay(
        DisplayDevice candidate,
        string name,
        HashSet<string> processorTiers)
    {
        if (HasContradictoryGpuBrand(name, "8086") || OtherIntelGpuFamily.IsMatch(name)) return null;

        var hardwareTiers = new HashSet<string>(StringComparer.Ordinal);
        var unsupportedHardwareId = false;
        foreach (var id in candidate.PciIds)
        {
            if (id.Vendor != "8086") continue;
            switch (id.Device)
            {
                case "B080":
                case "B082":
                    hardwareTiers.Add("extreme");
                    break;
                case "B081":
                case "B083":
                    hardwareTiers.Add("g3");
                    break;
                default:
                    unsupportedHardwareId = true;
                    break;
            }
        }
        if (unsupportedHardwareId || hardwareTiers.Count != 1) return null;

        var tier = hardwareTiers.Single();
        var namedTiers = GetIntelArcTiers(name);
        if (namedTiers.Any(named => named != tier) || processorTiers.Any(named => named != tier)) return null;
        return tier == "extreme" ? "graphics:intel-g3-extreme" : "graphics:intel-g3";
    }

    private static HashSet<string> GetIntelArcTiers(string? text)
    {
        var tiers = new HashSet<string>(StringComparer.Ordinal);
        if (text is null || !SafeText(text)) return tiers;
        if (IntelArcG3Extreme.IsMatch(text)) tiers.Add("extreme");
        if (IntelArcG3.IsMatch(text)) tiers.Add("g3");
        return tiers;
    }

    private static string? GetRyzenZProfile(string? processorName)
    {
        if (processorName is null || !SafeText(processorName)) return null;
        var match = RyzenZProcessor.Match(processorName.Trim());
        if (!match.Success) return null;

        var ai = match.Groups["ai"].Success;
        var generation = match.Groups["generation"].Value;
        var variant = match.Groups["variant"].Value.ToUpperInvariant();
        if (generation == "1")
        {
            if (ai || variant is "GO" or "A") return null;
            return variant == "EXTREME" ? "graphics:amd-z1-extreme" : "graphics:amd-z1";
        }

        if (ai)
            return variant == "EXTREME" ? "graphics:amd-ai-z2-extreme" : null;
        return variant switch
        {
            "EXTREME" => "graphics:amd-z2-extreme",
            "GO" => "graphics:amd-z2-go",
            "A" => "graphics:amd-z2-a",
            _ => "graphics:amd-z2"
        };
    }

    private static bool HasContradictoryGpuBrand(string name, string vendor) => vendor switch
    {
        "1002" => IntelGpuBrand.IsMatch(name) || NvidiaGpuBrand.IsMatch(name),
        "8086" => AmdGpuBrand.IsMatch(name) || NvidiaGpuBrand.IsMatch(name),
        _ => false
    };

    private static bool SafeText(string? value) => value is { Length: <= 4096 }
        && !value.Any(char.IsControl);
}
