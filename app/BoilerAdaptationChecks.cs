using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

namespace OxpInstaller;

internal static class BoilerAdaptationChecks
{
    private static T Field<T>(MainWindow window, string name) => (T)typeof(MainWindow).GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(window)!;
    private static object? Call(MainWindow window, string name, params object?[] args) => typeof(MainWindow).GetMethod(name, BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(window, args);
    private static async Task Flush(MainWindow window) { window.UpdateLayout(); await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle); }
    internal static Task<string> RunAsync() => Application.Current.Dispatcher.CheckAccess() ? OnUiThread() : Application.Current.Dispatcher.InvokeAsync(OnUiThread).Task.Unwrap();

    private static async Task<string> OnUiThread()
    {
        var directory = Path.Combine(Backend.Root, "logs", "device-choice-" + DateTime.UtcNow.ToString("yyyyMMddTHHmmssfff"));
        Directory.CreateDirectory(directory);
        var checks = new List<string>(); var captures = new List<string>(); var captureErrors = new List<string>();
        void Require(bool ok, string name, string? detail = null) { if (!ok) throw new IOException("Device choice check failed: " + name + (detail == null ? "" : ": " + detail)); checks.Add(name); }
        var originalLanguage = InstallerI18n.Language;
        var originalRenderMode = RenderOptions.ProcessRenderMode;
        RenderOptions.ProcessRenderMode = System.Windows.Interop.RenderMode.Default;
        MainWindow? window = null;
        try
        {
            foreach (var language in new[] { "zh-CN", "en-US" })
            {
                InstallerI18n.SetLanguage(language);
                window = new MainWindow(rehearsal: true) { Width = 1060, Height = 720, WindowState = WindowState.Normal, ShowActivated = true, DeviceIndexLoaderForChecks = _ => Task.FromResult(BoilerDeviceChecks.IndexResponse()) };
                ((PartitionPicker)window.FindName("PartitionSelector")).AutomaticReadEnabled = false;
                window.Show(); window.BoilerModeForChecks("custom");
                await Flush(window);
                var choices = Field<Grid>(window, "adaptationChoices");
                var options = Field<InstallerOptionsPanel>(window, "optionsPanel");
                Require(choices.IsVisible && choices.Children.Count == 2 && Field<int>(window, "step") == MainWindow.AdaptationStep, language + "-two-entry-cards-before-system-source");
                Require(!((Border)window.FindName("SidebarPanel")).IsVisible && ((ColumnDefinition)window.FindName("SidebarColumn")).ActualWidth == 0
                    && ((Button)window.FindName("AdaptationBackButton")).IsVisible && !((Grid)window.FindName("PageFooter")).IsVisible
                    && !((Button)window.FindName("AdaptationTab")).IsVisible, language + "-first-layer-entry-is-separate-from-numbered-wizard");
                ((Button)window.FindName("AdaptationBackButton")).RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); await Flush(window);
                Require(Field<Border>(window, "homeScreen") != null && !((Grid)window.FindName("WizardShell")).IsVisible, language + "-corner-back-returns-to-main-menu");
                window.BoilerModeForChecks("custom"); await Flush(window);
                Require(((Border)choices.Children[0]).Name == "ChooseDeviceInstallation" && ((Button)choices.Children[1]).Name == "ChooseGenericInstallation", language + "-first-layer-device-left-generic-right");
                var quickPicker = Field<StackPanel>(window, "deviceQuickPicker");
                var deviceList = Field<ComboBox>(window, "deviceList");
                Require(Field<StackPanel>(window, "adaptationDevicePickerHost").Children.Count == 1, language + "-reentering-mode-removes-stale-inline-picker");
                Require(quickPicker.IsVisible && deviceList.IsVisible && ReferenceEquals(quickPicker.Parent, Field<StackPanel>(window, "adaptationDevicePickerHost")), language + "-device-dropdown-is-inline-in-left-card");
                Require(Field<StackPanel>(window, "deviceImportArea").IsVisible
                    && Field<StackPanel>(window, "deviceImportArea").Children.OfType<Button>().Any(button => button.Name == "BrowseLocalDevice" && button.MinHeight >= 48), language + "-local-package-picker-in-online-path");
                Require(!Field<Button>(window, "inlineDeviceContinue").IsEnabled, language + "-inline-continue-requires-device-choice");
                Require(window.CurrentOptions.Plugins.Contains("core:maintenance") && options.OptionBoxes["core:maintenance"].IsChecked == true,
                    language + "-maintenance-default-enabled");
                Require(((TextBlock)((ComboBoxItem)deviceList.Items[1]).Content).Text == "Fixture Maker Test device" + PartitionInventory.L(" · GitHub 机型包", " · GitHub package"),
                    language + "-brand-precedes-model-name");
                var branded = JsonSerializer.SerializeToElement(new { id = "device:brand", brand = "ASUS", name = "ASUS ROG Ally" });
                Require(MainWindow.DeviceLabel(branded) == "ASUS ROG Ally", language + "-brand-prefix-not-duplicated");
                var anonymous = JsonSerializer.SerializeToElement(new { id = "device:anonymous", name = "Unbranded device" });
                Require(MainWindow.DeviceLabel(anonymous) == "Unbranded device", language + "-missing-brand-does-not-invent-one");
                foreach (var size in new[] { new Size(1060, 720), new Size(640, 480) })
                {
                    window.Width = size.Width; window.Height = size.Height; await Flush(window);
                    window.Activate(); deviceList.BringIntoView();
                    var searchList = (ModelSearchComboBox)deviceList;
                    searchList.IsDropDownOpen = true; await Flush(window);
                    var search = searchList.SearchBox!;
                    Require(!search.IsKeyboardFocusWithin && System.Windows.Automation.AutomationProperties.GetName(search).Length > 0,
                        language + "-model-search-opens-without-text-focus-and-is-labelled-" + size.Width,
                        JsonSerializer.Serialize(new { window.IsActive, searchList.IsDropDownOpen, search.IsVisible, search.IsKeyboardFocused,
                            search.IsKeyboardFocusWithin, label = System.Windows.Automation.AutomationProperties.GetName(search), focused = Keyboard.FocusedElement?.GetType().Name }));
                    await window.Dispatcher.InvokeAsync(() => { window.Activate(); searchList.Focus(); search.Focus(); }, DispatcherPriority.ContextIdle);
                    await Flush(window);
                    Require(search.IsKeyboardFocused, language + "-model-search-can-be-activated-explicitly-" + size.Width,
                        JsonSerializer.Serialize(new { window.IsActive, searchList.IsDropDownOpen, search.IsVisible, search.Focusable,
                            search.IsKeyboardFocused, focused = Keyboard.FocusedElement?.GetType().Name }));
                    search.Text = "fixture TEST";
                    Require(searchList.Items.Cast<ComboBoxItem>().Count(item => item.Visibility == Visibility.Visible) == 1
                        && searchList.SelectedIndex == 0 && window.CurrentOptions.DevicePackage == null,
                        language + "-model-search-matches-brand-and-model-without-changing-choice-" + size.Width);
                    search.Text = "no-such-device";
                    Require(searchList.Items.Cast<ComboBoxItem>().All(item => item.Visibility == Visibility.Collapsed),
                        language + "-model-search-empty-result-" + size.Width);
                    search.Text = "fixture maker";
                    var popup = (Popup)searchList.Template.FindName("PART_Popup", searchList);
                    await Capture((FrameworkElement)popup.Child, Path.Combine(directory, language + "-model-search-" + size.Width + ".png"), captures, captureErrors);
                    search.RaiseEvent(new KeyEventArgs(Keyboard.PrimaryDevice, PresentationSource.FromVisual(search), Environment.TickCount, Key.Enter) { RoutedEvent = Keyboard.PreviewKeyDownEvent });
                    await Flush(window);
                    Require(!searchList.IsDropDownOpen && searchList.SelectedIndex == 1 && window.CurrentOptions.DevicePackage == null,
                        language + "-model-search-enter-selects-without-loading-package-" + size.Width);
                    searchList.IsDropDownOpen = true; await Flush(window);
                    await window.Dispatcher.InvokeAsync(() => { searchList.SearchBox!.Focus(); }, DispatcherPriority.ContextIdle);
                    searchList.SearchBox!.RaiseEvent(new KeyEventArgs(Keyboard.PrimaryDevice, PresentationSource.FromVisual(searchList.SearchBox), Environment.TickCount, Key.Enter) { RoutedEvent = Keyboard.PreviewKeyDownEvent });
                    await Flush(window);
                    Require(searchList.SelectedIndex == 1, language + "-empty-enter-keeps-model-" + size.Width);
                    searchList.IsDropDownOpen = true; await Flush(window); searchList.SearchBox!.Text = "no-such-device";
                    await window.Dispatcher.InvokeAsync(() => { searchList.SearchBox!.Focus(); }, DispatcherPriority.ContextIdle);
                    searchList.SearchBox.RaiseEvent(new KeyEventArgs(Keyboard.PrimaryDevice, PresentationSource.FromVisual(searchList.SearchBox), Environment.TickCount, Key.Escape) { RoutedEvent = Keyboard.PreviewKeyDownEvent });
                    await Flush(window);
                    Require(!searchList.IsDropDownOpen && searchList.SelectedIndex == 1 && searchList.Items.Cast<ComboBoxItem>().All(item => item.Visibility == Visibility.Visible),
                        language + "-model-search-cancel-restores-list-and-keeps-model-" + size.Width,
                        JsonSerializer.Serialize(new { searchList.IsDropDownOpen, searchList.SelectedIndex, text = searchList.SearchBox.Text,
                            visible = searchList.Items.Cast<ComboBoxItem>().Select(item => item.Visibility.ToString()).ToArray(), searchList.SearchBox.IsKeyboardFocused }));
                    deviceList.SelectedIndex = 0;
                }
                window.Width = 1060; window.Height = 720; await Flush(window);
                var releasePackageAction = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
                var pendingPackageAction = (Task)Call(window, "DeviceAction", (Func<Task>)(() => releasePackageAction.Task))!;
                await Flush(window);
                Require(!choices.IsEnabled && !deviceList.IsEnabled && !((Button)window.FindName("AdaptationBackButton")).IsEnabled,
                    language + "-pending-package-prevents-model-edits-and-back");
                Call(window, "ChooseGenericInstallation");
                Call(window, "ChooseBoilerMode", "imported");
                Call(window, "ShowBoilerHome");
                Require(Field<int>(window, "step") == MainWindow.AdaptationStep && Field<string>(window, "installMode") == "custom"
                    && Field<Border?>(window, "homeScreen") == null, language + "-pending-package-cannot-switch-branch-or-mode");
                releasePackageAction.SetResult(); await pendingPackageAction; await Flush(window);
                Require(choices.IsEnabled && deviceList.IsEnabled && ((Button)window.FindName("AdaptationBackButton")).IsEnabled,
                    language + "-package-action-restores-navigation");
                window.DeviceHardwareReaderForChecks = () => new("Fixture Maker", "Fixture Model");
                Call(window, "DetectDeviceModel", true); await Flush(window);
                Require(deviceList.SelectedIndex == 1 && window.CurrentOptions.DevicePackage == null && Field<Button>(window, "inlineDeviceContinue").IsEnabled,
                    language + "-hardware-match-suggests-model-without-trusting-or-loading-package");
                deviceList.SelectedIndex = 0;
                Call(window, "RefreshDeviceLanguage"); await Flush(window);
                Require(deviceList.SelectedIndex == 0, language + "-manual-opt-out-survives-language-refresh");
                window.DeviceHardwareReaderForChecks = () => new("Fixture Maker", "Other Model");
                Call(window, "DetectDeviceModel", true); await Flush(window);
                Require(deviceList.SelectedIndex == 0 && window.CurrentOptions.DevicePackage == null, language + "-manufacturer-alone-does-not-match-another-model");
                deviceList.SelectedIndex = 1; await Flush(window);
                Require(Field<Button>(window, "inlineDeviceContinue").IsEnabled
                    && Field<TextBlock>(window, "inlineDeviceStatus").Text == MainWindow.DeviceCompatibilityNotice(),
                    language + "-mismatched-model-warns-without-blocking");
                deviceList.SelectedIndex = 0;
                window.DeviceHardwareReaderForChecks = () => null;
                Call(window, "DetectDeviceModel", true); await Flush(window);
                Require(deviceList.SelectedIndex == 0 && !Field<Button>(window, "inlineDeviceContinue").IsEnabled, language + "-unreadable-hardware-retains-manual-picker");
                deviceList.SelectedIndex = 1; await Flush(window);
                Require(Field<Button>(window, "inlineDeviceContinue").IsEnabled
                    && Field<TextBlock>(window, "inlineDeviceStatus").Text == MainWindow.DeviceCompatibilityNotice(),
                    language + "-unknown-model-warns-without-blocking");
                deviceList.SelectedIndex = 0;
                window.DeviceHardwareReaderForChecks = () => new("Fixture Maker", "Fixture Model");
                window.BoilerModeForChecks("custom"); await Flush(window);
                deviceList = Field<ComboBox>(window, "deviceList");
                Require(deviceList.SelectedIndex == 1 && window.CurrentOptions.DevicePackage == null, language + "-index-load-automatically-suggests-matching-model");
                deviceList.SelectedIndex = 0;
                await (Task)Call(window, "LoadDeviceIndexAsync", Field<int>(window, "devicePanelGeneration"), CancellationToken.None)!; await Flush(window);
                Require(deviceList.SelectedIndex == 0, language + "-directory-refresh-does-not-overwrite-manual-choice");
                Require(!options.IsVisible && !Field<Button>(window, "inlineDeviceContinue").IsEnabled, language + "-choice-does-not-show-generic-options-or-a-bypass-next");
                Require(!((StackPanel)window.FindName("RecoverySourceSection")).Children.Contains(Field<StackPanel>(window, "devicePanel")), language + "-device-picker-removed-from-source-page");
                foreach (var size in new[] { new Size(1060, 720), new Size(640, 480) })
                {
                    window.Width = size.Width; window.Height = size.Height; await Flush(window);
                    var wide = size.Width >= 960;
                    Require(choices.ColumnDefinitions.Count == (wide ? 2 : 1), language + "-choice-columns-" + size.Width);
                    Require(choices.Children.OfType<Button>().All(card => card.ActualHeight >= 48 && card.IsTabStop), language + "-cards-keyboard-and-touch-targets-" + size.Width);
                    var scroll = (ScrollViewer)window.FindName("PageScroll");
                    Require(scroll.ExtentWidth <= scroll.ViewportWidth + 1, language + "-choice-no-horizontal-overflow-" + size.Width);
                    scroll.ScrollToTop(); await Flush(window);
                    await Capture((FrameworkElement)window.Content, Path.Combine(directory, language + "-layer-one-choice-" + size.Width + ".png"), captures, captureErrors);
                    var deviceCard = (Border)choices.Children[0];
                    var genericCard = (Button)choices.Children[1];
                    Require(!wide || Math.Abs(deviceCard.ActualWidth - genericCard.ActualWidth) < 1
                        && Math.Abs(deviceCard.ActualHeight - genericCard.ActualHeight) < 1
                        && Grid.GetRow(deviceCard) == Grid.GetRow(genericCard)
                        && Grid.GetColumn(genericCard) == 1, language + "-first-layer-symmetric-two-column-cards-" + size.Width,
                        JsonSerializer.Serialize(new { deviceCard.ActualWidth, deviceCard.ActualHeight, genericWidth = genericCard.ActualWidth, genericHeight = genericCard.ActualHeight }));
                    window.PreviewStep(1); await Flush(window);
                    Require(((Border)window.FindName("SidebarPanel")).IsVisible
                        && !((Button)window.FindName("AdaptationTab")).IsVisible
                        && ((TextBlock)window.FindName("NavSource")).Text.StartsWith("01")
                        && ((TextBlock)window.FindName("NavContent")).Text.StartsWith("02")
                        && ((TextBlock)window.FindName("NavResult")).Text.StartsWith("07"),
                        language + "-second-layer-starts-at-system-source-and-has-seven-steps-" + size.Width);
                    await Capture((FrameworkElement)window.Content, Path.Combine(directory, language + "-layer-two-source-" + size.Width + ".png"), captures, captureErrors);
                    window.PreviewStep(MainWindow.AdaptationStep); await Flush(window);
                }
                window.Width = 1060; window.Height = 720;
                ((Button)choices.Children[1]).RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); await Flush(window);
                Require(Field<int>(window, "step") == 1 && !options.IsVisible && ((Border)window.FindName("SidebarPanel")).IsVisible, language + "-generic-card-goes-to-system-source-with-sidebar");
                Require(((Border)window.FindName("WorkspaceCard")).IsVisible, language + "-workspace-on-source-only");
                var source = Path.Combine(directory, language + "-recovery.img");
                await File.WriteAllBytesAsync(source, RecoveryImageChecks.Fixture());
                BindSourceFixture(window, source);
                ((Button)window.FindName("NextButton")).RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); await Flush(window);
                Require(options.IsVisible && options.RepairsExpander.IsVisible, language + "-generic-card-opens-wireless-and-common-options");
                Require(!((Border)window.FindName("WorkspaceCard")).IsVisible, language + "-workspace-not-repeated-on-options");
                Require(options.OptionBoxes["core:decky"].IsVisible && options.OptionBoxes["core:maintenance"].IsVisible,
                    language + "-generic-common-components-are-visible");
                var optionScroll = (ScrollViewer)window.FindName("PageScroll"); optionScroll.ScrollToTop(); await Flush(window);
                await Capture((FrameworkElement)window.Content, Path.Combine(directory, language + "-generic-options.png"), captures, captureErrors);
                var readerEntered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
                var pendingRadios = new TaskCompletionSource<IReadOnlyList<BoilerWirelessHardware.Device>>(TaskCreationOptions.RunContinuationsAsynchronously);
                options.WirelessHardwareReader = () => { readerEntered.TrySetResult(); return pendingRadios.Task.GetAwaiter().GetResult(); };
                var pendingDetection = options.DetectWirelessHardwareAsync();
                await readerEntered.Task.WaitAsync(TimeSpan.FromSeconds(10));
                window.PreviewStep(3); await Flush(window);
                var reviewBeforeDetection = ((TextBlock)window.FindName("ReviewSource")).Text;
                pendingRadios.SetResult([new(BoilerWirelessHardware.NetworkClass, "Intel Wi-Fi fixture", "Intel", [@"PCI\VEN_8086&DEV_0001"], [])]);
                await pendingDetection; await Flush(window);
                Require(Field<int>(window, "step") == 3 && window.CurrentOptions.Repairs.Length == 0
                    && ((TextBlock)window.FindName("ReviewSource")).Text == reviewBeforeDetection, language + "-late-wireless-result-cannot-change-reviewed-plan");
                window.PreviewStep(2); await Flush(window);
                options.OptionBoxes["wifi:intel"].IsChecked = true; options.OptionBoxes["bluetooth:realtek"].IsChecked = true;
                ((Button)window.FindName("BackButton")).RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); await Flush(window);
                Require(Field<int>(window, "step") == 1, language + "-options-back-returns-to-source");
                ((Button)window.FindName("BackButton")).RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); await Flush(window);
                Require(choices.IsVisible && !((Border)window.FindName("SidebarPanel")).IsVisible
                    && Field<string>(window, "adaptationBranch") == "generic", language + "-source-back-returns-to-first-layer-with-generic-selection");
                var retainedGenericOptions = JsonSerializer.Serialize(window.CurrentOptions);
                deviceList.SelectedIndex = 1;
                ((Button)choices.Children[1]).RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); await Flush(window);
                Require(Field<int>(window, "step") == 1 && JsonSerializer.Serialize(window.CurrentOptions) == retainedGenericOptions,
                    language + "-first-layer-generic-continue-preserves-all-option-choices");
                Require(deviceList.SelectedIndex == 0 && !Field<Button>(window, "inlineDeviceContinue").IsEnabled,
                    language + "-generic-continue-clears-uncommitted-device-candidate");
                ((Button)window.FindName("BackButton")).RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); await Flush(window);
                Call(window, "ChooseDeviceInstallation"); await Flush(window);
                Require(!options.IsVisible && window.CurrentOptions.Repairs.Length == 0, language + "-device-card-hides-and-clears-generic-wireless");
                Require(!((Button)window.FindName("NextButton")).IsEnabled, language + "-device-route-requires-an-actual-package");
                ((TextBox)window.FindName("ImagePath")).Clear();

                var packagePath = Path.Combine(directory, language + "-device.zip");
                var package = new
                {
                    schema_version = 1, kind = "DevicePackage", id = "device:route-fixture", version = "1.0.0",
                    name = new { zh = "机型交互测试包", en = "Device interaction fixture" }, status = "experimental",
                    source = new { kind = "local", path = "fixture" }, compatibility = new { }, files = Array.Empty<object>(),
                    components = new[] { new { schema_version = 1, kind = "ComponentManifest", id = "device:fixture-feature", version = "1.0.0",
                        name = new { zh = "机型专属测试选项", en = "Device fixture option" }, @default = false, files = Array.Empty<object>(), hooks = new { } },
                        new { schema_version = 1, kind = "ComponentManifest", id = "device:fixture-optional", version = "1.0.0",
                        name = new { zh = "另一项机型适配", en = "Another device adaptation" }, @default = false, files = Array.Empty<object>(), hooks = new { } } }
                };
                using (var archive = ZipFile.Open(packagePath, ZipArchiveMode.Create))
                using (var stream = archive.CreateEntry("manifest.json").Open())
                {
                    var data = System.Text.Json.Nodes.JsonNode.Parse(JsonSerializer.Serialize(package))!;
                    data["components"]![1]!["selection_policy"] = "explicit-opt-in";
                    await JsonSerializer.SerializeAsync(stream, data);
                }
                await (Task)Call(window, "ReceiveInputFilesAsync", new object?[] { new[] { packagePath } })!; await Flush(window);
                var preview = Field<Border>(window, "deviceOptionPreview");
                Require(!preview.IsVisible && !options.IsVisible && window.CurrentOptions.DevicePackage != null,
                    language + "-import-selects-device-without-preview-or-trust-confirmation");
                Require(!Field<Button>(window, "inputApplyButton").IsEnabled, language + "-completed-import-needs-no-associate-click");
                await Capture((FrameworkElement)window.Content, Path.Combine(directory, language + "-package-imported.png"), captures, captureErrors);
                Require(window.CurrentOptions.DevicePackage != null && window.CurrentOptions.DeviceComponents!.SequenceEqual(["device:fixture-feature", "device:fixture-optional"]), language + "-trusted-package-defaults-to-all-adaptations", Field<TextBlock>(window, "inputAssociationStatus").Text + " / " + JsonSerializer.Serialize(window.CurrentOptions.DeviceComponents));
                Require(Field<int>(window, "step") == 1 && !options.IsVisible, language + "-trusted-device-without-source-opens-source-first");
                ((Button)window.FindName("BackButton")).RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); await Flush(window);
                Require(choices.IsVisible && !Field<StackPanel>(window, "adaptationDeviceHost").IsVisible, language + "-source-back-skips-redundant-device-page");
                Require(Field<TextBlock>(window, "inlineDeviceStatus").IsVisible
                    && Field<TextBlock>(window, "inlineDeviceStatus").Text.Contains(language == "zh-CN" ? "机型交互测试包" : "Device interaction fixture")
                    && Field<Button>(window, "inlineDeviceContinue").IsVisible && Field<Button>(window, "inlineDeviceContinue").IsEnabled,
                    language + "-source-back-shows-active-local-package-and-can-continue");
                Field<Button>(window, "inlineDeviceContinue").RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); await Flush(window);
                Require(Field<int>(window, "step") == 1 && window.CurrentOptions.DeviceComponents!.Length == 2,
                    language + "-first-layer-package-continue-preserves-selection-and-opens-second-layer");
                Call(window, "ContinueAfterDeviceSelection"); await Flush(window);
                BindSourceFixture(window, source);
                ((Button)window.FindName("NextButton")).RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); await Flush(window);
                Require(options.IsVisible && !options.RepairsExpander.IsVisible && !preview.IsVisible, language + "-package-options-replace-generic-wireless");
                var packageOptions = Field<Dictionary<string, CheckBox>>(window, "deviceComponentChoices");
                var allAdaptations = Field<CheckBox>(window, "deviceAdaptationsChoice");
                Require(allAdaptations.IsVisible && allAdaptations.IsEnabled && allAdaptations.IsChecked == true
                    && Field<StackPanel>(window, "deviceComponentsPanel").Children.OfType<CheckBox>().Count() == 3,
                    language + "-default-checked-package-adaptation-options");
                Require(((TextBlock)allAdaptations.Content).Text == PartitionInventory.L("机型适配修复", "Device compatibility fixes"),
                    language + "-device-fixes-requested-label");
                await Capture((FrameworkElement)window.Content, Path.Combine(directory, language + "-package-defaults.png"), captures, captureErrors);
                allAdaptations.IsChecked = false;
                Require(window.CurrentOptions.DeviceComponents!.Length == 0 && packageOptions.Values.All(box => box.IsChecked == false), language + "-package-opt-out-clears-all-component-ids");
                allAdaptations.IsChecked = true;
                Require(window.CurrentOptions.DeviceComponents!.Length == 2, language + "-package-opt-in-restores-all-component-ids");
                allAdaptations.IsChecked = false;
                ((RadioButton)window.FindName(language == "zh-CN" ? "EnglishLanguage" : "ChineseLanguage")).IsChecked = true; await Flush(window);
                Require(window.CurrentOptions.DeviceComponents!.Length == 0 && !options.RepairsExpander.IsVisible, language + "-language-switch-keeps-package-choice-and-no-wireless");
                Require(((TextBlock)window.FindName("PageSubtitle")).Text == MainWindow.DeviceOptionsDescription(), language + "-language-switch-updates-options-description");
                await Capture((FrameworkElement)window.Content, Path.Combine(directory, language + "-package-options.png"), captures, captureErrors);
                ((Button)window.FindName("BackButton")).RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); await Flush(window);
                ((Button)window.FindName("NextButton")).RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); await Flush(window);
                Require(window.CurrentOptions.DeviceComponents!.Length == 0 && options.IsVisible && !options.RepairsExpander.IsVisible, language + "-back-and-next-preserve-edited-package-options");
                var selectedPackage = window.CurrentOptions.DevicePackage;
                var alternate = System.Text.Json.Nodes.JsonNode.Parse(JsonSerializer.Serialize(package))!;
                alternate["id"] = "device:another-route-fixture";
                alternate["components"]![0]!["id"] = "device:other-feature";
                var alternatePath = Path.Combine(directory, language + "-another-device.zip");
                using (var archive = ZipFile.Open(alternatePath, ZipArchiveMode.Create))
                using (var writer = new StreamWriter(archive.CreateEntry("manifest.json").Open()))
                    await writer.WriteAsync(alternate.ToJsonString());
                Field<Button>(window, "inputClearButton").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                await (Task)Call(window, "ReceiveInputFilesAsync", new object?[] { new[] { alternatePath } })!; await Flush(window);
                Require(!preview.IsVisible && window.CurrentOptions.DevicePackage != selectedPackage
                    && window.CurrentOptions.DeviceComponents!.Contains("device:other-feature"), language + "-new-import-replaces-package-without-confirmation");
                ((Button)window.FindName("BackButton")).RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); await Flush(window);
                Require(choices.IsVisible && !((Border)window.FindName("SidebarPanel")).IsVisible,
                    language + "-replacement-import-back-returns-to-first-layer");
                ((Button)choices.Children[1]).RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); await Flush(window);
                Require(window.CurrentOptions.DevicePackage == null && Field<int>(window, "step") == 1 && window.CurrentOptions.Repairs.Length == 0, language + "-explicit-generic-clears-package-and-does-not-restore-wireless");
                window.BoilerModeForChecks("vanilla");
                Require(window.CurrentOptions.Plugins.Contains("core:maintenance"), language + "-vanilla-maintenance-default-enabled");
                Require(Field<int>(window, "step") == 1 && Field<string>(window, "adaptationBranch") == "generic"
                    && !((StackPanel)window.FindName("AdaptationPage")).IsVisible,
                    language + "-vanilla-enters-source-directly-without-device-picker");
                BindSourceFixture(window, source);
                await (Task)Call(window, "ReceiveInputFilesAsync", new object?[] { new[] { packagePath } })!;
                await (Task)Call(window, "ApplyInputAssociationsAsync")!; await Flush(window);
                var digest = typeof(MainWindow).GetProperty("CurrentSourceDigest", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(window);
                Require(window.CurrentOptions.InstallMode == "custom" && digest is string value && value == new string('a', 64), language + "-vanilla-device-association-preserves-unchanged-source-binding");
                foreach (var mode in new[] { "local", "imported" })
                {
                    window.BoilerModeForChecks(mode); await Flush(window);
                    Require(Field<int>(window, "step") == 1 && !((StackPanel)window.FindName("AdaptationPage")).IsVisible
                        && !((Button)window.FindName("AdaptationBackButton")).IsVisible && ((Border)window.FindName("SidebarPanel")).IsVisible,
                        language + "-" + mode + "-retains-direct-source-route");
                }
                window.Close(); window = null;
            }
        }
        finally { window?.Close(); InstallerI18n.SetLanguage(originalLanguage); RenderOptions.ProcessRenderMode = originalRenderMode; }
        var result = JsonSerializer.Serialize(new { passed = true, visualCapturePassed = captureErrors.Count == 0 && captures.Count > 0, checks, captures, captureErrors, directory,
            scope = "Synthetic WPF route/card/drop/trust/component-selection interactions. Visual captures are reported separately and empty images are not accepted. No download, package execution, installation or device changes." }, new JsonSerializerOptions { WriteIndented = true });
        await File.WriteAllTextAsync(Path.Combine(directory, "result.json"), result); return result;
    }

    private static async Task Capture(FrameworkElement root, string path, List<string> captures, List<string> errors)
    {
        try { await BoilerUiChecks.CaptureAsync(root, path); captures.Add(path); }
        catch (IOException error) { errors.Add(Path.GetFileName(path) + ": " + error.Message); }
    }

    private static void BindSourceFixture(MainWindow window, string source)
    {
        ((CheckBox)window.FindName("UsePrepared")).IsChecked = false;
        ((CheckBox)window.FindName("Upgrade310")).IsChecked = false;
        ((TextBox)window.FindName("ImagePath")).Text = source;
        typeof(MainWindow).GetField("inspectedSourceKey", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(window, Call(window, "SourceInfoKey"));
        typeof(MainWindow).GetField("inspectedSourceDigest", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(window, new string('a', 64));
        typeof(MainWindow).GetField("inspectedSourceInfo", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(window, JsonSerializer.SerializeToElement(new { layout = "fixture", slots = new { } }));
    }
}
