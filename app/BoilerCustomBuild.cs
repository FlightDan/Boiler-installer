using System.IO;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Threading;

namespace OxpInstaller;

public partial class MainWindow
{
    private sealed record BuildRow(string Id, string Name, string Category, CheckBox Source, OptionItem? Generic = null);
    private StackPanel? customBuild, customRows, customSelected;
    private TextBox? customSearch, customPaste, customPlanName;
    private Grid? customColumns;
    private Expander? customPasteExpander;
    private TextBlock? customSearchHint;
    private TextBlock? customStatus, customIdentity;
    private ComboBox? customModel;
    private WrapPanel? customTabs, customChips;
    private Expander? customSettings;
    private string customCategory = "hardware", customChip = "all";
    private bool customBuildSwitchingDevice, customBuildRefreshing, customBuildRefreshQueued, customOnlySelected, customShowAllSelected;
    private int customPage;
    private bool CustomBuildBusy => operation != null || DownloadsBusy || sourceInspectionBusy || inputInspectionBusy || inputAssociationBusy;

    private void CreateCustomBuild()
    {
        customBuild = new StackPanel { Name = "CustomBuild" };
        ComponentOptionsHost.Children.Remove(optionsPanel);
        customSettings = new Expander { Content = optionsPanel, Margin = new(0, 16, 0, 0), HorizontalContentAlignment = HorizontalAlignment.Stretch };
        ComponentOptionsHost.Children.Insert(0, customBuild);
        ComponentOptionsHost.Children.Add(customSettings);
        customIdentity = new TextBlock { TextWrapping = TextWrapping.Wrap, Margin = new(0, 0, 0, 8), Foreground = BoilerBrush("#61728A") };
        customBuild.Children.Add(customIdentity);
        if (currentHardwarePanel != null)
        {
            optionsPanel.GeneralCustomComponents.Children.Remove(currentHardwarePanel);
            customBuild.Children.Add(currentHardwarePanel);
        }
        customModel = new ComboBox { Name = "CustomBuildModel", MinHeight = 44, Margin = new(0, 0, 0, 10) };
        customModel.Style = TryFindResource("SourceDropdown") as Style;
        customModel.SelectionChanged += async (_, _) => await SwitchCustomBuildModelAsync();
        customBuild.Children.Add(customModel);
        customTabs = new WrapPanel { Margin = new(0, 0, 0, 8) }; customBuild.Children.Add(customTabs);
        customSearch = new TextBox { Name = "CustomBuildSearch", MinHeight = 40, Padding = new(8), Margin = new(0, 0, 0, 8) };
        System.Windows.Automation.AutomationProperties.SetName(customSearch, PartitionInventory.L("搜索项目名称或 ID", "Search item names or IDs"));
        customSearchHint = new TextBlock { TextWrapping = TextWrapping.Wrap, Foreground = BoilerBrush("#61728A"), Margin = new(0, 0, 0, 4) }; customBuild.Children.Add(customSearchHint);
        customSearch.TextChanged += (_, _) => { customChip = "all"; customPage = 0; RenderCustomBuildRows(); }; customBuild.Children.Add(customSearch);
        customChips = new WrapPanel(); customBuild.Children.Add(customChips);
        customColumns = new Grid(); customBuild.Children.Add(customColumns);
        customRows = new StackPanel { Name = "CustomBuildRows" };
        customSelected = new StackPanel { Name = "CustomBuildSelected", Margin = new(12, 0, 0, 0) };
        customColumns.Children.Add(customRows); customColumns.Children.Add(customSelected);
        customColumns.SizeChanged += (_, _) => LayoutCustomBuildColumns();
        LayoutCustomBuildColumns();
        customPlanName = new TextBox { Name = "CustomBuildPlanName", MinHeight = 40, Padding = new(8), Margin = new(0, 2, 0, 4) };
        System.Windows.Automation.AutomationProperties.SetName(customPlanName, PartitionInventory.L("方案名称", "Plan name"));
        customPaste = new TextBox { Name = "CustomBuildPaste", AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, MinHeight = 56, Margin = new(0, 6, 0, 6) };
        var pasteBody = new StackPanel(); pasteBody.Children.Add(customPaste);
        customPasteExpander = new Expander { Content = pasteBody, Margin = new(0, 4, 0, 0) };
        var apply = new Button { Name = "CustomBuildApplyIds", MinHeight = 42, HorizontalAlignment = HorizontalAlignment.Left, Padding = new(12, 6, 12, 6) };
        apply.Click += (_, _) => { try { ApplyCustomBuildIds(customPaste.Text); customStatus!.Text = PartitionInventory.L("已应用项目列表。", "Item list applied."); } catch (Exception error) { customStatus!.Text = error.Message; } };
        pasteBody.Children.Add(apply); apply.Tag = "apply";
        customStatus = new TextBlock { TextWrapping = TextWrapping.Wrap, Foreground = BoilerBrush("#61728A"), Margin = new(0, 6, 0, 0) };
        foreach (var pair in optionsPanel.OptionBoxes) if (pair.Key != SsdBootRepair.Option) pair.Value.Visibility = Visibility.Collapsed;
        RefreshCustomBuild();
    }

