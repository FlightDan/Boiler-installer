using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

namespace OxpInstaller;

internal static class InputAssociationChecks
{
    private static T Field<T>(object owner, string name) => (T)owner.GetType().GetField(name, BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(owner)!;
    private static void Set(object owner, string name, object? value) => owner.GetType().GetField(name, BindingFlags.NonPublic | BindingFlags.Instance)!.SetValue(owner, value);
    private static Task Call(MainWindow window, string name, params object[] args) => (Task)typeof(MainWindow).GetMethod(name, BindingFlags.NonPublic | BindingFlags.Instance)!.Invoke(window, args is string[] paths ? [paths] : args)!;
    private static async Task Flush(MainWindow window) { window.UpdateLayout(); await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle); }
    private static string Options(MainWindow window) => JsonSerializer.Serialize(window.CurrentOptions with { DevicePackage = null, DeviceManifestSha256 = null, DeviceTrust = null, DeviceComponents = null });
    internal static Task<string> RunAsync() => Application.Current.Dispatcher.CheckAccess() ? OnUiThread() : Application.Current.Dispatcher.InvokeAsync(OnUiThread).Task.Unwrap();

    private static async Task<string> OnUiThread()
    {
        var directory = Path.Combine(InstallationController.Work, "input-ui-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(directory);
        var checks = new List<string>(); var captures = new List<string>();
        void Require(bool value, string name) { if (!value) throw new IOException("Input association check failed: " + name); checks.Add(name); }
        var originalLanguage = InstallerI18n.Language;
        var window = new MainWindow(rehearsal: true) { Width = 1060, Height = 720, WindowState = WindowState.Normal, ShowActivated = false, DeviceIndexLoaderForChecks = _ => Task.FromResult(BoilerDeviceChecks.IndexResponse()) };
        ((PartitionPicker)window.FindName("PartitionSelector")).AutomaticReadEnabled = false;
        string Model(string name, bool badHash = false)
        {
            var model = new
            {
                schema_version = 1, kind = "DevicePackage", id = "device:ui-test", version = "1.0.0", name = "OXP3 interaction fixture", status = 1,
                source = new { kind = "local", path = "fixture" }, compatibility = new { },
                files = badHash ? new[] { new { path = "payload.bin", size = 4, sha256 = new string('0', 64) } } : [],
                components = new[] { new { schema_version = 1, kind = "ComponentManifest", id = "device:test-feature", version = "1.0.0", name = "Example device feature", @default = true, files = Array.Empty<object>(), hooks = new { } } }
            };
            var path = Path.Combine(directory, name);
            using var archive = ZipFile.Open(path, ZipArchiveMode.Create);
            using (var stream = archive.CreateEntry("manifest.json").Open()) JsonSerializer.Serialize(stream, model);
            if (badHash) { using var stream = archive.CreateEntry("payload.bin").Open(); stream.Write("data"u8); }
            return path;
        }
        void Clear() => Field<Button>(window, "inputClearButton").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        try
        {
            window.Show(); window.BoilerModeForChecks("custom"); await Flush(window);
            var prior = Path.Combine(directory, "prior.img"); var recovery = Path.Combine(directory, "recovery.img");
            var secondRecovery = Path.Combine(directory, "second-recovery.img");
            await File.WriteAllBytesAsync(prior, RecoveryImageChecks.Fixture()); await File.WriteAllBytesAsync(recovery, RecoveryImageChecks.Fixture()); await File.WriteAllBytesAsync(secondRecovery, RecoveryImageChecks.Fixture());
            var disk = Path.Combine(directory, "complete.img"); await File.WriteAllBytesAsync(disk, RecoveryImageChecks.Fixture(prepared: true));
            var root = Path.Combine(directory, "rootfs.img");
            using (var stream = new FileStream(root, FileMode.CreateNew))
            {
                // This synthetic root contains only a magic marker. Keep zero-filled
                // ranges sparse so the fixture does not consume 5 GiB of workspace.
                if (!DeviceIoControl(stream.SafeFileHandle, 0x000900c4, IntPtr.Zero, 0, IntPtr.Zero, 0, out _, IntPtr.Zero))
                    throw new IOException("Cannot mark the owned root fixture sparse.");
                stream.SetLength(OfficialSystem.RootSize); stream.Position = 0x10040; stream.Write("_BHRfS_M"u8);
            }
            var model = Model("oxp3-ui.zip");
            var imagePath = (TextBox)window.FindName("ImagePath"); imagePath.Text = prior;
            var boxes = Field<Dictionary<string, CheckBox>>(Field<InstallerOptionsPanel>(window, "optionsPanel"), "boxes");
            var option = boxes.Values.First(); option.IsChecked = option.IsChecked != true;
            var oldOptions = Options(window); var oldCached = OfficialSystem.HasPreparedSource();
            var oldSession = new InstallationSession(1, "ui-fixture-complete", InstallationTargetKind.ExternalDisk, prior, false, "complete", "complete", null, null, DateTime.UtcNow);
            Set(window, "session", oldSession);

            await Call(window, "ReceiveInputFilesAsync", new string[] { recovery, root, model });
            var rows = Field<List<InputAssignment>>(window, "inputAssignments");
            Require(rows.Count == 3 && rows.All(row => row.Input?.Error == null), "batch-identifies-recovery-root-and-model");
            Require(imagePath.Text == prior && Options(window) == oldOptions && Field<InstallationSession>(window, "session") == oldSession, "preview-does-not-change-current-configuration-or-session");
            Require(!Field<Button>(window, "inputApplyButton").IsEnabled, "external-model-needs-session-trust");
            await Call(window, "ReceiveInputFilesAsync", new string[] { recovery }); Require(rows.Count == 3, "duplicate-path-drop-is-idempotent");
            await Call(window, "ReceiveInputFilesAsync", new string[] { secondRecovery });
            Field<CheckBox>(window, "inputDeviceTrust").IsChecked = true;
            Require(!Field<Button>(window, "inputApplyButton").IsEnabled && Field<TextBlock>(window, "inputAssociationStatus").Text.Length > 0, "same-role-conflict-blocks-overwrite");
            rows.Single(row => row.Path == secondRecovery).Role = InputRole.Ignore;
            CallRender(window); await Flush(window);
            Require(Field<Button>(window, "inputApplyButton").IsEnabled, "ignoring-extra-file-resolves-conflict");
            foreach (var language in new[] { "zh-CN", "en-US" })
            {
                ((RadioButton)window.FindName(language == "en-US" ? "EnglishLanguage" : "ChineseLanguage")).IsChecked = true;
                foreach (var width in new[] { 1060d, 640d })
                {
                    window.Width = width; window.Height = width == 1060 ? 720 : 480; await Flush(window);
                    ((ScrollViewer)window.FindName("PageScroll")).ScrollToTop(); await Flush(window);
                    var panel = Field<Border>(window, "inputAssociationPanel"); var roleSelectors = Field<StackPanel>(window, "inputRowsHost").Children.OfType<DockPanel>().SelectMany(line => line.Children.OfType<ComboBox>()).ToArray();
                    Require(roleSelectors.All(role => role.ActualWidth <= panel.ActualWidth - 32), "role-controls-fit-" + language + "-" + width);
                    var path = Path.Combine(directory, language + "-" + width + ".png"); if (Capture((FrameworkElement)window.Content, path)) captures.Add(path);
                }
            }
            ((RadioButton)window.FindName("ChineseLanguage")).IsChecked = true; window.Width = 1060; window.Height = 720;
            var stopwatch = System.Diagnostics.Stopwatch.StartNew();
            await Call(window, "ApplyInputAssociationsAsync"); stopwatch.Stop();
            Require(imagePath.Text == recovery && window.CurrentOptions.DevicePackage != null, "one-action-associates-recovery-and-real-device-plan");
            Require(Field<OfficialDownloadPanel>(window, "officialDownloadPanel").AssociatedSourcePath == root && OfficialSystem.HasPreparedSource() == oldCached,
                "system-root-association-is-a-reference-without-cache-mutation");
            Require(stopwatch.Elapsed < TimeSpan.FromSeconds(10), "associating-root-does-not-copy-or-hash-five-gib");
            Require(Options(window) == oldOptions, "association-retains-existing-generic-component-choices");
            Require(Field<InstallationSession?>(window, "session") == null && ((CheckBox)window.FindName("PhysicalConsent")).IsChecked != true, "association-clears-old-completion-and-install-consent");
            Require(Field<TextBlock>(window, "deviceStatus").Text.Contains("OXP3 interaction fixture"), "non-string-status-metadata-renders-safely");
            var feature = Field<Dictionary<string, CheckBox>>(window, "deviceComponentChoices").Single().Value; feature.IsChecked = false;
            await Call(window, "ApplyInputAssociationsAsync");
            Require(Field<Dictionary<string, CheckBox>>(window, "deviceComponentChoices").Single().Value.IsChecked == false, "same-model-reassociation-preserves-component-choices");

            Clear(); await Call(window, "ReceiveInputFilesAsync", new string[] { secondRecovery });
            var beforeCancel = imagePath.Text; var gate = new TaskCompletionSource();
            window.InputPreparationGateForChecks = _ => gate.Task;
            var cancelledApply = Call(window, "ApplyInputAssociationsAsync"); await Flush(window);
            Field<Button>(window, "inputCancelButton").RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); gate.SetResult(); await cancelledApply;
            window.InputPreparationGateForChecks = null;
            Require(imagePath.Text == beforeCancel && !Field<bool>(window, "inputAssociationApplied"), "cancel-before-commit-preserves-selected-source");

            Clear(); var bad = Model("bad-inventory.zip", badHash: true);
            await Call(window, "ReceiveInputFilesAsync", new string[] { secondRecovery, bad }); Field<CheckBox>(window, "inputDeviceTrust").IsChecked = true;
            Set(window, "session", oldSession);
            await Call(window, "ApplyInputAssociationsAsync");
            Require(imagePath.Text == beforeCancel && Field<InstallationSession>(window, "session") == oldSession && !Field<bool>(window, "inputAssociationApplied"), "full-package-validation-failure-has-no-partial-ui-commit");

            Clear(); await Call(window, "ReceiveInputFilesAsync", new string[] { secondRecovery });
            File.SetLastWriteTimeUtc(secondRecovery, File.GetLastWriteTimeUtc(secondRecovery).AddSeconds(10)); await Call(window, "ApplyInputAssociationsAsync");
            Require(imagePath.Text == beforeCancel && Field<TextBlock>(window, "inputAssociationStatus").Text.Contains("改变"), "changed-input-needs-new-inspection");

            Clear(); await Call(window, "ReceiveInputFilesAsync", new string[] { disk, recovery });
            Require(!Field<Button>(window, "inputApplyButton").IsEnabled, "complete-image-and-recovery-flow-conflict-is-explicit");
            Clear(); await Call(window, "ReceiveInputFilesAsync", new string[] { model }); Field<CheckBox>(window, "inputDeviceTrust").IsChecked = true;
            window.BoilerModeForChecks("vanilla"); await Flush(window);
            Require(Field<CheckBox>(window, "inputDeviceTrust").IsChecked == false && !Field<bool>(window, "inputAssociationApplied"), "new-installation-route-requires-new-model-trust");

            Clear(); NativeDrop(window, [recovery, model]);
            await Wait(window, () => !Field<bool>(window, "inputInspectionBusy"));
            Require(Field<List<InputAssignment>>(window, "inputAssignments").Count == 2 && window.CurrentOptions.DevicePackage == null, "native-multiple-file-drop-enters-the-same-preview");
            Require(Field<CheckBox>(window, "inputDeviceTrust").IsChecked == false, "native-drop-does-not-auto-grant-script-trust");

            // Exercise the extension correction without allocating/copying a large root.
            var misleading = Path.Combine(directory, "root-kind.raucb"); await File.WriteAllTextAsync(misleading, "owned normalizer fixture");
            var correction = new DiscoveredInput(misleading, InputKind.UpgradeRoot, "fixture", "", InputDiscovery.ReadStamp(misleading));
            var normalized = (Task<string>)typeof(MainWindow).GetMethod("NormalizeAssociationInputAsync", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(window, [correction, CancellationToken.None])!;
            Require((await normalized).EndsWith(".img", StringComparison.Ordinal), "root-role-cannot-inherit-rauc-bundle-suffix");
        }
        finally
        {
            window.Close(); InstallerI18n.SetLanguage(originalLanguage);
            var ownedRoot = Path.Combine(directory, "rootfs.img");
            if (File.Exists(ownedRoot)) { MediaSource.Ordinary(ownedRoot, directory); File.Delete(ownedRoot); }
        }
        var result = JsonSerializer.Serialize(new { passed = true, checks, captures, directory, scope = "Local content/role/state and native multi-file message fixtures; no downloads, installation or device writes. Actual cross-integrity Explorer gestures need separate manual acceptance." }, new JsonSerializerOptions { WriteIndented = true });
        await File.WriteAllTextAsync(Path.Combine(directory, "result.json"), result); return result;
    }
    private static void CallRender(MainWindow window) => typeof(MainWindow).GetMethod("RenderInputAssignments", BindingFlags.NonPublic | BindingFlags.Instance)!.Invoke(window, null);
    private static async Task Wait(MainWindow window, Func<bool> done) { var limit = DateTime.UtcNow.AddSeconds(15); while (!done()) { if (DateTime.UtcNow > limit) throw new IOException("Input UI timeout."); await Task.Delay(20); await Flush(window); } }
    private static void NativeDrop(MainWindow window, string[] paths)
    {
        var bytes = Encoding.Unicode.GetBytes(string.Join('\0', paths) + "\0\0");
        var drop = GlobalAlloc(0x42, (nuint)(20 + bytes.Length)); var memory = GlobalLock(drop);
        Marshal.WriteInt32(memory, 0, 20); Marshal.WriteInt32(memory, 16, 1); Marshal.Copy(bytes, 0, IntPtr.Add(memory, 20), bytes.Length); GlobalUnlock(drop);
        SendMessage(new WindowInteropHelper(window).Handle, 0x233, drop, IntPtr.Zero);
    }
    private static bool Capture(FrameworkElement surface, string path)
    {
        var image = new RenderTargetBitmap(Math.Max(1, (int)surface.ActualWidth), Math.Max(1, (int)surface.ActualHeight), 96, 96, PixelFormats.Pbgra32); image.Render(surface);
        var pixels = new byte[image.PixelWidth * image.PixelHeight * 4]; image.CopyPixels(pixels, image.PixelWidth * 4, 0);
        if (!pixels.Where((_, index) => index % 4 == 3).Any(alpha => alpha != 0)) return false;
        var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(image)); using var output = File.Create(path); encoder.Save(output); return true;
    }
    [DllImport("kernel32.dll")] private static extern IntPtr GlobalAlloc(uint flags, nuint bytes);
    [DllImport("kernel32.dll")] private static extern IntPtr GlobalLock(IntPtr memory);
    [DllImport("kernel32.dll")] private static extern bool GlobalUnlock(IntPtr memory);
    [DllImport("kernel32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool DeviceIoControl(Microsoft.Win32.SafeHandles.SafeFileHandle handle, uint controlCode,
        IntPtr input, uint inputSize, IntPtr output, uint outputSize, out uint returned, IntPtr overlapped);
    [DllImport("user32.dll")] private static extern IntPtr SendMessage(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);
}
