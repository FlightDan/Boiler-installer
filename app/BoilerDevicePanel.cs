using System.IO;
using System.Numerics;
using System.Text.RegularExpressions;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Media;
using Microsoft.Win32;

namespace OxpInstaller;

public partial class MainWindow
{
    private CancellationTokenSource? deviceIndexCancellation;
    private Task? deviceIndexTask;
    private JsonElement? lastDeviceIndex;
    private JsonElement? displayedDeviceMetadata;
    private TextBlock? deviceIndexStatus;
    private Button? deviceIndexRetry;
    private Button? signedDeviceUse;
    private Button? deviceDropTarget;
    private string? pendingDeviceDirectory;
    private string? deviceIndexError;
    private bool deviceIndexOffline;
    private bool updatingDeviceList;
    private int devicePanelGeneration;
    private readonly List<Action> deviceLanguageUpdates = new();
    private StackPanel? deviceQuickPicker;
    private TextBlock? deviceDetectionStatus;
    private StackPanel? deviceDirectoryBody;
    private Expander? deviceDirectoryDetails;
    private BoilerHardware.Identity? detectedComputer;
    private bool deviceChoiceChanged, applyingDeviceDetection;
    private string deviceDetectionState = "pending";
    private bool deviceSourceBundled, deviceSourceChosen;
    private WrapPanel? deviceSourceTabs;
    private RadioButton? bundledDeviceTab, onlineDeviceTab;
    private TextBlock? deviceSourceHelp, deviceSourceStatus;
    private StackPanel? deviceImportArea;
    private string? importedDeviceFileName;
    internal Func<CancellationToken, Task<JsonElement>>? DeviceIndexLoaderForChecks { get; set; }
    internal Func<BoilerHardware.Identity?> DeviceHardwareReaderForChecks { get; set; } = BoilerHardware.ReadThisComputer;

    private void CreateDevicePanel()
    {
        if (deviceQuickPicker?.Parent is Panel priorPickerParent) priorPickerParent.Children.Remove(deviceQuickPicker);
        deviceIndexCancellation?.Cancel();
        deviceIndexCancellation?.Dispose();
        deviceIndexCancellation = new();
        var token = deviceIndexCancellation.Token;
        var generation = ++devicePanelGeneration;
        lastDeviceIndex = displayedDeviceMetadata = null;
        modelCatalog = null; modelCatalogError = null;
        deviceIndexError = pendingDeviceDirectory = null;
        detectedComputer = null; deviceDetectionState = "pending"; deviceChoiceChanged = false;
        deviceSourceBundled = deviceSourceChosen = false; importedDeviceFileName = null;
        deviceLanguageUpdates.Clear();
        devicePanel = new StackPanel { Name = "DevicePanel", Margin = new Thickness(0, 0, 0, 20) };
        var panel = devicePanel;
        deviceQuickPicker = new StackPanel { Name = "DeviceQuickPicker" };
        var picker = deviceQuickPicker;
        panel.Children.Add(picker);
        TextBlock Text(string zh, string en, double size = 14)
        {
            var text = new TextBlock { FontSize = size, TextWrapping = TextWrapping.Wrap };
            void Refresh() => text.Text = PartitionInventory.L(zh, en);
            deviceLanguageUpdates.Add(Refresh); Refresh(); return text;
        }
        Button Button(string name, string zh, string en)
        {
            var button = new Button { Name = name, MinHeight = 48, HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(0, 4, 10, 4) };
            var label = Text(zh, en); button.Content = label;
            return button;
        }
        deviceSourceTabs = new WrapPanel { Name = "DeviceSourceTabs", Margin = new(0, 0, 0, 10), Visibility = Visibility.Collapsed };
        RadioButton SourceTab(string name, string zh, string en, bool bundled)
        {
            var tab = new RadioButton { Name = name, GroupName = "DevicePackageSource", MinHeight = 44,
                Style = (Style)FindResource("DeviceSourceTab"),
                VerticalContentAlignment = VerticalAlignment.Center, Margin = new(0, 0, 16, 0), Content = Text(zh, en) };
            tab.Checked += (_, _) => { if (!updatingDeviceList) SwitchDeviceSource(bundled); };
            deviceSourceTabs.Children.Add(tab); return tab;
        }
        bundledDeviceTab = SourceTab("BundledDeviceTab", "自带机型包", "Bundled packages", true);
        onlineDeviceTab = SourceTab("OnlineDeviceTab", "在线目录 / 导入", "Online directory / import", false);
        picker.Children.Add(deviceSourceTabs);
        deviceSourceHelp = new TextBlock { TextWrapping = TextWrapping.Wrap, Foreground = BoilerBrush("#61728A"), Margin = new(0, 0, 0, 8) };
        picker.Children.Add(deviceSourceHelp);
        var heading = Text("机型", "Device", 14); heading.FontWeight = FontWeights.SemiBold;
        picker.Children.Add(heading);
        var detect = Button("DetectDeviceModel", "自动识别", "Detect this computer");
        detect.Margin = new(0, 8, 0, 4);
        detect.Click += (_, _) => DetectDeviceModel(explicitRequest: true);
        var browse = Button("BrowseLocalDevice", "导入本地包…", "Import local package…");
        browse.Click += async (_, _) =>
        {
            if (operation != null || DownloadsBusy || deviceActionBusy) return;
            var dialog = new OpenFileDialog { Title = PartitionInventory.L("选择本地机型包", "Choose a local device package"),
                Filter = PartitionInventory.L("机型包与脚本|*.zip;*.tar;*.tar.gz;*.tgz;*.tar.xz;*.txz;*.sh|所有文件|*.*", "Device packages and scripts|*.zip;*.tar;*.tar.gz;*.tgz;*.tar.xz;*.txz;*.sh|All files|*.*"), CheckFileExists = true };
            if (dialog.ShowDialog(this) != true) return;
            if (installMode == "vanilla") PromoteDeviceMode();
            await ImportDeviceInputAsync(dialog.FileName);
        };
        deviceImportArea = new StackPanel { Name = "DeviceImportArea", Margin = new(0, 12, 0, 4) };
        deviceImportArea.Children.Add(new Border { Height = 1, Background = BoilerBrush("#DFE6F0"), Margin = new(0, 0, 0, 12) });
        var importHeading = Text("已有机型包？", "Already have a package?"); importHeading.FontWeight = FontWeights.SemiBold;
        deviceImportArea.Children.Add(importHeading);
        deviceImportArea.Children.Add(Text("浏览文件，或将机型包拖到此处。", "Browse files or drop a device package here."));
        deviceImportArea.Children.Add(browse);
        deviceDirectoryBody = new StackPanel();
        deviceDirectoryDetails = new Expander { Name = "DeviceDirectoryDetails", MinHeight = 48,
            Content = deviceDirectoryBody, Margin = new(0, 8, 0, 0) };
        void DirectoryLabel() => deviceDirectoryDetails.Header = PartitionInventory.L("目录与识别信息", "Directory and detection details");
        deviceLanguageUpdates.Add(DirectoryLabel); DirectoryLabel();
        deviceDetectionStatus = new TextBlock { TextWrapping = TextWrapping.Wrap, Foreground = BoilerBrush("#485B73"), Margin = new(0, 2, 0, 6) };
        deviceDirectoryBody.Children.Add(deviceDetectionStatus);
        deviceIndexStatus = new TextBlock { TextWrapping = TextWrapping.Wrap, Foreground = BoilerBrush("#485B73"), Margin = new Thickness(0, 6, 0, 4) };
        deviceDirectoryBody.Children.Add(deviceIndexStatus);
        deviceIndexRetry = Button("RetryDeviceIndex", "重新读取目录", "Retry device directory");
        deviceIndexRetry.Visibility = Visibility.Collapsed;
        deviceIndexRetry.Click += async (_, _) => await Task.WhenAll(LoadDeviceIndexAsync(generation, token), LoadModelCatalogAsync(generation, token));
        deviceDirectoryBody.Children.Add(deviceIndexRetry);
        deviceList = new ModelSearchComboBox { DeviceModels = true, SearchText = DeviceSearchText,
            Name = "DeviceList", MinHeight = 48, Margin = new Thickness(0, 8, 8, 4), HorizontalContentAlignment = HorizontalAlignment.Stretch };
        AutomationProperties.SetName(deviceList, PartitionInventory.L("目标机型", "Target device"));
        deviceList.SelectionChanged += (_, _) => DeviceListSelectionChanged();
        var pickerRow = new Grid();
        pickerRow.ColumnDefinitions.Add(new ColumnDefinition());
        pickerRow.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        pickerRow.Children.Add(deviceList); Grid.SetColumn(detect, 1); pickerRow.Children.Add(detect);
        picker.Children.Add(pickerRow);
        picker.Children.Add(deviceImportArea);
        deviceSourceStatus = new TextBlock { Name = "DeviceSourceStatus", TextWrapping = TextWrapping.Wrap,
            Foreground = BoilerBrush("#485B73"), Margin = new(0, 12, 0, 4) };
        picker.Children.Add(deviceSourceStatus);
        picker.Children.Add(deviceDirectoryDetails);
        CreateModelCatalogDetails(picker);
        deviceStatus = new TextBlock { TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 6, 0, 6) };
        panel.Children.Add(deviceStatus);
        signedDeviceUse = Button("UseSignedDevice", "使用所选机型包", "Use selected device package");
        signedDeviceUse.Visibility = Visibility.Collapsed;
        signedDeviceUse.Click += async (_, _) => await DeviceAction(SelectChosenDeviceAsync);
        panel.Children.Add(signedDeviceUse);