    // Proxy controls only change presentation. Their authoritative source controls
    // report actual selection changes through the existing handlers.
    private bool IsCustomBuildPresentationElement(object? source) => source is DependencyObject element
        && (customBuild != null && (ReferenceEquals(element, customBuild) || customBuild.IsAncestorOf(element))
            || source is System.Windows.Controls.Primitives.ToggleButton { TemplatedParent: Expander expander }
                && ReferenceEquals(expander, customSettings));

    private static string PublicBuildId(string id) => id.Contains(':') ? id : "generic:" + id;
    private List<BuildRow> CustomBuildRows()
    {
        var rows = new List<BuildRow>();
        var catalog = InstallerOptions.Catalog;
        foreach (var item in catalog.Repairs.Concat(catalog.Plugins))
        {
            if (item.Id == SsdBootRepair.Option && (!SsdTargetMatches() || HasSsdProvider())) continue;
            if (!optionsPanel.OptionBoxes.TryGetValue(item.Id, out var box)) continue;
            var category = BuildCategory(item.Id, catalog.Plugins.Contains(item));
            rows.Add(new(PublicBuildId(item.Id), InstallerI18n.Language == "en-US" ? item.En : item.Zh, category, box, item));
        }
        foreach (var pair in deviceComponentChoices.Where(p => p.Key != deviceLegacyBridge))
        {
            if (pair.Value.Tag is JsonElement component && SsdBootRepair.IsProvider(component) && !SsdTargetMatches()) continue;
            rows.Add(new(PublicBuildId(pair.Key), BuildName(pair.Value, pair.Key), BuildCategory(pair.Key, IsStaticDeckyConsumer(pair.Value)), pair.Value));
        }
        foreach (var pair in deviceLegacyChoices)
        {
            var item = (JsonElement)pair.Value.Tag;
            var category = BuildCategory(pair.Key, item.TryGetProperty("category", out var value) && value.GetString() == "plugins");
            rows.Add(new(PublicBuildId(pair.Key), PreviewText(item, "name", pair.Key), category, pair.Value));
        }
        return rows;
    }
    private void LayoutCustomBuildColumns()
    {
        if (customColumns == null || customRows == null || customSelected == null) return;
        var wide = customColumns.ActualWidth >= 680;
        customColumns.ColumnDefinitions.Clear(); customColumns.RowDefinitions.Clear();
        if (wide)
        {
            customColumns.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(2, GridUnitType.Star) });
            customColumns.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            Grid.SetColumn(customSelected, 1); Grid.SetRow(customSelected, 0); customSelected.Margin = new(12, 0, 0, 0);
        }
        else
        {
            customColumns.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto }); customColumns.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            Grid.SetColumn(customSelected, 0); Grid.SetRow(customSelected, 1); customSelected.Margin = new(0, 12, 0, 0);
        }
    }
    private static string BuildCategory(string id, bool plugin)
    {
        var name = id.ToLowerInvariant();
        if (new[] { "slot-switch", "refind", "windows-reboot", "ssd:gm7-boot" }.Any(name.Contains)) return "boot";
        if (new[] { "partition-mount", "gamescope-env", "keyboard", "flathub", "split-lock", "maintenance" }.Any(name.Contains)) return "system";
        if (plugin || new[] { "decky", "opti", "waydroid", "lsfg" }.Any(name.Contains)) return "plugins";
        return "hardware";
    }
    private static string BuildGroup(string id, string category)
    {
        var name = id.ToLowerInvariant();
        if (category == "boot") return "recovery";
        if (category == "system") return "tools";
        if (category == "plugins") return "apps";
        if (new[] { "wifi", "wireless", "bluetooth" }.Any(name.Contains)) return "wireless";
        if (new[] { "display", "hdr", "screen" }.Any(name.Contains)) return "display";
        if (new[] { "power", "fan", "battery", "performance", "tdp" }.Any(name.Contains)) return "power";
        if (new[] { "input", "key", "button", "gyro", "controller", "multifunction" }.Any(name.Contains)) return "input";
        return "other";
    }
    private static string BuildGroupLabel(string group) => group switch
    {
        "wireless" => PartitionInventory.L("无线与蓝牙", "Wi-Fi & Bluetooth"),
        "display" => PartitionInventory.L("显示与 HDR", "Display & HDR"),
        "power" => PartitionInventory.L("电源与性能", "Power & performance"),
        "input" => PartitionInventory.L("输入与按键", "Input & controls"),
        "recovery" => PartitionInventory.L("启动与恢复", "Boot & recovery"),
        "tools" => PartitionInventory.L("系统工具", "System tools"),
        "apps" => PartitionInventory.L("插件与应用", "Plugins & apps"),
        _ => PartitionInventory.L("其他硬件", "Other hardware")
    };
    private static string BuildName(CheckBox box, string fallback)
    {
        if (box.Tag is JsonElement item && item.TryGetProperty("name", out var name)) return LocalizedValue(name);
        return fallback;
    }
    private void QueueCustomBuildRefresh()
    {
        if (customBuild == null || customBuildRefreshing || customBuildRefreshQueued) return;
        customBuildRefreshQueued = true;
        Dispatcher.BeginInvoke(DispatcherPriority.Background, new Action(() => { customBuildRefreshQueued = false; if (step == 2) RefreshCustomBuild(); }));
    }
    private void RefreshCustomBuild()
    {
        if (customBuild == null || customBuildRefreshing) return;
        var focusedId = (System.Windows.Input.Keyboard.FocusedElement as CheckBox)?.Tag as string;
        customBuildRefreshing = true;
        try
        {
            customIdentity!.Text = selectedDevicePackage == null ? PartitionInventory.L("当前方案：通用配置", "Current plan: generic configuration") :
                PartitionInventory.L("当前机型包：", "Current device package: ") + (displayedDeviceMetadata is JsonElement metadata ? DeviceLabel(metadata) : selectedDevicePackage);
            customSearchHint!.Text = PartitionInventory.L("搜索全部项目的名称或 ID", "Search all item names or IDs");
            customPasteExpander!.Header = PartitionInventory.L("粘贴项目 ID 列表", "Paste item ID list");
            customSettings!.Header = PartitionInventory.L("系统、账户与硬件检测设置", "System, account and hardware detection settings");
            var applyButton = ((StackPanel)customPasteExpander!.Content).Children.OfType<Button>().Single();
            applyButton.Content = PartitionInventory.L("加入清单", "Add to selection"); applyButton.IsEnabled = !CustomBuildBusy;
            customModel!.Items.Clear();
            customModel.Items.Add(new ComboBoxItem { Content = PartitionInventory.L("选择其他机型…", "Choose another device…") });
            if (deviceList != null)
                foreach (var item in deviceList.Items.OfType<ComboBoxItem>().Where(i => i.Tag is JsonElement))
                    customModel.Items.Add(new ComboBoxItem { Content = ((TextBlock)item.Content).Text, Tag = item });
            customModel.SelectedIndex = 0;
            customModel.IsEnabled = installMode == "custom" && !CustomBuildBusy && !customBuildSwitchingDevice;
            customTabs!.Children.Clear();
            foreach (var (id, zh, en) in new[] { ("hardware", "硬件适配", "Hardware"), ("system", "系统工具", "System tools"), ("plugins", "插件应用", "Plugins & apps"), ("boot", "启动恢复", "Boot & recovery"), ("info", "方案信息", "Plan information") })
            {
                var button = new Button { Content = PartitionInventory.L(zh, en), Margin = new(0, 0, 6, 6), MinHeight = 40, Padding = new(10, 4, 10, 4), FontWeight = customCategory == id ? FontWeights.SemiBold : FontWeights.Normal, Background = customCategory == id ? BoilerBrush("#2968D9") : Brushes.White, Foreground = customCategory == id ? Brushes.White : BoilerBrush("#192B43") };
                button.Click += (_, _) => { customCategory = id; customChip = "all"; customPage = 0; RefreshCustomBuild(); }; customTabs.Children.Add(button);
            }
            if (deviceComponentsPanel != null) deviceComponentsPanel.Visibility = Visibility.Collapsed;
            RenderCustomBuildRows();
                if (focusedId != null) customRows!.Children.OfType<Border>().Select(b => b.Child).OfType<StackPanel>().SelectMany(p => p.Children.OfType<CheckBox>()).FirstOrDefault(c => (string?)c.Tag == focusedId)?.Focus();
        }
        finally { customBuildRefreshing = false; }
    }
    private string BuildDetails(BuildRow row)
    {
        var parts = new List<string>();
        if (row.Source.Tag is JsonElement item)
        {
            foreach (var key in new[] { "description", "reason", "legacy_notes", "migration_status", "dependencies" })
                if (item.TryGetProperty(key, out var value) && value.ValueKind is not JsonValueKind.Null and not JsonValueKind.Undefined)
                {
                    var text = DeviceText(value);
                    if (!string.IsNullOrWhiteSpace(text) && text != "[]") parts.Add(key + ": " + text);
                }
        }
        if (row.Generic is OptionItem generic)
        {
            if (!generic.Available) parts.Add(InstallerI18n.Language == "en-US" ? generic.UnavailableEn ?? "Unavailable" : generic.UnavailableZh ?? "不可用");
            if (generic.Dependencies.Length > 0) parts.Add(PartitionInventory.L("依赖：", "Dependencies: ") + string.Join(", ", generic.Dependencies.Select(PublicBuildId)));
        }
        return parts.Count == 0 ? row.Id : string.Join("\n", parts);
    }
    private void RenderCustomBuildRows()
    {
        if (customRows == null || customChips == null || customSelected == null) return;
        var rows = CustomBuildRows();
        customRows.Children.Clear(); customChips.Children.Clear(); customSelected.Children.Clear();
        var query = customSearch!.Text.Trim();
        if (customCategory == "info" && query.Length == 0)
        {
            customRows.Children.Add(new TextBlock { Text = CustomBuildPlan(), TextWrapping = TextWrapping.Wrap });
            if (deviceLegacyUpdateHold != null)
            {
                var hold = new CheckBox { Content = ((TextBlock)deviceLegacyUpdateHold.Content).Text, IsChecked = deviceLegacyUpdateHold.IsChecked, IsEnabled = !CustomBuildBusy, MinHeight = 48 };
                hold.Checked += (_, _) => { if (!CustomBuildBusy) deviceLegacyUpdateHold.IsChecked = true; };
                hold.Unchecked += (_, _) => { if (!CustomBuildBusy) deviceLegacyUpdateHold.IsChecked = false; }; customRows.Children.Add(hold);
            }
        }
        else
        {
            if (customCategory == "boot" && query.Length == 0)
            {
                customRows.Children.Add(new TextBlock { Text = PartitionInventory.L("启动管理器在后续步骤中配置。", "Configure the boot manager in the following step."), TextWrapping = TextWrapping.Wrap });
                var boot = new Button { Content = PartitionInventory.L("打开启动器设置", "Open boot settings"), IsEnabled = !CustomBuildBusy, MinHeight = 40, HorizontalAlignment = HorizontalAlignment.Left, Margin = new(0, 4, 0, 8) };
                boot.Click += (_, _) => { if (!CustomBuildBusy) ShowStep(4); }; customRows.Children.Add(boot);
            }
            var visible = rows.Where(r => query.Length > 0 || r.Category == customCategory).ToArray();
            var chips = visible.Select(r => BuildGroup(r.Id, r.Category)).Distinct().OrderBy(x => x).ToArray();
            foreach (var chip in new[] { "all" }.Concat(chips))
            {
                var active = customChip == chip;
                var button = new Button { Content = chip == "all" ? PartitionInventory.L("全部", "All") : BuildGroupLabel(chip), Margin = new(0, 0, 6, 6), MinHeight = 36, FontWeight = active ? FontWeights.SemiBold : FontWeights.Normal, Background = active ? BoilerBrush("#2968D9") : Brushes.White, Foreground = active ? Brushes.White : BoilerBrush("#192B43") };
                button.Click += (_, _) => { customChip = chip; customPage = 0; RenderCustomBuildRows(); }; customChips.Children.Add(button);
            }
            var only = new CheckBox { Content = PartitionInventory.L("仅显示已选", "Selected only"), IsChecked = customOnlySelected, MinHeight = 36, Margin = new(8, 0, 0, 0) };
            only.Checked += (_, e) => { e.Handled = true; customOnlySelected = true; customPage = 0; RenderCustomBuildRows(); };
            only.Unchecked += (_, e) => { e.Handled = true; customOnlySelected = false; customPage = 0; RenderCustomBuildRows(); }; customChips.Children.Add(only);
            var results = visible.Where(r => (customChip == "all" || BuildGroup(r.Id, r.Category) == customChip)
                && (!customOnlySelected || IsEffectiveBuildSelection(r))
                && (query.Length == 0 || r.Id.Contains(query, StringComparison.OrdinalIgnoreCase) || r.Name.Contains(query, StringComparison.OrdinalIgnoreCase)))
                .OrderByDescending(r => r.Source.IsEnabled).ThenBy(r => r.Generic != null).ThenBy(r => r.Name, StringComparer.CurrentCultureIgnoreCase).ToArray();
            var pages = Math.Max(1, (results.Length + 7) / 8); customPage = Math.Clamp(customPage, 0, pages - 1);
            customRows.Children.Add(new TextBlock { Text = PartitionInventory.L($"{results.Length} 项 · 第 {customPage + 1} / {pages} 页", $"{results.Length} items · Page {customPage + 1} / {pages}"), Margin = new(0, 4, 0, 8), Foreground = BoilerBrush("#61728A") });
            foreach (var row in results.Skip(customPage * 8).Take(8))
            {
                var source = row.Source;
                var check = new CheckBox { Tag = row.Id, Content = new TextBlock { Text = row.Name + "  ·  " + row.Id, TextWrapping = TextWrapping.Wrap }, IsChecked = source.IsChecked, IsEnabled = source.IsEnabled && !CustomBuildBusy, MinHeight = 44, Margin = new(0, 2, 0, 2) };
                System.Windows.Automation.AutomationProperties.SetName(check, row.Name);
                check.Checked += (_, _) => SetBuildRow(row, true, new()); check.Unchecked += (_, _) => SetBuildRow(row, false, new());
                var content = new StackPanel(); content.Children.Add(check);
                content.Children.Add(new TextBlock { Text = BuildGroupLabel(BuildGroup(row.Id, row.Category)) + "  ·  " + (source.IsEnabled ? (source.IsChecked == true ? PartitionInventory.L("已选择", "Selected") : PartitionInventory.L("可选择", "Available")) : PartitionInventory.L("不可用", "Unavailable")), Foreground = BoilerBrush("#61728A"), Margin = new(24, 0, 0, 4), TextWrapping = TextWrapping.Wrap });
                content.Children.Add(new Expander { Header = PartitionInventory.L("详情", "Details"), Content = new TextBlock { Text = BuildDetails(row), TextWrapping = TextWrapping.Wrap }, Margin = new(24, 0, 0, 4) });
                customRows.Children.Add(new Border { Background = Brushes.White, BorderBrush = BoilerBrush("#DFE6F0"), BorderThickness = new(1), CornerRadius = new(8), Padding = new(10), Margin = new(0, 0, 0, 8), Child = content });
            }
            if (results.Length == 0)
            {
                customRows.Children.Add(new TextBlock { Text = PartitionInventory.L("没有匹配的项目。可清除搜索或取消“仅显示已选”。", "No matching items. Clear the search or turn off Selected only."), TextWrapping = TextWrapping.Wrap });
                if (query.Length > 0) { var clear = new Button { Content = PartitionInventory.L("清除搜索", "Clear search"), HorizontalAlignment = HorizontalAlignment.Left, MinHeight = 36 }; clear.Click += (_, _) => customSearch.Text = ""; customRows.Children.Add(clear); }
            }
            if (pages > 1)
            {
                var nav = new WrapPanel();
                var previous = new Button { Content = PartitionInventory.L("上一页", "Previous"), IsEnabled = customPage > 0, MinHeight = 36, Margin = new(0, 0, 8, 0) };
                var next = new Button { Content = PartitionInventory.L("下一页", "Next"), IsEnabled = customPage + 1 < pages, MinHeight = 36 };
                previous.Click += (_, _) => { customPage--; RenderCustomBuildRows(); }; next.Click += (_, _) => { customPage++; RenderCustomBuildRows(); };
                nav.Children.Add(previous); nav.Children.Add(next); customRows.Children.Add(nav);
            }
        }
        var selected = rows.Where(IsEffectiveBuildSelection).ToArray();
        customSelected.Children.Add(new TextBlock { Text = PartitionInventory.L($"已选择 {selected.Length} 项", $"Selected: {selected.Length}"), FontWeight = FontWeights.SemiBold, Margin = new(0, 4, 0, 4) });
        customSelected.Children.Add(new TextBlock { Text = PartitionInventory.L("方案名称", "Plan name"), Margin = new(0, 4, 0, 0) });
        customSelected.Children.Add(customPlanName!);
        var actions = new WrapPanel();
        void AddAction(string zh, string en, Action action, bool enabled = true)
        {
            var button = new Button { Content = PartitionInventory.L(zh, en), IsEnabled = enabled, MinHeight = 36, Margin = new(0, 0, 6, 6) };
            button.Click += (_, _) => { try { action(); } catch (Exception error) { customStatus!.Text = error.Message; } }; actions.Children.Add(button);
        }
        AddAction("复制项目 ID", "Copy item IDs", () => { Clipboard.SetText(CustomBuildIds()); customStatus!.Text = PartitionInventory.L($"已复制 {selected.Length} 个组件 ID。", $"Copied {selected.Length} item IDs."); });
        AddAction("复制方案信息", "Copy plan information", () => { Clipboard.SetText(CustomBuildPlan()); customStatus!.Text = PartitionInventory.L("方案信息已复制。", "Plan information copied."); });
        AddAction("清空清单", "Clear selection", ClearCustomBuildSelection, !CustomBuildBusy);
        customSelected.Children.Add(actions);
        customSelected.Children.Add(new TextBox { Name = "CustomBuildIdSummary", Text = CustomBuildIds(), IsReadOnly = true, TextWrapping = TextWrapping.Wrap, MaxHeight = 80, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Margin = new(0, 4, 0, 8) });
        customSelected.Children.Add(customPasteExpander!); customSelected.Children.Add(customStatus!);
        foreach (var row in customShowAllSelected ? selected.AsEnumerable() : selected.Take(6))
        {
            var line = new DockPanel { Margin = new(0, 2, 0, 2) };
            var remove = new Button { Content = PartitionInventory.L("移除", "Remove"), IsEnabled = !CustomBuildBusy, MinHeight = 36, Margin = new(6, 0, 0, 0) };
            DockPanel.SetDock(remove, Dock.Right); line.Children.Add(remove);
            line.Children.Add(new TextBlock { Text = row.Name + "\n" + row.Id, TextWrapping = TextWrapping.Wrap, VerticalAlignment = VerticalAlignment.Center });
            remove.Click += (_, _) => SetBuildRow(row, false, new()); customSelected.Children.Add(line);
        }
        if (selected.Length > 6)
        {
            var more = new Button { Content = customShowAllSelected ? PartitionInventory.L("收起", "Show less") : PartitionInventory.L($"展开其余 {selected.Length - 6} 项", $"Show remaining {selected.Length - 6}"), MinHeight = 36, HorizontalAlignment = HorizontalAlignment.Left };
            more.Click += (_, _) => { customShowAllSelected = !customShowAllSelected; RenderCustomBuildRows(); }; customSelected.Children.Add(more);
        }
    }
    private void ClearCustomBuildSelection()
    {
        if (CustomBuildBusy) return;
        foreach (var row in CustomBuildRows().Where(r => r.Source.IsEnabled)) SetBuildRow(row, false, new());
        QueueCustomBuildRefresh();
    }

    private void SetBuildRow(BuildRow row, bool selected, HashSet<string> seen)
    {
        if (CustomBuildBusy || !seen.Add(row.Id)) return;
        var rows = CustomBuildRows();
        row.Source.IsChecked = selected && row.Source.IsEnabled;
        if (row.Generic == null) return;
        if (selected)
            foreach (var dep in row.Generic.Dependencies)
                if (rows.FirstOrDefault(r => r.Generic?.Id == dep) is BuildRow dependency) SetBuildRow(dependency, true, seen);
        if (!selected)
            foreach (var dependent in rows.Where(r => r.Generic?.Dependencies.Contains(row.Generic.Id) == true)) SetBuildRow(dependent, false, seen);
    }
    private bool IsEffectiveBuildSelection(BuildRow row) => row.Source.IsChecked == true && !(row.Id == "core:decky" && deviceLegacyChoices.TryGetValue("oxp3:decky", out var legacyDecky) && legacyDecky.IsChecked == true);
    private string CustomBuildIds() => string.Join("\n", CustomBuildRows().Where(IsEffectiveBuildSelection).Select(r => r.Id).OrderBy(id => id, StringComparer.Ordinal));
    private string CustomBuildPlan() => "name: " + (customPlanName?.Text.Replace("\r", " ").Replace("\n", " ") ?? "") + "\nmodel: " + (displayedDeviceMetadata is JsonElement metadata && metadata.TryGetProperty("id", out var id) ? id.GetString() : "generic") + "\npackage-sha256: " + (selectedDeviceDigest ?? "none") + "\n" + CustomBuildIds();
    private void ApplyCustomBuildIds(string input)
    {
        if (CustomBuildBusy) throw new InvalidOperationException(PartitionInventory.L("当前任务进行中，请稍后再修改。", "A task is in progress; change selections afterwards."));
        var ids = input.Split(new[] { '\r', '\n', ',', ';', ' ', '\t' }, StringSplitOptions.RemoveEmptyEntries).Distinct(StringComparer.Ordinal).ToArray();
        if (ids.Length == 0) throw new InvalidOperationException(PartitionInventory.L("请先输入项目 ID。", "Enter item IDs first."));
        var rows = CustomBuildRows();
        var byId = rows.GroupBy(r => r.Id, StringComparer.Ordinal).ToDictionary(g => g.Key, g => g.ToArray(), StringComparer.Ordinal);
        if (ids.Any(id => !byId.TryGetValue(id, out var choices) || choices.Length != 1 || !choices[0].Source.IsEnabled))
            throw new InvalidOperationException(PartitionInventory.L("列表包含未知、不可用或不属于当前机型的项目；未更改任何选择。", "The list contains an unknown, unavailable, or wrong-model item; no selections were changed."));
        // Validate the complete dependency closure before touching any source control.
        bool CanSelect(BuildRow row, HashSet<string> seen)
        {
            if (!seen.Add(row.Id)) return true;
            if (!row.Source.IsEnabled) return false;
            if (IsStaticDeckyConsumer(row.Source) && deviceLegacyBridge != null && (!deviceLegacyChoices.TryGetValue("oxp3:decky", out var provider) || !provider.IsEnabled)) return false;
            return row.Generic == null || row.Generic.Dependencies.All(dep =>
                rows.FirstOrDefault(r => r.Generic?.Id == dep) is BuildRow dependency && CanSelect(dependency, seen));
        }
        if (ids.Any(id => !CanSelect(byId[id][0], new())))
            throw new InvalidOperationException(PartitionInventory.L("项目依赖不可用；未更改任何选择。", "A required dependency is unavailable; no selections were changed."));
        foreach (var id in ids) SetBuildRow(byId[id][0], true, new());
        QueueCustomBuildRefresh();
    }
    private async Task SwitchCustomBuildModelAsync()
    {
        if (customBuildRefreshing || customBuildSwitchingDevice || CustomBuildBusy || customModel?.SelectedItem is not ComboBoxItem { Tag: ComboBoxItem target } || deviceList == null) return;
        var previous = deviceList.SelectedItem;
        var requestedId = ((JsonElement)target.Tag).GetProperty("id").GetString();
        customBuildSwitchingDevice = true; customModel.IsEnabled = false;
        try
        {
            updatingDeviceList = true; deviceList.SelectedItem = target; updatingDeviceList = false;
            if (!CanUseChosenDevice) throw new InvalidOperationException(PartitionInventory.L("该机型只有适配指南或当前不可用。", "This model is guide-only or unavailable."));
            await DeviceAction(SelectChosenDeviceAsync);
            if (deviceActionError != null) throw new InvalidOperationException(deviceActionError);
            if (selectedDevicePackage == null || displayedDeviceMetadata is not JsonElement applied || !applied.TryGetProperty("id", out var appliedId) || appliedId.GetString() != requestedId)
                throw new InvalidOperationException(PartitionInventory.L("机型包未应用，当前方案保持不变。", "The package was not applied; the current plan remains unchanged."));
        }
        catch (Exception error)
        {
            updatingDeviceList = true; deviceList.SelectedItem = previous; updatingDeviceList = false;
            customStatus!.Text = error.Message;
        }
        finally { updatingDeviceList = false; customBuildSwitchingDevice = false; RefreshCustomBuild(); }
    }
}
