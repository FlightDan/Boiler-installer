using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;

namespace OxpInstaller;

// Filtering only changes row visibility; typing never changes the installation choice.
internal sealed class ModelSearchComboBox : ComboBox
{
    internal TextBox? SearchBox { get; private set; }
    private TextBlock? searchHint, noResults;
    internal bool DeviceModels { get; init; }
    internal Func<ComboBoxItem, string>? SearchText { get; init; }

    internal ModelSearchComboBox()
    {
        Style = (Style)Application.Current.FindResource("GraphicsDropdown");
        IsTextSearchEnabled = false;
        DropDownOpened += (_, _) =>
        {
            ResetSearch();
            Dispatcher.BeginInvoke(DispatcherPriority.ContextIdle, new Action(() =>
            {
                if (IsDropDownOpen && !DeviceModels) SearchBox?.Focus();
            }));
        };
        DropDownClosed += (_, _) => ResetSearch();
    }

    public override void OnApplyTemplate()
    {
        if (SearchBox != null) SearchBox.TextChanged -= SearchChanged;
        base.OnApplyTemplate();
        SearchBox = GetTemplateChild("PART_GraphicsSearch") as TextBox;
        searchHint = GetTemplateChild("PART_GraphicsSearchHint") as TextBlock;
        noResults = GetTemplateChild("PART_GraphicsNoResults") as TextBlock;
        if (SearchBox != null) SearchBox.TextChanged += SearchChanged;
        RefreshSearch();
    }

    private void SearchChanged(object sender, TextChangedEventArgs e) => RefreshSearch();

    internal void RefreshSearch()
    {
        var hint = DeviceModels ? PartitionInventory.L("搜索品牌或机型", "Search brand or model")
            : PartitionInventory.L("搜索核显或处理器型号", "Search GPU or processor model");
        if (SearchBox != null) AutomationProperties.SetName(SearchBox, hint);
        if (searchHint != null)
        {
            searchHint.Text = hint;
            searchHint.Visibility = string.IsNullOrEmpty(SearchBox?.Text) ? Visibility.Visible : Visibility.Collapsed;
        }
        var terms = (SearchBox?.Text ?? "").Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries);
        var visible = 0;
        foreach (var item in Items.Cast<ComboBoxItem>())
        {
            var text = SearchText?.Invoke(item) ?? (item.Content as TextBlock)?.Text + " " + item.Tag;
            var matches = terms.All(term => text.Contains(term, StringComparison.OrdinalIgnoreCase));
            item.Visibility = matches ? Visibility.Visible : Visibility.Collapsed;
            if (matches) visible++;
        }
        if (noResults != null)
        {
            noResults.Text = PartitionInventory.L("没有匹配的型号，请换个关键词。", "No matching model. Try another keyword.");
            noResults.Visibility = visible == 0 ? Visibility.Visible : Visibility.Collapsed;
        }
    }

    internal void ResetSearch()
    {
        if (SearchBox != null) SearchBox.Text = "";
        RefreshSearch();
    }

    protected override void OnPreviewKeyDown(KeyEventArgs e)
    {
        if (IsDropDownOpen && SearchBox?.IsKeyboardFocusWithin == true)
        {
            if (e.Key is Key.Down or Key.Up or Key.Enter)
            {
                var matches = Items.Cast<ComboBoxItem>().Where(item => item.Visibility == Visibility.Visible);
                var item = e.Key == Key.Enter && string.IsNullOrWhiteSpace(SearchBox.Text)
                    ? SelectedItem as ComboBoxItem
                    : e.Key == Key.Up ? matches.LastOrDefault() : matches.FirstOrDefault();
                if (item != null)
                {
                    if (e.Key == Key.Enter) { SelectedItem = item; IsDropDownOpen = false; Focus(); }
                    else item.Focus();
                }
                e.Handled = true; return;
            }
            if (e.Key is Key.Escape or Key.Tab)
            {
                IsDropDownOpen = false; Focus();
                if (e.Key == Key.Tab) MoveFocus(new TraversalRequest(Keyboard.Modifiers.HasFlag(ModifierKeys.Shift) ? FocusNavigationDirection.Previous : FocusNavigationDirection.Next));
                e.Handled = true; return;
            }
            // Keep typing, space and Home/End in the search field, away from ComboBox navigation.
            return;
        }
        base.OnPreviewKeyDown(e);
    }

    protected override void OnKeyDown(KeyEventArgs e)
    {
        // The nested editor owns space and editing shortcuts while it has focus.
        if (IsDropDownOpen && SearchBox?.IsKeyboardFocusWithin == true) return;
        base.OnKeyDown(e);
    }
}

