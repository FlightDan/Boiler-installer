using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

namespace OxpInstaller;

internal static class BoilerModelCatalogChecks
{
    private static T Field<T>(MainWindow window, string name) => (T)typeof(MainWindow).GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(window)!;
    private static object? Call(MainWindow window, string name, params object?[] args) => typeof(MainWindow).GetMethod(name, BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(window, args);
    private static JsonElement ChosenModelEntry(ComboBox list) => (JsonElement)((ComboBoxItem)list.SelectedItem).Tag;
    private static async Task Flush(MainWindow window) { window.UpdateLayout(); await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle); }
    private static async Task CaptureSourceView(MainWindow window, string directory, string language, string view, double width, List<string> captures, bool captureScreenshots)
    {
        window.Width = width; await Flush(window);
        if (!captureScreenshots) return;
        var path = Path.Combine(directory, $"{language}-{view}-{(int)width}.png");
        await BoilerUiChecks.CaptureAsync((FrameworkElement)window.Content, path);
        captures.Add(path);
    }
    private static async Task Wait(MainWindow window) { for (var i = 0; Field<bool>(window, "deviceActionBusy"); i++) { if (i > 500) throw new IOException("Timed out waiting for model action."); await Task.Delay(20); await Flush(window); } }
    internal static Task<string> RunAsync(bool captureScreenshots = true) => Application.Current.Dispatcher.CheckAccess() ? OnUiThread(captureScreenshots) : Application.Current.Dispatcher.InvokeAsync(() => OnUiThread(captureScreenshots)).Task.Unwrap();
    internal static Task<string> RunPluginMatrixAsync() => Application.Current.Dispatcher.CheckAccess() ? PluginMatrixOnUiThread()
        : Application.Current.Dispatcher.InvokeAsync(PluginMatrixOnUiThread).Task.Unwrap();

