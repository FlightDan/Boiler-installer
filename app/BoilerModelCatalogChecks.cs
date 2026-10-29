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
    private static async Task Flush(MainWindow window) { window.UpdateLayout(); await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle); }
    private static async Task Wait(MainWindow window) { for (var i = 0; Field<bool>(window, "deviceActionBusy"); i++) { if (i > 500) throw new IOException("Timed out waiting for model action."); await Task.Delay(20); await Flush(window); } }
    internal static Task<string> RunAsync() => Application.Current.Dispatcher.CheckAccess() ? OnUiThread() : Application.Current.Dispatcher.InvokeAsync(OnUiThread).Task.Unwrap();

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

    private static async Task<string> OnUiThread()
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
                var bundleCalls = 0;
                window = new MainWindow(rehearsal: true) { Width = 1060, Height = 720, WindowState = WindowState.Normal, ShowActivated = false,
                    DeviceIndexLoaderForChecks = _ => Task.FromResult(BoilerDeviceChecks.IndexResponse()), ModelCatalogLoaderForChecks = _ => Task.FromResult(Catalog()),
                    DeviceHardwareReaderForChecks = () => null,
                    BundledModelLoaderForChecks = (_, _) => { bundleCalls++; throw new IOException("fixture request reached explicit trust boundary"); } };
                ((PartitionPicker)window.FindName("PartitionSelector")).AutomaticReadEnabled = false;
                window.Show(); window.BoilerModeForChecks("custom"); await Flush(window);
                var list = Field<ComboBox>(window, "deviceList");
                var use = Field<Button>(window, "inlineDeviceContinue");
                Require(list.Items.Count == 4, language + "-guides-and-signed-packages-coexist");
                Require(bundleCalls == 0 && window.CurrentOptions.DevicePackage == null, language + "-loading-does-not-grant-trust");
                list.SelectedIndex = 2; await Flush(window);
                Require(!use.IsEnabled && window.CurrentOptions.DevicePackage == null, language + "-x2-guide-without-package-cannot-install");
                var details = Field<StackPanel>(window, "modelCatalogDetails");
                var recommendations = (StackPanel)details.Children.OfType<Expander>().Single().Content;
                Require(details.IsVisible && recommendations.Children.OfType<TextBlock>().Any(block => block.Text.Contains("PL4"))
                    && !recommendations.Children.OfType<CheckBox>().Any(), language + "-pending-guide-items-are-read-only");
                list.SelectedIndex = 1; await Flush(window);
                Require(use.IsEnabled && ((TextBlock)use.Content).Text == PartitionInventory.L("使用所选机型包", "Use selected device package")
                    && Field<TextBlock>(window, "inlineDeviceStatus").Visibility == Visibility.Collapsed
                    && bundleCalls == 0 && window.CurrentOptions.DevicePackage == null, language + "-oxp3-simple-action-without-test-warning");
                foreach (var width in new[] { 1060d, 640d })
                {
                    window.Width = width; await Flush(window);
                    Require(details.ActualWidth > 100 && details.ActualWidth <= Field<StackPanel>(window, "deviceQuickPicker").ActualWidth + 1,
                        language + "-model-details-fit-" + width);
                    var actionPoint = use.TranslatePoint(new Point(0, 0), (UIElement)window.Content);
                    Require(actionPoint.Y >= 0 && actionPoint.Y + use.ActualHeight < ((FrameworkElement)window.Content).ActualHeight,
                        language + "-trust-action-visible-before-long-guides-" + width);
                    var path = Path.Combine(directory, language + "-" + width + ".png");
                    var surface = (FrameworkElement)window.Content;
                    var bitmap = new RenderTargetBitmap((int)surface.ActualWidth, (int)surface.ActualHeight, 96, 96, PixelFormats.Pbgra32);
                    bitmap.Render(surface); var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap));
                    using (var stream = File.Create(path)) encoder.Save(stream); captures.Add(path);
                }
                use.RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); await Wait(window);
                Require(bundleCalls == 1 && window.CurrentOptions.DevicePackage == null, language + "-explicit-click-enters-bundle-loader-without-pretrust");

                // A minimal validated local fixture exercises the actual package anchoring path.
                var folder = Path.Combine(directory, language + "-bundle"); Directory.CreateDirectory(folder);
                var package = JsonSerializer.SerializeToElement(new { schema_version = 1, kind = "DevicePackage", id = "device:oxp3", version = "0.1.0",
                    name = new { zh = "本地测试", en = "Local test fixture" }, status = "experimental", source = new { kind = "local", path = "fixture" },
                    compatibility = new { }, files = Array.Empty<object>(), components = new[] { new { schema_version = 1, kind = "ComponentManifest",
                        id = "oxp3:legacy-adaptation", version = "0.1.0", files = Array.Empty<object>(), hooks = new { } } } });
                var bytes = JsonSerializer.SerializeToUtf8Bytes(package); await File.WriteAllBytesAsync(Path.Combine(folder, "manifest.json"), bytes);
                var sha = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
                window.BundledModelLoaderForChecks = (_, _) => { bundleCalls++; return Task.FromResult(JsonSerializer.SerializeToElement(new { directory = folder, manifest_sha256 = sha, package })); };
                use.RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); await Wait(window);
                Require(window.CurrentOptions.DeviceManifestSha256 == sha && window.CurrentOptions.DeviceTrust == "developer-session",
                    language + "-bundled-package-uses-real-validation-and-session-trust");
                await (Task)Call(window, "LoadModelCatalogAsync", Field<int>(window, "devicePanelGeneration"), CancellationToken.None)!;
                Require(window.CurrentOptions.DeviceManifestSha256 == sha && !Field<JsonElement>(window, "displayedDeviceMetadata").TryGetProperty("guide_id", out _)
                    && Field<StackPanel>(window, "modelCatalogDetails").Visibility == Visibility.Collapsed,
                    language + "-catalog-refresh-keeps-selected-package-details");
                Require(Field<Dictionary<string, CheckBox>>(window, "deviceLegacyChoices").Count == 0,
                    language + "-frozen-package-does-not-offer-dynamic-capabilities");
                Require(Field<Dictionary<string, CheckBox>>(window, "deviceComponentChoices").Values.All(
                    check => Field<StackPanel>(window, "deviceComponentsPanel").Children.Contains(check)),
                    language + "-static-component-checkboxes-are-visible-and-individually-selectable");
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
                coreDecky.IsChecked = true;
                var otherCore = options.Selection.Repairs.Concat(options.Selection.Plugins).Where(id => id != "core:decky").ToHashSet();
                Require(coreDecky.IsChecked == true && capabilities["oxp3:decky"].IsChecked != true && capabilities["oxp3:opti2xmx"].IsChecked != true,
                    language + "-manual-common-decky-selection-keeps-device-provider-off");
                capabilities["oxp3:decky"].IsChecked = true;
                Require(coreDecky.IsChecked != true && capabilities["oxp3:decky"].IsChecked == true,
                    language + "-explicit-device-decky-clears-common-provider");
                coreDecky.IsChecked = true;
                Require(capabilities["oxp3:decky"].IsChecked != true && capabilities["oxp3:opti2xmx"].IsChecked != true,
                    language + "-explicit-common-decky-clears-device-provider");
                capabilities["oxp3:opti2xmx"].IsChecked = true;
                Require(capabilities["oxp3:decky"].IsChecked == true && coreDecky.IsChecked != true
                    && window.CurrentOptions.DeviceLegacyOptions?.Repairs.Contains("oxp3:opti2xmx") == true
                    && !window.CurrentOptions.Plugins.Contains("core:decky"), language + "-opti-dependency-selects-device-decky-only");
                coreDecky.IsChecked = true;
                Require(capabilities["oxp3:decky"].IsChecked != true && capabilities["oxp3:opti2xmx"].IsChecked != true
                    && capabilities["oxp3:dependent"].IsChecked == true && capabilities["oxp3:base"].IsChecked == true,
                    language + "-common-provider-clears-opti-without-unrelated-legacy-changes");
                Require(otherCore.SetEquals(options.Selection.Repairs.Concat(options.Selection.Plugins).Where(id => id != "core:decky"))
                    && options.OptionBoxes["core:maintenance"].IsChecked == true, language + "-provider-switch-keeps-maintenance-and-other-core-options");
                Require(Field<TextBlock>(window, "deviceLegacyNotice").Text.Contains(PartitionInventory.L("默认勾选全部可用项目", "All available items are selected by default"))
                    && Field<TextBlock>(window, "deviceLegacyNotice").Text.Contains(PartitionInventory.L("Opti2XMX 使用机型包 Decky", "Opti2XMX uses device-package Decky")),
                    language + "-defaults-and-opti-dependency-explained-concisely");
                var delayed = new TaskCompletionSource<JsonElement>();
                window.ModelCatalogLoaderForChecks = _ => delayed.Task; window.BoilerModeForChecks("custom"); await Flush(window);
                list = Field<ComboBox>(window, "deviceList"); list.SelectedIndex = 1;
                delayed.SetResult(Catalog()); await Flush(window);
                Require(((JsonElement)((ComboBoxItem)list.SelectedItem).Tag).GetProperty("id").GetString() == "device:fixture",
                    language + "-late-guides-preserve-manual-signed-choice");
                window.Close(); window = null;
            }
        }
        finally { window?.Close(); InstallerI18n.SetLanguage(languageBefore); }
        var result = JsonSerializer.Serialize(new { passed = true, checks, captures, directory, scope = "Synthetic WPF events, minimal local fixture validation and selection serialization; no hardware installation" }, new JsonSerializerOptions { WriteIndented = true });
        await File.WriteAllTextAsync(Path.Combine(directory, "result.json"), result); return result;
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
        JsonElement Chosen(ComboBox list) => (JsonElement)((ComboBoxItem)list.SelectedItem).Tag;
        try
        {
            window = Open(_ => Task.FromResult(MatchingIndex()), _ => Task.FromResult(Catalog())); await Flush(window);
            var list = Field<ComboBox>(window, "deviceList");
            require(list.Items.Count == 3, language + "-same-model-guide-and-two-signed-versions-merge-to-one-row");
            list.SelectedIndex = 1; await Flush(window);
            require(Chosen(list).TryGetProperty("guide_id", out _) && window.CurrentOptions.DevicePackage == null
                && Field<Button>(window, "inlineDeviceContinue").IsEnabled, language + "-merged-oxp3-prefers-bundled-test-without-granting-trust");
            window.Close(); window = null;

            var delayedCatalog = new TaskCompletionSource<JsonElement>();
            window = Open(_ => Task.FromResult(MatchingIndex()), _ => delayedCatalog.Task); await Flush(window);
            list = Field<ComboBox>(window, "deviceList"); list.SelectedIndex = 1; await Flush(window);
            var digest = Chosen(list).GetProperty("sha256").GetString();
            delayedCatalog.SetResult(Catalog()); await Flush(window);
            require(list.Items.Count == 3 && Chosen(list).GetProperty("sha256").GetString() == digest
                && !Field<JsonElement>(window, "displayedDeviceMetadata").TryGetProperty("guide_id", out _),
                language + "-late-bundle-keeps-explicit-signed-source-and-details");
            window.Close(); window = null;

            var delayedIndex = new TaskCompletionSource<JsonElement>();
            window = Open(_ => delayedIndex.Task, _ => Task.FromResult(Catalog())); await Flush(window);
            list = Field<ComboBox>(window, "deviceList"); list.SelectedIndex = 1; await Flush(window);
            delayedIndex.SetResult(MatchingIndex()); await Flush(window);
            require(list.Items.Count == 3 && Chosen(list).TryGetProperty("guide_id", out _)
                && Field<StackPanel>(window, "modelCatalogDetails").IsVisible && window.CurrentOptions.DevicePackage == null,
                language + "-late-signed-index-keeps-bundled-choice-and-session-trust-boundary");
            window.Close(); window = null;

            delayedIndex = new TaskCompletionSource<JsonElement>();
            window = Open(_ => delayedIndex.Task, _ => Task.FromResult(Catalog(bundled: false))); await Flush(window);
            list = Field<ComboBox>(window, "deviceList"); list.SelectedIndex = 1; await Flush(window);
            require(!Field<Button>(window, "inlineDeviceContinue").IsEnabled, language + "-guide-only-model-stays-read-only");
            delayedIndex.SetResult(MatchingIndex()); await Flush(window);
            require(list.Items.Count == 3 && Chosen(list).GetProperty("sha256").GetString() == new string('a', 64)
                && Field<Button>(window, "inlineDeviceContinue").IsEnabled && window.CurrentOptions.DevicePackage == null
                && Field<StackPanel>(window, "modelCatalogDetails").Visibility == Visibility.Collapsed
                && !Field<JsonElement>(window, "displayedDeviceMetadata").TryGetProperty("guide_id", out _),
                language + "-guide-without-bundle-uses-late-signed-fallback-and-refreshes-details");
            window.DeviceIndexLoaderForChecks = _ => Task.FromResult(MatchingIndex(revoked: true));
            await (Task)Call(window, "LoadDeviceIndexAsync", Field<int>(window, "devicePanelGeneration"), CancellationToken.None)!;
            require(list.Items.Count == 3 && Chosen(list).TryGetProperty("guide_id", out _)
                && !Field<Button>(window, "inlineDeviceContinue").IsEnabled && window.CurrentOptions.DevicePackage == null,
                language + "-revoked-signed-packages-fall-back-to-read-only-guide");
        }
        finally { window?.Close(); }
    }
}
