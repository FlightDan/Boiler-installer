using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Threading;

namespace OxpInstaller;

internal static class GraphicsOptionsChecks
{
    internal static Task<string> RunAsync() => Application.Current.Dispatcher.CheckAccess()
        ? RunOnUiThread() : Application.Current.Dispatcher.InvokeAsync(RunOnUiThread).Task.Unwrap();

    private static async Task<string> RunOnUiThread()
    {
        var checks = new List<string>();
        void Require(bool ok, string name) { if (!ok) throw new IOException(name); checks.Add(name); }
        void Refuse(Action action, string name)
        {
            var refused = false;
            try { action(); } catch (IOException) { refused = true; }
            Require(refused, name);
        }
        void Select(InstallerOptionsPanel panel, string? id) => panel.GraphicsChoice.SelectedItem = panel.GraphicsChoice.Items.Cast<ComboBoxItem>().Single(item => item.Tag as string == id);
        var panel = new InstallerOptionsPanel(); panel.SetMode("custom");
        Require(panel.GraphicsChoice.Items.Count == 12 && panel.Selection.GraphicsProfile == null, "eleven-candidate-models-and-default-no-selection");
        var reads = 0;
        panel.GraphicsHardwareReader = () => { reads++; return new(["graphics:amd-780m"], ["AMD Radeon 780M"], 0); };
        panel.Refresh(); Require(reads == 0, "refresh-never-starts-hardware-discovery");
        panel.GraphicsDetectButton.RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); await panel.ActiveGraphicsDetection;
        Require(reads == 1 && panel.Selection.GraphicsProfile == "graphics:amd-780m", "button-reads-and-selects-one-confirmed-model");
        Select(panel, "graphics:amd-880m");
        Require(panel.Describe().Contains("880M") && JsonSerializer.Serialize(panel.Selection).Contains("graphics:amd-880m"), "manual-choice-enters-preview-and-summary");
        Require(!panel.GraphicsDetectionStatus.Contains("已选择匹配项") && !panel.GraphicsDetectionStatus.Contains("A matching choice was selected"), "manual-choice-clears-stale-auto-selection-message");
        Require(panel.GraphicsDetectionStatus.Contains("手动选择") || panel.GraphicsDetectionStatus.Contains("selected manually"), "manual-choice-status-agrees-with-current-selection");
        InstallerOptions.Validate(panel.Selection);
        InstallerOptions.ValidateDeploymentPolicy(panel.Selection);
        Require(FirmwareDownloads.Selected(JsonSerializer.Serialize(panel.Selection,new JsonSerializerOptions { PropertyNamingPolicy=JsonNamingPolicy.SnakeCaseLower })), "selected-graphics-enters-firmware-planning");
        var sourceRequired = false;
        try { InstallationController.ValidateRequest(new(InstallationTargetKind.ExternalDisk, "", false, panel.Selection)); }
        catch (IOException error) { sourceRequired = error.Message.Contains("请选择存在的系统镜像") || error.Message.Contains("Select an existing"); }
        Require(sourceRequired, "controller-allows-candidates-and-requires-real-source");
        Refuse(() => InstallerOptions.Validate(panel.Selection with { GraphicsProfile = "graphics:amd-unknown" }), "unknown-profile-rejected");
        var digest = new string('a', 64);
        Refuse(() => InstallerOptions.Validate(panel.Selection with { DevicePackage = digest[..24], DeviceManifestSha256 = digest, DeviceTrust = "developer-session" }), "forged-package-plus-graphics-rejected");
        panel.GraphicsHardwareReader = () => new([], [], 1); await panel.DetectGraphicsHardwareAsync();
        Require(panel.Selection.GraphicsProfile == "graphics:amd-880m", "unknown-model-preserves-manual-choice");
        panel.GraphicsHardwareReader = () => new(["graphics:amd-780m", "graphics:intel-g3-extreme"], ["two adapters"], 0);
        await panel.DetectGraphicsHardwareAsync();
        Require(panel.Selection.GraphicsProfile == "graphics:amd-880m", "multiple-candidates-require-manual-choice");
        panel.GraphicsHardwareReader = () => throw new IOException("fixture reader failed"); await panel.DetectGraphicsHardwareAsync();
        Require(panel.Selection.GraphicsProfile == "graphics:amd-880m" && (panel.GraphicsDetectionStatus.Contains("无法") || panel.GraphicsDetectionStatus.Contains("Could not")), "read-failure-preserves-manual-choice-and-explains-recovery");
        foreach (var mode in new[] { "local", "imported" })
        {
            panel.SetMode(mode);
            Select(panel, "graphics:amd-780m");
            Require(panel.GraphicsExpander.Visibility == Visibility.Collapsed && panel.Selection.GraphicsProfile == null, mode + "-hidden-programmatic-choice-cannot-enter-plan");
            panel.SetMode("custom");
            Require(panel.Selection.GraphicsProfile == null, mode + "-return-starts-cleared");
        }
        foreach (var context in new[] { "manual", "package", "mode-reset", "navigation" })
        {
            panel.SetMode("custom");
            var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
            var result = new TaskCompletionSource<BoilerGraphicsHardware.Detection>(TaskCreationOptions.RunContinuationsAsynchronously);
            panel.GraphicsHardwareReader = () => { entered.TrySetResult(); return result.Task.GetAwaiter().GetResult(); };
            var pending = panel.DetectGraphicsHardwareAsync(); await entered.Task.WaitAsync(TimeSpan.FromSeconds(10));
            Require(!panel.GraphicsDetectButton.IsEnabled && panel.GraphicsChoice.IsEnabled, context + "-native-read-does-not-block-manual-controls");
            if (context == "manual") Select(panel, "graphics:amd-880m");
            else if (context == "package") { panel.SetWirelessContext("custom", true); panel.SetWirelessContext("custom", false); }
            else if (context == "mode-reset") panel.SetMode("custom");
            else panel.CancelPendingWirelessDetection();
            result.SetResult(new(["graphics:amd-780m"], ["late adapter"], 0)); await pending;
            Require(panel.Selection.GraphicsProfile == (context == "manual" ? "graphics:amd-880m" : null), context + "-late-result-cannot-overwrite-current-plan");
        }
        Select(panel, "graphics:amd-780m"); panel.SetWirelessContext("custom", true);
        Require(panel.GraphicsExpander.Visibility == Visibility.Collapsed && panel.Selection.GraphicsProfile == null && panel.GraphicsChoice.SelectedIndex == 0, "device-route-hides-disables-and-clears-graphics");
        panel.SetWirelessContext("custom", false); Select(panel, null);
        InstallerOptions.ValidateDeploymentPolicy(panel.Selection);
        Require(panel.GraphicsChoice.SelectedIndex == 0, "opting-out-restores-deployment-policy");
        HardwareChecks(Require);

