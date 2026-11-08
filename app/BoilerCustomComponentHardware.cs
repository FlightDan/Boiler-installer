using System.Text.RegularExpressions;

namespace OxpInstaller;

// Current-PC hardware hints for Custom Components. They deliberately do not
// describe an installation target, a selected device package, or Linux support.
internal static class BoilerCustomComponentHardware
{
    internal enum MatchState { Matched, Candidate, NotFound }

    internal sealed record ComponentResult(string ComponentId, MatchState State, string RawId, string Reason);
    internal sealed record Detection(ComponentResult[] Results);

    private static readonly Regex IntelG3Extreme = new(
        @"^PCI\\VEN_8086&DEV_(?:B080|B082)(?:&|$)", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant | RegexOptions.Compiled);
    private static readonly Regex IntelDisplay = new(
        @"^PCI\\VEN_8086&DEV_[0-9A-F]{4}(?:&|$)", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant | RegexOptions.Compiled);
    private static readonly Regex IntelConnectivity = new(
        @"^PCI\\VEN_8086&DEV_A840(?:&|$)", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant | RegexOptions.Compiled);
    private static readonly Regex Alc245 = new(
        @"^HDAUDIO\\FUNC_01&VEN_10EC&DEV_0245(?:&|$)", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant | RegexOptions.Compiled);
    private static readonly Regex Acpi10Ec5280 = new(
        @"^ACPI\\(?:10EC5280(?:\\|$)|VEN_10EC&DEV_5280(?:&|\\|$))", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant | RegexOptions.Compiled);
    private static readonly Regex AcpiBf63112Sensor = new(
        @"^ACPI\\BF63112(?:\\|$)", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant | RegexOptions.Compiled);
    private static readonly Regex AcpiFte3600Sensor = new(
        @"^ACPI\\FTE3600(?:\\|$)", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant | RegexOptions.Compiled);

    internal static Detection Match(IEnumerable<BoilerWirelessHardware.Device> devices)
    {
        ArgumentNullException.ThrowIfNull(devices);
        var inventory = devices.Where(Valid).ToArray();
        var g3Extreme = Find(inventory.Where(device => device.ClassGuid == BoilerGraphicsHardware.DisplayClass), IntelG3Extreme);
        var intelDisplay = Find(inventory.Where(device => device.ClassGuid == BoilerGraphicsHardware.DisplayClass), IntelDisplay);
        var connectivity = Find(inventory, IntelConnectivity);
        var alc245 = Find(inventory, Alc245);
        var bmi260Candidate = Find(inventory, Acpi10Ec5280);
        var bfSensor = Find(inventory, AcpiBf63112Sensor);
        var fteSensor = Find(inventory, AcpiFte3600Sensor);
        return new([
            g3Extreme is not null ? new("intel-xevma", MatchState.Matched, g3Extreme, "exact-intel-g3-extreme-pci")
                : intelDisplay is not null ? new("intel-xevma", MatchState.Candidate, intelDisplay, "intel-display-driver-unconfirmed") : Missing("intel-xevma"),
            // A840 is an Intel connectivity function shared by more than BE201.
            connectivity is null ? Missing("intel-be201-firmware") : new("intel-be201-firmware", MatchState.Candidate, connectivity, "shared-intel-connectivity-pci"),
            alc245 is null ? Missing("realtek-alc245-speaker") : new("realtek-alc245-speaker", MatchState.Matched, alc245, "exact-alc245-codec-speaker-route-unproven"),
            // This ACPI ID alone is not an actual BMI260 chip fact.
            bmi260Candidate is null ? Missing("bmi260-gyro") : new("bmi260-gyro", MatchState.Candidate, bmi260Candidate, "acpi-id-ambiguous-not-chip-proof"),
            // A sensor ACPI HID is only an identity observation. It does not prove the
            // chip variant, Linux driver runtime, or capture support (a FTE3600 HID
            // device on X2 hardware presented itself as an FT9365 backend at runtime).
            // BF63112 and FTE3600 are reported separately and never collapsed into one
            // another, and neither is mapped to an installable component here.
            bfSensor is null ? Missing("fingerprint-bf63112")
                : new("fingerprint-bf63112", MatchState.Candidate, bfSensor, "sensor-acpi-hid-candidate-runtime-unverified"),
            fteSensor is null ? Missing("fingerprint-fte3600")
                : new("fingerprint-fte3600", MatchState.Candidate, fteSensor, "sensor-acpi-hid-candidate-runtime-unverified")
        ]);
    }

    private static ComponentResult Missing(string componentId) => new(componentId, MatchState.NotFound, "", "not-present-on-current-pc");
    private static string? Find(IEnumerable<BoilerWirelessHardware.Device> devices, Regex identity) => devices
        .SelectMany(device => device.HardwareIds ?? [])
        .Where(id => SafeText(id)).Select(id => id.Trim()).Where(id => identity.IsMatch(id))
        .Distinct(StringComparer.OrdinalIgnoreCase).Order(StringComparer.OrdinalIgnoreCase).FirstOrDefault();
    private static bool Valid(BoilerWirelessHardware.Device? device) => device is not null
        && device.HardwareIds is { Length: > 0, Length: <= 256 } && device.HardwareIds.All(SafeText)
        && device.CompatibleIds is { Length: <= 256 } && device.CompatibleIds.All(SafeText)
        && SafeText(device.Name) && SafeText(device.Manufacturer);
    private static bool SafeText(string? text) => text is { Length: <= 4096 } && !text.Any(char.IsControl);
}
