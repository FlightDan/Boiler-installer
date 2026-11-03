using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;

namespace OxpInstaller;

internal static class WirelessOptionsChecks
{
    internal static async Task<string> RunAsync()
    {
        var checks = new List<string>();
        void Require(bool ok, string name) { if (!ok) throw new IOException(name); checks.Add(name); }
        HardwareMatchingChecks(Require);
        var panel = new InstallerOptionsPanel();
        var ids = InstallerOptions.WirelessComponents;
        Require(ids.Count == 6 && ids.SetEquals(InstallerOptions.Catalog.Repairs.Select(item => item.Id)), "six-explicit-wifi-and-bluetooth-options");
        foreach (var mode in new[] { "custom", "vanilla" })
        {
            panel.SetMode(mode);
            Require(panel.RepairsExpander.Visibility == Visibility.Visible && panel.Selection.Repairs.Length == 0, mode + "-generic-starts-visible-and-unselected");
            panel.OptionBoxes["bluetooth:intel"].IsChecked = true;
            Require(((TextBlock)panel.RepairsExpander.Header).Text.Contains("1/6"), mode + "-individual-checkbox-updates-count");
            var pluginChoices = panel.Selection.Plugins;
            panel.SetWirelessContext(mode, hasPackage: true);
            Require(panel.RepairsExpander.Visibility == Visibility.Collapsed && panel.Selection.Repairs.Length == 0
                && ids.All(id => panel.OptionBoxes[id].IsChecked == false && !panel.OptionBoxes[id].IsEnabled), mode + "-package-hides-clears-and-disables-wireless");
            Require(pluginChoices.SequenceEqual(panel.Selection.Plugins), mode + "-common-components-preserved");
            panel.OptionBoxes["wifi:intel"].IsChecked = true;
            Require(panel.Selection.Repairs.Length == 0 && !panel.Describe().Contains("Wi-Fi"), mode + "-hidden-programmatic-value-never-enters-plan");
            panel.SetWirelessContext(mode, hasPackage: true);
            panel.SetWirelessContext(mode, hasPackage: false);
            Require(panel.RepairsExpander.Visibility == Visibility.Visible && panel.Selection.Repairs.Length == 0
                && ids.All(id => panel.OptionBoxes[id].IsChecked == false && panel.OptionBoxes[id].IsEnabled), mode + "-returning-to-generic-never-restores-stale-checks");
        }
        foreach (var mode in new[] { "imported", "local" })
        {
            panel.SetMode("custom"); panel.OptionBoxes["bluetooth:realtek"].IsChecked = true;
            panel.SetMode(mode);
            Require(panel.RepairsExpander.Visibility == Visibility.Collapsed && panel.Selection.Repairs.Length == 0, mode + "-has-no-generic-wireless-options");
        }
        panel.SetMode("custom");
        var intelWifi = Radio("Intel Wi-Fi 6 AX201", "Intel", "PCI\\VEN_8086&DEV_06F0", bluetooth: false);
        var realtekBluetooth = Radio("Realtek Bluetooth Adapter", "Realtek", "USB\\VID_0BDA&PID_B00C", bluetooth: true);
        var reads = 0;
        var uiThread = Environment.CurrentManagedThreadId;
        var readerThread = uiThread;
        var buttonRelease = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        panel.WirelessHardwareReader = () => { Interlocked.Increment(ref reads); readerThread = Environment.CurrentManagedThreadId;
            buttonRelease.Task.GetAwaiter().GetResult(); return [intelWifi, realtekBluetooth, intelWifi]; };
        var changes = 0; panel.Changed += (_, _) => changes++;
        var detect = ((StackPanel)panel.RepairsExpander.Content).Children.OfType<Button>().Single();
        detect.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        var progressShown = !detect.IsEnabled && (panel.WirelessDetectionStatus.Contains("正在") || panel.WirelessDetectionStatus.Contains("Reading"));
        buttonRelease.TrySetResult(true);
        Require(progressShown, "detect-button-shows-progress-and-disables-repeat-clicks");
        await panel.ActiveWirelessDetection;
        Require(readerThread != uiThread && detect.IsEnabled, "hardware-read-runs-off-ui-thread-and-restores-button");
        Require(reads == 1 && changes > 0 && panel.Selection.Repairs.ToHashSet().SetEquals(["wifi:intel", "bluetooth:realtek"]), "detect-button-applies-distinct-mixed-brand-radios-and-notifies-plan");
        Require(panel.GenericSections.Contains(panel.RepairsExpander) && panel.GenericSections.Contains(panel.GraphicsExpander)
            && panel.Children.IndexOf(panel.GenericSections.OfType<Grid>().Single()) < panel.Children.IndexOf(panel.RepairsExpander), "common-components-appear-before-firmware");
        Require(((StackPanel)panel.RepairsExpander.Content).Children.OfType<CheckBox>().Count() == 0, "firmware-group-has-no-select-all-control");
        panel.OptionBoxes["wifi:intel"].IsChecked = false; panel.OptionBoxes["wifi:mediatek"].IsChecked = true;
        var manual = panel.Selection.Repairs;
        var language = InstallerI18n.Language;
        try
        {
            foreach (var next in new[] { "zh-CN", "en-US" })
            {
                InstallerI18n.SetLanguage(next); panel.Refresh();
                Require(panel.Selection.Repairs.SequenceEqual(manual) && reads == 1, next + "-refresh-keeps-manual-edits-and-never-redetects");
            }
        }
        finally { InstallerI18n.SetLanguage(language); panel.Refresh(); }
        panel.WirelessHardwareReader = () => throw new IOException("simulated unavailable PnP inventory");
        await panel.DetectWirelessHardwareAsync();
        Require(panel.Selection.Repairs.SequenceEqual(manual) && (panel.WirelessDetectionStatus.Contains("无法读取") || panel.WirelessDetectionStatus.Contains("Could not read")), "detection-failure-is-visible-and-preserves-selections");
        panel.WirelessHardwareReader = () => [];
        await panel.DetectWirelessHardwareAsync();
        Require(panel.Selection.Repairs.SequenceEqual(manual) && (panel.WirelessDetectionStatus.Contains("未识别") || panel.WirelessDetectionStatus.Contains("No supported")), "unrecognized-inventory-keeps-manual-selections");
        panel.WirelessHardwareReader = () => [intelWifi]; await panel.DetectWirelessHardwareAsync();
        Require(panel.Selection.Repairs.ToHashSet().SetEquals(["wifi:intel", "bluetooth:realtek"]), "recognized-transport-updates-independently-of-unrecognized-transport");
        panel.SetWirelessContext("custom", hasPackage: true);
        await panel.DetectWirelessHardwareAsync(); panel.Refresh();
        panel.SetWirelessContext("custom", hasPackage: false); panel.Refresh();
        Require(panel.Selection.Repairs.Length == 0, "context-hide-and-language-refresh-never-revive-detected-firmware");
        await InFlightChecksAsync(Require);
        var digest = new string('a', 64);
        foreach (var id in ids)
        {
            var generic = new SelectedOptions(1, [id], [], "en-US", "system", InstallMode: "custom");
            InstallerOptions.Validate(generic);
            Require(ComponentOutcome.Expected(generic).SetEquals([id]), id + "-result-coverage-is-exact");
            try
            {
                InstallerOptions.Validate(generic with { DevicePackage = digest[..24], DeviceManifestSha256 = digest, DeviceTrust = "developer-session" });
                throw new InvalidOperationException("Package plus wireless firmware was accepted.");
            }
            catch (IOException) { checks.Add(id + "-forged-package-combination-rejected"); }
        }
        var legacy = new SelectedOptions(1, [], [], "en-US", "system", Wireless: ["intel", "bluetooth:realtek"]);
        InstallerOptions.Validate(legacy);
        Require(ComponentOutcome.Expected(legacy).SetEquals(["wifi:intel", "bluetooth:realtek"]), "legacy-wireless-channel-normalizes-both-transports");
        var catalogBytes = System.Text.Encoding.UTF8.GetBytes("{}\n");
        var selection = "{\"repairs\":[\"bluetooth:intel\"],\"plugins\":[]}";
        var bound = JsonSerializer.Serialize(new { schema = 1, catalog_sha256 = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(catalogBytes)).ToLowerInvariant(),
            selection = JsonSerializer.Deserialize<JsonElement>(selection), slots = new Dictionary<string, object> { ["A"] = new { }, ["B"] = new { } } });
        FirmwareDownloads.ValidatePlan(bound, selection, catalogBytes);
        checks.Add("host-accepts-bound-selected-firmware-plan");
        foreach (var badSelection in new[] { "{\"repairs\":[],\"plugins\":[]}", "{\"repairs\":[\"bluetooth:realtek\"],\"plugins\":[]}" })
        {
            try { FirmwareDownloads.ValidatePlan(bound, badSelection, catalogBytes); throw new InvalidOperationException("Changed selection accepted."); }
            catch (IOException) { checks.Add("host-rejects-changed-firmware-selection"); }
        }
        try { FirmwareDownloads.ValidatePlan(bound, selection, [1]); throw new InvalidOperationException("Changed catalogue accepted."); }
        catch (IOException) { checks.Add("host-rejects-changed-firmware-catalogue"); }
        var nativeTimer = System.Diagnostics.Stopwatch.StartNew();
        var native = await Task.Run(() => { var devices = BoilerWirelessHardware.ReadPresentDevices(); return (devices, detection: BoilerWirelessHardware.Match(devices)); });
        var nativeDevices = native.devices;
        var nativeDetection = native.detection;
        nativeTimer.Stop();
        Require(nativeDevices.Count <= 16384, "native-present-pnp-enumeration-completes-within-limit");
        return JsonSerializer.Serialize(new { passed = true, checks,
            nativeHardwareRead = new { devices = nativeDevices.Count, elapsedMs = nativeTimer.ElapsedMilliseconds, components = nativeDetection.Components,
                note = "Read-only inspection of this Windows host; empty matches do not validate any physical radio." },
            scope = "WPF controls, selection validation and read-only host PnP inspection; no disk, firmware or hardware changes" }, new JsonSerializerOptions { WriteIndented = true });
    }

    private static async Task InFlightChecksAsync(Action<bool, string> require)
    {
        var intel = Radio("Intel Wireless Adapter", "Intel", "PCI\\VEN_8086&DEV_2725", false);
        var realtek = Radio("Realtek Bluetooth Adapter", "Realtek", "USB\\VID_0BDA&PID_8771", true);
        foreach (var nextContext in new[] { "device", "vanilla", "local", "custom-reset", "manual-edit", "navigation-cancel" })
        {
            var panel = new InstallerOptionsPanel(); panel.SetMode("custom");
            if (nextContext == "navigation-cancel") panel.OptionBoxes["wifi:mediatek"].IsChecked = true;
            var entered = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var release = new TaskCompletionSource<IReadOnlyList<BoilerWirelessHardware.Device>>(TaskCreationOptions.RunContinuationsAsynchronously);
            panel.WirelessHardwareReader = () => { entered.TrySetResult(true); return release.Task.GetAwaiter().GetResult(); };
            var operation = panel.DetectWirelessHardwareAsync();
            try
            {
                await entered.Task.WaitAsync(TimeSpan.FromSeconds(10));
                if (nextContext == "device") { panel.SetWirelessContext("custom", hasPackage: true); panel.SetWirelessContext("custom", hasPackage: false); }
                else if (nextContext == "local") { panel.SetMode("local"); panel.SetMode("custom"); }
                else if (nextContext == "vanilla") panel.SetMode("vanilla");
                else if (nextContext == "custom-reset") panel.SetMode("custom");
                if (nextContext != "navigation-cancel") panel.OptionBoxes["wifi:mediatek"].IsChecked = true;
                if (nextContext == "navigation-cancel")
                {
                    panel.CancelPendingWirelessDetection();
                    var button = ((StackPanel)panel.RepairsExpander.Content).Children.OfType<Button>().Single();
                    require(button.IsEnabled && panel.Selection.Repairs.SequenceEqual(["wifi:mediatek"])
                        && (panel.WirelessDetectionStatus.Contains("取消") || panel.WirelessDetectionStatus.Contains("cancelled")), "navigation-cancel-restores-controls-and-preserves-selected-firmware");
                }
                release.TrySetResult([intel]); await operation;
                require(panel.Selection.Repairs.SequenceEqual(["wifi:mediatek"]), nextContext + "-inflight-result-never-revives-or-overwrites-selection");
            }
            finally { release.TrySetResult([]); await operation; }
        }
        {
            var panel = new InstallerOptionsPanel(); panel.SetMode("custom");
            var oldEntered = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var oldRelease = new TaskCompletionSource<IReadOnlyList<BoilerWirelessHardware.Device>>(TaskCreationOptions.RunContinuationsAsynchronously);
            panel.WirelessHardwareReader = () => { oldEntered.TrySetResult(true); return oldRelease.Task.GetAwaiter().GetResult(); };
            var oldOperation = panel.DetectWirelessHardwareAsync();
            var currentEntered = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var currentRelease = new TaskCompletionSource<IReadOnlyList<BoilerWirelessHardware.Device>>(TaskCreationOptions.RunContinuationsAsynchronously);
            Task currentOperation = Task.CompletedTask;
            try
            {
                await oldEntered.Task.WaitAsync(TimeSpan.FromSeconds(10));
                panel.SetWirelessContext("custom", hasPackage: true); panel.SetWirelessContext("custom", hasPackage: false);
                panel.WirelessHardwareReader = () => { currentEntered.TrySetResult(true); return currentRelease.Task.GetAwaiter().GetResult(); };
                currentOperation = panel.DetectWirelessHardwareAsync();
                await currentEntered.Task.WaitAsync(TimeSpan.FromSeconds(10));
                oldRelease.TrySetResult([intel]); await oldOperation;
                var button = ((StackPanel)panel.RepairsExpander.Content).Children.OfType<Button>().Single();
                require(!button.IsEnabled && panel.Selection.Repairs.Length == 0, "stale-result-cannot-enable-button-or-replace-newer-detection-progress");
                currentRelease.TrySetResult([realtek]); await currentOperation;
                require(button.IsEnabled && panel.Selection.Repairs.SequenceEqual(["bluetooth:realtek"]), "newest-inflight-request-alone-applies-and-completes");
            }
            finally { oldRelease.TrySetResult([]); currentRelease.TrySetResult([]); await oldOperation; await currentOperation; }
        }
    }

    private static BoilerWirelessHardware.Device Radio(string name, string maker, string hardware, bool bluetooth,
        string[]? compatible = null) => new(bluetooth ? BoilerWirelessHardware.BluetoothClass : BoilerWirelessHardware.NetworkClass,
            name, maker, [hardware], compatible ?? []);

    private static void HardwareMatchingChecks(Action<bool, string> require)
    {
        var wifi = Radio("Intel Wireless Adapter", "Intel", "PCI\\VEN_8086&DEV_2725", false);
        var bluetooth = Radio("Realtek Bluetooth Adapter", "Realtek", "USB\\VID_0BDA&PID_8771", true);
        require(BoilerWirelessHardware.Match([wifi, bluetooth, wifi]).Components.ToHashSet().SetEquals(["wifi:intel", "bluetooth:realtek"]), "pnp-matching-deduplicates-and-separates-transports");
        require(BoilerWirelessHardware.Match([Radio("Intel Ethernet Controller", "Intel", "PCI\\VEN_8086&DEV_15F3", false),
            Radio("Realtek PCIe GbE Family Controller", "Realtek", "PCI\\VEN_10EC&DEV_8168", false),
            Radio("Wireless Ethernet", "Intel", "PCI\\VEN_8086&DEV_15F3", false, ["PCI\\CC_020000"])]).Components.Length == 0, "ethernet-vendors-never-select-wifi");
        var unknownClass = wifi with { ClassGuid = Guid.Empty, Name = "Network Controller", CompatibleIds = ["PCI\\CC_028000"] };
        require(BoilerWirelessHardware.Match([unknownClass]).Components.SequenceEqual(["wifi:intel"]), "driverless-pci-wireless-class-identifies-transport");
        require(BoilerWirelessHardware.Match([Radio("MediaTek Wi-Fi 7", "MediaTek", "PCI\\VEN_14C3&DEV_7925", false),
            Radio("MediaTek Bluetooth Adapter", "MediaTek", "USB\\VID_0E8D&PID_0616", true)]).Components.ToHashSet().SetEquals(["wifi:mediatek", "bluetooth:mediatek"]), "mediatek-pci-and-usb-radio-identities");
        foreach (var oem in new[] { "0489", "13d3" })
        {
            require(BoilerWirelessHardware.Match([Radio("MediaTek Bluetooth Adapter", "MediaTek", "USB\\VID_" + oem + "&PID_0001", true)]).Components.SequenceEqual(["bluetooth:mediatek"]), oem + "-oem-requires-explicit-chip-brand");
            require(BoilerWirelessHardware.Match([Radio("Bluetooth Adapter", "Generic", "USB\\VID_" + oem + "&PID_0001", true)]).Components.Length == 0, oem + "-unbranded-oem-is-not-inferred");
        }
        require(BoilerWirelessHardware.Match([Radio("Realtek Wireless Adapter", "Realtek", "PCI\\VEN_8086&DEV_2725", false),
            wifi with { HardwareIds = ["PCI\\VEN_8086&DEV_2725", "PCI\\VEN_10EC&DEV_0001"] }]).Components.Length == 0, "contradictory-vendor-fields-are-not-guessed");
        require(BoilerWirelessHardware.Match([Radio("Intel Bluetooth Peripheral", "Intel", "BTHENUM\\VID_8087", true),
            Radio("Intel Wireless Adapter", "Intel", "ROOT\\VIRTUAL", false),
            wifi with { Name = "Intel Wireless\nAdapter" }, wifi with { HardwareIds = [] }]).Components.Length == 0, "remote-software-missing-and-malformed-fields-are-excluded");
        require(BoilerWirelessHardware.Match([Radio("Wireless Adapter", "Unknown", "USB\\VID_FFFF&PID_0001", false)]).Unrecognized == 1,
            "unknown-radio-vendor-remains-explicitly-unrecognized");
    }
}