    private static async Task<string> PluginMatrixOnUiThread()
    {
        var directory = Path.Combine(Backend.Root, "logs", "model-plugin-matrix-" + DateTime.UtcNow.ToString("yyyyMMddTHHmmssfff"));
        Directory.CreateDirectory(directory);
        var checks = new List<string>(); var models = new List<object>();
        var originalLanguage = InstallerI18n.Language;
        void Require(bool condition, string name) { if (!condition) throw new IOException("Plugin matrix failed: " + name); checks.Add(name); }
        using var index = JsonDocument.Parse(await File.ReadAllBytesAsync(Path.Combine(Backend.Root, "cache/model-bundles/manifest.json")));
        var entries = index.RootElement.GetProperty("packages").EnumerateArray().ToArray();
        Require(new[] { "device:oxp3", "device:x2-mini" }.All(id => entries.Any(entry => entry.GetProperty("id").GetString() == id)), "both-supported-models-present");
        try
        {
            foreach (var entry in entries)
            {
                var id = entry.GetProperty("id").GetString()!;
                string packagePath;
                if (index.RootElement.GetProperty("kind").GetString() == "LocalTestModelBrandBundle")
                {
                    var target = Path.Combine(directory, id.Replace(':', '-'));
                    var extracted = await BoilerProtocol.RunAsync(["bundled-model-package", id, "--trust-local-test", "--destination", target], CancellationToken.None);
                    packagePath = Path.Combine(extracted.GetProperty("directory").GetString()!, "manifest.json");
                    MediaSource.Ordinary(packagePath, directory);
                }
                else
                {
                    packagePath = Path.GetFullPath(Path.Combine(Backend.Root, entry.GetProperty("directory").GetString()!, "manifest.json"));
                    MediaSource.Ordinary(packagePath, Path.Combine(Backend.Root, "cache/model-bundles"));
                }
                var bytes = await File.ReadAllBytesAsync(packagePath);
                var digest = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
                Require(bytes.Length == entry.GetProperty("manifest_size").GetInt64() && digest == entry.GetProperty("manifest_sha256").GetString(), id + "-manifest-lock");
                using var document = JsonDocument.Parse(bytes);
                var package = document.RootElement.Clone();
                Require(package.GetProperty("id").GetString() == id, id + "-manifest-identity");
                Require(SsdBootRepair.SupportsFixedModelEfi(package, new[] { "oxp3:legacy-adaptation" }), id + "-split-lock-efi-bridge");
                Require(!SsdBootRepair.SupportsFixedModelEfi(package, []), id + "-split-lock-efi-bridge-requires-adaptation");
                var resourceIds = package.TryGetProperty("resources", out var resourceRows)
                    ? resourceRows.EnumerateArray().Select(row => row.GetProperty("id").GetString()!).ToArray() : [];
                var defaultComponents = MainWindow.DefaultDeviceComponentIds(package, "developer-session");
                Require(defaultComponents.Contains("oxp3:legacy-adaptation"), id + "-default-selection-includes-adaptation");
                Require(resourceIds.All(defaultComponents.Contains), id + "-default-selection-includes-model-resources");
                var defaultExpected = ComponentOutcome.Expected(new SelectedOptions(1, [], ["core:maintenance"], "en-US", "system",
                    DeviceComponents: defaultComponents), package);
                Require(defaultComponents.All(defaultExpected.Contains), id + "-default-selection-resolves-as-components");
                var metadata = package.TryGetProperty("legacy_capabilities", out var capabilities)
                    ? capabilities.EnumerateArray().ToDictionary(item => item.GetProperty("id").GetString()!) : new Dictionary<string, JsonElement>();
                string[] Dependencies(string identity) => metadata[identity].GetProperty("dependencies").EnumerateArray().Select(item => item.GetString()!).ToArray();
                HashSet<string> Closure(string identity)
                {
                    var result = new HashSet<string>(); var pending = new Stack<string>(); pending.Push(identity);
                    while (pending.TryPop(out var next))
                        if (result.Add(next)) foreach (var dependency in Dependencies(next)) pending.Push(dependency);
                    return result;
                }
                var eligible = metadata.Keys.Where(identity => Closure(identity).All(dependency => metadata[dependency].GetProperty("available").GetBoolean())).ToHashSet();
                var plugins = metadata.Where(pair => pair.Value.GetProperty("category").GetString() == "plugins").Select(pair => pair.Key).ToArray();
                var pairCount = 0;
                foreach (var language in new[] { "zh-CN", "en-US" })
                {
                    InstallerI18n.SetLanguage(language);
                    var prefix = id + "/" + language;
                    var window = new MainWindow(rehearsal: true) {
                        DeviceIndexLoaderForChecks = _ => Task.FromResult(BoilerDeviceChecks.IndexResponse()),
                        ModelCatalogLoaderForChecks = _ => Task.FromResult(Catalog()), DeviceHardwareReaderForChecks = () => null };
                    Field<PartitionPicker>(window, "PartitionSelector").AutomaticReadEnabled = false;
                    try
                    {
                        window.BoilerModeForChecks("custom");
                        var preparedType = typeof(MainWindow).GetNestedType("PreparedDeviceSelection", BindingFlags.NonPublic)!;
                        void Commit() => Call(window, "CommitDeviceSelection", Activator.CreateInstance(preparedType, digest[..24], digest, "developer-session", package, null)!);
                        var core = Field<InstallerOptionsPanel>(window, "optionsPanel").OptionBoxes["core:decky"];
                        if (id == "device:x2-mini") core.IsChecked = false;
                        Commit();
                        var boxes = Field<Dictionary<string, CheckBox>>(window, "deviceLegacyChoices");
                        var allComponents = Field<Dictionary<string, CheckBox>>(window, "deviceComponentChoices");
                        var ssdProviders = package.GetProperty("components").EnumerateArray()
                            .Where(item => item.TryGetProperty("ssd_boot_policy", out _))
                            .Select(item => item.GetProperty("id").GetString()!).ToHashSet();
                        if (ssdProviders.Count != 0)
                            Require(ssdProviders.All(key => allComponents[key].IsChecked != true)
                                && !(window.CurrentOptions.DeviceComponents ?? []).Any(ssdProviders.Contains),
                                prefix + "-unmatched-ssd-provider-not-selected");
                        var components = allComponents.Where(pair => !ssdProviders.Contains(pair.Key)).ToDictionary();
                        if (id == "device:x2-mini" && components.ContainsKey("x2-mini:battery"))
                            Require(core.IsChecked == true && window.CurrentOptions.Plugins.Contains("core:decky"), prefix + "-import-defaults-enable-decky");
                        HashSet<string> Selected() => boxes.Where(pair => pair.Value.IsChecked == true).Select(pair => pair.Key).ToHashSet();
                        void Clear() { foreach (var box in boxes.Values) box.IsChecked = false; }
                        void SelectAll() { foreach (var identity in eligible) boxes[identity].IsChecked = true; }
                        void AssertPlan(string name)
                        {
                            var selected = Selected(); var plan = window.CurrentOptions;
                            var emitted = (plan.DeviceLegacyOptions?.Repairs ?? []).Concat(plan.DeviceLegacyOptions?.Plugins ?? []).ToHashSet();
                            Require(emitted.SetEquals(selected), prefix + "/" + name + "-request-matches-checkboxes");
                            Require((plan.DeviceComponents ?? []).ToHashSet().SetEquals(components.Where(pair => pair.Value.IsChecked == true).Select(pair => pair.Key)),
                                prefix + "/" + name + "-component-request-matches-checkboxes");
                            Require(selected.All(identity => Closure(identity).IsSubsetOf(selected)), prefix + "/" + name + "-dependencies-present");
                            var deviceDecky = selected.Contains("oxp3:decky");
                            Require((!deviceDecky || core.IsChecked == true) && plan.Plugins.Contains("core:decky") == (core.IsChecked == true && !deviceDecky),
                                prefix + "/" + name + "-single-decky-provider");
                            Require(Field<InstallerOptionsPanel>(window, "optionsPanel").OptionBoxes["core:maintenance"].IsChecked == true,
                                prefix + "/" + name + "-maintenance-preserved");
                            InstallerOptions.Validate(plan);
                        }
                        Require(components.Values.All(box => box.IsChecked == true), prefix + "-all-package-components-default-selected");
                        Require(boxes.Count == metadata.Count && Selected().SetEquals(eligible), prefix + "-all-available-default-selected");
                        Require(plugins.Where(eligible.Contains).All(identity => boxes[identity].IsChecked == true), prefix + "-every-available-plugin-default-selected");
                        Require(boxes.Where(pair => !eligible.Contains(pair.Key)).All(pair => !pair.Value.IsEnabled && pair.Value.IsChecked != true), prefix + "-unavailable-disabled");
                        AssertPlan("defaults");
                        await File.WriteAllTextAsync(Path.Combine(directory, id.Replace(':', '-') + "-" + language + "-default-options.json"),
                            JsonSerializer.Serialize(window.CurrentOptions, new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower, WriteIndented = true }));
                        foreach (var first in eligible.Order())
                        {
                            foreach (var second in eligible.Order())
                            {
                                Clear(); boxes[first].IsChecked = true;
                                Require(Selected().SetEquals(Closure(first)), prefix + "/" + first + "-individual-dependency-closure");
                                var before = Selected(); boxes[second].IsChecked = true;
                                var expected = new HashSet<string>(before); expected.UnionWith(Closure(second));
                                Require(Selected().SetEquals(expected), prefix + "/" + first + "+" + second + "-selection-only-adds-dependencies");
                                AssertPlan(first + "+" + second);
                                pairCount++;
                            }
                            Clear(); SelectAll(); boxes[first].IsChecked = false;
                            var expectedAfterRemoval = eligible.Where(identity => !Closure(identity).Contains(first)).ToHashSet();
                            Require(Selected().SetEquals(expectedAfterRemoval), prefix + "/" + first + "-removal-clears-only-dependents");
                            var retained = Selected(); boxes[first].IsChecked = true;
                            retained.UnionWith(Closure(first));
                            Require(retained.SetEquals(Selected()), prefix + "/" + first + "-reselection-only-adds-dependencies");
                            AssertPlan(first + "-reselected");
                            await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.Background);
                        }
                        if (eligible.Contains("oxp3:decky"))
                        {
                            Clear(); SelectAll(); core.IsChecked = false;
                            var expected = eligible.Where(identity => !Closure(identity).Contains("oxp3:decky")).ToHashSet();
                            Require(Selected().SetEquals(expected), prefix + "-common-decky-off-clears-only-dependents");
                            core.IsChecked = true; expected.Add("oxp3:decky");
                            Require(Selected().SetEquals(expected), prefix + "-common-decky-on-does-not-clear-other-plugins");
                            AssertPlan("common-decky-toggle");
                        }
                        else
                        {
                            foreach (var first in components.Keys.Order()) foreach (var second in components.Keys.Order())
                            {
                                foreach (var box in components.Values) box.IsChecked = false;
                                components[first].IsChecked = true; components[second].IsChecked = true;
                                Require(components[first].IsChecked == true && components[second].IsChecked == true, prefix + "/" + first + "+" + second + "-static-selection-preserved");
                                Require((window.CurrentOptions.DeviceComponents ?? []).ToHashSet().SetEquals(components.Where(pair => pair.Value.IsChecked == true).Select(pair => pair.Key)),
                                    prefix + "/" + first + "+" + second + "-static-request-matches-checkboxes");
                                pairCount++;
                            }
                            if (components.TryGetValue("x2-mini:battery", out var battery))
                            {
                                var microphone = components["x2-mini:microphone"];
                                foreach (var box in components.Values) box.IsChecked = false;
                                core.IsChecked = false; microphone.IsChecked = true;
                                Require(core.IsChecked != true && microphone.IsChecked == true, prefix + "-microphone-alone-does-not-enable-decky");
                                battery.IsChecked = true;
                                Require(core.IsChecked == true && microphone.IsChecked == true, prefix + "-battery-selects-decky-without-clearing-microphone");
                                core.IsChecked = false;
                                Require(battery.IsChecked != true && microphone.IsChecked == true, prefix + "-decky-off-clears-battery-only");
                                core.IsChecked = true;
                                Require(battery.IsChecked != true && microphone.IsChecked == true, prefix + "-decky-on-does-not-restore-battery");
                                battery.IsChecked = true;
                                Require(core.IsChecked == true && microphone.IsChecked == true, prefix + "-battery-reselection-only-adds-decky");
                                microphone.IsChecked = false; battery.IsChecked = false; core.IsChecked = false;
                                battery.IsChecked = true; microphone.IsChecked = true;
                                Require(core.IsChecked == true && microphone.IsChecked == true && battery.IsChecked == true,
                                    prefix + "-battery-then-microphone-preserves-both");
                                battery.IsChecked = false;
                                Require(core.IsChecked == true && microphone.IsChecked == true, prefix + "-battery-off-keeps-decky-and-microphone");
                                var all = Field<CheckBox>(window, "deviceAdaptationsChoice");
                                all.IsChecked = false; core.IsChecked = false; all.IsChecked = true;
                                Require(core.IsChecked == true && components.Values.All(box => box.IsChecked == true), prefix + "-bulk-select-enables-decky");
                                all.IsChecked = false;
                                Require(core.IsChecked == true && components.Values.All(box => box.IsChecked != true), prefix + "-bulk-clear-keeps-decky");
                                microphone.IsChecked = true; battery.IsChecked = true;
                                Require((window.CurrentOptions.DeviceComponents ?? []).ToHashSet().SetEquals(components.Where(pair => pair.Value.IsChecked == true).Select(pair => pair.Key))
                                    && window.CurrentOptions.Plugins.Contains("core:decky"), prefix + "-static-request-reflects-components-and-decky");
                            }
                        }
                        Clear(); SelectAll();
                        var beforeLanguage = JsonSerializer.Serialize(window.CurrentOptions.DeviceLegacyOptions);
                        var beforeComponents = (window.CurrentOptions.DeviceComponents ?? []).ToHashSet();
                        var beforePlugins = window.CurrentOptions.Plugins.ToHashSet();
                        InstallerI18n.SetLanguage(language == "zh-CN" ? "en-US" : "zh-CN");
                        Field<InstallerOptionsPanel>(window, "optionsPanel").Refresh(); Call(window, "RefreshDeviceOptionLabels");
                        Require(JsonSerializer.Serialize(window.CurrentOptions.DeviceLegacyOptions) == beforeLanguage, prefix + "-language-preserves-all-selections");
                        Require((window.CurrentOptions.DeviceComponents ?? []).ToHashSet().SetEquals(beforeComponents), prefix + "-language-preserves-static-components");
                        Require(window.CurrentOptions.Plugins.ToHashSet().SetEquals(beforePlugins), prefix + "-language-preserves-plugins");
                        InstallerI18n.SetLanguage(language);
                        Commit(); boxes = Field<Dictionary<string, CheckBox>>(window, "deviceLegacyChoices");
                        Require(Selected().SetEquals(eligible), prefix + "-same-package-reentry-preserves-defaults");
                        Require((window.CurrentOptions.DeviceComponents ?? []).ToHashSet().SetEquals(beforeComponents), prefix + "-same-package-reentry-preserves-static-components");
                        Require(window.CurrentOptions.Plugins.ToHashSet().SetEquals(beforePlugins), prefix + "-same-package-reentry-preserves-plugins");
                    }
                    finally { window.Close(); }
                }
                models.Add(new { id, version = package.GetProperty("version").GetString(), digest, capabilities = metadata.Count,
                    available = eligible.Count, plugins, pairs = pairCount, components = package.GetProperty("components").GetArrayLength() });
            }
        }
        finally { InstallerI18n.SetLanguage(originalLanguage); }
        var report = JsonSerializer.Serialize(new { passed = true, directory, models, checks,
            scope = "Actual locked bundle manifests and WPF selection events only. No payload execution, physical disk queries, installation or hardware acceptance." }, new JsonSerializerOptions { WriteIndented = true });
        await File.WriteAllTextAsync(Path.Combine(directory, "result.json"), report);
        return report;
    }

    private static JsonElement Catalog(bool bundled = true) => JsonSerializer.SerializeToElement(new
    {
        source = new { repository = "FlightDan/Boiler-installer", commit = new string('a', 40), subtree = "adaptations" }, offline = true,
        models = new[] { Model("device:oxp3", "guide:oxp3", "OXP3"), Model("device:x2-mini", "guide:x2-mini", "X2 Mini") },
        bundled_packages = bundled ? new[] { new { id = "device:oxp3", trust = "local-test", directory = "unused-metadata-path" } } : []
    });
    private static JsonElement MatchingIndex(bool revoked = false) => JsonSerializer.SerializeToElement(new
    {
        offline = true, index = new { sequence = 1, revocations = revoked ? new[] { new string('a', 64), new string('b', 64) } : [],
            packages = new[] { "a", "b" }.Select((hash, version) => new { id = "device:oxp3", version = "0.1." + version,
                name = "ONEXPLAYER 3", sha256 = new string(hash[0], 64), status = "experimental" }).ToArray() }
    });
    private static object Model(string id, string guideId, string name) => new
    {
        id, guide_id = guideId, name = new { zh = name, en = name }, guide_url = "https://github.com/FlightDan/Boiler-installer/blob/" + new string('a', 40) + "/adaptations/README.md",
        components = new[] { new { id = "pl4", name = new { zh = "PL4 待补", en = "PL4 pending" }, notes = new { zh = "未提供，不能安装", en = "Unavailable; cannot install" }, recommendation = "pending" } }
    };
    private static object Capability(string id, string category, string[] dependencies, bool available = true, bool selected = false) => new
    {
        id, category, name = new { zh = id + " 中文", en = id + " English" }, dependencies, available, @default = selected,
        reason = new { zh = "待补，不可安装", en = "Pending; unavailable" }
    };

    private static async Task<string> OnUiThread(bool captureScreenshots)
    {
        var directory = Path.Combine(Backend.Root, "logs", "model-catalog-" + DateTime.UtcNow.ToString("yyyyMMddTHHmmssfff"));
        Directory.CreateDirectory(directory);
        var checks = new List<string>(); var captures = new List<string>(); var languageBefore = InstallerI18n.Language;
        MainWindow? window = null;
        void Require(bool ok, string label) { if (!ok) throw new IOException("Model catalog check failed: " + label); checks.Add(label); }
        try
        {
            foreach (var language in new[] { "zh-CN", "en-US" })
            {
                InstallerI18n.SetLanguage(language);
                await CheckMergedModels(language, Require);
                await CheckVersionedModels(language, Require);
                await CheckFingerprintSelection(language, "device:oxp3", Require);
                await CheckFingerprintSelection(language, "device:x2-mini", Require);
                var bundleCalls = 0;
                window = new MainWindow(rehearsal: true) { Width = 1060, Height = 720, WindowState = WindowState.Normal, ShowActivated = false,
                    DeviceIndexLoaderForChecks = _ => Task.FromResult(BoilerDeviceChecks.IndexResponse()), ModelCatalogLoaderForChecks = _ => Task.FromResult(Catalog()),
                    DeviceHardwareReaderForChecks = () => null,
                    BundledModelLoaderForChecks = (_, _) => { bundleCalls++; throw new IOException("fixture request reached explicit trust boundary"); } };
                ((PartitionPicker)window.FindName("PartitionSelector")).AutomaticReadEnabled = false;
                window.Show(); window.BoilerModeForChecks("custom"); await Flush(window);
                var list = Field<ComboBox>(window, "deviceList");
                var use = Field<Button>(window, "inlineDeviceContinue");
                var sourceTabs = Field<WrapPanel>(window, "deviceSourceTabs");
                var bundledTab = Field<RadioButton>(window, "bundledDeviceTab");
                var onlineTab = Field<RadioButton>(window, "onlineDeviceTab");
                var importArea = Field<StackPanel>(window, "deviceImportArea");
                Require(sourceTabs.IsVisible && bundledTab.IsChecked == true && onlineTab.IsChecked != true
                    && list.Items.Count == 2 && list.SelectedIndex == 0,
                    language + "-bundled-source-defaults-when-a-local-package-is-present");
                Require(((JsonElement)((ComboBoxItem)list.Items[1]).Tag).GetProperty("guide_id").GetString() == "guide:oxp3"
                    && importArea.IsVisible,
                    language + "-bundled-tab-shows-its-model-guide-and-local-import");
                Require(bundleCalls == 0 && window.CurrentOptions.DevicePackage == null, language + "-loading-does-not-grant-trust");
                await CaptureSourceView(window, directory, language, "bundled-default", 1060, captures, captureScreenshots);
                await CaptureSourceView(window, directory, language, "bundled-default", 640, captures, captureScreenshots);
                window.Width = 1060; await Flush(window);
                onlineTab.IsChecked = true; await Flush(window);
                Require(list.SelectedIndex == 0 && list.Items.Count == 4 && importArea.IsVisible
                    && importArea.Children.OfType<Button>().Any(button => button.Name == "BrowseLocalDevice"),
                    language + "-online-tab-resets-list-and-shows-import-controls");
                await CaptureSourceView(window, directory, language, "online", 1060, captures, captureScreenshots);
                await CaptureSourceView(window, directory, language, "online", 640, captures, captureScreenshots);
                window.Width = 1060; await Flush(window);
                Require(((JsonElement)((ComboBoxItem)list.Items[1]).Tag).GetProperty("id").GetString() == "device:x2-mini"
                    && ((JsonElement)((ComboBoxItem)list.Items[1]).Tag).TryGetProperty("guide_id", out _),
                    language + "-online-tab-keeps-guide-only-models");
                list.SelectedIndex = 1; await Flush(window);
                Require(!use.IsEnabled && window.CurrentOptions.DevicePackage == null, language + "-x2-guide-without-package-cannot-install");
                var details = Field<StackPanel>(window, "modelCatalogDetails");
                var recommendations = (StackPanel)details.Children.OfType<Expander>().Single().Content;
                Require(details.IsVisible && recommendations.Children.OfType<TextBlock>().Any(block => block.Text.Contains("PL4"))
                    && !recommendations.Children.OfType<CheckBox>().Any(), language + "-pending-guide-items-are-read-only");
                list.SelectedIndex = 2; await Flush(window);
                var onlineEntry = (JsonElement)((ComboBoxItem)list.SelectedItem).Tag;
                Require(use.IsEnabled && (string)use.Content == PartitionInventory.L("使用所选机型包", "Use selected device package")
                    && Field<TextBlock>(window, "inlineDeviceStatus").Visibility == Visibility.Visible
                    && !string.IsNullOrWhiteSpace(Field<TextBlock>(window, "inlineDeviceStatus").Text)
                    && onlineEntry.GetProperty("id").GetString() == "device:fixture" && !onlineEntry.TryGetProperty("guide_id", out _)
                    && bundleCalls == 0 && window.CurrentOptions.DevicePackage == null,
                    language + "-online-tab-shows-signed-index-entries-without-granting-trust");
                bundledTab.IsChecked = true; await Flush(window);
                Require(list.SelectedIndex == 0 && list.Items.Count == 2 && importArea.IsVisible
                    && Field<TextBlock>(window, "deviceIndexStatus").Visibility == Visibility.Collapsed,
                    language + "-switching-back-keeps-local-import-and-hides-online-status");
                list.SelectedIndex = 1; await Flush(window);
                Require(ChosenModelEntry(list).GetProperty("guide_id").GetString() == "guide:oxp3"
                    && use.IsEnabled && bundleCalls == 0 && window.CurrentOptions.DevicePackage == null,
                    language + "-bundled-choice-is-available-separately-from-online-entry");
                foreach (var width in new[] { 1060d, 640d })
                {
                    window.Width = width; await Flush(window);
                    Require(details.ActualWidth > 100 && details.ActualWidth <= Field<StackPanel>(window, "deviceQuickPicker").ActualWidth + 1,
                        language + "-model-details-fit-" + width);
                    var actionPoint = use.TranslatePoint(new Point(0, 0), (UIElement)window.Content);
                    Require(actionPoint.Y >= 0 && actionPoint.Y + use.ActualHeight < ((FrameworkElement)window.Content).ActualHeight,
                        language + "-trust-action-visible-before-long-guides-" + width);
                    await CaptureSourceView(window, directory, language, "bundled-guide", width, captures, captureScreenshots);
                }
                use.RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); await Wait(window);
                Require(bundleCalls == 1 && window.CurrentOptions.DevicePackage == null, language + "-explicit-click-enters-bundle-loader-without-pretrust");

                // A minimal validated local fixture exercises the actual package anchoring path.
                var folder = Path.Combine(directory, language + "-bundle"); Directory.CreateDirectory(folder);
                var package = JsonSerializer.SerializeToElement(new { schema_version = 1, kind = "DevicePackage", id = "device:oxp3", version = "0.1.0",
                    name = new { zh = "本地测试", en = "Local test fixture" }, status = "experimental", source = new { kind = "local", path = "fixture/" + Path.GetFileName(directory) },
                    compatibility = new { }, files = Array.Empty<object>(), components = new object[] { new { schema_version = 1, kind = "ComponentManifest",
                        id = "oxp3:legacy-adaptation", version = "0.1.0", files = Array.Empty<object>(), hooks = new { } },
                        new { schema_version = 1, kind = "ComponentManifest", id = "oxp3:speaker-route", version = "0.1.0", files = Array.Empty<object>(),
                            available = false, reason = new { zh = "板级路由未验证", en = "Board route is not validated" } } } });
                var bytes = JsonSerializer.SerializeToUtf8Bytes(package); await File.WriteAllBytesAsync(Path.Combine(folder, "manifest.json"), bytes);
                var sha = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
                window.BundledModelLoaderForChecks = (_, _) => { bundleCalls++; return Task.FromResult(JsonSerializer.SerializeToElement(new { directory = folder, manifest_sha256 = sha, package })); };
                use.RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); await Wait(window);
                Require(window.CurrentOptions.DeviceManifestSha256 == sha && window.CurrentOptions.DeviceTrust == "developer-session",
                    language + "-bundled-package-uses-real-validation-and-session-trust: " + Field<TextBlock>(window, "deviceStatus").Text + " / " + Field<string?>(window, "deviceActionError"));
                Require(Field<int>(window, "step") == 1, language + "-single-first-layer-action-selects-package-and-opens-second-layer-source");
                var appliedPackage = window.CurrentOptions.DevicePackage;
                var appliedComponents = window.CurrentOptions.DeviceComponents?.ToArray() ?? [];
                onlineTab.IsChecked = true; await Flush(window);
                Require(list.SelectedIndex == 0 && window.CurrentOptions.DevicePackage == appliedPackage
                    && window.CurrentOptions.DeviceManifestSha256 == sha && window.CurrentOptions.DeviceTrust == "developer-session"
                    && window.CurrentOptions.DeviceComponents!.SequenceEqual(appliedComponents),
                    language + "-source-switch-resets-candidate-without-clearing-applied-package-or-options");
                Require(Field<TextBlock>(window, "deviceSourceStatus").Text.Contains(PartitionInventory.L("安装器自带", "Bundled with installer")),
                    language + "-applied-package-provenance-survives-online-tab-switch");
                bundledTab.IsChecked = true; await Flush(window);
                Require(window.CurrentOptions.DevicePackage == appliedPackage && window.CurrentOptions.DeviceManifestSha256 == sha
                    && window.CurrentOptions.DeviceTrust == "developer-session" && window.CurrentOptions.DeviceComponents!.SequenceEqual(appliedComponents),
                    language + "-source-switch-back-keeps-applied-package-and-options");
                await (Task)Call(window, "LoadModelCatalogAsync", Field<int>(window, "devicePanelGeneration"), CancellationToken.None)!;
                Require(window.CurrentOptions.DeviceManifestSha256 == sha && !Field<JsonElement>(window, "displayedDeviceMetadata").TryGetProperty("guide_id", out _)
                    && Field<StackPanel>(window, "modelCatalogDetails").Visibility == Visibility.Collapsed,
                    language + "-catalog-refresh-keeps-selected-package-details");
                Require(Field<Dictionary<string, CheckBox>>(window, "deviceLegacyChoices").Count == 0,
                    language + "-frozen-package-does-not-offer-dynamic-capabilities");
                Require(Field<Dictionary<string, CheckBox>>(window, "deviceComponentChoices").Values.All(
                    check => Field<StackPanel>(window, "deviceComponentsPanel").Children.Contains(check)),
                    language + "-static-component-checkboxes-are-visible-and-individually-selectable");
                var disabledSpeaker = Field<Dictionary<string, CheckBox>>(window, "deviceComponentChoices")["oxp3:speaker-route"];
                Require(!disabledSpeaker.IsEnabled && disabledSpeaker.IsChecked != true
                    && ((TextBlock)disabledSpeaker.Content).Text.Contains(PartitionInventory.L("板级路由未验证", "Board route is not validated"))
                    && !window.CurrentOptions.DeviceComponents!.Contains("oxp3:speaker-route"), language + "-unavailable-component-shown-and-not-selected");
                var selectAll=Field<CheckBox>(window,"deviceAdaptationsChoice");
                selectAll.IsChecked=false;selectAll.IsChecked=true;
                Require(disabledSpeaker.IsChecked != true && !window.CurrentOptions.DeviceComponents!.Contains("oxp3:speaker-route"),
                    language + "-bulk-selection-skips-unavailable-component");
                var optionsHost = (StackPanel)window.FindName("ComponentOptionsHost");
                var customBuild = Field<StackPanel>(window, "customBuild");
                var settings = Field<Expander>(window, "customSettings");
                Require(optionsHost.Children.Contains(customBuild) && optionsHost.Children.Contains(settings)
                    && ReferenceEquals(settings.Content, Field<InstallerOptionsPanel>(window, "optionsPanel"))
                    && optionsHost.Children.IndexOf(customBuild) < optionsHost.Children.IndexOf(Field<StackPanel>(window, "deviceComponentsPanel")),
                    language + "-custom-catalog-precedes-retained-device-controls-and-settings-remain-accessible");
                Call(window, "BuildLegacyDeviceOptions", JsonSerializer.SerializeToElement(new { legacy_capabilities = new[] { Capability("oxp3:descriptive", "repairs", []) } }), null);
                Require(Field<Dictionary<string, CheckBox>>(window, "deviceLegacyChoices").Count == 0,
                    language + "-legacy-capabilities-alone-do-not-authorize-edits");
                var dynamicPackage = JsonSerializer.SerializeToElement(new
                {
                    selection_contract = new { kind = "oxp3-legacy-options-v1", component = "oxp3:legacy-adaptation" },
                    legacy_capabilities = new[] { Capability("oxp3:base", "repairs", []), Capability("oxp3:dependent", "plugins", ["oxp3:base"], selected: true),
                        Capability("oxp3:pl4", "repairs", [], available: false), Capability("oxp3:blocked", "plugins", ["oxp3:pl4"]),
                        Capability("oxp3:decky", "plugins", []), Capability("oxp3:opti2xmx", "repairs", ["oxp3:decky"]) }
                });
                Field<StackPanel>(window, "deviceComponentsPanel").Children.Remove(disabledSpeaker);
                Call(window, "BuildLegacyDeviceOptions", dynamicPackage, null);
                var capabilities = Field<Dictionary<string, CheckBox>>(window, "deviceLegacyChoices");
                var consent = Field<CheckBox>(window, "deviceLegacyUpdateHold");
                Require(capabilities.Values.Where(box => box.IsEnabled).All(box => box.IsChecked == true),
                    language + "-all-available-adaptations-default-selected-including-dependencies");
                Require(!capabilities["oxp3:pl4"].IsEnabled && capabilities["oxp3:pl4"].IsChecked != true && !capabilities["oxp3:blocked"].IsEnabled,
                    language + "-pending-and-unavailable-dependencies-disabled");
                Require(consent.IsChecked == true && !((TextBlock)consent.Content).Text.Contains(PartitionInventory.L("我接受", "I accept")), language + "-first-update-pause-default-selected-with-short-label");
                InstallerOptions.Validate(window.CurrentOptions);
                consent.IsChecked = false;
                var rejected = false; try { InstallerOptions.Validate(window.CurrentOptions); } catch (IOException error) { rejected = error.Message.Contains(PartitionInventory.L("暂停", "pause")); }
                Require(rejected, language + "-missing-hold-consent-blocks-plan");
                consent.IsChecked = true; InstallerOptions.Validate(window.CurrentOptions);
                var json = JsonSerializer.SerializeToElement(window.CurrentOptions, new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower });
                Require(json.GetProperty("device_legacy_options").GetProperty("repairs")[0].GetString() == "oxp3:base"
                    && json.GetProperty("device_legacy_options").GetProperty("plugins")[0].GetString() == "oxp3:dependent"
                    && json.GetProperty("device_legacy_options").GetProperty("acknowledge_legacy_update_hold").GetBoolean(), language + "-selection-serializes-executable-options");
                capabilities["oxp3:base"].IsChecked = false;
                Require(capabilities["oxp3:dependent"].IsChecked != true && capabilities["oxp3:opti2xmx"].IsChecked == true,
                    language + "-clearing-dependency-clears-only-its-dependents");
                foreach (var box in capabilities.Values) box.IsChecked = false;
                Require(window.CurrentOptions.DeviceLegacyOptions == null && !window.CurrentOptions.DeviceComponents!.Contains("oxp3:legacy-adaptation"),
                    language + "-clearing-all-items-clears-bridge");
                json = JsonSerializer.SerializeToElement(window.CurrentOptions, new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower });
                Require(!json.TryGetProperty("device_legacy_options", out _), language + "-empty-selection-omits-legacy-options");
                capabilities["oxp3:dependent"].IsChecked = true;
                Require(window.CurrentOptions.DeviceLegacyOptions?.Repairs.Contains("oxp3:base") == true, language + "-manual-selection-closes-dependencies");
                var options = Field<InstallerOptionsPanel>(window, "optionsPanel");
                var coreDecky = options.OptionBoxes["core:decky"];
                var otherCore = options.Selection.Repairs.Concat(options.Selection.Plugins).Where(id => id != "core:decky").ToHashSet();
                coreDecky.IsChecked = true;
                Require(coreDecky.IsChecked == true && capabilities["oxp3:decky"].IsChecked == true,
                    language + "-common-decky-selects-device-decky");
                capabilities["oxp3:opti2xmx"].IsChecked = true;
                Require(capabilities["oxp3:decky"].IsChecked == true && coreDecky.IsChecked == true
                    && window.CurrentOptions.DeviceLegacyOptions?.Repairs.Contains("oxp3:opti2xmx") == true
                    && window.CurrentOptions.DeviceLegacyOptions?.Plugins.Contains("oxp3:decky") == true
                    && !window.CurrentOptions.Plugins.Contains("core:decky"), language + "-opti-keeps-both-checks-and-submits-device-provider-only");
                coreDecky.RaiseEvent(new RoutedEventArgs(CheckBox.CheckedEvent));
                Require(capabilities["oxp3:opti2xmx"].IsChecked == true, language + "-common-checked-keeps-opti");
                coreDecky.IsChecked = false;
                Require(capabilities["oxp3:decky"].IsChecked != true && capabilities["oxp3:opti2xmx"].IsChecked != true
                    && capabilities["oxp3:dependent"].IsChecked == true && capabilities["oxp3:base"].IsChecked == true,
                    language + "-clearing-common-decky-clears-dependent-only");
                capabilities["oxp3:opti2xmx"].IsChecked = true;
                capabilities["oxp3:decky"].IsChecked = false;
                Require(coreDecky.IsChecked != true && capabilities["oxp3:opti2xmx"].IsChecked != true,
                    language + "-clearing-device-decky-syncs-and-clears-dependent");
                Require(otherCore.SetEquals(options.Selection.Repairs.Concat(options.Selection.Plugins).Where(id => id != "core:decky"))
                    && options.OptionBoxes["core:maintenance"].IsChecked == true, language + "-decky-sync-keeps-other-core-options");
                Require(Field<TextBlock>(window, "deviceLegacyNotice").Text.Contains(PartitionInventory.L("默认勾选全部可用项目", "All available items are selected by default"))
                    && Field<TextBlock>(window, "deviceLegacyNotice").Text.Contains(PartitionInventory.L("Opti2XMX 使用机型包 Decky", "Opti2XMX uses device-package Decky")),
                    language + "-defaults-and-opti-dependency-explained-concisely");
                Call(window, "ClearLegacyDeviceOptions");
                var noDeviceDecky = JsonSerializer.SerializeToElement(new {
                    selection_contract = new { kind = "oxp3-legacy-options-v1", component = "oxp3:legacy-adaptation" },
                    legacy_capabilities = new[] { Capability("oxp3:base", "repairs", []), Capability("oxp3:decky", "plugins", [], available: false) }
                });
                coreDecky.IsChecked = true;
                Field<StackPanel>(window, "deviceComponentsPanel").Children.Remove(disabledSpeaker);
                Call(window, "BuildLegacyDeviceOptions", noDeviceDecky, null);
                capabilities = Field<Dictionary<string, CheckBox>>(window, "deviceLegacyChoices");
                Require(coreDecky.IsChecked == true && capabilities["oxp3:decky"].IsChecked != true
                    && window.CurrentOptions.Plugins.Contains("core:decky"), language + "-unavailable-device-decky-keeps-common-provider");
                var delayed = new TaskCompletionSource<JsonElement>();
                window.ModelCatalogLoaderForChecks = _ => delayed.Task; window.BoilerModeForChecks("custom"); await Flush(window);
                list = Field<ComboBox>(window, "deviceList"); list.SelectedIndex = 1;
                delayed.SetResult(Catalog()); await Flush(window);
                Require(((JsonElement)((ComboBoxItem)list.SelectedItem).Tag).GetProperty("id").GetString() == "device:fixture",
                    language + "-late-guides-preserve-manual-signed-choice");
                await CheckActualMetadata(window, language, directory, checks, captures, Require, captureScreenshots);
                window.Close(); window = null;
            }
        }
        finally { window?.Close(); InstallerI18n.SetLanguage(languageBefore); }
        var result = JsonSerializer.Serialize(new { passed = true, checks, captures, directory, scope = "Synthetic WPF events, minimal local fixture validation and selection serialization; no hardware installation" }, new JsonSerializerOptions { WriteIndented = true });
        await File.WriteAllTextAsync(Path.Combine(directory, "result.json"), result); return result;
    }

    private static async Task CheckFingerprintSelection(string language, string modelId, Action<bool, string> require)
    {
        var package = JsonSerializer.SerializeToElement(new {
            schema_version = 1, kind = "DevicePackage", id = modelId, version = "0.1.0-test.1",
            name = new { zh = "测试", en = "Fixture" }, status = "experimental", source = new { kind = "local", path = "fixture" },
            compatibility = new { }, files = Array.Empty<object>(),
            components = new object[] {
                new { id = "oxp3:legacy-adaptation", name = new { zh = "基础", en = "Base" } },
                new { id = "oxp3:fingerprint-unlock", name = new { zh = "指纹", en = "Fingerprint" }, @default = true }
            },
            selection_contract = new { kind = "oxp3-legacy-options-v1", component = "oxp3:legacy-adaptation" },
            legacy_capabilities = new[] { Capability("oxp3:decky", "plugins", []) }
        });
        var window = new MainWindow(rehearsal: true) { ShowActivated = false, WindowState = WindowState.Normal,
            Width = 1060, Height = 720 };
        try
        {
            var picker = (PartitionPicker)window.FindName("PartitionSelector");
            picker.AutomaticReadEnabled = false;
            window.Show(); window.BoilerModeForChecks("custom"); await Flush(window);
            var snapshot = System.Text.Json.Nodes.JsonNode.Parse(PartitionInventory.ExampleJson)!.AsArray();
            var disk = snapshot[0]!; disk["serial"] = "fingerprint-fixture";
            void Candidate(string guid)
            {
                disk["guid"] = guid; picker.LoadSnapshot(snapshot.ToJsonString(), false);
                picker.Partitions.SelectedItem = picker.Partitions.Items.Cast<PartitionRow>().First(row => row.Space != null);
                picker.CapacityInput.Text = "128";
                typeof(MainWindow).GetField("physicalInstallation", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(window, true);
                Call(window, "SyncFingerprintCandidateSelection");
            }
            void Confirm()
            {
                typeof(MainWindow).GetField("acknowledgedSelection", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(window, picker.Selection);
                Call(window, "SyncFingerprintSelection");
            }
            void Target(string guid) { Candidate(guid); Confirm(); }
            var preparedType = typeof(MainWindow).GetNestedType("PreparedDeviceSelection", BindingFlags.NonPublic)!;
            var digest = new string('d', 64);
            Call(window, "CommitDeviceSelection", Activator.CreateInstance(preparedType, digest[..24], digest, "developer-session", package, null)!);
            var components = Field<Dictionary<string, CheckBox>>(window, "deviceComponentChoices");
            var legacy = Field<Dictionary<string, CheckBox>>(window, "deviceLegacyChoices");
            var fingerprint = components["oxp3:fingerprint-unlock"];
            require(fingerprint.IsChecked == true && legacy["oxp3:decky"].IsChecked == true, language + "-fingerprint-default-and-decky");
            var all = Field<CheckBox>(window, "deviceAdaptationsChoice");
            all.IsChecked = false; all.IsChecked = true;
            require(fingerprint.IsChecked == true, language + "-bulk-selection-enables-fingerprint");
            fingerprint.IsChecked = false;
            var pendingOther = System.Text.Json.Nodes.JsonNode.Parse(package.GetRawText())!;
            pendingOther["id"] = modelId == "device:oxp3" ? "device:x2-mini" : "device:oxp3";
            using (var otherPendingDoc = JsonDocument.Parse(pendingOther.ToJsonString()))
                Call(window, "CommitDeviceSelection", Activator.CreateInstance(preparedType, new string('c', 24), new string('c', 64), "developer-session", otherPendingDoc.RootElement.Clone(), null)!);
            var otherPendingFingerprint = Field<Dictionary<string, CheckBox>>(window, "deviceComponentChoices")["oxp3:fingerprint-unlock"];
            require(otherPendingFingerprint.IsChecked == true, language + "-pending-cancellation-does-not-leak-to-other-model");
            otherPendingFingerprint.IsChecked = false;
            Call(window, "CommitDeviceSelection", Activator.CreateInstance(preparedType, digest[..24], digest, "developer-session", package, null)!);
            fingerprint = Field<Dictionary<string, CheckBox>>(window, "deviceComponentChoices")["oxp3:fingerprint-unlock"];
            require(fingerprint.IsChecked != true, language + "-pending-cancellation-survives-model-return");
            Target("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa");
            require(fingerprint.IsChecked != true, language + "-pre-target-cancellation-binds-on-confirmation");
            Call(window, "CommitDeviceSelection", Activator.CreateInstance(preparedType, digest[..24], digest, "developer-session", package, null)!);
            components = Field<Dictionary<string, CheckBox>>(window, "deviceComponentChoices");
            legacy = Field<Dictionary<string, CheckBox>>(window, "deviceLegacyChoices");
            fingerprint = components["oxp3:fingerprint-unlock"];
            require(fingerprint.IsChecked != true, language + "-fingerprint-cancellation-survives-refresh");
            var changedDigest = new string('e', 64);
            Call(window, "CommitDeviceSelection", Activator.CreateInstance(preparedType, changedDigest[..24], changedDigest, "developer-session", package, null)!);
            components = Field<Dictionary<string, CheckBox>>(window, "deviceComponentChoices");
            legacy = Field<Dictionary<string, CheckBox>>(window, "deviceLegacyChoices"); fingerprint = components["oxp3:fingerprint-unlock"];
            require(fingerprint.IsChecked != true, language + "-fingerprint-cancellation-survives-new-provider");
            fingerprint.IsChecked = true;
            Candidate("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb");
            require(fingerprint.IsChecked == true, language + "-different-target-default-on");
            fingerprint.IsChecked = false; Confirm();
            require(fingerprint.IsChecked != true, language + "-candidate-cancellation-binds-on-confirmation");
            Call(window, "CommitDeviceSelection", Activator.CreateInstance(preparedType, digest[..24], digest, "developer-session", package, null)!);
            components = Field<Dictionary<string, CheckBox>>(window, "deviceComponentChoices");
            legacy = Field<Dictionary<string, CheckBox>>(window, "deviceLegacyChoices"); fingerprint = components["oxp3:fingerprint-unlock"];
            require(fingerprint.IsChecked != true, language + "-candidate-cancellation-survives-new-provider");
            Target("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa");
            require(fingerprint.IsChecked == true, language + "-candidate-cancellation-does-not-affect-first-target");
            Candidate("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb");
            require(fingerprint.IsChecked != true, language + "-candidate-previously-disabled");
            fingerprint.IsChecked = true;
            Call(window, "CommitDeviceSelection", Activator.CreateInstance(preparedType, changedDigest[..24], changedDigest, "developer-session", package, null)!);
            components = Field<Dictionary<string, CheckBox>>(window, "deviceComponentChoices");
            legacy = Field<Dictionary<string, CheckBox>>(window, "deviceLegacyChoices"); fingerprint = components["oxp3:fingerprint-unlock"];
            require(fingerprint.IsChecked == true, language + "-candidate-enable-survives-provider-refresh-before-confirmation");
            Confirm(); require(fingerprint.IsChecked == true, language + "-candidate-enable-overrides-previous-disable");
            Target("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa");
            Candidate("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb");
            require(fingerprint.IsChecked == true, language + "-candidate-previously-enabled");
            fingerprint.IsChecked = false; Confirm();
            require(fingerprint.IsChecked != true, language + "-candidate-disable-overrides-previous-enable");
            Target("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa");
            require(fingerprint.IsChecked == true, language + "-candidate-overrides-do-not-affect-first-target");
            fingerprint.IsChecked = false;
            Target("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb");
            require(fingerprint.IsChecked != true, language + "-return-target-keeps-cancellation");
            Target("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa");
            fingerprint.IsChecked = true;
            Call(window, "CommitDeviceSelection", Activator.CreateInstance(preparedType, digest[..24], digest, "developer-session", package, null)!);
            components = Field<Dictionary<string, CheckBox>>(window, "deviceComponentChoices");
            legacy = Field<Dictionary<string, CheckBox>>(window, "deviceLegacyChoices"); fingerprint = components["oxp3:fingerprint-unlock"];
            require(fingerprint.IsChecked == true, language + "-fingerprint-reenabled-survives-new-provider");
            fingerprint.IsChecked = false;
            var otherPackage = System.Text.Json.Nodes.JsonNode.Parse(package.GetRawText())!;
            otherPackage["id"] = modelId == "device:oxp3" ? "device:x2-mini" : "device:oxp3";
            using var otherDocument = JsonDocument.Parse(otherPackage.ToJsonString());
            Call(window, "CommitDeviceSelection", Activator.CreateInstance(preparedType, changedDigest[..24], changedDigest, "developer-session", otherDocument.RootElement.Clone(), null)!);
            components = Field<Dictionary<string, CheckBox>>(window, "deviceComponentChoices"); fingerprint = components["oxp3:fingerprint-unlock"];
            require(fingerprint.IsChecked == true, language + "-different-model-default-on");
            Call(window, "CommitDeviceSelection", Activator.CreateInstance(preparedType, digest[..24], digest, "developer-session", package, null)!);
            components = Field<Dictionary<string, CheckBox>>(window, "deviceComponentChoices"); legacy = Field<Dictionary<string, CheckBox>>(window, "deviceLegacyChoices"); fingerprint = components["oxp3:fingerprint-unlock"];
            require(fingerprint.IsChecked != true, language + "-return-model-keeps-cancellation");
            legacy["oxp3:decky"].IsChecked = false;
            fingerprint.IsChecked = true;
            require(legacy["oxp3:decky"].IsChecked == true, language + "-fingerprint-selects-device-decky");
            legacy["oxp3:decky"].IsChecked = false;
            require(fingerprint.IsChecked != true, language + "-device-decky-off-clears-fingerprint");
            fingerprint.IsChecked = true;
            Field<InstallerOptionsPanel>(window, "optionsPanel").OptionBoxes["core:decky"].IsChecked = false;
            require(fingerprint.IsChecked != true, language + "-common-decky-off-clears-fingerprint");
        }
        finally { window.Close(); }
    }

    private static async Task CheckActualMetadata(MainWindow window, string language, string directory, List<string> checks, List<string> captures, Action<bool, string> require, bool captureScreenshots)
    {
        var indexPath = Path.Combine(Backend.Root, "cache", "model-bundles", "manifest.json");
        if (!File.Exists(indexPath)) { checks.Add(language + "-actual-metadata-skipped-cache-unavailable"); return; }
        using var index = JsonDocument.Parse(await File.ReadAllBytesAsync(indexPath));
        var entry = index.RootElement.GetProperty("packages").EnumerateArray().Single(item => item.GetProperty("id").GetString() == "device:oxp3");
        byte[] bytes;
        try { bytes = BundledModelManifest.Read("device:oxp3"); }
        catch (IOException) { checks.Add(language + "-actual-metadata-skipped-cache-unavailable"); return; }
        var sha = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
        require(bytes.Length == entry.GetProperty("manifest_size").GetInt64() && sha == entry.GetProperty("manifest_sha256").GetString(), language + "-actual-metadata-matches-cache-index");
        using var document = JsonDocument.Parse(bytes);
        var package = document.RootElement.Clone();
        var preparedType = typeof(MainWindow).GetNestedType("PreparedDeviceSelection", BindingFlags.NonPublic)!;
        object Prepared(string digest) => Activator.CreateInstance(preparedType, digest[..24], digest, "developer-session", package, null)!;
        void Commit(string digest) => Call(window, "CommitDeviceSelection", Prepared(digest));
        Commit(sha);
        var boxes = Field<Dictionary<string, CheckBox>>(window, "deviceLegacyChoices");
        var core = Field<InstallerOptionsPanel>(window, "optionsPanel").OptionBoxes["core:decky"];
        require(boxes.Count == 28 && boxes.Values.Where(box => box.IsEnabled).All(box => box.IsChecked == true)
            && boxes.Values.Where(box => !box.IsEnabled).All(box => box.IsChecked != true), language + "-actual-28-capabilities-all-available-selected");
        checks.Add(language + "-actual-count-available-" + boxes.Values.Count(box => box.IsEnabled) + "-unavailable-" + boxes.Values.Count(box => !box.IsEnabled));
        require(core.IsChecked == true && boxes["oxp3:decky"].IsChecked == true && boxes["oxp3:opti2xmx"].IsChecked == true
            && !window.CurrentOptions.Plugins.Contains("core:decky"), language + "-actual-default-decky-and-opti");
        window.Width = 1060; window.PreviewStep(2); await Flush(window);
        async Task Capture(CheckBox box, string name)
        {
            box.BringIntoView(); await Flush(window);
            var surface = (FrameworkElement)window.Content;
            var bitmap = new RenderTargetBitmap((int)surface.ActualWidth, (int)surface.ActualHeight, 96, 96, PixelFormats.Pbgra32);
            bitmap.Render(surface); var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap));
            var path = Path.Combine(directory, language + "-actual-" + name + ".png");
            using (var stream = File.Create(path)) encoder.Save(stream); captures.Add(path);
        }
        if (captureScreenshots) { await Capture(core, "core-decky"); await Capture(boxes["oxp3:opti2xmx"], "opti"); await Capture(boxes["oxp3:decky"], "device-decky"); }
        boxes["oxp3:opti2xmx"].IsChecked = false; boxes["oxp3:opti2xmx"].IsChecked = true;
        require(core.IsChecked == true && boxes["oxp3:decky"].IsChecked == true, language + "-actual-opti-reselection-keeps-decky");
        boxes["oxp3:opti2xmx"].IsChecked = false; Commit(sha);
        boxes = Field<Dictionary<string, CheckBox>>(window, "deviceLegacyChoices");
        require(boxes["oxp3:opti2xmx"].IsChecked != true && boxes["oxp3:decky"].IsChecked == true && core.IsChecked == true, language + "-same-package-preserves-optout");
        foreach (var box in boxes.Values) box.IsChecked = false;
        Commit(sha); boxes = Field<Dictionary<string, CheckBox>>(window, "deviceLegacyChoices");
        require(boxes.Values.All(box => box.IsChecked != true) && core.IsChecked != true, language + "-same-package-preserves-empty-selection");
        Commit(new string('f', 64)); boxes = Field<Dictionary<string, CheckBox>>(window, "deviceLegacyChoices");
        require(boxes.Values.Where(box => box.IsEnabled).All(box => box.IsChecked == true) && core.IsChecked == true, language + "-new-package-defaults-all-available");
    }

    private static async Task CheckVersionedModels(string language, Action<bool, string> require)
    {
        object Package(string version, char digest, string minimum = "0.1.5") => new {
            id="device:oxp3", version, sha256=new string(digest,64), name="Fixture", status="experimental",
            minimum_engine_version=minimum, source=new { kind="https", url="https://github.com/FlightDan/Boiler-installer/releases/download/v/package.zip" } };
        JsonElement Index(object[] packages, string[]? revoked=null) => JsonSerializer.SerializeToElement(new {
            offline=false, index=new { generated_at="2026-11-04T00:00:00Z", sequence=3, packages, revocations=revoked ?? [] } });
        var old=Package("0.1.0-preview.1",'a'); var newest=Package("0.2.0-preview.20261104.1",'b');
        var future=Package("0.3.0-preview.1",'c',"0.1.6");
        var index=Index([old,newest,future]);
        var window=new MainWindow(rehearsal:true) { Width=1060, Height=720, WindowState=WindowState.Normal, ShowActivated=false,
            DeviceIndexLoaderForChecks=_ => Task.FromResult(index), ModelCatalogLoaderForChecks=_ => Task.FromResult(Catalog(bundled:false)),
            DeviceHardwareReaderForChecks=() => null };
        ((PartitionPicker)window.FindName("PartitionSelector")).AutomaticReadEnabled=false;
        try
        {
            window.Show(); window.BoilerModeForChecks("custom"); await Flush(window);
            var list=Field<ComboBox>(window,"deviceList");
            JsonElement OXP() => list.Items.OfType<ComboBoxItem>().Where(i => i.Tag is JsonElement e && e.GetProperty("id").GetString()=="device:oxp3")
                .Select(i => (JsonElement)i.Tag).Single();
            async Task Refresh(JsonElement next) { index=next; await (Task)Call(window,"LoadDeviceIndexAsync",Field<int>(window,"devicePanelGeneration"),CancellationToken.None)!; await Flush(window); }
            require(OXP().GetProperty("sha256").GetString()==new string('b',64),language+"-highest-compatible-version");
            await Refresh(Index([future,newest,old]));
            require(OXP().GetProperty("sha256").GetString()==new string('b',64),language+"-highest-version-independent-of-order");
            await Refresh(Index([old,newest], [new string('b',64)]));
            require(OXP().GetProperty("sha256").GetString()==new string('a',64),language+"-revoked-highest-uses-previous");
            await Refresh(Index([future]));
            require(OXP().TryGetProperty("guide_id",out _),language+"-incompatible-only-falls-back-to-guide");
            await Refresh(Index([]));
            require(OXP().TryGetProperty("guide_id",out _),language+"-empty-falls-back-to-guide");
            await Refresh(Index([old]));
            list.SelectedItem=list.Items.OfType<ComboBoxItem>().Single(i => i.Tag is JsonElement e && e.GetProperty("id").GetString()=="device:oxp3");
            await Flush(window);
            var package=JsonSerializer.SerializeToElement(new { id="device:oxp3",version="0.1.0-preview.1",components=Array.Empty<object>() });
            var prepared=typeof(MainWindow).GetNestedType("PreparedDeviceSelection",BindingFlags.NonPublic)!;
            Call(window,"CommitDeviceSelection",Activator.CreateInstance(prepared,new string('a',24),new string('a',64),"signed-index",package,null)!);
            var before=window.CurrentOptions.DevicePackage; var beforeDigest=window.CurrentOptions.DeviceManifestSha256;
            await Refresh(Index([newest,old]));
            require(OXP().GetProperty("sha256").GetString()==new string('a',64) && window.CurrentOptions.DevicePackage==before
                && window.CurrentOptions.DeviceManifestSha256==beforeDigest,language+"-selected-version-and-source-survive-refresh");
            var sameSource= new { id="device:oxp3",version="0.1.0-preview.1",sha256=new string('a',64),name="Fixture",status="experimental",
                minimum_engine_version="0.1.5",source=new { kind="https",url="https://github.com/FlightDan/Boiler-installer/releases/download/other-tag/package.zip" } };
            await Refresh(Index([newest,sameSource]));
            require(window.CurrentOptions.DevicePackage==before && window.CurrentOptions.DeviceManifestSha256==beforeDigest,
                language+"-same-source-identity-with-new-transport-preserves-selection");
            var changedSource= new { id="device:oxp3",version="0.1.0-preview.1",sha256=new string('a',64),name="Fixture",status="experimental",
                minimum_engine_version="0.1.5",source=new { kind="https",url="https://github.com/Other/Repository/releases/download/v/package.zip" } };
            await Refresh(Index([changedSource,newest]));
            require(window.CurrentOptions.DevicePackage==null,language+"-changed-source-clears-selected-package");
            await Refresh(Index([old]));
            list.SelectedItem=list.Items.OfType<ComboBoxItem>().Single(i => i.Tag is JsonElement e && e.GetProperty("id").GetString()=="device:oxp3");
            await Flush(window);
            Call(window,"CommitDeviceSelection",Activator.CreateInstance(prepared,new string('a',24),new string('a',64),"signed-index",package,null)!);
            await Refresh(Index([Package("0.1.0-preview.1",'a',"0.1.6"),newest]));
            require(window.CurrentOptions.DevicePackage==null,language+"-incompatible-selected-package-cleared");
            await Refresh(Index([old]));
            list.SelectedItem=list.Items.OfType<ComboBoxItem>().Single(i => i.Tag is JsonElement e && e.GetProperty("id").GetString()=="device:oxp3");
            await Flush(window);
            Call(window,"CommitDeviceSelection",Activator.CreateInstance(prepared,new string('a',24),new string('a',64),"signed-index",package,null)!);
            await Refresh(Index([old,newest],[new string('a',64)]));
            require(window.CurrentOptions.DevicePackage==null,language+"-revoked-selected-package-cleared");
            foreach (var (low,high) in new[] { ("0.1.0-preview.9","0.1.0-preview.10"), ("0.1.0-preview.10","0.1.0"),
                ("0.1.0+maintenance.2","0.1.0+maintenance.10"), ("0.1.0-9","0.1.0-alpha"), ("0.1.0","0.2.0-preview.1") })
                require(DevicePackageVersion.Comparer.Compare(low,high)<0,language+"-protocol-version-order-"+low+"-"+high);
        }
        finally { window.Close(); }
    }

    private static async Task CheckMergedModels(string language, Action<bool, string> require)
    {
        MainWindow? window = null;
        MainWindow Open(Func<CancellationToken, Task<JsonElement>> index, Func<CancellationToken, Task<JsonElement>> catalog)
        {
            var result = new MainWindow(rehearsal: true) { Width = 1060, Height = 720, WindowState = WindowState.Normal, ShowActivated = false,
                DeviceIndexLoaderForChecks = index, ModelCatalogLoaderForChecks = catalog, DeviceHardwareReaderForChecks = () => null };
            ((PartitionPicker)result.FindName("PartitionSelector")).AutomaticReadEnabled = false;
            result.Show(); result.BoilerModeForChecks("custom"); return result;
        }
        try
        {
            window = Open(_ => Task.FromResult(MatchingIndex()), _ => Task.FromResult(Catalog())); await Flush(window);
            var list = Field<ComboBox>(window, "deviceList");
            var sourceTabs = Field<WrapPanel>(window, "deviceSourceTabs");
            var bundledTab = Field<RadioButton>(window, "bundledDeviceTab");
            var onlineTab = Field<RadioButton>(window, "onlineDeviceTab");
            require(sourceTabs.IsVisible && bundledTab.IsChecked == true && list.Items.Count == 2
                && ((JsonElement)((ComboBoxItem)list.Items[1]).Tag).GetProperty("guide_id").GetString() == "guide:oxp3",
                language + "-bundled-model-is-default-and-lists-only-the-local-package-choice");
            list.SelectedIndex = 1; await Flush(window);
            require(ChosenModelEntry(list).TryGetProperty("guide_id", out _) && window.CurrentOptions.DevicePackage == null
                && Field<Button>(window, "inlineDeviceContinue").IsEnabled, language + "-local-package-choice-does-not-grant-trust");
            onlineTab.IsChecked = true; await Flush(window);
            require(sourceTabs.IsVisible && onlineTab.IsChecked == true && list.SelectedIndex == 0 && list.Items.Count == 3,
                language + "-online-switch-clears-the-uncommitted-local-candidate");
            var onlineGuide = (JsonElement)((ComboBoxItem)list.Items[1]).Tag;
            var onlineSigned = (JsonElement)((ComboBoxItem)list.Items[2]).Tag;
            require(onlineGuide.GetProperty("id").GetString() == "device:x2-mini" && onlineGuide.TryGetProperty("guide_id", out _)
                && onlineSigned.GetProperty("id").GetString() == "device:oxp3" && !onlineSigned.TryGetProperty("guide_id", out _)
                && onlineSigned.GetProperty("sha256").GetString() == new string('b', 64),
                language + "-same-model-online-signed-package-is-separate-and-preferred-over-bundled-guide");
            list.SelectedIndex = 2; await Flush(window);
            require(ChosenModelEntry(list).GetProperty("id").GetString() == "device:oxp3"
                && !ChosenModelEntry(list).TryGetProperty("guide_id", out _) && window.CurrentOptions.DevicePackage == null
                && Field<Button>(window, "inlineDeviceContinue").IsEnabled
                && Field<StackPanel>(window, "modelCatalogDetails").Visibility == Visibility.Collapsed,
                language + "-online-signed-choice-keeps-package-validation-explicit");
            window.DeviceIndexLoaderForChecks = _ => Task.FromResult(MatchingIndex(revoked: true));
            await (Task)Call(window, "LoadDeviceIndexAsync", Field<int>(window, "devicePanelGeneration"), CancellationToken.None)!;
            require(ChosenModelEntry(list).GetProperty("id").GetString() == "device:oxp3"
                && ChosenModelEntry(list).TryGetProperty("guide_id", out _), language + "-bundled-guide-fallback-on-online-tab");
            window.Close(); window = null;

            var delayedCatalog = new TaskCompletionSource<JsonElement>();
            window = Open(_ => Task.FromResult(MatchingIndex()), _ => delayedCatalog.Task); await Flush(window);
            list = Field<ComboBox>(window, "deviceList");
            sourceTabs = Field<WrapPanel>(window, "deviceSourceTabs");
            onlineTab = Field<RadioButton>(window, "onlineDeviceTab");
            require(sourceTabs.Visibility == Visibility.Collapsed && onlineTab.IsChecked == true && list.Items.Count == 2,
                language + "-source-stays-online-until-bundled-catalog-is-known");
            list.SelectedIndex = 1; await Flush(window);
            var digest = ChosenModelEntry(list).GetProperty("sha256").GetString();
            delayedCatalog.SetResult(Catalog()); await Flush(window);
            require(sourceTabs.IsVisible && onlineTab.IsChecked == true && list.Items.Count == 3
                && ChosenModelEntry(list).GetProperty("id").GetString() == "device:oxp3"
                && ChosenModelEntry(list).GetProperty("sha256").GetString() == digest
                && !ChosenModelEntry(list).TryGetProperty("guide_id", out _)
                && !Field<JsonElement>(window, "displayedDeviceMetadata").TryGetProperty("guide_id", out _)
                && window.CurrentOptions.DevicePackage == null,
                language + "-late-bundle-catalog-keeps-manual-online-package-choice");
            window.Close(); window = null;

            var delayedIndex = new TaskCompletionSource<JsonElement>();
            window = Open(_ => delayedIndex.Task, _ => Task.FromResult(Catalog())); await Flush(window);
            list = Field<ComboBox>(window, "deviceList");
            sourceTabs = Field<WrapPanel>(window, "deviceSourceTabs");
            bundledTab = Field<RadioButton>(window, "bundledDeviceTab");
            require(sourceTabs.IsVisible && bundledTab.IsChecked == true && list.Items.Count == 2,
                language + "-bundled-default-does-not-wait-for-online-index");
            list.SelectedIndex = 1; await Flush(window);
            delayedIndex.SetResult(MatchingIndex()); await Flush(window);
            require(sourceTabs.IsVisible && bundledTab.IsChecked == true && list.Items.Count == 2
                && ChosenModelEntry(list).TryGetProperty("guide_id", out _)
                && Field<StackPanel>(window, "modelCatalogDetails").IsVisible && window.CurrentOptions.DevicePackage == null,
                language + "-late-signed-index-does-not-enter-bundled-tab-or-grant-trust");
            window.Close(); window = null;

            delayedIndex = new TaskCompletionSource<JsonElement>();
            window = Open(_ => delayedIndex.Task, _ => Task.FromResult(Catalog(bundled: false))); await Flush(window);
            list = Field<ComboBox>(window, "deviceList");
            sourceTabs = Field<WrapPanel>(window, "deviceSourceTabs");
            onlineTab = Field<RadioButton>(window, "onlineDeviceTab");
            require(sourceTabs.Visibility == Visibility.Collapsed && onlineTab.IsChecked == true && list.Items.Count == 3,
                language + "-without-bundled-model-tabs-are-hidden-and-online-is-default");
            list.SelectedIndex = 1; await Flush(window);
            require(!Field<Button>(window, "inlineDeviceContinue").IsEnabled, language + "-guide-only-model-stays-read-only");
            delayedIndex.SetResult(MatchingIndex()); await Flush(window);
            require(sourceTabs.Visibility == Visibility.Collapsed && onlineTab.IsChecked == true
                && list.Items.Count == 3 && ChosenModelEntry(list).GetProperty("sha256").GetString() == new string('b', 64)
                && Field<Button>(window, "inlineDeviceContinue").IsEnabled && window.CurrentOptions.DevicePackage == null
                && Field<StackPanel>(window, "modelCatalogDetails").Visibility == Visibility.Collapsed
                && !Field<JsonElement>(window, "displayedDeviceMetadata").TryGetProperty("guide_id", out _),
                language + "-guide-without-bundle-uses-late-signed-fallback-and-refreshes-details");
            window.DeviceIndexLoaderForChecks = _ => Task.FromResult(MatchingIndex(revoked: true));
            await (Task)Call(window, "LoadDeviceIndexAsync", Field<int>(window, "devicePanelGeneration"), CancellationToken.None)!;
            require(sourceTabs.Visibility == Visibility.Collapsed && list.Items.Count == 3 && ChosenModelEntry(list).TryGetProperty("guide_id", out _)
                && !Field<Button>(window, "inlineDeviceContinue").IsEnabled && window.CurrentOptions.DevicePackage == null,
                language + "-revoked-signed-packages-fall-back-to-read-only-guide");
        }
        finally { window?.Close(); }
    }
}
