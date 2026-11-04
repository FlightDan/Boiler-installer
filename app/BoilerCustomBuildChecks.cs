using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;

namespace OxpInstaller;

internal static class BoilerCustomBuildChecks
{
    internal static InstallerOptionsPanel Options(MainWindow window, bool expandSettings = false)
    {
        var flags = BindingFlags.NonPublic | BindingFlags.Instance;
        if (expandSettings)
        {
            var settings = (Expander)typeof(MainWindow).GetField("customSettings", flags)!.GetValue(window)!;
            settings.IsExpanded = true;
            window.UpdateLayout();
        }
        return (InstallerOptionsPanel)typeof(MainWindow).GetField("optionsPanel", flags)!.GetValue(window)!;
    }

    internal static CheckBox? FindChoice(MainWindow window, string id)
    {
        var flags = BindingFlags.NonPublic | BindingFlags.Instance;
        var search = (TextBox)typeof(MainWindow).GetField("customSearch", flags)!.GetValue(window)!;
        search.Text = id;
        window.UpdateLayout();
        var rows = (StackPanel)typeof(MainWindow).GetField("customRows", flags)!.GetValue(window)!;
        IEnumerable<DependencyObject> Descendants(DependencyObject node)
        {
            foreach (var child in LogicalTreeHelper.GetChildren(node).OfType<DependencyObject>())
            {
                yield return child;
                foreach (var nested in Descendants(child)) yield return nested;
            }
        }
        return Descendants(rows).OfType<CheckBox>().SingleOrDefault(box => box.Tag is string tag && tag == id);
    }

    internal static bool HasVisibleChoice(MainWindow window, string id)
    {
        var flags = BindingFlags.NonPublic | BindingFlags.Instance;
        var search = (TextBox)typeof(MainWindow).GetField("customSearch", flags)!.GetValue(window)!;
        var previous = search.Text;
        try { return FindChoice(window, id)?.IsVisible == true; }
        finally { search.Text = previous; window.UpdateLayout(); }
    }