internal static class GraphicsCatalogue
{
    internal sealed record Profile(string Id, string Zh, string En)
    {
        internal string Label => InstallerI18n.Language == "en-US"
            ? En.Replace(" graphics firmware candidate", "", StringComparison.Ordinal)
            : Zh.Replace(" 显卡固件候选", "", StringComparison.Ordinal);
    }
    internal static Profile[] Read()
    {
        using var json = JsonDocument.Parse(File.ReadAllText(Path.Combine(Backend.Root, "components/graphics-support.json")));
        var root = json.RootElement;
        if (root.GetProperty("schema").GetInt32() != 1 || root.GetProperty("kind").GetString() != "GraphicsCandidateCatalogue"
            || root.GetProperty("delivery").GetProperty("mode").GetString() != "on-demand"
            || root.GetProperty("delivery").GetProperty("installer_bundled_binaries").GetBoolean())
            throw new IOException("Invalid graphics candidate catalogue.");
        var profiles = root.GetProperty("profiles").EnumerateArray().Select(item =>
        {
            if (!item.GetProperty("install_available").GetBoolean() || item.GetProperty("status").GetString() != "experimental-baseline")
                throw new IOException("Graphics candidate baseline is not prepared.");
            var name = item.GetProperty("name");
            return new Profile(item.GetProperty("id").GetString()!, name.GetProperty("zh").GetString()!, name.GetProperty("en").GetString()!);
        }).ToArray();
        if (profiles.Length is < 1 or > 64 || profiles.Select(item => item.Id).Distinct().Count() != profiles.Length
            || profiles.Any(item => !System.Text.RegularExpressions.Regex.IsMatch(item.Id, "^graphics:(amd|intel)-[a-z0-9-]{1,80}$")
                || new[] { item.Zh, item.En }.Any(text => string.IsNullOrWhiteSpace(text) || text.Length > 1024 || text.Any(char.IsControl))))
            throw new IOException("Invalid graphics preview identities.");
        return profiles;
    }
}

public sealed partial class InstallerOptionsPanel
{
    private readonly GraphicsCatalogue.Profile[] graphicsProfiles = GraphicsCatalogue.Read();
    private readonly ModelSearchComboBox graphicsChoice = new() { Name = "GraphicsProfileChoice", HorizontalContentAlignment = HorizontalAlignment.Stretch };
    private readonly Button detectGraphics = new() { Name = "DetectGraphicsHardware", MinHeight = 48, MaxWidth = 180, Padding = new(12, 6, 12, 6), Margin = new(12, 0, 0, 0) };
    private readonly TextBlock graphicsStatus = new() { TextWrapping = TextWrapping.Wrap, Foreground = Brushes.SlateGray, Margin = new(0, 0, 0, 8) };
    private bool graphicsAllowed = true, graphicsBusy, graphicsUpdating, graphicsFailed, graphicsSuperseded, graphicsCancelled;
    private int graphicsContext, graphicsSelectionVersion, graphicsRequest;
    private BoilerGraphicsHardware.Detection? graphicsDetection;
    internal Expander GraphicsExpander { get; } = new() { Name = "GraphicsFirmwarePreview", IsExpanded = true, HorizontalContentAlignment = HorizontalAlignment.Stretch, Margin = new(0, 18, 0, 0) };
    internal Func<BoilerGraphicsHardware.Detection> GraphicsHardwareReader { get; set; } = BoilerGraphicsHardware.ReadThisComputer;
    internal Task ActiveGraphicsDetection { get; private set; } = Task.CompletedTask;
    internal ComboBox GraphicsChoice => graphicsChoice;
    internal Button GraphicsDetectButton => detectGraphics;
    internal string GraphicsDetectionStatus => graphicsStatus.Text;
    private string? GraphicsSelection => graphicsAllowed && graphicsChoice.SelectedItem is ComboBoxItem { Tag: string id } ? id : null;

