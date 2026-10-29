using System.IO;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Media;
using DrawingPath = System.Windows.Shapes.Path;

namespace OxpInstaller;

public sealed class SystemSourceCard : Border
{
    private readonly Grid header = new();
    private readonly Grid sourceRow = new();
    private readonly Grid sourceContent = new();
    private readonly Grid fileInfo = new();
    private readonly Border icon = new() { Width = 40, Height = 40, CornerRadius = new(8), Margin = new(0, 0, 16, 0), VerticalAlignment = VerticalAlignment.Center };
    private readonly StackPanel titles = new();
    private readonly WrapPanel titleRow = new() { VerticalAlignment = VerticalAlignment.Center };
    private readonly TextBlock title = new() { FontSize = 18, FontWeight = FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap, VerticalAlignment = VerticalAlignment.Center };
    private readonly TextBlock description = new() { FontSize = 12, Foreground = Brush("#61728A"), TextWrapping = TextWrapping.Wrap, Margin = new(0, 4, 0, 0) };
    private readonly TextBlock filename = new() { TextTrimming = TextTrimming.CharacterEllipsis, VerticalAlignment = VerticalAlignment.Center, Margin = new(12, 0, 12, 0) };
    private readonly TextBlock associated = new() { FontSize = 12, Foreground = Brush("#237A4B"), VerticalAlignment = VerticalAlignment.Center, Margin = new(0, 0, 12, 0) };
    private readonly TextBlock skippedText = new() { FontSize = 12, Foreground = Brush("#61728A"), TextWrapping = TextWrapping.Wrap, Margin = new(0, 4, 0, 0) };
    private readonly StackPanel actions = new() { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
    private readonly Button drop = new() { MinHeight = 48, Background = Brush("#F7F9FC"), HorizontalContentAlignment = HorizontalAlignment.Left, Margin = new(0, 12, 0, 0), Padding = new(12), BorderThickness = new(0) };
    private readonly TextBlock formats = new() { FontSize = 12, Foreground = Brush("#61728A"), TextWrapping = TextWrapping.Wrap, Margin = new(0, 10, 0, 0) };
    private readonly StackPanel selectionBody = new();
    private readonly Border downloadBody = new() { Margin = new(0, 16, 0, 0), Padding = new(0, 16, 0, 0), BorderBrush = Brush("#DFE6F0"), BorderThickness = new(0, 1, 0, 0), Visibility = Visibility.Collapsed };
    private bool expanded, syncing, selected, skipped, busy;
    private string? selectedPath;
    private string helpText = "";
    internal Button BrowseButton { get; } = ActionButton(false);
    internal Button DownloadButton { get; } = ActionButton(true);
    internal CheckBox SkipUpgrade { get; } = new() { Margin = new(12, 0, 0, 0), MinHeight = 40, FontSize = 13, Visibility = Visibility.Collapsed };
    internal bool IsExpanded => expanded;
    internal bool IsAssociated => selected;
    public UIElement? DownloadContent { get => downloadBody.Child; set => downloadBody.Child = value; }
    public event Action? BrowseRequested;
    public event Action<bool>? SkipChanged;
    private static SolidColorBrush Brush(string color) => (SolidColorBrush)new BrushConverter().ConvertFromString(color)!;
    private static Button ActionButton(bool download) => new()
    {
        MinWidth = 80, MinHeight = 48, Padding = new(12, 8, 12, 8), Margin = new(download ? 8 : 0, 0, 0, 0),
        Background = Brush(download ? "#EFF5FF" : "#FFFFFF"), Foreground = Brush(download ? "#2968D9" : "#192B43"),
        BorderBrush = Brush(download ? "#2968D9" : "#DFE6F0"), BorderThickness = new(1)
    };
    public SystemSourceCard()
    {
        Background = Brushes.White; BorderBrush = Brush("#DFE6F0"); BorderThickness = new(1); CornerRadius = new(12); Padding = new(20); Margin = new(0, 0, 0, 16);
        header.ColumnDefinitions.Add(new() { Width = GridLength.Auto }); header.ColumnDefinitions.Add(new() { Width = new(1, GridUnitType.Star) });
        sourceRow.ColumnDefinitions.Add(new() { Width = new(1, GridUnitType.Star) }); sourceRow.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        sourceRow.RowDefinitions.Add(new() { Height = GridLength.Auto }); sourceRow.RowDefinitions.Add(new() { Height = GridLength.Auto });
        fileInfo.ColumnDefinitions.Add(new() { Width = new(1, GridUnitType.Star) }); fileInfo.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        fileInfo.Margin = new(0, 12, 0, 0);
        titleRow.Children.Add(title); titleRow.Children.Add(SkipUpgrade); titles.Children.Add(titleRow); titles.Children.Add(description); titles.Children.Add(skippedText);
        header.Children.Add(icon); Grid.SetColumn(titles, 1); header.Children.Add(titles);
        Grid.SetColumn(filename, 0); fileInfo.Children.Add(filename); Grid.SetColumn(associated, 1); fileInfo.Children.Add(associated);
        sourceContent.Children.Add(drop); sourceContent.Children.Add(fileInfo); sourceRow.Children.Add(sourceContent);
        actions.Children.Add(BrowseButton); actions.Children.Add(DownloadButton); Grid.SetColumn(actions, 1); sourceRow.Children.Add(actions);
        selectionBody.Children.Add(sourceRow); selectionBody.Children.Add(formats);
        var body = new StackPanel(); body.Children.Add(header); body.Children.Add(selectionBody); body.Children.Add(downloadBody); Child = body;
        BrowseButton.Click += (_, _) => BrowseRequested?.Invoke(); drop.Click += (_, _) => BrowseRequested?.Invoke();
        DownloadButton.Click += (_, _) => { if (busy || skipped) return; expanded = !expanded; Render(); };
        SkipUpgrade.Checked += (_, _) => { if (!syncing) SkipChanged?.Invoke(true); };
        SkipUpgrade.Unchecked += (_, _) => { if (!syncing) SkipChanged?.Invoke(false); };
        SizeChanged += (_, _) => { ArrangeHeader(); ArrangeSourceRow(); };
        Render();
    }
    internal void Configure(string heading, string help, string acceptedFormats, bool showSkip, bool canSkip, bool allowDownload)
    {
        title.Text = heading; helpText = help; description.Text = help; formats.Text = acceptedFormats;
        SkipUpgrade.Visibility = showSkip ? Visibility.Visible : Visibility.Collapsed; SkipUpgrade.IsEnabled = canSkip;
        DownloadButton.Visibility = allowDownload ? Visibility.Visible : Visibility.Collapsed;
        AutomationProperties.SetName(BrowseButton, heading + " · " + PartitionInventory.L("浏览文件", "Browse files"));
        AutomationProperties.SetName(DownloadButton, heading + " · " + PartitionInventory.L("下载选项", "Download options"));
        Render();
    }
    internal void SetSelection(string? path, bool skip, bool isBusy, bool interactive)
    {
        var exists = !string.IsNullOrWhiteSpace(path) && File.Exists(path);
        if (exists && !string.Equals(path, selectedPath, StringComparison.OrdinalIgnoreCase)) expanded = false;
        selectedPath = path; selected = exists; skipped = skip; busy = isBusy;
        syncing = true; SkipUpgrade.IsChecked = skip; syncing = false;
        if (skip) expanded = false;
        if (busy && DownloadButton.Visibility == Visibility.Visible) expanded = true;
        BrowseButton.IsEnabled = drop.IsEnabled = interactive && !isBusy && !skip;
        DownloadButton.IsEnabled = interactive && !isBusy && !skip;
        filename.Text = exists ? Path.GetFileName(path) : ""; filename.ToolTip = path;
        Render();
    }
    internal void CollapseForAssociation() { expanded = false; Render(); }
    internal void ResetDisclosure() { expanded = false; selectedPath = null; Render(); }
    private void Render()
    {
        BrowseButton.Content = PartitionInventory.L("浏览", "Browse"); DownloadButton.Content = expanded ? PartitionInventory.L("收起", "Hide") : PartitionInventory.L("下载", "Download");
        SkipUpgrade.Content = PartitionInventory.L("不升级", "Skip upgrade");
        associated.Text = PartitionInventory.L("已关联", "Associated");
        skippedText.Text = PartitionInventory.L("沿用恢复镜像中的系统。", "Keep the system in the recovery image.");
        var compact = selected && !expanded && !skipped;
        filename.Visibility = associated.Visibility = compact ? Visibility.Visible : Visibility.Collapsed;
        skippedText.Visibility = skipped ? Visibility.Visible : Visibility.Collapsed;
        var showDrop = !selected && !expanded && !skipped;
        drop.Visibility = showDrop ? Visibility.Visible : Visibility.Collapsed;
        fileInfo.Visibility = compact ? Visibility.Visible : Visibility.Collapsed;
        sourceContent.Visibility = showDrop || compact ? Visibility.Visible : Visibility.Collapsed;
        formats.Visibility = showDrop ? Visibility.Visible : Visibility.Collapsed;
        downloadBody.Visibility = expanded && !skipped ? Visibility.Visible : Visibility.Collapsed;
        drop.Content = new TextBlock { Text = PartitionInventory.L("拖入文件，或点击“浏览”选择", "Drop a file, or choose Browse"), TextWrapping = TextWrapping.Wrap };
        icon.Background = Brush(compact ? "#237A4B" : "#EFF5FF"); icon.Width = icon.Height = compact ? 24 : 40;
        icon.CornerRadius = new(compact ? 12 : 8);
        icon.Child = new DrawingPath { Data = Geometry.Parse(compact ? "M 3,9 L 7,13 L 15,5" : "M 3,1 L 12,1 L 17,6 L 17,21 L 3,21 Z M 12,1 L 12,6 L 17,6 M 6,11 L 14,11 M 6,15 L 12,15"),
            Width = compact ? 16 : 20, Height = compact ? 14 : 24, Stretch = Stretch.Uniform, Stroke = compact ? Brushes.White : Brush("#2968D9"), StrokeThickness = 1.7, HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center };
        title.FontSize = compact ? 14 : 18;
        ArrangeHeader(); ArrangeSourceRow();
    }
    private void ArrangeHeader()
    {
        var narrow = ActualWidth > 0 && ActualWidth < 660;
        var compact = selected && !expanded && !skipped;
        description.Visibility = !skipped && (!compact || narrow) ? Visibility.Visible : Visibility.Collapsed;
        description.Text = helpText;
    }
    private void ArrangeSourceRow()
    {
        var narrow = ActualWidth > 0 && ActualWidth < 660;
        sourceRow.ColumnDefinitions[1].Width = narrow ? new(1, GridUnitType.Star) : GridLength.Auto;
        Grid.SetColumn(sourceContent, 0); Grid.SetRow(sourceContent, 0); Grid.SetColumnSpan(sourceContent, narrow ? 2 : 1);
        Grid.SetColumn(actions, narrow ? 0 : 1); Grid.SetRow(actions, narrow ? 1 : 0); Grid.SetColumnSpan(actions, narrow ? 2 : 1);
        actions.HorizontalAlignment = HorizontalAlignment.Right; actions.VerticalAlignment = VerticalAlignment.Center;
        actions.Margin = narrow ? new(0, 8, 0, 0) : new(12, 12, 0, 0);
    }
}