        var originalLanguage = InstallerI18n.Language;
        var directory = Path.Combine(InstallationController.Work, "graphics-ui-checks-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        var captures = new List<string>();
        var captureErrors = new List<string>();
        MainWindow? window = null;
        try
        {
            foreach (var language in new[] { "zh-CN", "en-US" })
            {
                InstallerI18n.SetLanguage(language);
                window = new MainWindow(rehearsal: true) { ShowInTaskbar = false, ShowActivated = true, WindowState = WindowState.Normal,
                    DeviceIndexLoaderForChecks = _ => Task.FromResult(BoilerDeviceChecks.IndexResponse()) };
                ((PartitionPicker)window.FindName("PartitionSelector")).AutomaticReadEnabled = false;
                window.Show(); window.BoilerModeForChecks("custom");
                typeof(MainWindow).GetMethod("ChooseGenericInstallation", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(window, null);
                window.PreviewStep(2);
                var options = ((StackPanel)window.FindName("ComponentOptionsHost")).Children.OfType<InstallerOptionsPanel>().Single();
                Select(options, "graphics:amd-880m");
                foreach (var size in new[] { new Size(1060, 720), new Size(640, 360) })
                {
                    window.Width = size.Width; window.Height = size.Height; window.UpdateLayout();
                    options.GraphicsChoice.BringIntoView();
                    await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
                    Require(options.GraphicsExpander.IsVisible && options.GraphicsChoice.IsVisible && options.GraphicsChoice.ActualWidth > 100,
                        language + "-graphics-choice-visible-" + size.Width);
                    var choicePosition = options.GraphicsChoice.TranslatePoint(new Point(), window);
                    var detectPosition = options.GraphicsDetectButton.TranslatePoint(new Point(), window);
                    Require(detectPosition.X >= choicePosition.X + options.GraphicsChoice.ActualWidth
                        && Math.Abs(detectPosition.Y - choicePosition.Y) < 1,
                        language + "-detect-is-right-of-model-" + size.Width);
                    var capture = Path.Combine(directory, language + "-graphics-" + size.Width + ".png");
                    try { await BoilerUiChecks.CaptureAsync((FrameworkElement)window.Content, capture); captures.Add(capture); }
                    catch (IOException error) { captureErrors.Add(error.Message); }
                    if (size.Width is 1060 or 640)
                    {
                        window.Activate();
                        options.GraphicsChoice.IsDropDownOpen = true;
                        await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
                        Require(options.GraphicsChoice.IsDropDownOpen && options.GraphicsChoice.Items.Count == 12, language + "-all-models-can-be-opened-and-selected");
                        var searchable = (ModelSearchComboBox)options.GraphicsChoice;
                        var search = searchable.SearchBox!;
                        Require(search.IsKeyboardFocused, language + "-search-focused-on-open-" + size.Width);
                        search.Text = "7840u";
                        var visible = searchable.Items.Cast<ComboBoxItem>().Where(item => item.Visibility == Visibility.Visible).ToArray();
                        Require(visible.Length == 1 && visible[0].Tag as string == "graphics:amd-780m"
                            && options.Selection.GraphicsProfile == "graphics:amd-880m", language + "-processor-search-preserves-choice-" + size.Width);
                        search.Text = "z2 extreme";
                        search.RaiseEvent(new KeyEventArgs(Keyboard.PrimaryDevice, PresentationSource.FromVisual(search), Environment.TickCount, Key.Space) { RoutedEvent = Keyboard.KeyDownEvent });
                        Require(searchable.IsDropDownOpen && search.IsKeyboardFocused && options.Selection.GraphicsProfile == "graphics:amd-880m",
                            language + "-space-stays-in-search-editor-" + size.Width);
                        Require(searchable.Items.Cast<ComboBoxItem>().Where(item => item.Visibility == Visibility.Visible).Any()
                            && searchable.Items.Cast<ComboBoxItem>().Where(item => item.Visibility == Visibility.Visible).All(item => ((TextBlock)item.Content).Text.Contains("Z2", StringComparison.OrdinalIgnoreCase)
                                && ((TextBlock)item.Content).Text.Contains("Extreme", StringComparison.OrdinalIgnoreCase)),
                            language + "-multiword-case-insensitive-search-" + size.Width);
                        search.Text = "no-such-gpu";
                        Require(searchable.Items.Cast<ComboBoxItem>().All(item => item.Visibility == Visibility.Collapsed)
                            && options.Selection.GraphicsProfile == "graphics:amd-880m", language + "-no-result-preserves-choice-" + size.Width);
                        search.Text = "780m";
                        await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
                        var popup = (Popup)searchable.Template.FindName("PART_Popup", searchable);
                        var popupCapture = Path.Combine(directory, language + "-graphics-search-" + size.Width + ".png");
                        try { await BoilerUiChecks.CaptureAsync((FrameworkElement)popup.Child, popupCapture); captures.Add(popupCapture); }
                        catch (IOException error) { captureErrors.Add(error.Message); }
                        options.GraphicsChoice.IsDropDownOpen = false;
                        await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
                        Require(search.Text == "" && searchable.Items.Cast<ComboBoxItem>().All(item => item.Visibility == Visibility.Visible)
                            && options.Selection.GraphicsProfile == "graphics:amd-880m", language + "-dismiss-clears-filter-keeps-choice-" + size.Width);
                        searchable.IsDropDownOpen = true;
                        await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
                        search.Text = "780m";
                        search.RaiseEvent(new KeyEventArgs(Keyboard.PrimaryDevice, PresentationSource.FromVisual(search), Environment.TickCount, Key.Enter) { RoutedEvent = Keyboard.PreviewKeyDownEvent });
                        Require(!searchable.IsDropDownOpen && options.Selection.GraphicsProfile == "graphics:amd-780m",
                            language + "-enter-confirms-search-result-" + size.Width);
                        Select(options, "graphics:amd-880m");
                        searchable.IsDropDownOpen = true;
                        await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
                        search.RaiseEvent(new KeyEventArgs(Keyboard.PrimaryDevice, PresentationSource.FromVisual(search), Environment.TickCount, Key.Enter) { RoutedEvent = Keyboard.PreviewKeyDownEvent });
                        Require(!searchable.IsDropDownOpen && options.Selection.GraphicsProfile == "graphics:amd-880m",
                            language + "-empty-enter-preserves-model-" + size.Width);
                        searchable.IsDropDownOpen = true;
                        await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
                        search.Text = "780m";
                        search.RaiseEvent(new KeyEventArgs(Keyboard.PrimaryDevice, PresentationSource.FromVisual(search), Environment.TickCount, Key.Escape) { RoutedEvent = Keyboard.PreviewKeyDownEvent });
                        Require(!searchable.IsDropDownOpen && options.Selection.GraphicsProfile == "graphics:amd-880m",
                            language + "-escape-preserves-model-" + size.Width);
                    }
                }
                window.PreviewStep(3);
                Require(window.CurrentOptions.GraphicsProfile == "graphics:amd-880m" && ((TextBlock)window.FindName("ReviewLayout")).Text.Contains("880M"), language + "-review-includes-graphics-preview");
                InstallerI18n.SetLanguage(language == "zh-CN" ? "en-US" : "zh-CN"); options.Refresh();
                Require(options.Selection.GraphicsProfile == "graphics:amd-880m", language + "-language-switch-preserves-choice");
                options.SetWirelessContext("custom", true);
                Require(!options.GraphicsExpander.IsVisible && window.CurrentOptions.GraphicsProfile == null, language + "-device-route-clears-real-window-plan");
                window.Close(); window = null;
            }
        }
        finally { window?.Close(); InstallerI18n.SetLanguage(originalLanguage); }
        var report = JsonSerializer.Serialize(new { passed = true, checks, captures, captureErrors, directory,
            scope = "GPU suggestion matching, candidate deployment selection, WPF layout and asynchronous result isolation. VM download/deployment/boot proof and physical hardware acceptance are recorded separately." }, new JsonSerializerOptions { WriteIndented = true });
        await File.WriteAllTextAsync(Path.Combine(directory, "result.json"), report);
        return report;
    }

    private static void HardwareChecks(Action<bool, string> require)
    {
        BoilerWirelessHardware.Device Device(string name, string id, Guid? cls = null) => new(cls ?? BoilerGraphicsHardware.DisplayClass, name, "GPU vendor", [id], []);
        require(BoilerGraphicsHardware.Match([Device("AMD Radeon(TM) 780M Graphics", "PCI\\VEN_1002&DEV_15BF")]).Profiles.SequenceEqual(["graphics:amd-780m"]), "pnp-780m-exact-name-and-pci-vendor");
        require(BoilerGraphicsHardware.Match([Device("AMD Radeon 880M Graphics", "PCI\\VEN_1002&DEV_150E")]).Profiles.SequenceEqual(["graphics:amd-880m"]), "pnp-880m-exact-name-and-pci-vendor");
        require(BoilerGraphicsHardware.Match([Device("AMD Radeon Graphics", "PCI\\VEN_1002&DEV_150E")]).Profiles.Length == 0, "shared-amd-pci-id-never-guesses-880m");
        require(BoilerGraphicsHardware.Match([Device("AMD Radeon 780M", "PCI\\VEN_10DE&DEV_1234"), Device("AMD Radeon 880M", "USB\\VID_1002&PID_1234"), Device("AMD Radeon 780M", "PCI\\VEN_1002&DEV_15BF", BoilerWirelessHardware.NetworkClass)]).Profiles.Length == 0, "software-other-class-usb-and-wrong-vendor-never-match");
        require(BoilerGraphicsHardware.Match([Device("Intel Arc B390", "PCI\\VEN_8086&DEV_B080")]).Profiles.SequenceEqual(["graphics:intel-g3-extreme"]), "intel-g3-extreme-official-pci-id");
        require(BoilerGraphicsHardware.Match([Device("Intel Arc B370", "PCI\\VEN_8086&DEV_B081")]).Profiles.SequenceEqual(["graphics:intel-g3"]), "intel-g3-official-pci-id");
        require(BoilerGraphicsHardware.Match([Device("Intel Arc B370", "PCI\\VEN_8086&DEV_B080")]).Profiles.Length == 0, "contradicting-intel-name-and-pci-id-never-match");
        require(BoilerGraphicsHardware.Match([Device("AMD Radeon Graphics", "PCI\\VEN_1002&DEV_150E")], "AMD Ryzen Z2 Extreme w/ Radeon Graphics").Profiles.SequenceEqual(["graphics:amd-z2-extreme"]), "z2-extreme-requires-exact-cpu-and-amd-display-device");
        require(BoilerGraphicsHardware.Match([Device("AMD Radeon RX 7900 XTX", "PCI\\VEN_1002&DEV_744C")], "AMD Ryzen Z2 Extreme w/ Radeon Graphics").Profiles.Length == 0, "z-cpu-plus-discrete-egpu-cannot-stand-in-for-integrated-gpu");
        require(BoilerGraphicsHardware.Match([Device("AMD Radeon 780M Graphics", "PCI\\VEN_1002&DEV_15BF")], "AMD Ryzen Z1 Extreme").Profiles.SequenceEqual(["graphics:amd-z1-extreme"]), "compatible-z-and-named-gpu-produce-one-precise-suggestion");
        require(BoilerGraphicsHardware.Match([Device("AMD Radeon 880M Graphics", "PCI\\VEN_1002&DEV_150E")], "AMD Ryzen Z1").Profiles.Length == 0, "contradicting-z-cpu-and-integrated-name-cannot-match");
        foreach (var (cpu, profile) in new[] { ("Ryzen Z1", "amd-z1"), ("Ryzen Z1 Extreme", "amd-z1-extreme"), ("Ryzen Z2", "amd-z2"), ("Ryzen Z2 Extreme", "amd-z2-extreme"), ("Ryzen AI Z2 Extreme", "amd-ai-z2-extreme"), ("Ryzen Z2 Go", "amd-z2-go"), ("Ryzen Z2 A", "amd-z2-a") })
            require(BoilerGraphicsHardware.Match([Device("AMD Radeon(TM) Graphics", "PCI\\VEN_1002&DEV_150E")], "AMD " + cpu).Profiles.SequenceEqual(["graphics:" + profile]), "exact-z-variant-" + profile);
        var duplicate = Device("AMD Radeon 780M Graphics", "PCI\\VEN_1002&DEV_15BF");
        require(BoilerGraphicsHardware.Match([duplicate, duplicate]).Profiles.SequenceEqual(["graphics:amd-780m"]), "duplicate-adapters-do-not-create-ambiguous-profiles");
    }
}