    private void InitializeGraphicsOptions()
    {
        var body = new StackPanel();
        var note = new TextBlock { TextWrapping = TextWrapping.Wrap, Foreground = Brushes.SlateGray, Margin = new(0, 8, 0, 10) };
        labels.Add((note, "识别当前 Windows 电脑的核显，或手动选择安装目标型号。按候选基线补充固件，基础驱动由 SteamOS 提供。",
            "Detect graphics in this Windows computer, or select the target model manually. Add firmware from the candidate baseline; SteamOS supplies the base driver."));
        body.Children.Add(note);
        var label = new TextBlock { TextWrapping = TextWrapping.Wrap, Margin = new(0, 0, 0, 6) };
        labels.Add((label, "目标核显／处理器型号", "Target integrated GPU / processor"));
        body.Children.Add(label);
        AutomationProperties.SetLabeledBy(graphicsChoice, label);
        graphicsChoice.Items.Add(new ComboBoxItem { Content = new TextBlock { TextWrapping = TextWrapping.Wrap } });
        foreach (var profile in graphicsProfiles)
            graphicsChoice.Items.Add(new ComboBoxItem { Tag = profile.Id, Content = new TextBlock { TextWrapping = TextWrapping.Wrap } });
        graphicsChoice.SelectedIndex = 0;
        graphicsChoice.SelectionChanged += (_, _) =>
        {
            if (graphicsUpdating) return;
            graphicsSelectionVersion++;
            graphicsDetection = null; graphicsFailed = graphicsSuperseded = graphicsCancelled = false;
            RefreshGraphicsOptions(); Changed?.Invoke(this, EventArgs.Empty);
        };
        var choiceRow = new Grid { Margin = new(0, 0, 0, 8) };
        choiceRow.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        choiceRow.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        choiceRow.Children.Add(graphicsChoice);
        Grid.SetColumn(detectGraphics, 1); choiceRow.Children.Add(detectGraphics);
        body.Children.Add(choiceRow);
        body.Children.Add(graphicsStatus);
        var boundary = new TextBlock { TextWrapping = TextWrapping.Wrap, Foreground = Brushes.SlateGray };
        labels.Add((boundary, "实验性补全：只获取所选型号缺少的候选固件，保留已有文件，并检查启动镜像。候选清单可后续调整，硬件效果需实机核对。机型包沿用自己的适配方案。",
            "Experimental: fetch only missing candidates for the selected model, preserve existing files and check boot images. The candidate list can be revised; hardware behavior needs device testing. Device packages use their own support policy."));
        body.Children.Add(boundary);
        detectGraphics.Click += (_, _) => { ActiveGraphicsDetection = DetectGraphicsHardwareAsync(); };
        GraphicsExpander.Content = body;
        Children.Add(GraphicsExpander); GenericSections.Add(GraphicsExpander);
        RefreshGraphicsOptions();
    }

    private void RefreshGraphicsOptions()
    {
        GraphicsExpander.Header = new TextBlock { Text = PartitionInventory.L("核显固件 · 实验性补全", "Integrated graphics firmware · experimental"), FontSize = 17, FontWeight = FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap };
        var selected = graphicsChoice.SelectedIndex;
        graphicsUpdating = true;
        try
        {
            ((TextBlock)((ComboBoxItem)graphicsChoice.Items[0]).Content).Text = PartitionInventory.L("不补充核显固件", "Keep the image's graphics firmware");
            for (var index = 0; index < graphicsProfiles.Length; index++)
                ((TextBlock)((ComboBoxItem)graphicsChoice.Items[index + 1]).Content).Text = graphicsProfiles[index].Label;
            graphicsChoice.SelectedIndex = Math.Max(0, selected);
        }
        finally { graphicsUpdating = false; }
        graphicsChoice.RefreshSearch();
        detectGraphics.Content = new TextBlock { TextWrapping = TextWrapping.Wrap, Text = graphicsBusy
            ? PartitionInventory.L("正在识别本机核显…", "Detecting this computer's graphics…")
            : PartitionInventory.L("识别本机核显", "Detect this computer's graphics") };
        detectGraphics.IsEnabled = graphicsAllowed && !graphicsBusy;
        graphicsChoice.IsEnabled = graphicsAllowed;
        graphicsStatus.Text = graphicsBusy ? PartitionInventory.L("正在只读检查本机硬件，可继续手动选择。", "Reading local hardware without changing it. You can still select manually.")
            : graphicsSuperseded ? PartitionInventory.L("识别期间型号已修改，已保留手动选择。", "The model changed during detection; your manual choice was kept.")
            : graphicsCancelled ? PartitionInventory.L("识别已取消，现有选择已保留。", "Detection was cancelled; your choice was kept.")
            : graphicsFailed ? PartitionInventory.L("无法读取本机核显，请手动选择；现有选择已保留。", "Could not read local graphics. Select manually; your choice was kept.")
            : graphicsDetection is not BoilerGraphicsHardware.Detection found ? GraphicsSelection != null
                ? PartitionInventory.L("已手动选择核显型号，请核对安装目标；候选补全仍需实机验证。", "A graphics model is selected manually. Check the target; candidate support still needs device testing.")
                : PartitionInventory.L("尚未识别，核显固件默认不选。", "Not detected yet; no graphics firmware is selected.")
            : found.Profiles.Length == 1 ? PartitionInventory.L("本机识别：", "Detected on this computer: ") + string.Join("、", found.Devices)
                + PartitionInventory.L("。已选择匹配项，请核对安装目标；硬件效果需实机验证。", ". A matching choice was selected. Check the target; hardware behavior needs device testing.")
            : found.Profiles.Length > 1 ? PartitionInventory.L("发现多个核显候选，请核对目标并手动选择；现有选择已保留。", "Multiple graphics candidates were found. Check the target and select manually; your choice was kept.")
            : PartitionInventory.L("未能确认支持的核显型号，请手动选择；现有选择已保留。", "No supported graphics model was confirmed. Select manually; your choice was kept.");
    }

