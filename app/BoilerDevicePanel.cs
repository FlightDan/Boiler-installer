using System.IO;
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
    private Button? externalDeviceUse;
    private Button? deviceDropTarget;
    private string? pendingDeviceDirectory;
    private string? deviceIndexError;
    private bool deviceIndexOffline;
    private bool updatingDeviceList;
    private int devicePanelGeneration;
    private readonly List<Action> deviceLanguageUpdates = new();
    private StackPanel? deviceQuickPicker;
    private TextBlock? deviceDetectionStatus;
    private BoilerHardware.Identity? detectedComputer;
    private bool deviceChoiceChanged, applyingDeviceDetection;
    private string deviceDetectionState = "pending";
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
        var heading = Text("目标机型", "Target device", 19); heading.FontWeight = FontWeights.SemiBold;
        picker.Children.Add(heading);
        var actions = new WrapPanel { Margin = new(0, 6, 0, 2) };
        var detect = Button("DetectDeviceModel", "自动识别本机机型", "Detect this computer");
        detect.Click += (_, _) => DetectDeviceModel(explicitRequest: true);
        var browse = Button("BrowseLocalDevice", "选择本地机型包…", "Choose a local package…");
        browse.Click += async (_, _) =>
        {
            if (operation != null || DownloadsBusy || deviceActionBusy) return;
            var dialog = new OpenFileDialog { Title = PartitionInventory.L("选择本地机型包", "Choose a local device package"),
                Filter = PartitionInventory.L("机型包与脚本|*.zip;*.tar;*.tar.gz;*.tgz;*.tar.xz;*.txz;*.sh|所有文件|*.*", "Device packages and scripts|*.zip;*.tar;*.tar.gz;*.tgz;*.tar.xz;*.txz;*.sh|All files|*.*"), CheckFileExists = true };
            if (dialog.ShowDialog(this) != true) return;
            if (installMode == "vanilla") PromoteDeviceMode();
            await ImportDeviceInputAsync(dialog.FileName);
        };
        actions.Children.Add(detect); actions.Children.Add(browse); picker.Children.Add(actions);
        deviceDetectionStatus = new TextBlock { TextWrapping = TextWrapping.Wrap, Foreground = BoilerBrush("#485B73"), Margin = new(0, 2, 0, 6) };
        picker.Children.Add(deviceDetectionStatus);
        deviceIndexStatus = new TextBlock { TextWrapping = TextWrapping.Wrap, Foreground = BoilerBrush("#485B73"), Margin = new Thickness(0, 6, 0, 4) };
        picker.Children.Add(deviceIndexStatus);
        deviceIndexRetry = Button("RetryDeviceIndex", "重新读取目录", "Retry device directory");
        deviceIndexRetry.Visibility = Visibility.Collapsed;
        deviceIndexRetry.Click += async (_, _) => await Task.WhenAll(LoadDeviceIndexAsync(generation, token), LoadModelCatalogAsync(generation, token));
        picker.Children.Add(deviceIndexRetry);
        deviceList = new ModelSearchComboBox { DeviceModels = true, SearchText = DeviceSearchText,
            Name = "DeviceList", MinHeight = 48, Margin = new Thickness(0, 8, 0, 4), HorizontalContentAlignment = HorizontalAlignment.Stretch };
        AutomationProperties.SetName(deviceList, PartitionInventory.L("目标机型", "Target device"));
        deviceList.SelectionChanged += (_, _) => DeviceListSelectionChanged();
        picker.Children.Insert(1, deviceList);
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

        var trustLabel = Text("我已了解来源，本次信任此外部机型脚本", "I trust this external device script for this session");
        developerPackages = new CheckBox { Name = "TrustExternalDevice", Content = trustLabel, MinHeight = 48, Visibility = Visibility.Collapsed };
        developerPackages.Checked += (_, _) => externalDeviceUse!.IsEnabled = pendingDeviceDirectory != null;
        developerPackages.Unchecked += (_, _) =>
        {
            externalDeviceUse!.IsEnabled = false;
            if (selectedDeviceTrust == "developer-session")
            {
                ClearDeviceSelection();
                deviceStatus.Text = PartitionInventory.L("已取消本次信任，请重新选用机型包。", "Session trust removed. Select a device package to continue.");
            }
        };
        panel.Children.Add(developerPackages);
        externalDeviceUse = Button("UseExternalDevice", "使用此外部机型包", "Use this external device package");
        externalDeviceUse.Visibility = Visibility.Collapsed; externalDeviceUse.IsEnabled = false;
        externalDeviceUse.Click += async (_, _) => await DeviceAction(async () =>
        {
            if (pendingDeviceDirectory == null) throw new IOException("Import a device package first.");
            reviewedDeviceMetadata = null;
            await SelectDeviceDirectory(pendingDeviceDirectory, "developer-session", null);
        });
        panel.Children.Add(externalDeviceUse);
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
                : await BoilerProtocol.RunAsync(["load-index", "https://github.com/FlightDan/Boiler-installer/releases/latest/download/boiler-device-index.json", "https://github.com/FlightDan/Boiler-installer/releases/latest/download/boiler-device-index.sig", BoilerProtocol.RegistryCache], token);
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
        deviceIndexRetry!.Visibility = deviceIndexError != null || deviceIndexOffline ? Visibility.Visible : Visibility.Collapsed;
        if (loading)
        {
            deviceIndexStatus!.Text = PartitionInventory.L("正在读取 GitHub 机型目录…", "Loading the GitHub device directory…"); return;
        }
        if (deviceIndexError != null)
        {
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
        && !index.GetProperty("revocations").EnumerateArray().Any(value => value.GetString() == entry.GetProperty("sha256").GetString());

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
        developerPackages!.IsChecked = false; developerPackages.Visibility = Visibility.Collapsed;
        externalDeviceUse!.Visibility = Visibility.Collapsed;
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
        var packages = lastDeviceIndex is JsonElement index ? index.GetProperty("packages").EnumerateArray().Where(IsAvailableDeviceEntry).ToArray() : [];
        foreach (var model in guides.Concat(packages).GroupBy(entry => entry.GetProperty("id").GetString(), StringComparer.Ordinal))
        {
            // One model can have both a guide and a package. Keep an explicit
            // choice during refresh; otherwise prefer this build's bundled payload.
            yield return model.OrderBy(entry => DeviceEntryKey(entry) == preferredKey ? 0
                : IsModelGuide(entry) && HasBundledModel(model.Key!) ? 1 : !IsModelGuide(entry) ? 2 : 3).First();
        }
    }

    private void PopulateDeviceList()
    {
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
                deviceList.Items.Add(new ComboBoxItem { Content = new TextBlock { Text = DeviceLabel(entry), TextWrapping = TextWrapping.Wrap }, Tag = entry.Clone() });
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
        // A refresh may revoke a package that was selected before the request.
        if (selectedDeviceTrust == "signed-index" && lastDeviceIndex is JsonElement latest
            && !latest.GetProperty("packages").EnumerateArray().Any(entry => entry.GetProperty("sha256").GetString() == selectedDeviceDigest && IsAvailableDeviceEntry(entry)))
        {
            ClearDeviceSelection();
            signedDeviceUse!.Visibility = Visibility.Collapsed;
            deviceStatus!.Text = PartitionInventory.L("此前选择的机型包已从可用目录移除，请重新选择。", "The previously selected package is no longer available. Select another device.");
        }
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

    private async Task ImportDeviceInputAsync(string path)
    {
        if (installMode != "custom" || homeScreen != null || operation != null || DownloadsBusy) return;
        deviceChoiceChanged = true; deviceDetectionState = "manual";
        await DeviceAction(async () =>
        {
            adaptationBranch = "device"; adaptationScreen = AdaptationScreen.Device;
            ClearPackageOptionPreview(); ShowStep(AdaptationStep);
            ClearDeviceSelection(); pendingDeviceDirectory = null;
            developerPackages!.IsChecked = false; developerPackages.Visibility = Visibility.Collapsed;
            externalDeviceUse!.Visibility = signedDeviceUse!.Visibility = Visibility.Collapsed;
            updatingDeviceList = true; deviceList!.SelectedIndex = 0; updatingDeviceList = false;
            ClearModelGuide();
            deviceStatus!.Text = PartitionInventory.L("正在识别并校验外部机型内容…", "Inspecting and verifying the external device content…");
            var destination = Path.Combine(InstallerWorkspace.DirectoryPath, "device-imports", Guid.NewGuid().ToString("N"));
            var result = await BoilerProtocol.RunAsync(["import-device", path, destination, "--developer"], CancellationToken.None);
            var package = result.GetProperty("package");
            if (package.TryGetProperty("purpose", out var purpose) && purpose.GetString() == "maintenance")
                throw new IOException(PartitionInventory.L("这是维护专用包，请使用维护器导入。", "This is a maintenance package. Import it with the maintainer."));
            pendingDeviceDirectory = result.GetProperty("directory").GetString();
            ShowDeviceDetails(package);
            ShowPackageOptionPreview(package, PreviewText(package, "name", package.GetProperty("id").GetString()!), path, fromInputs: false);
            deviceStatus.Text += "\n" + PartitionInventory.L("已识别并校验；尚未加入安装方案。确认来源后勾选本次信任。", "Recognized and verified; not yet added to the installation. Confirm its source and grant session trust.");
            developerPackages.Visibility = externalDeviceUse.Visibility = Visibility.Visible;
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
            if (pendingDeviceDirectory != null) deviceStatus!.Text += "\n" + PartitionInventory.L("已识别并校验；尚未加入安装方案。确认来源后勾选本次信任。", "Recognized and verified; not yet added to the installation. Confirm its source and grant session trust.");
            else if (selectedDeviceTrust != null) deviceStatus!.Text += "\n" + (selectedDeviceTrust == "signed-index" ? PartitionInventory.L("来源：已验签目录中的固定版本", "Source: fixed version from the verified index") : PartitionInventory.L("来源：仅本次信任的开发包", "Source: development package trusted for this session"));
        }
        else deviceStatus!.Text = PartitionInventory.L("请选择机型，或拖入外部机型脚本与包。", "Choose a device, or drop an external device script or package.");
    }
}