        deviceDropTarget = inputDropButton;
        var links = new WrapPanel { Margin = new Thickness(0, 4, 0, 4) };
        var guide = Button("DevicePackageGuide", "机型包制作说明", "Device package guide");
        guide.Click += (_, _) =>
        {
            try
            {
                var viewer = new System.Diagnostics.ProcessStartInfo("notepad.exe") { UseShellExecute = true };
                viewer.ArgumentList.Add(Path.Combine(Backend.Root, "docs/DEVICE_PACKAGES.md"));
                System.Diagnostics.Process.Start(viewer);
            }
            catch (Exception error) { deviceStatus.Text = PartitionInventory.L("请打开 docs/DEVICE_PACKAGES.md：", "Open docs/DEVICE_PACKAGES.md: ") + error.Message; }
        };
        links.Children.Add(guide); panel.Children.Add(links);

        deviceNotes = new TextBlock { TextWrapping = TextWrapping.Wrap, Margin = new Thickness(12, 8, 12, 12) };
        deviceNotesExpander = new Expander { Content = deviceNotes, MinHeight = 48, Visibility = Visibility.Collapsed };
        void NotesLabel() => deviceNotesExpander.Header = PartitionInventory.L("适配备注与验证记录", "Compatibility notes and validation records");
        deviceLanguageUpdates.Add(NotesLabel); NotesLabel(); panel.Children.Add(deviceNotesExpander);
        deviceSourceLink = new TextBlock { MinHeight = 48, TextWrapping = TextWrapping.Wrap, Visibility = Visibility.Collapsed };
        panel.Children.Add(deviceSourceLink);
        PopulateDeviceList();
        RefreshDeviceLanguage();
        var started = false;
        void LoadWhenVisible()
        {
            if (started || installMode is not ("custom" or "vanilla") || !picker.IsVisible) return;
            started = true; deviceIndexTask = Task.WhenAll(LoadDeviceIndexAsync(generation, token), LoadModelCatalogAsync(generation, token));
        }
        picker.Loaded += (_, _) => LoadWhenVisible();
        picker.IsVisibleChanged += (_, _) => LoadWhenVisible();
        adaptationDeviceHost!.Children.Insert(0, panel);
        PlaceDeviceQuickPicker(adaptationScreen == AdaptationScreen.Choice);
        InitializeDeviceFileDrop();
    }

    private bool HasBundledDeviceChoices => modelCatalog is JsonElement catalog
        && catalog.GetProperty("models").EnumerateArray().Any(model => HasBundledModel(model.GetProperty("id").GetString()!));

    private void SwitchDeviceSource(bool bundled)
    {
        if (operation != null || DownloadsBusy || deviceActionBusy || inputInspectionBusy || inputAssociationBusy)
        { RefreshDeviceSourcePresentation(); return; }
        deviceSourceChosen = true; deviceChoiceChanged = true;
        deviceDetectionState = "manual";
        if (deviceSourceBundled == bundled) { RefreshDeviceSourcePresentation(); return; }
        deviceSourceBundled = bundled && HasBundledDeviceChoices;
        updatingDeviceList = true;
        try { deviceList!.SelectedIndex = 0; }
        finally { updatingDeviceList = false; }
        // Browsing another source does not remove a package already in the installation configuration.
        if (selectedDevicePackage == null && !HasPendingDevicePreview)
        {
            displayedDeviceMetadata = null; ClearModelGuide();
            signedDeviceUse!.Visibility = Visibility.Collapsed;
            deviceStatus!.Text = "";
        }
        PopulateDeviceList(); RefreshDeviceDetectionStatus();
    }

    private void RefreshDeviceSourcePresentation()
    {
        if (deviceSourceTabs == null) return;
        deviceSourceTabs.Visibility = HasBundledDeviceChoices ? Visibility.Visible : Visibility.Collapsed;
        var wasUpdating = updatingDeviceList; updatingDeviceList = true;
        try { bundledDeviceTab!.IsChecked = deviceSourceBundled; onlineDeviceTab!.IsChecked = !deviceSourceBundled; }
        finally { updatingDeviceList = wasUpdating; }
        deviceSourceHelp!.Text = deviceSourceBundled
            ? PartitionInventory.L("随安装器提供，无需下载机型包。", "Included with this installer; no package download needed.")
            : deviceIndexOffline ? PartitionInventory.L("离线：使用已验签的 GitHub 缓存，无法检查新的撤销记录。", "Offline: using verified GitHub cache; newer revocations cannot be checked.")
            : PartitionInventory.L("从 GitHub 目录查找机型，或导入已有机型包。", "Find a device in the GitHub directory or import an existing package.");
        deviceDirectoryDetails!.Header = deviceSourceBundled ? PartitionInventory.L("识别与适配信息", "Detection and adaptation details")
            : PartitionInventory.L("目录与识别信息", "Directory and detection details");
        deviceIndexStatus!.Visibility = deviceIndexRetry!.Visibility = deviceSourceBundled ? Visibility.Collapsed : Visibility.Visible;
        if (!deviceSourceBundled) deviceIndexRetry.Visibility = deviceIndexError != null || deviceIndexOffline || modelCatalogError != null ? Visibility.Visible : Visibility.Collapsed;
        string source = "";
        if (selectedDevicePackage != null)
            source = selectedDeviceTrust == "signed-index" ? PartitionInventory.L("GitHub 目录", "GitHub directory")
                : importedDeviceFileName != null ? PartitionInventory.L("手动导入：", "Manual import: ") + importedDeviceFileName
                : PartitionInventory.L("安装器自带", "Bundled with installer");
        else if (deviceList?.SelectedItem is ComboBoxItem { Tag: JsonElement entry })
            source = IsModelGuide(entry)
                ? deviceSourceBundled ? PartitionInventory.L("安装器自带", "Bundled with installer") : PartitionInventory.L("仅适配指南", "Guide only")
                : PartitionInventory.L("GitHub 目录", "GitHub directory");
        deviceSourceStatus!.Text = source.Length == 0 ? "" : PartitionInventory.L("当前来源：", "Current source: ") + source;
        deviceSourceStatus.Visibility = source.Length == 0 ? Visibility.Collapsed : Visibility.Visible;
    }

    private static bool TryDeviceDrop(IDataObject data, out string? path)
    {
        path = null;
        if (!data.GetDataPresent(DataFormats.FileDrop) || data.GetData(DataFormats.FileDrop) is not string[] { Length: 1 } files) return false;
        path = files[0]; return IsDeviceInput(path);
    }
    internal static bool IsDeviceInput(string path) => Directory.Exists(path) || new[] { ".sh", ".zip", ".tar", ".tar.gz", ".tgz", ".tar.xz", ".txz" }.Any(suffix => path.EndsWith(suffix, StringComparison.OrdinalIgnoreCase));

    private async Task LoadDeviceIndexAsync(int generation, CancellationToken token)
    {
        if (generation != devicePanelGeneration || token.IsCancellationRequested) return;
        deviceIndexRetry!.IsEnabled = false;
        deviceIndexError = null; UpdateDeviceIndexStatus(loading: true);
        try
        {
            var response = DeviceIndexLoaderForChecks != null ? await DeviceIndexLoaderForChecks(token)
                : await BoilerProtocol.RunAsync(["load-index", "https://github.com/FlightDan/Boiler-adaptations/releases/latest/download/index.json", "https://github.com/FlightDan/Boiler-adaptations/releases/latest/download/index.sig", BoilerProtocol.RegistryCache], token);
            if (generation != devicePanelGeneration || token.IsCancellationRequested) return;
            lastDeviceIndex = response.GetProperty("index").Clone();
            deviceIndexOffline = response.TryGetProperty("offline", out var offline) && offline.GetBoolean();
            PopulateDeviceList(); UpdateDeviceIndexStatus();
            if (!deviceChoiceChanged) DetectDeviceModel(explicitRequest: false);
        }
        catch (OperationCanceledException) { }
        catch (Exception error)
        {
            if (generation != devicePanelGeneration || token.IsCancellationRequested) return;
            deviceIndexError = error.Message;
            UpdateDeviceIndexStatus();
        }
        finally
        {
            if (generation == devicePanelGeneration) { deviceIndexRetry!.IsEnabled = true; RefreshModelCatalogStatus(); }
        }
    }

    private void UpdateDeviceIndexStatus(bool loading = false)
    {
        deviceIndexStatus!.Visibility = deviceSourceBundled ? Visibility.Collapsed : Visibility.Visible;
        deviceIndexRetry!.Visibility = !deviceSourceBundled && (deviceIndexError != null || deviceIndexOffline) ? Visibility.Visible : Visibility.Collapsed;
        if (loading)
        {
            deviceIndexStatus!.Text = PartitionInventory.L("正在读取 GitHub 机型目录…", "Loading the GitHub device directory…"); return;
        }
        if (deviceIndexError != null)
        {
            if (!deviceSourceBundled) deviceDirectoryDetails!.IsExpanded = true;
            deviceIndexStatus!.Text = PartitionInventory.L("签名机型包目录暂时无法读取。可以重试，仍可查看适配清单或导入本地包。", "The signed package directory is unavailable. Retry, view adaptation guides, or import a local package.");
            deviceIndexStatus.ToolTip = deviceIndexError; return;
        }
        deviceIndexStatus!.ToolTip = null;
        if (lastDeviceIndex is not JsonElement index)
        {
            deviceIndexStatus.Text = PartitionInventory.L("进入页面后自动读取 GitHub 机型目录。", "The GitHub device directory loads automatically."); return;
        }
        var count = index.GetProperty("packages").EnumerateArray().Count(IsAvailableDeviceEntry);
        deviceIndexStatus.Text = (deviceIndexOffline
            ? PartitionInventory.L("离线：已自动使用验签缓存，无法检查新的撤销记录。", "Offline: using verified cache; newer revocations cannot be checked.")
            : PartitionInventory.L("GitHub 机型目录已更新并验签。", "GitHub device directory updated and signature verified."))
            + " " + (count == 0 ? PartitionInventory.L("目前没有可用机型包。", "No device packages are available yet.") : PartitionInventory.L($"共 {count} 个机型包。", $"{count} device package(s) available."));
    }

    private bool IsAvailableDeviceEntry(JsonElement entry) => lastDeviceIndex is JsonElement index
        && !index.GetProperty("revocations").EnumerateArray().Any(value => value.GetString() == entry.GetProperty("sha256").GetString())
        && DevicePackageVersion.TryParse(entry.GetProperty("version").GetString(), out _)
        && (!entry.TryGetProperty("available", out var available) || available.ValueKind == JsonValueKind.True)
        && (!entry.TryGetProperty("minimum_engine_version", out var minimum)
            || minimum.ValueKind == JsonValueKind.String && DevicePackageVersion.EngineSupports(minimum.GetString()));

    internal static string DeviceLabel(JsonElement entry)
    {
        var model = entry.TryGetProperty("name", out var name) ? LocalizedValue(name) : entry.GetProperty("id").GetString()!;
        var brand = new[] { "brand", "manufacturer" }.Select(key => entry.TryGetProperty(key, out var value) ? LocalizedValue(value) : "")
            .FirstOrDefault(value => !string.IsNullOrWhiteSpace(value));
        if (brand == null && entry.TryGetProperty("hardware_matching", out var matching) && matching.TryGetProperty("pairs", out var pairs))
        {
            var makers = pairs.EnumerateArray().Select(pair => pair.GetProperty("manufacturer").GetString()!.Trim())
                .Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
            if (makers.Length == 1) brand = makers[0];
        }
        return string.IsNullOrWhiteSpace(brand) || model.StartsWith(brand, StringComparison.OrdinalIgnoreCase) ? model : brand + " " + model;
    }

    private static string DeviceSearchText(ComboBoxItem item)
    {
        if (item.Tag is not JsonElement entry) return "";
        var names = entry.TryGetProperty("name", out var name) ? name.ToString() : "";
        return DeviceLabel(entry) + " " + names + " " + entry.GetProperty("id").GetString();
    }

    private void DeviceListSelectionChanged()
    {
        if (updatingDeviceList) return;
        if (!applyingDeviceDetection) { deviceChoiceChanged = true; deviceDetectionState = "manual"; }
        ClearPackageOptionPreview();
        ClearDeviceSelection(); pendingDeviceDirectory = null;
        if (deviceList!.SelectedItem is ComboBoxItem { Tag: JsonElement item })
        {
            if (IsModelGuide(item)) ShowModelGuide(item); else { ClearModelGuide(); ShowDeviceDetails(item); }
            signedDeviceUse!.Visibility = Visibility.Visible;
        }
        else
        {
            displayedDeviceMetadata = null;
            signedDeviceUse!.Visibility = Visibility.Collapsed;
            ClearModelGuide();
            deviceStatus!.Text = PartitionInventory.L("请选择机型，或拖入外部机型脚本与包。", "Choose a device, or drop an external device script or package.");
        }
        RefreshDeviceDetectionStatus(); RefreshInlineDeviceChoice();
    }

    private IEnumerable<JsonElement> DeviceModelEntries(string? preferredKey)
    {
        var guides = modelCatalog is JsonElement catalog ? catalog.GetProperty("models").EnumerateArray().ToArray() : [];
        if (deviceSourceBundled)
        {
            foreach (var guide in guides.Where(entry => HasBundledModel(entry.GetProperty("id").GetString()!))) yield return guide;
            yield break;
        }
        var packages = lastDeviceIndex is JsonElement index ? index.GetProperty("packages").EnumerateArray().Where(IsAvailableDeviceEntry).ToArray() : [];
        foreach (var model in guides.Where(entry => !HasBundledModel(entry.GetProperty("id").GetString()!)).Concat(packages)
                     .Concat(guides.Where(entry => HasBundledModel(entry.GetProperty("id").GetString()!)))
                     .GroupBy(entry => entry.GetProperty("id").GetString(), StringComparer.Ordinal))
        {
            // Keep a reviewed source only while it is still available; otherwise
            // choose the newest signed package before falling back to the guide.
            var retained = model.FirstOrDefault(entry => !IsModelGuide(entry) && preferredKey != null && DeviceEntryKey(entry) == preferredKey);
            if (retained.ValueKind != JsonValueKind.Undefined) { yield return retained; continue; }
            var candidates = model.Where(entry => !IsModelGuide(entry)).OrderByDescending(
                entry => entry.GetProperty("version").GetString(), DevicePackageVersion.Comparer)
                .ThenBy(entry => DeviceEntryKey(entry), StringComparer.Ordinal).ToArray();
            yield return candidates.Length > 0 ? candidates[0] : model.First();
        }
    }

    private void PopulateDeviceList()
    {
        if (!deviceSourceChosen && !deviceChoiceChanged && selectedDevicePackage == null && !HasPendingDevicePreview)
            deviceSourceBundled = HasBundledDeviceChoices;
        if (!HasBundledDeviceChoices) deviceSourceBundled = false;
        var previous = deviceList!.SelectedItem is ComboBoxItem { Tag: JsonElement selected } ? selected : (JsonElement?)null;
        var old = previous is JsonElement before ? DeviceEntryKey(before) : null;
        var keepSource = previous is JsonElement chosen && (selectedDevicePackage != null
            || (deviceChoiceChanged || deviceActionBusy) && (!IsModelGuide(chosen) || HasBundledModel(chosen.GetProperty("id").GetString()!)));
        updatingDeviceList = true;
        try
        {
            deviceList.Items.Clear();
            deviceList.Items.Add(new ComboBoxItem { Content = new TextBlock { Text = PartitionInventory.L("请选择机型…", "Choose a device…"), TextWrapping = TextWrapping.Wrap } });
            foreach (var entry in DeviceModelEntries(keepSource ? old : null))
                deviceList.Items.Add(new ComboBoxItem { Content = new TextBlock { Text = DeviceLabel(entry) + (deviceSourceBundled ? "" : IsModelGuide(entry)
                    ? PartitionInventory.L(" · 仅适配指南", " · Guide only") : PartitionInventory.L(" · GitHub 机型包", " · GitHub package")), TextWrapping = TextWrapping.Wrap }, Tag = entry.Clone() });
            deviceList.SelectedItem = deviceList.Items.OfType<ComboBoxItem>().FirstOrDefault(item => item.Tag is JsonElement entry && DeviceEntryKey(entry) == old)
                ?? deviceList.Items.OfType<ComboBoxItem>().FirstOrDefault(item => previous is JsonElement prior && item.Tag is JsonElement entry
                    && entry.GetProperty("id").GetString() == prior.GetProperty("id").GetString()) ?? deviceList.Items[0];
        }
        finally { updatingDeviceList = false; }
        var current = deviceList.SelectedItem is ComboBoxItem { Tag: JsonElement after } ? DeviceEntryKey(after) : null;
        if (old != null && old != current)
            DeviceListSelectionChanged();
        ((ModelSearchComboBox)deviceList).RefreshSearch();
        RefreshInlineDeviceChoice();
        QueueCustomBuildRefresh();
        // A refresh may revoke a package that was selected before the request.
        if (selectedDeviceTrust == "signed-index" && lastDeviceIndex is JsonElement latest
            && !latest.GetProperty("packages").EnumerateArray().Any(entry => old != null && DeviceEntryKey(entry) == old
                && entry.GetProperty("sha256").GetString() == selectedDeviceDigest && IsAvailableDeviceEntry(entry)))
        {
            ClearDeviceSelection();
            signedDeviceUse!.Visibility = Visibility.Collapsed;
            deviceStatus!.Text = PartitionInventory.L("此前选择的机型包已从可用目录移除，请重新选择。", "The previously selected package is no longer available. Select another device.");
        }
        RefreshDeviceSourcePresentation();
    }

    private void DetectDeviceModel(bool explicitRequest)
    {
        if (deviceActionBusy || operation != null || DownloadsBusy || deviceList == null) return;
        if (!explicitRequest && (deviceChoiceChanged || HasPendingDevicePreview || selectedDevicePackage != null
            || step != AdaptationStep || adaptationScreen != AdaptationScreen.Choice)) return;
        detectedComputer = DeviceHardwareReaderForChecks();
        if (detectedComputer == null) deviceDetectionState = "unavailable";
        else if (lastDeviceIndex == null && modelCatalog == null) deviceDetectionState = "pending";
        else
        {
            var matches = deviceList.Items.OfType<ComboBoxItem>().Where(item => item.Tag is JsonElement entry && BoilerHardware.Matches(entry, detectedComputer))
                .GroupBy(item => ((JsonElement)item.Tag).GetProperty("id").GetString()).Select(group => group.First()).ToArray();
            deviceDetectionState = matches.Length == 1 ? "matched" : matches.Length > 1 ? "ambiguous" : "unmatched";
            if (matches.Length == 1)
            {
                applyingDeviceDetection = true;
                try { deviceList.SelectedItem = matches[0]; }
                finally { applyingDeviceDetection = false; }
            }
        }
        // Detection is a suggestion; signed package validation and explicit use still apply.
        if (explicitRequest) deviceChoiceChanged = lastDeviceIndex != null || modelCatalog != null;
        RefreshDeviceDetectionStatus(); RefreshInlineDeviceChoice();
    }

    private void RefreshDeviceDetectionStatus()
    {
        if (deviceDetectionStatus == null) return;
        var computer = detectedComputer == null ? "" : detectedComputer.Manufacturer + " / " + detectedComputer.Model + "\n";
        var status = deviceDetectionState switch
        {
            "matched" => PartitionInventory.L("已匹配目录机型，请确认后使用所选机型包。", "Matching device found. Review it, then use the selected package."),
            "ambiguous" => PartitionInventory.L("有多个匹配机型包，请在下拉框中选择。", "Several device packages match. Choose one from the list."),
            "unmatched" => PartitionInventory.L("目录中没有匹配机型，可手动选择、导入本地包或使用通用安装。", "No matching model in the directory. Choose manually, import a local package, or use generic installation."),
            "unavailable" => PartitionInventory.L("无法读取本机品牌与型号，请手动选择或导入本地包。", "Could not read this computer's manufacturer and model. Choose manually or import a local package."),
            "manual" => PartitionInventory.L("已保留手动选择。自动识别只针对当前运行安装器的电脑。", "Manual choice kept. Detection applies to the computer running this installer."),
            _ => PartitionInventory.L("目录载入后按本机品牌与型号匹配；为其他设备安装时请手动选择。", "Matches this computer's manufacturer and model when the directory loads. Choose manually for another target device.")
        };
        deviceDetectionStatus.Text = computer + status;
    }

    private async Task SelectSignedDeviceAsync()
    {
        if (deviceList!.SelectedItem is not ComboBoxItem { Tag: JsonElement item }) throw new IOException(PartitionInventory.L("请选择机型。", "Select a device."));
        var id = item.GetProperty("id").GetString()!;
        var digest = item.GetProperty("sha256").GetString()!;
        var current = await BoilerProtocol.RunAsync(["cached-index", BoilerProtocol.RegistryCache], CancellationToken.None);
        if (!current.GetProperty("packages").EnumerateArray().Any(p => p.GetProperty("id").GetString() == id && p.GetProperty("sha256").GetString() == digest)
            || current.GetProperty("revocations").EnumerateArray().Any(p => p.GetString() == digest)) throw new IOException("Selected package is no longer approved.");
        reviewedDeviceMetadata = item.Clone();
        var folder = Path.Combine(BoilerProtocol.PackageRoot, digest[..24]);
        var validCache = false;
        if (Directory.Exists(folder))
        {
            try { await BoilerProtocol.RunAsync(["package", folder, "--manifest-sha256", digest], CancellationToken.None); validCache = true; }
            catch (BoilerProtocolFailure) { /* Download a clean copy before quarantining broken cache. */ }
        }
        if (!validCache)
        {
            folder = Path.Combine(BoilerProtocol.PackageRoot, Guid.NewGuid().ToString("N"));
            deviceStatus!.Text = PartitionInventory.L("正在下载并校验机型包…", "Downloading and verifying the device package…");
            await BoilerProtocol.RunAsync(["fetch-package", BoilerProtocol.RegistryCache, id, item.GetProperty("version").GetString()!, folder], CancellationToken.None);
        }
        await SelectDeviceDirectory(folder, "signed-index", digest);
    }

    internal static string BrandModelLabel(JsonElement item) => PreviewText(item, "name", item.GetProperty("id").GetString() ?? "");

    internal static string BrandPendingDescription(JsonElement model)
    {
        if (!model.TryGetProperty("pending", out var pending) || pending.ValueKind != JsonValueKind.Array || pending.GetArrayLength() == 0)
            return "";
        var lines = new List<string> { PartitionInventory.L(
            "以下为新资源和额外功能的状态；已有适配仍可使用：",
            "Status of new resources and additional features; existing adaptations remain available:") };
        foreach (var item in pending.EnumerateArray())
        {
            var id = item.TryGetProperty("resource", out var resource) ? resource.GetString()
                : item.TryGetProperty("component", out var component) ? component.GetString() : null;
            if (string.IsNullOrWhiteSpace(id)) continue;
            var name = id switch
            {
                "resource:intel-be201-firmware" => PartitionInventory.L("Intel BE201 固件", "Intel BE201 firmware"),
                "resource:intel-g3e-public-cpu-controls" => PartitionInventory.L("Intel CPU 控制", "Intel CPU controls"),
                "resource:gamma22-hdr-game" => PartitionInventory.L("游戏 HDR", "Game HDR"),
                "resource:onexplayer-battery-common" => PartitionInventory.L("电池功能", "Battery support"),
                "resource:onexplayer-battery-charge-backend" => PartitionInventory.L("电池充电控制", "Battery charge control"),
                "resource:common-charge-compatibility-guard" => PartitionInventory.L("充电兼容保护", "Charging compatibility guard"),
                "resource:onexplayer-light" => PartitionInventory.L("灯效", "Lighting"),
                "oxp3:legacy-adaptation" => PartitionInventory.L("旧机型适配", "Legacy model adaptation"),
                "base-system" => PartitionInventory.L("系统镜像", "System image"),
                "pl4-175w" => "PL4 175 W",
                "lsfg-vk" => "LSFG-VK",
                _ => id
            };
            var status = item.TryGetProperty("status", out var value) && value.ValueKind == JsonValueKind.String ? value.GetString() : null;
            var description = status switch
            {
                "external_boiler_core" => PartitionInventory.L("在安装器中单独选择", "selected separately in the installer"),
                "external_plugin" => PartitionInventory.L("需外部插件，未随包提供", "external plugin, not included"),
                "linux-lifecycle-not-verified" => PartitionInventory.L("安装和维护仍待验证", "installation and maintenance not yet verified"),
                "implementation_missing" => PartitionInventory.L("尚未实现", "not implemented"),
                _ => PartitionInventory.L("新资源尚未接入", "new resource not integrated")
            };
            lines.Add("• " + name + " — " + description);
        }
        return lines.Count == 1 ? "" : string.Join("\n", lines);
    }

    internal Func<JsonElement, JsonElement?>? BrandModelChooserForChecks { get; set; }
    internal Action<string>? BrandStageForChecks { get; set; }

    private JsonElement? ChooseBrandModel(JsonElement models)
    {
        if (BrandModelChooserForChecks != null) return BrandModelChooserForChecks(models);
        var choices = models.EnumerateArray().Select(item => item.Clone()).ToArray();
        if (choices.Length == 0) throw new IOException(PartitionInventory.L("品牌包没有机型。", "The brand package has no models."));
        var dialog = new Window
        {
            Owner = this, WindowStartupLocation = WindowStartupLocation.CenterOwner,
            Title = PartitionInventory.L("选择机型", "Choose a model"),
            SizeToContent = SizeToContent.WidthAndHeight, MinWidth = 390, ResizeMode = ResizeMode.NoResize
        };
        var body = new StackPanel { Margin = new Thickness(20) };
        body.Children.Add(new TextBlock
        {
            Text = PartitionInventory.L("选择要从品牌包使用的机型：", "Choose the model to use from this brand package:"),
            TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 12)
        });
        var list = new ComboBox { MinHeight = 38, MinWidth = 350, SelectedIndex = -1 };
        foreach (var item in choices)
            list.Items.Add(new ComboBoxItem { Content = BrandModelLabel(item), Tag = item });
        body.Children.Add(list);
        var pendingNotice = new TextBlock { TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 12, 0, 0) };
        body.Children.Add(new ScrollViewer { MaxHeight = 180, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Content = pendingNotice });
        var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right,
            Margin = new Thickness(0, 16, 0, 0) };
        var cancel = new Button { Content = PartitionInventory.L("取消", "Cancel"), MinWidth = 88, Margin = new Thickness(0, 0, 8, 0), IsCancel = true };
        var select = new Button { Content = PartitionInventory.L("使用", "Use"), MinWidth = 88, IsDefault = true, IsEnabled = false };
        list.SelectionChanged += (_, _) =>
        {
            select.IsEnabled = list.SelectedItem != null;
            pendingNotice.Text = list.SelectedItem is ComboBoxItem { Tag: JsonElement selected } ? BrandPendingDescription(selected) : "";
        };
        select.Click += (_, _) => dialog.DialogResult = true;
        buttons.Children.Add(cancel); buttons.Children.Add(select); body.Children.Add(buttons);
        dialog.Content = body;
        if (dialog.ShowDialog() != true) return null;
        return list.SelectedItem is ComboBoxItem { Tag: JsonElement selected } ? selected.Clone() : null;
    }

    private async Task ImportDeviceInputAsync(string path)
    {
        if (installMode != "custom" || homeScreen != null || operation != null || DownloadsBusy) return;
        deviceChoiceChanged = true; deviceDetectionState = "manual";
        SwitchDeviceSource(false);
        await DeviceAction(async () =>
        {
            var destination = Path.Combine(InstallerWorkspace.DirectoryPath, "device-imports", Guid.NewGuid().ToString("N"));
            var stage = Path.Combine(InstallerWorkspace.DirectoryPath, "device-imports", Guid.NewGuid().ToString("N"));
            if (Directory.Exists(stage) || File.Exists(stage)) throw new IOException("Brand staging path already exists.");
            BrandStageForChecks?.Invoke(stage);
            var previousStatus = deviceStatus!.Text;
            try
            {
                deviceStatus.Text = PartitionInventory.L("正在识别并校验外部机型内容…", "Inspecting and verifying the external device content…");
                var brand = await BoilerProtocol.RunAsync(["stage-brand", path, stage, "--developer"], CancellationToken.None);
                JsonElement? selectedBrand = null;
                if (brand.GetProperty("recognized").GetBoolean())
                {
                    if (brand.GetProperty("unresolved").GetArrayLength() != 0)
                        throw new IOException(PartitionInventory.L("品牌包缺少必要内容，暂时无法使用。", "The brand package is missing required content."));
                    selectedBrand = ChooseBrandModel(brand.GetProperty("models"));
                    if (selectedBrand == null) { deviceStatus.Text = previousStatus; return; }
                }

                adaptationBranch = "device"; adaptationScreen = AdaptationScreen.Device;
                ClearPackageOptionPreview(); ShowStep(AdaptationStep);
                ClearDeviceSelection(); pendingDeviceDirectory = null;
                signedDeviceUse!.Visibility = Visibility.Collapsed;
                updatingDeviceList = true; deviceList!.SelectedIndex = 0; updatingDeviceList = false;
                ClearModelGuide();
                JsonElement result;
                if (selectedBrand is JsonElement selected)
                    result = await BoilerProtocol.RunAsync(["import-brand", brand.GetProperty("directory").GetString()!, destination,
                        selected.GetProperty("id").GetString()!, selected.GetProperty("manifest_sha256").GetString()!,
                        "--developer"], CancellationToken.None);
                else result = await BoilerProtocol.RunAsync(["import-device", path, destination, "--developer"], CancellationToken.None);
                var package = result.GetProperty("package");
                if (package.TryGetProperty("purpose", out var purpose) && purpose.GetString() == "maintenance")
                    throw new IOException(PartitionInventory.L("这是维护专用包，请使用维护器导入。", "This is a maintenance package. Import it with the maintainer."));
                reviewedDeviceMetadata = null;
                var selection = await PrepareDeviceSelectionAsync(result.GetProperty("directory").GetString()!,
                    "developer-session", result.GetProperty("manifest_sha256").GetString(), CancellationToken.None, sessionTrusted: true);
                importedDeviceFileName = Path.GetFileName(path.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar));
                CommitDeviceSelection(selection);
            }
            finally
            {
                // Only the fresh, GUID-named brand staging tree is removed. The
                // original archive, selected package and its cache are separate.
                if (Directory.Exists(stage)) Directory.Delete(stage, recursive: true);
            }
        });
    }

    private void RefreshDeviceLanguage()
    {
        if (devicePanel == null) return;
        foreach (var refresh in deviceLanguageUpdates) refresh();
        AutomationProperties.SetName(deviceList!, PartitionInventory.L("目标机型", "Target device"));
        PopulateDeviceList(); UpdateDeviceIndexStatus();
        RefreshModelCatalogStatus();
        RefreshDeviceDetectionStatus();
        if (displayedDeviceMetadata is JsonElement metadata)
        {
            if (IsModelGuide(metadata)) ShowModelGuide(metadata); else ShowDeviceDetails(metadata);
            if (selectedDeviceTrust != null) deviceStatus!.Text += "\n" + PartitionInventory.L("已加入安装配置。可在安装选项中调整。", "Added to the installation. Adjust it in Installation options.");
        }
        else deviceStatus!.Text = PartitionInventory.L("请选择机型，或拖入外部机型脚本与包。", "Choose a device, or drop an external device script or package.");
    }
}