    internal async Task DetectGraphicsHardwareAsync()
    {
        if (!graphicsAllowed || graphicsBusy) return;
        var context = graphicsContext; var version = graphicsSelectionVersion; var request = ++graphicsRequest;
        var reader = GraphicsHardwareReader;
        graphicsBusy = true; graphicsFailed = graphicsSuperseded = graphicsCancelled = false;
        RefreshGraphicsOptions();
        try
        {
            var found = await Task.Run(reader);
            if (request != graphicsRequest || context != graphicsContext || !graphicsAllowed) return;
            if (version != graphicsSelectionVersion) { graphicsDetection = null; graphicsSuperseded = true; return; }
            graphicsDetection = found;
            if (found.Profiles.Length == 1)
            {
                var item = graphicsChoice.Items.Cast<ComboBoxItem>().SingleOrDefault(item => item.Tag as string == found.Profiles[0]);
                if (item != null)
                {
                    graphicsUpdating = true;
                    try { graphicsChoice.SelectedItem = item; }
                    finally { graphicsUpdating = false; }
                }
            }
        }
        catch (Exception error) when (error is System.ComponentModel.Win32Exception or IOException or UnauthorizedAccessException
            or System.Security.SecurityException or PlatformNotSupportedException or InvalidOperationException)
        {
            if (request != graphicsRequest || context != graphicsContext || !graphicsAllowed) return;
            graphicsDetection = null; graphicsFailed = true;
        }
        finally
        {
            if (request == graphicsRequest && context == graphicsContext)
            { graphicsBusy = false; RefreshGraphicsOptions(); Changed?.Invoke(this, EventArgs.Empty); }
        }
    }

    internal void CancelPendingGraphicsDetection()
    {
        if (!graphicsBusy) return;
        graphicsRequest++; graphicsContext++; graphicsBusy = false;
        graphicsDetection = null; graphicsFailed = graphicsSuperseded = false; graphicsCancelled = true;
        RefreshGraphicsOptions();
    }

    private void SetGraphicsContext(string mode, bool hasPackage)
    {
        graphicsRequest++; graphicsContext++; graphicsBusy = false;
        graphicsDetection = null; graphicsFailed = graphicsSuperseded = graphicsCancelled = false;
        graphicsChoice.IsDropDownOpen = false; graphicsChoice.ResetSearch();
        graphicsAllowed = InstallerOptions.AllowsWireless(mode, hasPackage);
        GraphicsExpander.Visibility = graphicsAllowed ? Visibility.Visible : Visibility.Collapsed;
        if (!graphicsAllowed) ResetGraphicsChoice();
        RefreshGraphicsOptions();
    }

    private void ResetGraphicsChoice()
    {
        graphicsUpdating = true;
        try { graphicsChoice.SelectedIndex = 0; graphicsSelectionVersion++; }
        finally { graphicsUpdating = false; }
    }

    private string DescribeGraphics() => GraphicsSelection is string id
        ? "\n" + PartitionInventory.L("核显固件（候选基线）：", "Graphics firmware (candidate baseline): ")
            + graphicsProfiles.Where(item => item.Id == id).Select(item => InstallerI18n.Language == "en-US" ? item.En : item.Zh).Single()
        : "";
}
