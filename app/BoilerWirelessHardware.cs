using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;

namespace OxpInstaller;

// A read-only suggestion for this Windows computer, never evidence about a target disk.
internal static class BoilerWirelessHardware
{
    internal sealed record Device(Guid ClassGuid, string Name, string Manufacturer, string[] HardwareIds, string[] CompatibleIds);
    internal sealed record Detection(string[] Components, string[] Devices, int Unrecognized);
    internal static readonly Guid NetworkClass = new("4d36e972-e325-11ce-bfc1-08002be10318");
    internal static readonly Guid BluetoothClass = new("e0cbf06c-cd8b-4647-bb8a-263b43f0f974");

    internal static Detection Match(IEnumerable<Device> devices)
    {
        var components = new HashSet<string>(StringComparer.Ordinal);
        var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var unknown = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var device in devices)
        {
            if (!Valid(device.Name) || !Valid(device.Manufacturer) || device.HardwareIds.Length > 256 || device.CompatibleIds.Length > 256) continue;
            var ids = device.HardwareIds.Where(Valid).ToArray();
            var compatible = string.Join(" ", device.CompatibleIds.Where(Valid));
            // Remote Bluetooth peripherals, software adapters and motherboard branding
            // are not radio identities. Only actual PCI/USB PnP hardware IDs qualify.
            if (!ids.Any(id => id.StartsWith("PCI\\", StringComparison.OrdinalIgnoreCase) || id.StartsWith("USB\\", StringComparison.OrdinalIgnoreCase))) continue;
            var text = device.Name + " " + device.Manufacturer;
            var bluetooth = device.ClassGuid == BluetoothClass || Contains(text, @"\bbluetooth\b")
                && Contains(compatible, @"USB\\Class_E0&SubClass_01&Prot_01\b");
            var wireless = !bluetooth && (device.ClassGuid == NetworkClass && Contains(device.Name, @"wi[ -]?fi|wireless|wlan|802\.11")
                    || Contains(compatible, @"PCI\\CC_0280"))
                && !Contains(device.Name, @"ethernet|gigabit|\bgbe\b") && !Contains(compatible, @"PCI\\CC_0200");
            if (!bluetooth && !wireless) continue;
            var vendors = new HashSet<string>(StringComparer.Ordinal);
            foreach (var id in ids)
            {
                var match = Regex.Match(id, @"^(PCI\\VEN_|USB\\VID_)([0-9A-F]{4})(?:&|$)", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
                if (!match.Success) continue;
                var pci = match.Groups[1].Value.StartsWith("PCI", StringComparison.OrdinalIgnoreCase);
                var code = match.Groups[2].Value.ToLowerInvariant();
                var vendor = (pci, code) switch
                {
                    (true, "8086") or (false, "8087") => "intel",
                    (true, "10ec") or (false, "0bda") => "realtek",
                    (true, "14c3") or (false, "0e8d") => "mediatek",
                    // These USB vendors supply several chip brands. Require explicit
                    // MediaTek branding as well as an identified wireless transport.
                    (false, "0489") or (false, "13d3") when Contains(text, @"\bmediatek\b|\bmtk\b") => "mediatek",
                    _ => null
                };
                if (vendor != null) vendors.Add(vendor);
            }
            var branded = new[] { "intel", "realtek", "mediatek" }.Where(v => Contains(text, @"\b" + v + @"\b")).ToArray();
            if (vendors.Count != 1 || branded.Any(v => !vendors.Contains(v))) { unknown.Add(device.Name); continue; }
            components.Add((bluetooth ? "bluetooth:" : "wifi:") + vendors.Single());
            names.Add(device.Name);
        }
        return new(components.Order(StringComparer.Ordinal).ToArray(), names.Order(StringComparer.OrdinalIgnoreCase).ToArray(), unknown.Count);
    }

    private static bool Valid(string text) => text.Length <= 4096 && !text.Any(char.IsControl);
    private static bool Contains(string text, string expression) => Regex.IsMatch(text, expression, RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

    internal static IReadOnlyList<Device> ReadPresentDevices()
    {
        if (!OperatingSystem.IsWindows()) throw new PlatformNotSupportedException("Windows PnP hardware detection is unavailable.");
        var handle = SetupDiGetClassDevsW(IntPtr.Zero, null, IntPtr.Zero, 0x2 | 0x4); // PRESENT | ALLCLASSES
        if (handle == new IntPtr(-1)) throw new Win32Exception(Marshal.GetLastWin32Error());
        try
        {
            var devices = new List<Device>();
            for (uint index = 0; index < 16384; index++)
            {
                var info = new DeviceInfo { Size = (uint)Marshal.SizeOf<DeviceInfo>() };
                if (!SetupDiEnumDeviceInfo(handle, index, ref info))
                {
                    var error = Marshal.GetLastWin32Error();
                    if (error == 259) return devices; // ERROR_NO_MORE_ITEMS
                    throw new Win32Exception(error);
                }
                var hardware = Property(handle, ref info, 1); // SPDRP_HARDWAREID (MULTI_SZ)
                if (hardware.Length == 0) continue;
                var friendly = Property(handle, ref info, 12); // SPDRP_FRIENDLYNAME
                var description = Property(handle, ref info, 0); // SPDRP_DEVICEDESC
                devices.Add(new(info.ClassGuid, friendly.FirstOrDefault() ?? description.FirstOrDefault() ?? "",
                    Property(handle, ref info, 11).FirstOrDefault() ?? "", hardware, Property(handle, ref info, 2)));
            }
            throw new InvalidOperationException("PnP device enumeration exceeds the inspection limit.");
        }
        finally { SetupDiDestroyDeviceInfoList(handle); }
    }

    private static string[] Property(IntPtr handle, ref DeviceInfo info, uint property)
    {
        if (SetupDiGetDeviceRegistryPropertyW(handle, ref info, property, out var type, null, 0, out var required)) return [];
        var error = Marshal.GetLastWin32Error();
        if (error == 13) return []; // ERROR_INVALID_DATA: optional property is absent
        if (error != 122) throw new Win32Exception(error); // ERROR_INSUFFICIENT_BUFFER
        if (required is 0 or > 65536 || (required & 1) != 0) throw new InvalidOperationException("Invalid PnP property size.");
        var bytes = new byte[required];
        if (!SetupDiGetDeviceRegistryPropertyW(handle, ref info, property, out type, bytes, (uint)bytes.Length, out required)) throw new Win32Exception(Marshal.GetLastWin32Error());
        if (type is not (1 or 7) || required > bytes.Length || (required & 1) != 0) throw new InvalidOperationException("Invalid PnP text property.");
        return Encoding.Unicode.GetString(bytes, 0, (int)required).Split('\0', StringSplitOptions.RemoveEmptyEntries);
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct DeviceInfo { internal uint Size; internal Guid ClassGuid; internal uint DevInst; internal UIntPtr Reserved; }
    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true, ExactSpelling = true)]
    private static extern IntPtr SetupDiGetClassDevsW(IntPtr classGuid, string? enumerator, IntPtr parent, uint flags);
    [DllImport("setupapi.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetupDiEnumDeviceInfo(IntPtr set, uint index, ref DeviceInfo info);
    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true, ExactSpelling = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetupDiGetDeviceRegistryPropertyW(IntPtr set, ref DeviceInfo info, uint property, out uint type, [Out] byte[]? buffer, uint size, out uint required);
    [DllImport("setupapi.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetupDiDestroyDeviceInfoList(IntPtr set);
}