internal static class DevicePackageVersion
{
    private sealed record Part(bool Numeric, BigInteger Number, string Text);
    private sealed record Key(BigInteger[] Numbers, bool Stable, Part[] Pre, BigInteger? Maintenance);
    private static readonly Regex Pattern = new(@"^([0-9]+(?:\.[0-9]+){1,3})(?:-([A-Za-z0-9.-]+))?(?:\+([A-Za-z0-9.-]+))?$", RegexOptions.CultureInvariant);
    private static readonly Regex EnginePattern = new(@"^ENGINE_VERSION = ""([0-9]+\.[0-9]+\.[0-9]+)""$", RegexOptions.Multiline | RegexOptions.CultureInvariant);
    private static readonly string Engine = LoadEngine();
    internal static readonly IComparer<string?> Comparer = System.Collections.Generic.Comparer<string?>.Create(Compare);
    private static string LoadEngine()
    {
        using var stream = typeof(DevicePackageVersion).Assembly.GetManifestResourceStream("OxpInstaller.ProtocolManifest")
            ?? throw new InvalidOperationException("Embedded protocol version is missing.");
        using var reader = new StreamReader(stream);
        var match = EnginePattern.Match(reader.ReadToEnd().Replace("\r\n", "\n"));
        if (!match.Success) throw new InvalidOperationException("Embedded protocol version is invalid.");
        return match.Groups[1].Value;
    }
    internal static bool EngineSupports(string? minimum)
    {
        if (minimum == null || !Regex.IsMatch(minimum, @"^[0-9]+\.[0-9]+\.[0-9]+$", RegexOptions.CultureInvariant)) return false;
        var requested = minimum.Split('.').Select(BigInteger.Parse).ToArray();
        var current = Engine.Split('.').Select(BigInteger.Parse).ToArray();
        for (var i = 0; i < 3; i++) { var c = requested[i].CompareTo(current[i]); if (c != 0) return c < 0; }
        return true;
    }
    private static Key? Parse(string? value)
    {
        if (value == null) return null;
        var match = Pattern.Match(value); if (!match.Success) return null;
        var nums = match.Groups[1].Value.Split('.').Select(BigInteger.Parse).Concat(Enumerable.Repeat(BigInteger.Zero, 4)).Take(4).ToArray();
        var pre = match.Groups[2].Success ? match.Groups[2].Value.Split('.').Select(part =>
            Regex.IsMatch(part, @"^[0-9]+$", RegexOptions.CultureInvariant)
                ? new Part(true, BigInteger.Parse(part), "") : new Part(false, BigInteger.Zero, part)).ToArray() : [];
        var revision = Regex.Match(match.Groups[3].Value, @"^maintenance\.([0-9]+)$", RegexOptions.CultureInvariant);
        return new Key(nums, !match.Groups[2].Success, pre, revision.Success ? BigInteger.Parse(revision.Groups[1].Value) : null);
    }
    internal static bool TryParse(string? value, out bool valid) { valid = Parse(value) != null; return valid; }
    private static int Compare(string? left, string? right)
    {
        var a = Parse(left) ?? throw new InvalidOperationException("Unsupported package version.");
        var b = Parse(right) ?? throw new InvalidOperationException("Unsupported package version.");
        for (var i = 0; i < 4; i++) { var c = a.Numbers[i].CompareTo(b.Numbers[i]); if (c != 0) return c; }
        if (a.Stable != b.Stable) return a.Stable ? 1 : -1;
        for (var i = 0; i < Math.Min(a.Pre.Length, b.Pre.Length); i++)
        {
            var x = a.Pre[i]; var y = b.Pre[i];
            if (x.Numeric != y.Numeric) return x.Numeric ? -1 : 1;
            var c = x.Numeric ? x.Number.CompareTo(y.Number) : StringComparer.Ordinal.Compare(x.Text, y.Text);
            if (c != 0) return c;
        }
        if (a.Pre.Length != b.Pre.Length) return a.Pre.Length.CompareTo(b.Pre.Length);
        if (a.Maintenance.HasValue != b.Maintenance.HasValue) return a.Maintenance.HasValue ? 1 : -1;
        return a.Maintenance.HasValue ? a.Maintenance.Value.CompareTo(b.Maintenance!.Value) : 0;
    }
}