    internal static async Task<string> RunAsync(bool captureScreenshots = true)
    {
        var originalLanguage = InstallerI18n.Language;
        var captures = new List<string>();
        var visualBlocked = new List<string>();
        var directory = Path.Combine(InstallationController.Work, "work", "custom-build-checks-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        var window = new MainWindow(rehearsal: true) { Width = 1060, Height = 720, WindowState = WindowState.Normal, ShowActivated = false,
            DeviceIndexLoaderForChecks = _ => Task.FromResult(BoilerDeviceChecks.IndexResponse()),
            ModelCatalogLoaderForChecks = _ => Task.FromResult((JsonElement)typeof(BoilerModelCatalogChecks).GetMethod("Catalog", BindingFlags.NonPublic | BindingFlags.Static)!.Invoke(null, new object[] { true })!), DeviceHardwareReaderForChecks = () => null,
            CurrentHardwareReaderForChecks = () => [] };
        var type = typeof(MainWindow);
        object? Call(string name, params object?[] args) => type.GetMethod(name, BindingFlags.NonPublic | BindingFlags.Instance)!.Invoke(window, args);
        T Field<T>(string name) => (T)type.GetField(name, BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(window)!;
        void Require(bool condition, string name) { if (!condition) throw new InvalidOperationException(name); }
        try
        {
            ((PartitionPicker)window.FindName("PartitionSelector")).AutomaticReadEnabled = false;
            window.Show(); window.BoilerModeForChecks("custom");
            var options = Field<InstallerOptionsPanel>("optionsPanel");
            var indexPath = Path.Combine(Backend.Root, "cache", "model-bundles", "manifest.json");
            var packagePath = Path.Combine(Backend.Root, "cache", "model-bundles", "packages", "oxp3", "manifest.json");
            var bytes = await File.ReadAllBytesAsync(packagePath);
            var digest = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
            using var index = JsonDocument.Parse(await File.ReadAllBytesAsync(indexPath));
            var entry = index.RootElement.GetProperty("packages").EnumerateArray().Single(p => p.GetProperty("id").GetString() == "device:oxp3");
            Require(bytes.Length == entry.GetProperty("manifest_size").GetInt64() && digest == entry.GetProperty("manifest_sha256").GetString(), "fixture lock");
            using var document = JsonDocument.Parse(bytes);
            var prepared = type.GetNestedType("PreparedDeviceSelection", BindingFlags.NonPublic)!;
            void Commit(JsonElement package, string sha) => Call("CommitDeviceSelection", Activator.CreateInstance(prepared, sha[..24], sha, "developer-session", package, null)!);
            Commit(document.RootElement.Clone(), digest);
            window.PreviewStep(2);
            var boxes = Field<Dictionary<string, CheckBox>>("deviceLegacyChoices");
            var deviceComponents = Field<Dictionary<string, CheckBox>>("deviceComponentChoices");
            BoilerWirelessHardware.Device Hardware(Guid classGuid, string id) => new(classGuid, "fixture", "fixture", [id], []);
            var negativeHardware = BoilerCustomComponentHardware.Match([
                new(BoilerWirelessHardware.NetworkClass, "Intel Wi-Fi 7 BE201", "Intel", ["PCI\\VEN_8086&DEV_FFFF"], []),
                Hardware(BoilerGraphicsHardware.DisplayClass, "PCI\\VEN_1002&DEV_B081"),
                Hardware(Guid.Empty, "HDAUDIO\\FUNC_01&VEN_10EC&DEV_0246"),
                Hardware(Guid.Empty, "ACPI\\10EC5281\\0")]);
            Require(negativeHardware.Results.All(result => result.State == BoilerCustomComponentHardware.MatchState.NotFound),
                "current-pc-matcher-rejects-friendly-name-vendor-and-nearby-id-guessing");
            var otherIntel = BoilerCustomComponentHardware.Match([
                Hardware(BoilerGraphicsHardware.DisplayClass, "PCI\\VEN_8086&DEV_B081")]);
            Require(otherIntel.Results.Single(result => result.ComponentId == "intel-xevma").State == BoilerCustomComponentHardware.MatchState.Candidate,
                "other-intel-display-is-candidate-without-claiming-xe-driver");
            var idsBeforeHardwareDetection = (string)Call("CustomBuildIds")!;
            var planBeforeHardwareDetection = (string)Call("CustomBuildPlan")!;
            var componentSelectionsBeforeDetection = deviceComponents.ToDictionary(pair => pair.Key, pair => pair.Value.IsChecked);
            var optionSelectionsBeforeDetection = options.OptionBoxes.ToDictionary(pair => pair.Key, pair => pair.Value.IsChecked);
            window.CurrentHardwareReaderForChecks = () => [
                Hardware(BoilerGraphicsHardware.DisplayClass, "PCI\\VEN_8086&DEV_B080&SUBSYS_00000000"),
                Hardware(BoilerWirelessHardware.NetworkClass, "PCI\\VEN_8086&DEV_A840&SUBSYS_00000000"),
                Hardware(Guid.Empty, "HDAUDIO\\FUNC_01&VEN_10EC&DEV_0245&SUBSYS_00000000"),
                Hardware(Guid.Empty, "ACPI\\VEN_10EC&DEV_5280\\0")];
            var detectHardware = Field<Button>("currentHardwareDetectButton");
            detectHardware.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            for (var attempt = 0; attempt < 100 && Field<bool>("currentHardwareDetectionBusy"); attempt++)
                await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
            var hardwareDetection = Field<BoilerCustomComponentHardware.Detection>("currentHardwareDetection");
            Require(hardwareDetection.Results.Select(result => result.State).SequenceEqual([
                BoilerCustomComponentHardware.MatchState.Matched,
                BoilerCustomComponentHardware.MatchState.Candidate,
                BoilerCustomComponentHardware.MatchState.Matched,
                BoilerCustomComponentHardware.MatchState.Candidate]), "current-pc-custom-hardware-results-are-bounded-and-typed");
            var componentAliases = new Dictionary<string, string[]>(StringComparer.Ordinal)
            {
                ["intel-xevma"] = ["resource:intel-xevmafix-724", "oxp3:xevmafix", "x2-mini:xevmafix", "generic:xevmafix"],
                ["intel-be201-firmware"] = ["resource:intel-be201-firmware", "wifi:intel"],
                ["realtek-alc245-speaker"] = ["resource:oxp3-speaker-route", "oxp3:speaker-route"],
                ["bmi260-gyro"] = ["resource:bmi260-gyro"]
            };
            var enabledBuildIds = ((System.Collections.IEnumerable)Call("CustomBuildRows")!).Cast<object>()
                .Where(row => ((CheckBox)row.GetType().GetProperty("Source")!.GetValue(row)!).IsEnabled)
                .Select(row => (string)row.GetType().GetProperty("Id")!.GetValue(row)!)
                .ToHashSet(StringComparer.Ordinal);
            bool HasAvailableComponent(string componentId) => componentAliases[componentId].Any(enabledBuildIds.Contains);
            Require(hardwareDetection.Results.Where(result => result.State == BoilerCustomComponentHardware.MatchState.Matched).Any(result => HasAvailableComponent(result.ComponentId))
                && hardwareDetection.Results.Any(result => !HasAvailableComponent(result.ComponentId)),
                "current-pc-availability-fixture-includes-available-and-not-provided-results");
            var hardwareResultRows = Field<StackPanel>("currentHardwareResults");
            foreach (var language in new[] { "zh-CN", "en-US" })
            {
                InstallerI18n.SetLanguage(language); Call("RefreshCurrentHardwareUi");
                var availabilityByComponent = hardwareResultRows.Children.OfType<StackPanel>()
                    .ToDictionary(row => (string)row.Tag!, row => row.Children.OfType<TextBlock>().Single(text => Equals(text.Tag, "availability")).Text, StringComparer.Ordinal);
                foreach (var result in hardwareDetection.Results)
                {
                    var expected = result.ComponentId == "intel-be201-firmware"
                        ? language == "en-US" ? "BE201 on the destination is unconfirmed; firmware will not be installed." : "目标是否为 BE201 尚未确认，暂不安装固件。"
                        : language == "en-US"
                        ? HasAvailableComponent(result.ComponentId) ? "Related component: currently available (select it manually)." : "Related component: not currently provided. Detection does not establish Linux-driver or hardware support."
                        : HasAvailableComponent(result.ComponentId) ? "对应构件：当前可选（请手动选择）。" : "对应构件：尚未提供。识别结果不代表 Linux 驱动或硬件支持。";
                    Require(availabilityByComponent[result.ComponentId] == expected,
                        $"current-pc-related-component-availability-localizes-{result.ComponentId}-{language}");
                }
                var unavailableWirelessText = language == "en-US" ? "BE201 on the destination is unconfirmed; firmware will not be installed." : "目标是否为 BE201 尚未确认，暂不安装固件。";
                Require(options.RepairsExpander.Visibility == Visibility.Collapsed && !options.OptionBoxes["wifi:intel"].IsEnabled
                    && !HasAvailableComponent("intel-be201-firmware")
                    && availabilityByComponent["intel-be201-firmware"] == unavailableWirelessText,
                    $"current-pc-hidden-generic-wireless-is-not-advertised-as-available-{language}");
            }
            InstallerI18n.SetLanguage(originalLanguage); Call("RefreshCurrentHardwareUi");
            Require((string)Call("CustomBuildIds")! == idsBeforeHardwareDetection
                && (string)Call("CustomBuildPlan")! == planBeforeHardwareDetection
                && componentSelectionsBeforeDetection.All(pair => deviceComponents[pair.Key].IsChecked == pair.Value)
                && optionSelectionsBeforeDetection.All(pair => options.OptionBoxes[pair.Key].IsChecked == pair.Value),
                "current-pc-matching-results-do-not-change-component-selections");
            var idsBeforeHardwareRefresh = (string)Call("CustomBuildIds")!;
            detectHardware.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            for (var attempt = 0; attempt < 100 && Field<bool>("currentHardwareDetectionBusy"); attempt++)
                await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
            Require((string)Call("CustomBuildIds")! == idsBeforeHardwareRefresh, "current-pc-repeat-detection-preserves-custom-ids");
            var idsBeforeHardwareError = (string)Call("CustomBuildIds")!;
            window.CurrentHardwareReaderForChecks = () => throw new IOException("fixture current-pc inventory failure");
            detectHardware.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            for (var attempt = 0; attempt < 100 && Field<bool>("currentHardwareDetectionBusy"); attempt++)
                await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
            Require(Field<string>("currentHardwareDetectionError") != null && detectHardware.IsEnabled
                && (string)Call("CustomBuildIds")! == idsBeforeHardwareError
                && (string)Call("CustomBuildPlan")! == planBeforeHardwareDetection, "current-pc-error-retry-keeps-component-selections");
            window.CurrentHardwareReaderForChecks = () => [];
            var idsBeforeEmptyInventory = (string)Call("CustomBuildIds")!;
            detectHardware.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            for (var attempt = 0; attempt < 100 && Field<bool>("currentHardwareDetectionBusy"); attempt++)
                await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
            Require(Field<BoilerCustomComponentHardware.Detection>("currentHardwareDetection").Results.All(result => result.State == BoilerCustomComponentHardware.MatchState.NotFound)
                && (string)Call("CustomBuildIds")! == idsBeforeEmptyInventory
                && (string)Call("CustomBuildPlan")! == planBeforeHardwareDetection, "current-pc-empty-inventory-keeps-component-selections");
            var bmiForms = BoilerCustomComponentHardware.Match([
                Hardware(Guid.Empty, "ACPI\\10EC5280\\0"),
                Hardware(Guid.Empty, "ACPI\\VEN_10EC&DEV_5280\\0")]);
            Require(bmiForms.Results.Single(result => result.ComponentId == "bmi260-gyro").State == BoilerCustomComponentHardware.MatchState.Candidate,
                "current-pc-bmi260-acpi-short-and-long-forms-remain-candidates");
            var before = boxes.ToDictionary(p => p.Key, p => p.Value.IsChecked);
            var ids = (string)Call("CustomBuildIds")!;
            try { Call("ApplyCustomBuildIds", ids + "\nwrong-model:unknown"); throw new InvalidOperationException("invalid IDs accepted"); }
            catch (TargetInvocationException error) when (error.InnerException is InvalidOperationException) { }
            Require(before.All(p => boxes[p.Key].IsChecked == p.Value), "invalid paste must be atomic");
            foreach (var box in boxes.Values) box.IsChecked = false;
            Call("ApplyCustomBuildIds", "oxp3:opti2xmx");
            Require(boxes["oxp3:opti2xmx"].IsChecked == true && boxes["oxp3:decky"].IsChecked == true && options.OptionBoxes["core:decky"].IsChecked == true, "legacy dependency and provider closure");
            Require(!((string)Call("CustomBuildIds")!).Contains("core:decky", StringComparison.Ordinal), "effective provider export");
            var preserved = options.OptionBoxes["core:maintenance"].IsChecked;
            Call("ApplyCustomBuildIds", "oxp3:opti2xmx");
            Require(options.OptionBoxes["core:maintenance"].IsChecked == preserved, "append preserves unrelated selection");
            var proxy = FindChoice(window, "oxp3:opti2xmx")!;
            proxy.IsChecked = false;
            await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
            Require(!((string)Call("CustomBuildIds")!).Contains("oxp3:opti2xmx", StringComparison.Ordinal), "proxy removal updates IDs");
            var summary = Field<StackPanel>("customSelected").Children.OfType<TextBox>().Single(x => x.Name == "CustomBuildIdSummary");
            Require(summary.Text == (string)Call("CustomBuildIds")!, "summary tracks proxy removal");
            FindChoice(window, "oxp3:opti2xmx")!.IsChecked = true;
            await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
            Require(FindChoice(window, "oxp3:decky")!.IsChecked == true, "dependency proxy refreshed");
            type.GetField("reviewSeen", BindingFlags.NonPublic | BindingFlags.Instance)!.SetValue(window, true);
            var consent = (CheckBox)window.FindName("PhysicalConsent"); consent.IsChecked = true;
            Call("RefreshCustomBuild");
            Require(Field<bool>("reviewSeen") && consent.IsChecked == true, "model selector refresh preserves review and consent");
            Field<WrapPanel>("customTabs").Children.OfType<Button>().ElementAt(1).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            Require(Field<bool>("reviewSeen") && consent.IsChecked == true, "category tab preserves review and consent");
            var selectedOnly = Field<WrapPanel>("customChips").Children.OfType<CheckBox>().Single();
            selectedOnly.IsChecked = true;
            await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
            Require(Field<bool>("reviewSeen") && consent.IsChecked == true, "presentation refresh preserves review and consent");
            selectedOnly = Field<WrapPanel>("customChips").Children.OfType<CheckBox>().Single(); selectedOnly.IsChecked = false;
            FindChoice(window, "oxp3:opti2xmx")!.IsChecked = false;
            await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
            Require(!Field<bool>("reviewSeen") && consent.IsChecked != true, "real source selection invalidates review and consent");
            FindChoice(window, "oxp3:opti2xmx")!.IsChecked = true;
            await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
            var unavailable = boxes.FirstOrDefault(p => !p.Value.IsEnabled);
            if (unavailable.Key != null)
            {
                before = boxes.ToDictionary(p => p.Key, p => p.Value.IsChecked);
                try { Call("ApplyCustomBuildIds", "oxp3:opti2xmx\n" + unavailable.Key); throw new InvalidOperationException("unavailable ID accepted"); }
                catch (TargetInvocationException error) when (error.InnerException is InvalidOperationException) { }
                Require(before.All(p => boxes[p.Key].IsChecked == p.Value), "unavailable paste must be atomic");
            }
            var synthetic = JsonNode.Parse(bytes)!.AsObject();
            synthetic["components"]!.AsArray().Add(JsonNode.Parse("{\"id\":\"fixture:opt-in\",\"name\":{\"en\":\"Fixture\",\"zh\":\"Fixture\"},\"selection_policy\":\"explicit-opt-in\",\"files\":[]}")!);
            using var syntheticDoc = JsonDocument.Parse(synthetic.ToJsonString());
            Commit(syntheticDoc.RootElement.Clone(), new string('f', 64));
            Require(Field<Dictionary<string, CheckBox>>("deviceComponentChoices")["fixture:opt-in"].IsChecked != true, "explicit opt-in default");
            options.PasswordInput.Password = "private-test-password";
            var plan = (string)Call("CustomBuildPlan")!;
            Require(!plan.Contains("private-test-password", StringComparison.Ordinal) && !plan.Contains("developer-session", StringComparison.Ordinal), "plan contains sensitive data");
            var search = Field<TextBox>("customSearch"); search.Text = "fixture:opt-in";
            var rows = Field<StackPanel>("customRows");
            Require(rows.Children.OfType<Border>().Select(b => b.Child).OfType<StackPanel>().SelectMany(p => p.Children.OfType<CheckBox>()).Any(c => ((TextBlock)c.Content).Text.Contains("fixture:opt-in", StringComparison.OrdinalIgnoreCase)), "global ID search");
            search.Text = "no-such-custom-build-item";
            Require(!rows.Children.OfType<Border>().Any(), "search excludes unrelated rows");
            search.Text = ""; Commit(document.RootElement.Clone(), digest);
            var hardwareTitle = Field<TextBlock>("currentHardwareTitle");
            var hardwareIntroduction = Field<TextBlock>("currentHardwareIntro");
            var hardwareComponentName = type.GetMethod("CurrentHardwareName", BindingFlags.NonPublic | BindingFlags.Static)!;
            foreach (var language in new[] { "zh-CN", "en-US" })
            {
                InstallerI18n.SetLanguage(language); Call("RefreshCustomBuild");
                Call("RefreshCurrentHardwareUi");
                var expectedTitle = language == "en-US" ? "Detect this computer's hardware" : "识别当前电脑硬件";
                var expectedIntroduction = language == "en-US"
                    ? "Identify components on this Windows computer to help choose matching components."
                    : "识别当前 Windows 电脑的部件，辅助选择对应构件。";
                var expectedSpeaker = language == "en-US" ? "Realtek ALC245 speaker" : "Realtek ALC245 扬声器";
                var expectedGyro = language == "en-US" ? "BMI260 gyro" : "BMI260 陀螺仪";
                Require(hardwareTitle.Text == expectedTitle && hardwareIntroduction.Text == expectedIntroduction,
                    $"current-pc-hardware-detection-labels-localize-{language}");
                Require((string)hardwareComponentName.Invoke(null, ["realtek-alc245-speaker"])! == expectedSpeaker
                    && (string)hardwareComponentName.Invoke(null, ["bmi260-gyro"])! == expectedGyro,
                    $"current-pc-hardware-component-names-localize-{language}");
                if (!captureScreenshots) continue;
                foreach (var size in new[] { new Size(1060, 720), new Size(640, 480) })
                {
                    window.Width = size.Width; window.Height = size.Height; window.PreviewStep(2);
                    await window.Dispatcher.InvokeAsync(() => { window.UpdateLayout(); }, DispatcherPriority.ApplicationIdle);
                    var path = Path.Combine(directory, $"{language}-{size.Width}x{size.Height}.png");
                    try { await BoilerUiChecks.CaptureAsync((FrameworkElement)window.Content, path); captures.Add(path); }
                    catch (IOException error)
                    {
                        visualBlocked.Add($"{Path.GetFileName(path)}: {error.Message}");
                        break;
                    }
                }
                if (visualBlocked.Count > 0) break;
            }
            return JsonSerializer.Serialize(new { passed = true, visual_acceptance = !captureScreenshots ? "not-requested" : visualBlocked.Count == 0 ? "captures-produced-needs-review" : "blocked-empty-render", visualBlocked, checks = new[] { "fixture-lock", "current-pc-matcher-negative-identities", "current-pc-custom-hardware-results", "current-pc-related-component-availability", "current-pc-hidden-generic-wireless-not-advertised", "current-pc-detection-preserves-custom-ids", "current-pc-repeat-detection-preserves-custom-ids", "current-pc-error-retry-preserves-custom-ids", "current-pc-empty-inventory-preserves-custom-ids", "current-pc-bmi260-acpi-forms", "current-pc-hardware-detection-labels-localize", "current-pc-hardware-component-names-localize", "invalid-and-unavailable-paste-atomic", "legacy-dependency-provider", "effective-provider-export", "append-preserves-selection", "selection-invalidates-review-and-consent", "explicit-opt-in", "secret-free-plan", "global-id-search" }, captures });
        }
        finally { window.Close(); InstallerI18n.SetLanguage(originalLanguage); }
    }
}
