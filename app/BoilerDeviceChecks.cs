using System.IO;
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
            foreach (var language in new[] { "zh-CN", "en-US" })
            {
                InstallerI18n.SetLanguage(language);
                var calls = 0;
                window = new MainWindow(rehearsal: true) { Width = 1060, Height = 720, WindowState = WindowState.Normal, ShowActivated = false };
                ((PartitionPicker)window.FindName("PartitionSelector")).AutomaticReadEnabled = false;
                window.DeviceIndexLoaderForChecks = _ => { calls++; return Task.FromResult(IndexResponse()); };
                window.Show(); window.BoilerModeForChecks("custom"); await Flush(window);
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
                    Require(Field<ComboBox>(window, "deviceList").Items.OfType<ComboBoxItem>().Any(i => i.Content is TextBlock t && t.Text == currentLabel), "stale-response-cannot-replace-current-route");

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

                    var droppedIndex = new TaskCompletionSource<JsonElement>();
                    window.DeviceIndexLoaderForChecks = _ => droppedIndex.Task;
                    window.BoilerModeForChecks("custom"); await Flush(window);
                    await (Task)typeof(MainWindow).GetMethod("ReceiveInputFilesAsync", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(window, [new[] { script }])!;
                    await Flush(window);
                    droppedIndex.SetResult(IndexResponse()); await Flush(window);
                    Require(!Field<Border>(window, "deviceOptionPreview").IsVisible && Field<ComboBox>(window, "deviceList").SelectedIndex == 0
                        && window.CurrentOptions.DevicePackage != null, "late-matching-directory-does-not-replace-dropped-device-selection");
                }
                window.PreviewStep(MainWindow.AdaptationStep);
                window.DeviceIndexLoaderForChecks = _ => throw new IOException("Directory unavailable fixture");
                await (Task)typeof(MainWindow).GetMethod("LoadDeviceIndexAsync", BindingFlags.Instance | BindingFlags.NonPublic)!
                    .Invoke(window, [Field<int>(window, "devicePanelGeneration"), CancellationToken.None])!;
                await Flush(window);
                Require(Field<Expander>(window, "deviceDirectoryDetails").IsExpanded
                    && Field<Button>(window, "deviceIndexRetry").IsVisible,
                    "directory-failure-exposes-retry-without-opening-details-manually-" + language);
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
