using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

namespace OxpInstaller;

internal static class BoilerResourceSelectionChecks
{
    private static T Field<T>(MainWindow window, string name) => (T)typeof(MainWindow).GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(window)!;
    private static object? Call(MainWindow window, string name, params object?[] args) => typeof(MainWindow).GetMethod(name, BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(window, args);
    private static async Task Flush(MainWindow window) { window.UpdateLayout(); await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle); }
    internal static Task<string> RunAsync() => Application.Current.Dispatcher.CheckAccess() ? Run() : Application.Current.Dispatcher.InvokeAsync(Run).Task.Unwrap();

    private static async Task<string> Run()
    {
        var checks = new List<string>(); var captures = new List<string>();
        var directory = Path.Combine(Backend.Root, "logs", "resource-selection-" + DateTime.UtcNow.ToString("yyyyMMddTHHmmssfff"));
        Directory.CreateDirectory(directory);
        void Require(bool condition, string label) { if (!condition) throw new IOException("Resource selection check failed: " + label); checks.Add(label); }
        var before = InstallerI18n.Language;
        try
        {
            var realCatalog = await BoilerProtocol.RunAsync(["resource-catalog"], CancellationToken.None);
            foreach (var language in new[] { "zh-CN", "en-US" })
            {
                InstallerI18n.SetLanguage(language);
                var modelMetadata = JsonSerializer.SerializeToElement(new { offline = true,
                    source = new { repository = "FlightDan/Boiler-installer", commit = new string('a', 40) },
                    models = Array.Empty<object>(), bundled_packages = new[] { new { id = "device:oxp3", trust = "local-test" } } });
                var window = new MainWindow(rehearsal: true) { Width = 1060, Height = 720, WindowState = WindowState.Normal, ShowActivated = false,
                    DeviceIndexLoaderForChecks = _ => Task.FromResult(BoilerDeviceChecks.IndexResponse()),
                    ModelCatalogLoaderForChecks = _ => Task.FromResult(modelMetadata),
                    ResourceCatalogLoaderForChecks = _ => Task.FromResult(realCatalog), DeviceHardwareReaderForChecks = () => null };
                try
                {
                    ((PartitionPicker)window.FindName("PartitionSelector")).AutomaticReadEnabled = false;
                    window.Show(); window.BoilerModeForChecks("custom"); await Flush(window);
                    await (Task)Call(window, "LoadModelCatalogAsync", Field<int>(window, "devicePanelGeneration"), CancellationToken.None)!;
                    var list = Field<ComboBox>(window, "deviceList");
                    var candidate = list.Items.OfType<ComboBoxItem>().FirstOrDefault(item => item.Tag is JsonElement entry && entry.GetProperty("id").GetString() == "device:oxp3")
                        ?? throw new IOException("Missing OXP3 resource model; model error=" + Field<string?>(window, "modelCatalogError") + "; resource error=" + Field<string?>(window, "resourceCatalogError"));
                    list.SelectedItem = candidate;
                    await Flush(window);
                    var choices = Field<Dictionary<string, CheckBox>>(window, "resourceChoices");
                    var consent = Field<CheckBox>(window, "resourceConsent");
                    var action = Field<Button>(window, "inlineDeviceContinue");
                    Require(choices.Count == realCatalog.GetProperty("models").EnumerateArray().Single(model => model.GetProperty("id").GetString() == "device:oxp3").GetProperty("components").GetArrayLength()
                        && choices.Values.All(box => !box.IsEnabled && (box.IsChecked == true) == ((JsonElement)box.Tag).GetProperty("default").GetBoolean()), language + "-source-defaults-visible-without-granting-trust");
                    Require(!action.IsEnabled && window.CurrentOptions.ResourceSelection == null && window.CurrentOptions.DevicePackage == null,
                        language + "-model-loading-does-not-select-legacy-package-or-resources");
                    var details = Field<StackPanel>(window, "modelCatalogDetails");
                    Require(choices.Count == 30 && choices.Values.All(box => ((JsonElement)box.Tag).GetProperty("selectable").GetBoolean())
                        && !details.Children.OfType<Expander>().Any(), language + "-all-thirty-integrated-resources-available-without-stale-pending-items");
                    consent.IsChecked = true;
                    Require(window.CurrentOptions.ResourceSelection == null, language + "-consent-alone-does-not-select");
                    var edited = choices.First(pair => pair.Value.IsEnabled && pair.Value.IsChecked == true);
                    edited.Value.IsChecked = false;
                    var actualModel = realCatalog.GetProperty("models").EnumerateArray().Single(model => model.GetProperty("id").GetString() == "device:oxp3");
                    Call(window, "ShowResourceModel", actualModel); await Flush(window);
                    choices = Field<Dictionary<string, CheckBox>>(window, "resourceChoices");
                    consent = Field<CheckBox>(window, "resourceConsent");
                    Require(consent.IsChecked == true && choices[edited.Key].IsChecked != true, language + "-unconfirmed-user-draft-survives-refresh");
                    var chosen = choices.FirstOrDefault(pair => pair.Value.IsEnabled && ((JsonElement)pair.Value.Tag).GetProperty("dependencies").GetArrayLength() > 0);
                    if (chosen.Value == null) chosen = choices.First(pair => pair.Value.IsEnabled);
                    chosen.Value.IsChecked = true;
                    Require(action.IsEnabled && ((TextBlock)action.Content).Text.Contains(language == "en-US" ? "local test" : "本地测试"), language + "-action-identifies-local-preview");
                    await (Task)Call(window, "SelectChosenDeviceAsync")!;
                    var selection = window.CurrentOptions.ResourceSelection;
                    Require(selection != null && selection.ModelId == "device:oxp3" && selection.Trust == "local-test"
                        && selection.Components.Contains(chosen.Key) && selection.CatalogSha256 == realCatalog.GetProperty("catalog_sha256").GetString()
                        && window.CurrentOptions.DevicePackage == null && window.CurrentOptions.DeviceLegacyOptions == null,
                        language + "-real-protocol-selects-independent-resources-without-old-package");
                    Require(selection!.Components.All(component => choices[component].IsChecked == true), language + "-resolved-dependencies-are-visible-in-the-checked-choices");
                    var serialized = JsonSerializer.SerializeToElement(window.CurrentOptions, ResourceSelections.Json);
                    Require(serialized.GetProperty("resource_selection").GetProperty("model_id").GetString() == "device:oxp3", language + "-typed-options-retain-resource-selection");
                    InstallerOptions.Validate(window.CurrentOptions);
                    Require(window.CurrentOptions.Plugins.Contains("core:maintenance"), language + "-complete-deployment-retains-fixed-firstboot-maintenance-runtime");
                    foreach (var width in new[] { 1060d, 640d })
                    {
                        window.Width = width; await Flush(window);
                        Require(details.ActualWidth > 100 && details.ActualWidth <= Field<StackPanel>(window, "deviceQuickPicker").ActualWidth + 1,
                            language + "-resource-controls-fit-" + width);
                        var surface = (FrameworkElement)window.Content;
                        var bitmap = new RenderTargetBitmap((int)surface.ActualWidth, (int)surface.ActualHeight, 96, 96, PixelFormats.Pbgra32);
                        bitmap.Render(surface); var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap));
                        var capture = Path.Combine(directory, language + "-" + width + ".png");
                        using (var stream = File.Create(capture)) encoder.Save(stream); captures.Add(capture);
                    }
                    consent.IsChecked = false;
                    Require(window.CurrentOptions.ResourceSelection == null && choices.Values.All(box => !box.IsEnabled && box.IsChecked != true),
                        language + "-revoking-session-consent-clears-typed-selection");
                    var staleCatalog = JsonSerializer.SerializeToElement(new { schema_version = 1, kind = "LocalResourceSelectionCatalog",
                        deployment_available = false, catalog_sha256 = new string('0', 64), models = Array.Empty<object>() });
                    window.ResourceCatalogLoaderForChecks = _ => Task.FromResult(staleCatalog);
                    await (Task)Call(window, "LoadResourceCatalogAsync", Field<int>(window, "devicePanelGeneration") - 1, CancellationToken.None)!;
                    Require(Field<JsonElement?>(window, "resourceCatalog")!.Value.GetProperty("catalog_sha256").GetString()
                        == realCatalog.GetProperty("catalog_sha256").GetString(), language + "-stale-resource-response-cannot-replace-current-generation");
                    Require(MainWindow.IsDeviceInput("external.zip") && Field<Button>(window, "inputDropButton") != null
                        && typeof(MainWindow).GetMethod("ReceiveDroppedInputFilesAsync", BindingFlags.Instance | BindingFlags.NonPublic) != null,
                        language + "-external-import-and-drop-entry-remain-present");
                }
                finally { window.Close(); }
            }
        }
        finally { InstallerI18n.SetLanguage(before); }
        var report = Path.Combine(directory, "report.json");
        await File.WriteAllTextAsync(report, JsonSerializer.Serialize(new { passed = true, checks, captures,
            scope = "Real local protocol with WPF controls and typed options; complete file deployment is enabled under its bound host lease and fixed firstboot runtime. This UI check performs no target writes or hardware acceptance." }));
        return report;
    }
}
