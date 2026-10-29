using System.IO;
using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace OxpInstaller;

public sealed record SelectedOptions(int Schema, string[] Repairs, string[] Plugins, string InstallerLanguage, string KdeLanguage,
    bool SetDeckPassword = false, bool EnableSsh = false,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] StorageMount[]? StorageMounts = null,
    string InstallMode = "custom", string? DevicePackage = null, string[]? Wireless = null,
    string? DeviceManifestSha256 = null, string? DeviceTrust = null, string[]? DeviceComponents = null,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] string? GraphicsProfile = null,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] DeviceLegacySelection? DeviceLegacyOptions = null);
public sealed record DeviceLegacySelection(int SchemaVersion, string[] Repairs, string[] Plugins, bool AcknowledgeLegacyUpdateHold);
public sealed record OptionItem(string Id, string Zh, string En, bool Default, string[] Dependencies,
    bool Available = true, string? UnavailableZh = null, string? UnavailableEn = null);
public sealed record OptionCatalog(int Schema, string[] Languages, string[] KdeLanguages, OptionItem[] Repairs, OptionItem[] Plugins);

public static class InstallerOptions
{
    internal static readonly HashSet<string> WirelessComponents = new(
        from transport in new[] { "wifi", "bluetooth" }
        from vendor in new[] { "intel", "realtek", "mediatek" }
        select transport + ":" + vendor);
    internal static bool AllowsWireless(string mode, bool hasPackage) => !hasPackage && mode is "custom" or "vanilla";
    public static OptionCatalog Catalog => JsonSerializer.Deserialize<OptionCatalog>(
        File.ReadAllText(Path.Combine(Backend.Root, "components/options.json")),
        new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower, PropertyNameCaseInsensitive = true })
        ?? throw new IOException("Missing installer option catalog.");
    public static bool IsDefault(OptionItem item) => item.Available && (item.Id == "flathub-ustc" ? InstallerI18n.Language == "zh-CN" : item.Default);
    internal static string KdeLanguageForWindows(string cultureName)
    {
        var name = cultureName.Replace('_', '-').ToLowerInvariant();
        if (name == "zh" || name.StartsWith("zh-", StringComparison.Ordinal))
            return name.Contains("hant", StringComparison.Ordinal) || name is "zh-tw" or "zh-hk" or "zh-mo" ? "zh_TW" : "zh_CN";
        return name.Split('-')[0] switch
        {
            "en" => "en_US", "ja" => "ja_JP", "ko" => "ko_KR", "de" => "de_DE",
            "fr" => "fr_FR", "es" => "es_ES", "ru" => "ru_RU", _ => "system"
        };
    }
    public static string DefaultKdeLanguage => KdeLanguageForWindows(CultureInfo.CurrentUICulture.Name);
    public static SelectedOptions Defaults => new(1, Catalog.Repairs.Where(IsDefault).Select(x => x.Id).ToArray(),
        Catalog.Plugins.Where(IsDefault).Select(x => x.Id).ToArray(), InstallerI18n.Language, DefaultKdeLanguage);
    public static void Validate(SelectedOptions value)
    {
        if (value.DeviceLegacyOptions is { } legacy)
        {
            if (value.DevicePackage == null || value.InstallMode != "custom" || legacy.SchemaVersion != 1
                || legacy.Repairs == null || legacy.Plugins == null || legacy.Repairs.Length + legacy.Plugins.Length == 0
                || !legacy.Repairs.Concat(legacy.Plugins).All(id => id.StartsWith("oxp3:", StringComparison.Ordinal))
                || !(value.DeviceComponents ?? []).Contains("oxp3:legacy-adaptation"))
                throw new IOException("Invalid device legacy selections.");
            if (!legacy.AcknowledgeLegacyUpdateHold) throw new IOException(PartitionInventory.L(
                "为保留适配，请勾选暂停安装后进入系统时的首次更新。", "Select the first system update pause to preserve device support."));
        }
        if (value.Repairs == null || value.Plugins == null) throw new IOException("Missing component selections.");
        if (value.InstallMode is not ("custom" or "vanilla" or "imported" or "local")) throw new IOException("Unknown Boiler installation mode.");
        if (value.InstallMode is "imported" or "local" && (value.Repairs.Length != 0 || value.Plugins.Length != 0 || value.DevicePackage != null || value.DeviceComponents is { Length: > 0 } || value.Wireless is { Length: > 0 } || value.SetDeckPassword || value.EnableSsh || value.KdeLanguage != "system"))
            throw new IOException("Imported and local image installation cannot inject component selections.");
        if (value.InstallMode != "custom" && value.DevicePackage != null) throw new IOException("Device packages require custom mode.");
        if (value.StorageMounts is { Length: > 0 }) throw new IOException("Unsupported generic storage policy.");
        if (value.DeviceComponents is { Length: > 0 } && value.DevicePackage == null) throw new IOException("Device components require a package.");
        if (value.DevicePackage != null && (value.DeviceManifestSha256 == null
            || !System.Text.RegularExpressions.Regex.IsMatch(value.DeviceManifestSha256, "^[0-9a-f]{64}$")
            || value.DevicePackage != value.DeviceManifestSha256[..24]
            || value.DeviceTrust is not ("signed-index" or "developer-session"))) throw new IOException("Invalid locked device package identity.");
        if ((value.Wireless ?? []).Any(vendor => !WirelessComponents.Contains(vendor) && vendor is not ("intel" or "realtek" or "mediatek"))) throw new IOException("Unknown wireless firmware selection.");
        if (!AllowsWireless(value.InstallMode, value.DevicePackage != null)
            && (value.Repairs.Any(WirelessComponents.Contains) || value.Wireless is { Length: > 0 }))
            throw new IOException("Wireless firmware selections require generic installation without a device package.");
        if (value.GraphicsProfile != null && (!AllowsWireless(value.InstallMode, value.DevicePackage != null)
            || !GraphicsCatalogue.Read().Any(item => item.Id == value.GraphicsProfile)))
            throw new IOException("Graphics firmware requires a known profile and generic installation without a device package.");
        SharedStorage.Validate(value.StorageMounts);
        var catalog = Catalog;
        if (value.Schema != 1 || !catalog.Languages.Contains(value.InstallerLanguage) || !catalog.KdeLanguages.Contains(value.KdeLanguage)
            || value.Repairs == null || value.Plugins == null || value.Repairs.Distinct().Count() != value.Repairs.Length
            || value.Plugins.Distinct().Count() != value.Plugins.Length
            || value.Repairs.Except(catalog.Repairs.Select(x => x.Id)).Any() || value.Plugins.Except(catalog.Plugins.Select(x => x.Id)).Any())
            throw new IOException(InstallerI18n.T("安装选项或语言无效。"));
        var selected = value.Repairs.Concat(value.Plugins).ToHashSet();
        foreach (var item in catalog.Repairs.Concat(catalog.Plugins).Where(x => selected.Contains(x.Id)))
        {
            if (!item.Available) throw new IOException(InstallerI18n.Language == "en-US"
                ? item.UnavailableEn ?? $"{item.En} is not available for installation."
                : item.UnavailableZh ?? $"{item.Zh}暂不可安装。");
            foreach (var dependency in item.Dependencies)
                if (!selected.Contains(dependency)) throw new IOException(InstallerI18n.Language == "en-US"
                    ? $"{item.En} requires {catalog.Repairs.Concat(catalog.Plugins).Single(x => x.Id == dependency).En}. Enable the dependency or disable this item."
                    : $"{item.Zh}需要{catalog.Repairs.Concat(catalog.Plugins).Single(x => x.Id == dependency).Zh}；请开启依赖或关闭该项。");
        }
    }
    internal static void ValidateDeploymentPolicy(SelectedOptions value)
    {
        if (value.GraphicsProfile != null && !File.Exists(Path.Combine(Backend.Root, "components/graphics-firmware.json")))
            throw new IOException(PartitionInventory.L("缺少核显候选清单，请重新准备组件元数据。", "Graphics candidate inventory is missing. Prepare component metadata again."));
    }
}

