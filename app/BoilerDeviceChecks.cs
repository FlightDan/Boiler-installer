using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

namespace OxpInstaller;

internal static class BoilerDeviceChecks
{
    internal static JsonElement IndexResponse(bool offline = false, string name = "Test device") => JsonSerializer.SerializeToElement(new
    {
        offline,
        index = new { generated_at = "2026-10-24T00:00:00Z", sequence = 2, revocations = Array.Empty<string>(), packages = new[] {
            new { id = "device:fixture", version = "0.1.0", name, sha256 = new string('a', 64), status = "experimental",
                hardware_matching = new { mode = "advisory-exact-dmi-v1", pairs = new[] { new { manufacturer = "Fixture Maker", model = "Fixture Model" } } } } } }
    });

    internal static Task<string> RunAsync()
    {
        var dispatcher = Application.Current.Dispatcher;
        return dispatcher.CheckAccess() ? RunOnUiThreadAsync() : dispatcher.InvokeAsync(RunOnUiThreadAsync).Task.Unwrap();
    }
    private static T Field<T>(MainWindow window, string name) => (T)(typeof(MainWindow).GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(window)!);
    private static Task Import(MainWindow window, string path) => (Task)typeof(MainWindow).GetMethod("ImportDeviceInputAsync", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(window, [path])!;
    private static async Task Flush(MainWindow window)
    {
        window.UpdateLayout();
        await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
    }
    private static async Task Wait(MainWindow window, Func<bool> finished)
    {
        var start = DateTime.UtcNow;
        while (!finished())
        {
            if ((DateTime.UtcNow - start).TotalSeconds > 15) throw new IOException("Timed out waiting for device UI action.");
            await Task.Delay(20); await Flush(window);
        }
    }

    private static async Task VerifyCurrentHardwarePanel(MainWindow window)
    {
        BoilerWirelessHardware.Device Device(Guid classGuid, string id) => new(classGuid, "fixture", "fixture", [id], []);
        var rejected = BoilerCustomComponentHardware.Match([
            new(BoilerWirelessHardware.NetworkClass, "Intel Wi-Fi 7 BE201", "Intel", ["PCI\\VEN_8086&DEV_FFFF"], []),
            Device(BoilerGraphicsHardware.DisplayClass, "PCI\\VEN_1002&DEV_B081"),
            Device(Guid.Empty, "HDAUDIO\\FUNC_01&VEN_10EC&DEV_0246"),
            Device(Guid.Empty, "ACPI\\10EC5281\\0")]);
        if (rejected.Results.Any(result => result.State != BoilerCustomComponentHardware.MatchState.NotFound))
            throw new IOException("Current-PC matcher accepted a friendly name, vendor, or nearby ID.");
        var otherIntel = BoilerCustomComponentHardware.Match([Device(BoilerGraphicsHardware.DisplayClass, "PCI\\VEN_8086&DEV_B081")]);
        if (otherIntel.Results.Single(result => result.ComponentId == "intel-xevma").State != BoilerCustomComponentHardware.MatchState.Candidate)
            throw new IOException("Other Intel displays must remain candidates until xe compatibility is established.");
        var options = Field<InstallerOptionsPanel>(window, "optionsPanel");
        void RequireGeneralPanel(string context)
        {
            var detector = Field<Border>(window, "currentHardwarePanel");
            if (detector.Visibility != Visibility.Visible || !Field<StackPanel>(window, "customBuild").Children.Contains(detector))
                throw new IOException("Current-PC detector was not available in Custom Components " + context + ".");
        }
        typeof(MainWindow).GetMethod("ClearDeviceSelection", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(window, null);
        await Flush(window); RequireGeneralPanel("before selecting a model");
        var expectedTitle = PartitionInventory.L("识别当前电脑硬件", "Detect this computer's hardware");
        var expectedIntro = PartitionInventory.L("识别当前 Windows 电脑的部件，辅助选择对应构件。", "Identify components on this Windows computer to help choose matching components.");
        if (Field<TextBlock>(window, "currentHardwareTitle").Text != expectedTitle
            || Field<TextBlock>(window, "currentHardwareIntro").Text != expectedIntro
            || Field<Button>(window, "currentHardwareDetectButton").Content as string != expectedTitle)
            throw new IOException("Current-PC detector controls did not use the active language.");

        using var package = JsonDocument.Parse("""
            {"id":"device:fixture-current-pc","version":"0.0.0","name":{"en":"Fixture current PC","zh":"当前电脑测试构件"},"components":[
              {"id":"resource:intel-xevmafix-724","name":{"en":"xeVMA","zh":"xeVMA"},"available":true},
              {"id":"wifi:intel","name":{"en":"Intel wireless","zh":"Intel 无线"},"available":true},
              {"id":"fixture:ssd-provider","name":{"en":"SSD fixture","zh":"SSD 测试构件"},"available":true,"ssd_boot_policy":"fixture"}
            ]}
            """);
        var prepared = typeof(MainWindow).GetNestedType("PreparedDeviceSelection", BindingFlags.NonPublic)!;
        var selection = Activator.CreateInstance(prepared, "fixture-current-pc", new string('c', 64), "signed-index", package.RootElement.Clone(), null)!;
        typeof(MainWindow).GetMethod("CommitDeviceSelection", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(window, [selection]);
        await Flush(window);

        RequireGeneralPanel("with an unmatched SSD provider");
        var choices = Field<Dictionary<string, CheckBox>>(window, "deviceComponentChoices");
        choices["resource:intel-xevmafix-724"].IsChecked = false;
        choices["wifi:intel"].IsChecked = false;
        choices["fixture:ssd-provider"].IsChecked = true; // A user/provider change must not make host detection select anything.
        // A hidden generic radio option cannot be advertised while a device package owns wireless selection.
        var fixtureWirelessChoice = choices["wifi:intel"];
        choices.Remove("wifi:intel");
        try
        {
            var availability = (string)typeof(MainWindow).GetMethod("CurrentHardwareAvailability", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(window, ["intel-be201-firmware"])!;
            if (options.RepairsExpander.Visibility != Visibility.Collapsed
                || availability != PartitionInventory.L("目标是否为 BE201 尚未确认，暂不安装固件。", "BE201 on the destination is unconfirmed; firmware will not be installed."))
                throw new IOException("A hidden generic wireless option was advertised as available.");
        }
        finally { choices["wifi:intel"] = fixtureWirelessChoice; }
        RequireGeneralPanel("after an SSD-provider selection change");
        var expectedSelections = choices.ToDictionary(pair => pair.Key, pair => pair.Value.IsChecked);
        void RequireUnchanged(string outcome)
        {
            if (choices.Any(pair => pair.Value.IsChecked != expectedSelections[pair.Key]))
                throw new IOException("Current-PC detection changed component selections after " + outcome + ".");
        }
        window.CurrentHardwareReaderForChecks = () => [
            Device(BoilerGraphicsHardware.DisplayClass, "PCI\\VEN_8086&DEV_B080&SUBSYS_00000000"),
            Device(BoilerWirelessHardware.NetworkClass, "PCI\\VEN_8086&DEV_A840&SUBSYS_00000000"),
            Device(Guid.Empty, "HDAUDIO\\FUNC_01&VEN_10EC&DEV_0245&SUBSYS_00000000"),
            Device(Guid.Empty, "ACPI\\VEN_10EC&DEV_5280\\0")];
        var button = Field<Button>(window, "currentHardwareDetectButton");
        button.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        await Wait(window, () => !Field<bool>(window, "currentHardwareDetectionBusy"));
        var found = Field<BoilerCustomComponentHardware.Detection>(window, "currentHardwareDetection");
        if (!found.Results.Select(result => result.State).SequenceEqual([
                BoilerCustomComponentHardware.MatchState.Matched,
                BoilerCustomComponentHardware.MatchState.Candidate,
                BoilerCustomComponentHardware.MatchState.Matched,
                BoilerCustomComponentHardware.MatchState.Candidate]))
            throw new IOException("Current-PC matcher did not preserve exact/candidate distinctions.");
        RequireUnchanged("a matched/candidate result");
        window.CurrentHardwareReaderForChecks = () => [];
        button.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        await Wait(window, () => !Field<bool>(window, "currentHardwareDetectionBusy"));
        if (Field<BoilerCustomComponentHardware.Detection>(window, "currentHardwareDetection").Results.Any(result => result.State != BoilerCustomComponentHardware.MatchState.NotFound))
            throw new IOException("Empty current-PC inventory did not report all components absent.");
        RequireUnchanged("an empty result");
        window.CurrentHardwareReaderForChecks = () => throw new IOException("fixture SetupAPI failure");
        button.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        await Wait(window, () => !Field<bool>(window, "currentHardwareDetectionBusy"));
        if (Field<string>(window, "currentHardwareDetectionError") == null)
            throw new IOException("Current-PC SetupAPI error did not reach the advisory state.");
        RequireUnchanged("a reader error");
        typeof(MainWindow).GetMethod("ClearDeviceSelection", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(window, null);
        await Flush(window); RequireGeneralPanel("after clearing a model");
        using var resourcePackage = JsonDocument.Parse("""
            {"id":"device:fixture","version":"0.1.0","components":[],"resources":[
                {"id":"resource:intel-be201-firmware","path":"common/network/intel/be201/resource.toon"},
                {"id":"resource:explicitly-disabled","path":"common/fixture/resource.toon","default":false}]}
            """);
        var commit = typeof(MainWindow).GetMethod("CommitDeviceSelection", BindingFlags.Instance | BindingFlags.NonPublic)!;
        object ResourceSelection(string trust, char digest = 'b') =>
            Activator.CreateInstance(prepared, "resource-fixture", new string(digest, 64), trust, resourcePackage.RootElement.Clone(), null)!;
        commit.Invoke(window, [ResourceSelection("signed-index")]);
        if (choices.ContainsKey("resource:intel-be201-firmware"))
            throw new IOException("A signed-index resource was offered without target hardware verification.");
        commit.Invoke(window, [ResourceSelection("developer-session")]);
        var resourceChoice = choices["resource:intel-be201-firmware"];
        if (!resourceChoice.IsEnabled || resourceChoice.IsChecked != true
            || !(window.CurrentOptions.DeviceComponents ?? []).Contains("resource:intel-be201-firmware")
            || !window.CurrentOptions.Plugins.Contains("core:maintenance"))
            throw new IOException("The model resource and its maintenance dependency were not selected by default.");
        if (choices["resource:explicitly-disabled"].IsChecked == true)
            throw new IOException("An explicit TOON default-off was ignored.");
        resourceChoice.IsChecked = false;
        choices["resource:explicitly-disabled"].IsChecked = true;
        commit.Invoke(window, [ResourceSelection("developer-session", 'd')]);
        if (choices["resource:intel-be201-firmware"].IsChecked == true
            || (window.CurrentOptions.DeviceComponents ?? []).Contains("resource:intel-be201-firmware")
            || choices["resource:explicitly-disabled"].IsChecked != true)
            throw new IOException("Reloading the model from another package overrode the user's choice.");
        typeof(MainWindow).GetMethod("ClearDeviceSelection", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(window, null);
    }

    private static async Task<string> RunOnUiThreadAsync()
    {
        var directory = Path.Combine(Backend.Root, "logs", "device-ui-" + DateTime.UtcNow.ToString("yyyyMMddTHHmmssfff"));
        Directory.CreateDirectory(directory);
        var checks = new List<string>(); var captures = new List<string>();
        var originalLanguage = InstallerI18n.Language;
        MainWindow? window = null;
        void Require(bool ok, string name) { if (!ok) throw new IOException("Device UI check failed: " + name); checks.Add(name); }
        try
        {
            // Exercise the actual SetupAPI reader but retain no device IDs in the check result or log.
            var present = BoilerWirelessHardware.ReadPresentDevices();
            Require(present.Count <= 16384 && present.All(device => device.HardwareIds.Length is > 0 and <= 256), "current-pc-setupapi-readonly-aggregate");
            foreach (var language in new[] { "zh-CN", "en-US" })
            {
                InstallerI18n.SetLanguage(language);
                var brandModel = JsonSerializer.SerializeToElement(new { id = "device:fixture", name = new { en = "English model", zh = "中文机型" } });
                Require(MainWindow.BrandModelLabel(brandModel) == (language == "en-US" ? "English model" : "中文机型"),
                    "brand-model-localized-name-" + language);
                var brandPending = JsonSerializer.SerializeToElement(new { pending = new[] {
                    new { resource = "resource:intel-be201-firmware", status = "disabled-resource-not-integrated" },
                    new { resource = "resource:gamma22-hdr-game", status = "disabled-resource-not-integrated" } } });
                var pendingText = MainWindow.BrandPendingDescription(brandPending);
                Require(pendingText.Contains("Intel BE201") && pendingText.Contains(language == "en-US" ? "Game HDR" : "游戏 HDR")
                    && pendingText.Contains(language == "en-US" ? "not integrated" : "尚未接入"),
                    "brand-pending-localized-resource-status-" + language);
                var legacyPending = JsonSerializer.SerializeToElement(new { pending = new[] { new { component = "oxp3:legacy-adaptation", status = "linux-lifecycle-not-verified" } } });
                var legacyPendingText = MainWindow.BrandPendingDescription(legacyPending);
                Require(legacyPendingText.Contains(language == "en-US" ? "not yet verified" : "仍待验证"), "brand-pending-lifecycle-" + language);
                Require(MainWindow.BrandPendingDescription(JsonSerializer.SerializeToElement(new { id = "old" })) == "",
                    "brand-old-package-no-pending-" + language);
                var calls = 0;
                window = new MainWindow(rehearsal: true) { Width = 1060, Height = 720, WindowState = WindowState.Normal, ShowActivated = false };
                ((PartitionPicker)window.FindName("PartitionSelector")).AutomaticReadEnabled = false;
                window.DeviceIndexLoaderForChecks = _ => { calls++; return Task.FromResult(IndexResponse()); };
                window.Show(); window.BoilerModeForChecks("custom"); await Flush(window);
                var modelChoices = JsonSerializer.SerializeToElement(new[] {
                    new { id = "device:oxp3", name = new { en = "OXP3", zh = "OXP3" }, pending = new[] {
                        new { resource = "resource:intel-be201-firmware", status = "disabled-resource-not-integrated" } } },
                    new { id = "device:x2-mini", name = new { en = "X2 Mini", zh = "X2 Mini" }, pending = new[] {
                        new { resource = "resource:gamma22-hdr-game", status = "disabled-resource-not-integrated" } } } });
                var dialogShowsSelectedPending = false;
                _ = window.Dispatcher.BeginInvoke(DispatcherPriority.ApplicationIdle, new Action(() =>
                {
                    var dialog = Application.Current.Windows.OfType<Window>().Single(candidate => candidate.Owner == window);
                    var body = (StackPanel)dialog.Content;
                    var chooser = body.Children.OfType<ComboBox>().Single();
                    var notice = (TextBlock)body.Children.OfType<ScrollViewer>().Single().Content;
                    chooser.SelectedIndex = 1;
                    dialogShowsSelectedPending = notice.Text.Contains(language == "en-US" ? "Game HDR" : "游戏 HDR")
                        && !notice.Text.Contains("BE201");
                    var select = body.Children.OfType<StackPanel>().Single().Children.OfType<Button>().Last();
                    select.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                }));
                var chosenModel = (JsonElement?)typeof(MainWindow).GetMethod("ChooseBrandModel", BindingFlags.Instance | BindingFlags.NonPublic)!
                    .Invoke(window, [modelChoices]);
                Require(dialogShowsSelectedPending && chosenModel?.GetProperty("id").GetString() == "device:x2-mini",
                    "brand-selection-dialog-shows-selected-pending-" + language);
                Require(calls == 1, "inline-device-choice-loads-device-index-" + language);
                typeof(MainWindow).GetMethod("ChooseDeviceInstallation", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(window, null); await Flush(window);
                Require(calls == 1, "automatic-index-load-once-" + language);
                var list = Field<ComboBox>(window, "deviceList");
                Require(list.Items.Count == 2 && list.SelectedIndex == 0, "unselected-placeholder-and-fetched-device-" + language);
                Require(window.CurrentOptions.DevicePackage == null, "discovery-never-autoselects-" + language);
                Require(Field<Button>(window, "deviceIndexRetry").Visibility == Visibility.Collapsed, "no-manual-load-button-on-success-" + language);
                list.SelectedIndex = 1; await Flush(window);
                Require(Field<TextBlock>(window, "deviceStatus").Text.Contains("Test device"), "selection-shows-device-details-" + language);
                Require(Field<Button>(window, "signedDeviceUse").Visibility == Visibility.Visible, "signed-package-action-visible-" + language);
                list.SelectedIndex = 0;
                foreach (var size in new[] { new Size(1060, 720), new Size(640, 360) })
                {
                    window.Width = size.Width; window.Height = size.Height; await Flush(window);
                    var panel = Field<StackPanel>(window, "devicePanel");
                    Require(panel.ActualWidth > 200 && Field<Button>(window, "deviceDropTarget").ActualWidth <= panel.ActualWidth + 1,
                        "drop-target-fits-" + language + "-" + size.Width);
                    var path = Path.Combine(directory, language + "-" + size.Width + ".png");
                    if (Capture((FrameworkElement)window.Content, path)) captures.Add(path);
                }
                window.Width = 1060; window.Height = 720; await Flush(window);
                if (language == "zh-CN")
                {
                    var script = Path.Combine(directory, "example-device.sh");
                    await File.WriteAllTextAsync(script, "#!/bin/bash\n# boiler-script: 1\n# boiler-name: Device UI example\nexit 0\n", new UTF8Encoding(false));
                    await Import(window, script);
                    Require(window.CurrentOptions.DevicePackage != null && Field<int>(window, "step") == 1,
                        "explicit-import-selects-package-and-opens-source-without-extra-confirmation");
                    var options = Field<InstallerOptionsPanel>(window, "optionsPanel");
                    Require(!options.IsVisible && window.CurrentOptions.Repairs.Length == 0,
                        "device-import-shows-package-preview-instead-of-generic-wireless");
                    Require(window.CurrentOptions.DeviceTrust == "developer-session" && window.CurrentOptions.DeviceComponents?.Length == 1,
                        "local-import-keeps-local-trust-and-selects-all-components");
                    Require(options.RepairsExpander.Visibility == Visibility.Collapsed && window.CurrentOptions.Repairs.Length == 0
                        && InstallerOptions.WirelessComponents.All(id => options.OptionBoxes[id].IsChecked == false), "selected-device-package-hides-and-clears-wireless-plan");
                    typeof(MainWindow).GetMethod("ChooseGenericInstallation", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(window, null);
                    Require(window.CurrentOptions.DevicePackage == null, "choosing-generic-clears-local-package");

                    // Exercise the actual native Shell message parser and async UI intake.
                    var bytes = Encoding.Unicode.GetBytes(script + "\0\0");
                    var drop = GlobalAlloc(0x0042, (nuint)(20 + bytes.Length));
                    var memory = GlobalLock(drop);
                    Marshal.WriteInt32(memory, 0, 20); Marshal.WriteInt32(memory, 16, 1);
                    Marshal.Copy(bytes, 0, IntPtr.Add(memory, 20), bytes.Length); GlobalUnlock(drop);
                    SendMessage(new WindowInteropHelper(window).Handle, 0x233, drop, IntPtr.Zero);
                    await Wait(window, () => !Field<bool>(window, "inputInspectionBusy") && !Field<bool>(window, "inputAssociationBusy") && window.CurrentOptions.DevicePackage != null);
                    Require(Field<List<InputAssignment>>(window, "inputAssignments").Any(row => row.Input?.Kind == InputKind.DeviceScript) && window.CurrentOptions.DevicePackage != null && window.CurrentOptions.DeviceTrust == "developer-session",
                        "native-file-drop-auto-imports-with-local-trust");
                    await Import(window, script);

                    // A valid inventory containing .pyc must survive both cache and guest transport.
                    var imported = Path.Combine(directory, "inventory-import");
                    await BoilerProtocol.RunAsync(["import-device", script, imported, "--developer"], CancellationToken.None);
                    var data = Encoding.UTF8.GetBytes("inventoried bytes; never imported as Python\n" + Path.GetFileName(directory));
                    Directory.CreateDirectory(Path.Combine(imported, "data"));
                    await File.WriteAllBytesAsync(Path.Combine(imported, "data/cache.pyc"), data);
                    var manifest = JsonNode.Parse(await File.ReadAllTextAsync(Path.Combine(imported, "manifest.json")))!;
                    manifest["files"]!.AsArray().Add(JsonSerializer.SerializeToNode(new { path = "data/cache.pyc", size = data.Length, sha256 = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(data)).ToLowerInvariant() }));
                    var manifestBytes = Encoding.UTF8.GetBytes(manifest.ToJsonString());
                    await File.WriteAllBytesAsync(Path.Combine(imported, "manifest.json"), manifestBytes);
                    var digest = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(manifestBytes)).ToLowerInvariant();
                    var cached = Path.Combine(BoilerProtocol.PackageRoot, digest[..24]);
                    var quarantinesBefore = Directory.GetDirectories(BoilerProtocol.PackageRoot, digest[..24] + ".invalid-*").Length;
                    Directory.CreateDirectory(cached); await File.WriteAllTextAsync(Path.Combine(cached, "incomplete"), "broken fixture");
                    var copied = await BoilerProtocol.CachePackageAsync(imported, digest, CancellationToken.None);
                    Require(File.Exists(Path.Combine(copied, "data/cache.pyc")) && Directory.GetDirectories(BoilerProtocol.PackageRoot, digest[..24] + ".invalid-*").Length == quarantinesBefore + 1,
                        "strict-cache-copy-preserves-inventory-and-recovers-incomplete-generation");
                    var guest = Path.Combine(directory, "guest-transport");
                    await BoilerProtocol.RunAsync(["import-device", copied, guest, "--developer"], CancellationToken.None);
                    await BoilerProtocol.RunAsync(["package", guest, "--manifest-sha256", digest], CancellationToken.None);
                    Require(File.Exists(Path.Combine(guest, "data/cache.pyc")), "guest-transport-preserves-inventoried-pyc");

                    // Import the same inventoried bytes as a two-model brand ZIP.
                    var brandZip = Path.Combine(directory, "brand-fixture.zip");
                    var fixtureManifest = JsonNode.Parse(await File.ReadAllTextAsync(Path.Combine(imported, "manifest.json")))!;
                    var fixtureFiles = fixtureManifest["files"]!.AsArray().Select(node => node!["path"]!.GetValue<string>()).ToArray();
                    var toon = new StringBuilder("schema_version: 1\nkind: BoilerBrandBundle\nbrand: onexplayer\nsource_repository: FlightDan/Boiler-adaptations\nsource_commit: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\nunresolved: []\npending[3]:\n  - model: device:oxp3\n    resource: resource:intel-be201-firmware\n    status: disabled-resource-not-integrated\n  - model: device:x2-mini\n    component: pl4-175w\n    status: implementation_missing\n  - model: device:x2-mini\n    component: lsfg-vk\n    status: external_plugin\nmodels[2]:\n");
                    using (var brandArchive = ZipFile.Open(brandZip, ZipArchiveMode.Create))
                    {
                        void AddText(string path, string text)
                        {
                            using var writer = new StreamWriter(brandArchive.CreateEntry(path).Open(), new UTF8Encoding(false));
                            writer.Write(text);
                        }
                        foreach (var (id, model) in new[] { ("device:oxp3", "oxp3"), ("device:x2-mini", "x2-mini") })
                        {
                            var guide = "brands/onexplayer/" + model + "/" + model + ".toon";
                            var name = "brands/onexplayer/" + model + "/runtime/manifest.json";
                            var item = fixtureManifest.DeepClone();
                            item["id"] = id;
                            item["name"] = JsonSerializer.SerializeToNode(new { en = model, zh = model });
                            AddText(guide, "schema_version: 1\n"); AddText(name, item.ToJsonString());
                            toon.Append("  - id: ").Append(id).Append("\n    guide: ").Append(guide)
                                .Append("\n    manifest: ").Append(name).Append("\n    files[")
                                .Append(fixtureFiles.Length).Append("]{path,member}:\n");
                            foreach (var nameInPackage in fixtureFiles)
                                toon.Append("      ").Append(nameInPackage).Append(",common/fixture/").Append(nameInPackage).Append('\n');
                        }
                        foreach (var nameInPackage in fixtureFiles)
                            brandArchive.CreateEntryFromFile(Path.Combine(imported, nameInPackage.Replace('/', Path.DirectorySeparatorChar)), "common/fixture/" + nameInPackage);
                        AddText("brand.toon", toon.ToString());
                    }
                    string? brandStage = null;
                    window.BrandStageForChecks = path => brandStage = path;
                    window.BrandModelChooserForChecks = models =>
                    {
                        var choices = models.EnumerateArray().ToArray();
                        var oxpText = MainWindow.BrandPendingDescription(choices[0]);
                        var x2Text = MainWindow.BrandPendingDescription(choices[1]);
                        Require(oxpText.Contains("Intel BE201") && !oxpText.Contains("PL4")
                            && x2Text.Contains("PL4 175 W") && x2Text.Contains("LSFG-VK") && !x2Text.Contains("BE201"),
                            "brand-pending-follows-selected-model");
                        return choices[1].Clone();
                    };
                    await Import(window, brandZip);
                    var chosenBrandPath = Path.Combine(BoilerProtocol.PackageRoot, window.CurrentOptions.DevicePackage!, "manifest.json");
                    var chosenBrand = JsonNode.Parse(await File.ReadAllTextAsync(chosenBrandPath))!;
                    Require(chosenBrand["id"]!.GetValue<string>() == "device:x2-mini"
                        && window.CurrentOptions.DeviceTrust == "developer-session", "brand-import-selects-x2-through-normal-package-flow");
                    Require(brandStage != null && !Directory.Exists(brandStage) && File.Exists(brandZip) && File.Exists(chosenBrandPath),
                        "brand-success-cleans-stage-and-preserves-input-and-selected-cache");
                    var selectedBeforeCancel = window.CurrentOptions.DevicePackage;
                    window.BrandModelChooserForChecks = _ => null;
                    await Import(window, brandZip);
                    Require(brandStage != null && !Directory.Exists(brandStage) && File.Exists(brandZip)
                        && window.CurrentOptions.DevicePackage == selectedBeforeCancel && File.Exists(chosenBrandPath),
                        "brand-cancel-cleans-stage-and-preserves-previous-selection");
                    var incompleteZip = Path.Combine(directory, "brand-incomplete.zip");
                    File.Copy(brandZip, incompleteZip);
                    using (var incomplete = ZipFile.Open(incompleteZip, ZipArchiveMode.Update))
                    {
                        incomplete.GetEntry("brand.toon")!.Delete();
                        using var writer = new StreamWriter(incomplete.CreateEntry("brand.toon").Open(), new UTF8Encoding(false));
                        writer.Write(toon.ToString().Replace("unresolved: []", "unresolved[1]: missing-runtime"));
                    }
                    await Import(window, incompleteZip);
                    Require(brandStage != null && !Directory.Exists(brandStage) && File.Exists(incompleteZip)
                        && window.CurrentOptions.DevicePackage == selectedBeforeCancel && File.Exists(chosenBrandPath),
                        "brand-failure-cleans-stage-and-preserves-previous-selection");
                    window.BrandModelChooserForChecks = null;
                    window.BrandStageForChecks = null;
                    await Import(window, Path.Combine(directory, "missing.zip"));
                    Require(window.CurrentOptions.DevicePackage == null && Field<TextBlock>(window, "deviceStatus").Text.Length > 0, "failed-import-has-no-stale-selection");

                    var stale = new TaskCompletionSource<JsonElement>();
                    window.DeviceIndexLoaderForChecks = _ => stale.Task;
                    window.BoilerModeForChecks("custom"); await Flush(window);
                    typeof(MainWindow).GetMethod("ChooseDeviceInstallation", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(window, null); await Flush(window);
                    window.DeviceIndexLoaderForChecks = _ => Task.FromResult(IndexResponse(name: "Current route"));
                    window.BoilerModeForChecks("custom"); await Flush(window);
                    typeof(MainWindow).GetMethod("ChooseDeviceInstallation", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(window, null); await Flush(window);
                    stale.SetResult(IndexResponse(name: "Stale route")); await Flush(window);
                    var currentLabel = MainWindow.DeviceLabel(IndexResponse(name: "Current route").GetProperty("index").GetProperty("packages")[0]);
                    Require(Field<ComboBox>(window, "deviceList").Items.OfType<ComboBoxItem>().Any(i => i.Tag is JsonElement entry && MainWindow.DeviceLabel(entry) == currentLabel), "stale-response-cannot-replace-current-route");

                    var delayed = new TaskCompletionSource<JsonElement>();
                    window.DeviceHardwareReaderForChecks = () => new("Fixture Maker", "Fixture Model");
                    window.DeviceIndexLoaderForChecks = _ => delayed.Task;
                    window.BoilerModeForChecks("custom"); await Flush(window);
                    await Import(window, script); await Flush(window);
                    var pendingLocal = window.CurrentOptions.DevicePackage;
                    delayed.SetResult(IndexResponse()); await Flush(window);
                    Require(window.CurrentOptions.DevicePackage == pendingLocal && !Field<Border>(window, "deviceOptionPreview").IsVisible
                        && Field<ComboBox>(window, "deviceList").SelectedIndex == 0,
                        "late-matching-directory-does-not-replace-local-package-selection");
                    Require(Field<TextBlock>(window, "deviceSourceStatus").Text == PartitionInventory.L("当前来源：手动导入：", "Current source: Manual import: ") + Path.GetFileName(script)
                        && !Field<bool>(window, "deviceSourceBundled"), "explicit-local-import-shows-filename-in-online-path");

                    var droppedIndex = new TaskCompletionSource<JsonElement>();
                    window.DeviceIndexLoaderForChecks = _ => droppedIndex.Task;
                    window.BoilerModeForChecks("custom"); await Flush(window);
                    await (Task)typeof(MainWindow).GetMethod("ReceiveInputFilesAsync", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(window, [new[] { script }])!;
                    await Flush(window);
                    droppedIndex.SetResult(IndexResponse()); await Flush(window);
                    Require(!Field<Border>(window, "deviceOptionPreview").IsVisible && Field<ComboBox>(window, "deviceList").SelectedIndex == 0
                        && window.CurrentOptions.DevicePackage != null, "late-matching-directory-does-not-replace-dropped-device-selection");
                    Require(Field<TextBlock>(window, "deviceSourceStatus").Text.Contains(Path.GetFileName(script))
                        && !Field<bool>(window, "deviceSourceBundled"), "dropped-package-keeps-import-provenance-in-online-path");
                }
                window.PreviewStep(MainWindow.AdaptationStep);
                window.DeviceIndexLoaderForChecks = _ => throw new IOException("Directory unavailable fixture");
                await (Task)typeof(MainWindow).GetMethod("LoadDeviceIndexAsync", BindingFlags.Instance | BindingFlags.NonPublic)!
                    .Invoke(window, [Field<int>(window, "devicePanelGeneration"), CancellationToken.None])!;
                await Flush(window);
                Require(Field<Expander>(window, "deviceDirectoryDetails").IsExpanded
                    && Field<Button>(window, "deviceIndexRetry").IsVisible,
                    "directory-failure-exposes-retry-without-opening-details-manually-" + language);
                window.DeviceIndexLoaderForChecks = _ => Task.FromResult(IndexResponse(offline: true));
                await (Task)typeof(MainWindow).GetMethod("LoadDeviceIndexAsync", BindingFlags.Instance | BindingFlags.NonPublic)!
                    .Invoke(window, [Field<int>(window, "devicePanelGeneration"), CancellationToken.None])!;
                Field<Expander>(window, "deviceDirectoryDetails").IsExpanded = false; await Flush(window);
                Require(Field<TextBlock>(window, "deviceSourceHelp").IsVisible
                    && Field<TextBlock>(window, "deviceSourceHelp").Text.Contains(PartitionInventory.L("无法检查新的撤销记录", "newer revocations cannot be checked")),
                    "offline-github-warning-visible-with-details-collapsed-" + language);
                await VerifyCurrentHardwarePanel(window);
                checks.Add("current-pc-hardware-panel-advisory-match-empty-error-and-provider-selection-" + language);
                window.Close(); window = null;
            }
        }
        finally { window?.Close(); InstallerI18n.SetLanguage(originalLanguage); }
        var result = JsonSerializer.Serialize(new { passed = true, checks, captures, scope = "Synthetic WPF events and native WM_DROPFILES parsing; no physical installation or cross-integrity Explorer gesture acceptance", directory }, new JsonSerializerOptions { WriteIndented = true });
        await File.WriteAllTextAsync(Path.Combine(directory, "result.json"), result);
        return result;
    }

    private static bool Capture(FrameworkElement surface, string path)
    {
        var bitmap = new RenderTargetBitmap(Math.Max(1, (int)surface.ActualWidth), Math.Max(1, (int)surface.ActualHeight), 96, 96, PixelFormats.Pbgra32);
        bitmap.Render(surface);
        var pixels = new byte[bitmap.PixelWidth * bitmap.PixelHeight * 4]; bitmap.CopyPixels(pixels, bitmap.PixelWidth * 4, 0);
        if (!pixels.Where((_, index) => index % 4 == 3).Any(alpha => alpha != 0)) return false;
        var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap)); using var output = File.Create(path); encoder.Save(output); return true;
    }
    [DllImport("kernel32.dll")] private static extern IntPtr GlobalAlloc(uint flags, nuint bytes);
    [DllImport("kernel32.dll")] private static extern IntPtr GlobalLock(IntPtr block);
    [DllImport("kernel32.dll")] private static extern bool GlobalUnlock(IntPtr block);
    [DllImport("user32.dll")] private static extern IntPtr SendMessage(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);
}
