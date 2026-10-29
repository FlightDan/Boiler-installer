using System.IO;
using System.Security.Cryptography;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace OxpInstaller;

internal static class OfficialSystemProgressChecks
{
    private static readonly System.Reflection.BindingFlags PrivateInstance = System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance;
    private static T Find<T>(MainWindow window, string name) => (T)window.FindName(name);
    private static T PanelField<T>(OfficialDownloadPanel panel, string name) =>
        (T)typeof(OfficialDownloadPanel).GetField(name, PrivateInstance)!.GetValue(panel)!;
    private static void Click(MainWindow window, string name) => Find<Button>(window, name).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
    private static async Task FlushLayoutAsync(MainWindow window)
    {
        await window.Dispatcher.InvokeAsync(() => { }, System.Windows.Threading.DispatcherPriority.ApplicationIdle);
        window.UpdateLayout();
    }
    private static void ShowSourceMode(MainWindow window, string mode)
    {
        Find<PartitionPicker>(window, "PartitionSelector").AutomaticReadEnabled = false;
        window.BoilerModeForChecks(mode);
        if (mode is "custom" or "vanilla") typeof(MainWindow).GetMethod("ChooseGenericInstallation", PrivateInstance)!.Invoke(window, null);
        window.WindowState = WindowState.Normal; window.ShowActivated = false; window.Show();
        BootSetupChecks.Acknowledge(window);
        window.PreviewStep(1);
    }
    private sealed record CaptureEvidence(string RequestedPath, string? Path, string Status, string? Reason, int VisiblePixels);
    private static CaptureEvidence Capture(MainWindow window, string path)
    {
        try
        {
            // A previous empty capture must not survive as evidence for this attempt.
            if (File.Exists(path)) File.Delete(path);
            var root = (FrameworkElement)window.Content;
            if (root.ActualWidth <= 0 || root.ActualHeight <= 0)
                return new(path, null, "unavailable-empty-layout", "The source page has no rendered dimensions.", 0);
            var bitmap = new RenderTargetBitmap((int)Math.Ceiling(root.ActualWidth), (int)Math.Ceiling(root.ActualHeight), 96, 96, PixelFormats.Pbgra32);
            bitmap.Render(root);
            var stride = checked(bitmap.PixelWidth * 4); var pixels = new byte[checked(stride * bitmap.PixelHeight)];
            bitmap.CopyPixels(pixels, stride, 0);
            var visiblePixels = 0; var varied = false;
            for (var offset = 0; offset < pixels.Length; offset += 4)
            {
                if (pixels[offset + 3] != 0) visiblePixels++;
                if (offset != 0 && !pixels.AsSpan(offset, 4).SequenceEqual(pixels.AsSpan(0, 4))) varied = true;
            }
            if (visiblePixels == 0 || !varied)
                return new(path, null, "unavailable-empty-render", "RenderTargetBitmap returned transparent or uniform pixels; no PNG was saved.", visiblePixels);
            var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap));
            using (var image = File.Create(path)) encoder.Save(image);
            return new(path, path, "captured-needs-visual-review", null, visiblePixels);
        }
        catch (Exception error)
        {
            try { if (File.Exists(path)) File.Delete(path); } catch (IOException) { } catch (UnauthorizedAccessException) { }
            return new(path, null, "unavailable-capture-error", error.Message, 0);
        }
    }
    private static async Task AdvanceAfterSourceInspectionAsync(MainWindow window, string destination)
    {
        var next = Find<Button>(window, "NextButton");
        if (!next.IsEnabled) throw new IOException("Source inputs are not ready; the disabled Next button was not invoked.");
        var message = Find<TextBlock>(window, "ValidationMessage"); message.Text = "";
        var busyField = typeof(MainWindow).GetField("sourceInspectionBusy", PrivateInstance)!;
        bool Busy() => (bool)busyField.GetValue(window)!;
        var deadline = DateTime.UtcNow.AddMinutes(5);
        Click(window, "NextButton");
        while (Busy() || !Find<FrameworkElement>(window, destination).IsVisible && message.Text.Length == 0)
        {
            if (DateTime.UtcNow >= deadline)
            {
                (typeof(MainWindow).GetField("sourceInspection", PrivateInstance)?.GetValue(window) as CancellationTokenSource)?.Cancel();
                var cleanupDeadline = DateTime.UtcNow.AddSeconds(30);
                while (Busy() && DateTime.UtcNow < cleanupDeadline) await Task.Delay(40);
                throw new TimeoutException("Read-only source inspection did not complete within five minutes; cancellation was requested.");
            }
            await Task.Delay(40);
        }
        await FlushLayoutAsync(window);
        if (!Find<FrameworkElement>(window, destination).IsVisible)
            throw new IOException("Source inspection did not advance: " + message.Text);
    }
    private static OfficialSourceBinding ReviewedOfficialSource(MainWindow window) =>
        typeof(MainWindow).GetProperty("CurrentOfficialSourceBinding", PrivateInstance)!.GetValue(window) as OfficialSourceBinding
            ?? throw new IOException("The second source was not bound by read-only inspection.");

    internal static async Task<string> RunImportUiAsync(MainWindow window, string bundle)
    {
        var originalWorkspace = InstallerWorkspace.DirectoryPath;
        var workspaceField = typeof(InstallerWorkspace).GetField("<DirectoryPath>k__BackingField", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static)!;
        var recovery = Path.Combine(originalWorkspace, "source", "steamdeck-oobe-repair-20260707.10-3.8.14.img");
        if (!File.Exists(bundle) || !File.Exists(recovery)) throw new IOException("This fixture test requires a real local RAUC bundle and official recovery image.");
        var directory = Path.Combine(originalWorkspace, "local-import-check-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(directory);
        var checks = new List<string>();
        void Require(bool value, string label) { if (!value) throw new IOException(label); checks.Add(label); }
        var panel = Find<StackPanel>(window, "OfficialDownloadHost").Children.OfType<OfficialDownloadPanel>().Single();
        try
        {
            workspaceField.SetValue(null, directory);
            ShowSourceMode(window, "custom"); panel.RefreshWorkspace();
            PanelField<CheckBox>(panel, "agreement").IsChecked = false;
            await panel.DownloadAsync();
            Require(!OfficialSystem.HasPreparedSource() && (PanelField<TextBlock>(panel, "status").Text.Contains("条款")
                || PanelField<TextBlock>(panel, "status").Text.Contains("terms")), "online-download-still-requires-consent");
            window.BoilerModeForChecks("local");
            Require(Find<StackPanel>(window, "RecoverySourceSection").IsVisible && Find<StackPanel>(window, "OfficialSourceSection").IsVisible
                && !Find<StackPanel>(window, "SourceSubitems").IsVisible && PanelField<Border>(panel, "downloadCard").Visibility == Visibility.Collapsed,
                "local-pair-shares-one-page-without-download-card-or-source-subtabs");
            var busyEvents = new List<bool>(); panel.BusyChanged += busyEvents.Add;
            try { await panel.DownloadAsync(bundle); }
            finally { panel.BusyChanged -= busyEvents.Add; }
            Require(OfficialSystem.IsBundleReady() && !OfficialSystem.IsCached(), "local-raucb-imports-without-download-consent-or-root-reconstruction");
            var staged = OfficialSystem.RequirePreparedSource();
            Require(PanelField<TextBox>(panel, "localPath").Text == staged, "import-displays-workspace-bundle-path");
            Require(busyEvents.SequenceEqual(new[] { true, false }) && !panel.IsBusy && PanelField<Button>(panel, "browse").IsEnabled, "import-restores-controls-and-reports-busy-state");
            await FlushLayoutAsync(window);
            Require(PanelField<TextBlock>(panel, "localStatus").IsVisible && (PanelField<TextBlock>(panel, "localStatus").Text.Contains("下一步")
                || PanelField<TextBlock>(panel, "localStatus").Text.Contains("Next")), "success-feedback-visible-beside-local-file");
            var invalid = Path.Combine(directory, "invalid.raucb"); await File.WriteAllTextAsync(invalid, "invalid bundle");
            await panel.DownloadAsync(invalid); await FlushLayoutAsync(window);
            Require(PanelField<TextBlock>(panel, "localStatus").IsVisible && (PanelField<TextBlock>(panel, "localStatus").Text.Contains("准备未完成")
                || PanelField<TextBlock>(panel, "localStatus").Text.Contains("Preparation incomplete")), "invalid-local-bundle-shows-nearby-error");
            Require(OfficialSystem.IsBundleReady() && PanelField<TextBox>(panel, "localPath").Text == staged && !panel.IsBusy, "failed-import-preserves-previous-ready-bundle");
            await panel.DownloadAsync(bundle);
            Require(!Find<Button>(window, "NextButton").IsEnabled, "second-file-alone-cannot-advance-local-pair");
            window.Width = 1060; window.Height = 720; PanelField<TextBlock>(panel, "localStatus").BringIntoView(); await FlushLayoutAsync(window);
            var screenshot = Capture(window, Path.Combine(directory, "local-import.png"));
            Find<TextBox>(window, "ImagePath").Text = recovery;
            await AdvanceAfterSourceInspectionAsync(window, "TargetPage");
            Require(ReviewedOfficialSource(window).Path == staged && Find<StackPanel>(window, "TargetPage").IsVisible
                && !Find<StackPanel>(window, "ContentPage").IsVisible, "reviewed-local-pair-advances-to-target-without-component-page");
            return JsonSerializer.Serialize(new { passed = true, checks, directory,
                status = screenshot.Path == null ? "functional-pass-visual-unavailable" : "functional-pass-visual-review-pending",
                screenshotReview = screenshot.Path == null ? "unavailable" : "needs_visual_review", screenshots = new[] { screenshot },
                scope = "Real local RAUC import and read-only recovery VM inspection with SHA-256 binding of both sources. Routes passed; "
                    + (screenshot.Path == null ? "visual evidence unavailable. " : "saved visual evidence requires review. ")
                    + "No online download, root reconstruction, installation or physical write." }, new JsonSerializerOptions { WriteIndented = true });
        }
        finally { workspaceField.SetValue(null, originalWorkspace); panel.RefreshWorkspace(); }
    }

    internal static async Task<string> RunLocalBundleAsync(MainWindow window, string bundle)
    {
        var originalWorkspace = InstallerWorkspace.DirectoryPath;
        var workspaceField = typeof(InstallerWorkspace).GetField("<DirectoryPath>k__BackingField", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static)!;
        var recovery = Path.Combine(originalWorkspace, "source", "steamdeck-oobe-repair-20260707.10-3.8.14.img");
        var seed = Path.Combine(originalWorkspace, "steamos-3.10", "rootfs.img");
        if (!File.Exists(bundle) || !File.Exists(recovery) || !File.Exists(seed))
            throw new IOException("This online/VM fixture test requires a real local RAUC bundle, official recovery image and rebuilt root seed.");
        var directory = Path.Combine(originalWorkspace, "local-bundle-check-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(directory);
        var checks = new List<string>();
        void Require(bool value, string label) { if (!value) throw new IOException(label); checks.Add(label); }
        var panel = Find<StackPanel>(window, "OfficialDownloadHost").Children.OfType<OfficialDownloadPanel>().Single();
        try
        {
            workspaceField.SetValue(null, directory);
            ShowSourceMode(window, "custom"); panel.RefreshWorkspace();
            Find<TextBox>(window, "ImagePath").Text = recovery;
            PanelField<CheckBox>(panel, "agreement").IsChecked = true;
            await panel.DownloadAsync();
            Require(OfficialSystem.IsBundleReady() && !OfficialSystem.IsCached(), "online-raucb-ready-without-rebuilding-root");
            Require(PanelField<TextBox>(panel, "localPath").Text.EndsWith(".raucb"), "online-download-fills-local-bundle-path");
            await AdvanceAfterSourceInspectionAsync(window, "ContentPage");
            Require(Find<StackPanel>(window, "ContentPage").IsVisible, "reviewed-custom-pair-advances-to-installation-contents");
            window.BoilerModeForChecks("local"); Find<TextBox>(window, "ImagePath").Text = recovery;
            await panel.DownloadAsync(bundle);
            var staged = OfficialSystem.RequirePreparedSource();
            Require(staged.EndsWith(".raucb") && PanelField<TextBox>(panel, "localPath").Text == staged, "selected-local-raucb-fills-staged-path");
            Require(!File.Exists(Path.Combine(directory, "steamos-3.10", "rootfs.img")), "local-selection-does-not-rebuild-root");
            await AdvanceAfterSourceInspectionAsync(window, "TargetPage");
            var binding = ReviewedOfficialSource(window);
            Require(binding.Path == staged, "local-navigation-waits-for-both-source-inspections-and-retains-second-source-binding");
            // The bound preparation uses its own directory. Seed only its desync reconstruction;
            // the earlier download/selection/navigation checks ran with an empty workspace.
            var reconstruction = Path.Combine(directory, "official-preparation"); Directory.CreateDirectory(reconstruction);
            File.Copy(seed, Path.Combine(reconstruction, "rootfs.img"));
            using var timeout = new CancellationTokenSource(TimeSpan.FromMinutes(5));
            using var log = new StreamWriter(Path.Combine(directory, "preparation.log")) { AutoFlush = true };
            var prepared = await OfficialSystem.PrepareAsync(recovery, directory, line => { lock (log) log.WriteLine(line); }, timeout.Token, officialSource: binding);
            Require(File.Exists(prepared) && File.Exists(Path.Combine(directory, "official-source.json"))
                && File.Exists(Path.Combine(reconstruction, "complete.txt")), "bound-installation-preparation-rebuilds-root-and-integrates-recovery-in-isolated-session");
            return JsonSerializer.Serialize(new { passed = true, checks, directory,
                scope = "Real official RAUC download, custom/local combined-page navigation with awaited read-only VM inspections, then bound source preparation in an isolated VM. Desync reuses an existing real root seed. No installation or physical write." }, new JsonSerializerOptions { WriteIndented = true });
        }
        finally { workspaceField.SetValue(null, originalWorkspace); panel.RefreshWorkspace(); }
    }
    internal static async Task<string> RunUiAsync(MainWindow window)
    {
        var directory = Path.Combine(Backend.Root, "logs", "official-download-ui-" + DateTime.Now.ToString("yyyyMMdd-HHmmss")); Directory.CreateDirectory(directory);
        var originalWorkspace = InstallerWorkspace.DirectoryPath;
        var originalLanguage = InstallerI18n.Language;
        var workspaceField = typeof(InstallerWorkspace).GetField("<DirectoryPath>k__BackingField", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static)!;
        var workspace = Path.Combine(originalWorkspace, "official-ui-empty-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(workspace);
        var checks = new List<string>();
        void Require(bool value, string label) { if (!value) throw new IOException(label); checks.Add(label); }
        var source = Find<StackPanel>(window, "SourcePage"); var host = Find<StackPanel>(window, "OfficialDownloadHost");
        var screenshots = new List<CaptureEvidence>();
        var panel = host.Children.OfType<OfficialDownloadPanel>().Single();
        var operationField = typeof(OfficialDownloadPanel).GetField("operation", PrivateInstance)!;
        var refresh = typeof(MainWindow).GetMethod("UpdateNavigationState", PrivateInstance)!;
        bool WithinSource(DependencyObject item)
        {
            for (DependencyObject? current = item; current != null; current = LogicalTreeHelper.GetParent(current)) if (current == source) return true;
            return false;
        }
        try
        {
            workspaceField.SetValue(null, workspace);
            ShowSourceMode(window, "custom"); panel.RefreshWorkspace(); await FlushLayoutAsync(window);
            Require(WithinSource(Find<CheckBox>(window, "Upgrade310")) && WithinSource(panel), "version-selection-and-download-on-system-page");
            Require(Find<StackPanel>(window, "RecoverySourceSection").IsVisible && Find<StackPanel>(window, "OfficialSourceSection").IsVisible
                && !Find<StackPanel>(window, "SourceSubitems").IsVisible, "combined-source-page-shows-recovery-and-310-without-source-subtabs");
            Require(!Find<Button>(window, "NextButton").IsEnabled && !OfficialSystem.HasPreparedSource(), "missing-source-pair-keeps-next-disabled-without-inspection");
            var invalid = Path.Combine(workspace, "invalid-recovery.img"); await File.WriteAllTextAsync(invalid, "not an image");
            Find<TextBox>(window, "ImagePath").Text = invalid;
            Click(window, "ContentTab"); Click(window, "SourceTab");
            Require(Find<TextBox>(window, "ImagePath").Text == invalid && !Find<Button>(window, "NextButton").IsEnabled,
                "returning-to-combined-source-page-preserves-selection-and-invalid-source-cannot-advance");
            foreach (var language in new[] { "ChineseLanguage", "EnglishLanguage" })
            foreach (var size in new[] { new Size(1060, 720), new Size(853, 480) })
            foreach (var mode in new[] { "custom", "local" })
            {
                Find<RadioButton>(window, language).IsChecked = true;
                ShowSourceMode(window, mode); window.Width = size.Width; window.Height = size.Height; await FlushLayoutAsync(window);
                Require(Find<StackPanel>(window, "RecoverySourceSection").IsVisible && Find<StackPanel>(window, "OfficialSourceSection").IsVisible
                    && !Find<StackPanel>(window, "SourceSubitems").IsVisible, "both-inputs-share-source-page-" + mode + language + size);
                var sourceCard = Find<SystemSourceCard>(window, "UpgradeSourceCard");
                Require(sourceCard.DownloadButton.Visibility == (mode == "local" ? Visibility.Collapsed : Visibility.Visible),
                    "download-card-mode-visibility-" + mode + language + size);
                Require(sourceCard.ActualWidth > 300 && sourceCard.ActualHeight >= 76, "official-source-card-layout-" + mode + language + size);
                var scroll = Find<ScrollViewer>(window, "PageScroll"); scroll.ScrollToTop(); await FlushLayoutAsync(window);
                screenshots.Add(Capture(window, Path.Combine(directory, $"combined-{mode}-{language}-{size.Width}x{size.Height}.png")));
                sourceCard.BrowseButton.BringIntoView(); await FlushLayoutAsync(window);
                Require(scroll.ScrollableHeight > 0 || host.ActualHeight <= scroll.ActualHeight + 1,
                    "official-source-controls-reachable-" + mode + language + size);
                screenshots.Add(Capture(window, Path.Combine(directory, $"official-{mode}-{language}-{size.Width}x{size.Height}.png")));
                Require(!Find<Button>(window, "NextButton").IsEnabled && !(bool)typeof(MainWindow).GetField("sourceInspectionBusy", PrivateInstance)!.GetValue(window)!,
                    "empty-pair-stays-idle-and-cannot-advance-" + mode + language + size);
            }
            ShowSourceMode(window, "custom");
            using var operation = new CancellationTokenSource();
            operationField.SetValue(panel, operation); refresh.Invoke(window, null);
            try
            {
                Require(!Find<Button>(window, "NextButton").IsEnabled && !Find<Button>(window, "BrowseWorkspaceButton").IsEnabled
                    && !Find<CheckBox>(window, "Upgrade310").IsEnabled && !Find<StackPanel>(window, "DownloadHost").Children[0].IsEnabled,
                    "download-busy-state-locks-workspace-version-and-conflicting-download");
                panel.Pause(); Require(operation.IsCancellationRequested, "pause-cancels-official-preparation-state");
            }
            finally { operationField.SetValue(panel, null); refresh.Invoke(window, null); }
            Require(!Find<Button>(window, "NextButton").IsEnabled && panel.IsEnabled
                && Find<Button>(window, "BrowseWorkspaceButton").IsEnabled == !Find<PartitionPicker>(window, "PartitionSelector").IsBusy,
                "idle-controls-restore-while-missing-pair-keeps-next-disabled");
            var missingScreenshots = screenshots.Count(item => item.Path == null);
            return JsonSerializer.Serialize(new { passed = true, checks, directory,
                status = missingScreenshots == 0 ? "functional-pass-visual-review-pending" : "functional-pass-visual-unavailable",
                screenshotReview = missingScreenshots == 0 ? "needs_visual_review" : missingScreenshots == screenshots.Count ? "unavailable" : "partial_evidence_needs_review",
                screenshots,
                scope = "Synthetic invalid/missing-source and injected download-busy UI states in an empty workspace, Chinese/English. Geometry/routes passed; "
                    + (missingScreenshots == 0 ? "saved visual evidence requires review. " : "visual evidence unavailable for one or more captures. ")
                    + "No Next invocation, image inspection, VM, network download, installation or physical write." }, new JsonSerializerOptions { WriteIndented = true });
        }
        finally
        {
            operationField.SetValue(panel, null);
            workspaceField.SetValue(null, originalWorkspace); panel.RefreshWorkspace();
            Find<RadioButton>(window, originalLanguage == "en-US" ? "EnglishLanguage" : "ChineseLanguage").IsChecked = true;
        }
    }
    internal static async Task<string> RunAsync()
    {
        var checks = new List<string>();
        void Require(bool value, string label) { if (!value) throw new IOException(label); checks.Add(label); }
        var payload = new byte[1024 * 1024]; RandomNumberGenerator.Fill(payload);
        foreach (long? length in new long?[] { payload.Length, null })
        {
            using var source = new MemoryStream(payload); using var destination = new MemoryStream();
            var events = new List<long>();
            await OfficialSystem.CopyDownloadAsync(source, destination, length, (bytes, total, speed) =>
            {
                Require(total == length && speed >= 0, "valid-download-size-and-speed"); events.Add(bytes);
            }, CancellationToken.None);
            Require(destination.ToArray().SequenceEqual(payload) && events[0] == 0 && events[^1] == payload.Length,
                length.HasValue ? "known-size-download-completes-with-actual-bytes" : "unknown-size-download-reports-bytes-without-inventing-total");
        }
        using (var truncated = new MemoryStream(payload))
        using (var target = new MemoryStream())
        {
            var rejected = false;
            try { await OfficialSystem.CopyDownloadAsync(truncated, target, payload.Length + 1, (_, _, _) => { }, CancellationToken.None); }
            catch (IOException) { rejected = true; }
            Require(rejected, "truncated-download-cannot-complete");
        }
        using (var source = new MemoryStream(payload))
        using (var target = new MemoryStream())
        {
            var cancelled = false;
            try { await OfficialSystem.CopyDownloadAsync(source, target, payload.Length, (_, _, _) => { }, new CancellationToken(true)); }
            catch (OperationCanceledException) { cancelled = true; }
            Require(cancelled && target.Length == 0, "cancel-before-read-prevents-download-write");
        }
        Require(OfficialSystem.AssemblyFraction("Attempt 1: Assembling   37.25% 10s") == .3725, "desync-real-format-parses");
        Require(OfficialSystem.AssemblyFraction("Attempt 2: Validating 50.00%") == null
            && OfficialSystem.AssemblyFraction("Attempt 2: Assembling 101.00%") == null, "validation-and-invalid-percent-do-not-credit-download");
        var tracker = new InstallationProgressTracker();
        foreach (var fraction in new[] { 0d, .1, .7, .2, .8, .9, 1 })
        {
            var previous = tracker.Percent;
            var next = tracker.Advance("preparing", (2 + 10 * fraction) / 32);
            Require(next >= previous && next <= 12, "official-substeps-and-retries-remain-monotonic");
        }
        var directory = Path.Combine(InstallationController.Work, "official-progress-check-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory); Directory.CreateDirectory(Path.Combine(directory, "store"));
        var input = Path.Combine(directory, "source.bin"); var index = Path.Combine(directory, "source.caibx");
        var output = Path.Combine(directory, "rebuilt.bin"); var executable = Path.Combine(Backend.Root, "runtime/desync/desync.exe");
        await File.WriteAllBytesAsync(input, payload);
        await RunPairedSourceChecksAsync(directory, payload, Require);
        var cachedBefore = OfficialSystem.IsCached();
        var rejectedImport = false;
        try { await OfficialSystem.ImportRootAsync(input, CancellationToken.None); }
        catch (IOException) { rejectedImport = true; }
        Require(rejectedImport && OfficialSystem.IsCached() == cachedBefore, "invalid-local-image-rejected-without-changing-cache");
        await Backend.ProcessAsync(executable, ["make", "-s", Path.Combine(directory, "store"), index, input], _ => { }, CancellationToken.None);
        var observed = new List<double>();
        var transcript = await Backend.ProcessAsync(executable, ["extract", "-s", Path.Combine(directory, "store"), index, output], line =>
        {
            if (OfficialSystem.AssemblyFraction(line) is double value) observed.Add(value);
        }, CancellationToken.None, new Dictionary<string, string> { ["DESYNC_ENABLE_PARSABLE_PROGRESS"] = "1" });
        Require(observed.Count > 0 && observed[^1] == 1 && (await File.ReadAllBytesAsync(output)).SequenceEqual(payload),
            "bundled-desync-redirected-output-delivers-real-completion");
        await File.WriteAllTextAsync(Path.Combine(directory, "desync-progress.txt"), transcript);
        return JsonSerializer.Serialize(new { passed = true, checks, directory }, new JsonSerializerOptions { WriteIndented = true });
    }
    private static async Task RunPairedSourceChecksAsync(string directory, byte[] payload, Action<bool, string> require)
    {
        OfficialSystem.ValidatePreparedRootResult("OXP_UPDATE_ROOT=3.10:" + OfficialSystem.Build + "\n");
        OfficialSystem.ValidatePreparedRootResult("OXP_UPDATE_ROOT=3.10.0:" + OfficialSystem.Build + "\r\n");
        var wrongVersionRejected = false;
        try { OfficialSystem.ValidatePreparedRootResult("OXP_UPDATE_ROOT=3.8:" + OfficialSystem.Build); }
        catch (IOException) { wrongVersionRejected = true; }
        require(wrongVersionRejected, "wrong-version-root-stops-before-prepared-image-deployment");
        var wrongBuildRejected = false;
        try { OfficialSystem.ValidatePreparedRootResult("OXP_UPDATE_ROOT=3.10:other-build"); }
        catch (IOException) { wrongBuildRejected = true; }
        require(wrongBuildRejected, "wrong-build-root-stops-before-prepared-image-deployment");
        var source = Path.Combine(directory, "reviewed.raucb"); await File.WriteAllBytesAsync(source, payload);
        var file = new FileInfo(source);
        var binding = new OfficialSourceBinding(source, Convert.ToHexString(SHA256.HashData(payload)).ToLowerInvariant(), payload.Length, file.LastWriteTimeUtc.Ticks);
        var snapshot = await OfficialSystem.SnapshotBoundSourceAsync(binding, directory, CancellationToken.None);
        require((await File.ReadAllBytesAsync(snapshot)).SequenceEqual(payload), "reviewed-second-file-snapshot-copies-exact-hashed-content");
        var originalTimestamp = file.LastWriteTimeUtc;
        var changed = (byte[])payload.Clone(); changed[0] ^= 1; await File.WriteAllBytesAsync(source, changed); File.SetLastWriteTimeUtc(source, originalTimestamp);
        var snapshotsBefore = Directory.GetFiles(directory, "official-source-*").Length;
        var rejected = false;
        try { await OfficialSystem.SnapshotBoundSourceAsync(binding, directory, CancellationToken.None); }
        catch (IOException) { rejected = true; }
        require(rejected && Directory.GetFiles(directory, "official-source-*").Length == snapshotsBefore,
            "same-size-same-timestamp-second-file-substitution-fails-sha256-and-cleans-partial");
        await File.WriteAllBytesAsync(source, payload); File.SetLastWriteTimeUtc(source, originalTimestamp);
        var cancelled = false;
        try { await OfficialSystem.SnapshotBoundSourceAsync(binding, directory, new CancellationToken(true)); }
        catch (OperationCanceledException) { cancelled = true; }
        require(cancelled && Directory.GetFiles(directory, "official-source-*").Length == snapshotsBefore,
            "cancelled-second-file-snapshot-cleans-partial");
        var recovery = Path.Combine(directory, "recovery.img"); await File.WriteAllBytesAsync(recovery, RecoveryImageChecks.Fixture());
        var recoveryHash = Convert.ToHexString(SHA256.HashData(await File.ReadAllBytesAsync(recovery))).ToLowerInvariant();
        var options = new SelectedOptions(1, [], [], InstallerI18n.Language, "system", InstallMode: "local");
        var request = new InstallationRequest(InstallationTargetKind.ExternalDisk, recovery, false, options,
            UpgradeTo310: true, Mode: "local", ExpectedSourceSha256: recoveryHash, OfficialSource: binding);
        InstallationController.ValidateRequest(request);
        require(true, "local-pair-request-requires-recovery-and-second-file-identities-with-empty-components");
        void Reject(InstallationRequest invalid, string label)
        {
            var refused = false;
            try { InstallationController.ValidateRequest(invalid); } catch (IOException) { refused = true; }
            require(refused, label);
        }
        Reject(request with { OfficialSource = null }, "local-request-without-second-file-review-refused");
        Reject(request with { ExpectedSourceSha256 = null }, "local-request-without-recovery-review-refused");
        Reject(request with { UpgradeTo310 = false }, "local-request-cannot-disable-selected-310-system");
        Reject(request with { Prepared = true }, "local-request-cannot-substitute-prepared-image");
        Reject(request with { Options = options with { Plugins = ["decky"] } }, "local-request-cannot-inject-components");
        Reject(request with { Mode = "imported", Options = options with { InstallMode = "imported" } }, "legacy-prebuilt-mode-still-refuses-upgrade");
        File.SetLastWriteTimeUtc(source, originalTimestamp.AddSeconds(3));
        Reject(request, "changed-second-file-timestamp-revokes-reviewed-request");
    }
}