public sealed partial class InstallerOptionsPanel : StackPanel
{
    private readonly Dictionary<string, CheckBox> boxes = new();
    private readonly ComboBox kde = new() { MinWidth = 210, Margin = new Thickness(0, 6, 0, 14) };
    private readonly CheckBox setDeckPassword = new() { Margin = new Thickness(0, 0, 0, 8) };
    private readonly CheckBox enableSsh = new() { Margin = new Thickness(0, 8, 0, 4) };
    private readonly PasswordBox deckPassword = new() { MaxLength = 128, Margin = new Thickness(0, 2, 0, 8), Padding = new Thickness(8, 5, 8, 5) };
    private readonly PasswordBox deckPasswordConfirm = new() { MaxLength = 128, Margin = new Thickness(0, 2, 0, 8), Padding = new Thickness(8, 5, 8, 5) };
    private readonly TextBlock deckPasswordLabel = new() { TextWrapping = TextWrapping.Wrap };
    private readonly TextBlock deckPasswordConfirmLabel = new() { TextWrapping = TextWrapping.Wrap };
    private readonly TextBlock accessNote = new() { TextWrapping = TextWrapping.Wrap, Foreground = Brushes.SlateGray, Margin = new Thickness(0, 2, 0, 4) };
    private readonly List<(TextBlock Block, string Zh, string En)> labels = new();
    private readonly OptionCatalog catalog = InstallerOptions.Catalog;
    private readonly TextBlock repairHeading = new() { FontSize = 17, FontWeight = FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap };
    private readonly Button detectWireless = new() { Name = "DetectWirelessHardware", MinHeight = 44, Padding = new Thickness(12, 6, 12, 6), HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(0, 0, 0, 8) };
    private readonly TextBlock detectionStatus = new() { TextWrapping = TextWrapping.Wrap, Foreground = Brushes.SlateGray, Margin = new Thickness(0, 0, 0, 10) };
    private BoilerWirelessHardware.Detection? detection;
    private bool detectionFailed;
    private bool detectionBusy;
    private bool detectionSuperseded;
    private bool detectionCancelled;
    private int wirelessContextGeneration;
    private int wirelessSelectionGeneration;
    private int detectionRequest;
    private bool updatingRepairs;
    private bool mirrorChanged;
    private bool kdeChanged;
    private bool applyingLanguageDefault;
    private bool wirelessOptionsAllowed = true;
    internal Expander RepairsExpander { get; } = new() { IsExpanded = false, HorizontalContentAlignment = HorizontalAlignment.Stretch };
    internal IReadOnlyDictionary<string, CheckBox> OptionBoxes => boxes;
    internal ComboBox KdeChoice => kde;
    internal readonly HashSet<UIElement> GenericSections = new();
    internal Func<IReadOnlyList<BoilerWirelessHardware.Device>> WirelessHardwareReader { get; set; } = BoilerWirelessHardware.ReadPresentDevices;
    internal string WirelessDetectionStatus => detectionStatus.Text;
    internal Task ActiveWirelessDetection { get; private set; } = Task.CompletedTask;
    internal StackPanel SystemPresetsPanel { get; } = new();
    internal StackPanel OptionsNotesPanel { get; } = new() { Margin = new(0, 18, 0, 0) };
    internal CheckBox PasswordChoice => setDeckPassword;
    internal CheckBox SshChoice => enableSsh;
    internal PasswordBox PasswordInput => deckPassword;
    internal PasswordBox PasswordConfirmation => deckPasswordConfirm;
    public event EventHandler? Changed;
    private IEnumerable<OptionItem> DeviceRepairs => catalog.Repairs.Where(x => x.Id is not ("flathub-ustc" or "refind"));
    public InstallerOptionsPanel()
    {
        GenericSections.Add(Heading("通用组件", "Common components"));
        var common = OptionGrid(catalog.Plugins);
        Children.Add(common); GenericSections.Add(common);
        var driverNote = new TextBlock { TextWrapping = TextWrapping.Wrap, Foreground = Brushes.SlateGray, Margin = new Thickness(0, 0, 0, 16) };
        labels.Add((driverNote, "基础驱动由 SteamOS 提供，额外适配由机型包提供。",
            "SteamOS provides basic drivers; device packages add hardware support."));
        OptionsNotesPanel.Children.Add(driverNote);
        var repairs = new StackPanel();
        var wirelessNote = new TextBlock { TextWrapping = TextWrapping.Wrap, Foreground = Brushes.SlateGray, Margin = new Thickness(0, 0, 0, 10) };
        labels.Add((wirelessNote, "可识别当前 Windows 电脑的无线与蓝牙芯片，也可手动选择。当前电脑可能不是安装目标，请核对结果。仅下载所选组件在镜像中缺少的固件。",
            "Detect Wi-Fi and Bluetooth chips in this Windows computer, or select them manually. This computer may differ from the installation target; check the result. Only missing firmware for selected components is downloaded."));
        repairs.Children.Add(wirelessNote);
        repairs.Children.Add(detectWireless); repairs.Children.Add(detectionStatus);
        detectWireless.Click += (_, _) => { ActiveWirelessDetection = DetectWirelessHardwareAsync(); };
        repairs.Children.Add(OptionGrid(DeviceRepairs, trackRepairSelection: true));
        RepairsExpander.Header = repairHeading;
        RepairsExpander.Content = repairs;
        RepairsExpander.IsExpanded = true;
        Children.Add(RepairsExpander); GenericSections.Add(RepairsExpander);
        InitializeGraphicsOptions();
        if (catalog.Repairs.Any(x => x.Id == "flathub-ustc"))
        {
            Heading("其他设置", "Other settings");
            Children.Add(OptionGrid(catalog.Repairs.Where(x => x.Id == "flathub-ustc")));
        }
        Children.Add(SystemPresetsPanel);
        var presetNote = new TextBlock { TextWrapping = TextWrapping.Wrap, Foreground = Brushes.SlateGray };
        labels.Add((presetNote,
            "系统设置在安装时生效。Steam 语言请在 Steam 内设置。",
            "System settings take effect during installation. Set the Steam language in Steam."));
        OptionsNotesPanel.Children.Add(presetNote);
        var languageLabel = Heading("系统与桌面语言", "System and desktop language", SystemPresetsPanel);
        System.Windows.Automation.AutomationProperties.SetLabeledBy(kde, languageLabel);
        var languageNote = new TextBlock { TextWrapping = TextWrapping.Wrap, Foreground = Brushes.SlateGray };
        labels.Add((languageNote,
            "默认与 Windows 语言相同。",
            "Defaults to your Windows language."));
        SystemPresetsPanel.Children.Add(languageNote);
        var names = new[] { "保持镜像语言", "简体中文", "繁體中文", "English", "日本語", "한국어", "Deutsch", "Français", "Español", "Русский" };
        for (var i = 0; i < catalog.KdeLanguages.Length; i++) kde.Items.Add(new ComboBoxItem { Content = names[i], Tag = catalog.KdeLanguages[i] });
        kde.SelectedItem = kde.Items.Cast<ComboBoxItem>().Single(x => (string)x.Tag == InstallerOptions.DefaultKdeLanguage);
        kde.SelectionChanged += (_, _) =>
        {
            if (applyingLanguageDefault) return;
            kdeChanged = true;
            Changed?.Invoke(this, EventArgs.Empty);
        };
        SystemPresetsPanel.Children.Add(kde);
        Heading("账户与远程访问", "Account and remote access", SystemPresetsPanel);
        labels.Add(((TextBlock)(setDeckPassword.Content = new TextBlock { TextWrapping = TextWrapping.Wrap }),
            "为 deck 用户设置初始密码", "Set an initial password for the deck user"));
        SystemPresetsPanel.Children.Add(setDeckPassword);
        labels.Add((deckPasswordLabel, "新密码", "New password")); SystemPresetsPanel.Children.Add(deckPasswordLabel); SystemPresetsPanel.Children.Add(deckPassword);
        labels.Add((deckPasswordConfirmLabel, "确认密码", "Confirm password")); SystemPresetsPanel.Children.Add(deckPasswordConfirmLabel); SystemPresetsPanel.Children.Add(deckPasswordConfirm);
        System.Windows.Automation.AutomationProperties.SetLabeledBy(deckPassword, deckPasswordLabel);
        System.Windows.Automation.AutomationProperties.SetLabeledBy(deckPasswordConfirm, deckPasswordConfirmLabel);
        labels.Add(((TextBlock)(enableSsh.Content = new TextBlock { TextWrapping = TextWrapping.Wrap }),
            "预先启用 SSH 远程登录", "Enable SSH remote login"));
        SystemPresetsPanel.Children.Add(enableSsh);
        labels.Add((accessNote,
            "SSH 默认关闭。需要密码登录时，也请设置 deck 用户密码；至少 8 个字符。密码不会写入方案摘要或日志。",
            "SSH is off by default. For password login, also set a deck user password of at least 8 characters. Passwords are excluded from the plan summary and logs."));
        SystemPresetsPanel.Children.Add(accessNote);
        Children.Add(OptionsNotesPanel);
        setDeckPassword.Checked += (_, _) => { UpdateDeckPasswordState(); Changed?.Invoke(this, EventArgs.Empty); };
        setDeckPassword.Unchecked += (_, _) => { ClearSensitiveInputs(); UpdateDeckPasswordState(); Changed?.Invoke(this, EventArgs.Empty); };
        enableSsh.Checked += (_, _) => Changed?.Invoke(this, EventArgs.Empty);
        enableSsh.Unchecked += (_, _) => Changed?.Invoke(this, EventArgs.Empty);
        deckPassword.PasswordChanged += (_, _) => { if (!clearingSensitiveInputs) Changed?.Invoke(this, EventArgs.Empty); };
        deckPasswordConfirm.PasswordChanged += (_, _) => { if (!clearingSensitiveInputs) Changed?.Invoke(this, EventArgs.Empty); };
        UpdateDeckPasswordState();
        Refresh();
    }
    private TextBlock Heading(string zh, string en, Panel? parent = null)
    {
        var label = new TextBlock { FontSize = 18, FontWeight = FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 18, 0, 9) };
        labels.Add((label, zh, en)); (parent ?? this).Children.Add(label);
        return label;
    }
    private Grid OptionGrid(IEnumerable<OptionItem> items, bool trackRepairSelection = false)
    {
        var grid = new Grid();
        grid.ColumnDefinitions.Add(new ColumnDefinition()); grid.ColumnDefinitions.Add(new ColumnDefinition());
        var index = 0;
        foreach (var item in items)
        {
            if (index % 2 == 0) grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            var cell = new StackPanel { Margin = new Thickness(index % 2 == 0 ? 0 : 8, 0, index % 2 == 0 ? 8 : 0, 4) };
            var check = new CheckBox { IsChecked = item.Available && InstallerOptions.IsDefault(item), IsEnabled = item.Available,
                Margin = new Thickness(0), Tag = item.Id, HorizontalContentAlignment = HorizontalAlignment.Stretch,
                Content = new TextBlock { TextWrapping = TextWrapping.Wrap, LineHeight = 21 } };
            boxes.Add(item.Id, check); cell.Children.Add(check);
            if (trackRepairSelection)
            {
                void SelectionChanged(object sender, RoutedEventArgs args)
                {
                    UpdateRepairSummary();
                    if (!updatingRepairs) { wirelessSelectionGeneration++; Changed?.Invoke(this, EventArgs.Empty); }
                }
                check.Checked += SelectionChanged;
                check.Unchecked += SelectionChanged;
            }
            if (item.Id == "flathub-ustc")
            {
                check.Checked += (_, _) => { if (!applyingLanguageDefault) mirrorChanged = true; };
                check.Unchecked += (_, _) => { if (!applyingLanguageDefault) mirrorChanged = true; };
            }
            if (!item.Available)
            {
                var reason = new TextBlock { TextWrapping = TextWrapping.Wrap, Foreground = Brushes.SlateGray, FontSize = 12,
                    Margin = new Thickness(32, 0, 0, 8) };
                labels.Add((reason, item.UnavailableZh ?? "暂不可安装。", item.UnavailableEn ?? "Not available for installation."));
                cell.Children.Add(reason);
            }
            Grid.SetRow(cell, index / 2); Grid.SetColumn(cell, index % 2); grid.Children.Add(cell); index++;
        }
        grid.SizeChanged += (_, _) => LayoutOptions(grid);
        return grid;
    }
    private static void LayoutOptions(Grid grid)
    {
        var columns = grid.ActualWidth < 600 ? 1 : 2;
        if (grid.ColumnDefinitions.Count == columns) return;
        grid.ColumnDefinitions.Clear(); grid.RowDefinitions.Clear();
        for (var column = 0; column < columns; column++) grid.ColumnDefinitions.Add(new ColumnDefinition());
        for (var row = 0; row < (grid.Children.Count + columns - 1) / columns; row++) grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        for (var index = 0; index < grid.Children.Count; index++)
        {
            var cell = (StackPanel)grid.Children[index];
            Grid.SetRow(cell, index / columns); Grid.SetColumn(cell, index % columns);
            cell.Margin = columns == 1 ? new Thickness(0, 0, 0, 4) : new Thickness(index % columns == 0 ? 0 : 8, 0, index % columns == 0 ? 8 : 0, 4);
        }
    }
    internal async Task DetectWirelessHardwareAsync()
    {
        if (!wirelessOptionsAllowed || detectionBusy) return;
        var context = wirelessContextGeneration;
        var selection = wirelessSelectionGeneration;
        var request = ++detectionRequest;
        var reader = WirelessHardwareReader;
        detectionBusy = true; detectionFailed = detectionSuperseded = detectionCancelled = false;
        RefreshDetectionStatus(); RefreshDetectionButton();
        try
        {
            var found = await Task.Run(() => BoilerWirelessHardware.Match(reader()));
            // The native read cannot be interrupted. Results belong only to the
            // context and explicit selections that existed when it started.
            if (request != detectionRequest || context != wirelessContextGeneration || !wirelessOptionsAllowed) return;
            if (selection != wirelessSelectionGeneration)
            {
                detection = null; detectionSuperseded = true;
                return;
            }
            updatingRepairs = true;
            // Only recognized transports update their choices. Unknown radios
            // never replace a manual choice with a guess.
            foreach (var transport in new[] { "wifi:", "bluetooth:" })
            {
                var matched = found.Components.Where(id => id.StartsWith(transport, StringComparison.Ordinal)).ToHashSet();
                if (matched.Count == 0) continue;
                foreach (var item in DeviceRepairs.Where(item => item.Id.StartsWith(transport, StringComparison.Ordinal)))
                    boxes[item.Id].IsChecked = item.Available && matched.Contains(item.Id);
            }
            detection = found; detectionFailed = false;
        }
        catch (Exception error) when (error is System.ComponentModel.Win32Exception or IOException or UnauthorizedAccessException
            or System.Security.SecurityException or PlatformNotSupportedException or InvalidOperationException)
        {
            if (request != detectionRequest || context != wirelessContextGeneration || !wirelessOptionsAllowed) return;
            detection = null; detectionFailed = true;
        }
        finally
        {
            if (request == detectionRequest && context == wirelessContextGeneration)
            {
                updatingRepairs = false; detectionBusy = false;
                UpdateRepairSummary(); RefreshDetectionStatus(); RefreshDetectionButton(); Changed?.Invoke(this, EventArgs.Empty);
            }
        }
    }
    private void RefreshDetectionButton()
    {
        detectWireless.IsEnabled = wirelessOptionsAllowed && !detectionBusy;
        detectWireless.Content = new TextBlock { Text = detectionBusy
            ? PartitionInventory.L("正在识别本机硬件…", "Detecting this computer's hardware…")
            : PartitionInventory.L("识别本机 Wi-Fi 与蓝牙", "Detect this computer's Wi-Fi and Bluetooth"), TextWrapping = TextWrapping.Wrap };
    }
    internal void CancelPendingWirelessDetection()
    {
        CancelPendingGraphicsDetection();
        if (!detectionBusy) return;
        InvalidateWirelessDetection();
        detection = null; detectionFailed = detectionSuperseded = false; detectionCancelled = true;
        RefreshDetectionButton(); RefreshDetectionStatus(); UpdateRepairSummary();
    }
    private void InvalidateWirelessDetection()
    {
        wirelessContextGeneration++; detectionRequest++; detectionBusy = false;
    }
    private void RefreshDetectionStatus()
    {
        if (detectionBusy)
            detectionStatus.Text = PartitionInventory.L("正在只读检查本机设备，可继续修改选项。", "Reading this computer's devices without changing them. You can continue editing options.");
        else if (detectionCancelled)
            detectionStatus.Text = PartitionInventory.L("已取消识别，现有固件选择已保留。需要时可重新识别。", "Detection was cancelled; existing firmware choices were kept. Detect again if needed.");
        else if (detectionSuperseded)
            detectionStatus.Text = PartitionInventory.L("识别期间选项已修改，已保留手动选择。需要时可重新识别。", "Options changed during detection; your manual choices were kept. Detect again if needed.");
        else if (detectionFailed)
            detectionStatus.Text = PartitionInventory.L("无法读取本机硬件。请手动选择 Wi-Fi 与蓝牙厂商；现有选择已保留。", "Could not read this computer's hardware. Select Wi-Fi and Bluetooth vendors manually; existing choices were kept.");
        else if (detection is not BoilerWirelessHardware.Detection found)
            detectionStatus.Text = PartitionInventory.L("尚未识别，固件默认不选。", "Not detected yet; firmware is unselected by default.");
        else if (found.Components.Length == 0)
            detectionStatus.Text = PartitionInventory.L("未识别到支持的 Wi-Fi 或蓝牙厂商。请核对目标硬件并手动选择；现有选择已保留。", "No supported Wi-Fi or Bluetooth vendor was recognized. Check the target hardware and select manually; existing choices were kept.");
        else
        {
            var names = catalog.Repairs.Where(item => found.Components.Contains(item.Id)).Select(item => InstallerI18n.Language == "en-US" ? item.En : item.Zh);
            detectionStatus.Text = PartitionInventory.L("本机识别：", "Detected on this computer: ") + string.Join(InstallerI18n.Language == "en-US" ? ", " : "、", names)
                + PartitionInventory.L("。已更新匹配类别；未识别类别保留手动选择。请核对安装目标。", ". Matched categories were updated; manual choices for unrecognized categories were kept. Check the installation target.")
                + (found.Unrecognized > 0 ? PartitionInventory.L($" 另有 {found.Unrecognized} 个无线/蓝牙设备无法确认厂商。", $" Vendor could not be confirmed for {found.Unrecognized} other Wi-Fi/Bluetooth device(s).") : "");
        }
    }
    private void UpdateRepairSummary()
    {
        if (updatingRepairs) return;
        var available = DeviceRepairs.Where(x => x.Available && boxes.ContainsKey(x.Id)).ToArray();
        var selected = available.Count(x => boxes[x.Id].IsChecked == true);
        repairHeading.Text = PartitionInventory.L($"无线适配 · 可多选   {selected}/{available.Length} 项", $"Wireless · multiple selections   {selected}/{available.Length}");
    }
    internal void SetWirelessContext(string mode, bool hasPackage)
    {
        SetGraphicsContext(mode, hasPackage);
        // Every call also marks a mode reset, including generic -> generic.
        // A background result from the previous selection must never revive it.
        if (detectionBusy) { detection = null; detectionFailed = detectionSuperseded = detectionCancelled = false; }
        InvalidateWirelessDetection();
        var allowed = InstallerOptions.AllowsWireless(mode, hasPackage);
        wirelessOptionsAllowed = allowed;
        RepairsExpander.Visibility = allowed ? Visibility.Visible : Visibility.Collapsed;
        if (!allowed) { detection = null; detectionFailed = detectionSuperseded = detectionCancelled = false; }
        RefreshDetectionButton(); RefreshDetectionStatus();
        updatingRepairs = true;
        try
        {
            foreach (var item in DeviceRepairs)
            {
                var box = boxes[item.Id];
                box.IsEnabled = allowed && item.Available;
                if (!allowed) box.IsChecked = false;
            }
        }
        finally { updatingRepairs = false; }
        UpdateRepairSummary();
    }
    public SelectedOptions Selection => new(1, catalog.Repairs.Where(x => (wirelessOptionsAllowed || !InstallerOptions.WirelessComponents.Contains(x.Id))
            && boxes.TryGetValue(x.Id, out var box) && box.IsChecked == true).Select(x => x.Id).ToArray(),
        catalog.Plugins.Where(x => boxes[x.Id].IsChecked == true).Select(x => x.Id).ToArray(), InstallerI18n.Language,
        (string)((ComboBoxItem)kde.SelectedItem).Tag, setDeckPassword.IsChecked == true, enableSsh.IsChecked == true,
        GraphicsProfile: GraphicsSelection);
    public string? InitialDeckPassword => setDeckPassword.IsChecked == true ? deckPassword.Password : null;
    public void ValidateDeckPassword()
    {
        if (setDeckPassword.IsChecked != true) return;
        var value = deckPassword.Password;
        var bytes = Encoding.UTF8.GetByteCount(value);
        if (string.IsNullOrEmpty(value) || value != deckPasswordConfirm.Password || value.Length < 8 || bytes > 255
            || value.Any(c => c is '\0' or '\r' or '\n'))
            throw new IOException(InstallerI18n.Language == "en-US"
                ? "Enter and confirm a matching deck password of at least 8 characters (maximum 255 UTF-8 bytes)."
                : "请输入并确认一致的 deck 密码；至少 8 个字符，最多 255 个 UTF-8 字节。");
    }
    private bool clearingSensitiveInputs;
    public void ClearSensitiveInputs()
    {
        // The request already holds the validated password. Clearing its UI copy
        // must not invalidate the installation plan the user just approved.
        clearingSensitiveInputs = true;
        try { deckPassword.Clear(); deckPasswordConfirm.Clear(); }
        finally { clearingSensitiveInputs = false; }
    }
    private void UpdateDeckPasswordState()
    {
        var enabled = setDeckPassword.IsChecked == true;
        deckPasswordLabel.IsEnabled = deckPassword.IsEnabled = enabled;
        deckPasswordConfirmLabel.IsEnabled = deckPasswordConfirm.IsEnabled = enabled;
        deckPasswordLabel.Visibility = deckPassword.Visibility = enabled ? Visibility.Visible : Visibility.Collapsed;
        deckPasswordConfirmLabel.Visibility = deckPasswordConfirm.Visibility = enabled ? Visibility.Visible : Visibility.Collapsed;
    }
    public void Refresh()
    {
        if (!kdeChanged)
        {
            var previous = kde.SelectedItem;
            applyingLanguageDefault = true;
            try { kde.SelectedItem = kde.Items.Cast<ComboBoxItem>().Single(x => (string)x.Tag == InstallerOptions.DefaultKdeLanguage); }
            finally { applyingLanguageDefault = false; }
            if (previous != kde.SelectedItem) Changed?.Invoke(this, EventArgs.Empty);
        }
        if (!mirrorChanged && boxes.TryGetValue("flathub-ustc", out var mirror))
        {
            applyingLanguageDefault = true;
            mirror.IsChecked = InstallerI18n.Language == "zh-CN";
            applyingLanguageDefault = false;
        }
        RefreshDetectionButton();
        RefreshDetectionStatus();
        RefreshGraphicsOptions();
        ((ComboBoxItem)kde.Items[0]).Content = PartitionInventory.L("保持镜像语言", "Keep image language");
        UpdateRepairSummary();
        foreach (var item in catalog.Repairs.Concat(catalog.Plugins).Where(x => boxes.ContainsKey(x.Id))) ((TextBlock)boxes[item.Id].Content).Text = InstallerI18n.Language == "en-US" ? item.En : item.Zh;
        foreach (var (block, zh, en) in labels) block.Text = InstallerI18n.Language == "en-US" ? en : zh;
    }
    public string Describe()
    {
        var selected = Selection.Repairs.Concat(Selection.Plugins).ToHashSet();
        return string.Join("\n", catalog.Repairs.Concat(catalog.Plugins).Where(x => selected.Contains(x.Id)).Select(x => InstallerI18n.Language == "en-US" ? x.En : x.Zh))
            + "\n" + PartitionInventory.L("系统与桌面语言：", "System and desktop language: ") + ((ComboBoxItem)kde.SelectedItem).Content
            + "\n" + (InstallerI18n.Language == "en-US" ? "Set deck password: " : "设置 deck 密码：") + (Selection.SetDeckPassword ? (InstallerI18n.Language == "en-US" ? "Yes" : "是") : (InstallerI18n.Language == "en-US" ? "No" : "否"))
            + "\nSSH: " + (Selection.EnableSsh ? PartitionInventory.L("启用", "Enable") : PartitionInventory.L("沿用镜像设置", "Keep image setting"))
            + DescribeGraphics();
    }
}
