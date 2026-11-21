using System.Globalization;
using System.IO;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;

namespace OxpInstaller;

public partial class PartitionPicker : UserControl
{
    private string? snapshot;
    private bool example, updating, busy;
    private bool automaticReadAttempted;
    // Synthetic WPF checks must not race a live inventory read.
    internal bool AutomaticReadEnabled { get; set; } = true;
    internal Func<CancellationToken, Task<string>> ReadDiskSnapshotAsync { get; set; } =
        token => Backend.PowerShellAsync("read-disks.ps1", [], _ => { }, token);
    internal bool BootPrerequisitesReady { get; set; } = true;
    private SpaceChoice? gap;
    private readonly HashSet<long> steamOsSelected = [];
    public event Action? Changed;
    private static string L(string zh, string en) => PartitionInventory.L(zh, en);
    public PartitionPicker()
    {
        InitializeComponent(); RefreshLanguage();
        Loaded += async (_, _) => await ReadDisksOnOpenAsync();
        IsVisibleChanged += async (_, _) => await ReadDisksOnOpenAsync();
    }
    internal async Task ReadDisksOnOpenAsync()
    {
        if (!BootPrerequisitesReady || !AutomaticReadEnabled || !IsLoaded || !IsVisible || automaticReadAttempted || busy || snapshot != null) return;
        await ReadDisksAsync();
    }
    internal InstallationTargetKind TargetKind => PhysicalInstallation.IsExternalBus((Disks.SelectedItem as InventoryDisk)?.Bus)
        ? InstallationTargetKind.ExternalDisk : InstallationTargetKind.InternalShared;
    internal decimal DefaultCapacityGiB { get; set; } = 128;
    internal bool AllowPhysicalDeletion { get; set; } = true;
    internal bool IsBusy => busy;
    internal string? InventorySnapshot => snapshot;
    internal bool IsExampleSnapshot => example;
    private bool debugMode;
    internal void SetDebugMode(bool enabled)
    {
        debugMode = enabled;
        ExampleButton.Visibility = enabled ? Visibility.Visible : Visibility.Collapsed;
        SteamOsExampleButton.Visibility = enabled ? Visibility.Visible : Visibility.Collapsed;
        if (!enabled && example) { Clear(); SelectionHint.Text = ""; }
        RefreshLanguage();
    }
    private void PickerSizeChanged(object sender, SizeChangedEventArgs e) => FitColumns();
    internal void FitColumns()
    {
        if (Partitions == null || Partitions.ActualWidth < 200) return;
        var available = Math.Max(200, Partitions.ActualWidth - 4);
        var details = FreeColumn.Visibility == Visibility.Visible;
        SizeColumn.Width = new DataGridLength(82);
        FreeColumn.Width = new DataGridLength(82);
        var flexible = available - (details ? 164 : 82);
        NameColumn.Width = new DataGridLength(flexible * (details ? .36 : .48));
        TypeColumn.Width = new DataGridLength(flexible * .22);
        StatusColumn.Width = new DataGridLength(flexible * (details ? .42 : .52));
    }
    public PartitionSelection Selection
    {
        get
        {
            if (busy || snapshot == null || gap == null) throw new InvalidOperationException(L("请选择一段可安装的未分配空间。", "Select an eligible unallocated region."));
            var capacity = PartitionInventory.ParseCapacity(CapacityInput.Text, gap.Size);
            if (capacity != 128 * PartitionInventory.GiB && capacity != 256 * PartitionInventory.GiB)
                throw new InvalidOperationException(L("请选择 128 GiB 或 256 GiB。", "Choose 128 GiB or 256 GiB."));
            var selected = new PartitionSelection(snapshot, example, gap.Disk, gap.Start, capacity, AlignRight.IsChecked == true);
            selected.Target(); return selected;
        }
    }
    public void RefreshLanguage()
    {
        RefreshButton.Content = L("刷新", "Refresh");
        AutomationProperties.SetName(RefreshButton, L("刷新磁盘列表", "Refresh disk list"));
        ExampleButton.Content = L("使用示例布局", "Use example layout");
        SteamOsExampleButton.Content = L("SteamOS 重装示例", "SteamOS reinstall example");
        DeletePartitionButton.Content = L("删除选定分区…", "Delete selected partition…");
        ReinstallSteamOsLabel.Text = L("重新安装 SteamOS", "Reinstall SteamOS");
        FormatRemainingButton.Content = L("将剩余空间格式化为 NTFS…", "Format remaining space as NTFS…");
        FormatRemainingHint.Text = L("保留所选 SteamOS 容量（128 或 256 GiB），将同一段空间的余量建为共享文件分区。确认后立即格式化；分区起点 1 MiB 对齐（满足 4K 对齐），4096 字节簇。", "Reserve the selected SteamOS capacity (128 or 256 GiB) and create a shared-file partition in the rest of this region. Formats immediately after confirmation; partition start aligned to 1 MiB (4K aligned), 4096-byte clusters.");
        NameColumn.Header = L("分区 / 空间", "Partition / region"); SizeColumn.Header = L("容量", "Size");
        FreeColumn.Header = L("可用", "Free"); TypeColumn.Header = L("类型", "Type"); StatusColumn.Header = L("状态", "Status");
        CapacityLabel.Text = L("SteamOS 安装容量", "SteamOS capacity"); AllSpaceButton.Content = L("使用全部空间", "Use all space");
        PlacementLabel.Text = L("安装区位置", "Installation placement");
        AlignLeft.Content = L("靠左", "Left");
        AlignRight.Content = L("靠右（默认）", "Right (default)");
        PlacementHint.Text = L("靠左：SteamOS 在前、余量在后；靠右：余量在前、SteamOS 在后。两种方式均按 1 MiB 对齐，满足 4K 对齐。", "Left: SteamOS first, remainder after. Right: remainder first, SteamOS after. Both use 1 MiB boundaries for 4K alignment.");
        AutomationProperties.SetName(Disks, L("目标磁盘", "Target disk"));
        AutomationProperties.SetName(Partitions, L("分区与未分配空间", "Partitions and unallocated space"));
        AutomationProperties.SetName(CapacityInput, CapacityLabel.Text);
        AutomationProperties.SetName(Capacity128Button, L("安装容量 128 GiB", "Install using 128 GiB"));
        AutomationProperties.SetName(Capacity256Button, L("安装容量 256 GiB", "Install using 256 GiB"));
        if (snapshot != null && !busy)
        {
            var disk = (Disks.SelectedItem as InventoryDisk)?.Number; var start = (Partitions.SelectedItem as PartitionRow)?.Start; var input = CapacityInput.Text;
            var selected = steamOsSelected.ToArray();
            LoadSnapshot(snapshot, example);
            Disks.SelectedItem = Disks.Items.Cast<InventoryDisk>().FirstOrDefault(d => d.Number == disk);
            Partitions.SelectedItem = Partitions.Items.Cast<PartitionRow>().FirstOrDefault(r => r.Start == start);
            CapacityInput.Text = input;
            steamOsSelected.UnionWith(selected.Where(s => (Disks.SelectedItem as InventoryDisk)?.Rows.Any(r => r.Start == s && r.CanDelete && r.SteamOsRole != null) == true));
            RefreshSteamOsStatus();
        }
    }
    internal void LoadSnapshot(string json, bool isExample, InventoryDisk? target = null)
    {
        Clear();
        var disks = PartitionInventory.Read(json);
        snapshot = json; example = isExample; Disks.ItemsSource = disks;
        if (target != null)
        {
            Disks.SelectedItem = disks.SingleOrDefault(d => IsSameDisk(d, target));
            if (Disks.SelectedItem == null)
                InventoryStatus.Text = L("原先选择的磁盘已断开或发生变化，请从列表重新选择。", "The selected disk was disconnected or changed. Select a disk again.");
        }
        else if (disks.Length > 0) Disks.SelectedIndex = 0;
        else InventoryStatus.Text = debugMode ? L("未发现磁盘。可重试读取，或使用示例布局。", "No disks found. Retry or use the example layout.") : L("未发现磁盘，请检查设备连接后重新读取。", "No disks found. Check the device connection and retry.");
    }
    private void Clear()
    {
        snapshot = null; gap = null; example = false; Disks.ItemsSource = null; Partitions.ItemsSource = null;
        InventoryStatus.Text = "";
        CapacityInput.Clear(); CapacityPanel.IsEnabled = false; ChangesHost.Children.Clear(); CapacityHint.Text = "";
        DeletePartitionButton.IsEnabled = false;
        FormatRemainingButton.IsEnabled = false;
        steamOsSelected.Clear(); RefreshSteamOsStatus();
        Changed?.Invoke();
    }
    private async void ReadDisks(object sender, RoutedEventArgs e) => await ReadDisksAsync();
    private static bool IsSameDisk(InventoryDisk disk, InventoryDisk target) =>
        disk.Number == target.Number && disk.GptId != Guid.Empty && disk.GptId == target.GptId;

    private async Task ReadDisksAsync(InventoryDisk? target = null)
    {
        if (busy) return;
        automaticReadAttempted = true;
        busy = true; RefreshButton.IsEnabled = ExampleButton.IsEnabled = SteamOsExampleButton.IsEnabled = false;
        Clear();
        InventoryStatus.Text = target == null ? L("正在读取磁盘布局…", "Reading disk layout…")
            : L("正在识别所选磁盘上的 SteamOS 分区…", "Identifying SteamOS partitions on the selected disk…");
        try
        {
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(30));
            var json = await ReadDiskSnapshotAsync(timeout.Token);
            LoadSnapshot(json, false, target);
        }
        catch (Exception error)
        {
            Clear(); InventoryStatus.Text = L("读取失败：", "Read failed: ") + error.Message;
            SelectionHint.Text = debugMode ? L("请重试读取；也可使用示例布局进行规划演示。", "Retry, or use the example layout to explore planning.") : L("请检查设备连接和管理员权限，然后重新读取。", "Check the device connection and administrator permission, then retry.");
        }
        finally { busy = false; RefreshButton.IsEnabled = ExampleButton.IsEnabled = SteamOsExampleButton.IsEnabled = true; RefreshSteamOsStatus(); Changed?.Invoke(); }
    }
    private void UseExample(object sender, RoutedEventArgs e) { if (debugMode && !busy) LoadSnapshot(PartitionInventory.ExampleJson, true); }
    private void UseSteamOsExample(object sender, RoutedEventArgs e) { if (debugMode && !busy) LoadSnapshot(SteamOsPartitions.ExampleJson(), true); }
    private void DiskChanged(object sender, SelectionChangedEventArgs e)
    {
        gap = null; CapacityInput.Clear(); ChangesHost.Children.Clear(); CapacityPanel.IsEnabled = false;
        Partitions.ItemsSource = (Disks.SelectedItem as InventoryDisk)?.Rows.Select(r => r.Space != null && r.Space.Size < 128 * PartitionInventory.GiB ? r with { Space = null, Status = L("不足 128 GiB", "Less than 128 GiB") } : r).ToArray();
        steamOsSelected.Clear(); RefreshSteamOsStatus();
        SelectionHint.Text = (Disks.SelectedItem as InventoryDisk)?.Rows.Any(r => r.Space != null && r.Space.Size >= 128 * PartitionInventory.GiB) == true
            ? L("请选择标记为“可安装 SteamOS”的未分配空间。", "Select unallocated space marked as available for SteamOS.")
            : L("需要至少 128 GiB 连续未分配空间。可删除不再需要的数据分区来释放空间；系统、启动和恢复分区受保护。", "At least 128 GiB of contiguous unallocated space is required. Delete an unwanted data partition to free space; system, boot and recovery partitions are protected.");
        Changed?.Invoke();
    }
    private void PartitionChanged(object sender, SelectionChangedEventArgs e)
    {
        gap = (Partitions.SelectedItem as PartitionRow)?.Space;
        DeletePartitionButton.IsEnabled = !busy && (example || AllowPhysicalDeletion) && (Partitions.SelectedItem as PartitionRow)?.CanDelete == true;
        CapacityPanel.IsEnabled = gap != null; ChangesHost.Children.Clear(); CapacityHint.Text = "";
        if (gap == null)
        {
            SelectionHint.Foreground = Brushes.Sienna;
            CapacityInput.Clear();
            SelectionHint.Text = (Partitions.SelectedItem as PartitionRow)?.CanDelete == true
                ? (!example && !AllowPhysicalDeletion ? L("演练模式不会删除真实分区。可使用示例布局体验删除。", "Rehearsal mode cannot delete real partitions. Use the example layout to try deletion.")
                    : L("可以删除此数据分区以释放空间。整个分区内的文件都会丢失，请先备份；点击下方按钮核对详情。", "You can delete this data partition to free space. All its files will be lost; back them up first, then review the details below."))
                : (Partitions.SelectedItem as PartitionRow)?.Status ?? L("请选择未分配空间。", "Select unallocated space.");
            Changed?.Invoke(); return;
        }
        SelectionHint.Foreground = new SolidColorBrush(Color.FromRgb(53,84,122));
        SelectionHint.Text = L("已有分区将保留。只在所选未分配空间内规划 SteamOS。", "Existing partitions are kept. SteamOS is planned only inside the selected unallocated region.");
        CapacityInput.Text = DefaultCapacityGiB.ToString(CultureInfo.InvariantCulture);
        UpdatePlan();
    }
    private async void DeletePartition(object sender, RoutedEventArgs e)
    {
        if (busy || snapshot == null || (!example && !AllowPhysicalDeletion) || Partitions.SelectedItem is not PartitionRow { CanDelete: true } row) return;
        var request = new PartitionDeleteRequest(snapshot, example, row.Disk, row.Start, InstallerI18n.Language);
        await DeletePartitionsAsync(request);
    }
    private bool CanReinstall(InventoryDisk? disk) => !busy && snapshot != null && (example || AllowPhysicalDeletion)
        && disk != null && disk.Style == "GPT" && disk.GptId != Guid.Empty;

    private async void ReinstallSteamOs(object sender, RoutedEventArgs e)
    {
        if (Disks.SelectedItem is not InventoryDisk disk || !CanReinstall(disk)) return;
        try
        {
            if (!example)
            {
                await ReadDisksAsync(disk);
                if (Disks.SelectedItem is not InventoryDisk fresh || !IsSameDisk(fresh, disk)) return;
                disk = fresh;
            }
            if (!disk.Rows.Any(r => r.SteamOsRole != null)) return;
            if (!ReviewSteamOsSelection(disk)) return;
            await DeletePartitionsAsync(SteamOsDeleteRequest());
        }
        catch (InvalidOperationException error) { InventoryStatus.Text = error.Message; }
    }
    internal PartitionDeleteRequest SteamOsDeleteRequest()
    {
        if (snapshot == null || Disks.SelectedItem is not InventoryDisk disk || steamOsSelected.Count == 0)
            throw new InvalidOperationException(L("请先选择 SteamOS 分区。", "Select SteamOS partitions first."));
        var starts = steamOsSelected.Order().ToArray();
        var request = new PartitionDeleteRequest(snapshot, example, disk.Number, starts[0], InstallerI18n.Language, starts);
        PartitionDeletion.ValidateTargets(request); return request;
    }
    private async Task DeletePartitionsAsync(PartitionDeleteRequest request)
    {
        var owner = Window.GetWindow(this);
        if (example)
        {
            var dialog = PartitionDeletion.Confirmation(request); dialog.Owner = owner; dialog.WindowStartupLocation = WindowStartupLocation.CenterOwner;
            if (dialog.ShowDialog() != true) return;
            SelectReleasedSpace(PartitionDeletion.Simulate(request), true, request);
            return;
        }
        busy = true; gap = null; Changed?.Invoke();
        RefreshSteamOsStatus();
        if (owner != null) owner.IsEnabled = false;
        DeletePartitionButton.IsEnabled = false;
        InventoryStatus.Text = L("请在管理员确认窗口核对删除目标。完成后将重新读取磁盘…", "Review the target in the administrator confirmation window. Disks will be refreshed afterward…");
        string? failure = null; bool deleted = false;
        try { deleted = await PartitionDeletion.RunAsync(request); }
        catch (Exception error) { failure = error.Message; }
        // Even cancellation/failure invalidates the old snapshot: the outcome may be uncertain.
        Clear();
        try
        {
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(30));
            var fresh = await Backend.PowerShellAsync("read-disks.ps1", [], _ => { }, timeout.Token);
            busy = false;
            if (deleted) SelectReleasedSpace(fresh, false, request); else LoadSnapshot(fresh, false);
            if (!deleted) InventoryStatus.Text = failure == null ? L("已取消删除。磁盘列表已刷新，请重新选择。", "Deletion cancelled. Disks refreshed; select again.")
                : (request.SteamOsStarts != null
                    ? L("删除未全部完成，可能已删除部分所选分区。已停止并刷新磁盘，请核对当前布局：\n", "Deletion may be partially complete. Stopped and refreshed disks; review the current layout:\n")
                    : L("删除未确认完成。已刷新磁盘，请核对当前布局：\n", "Deletion was not confirmed. Disks refreshed; review the current layout:\n")) + failure;
        }
        catch (Exception error)
        {
            Clear(); InventoryStatus.Text = (deleted ? L("删除已完成，但刷新失败。", "Deletion completed, but refresh failed.") : L("未能确认磁盘当前状态。", "Could not confirm the current disk state."))
                + L("请刷新磁盘列表后再继续。\n", "Refresh the disk list before continuing.\n") + (failure == null ? "" : failure + "\n") + error.Message;
        }
        finally { busy = false; if (owner != null) owner.IsEnabled = true; RefreshSteamOsStatus(); Changed?.Invoke(); }
    }
    internal void SelectReleasedSpace(string json, bool isExample, PartitionDeleteRequest request)
    {
        LoadSnapshot(json, isExample);
        Disks.SelectedItem = Disks.Items.Cast<InventoryDisk>().SingleOrDefault(d => d.Number == request.Disk);
        var starts = request.SteamOsStarts ?? [request.Start];
        var released = Partitions.Items.Cast<PartitionRow>().Where(r => r.Space != null && starts.Any(s => r.Start <= s && r.Start + r.Size > s)).OrderByDescending(r => r.Size).FirstOrDefault();
        Partitions.SelectedItem = released;
        InventoryStatus.Text = (isExample ? L("示例：已模拟删除，真实磁盘未改动。", "Example: deletion simulated; real disks unchanged.") : L("已删除选定分区。", "Selected partitions deleted."))
            + (released != null ? L("已选中释放后的未分配空间，可继续设置安装容量。", "The released unallocated space is selected. Choose an installation capacity.")
                : L("释放后的连续空间仍不足 128 GiB，或磁盘不符合安装要求。请查看列表。", "Released contiguous space is still below 128 GiB, or this disk is ineligible. Review the list."));
    }
    private void RefreshSteamOsStatus()
    {
        if (SteamOsStatus == null) return;
        var disk = Disks.SelectedItem as InventoryDisk;
        var rows = disk?.Rows.Where(r => r.SteamOsRole != null).ToArray() ?? [];
        var deletable = rows.Count(r => r.CanDelete);
        SteamOsStatus.Text = disk == null ? "" : rows.Length == 0
            ? L("未识别到可核验的 SteamOS 分区组。支持标准八分区及其三个启动分区残留；其他布局请在下方逐项核对。读取识别信息需要管理员权限。", "No verifiable SteamOS group identified. Standard eight-partition groups and their residual boot trios are supported; review other layouts below. Administrator access is needed to read identification data.")
            : InstallerI18n.Format($"识别到 {rows.Length} 个 SteamOS 分区，其中 {deletable} 个可删除。删除前会单独确认。", $"{rows.Length} SteamOS partitions identified, {deletable} eligible for deletion. Deletion requires a separate confirmation.");
        ReinstallSteamOsButton.IsEnabled = CanReinstall(disk);
    }

    private bool ReviewSteamOsSelection(InventoryDisk disk)
    {
        var dialog = PartitionDeletion.ReviewWindow(L("选择要删除的 SteamOS 分区", "Select SteamOS partitions to delete"), wide: true);
        dialog.Owner = Window.GetWindow(this);
        dialog.WindowStartupLocation = dialog.Owner == null ? WindowStartupLocation.CenterScreen : WindowStartupLocation.CenterOwner;
        dialog.ShowInTaskbar = false;
        var rows = disk.Rows.Where(r => r.SteamOsRole != null).ToArray();
        var selected = rows.Where(r => r.CanDelete && (steamOsSelected.Count == 0 || steamOsSelected.Contains(r.Start))).Select(r => r.Start).ToHashSet();
        var header = new StackPanel();
        header.Children.Add(PartitionDeletion.WrappedText(disk.Title));
        var status = PartitionDeletion.WrappedText("");
        AutomationProperties.SetLiveSetting(status, AutomationLiveSetting.Polite);
        header.Children.Add(status);
        var columns = new UniformGrid { Columns = dialog.Width >= 600 ? 2 : 1 };
        var actions = new WrapPanel { HorizontalAlignment = HorizontalAlignment.Right };
        var selectAll = new Button { Name = "SteamOsSelectAll", Content = L("全选可删除分区", "Select all eligible partitions") };
        var clear = new Button { Name = "SteamOsClear", Content = L("取消全选", "Clear selection") };
        var cancel = new Button { Content = L("取消", "Cancel"), IsCancel = true };
        var review = new Button
        {
            Name = "SteamOsReview", Content = L("核对删除范围…", "Review deletion…"),
            Background = new SolidColorBrush(Color.FromRgb(41, 104, 217)), Foreground = Brushes.White, Margin = new Thickness(0)
        };
        actions.Children.Add(selectAll); actions.Children.Add(clear); actions.Children.Add(cancel); actions.Children.Add(review);
        var deletable = rows.Count(r => r.CanDelete);
        void UpdateSelection()
        {
            var size = PartitionInventory.SizeText(rows.Where(r => selected.Contains(r.Start)).Sum(r => r.Size));
            status.Text = InstallerI18n.Format($"已选 {selected.Count} 个可删除分区 · 共 {size}", $"{selected.Count} eligible partitions selected · {size} total");
            selectAll.IsEnabled = selected.Count < deletable;
            clear.IsEnabled = review.IsEnabled = selected.Count > 0;
        }
        foreach (var row in rows)
        {
            var label = row.Name + " · " + row.CapacityText + (row.CanDelete ? "" : L(" · 保留：", " · Kept: ") + row.Status);
            var choice = new CheckBox
            {
                Tag = row.Start, IsChecked = selected.Contains(row.Start), IsEnabled = row.CanDelete,
                MinHeight = 44, VerticalContentAlignment = VerticalAlignment.Center, Margin = new Thickness(0, 0, 16, 4),
                Content = PartitionDeletion.WrappedText(label), ToolTip = row.Status
            };
            AutomationProperties.SetName(choice, label);
            if (row.CanDelete)
            {
                choice.Checked += (_, _) => { selected.Add(row.Start); UpdateSelection(); };
                choice.Unchecked += (_, _) => { selected.Remove(row.Start); UpdateSelection(); };
            }
            columns.Children.Add(choice);
        }
        void SetAll(bool value)
        {
            foreach (var choice in columns.Children.OfType<CheckBox>().Where(c => c.IsEnabled)) choice.IsChecked = value;
            (value ? clear : selectAll).Focus();
        }
        selectAll.Click += (_, _) => SetAll(true);
        clear.Click += (_, _) => SetAll(false);
        review.Click += (_, _) => { if (selected.Count > 0) dialog.DialogResult = true; };
        UpdateSelection();
        PartitionDeletion.SetReviewContent(dialog, header, columns, actions);
        dialog.Loaded += (_, _) => cancel.Focus();
        if (dialog.ShowDialog() != true) return false;
        steamOsSelected.Clear();
        steamOsSelected.UnionWith(selected);
        return true;
    }
    private void UseAll(object sender, RoutedEventArgs e)
    { if (gap != null) CapacityInput.Text = (gap.Size / (decimal)PartitionInventory.GiB).ToString(CultureInfo.InvariantCulture); }
    private void UsePreset(object sender, RoutedEventArgs e)
    {
        if (gap != null && sender is Button { Tag: string value }
            && decimal.TryParse(value, CultureInfo.InvariantCulture, out var capacity)
            && capacity * PartitionInventory.GiB <= gap.Size) CapacityInput.Text = value;
    }
    private void CapacityChanged(object sender, TextChangedEventArgs e) { if (!updating && ChangesHost != null) UpdatePlan(); }
    private void PlacementChanged(object sender, RoutedEventArgs e) { if (!updating && ChangesHost != null) UpdatePlan(); }
    private void UpdatePlan()
    {
        if (updating) return;
        updating = true;
        FormatRemainingButton.IsEnabled = false;
        FormatRemainingButton.ToolTip = L("选择安装容量后，至少保留 64 MiB 余量才能创建 NTFS。", "Choose an installation capacity leaving at least 64 MiB for NTFS.");
        Capacity128Button.IsEnabled = gap?.Size >= 128 * PartitionInventory.GiB;
        Capacity256Button.IsEnabled = gap?.Size >= 256 * PartitionInventory.GiB;
        Capacity256Button.ToolTip = gap != null && gap.Size < 256 * PartitionInventory.GiB
            ? L("所选连续未分配空间不足 256 GiB。", "The selected unallocated region is smaller than 256 GiB.")
            : L("将安装容量设为 256 GiB。", "Set installation capacity to 256 GiB.");
        ChangesHost.Children.Clear();
        try
        {
            if (gap == null) return;
            var plan = Selection;
            FormatRemainingButton.IsEnabled = (example || AllowPhysicalDeletion) && gap.Size - plan.Capacity >= RemainingSpaceFormat.Minimum;
            if (!example && !AllowPhysicalDeletion) FormatRemainingButton.ToolTip = L("演练模式不会格式化真实磁盘。", "Rehearsal mode cannot format real disks.");
            else if (FormatRemainingButton.IsEnabled) FormatRemainingButton.ToolTip = L("核对范围并确认后立即创建 NTFS；已有分区保持不变。", "Review and confirm to create NTFS immediately; existing partitions are kept.");
            var after = PartitionInventory.After(plan); var home = after.Single(s => s.Name == "home" && s.Action == "new");
            CapacityHint.Foreground = new SolidColorBrush(Color.FromRgb(97,114,138));
            CapacityHint.Text = InstallerI18n.Format($"已选择安装容量：{PartitionInventory.SizeText(plan.Capacity)} · 剩余未分配：{PartitionInventory.SizeText(gap.Size - plan.Capacity)}\n系统及启动约 24.375 GiB；数据分区 {PartitionInventory.SizeText(home.Size)}（格式化前）。\n可选容量：128 或 256 GiB。", $"Selected installation capacity: {PartitionInventory.SizeText(plan.Capacity)} · Unallocated remainder: {PartitionInventory.SizeText(gap.Size - plan.Capacity)}\nSystem and boot: about 24.375 GiB; data: {PartitionInventory.SizeText(home.Size)} (before formatting).\nCapacity choices: 128 or 256 GiB.");
            ShowChanges(ChangesHost, plan);
        }
        catch (InvalidOperationException error) { CapacityHint.Text = error.Message; CapacityHint.Foreground = Brushes.Sienna; }
        finally { updating = false; Changed?.Invoke(); }
    }
    private async void FormatRemaining(object sender, RoutedEventArgs e)
    {
        if (busy || snapshot == null || gap == null || (!example && !AllowPhysicalDeletion)) return;
        RemainingFormatRequest request;
        try { request = new(Selection, InstallerI18n.Language); RemainingSpaceFormat.Plan(request.Selection); }
        catch (InvalidOperationException error) { InventoryStatus.Text = error.Message; return; }
        var owner = Window.GetWindow(this);
        if (example)
        {
            var dialog = RemainingSpaceFormat.Confirmation(request); dialog.Owner = owner; dialog.WindowStartupLocation = WindowStartupLocation.CenterOwner;
            if (dialog.ShowDialog() == true) SelectReservedSpace(RemainingSpaceFormat.Simulate(request), true, request);
            return;
        }
        busy = true; gap = null; Changed?.Invoke();
        if (owner != null) owner.IsEnabled = false;
        FormatRemainingButton.IsEnabled = false;
        InventoryStatus.Text = L("请在管理员窗口核对剩余空间。完成后将重新读取磁盘…", "Review the remaining space in the administrator window. Disks will be refreshed afterward…");
        string? failure = null; bool formatted = false;
        try { formatted = await RemainingSpaceFormat.RunAsync(request); }
        catch (Exception error) { failure = error.Message; }
        Clear();
        try
        {
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(30));
            var fresh = await Backend.PowerShellAsync("read-disks.ps1", [], _ => { }, timeout.Token);
            busy = false;
            if (formatted) SelectReservedSpace(fresh, false, request); else LoadSnapshot(fresh, false);
            if (!formatted) InventoryStatus.Text = failure == null ? L("已取消格式化。磁盘列表已刷新，请重新选择安装空间和容量。", "Formatting cancelled. Disks refreshed; select the installation region and capacity again.")
                : L("格式化未确认完成，可能已创建分区。请核对刷新后的布局再继续：\n", "Formatting was not confirmed; a partition may have been created. Review the refreshed layout before continuing:\n") + failure;
        }
        catch (Exception error) { Clear(); InventoryStatus.Text = L("无法确认当前布局，请重新读取磁盘：\n", "Could not confirm the layout. Read disks again:\n") + failure + "\n" + error.Message; }
        finally { busy = false; if (owner != null) owner.IsEnabled = true; Changed?.Invoke(); }
    }

    internal void SelectReservedSpace(string json, bool isExample, RemainingFormatRequest request)
    {
        RemainingSpaceFormat.ValidateFormattedSnapshot(json, isExample, request);
        LoadSnapshot(json, isExample);
        Disks.SelectedItem = Disks.Items.Cast<InventoryDisk>().SingleOrDefault(d => d.Number == request.Selection.Disk);
        Partitions.SelectedItem = Partitions.Items.Cast<PartitionRow>().SingleOrDefault(r => r.Space?.Start == request.Selection.Target().Start);
        AlignRight.IsChecked = request.Selection.AlignRight;
        AlignLeft.IsChecked = !request.Selection.AlignRight;
        if (gap != null) CapacityInput.Text = (request.Selection.Capacity / (decimal)PartitionInventory.GiB).ToString(CultureInfo.InvariantCulture);
        InventoryStatus.Text = isExample ? L("示例：已模拟创建 NTFS，保留的 SteamOS 空间已选中。", "Example: NTFS creation simulated; reserved SteamOS space selected.")
            : L("NTFS 数据分区已创建。已刷新磁盘并选中保留的 SteamOS 空间。", "NTFS data partition created. Disks refreshed and reserved SteamOS space selected.");
    }

    public static void ShowChanges(StackPanel host, PartitionSelection plan)
    {
        host.Children.Clear(); var target = plan.Target();
        var disk = PartitionInventory.Read(plan.Snapshot).Single(d => d.Number == target.Disk);
        Text(L("安装前后 · 规划预览", "Before and after · planned layout"), true);
        Text((plan.Example ? L("示例 · ", "Example · ") : "") + disk.Title);
        var before = PartitionInventory.Before(disk); var after = PartitionInventory.After(plan);
        var remainder = disk.Rows.Single(r => r.Space?.Start == plan.Start).Size - plan.Capacity;
        Text(plan.AlignRight ? L("安装区靠右 · SteamOS 位于所选空间末端", "Right placement · SteamOS at the end of the selected region") : L("安装区靠左 · SteamOS 位于所选空间起点", "Left placement · SteamOS at the start of the selected region"));
        Text(InstallerI18n.Format($"SteamOS：{PartitionInventory.SizeText(plan.Capacity)} · 此段剩余未分配：{PartitionInventory.SizeText(remainder)}", $"SteamOS: {PartitionInventory.SizeText(plan.Capacity)} · Unallocated remainder in this region: {PartitionInventory.SizeText(remainder)}"));
        Bar(L("安装前", "Before"), before); Bar(L("安装后（规划）", "After (planned)"), after);
        Text(L("蓝：保留分区　绿：新增 SteamOS　灰：未分配", "Blue: kept   Green: new SteamOS   Gray: unallocated"));
        Text(L("保留所有已有分区；新增 8 个 SteamOS 分区。", "Keep all existing partitions; add 8 SteamOS partitions."));
        var detail = new StackPanel();
        foreach (var s in after)
        {
            var action = s.Action == "new" ? L("新增", "New") : s.Action == "keep" ? L("保留", "Keep") : L("未分配", "Unallocated");
            detail.Children.Add(new TextBlock { Text = $"{action} · {s.Name} · {PartitionInventory.SizeText(s.Size)}", TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0,4,0,4) });
        }
        host.Children.Add(new Expander { Header = L("查看分区变化明细", "Partition change details"), Content = detail, Margin = new Thickness(0,8,0,8) });
        void Text(string value, bool bold = false) => host.Children.Add(new TextBlock { Text = value, TextWrapping = TextWrapping.Wrap, FontWeight = bold ? FontWeights.SemiBold : FontWeights.Normal, Margin = new Thickness(0,6,0,6) });
        void Bar(string title, LayoutSegment[] segments)
        {
            Text(title); var grid = new Grid { Height = 32, ClipToBounds = true };
            foreach (var s in segments)
            {
                grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(s.Size, GridUnitType.Star) });
                var label = $"{s.Name} · {PartitionInventory.SizeText(s.Size)}";
                var border = new Border { Background = new SolidColorBrush(s.Action == "new" ? Color.FromRgb(41,151,112) : s.Action == "keep" ? Color.FromRgb(68,116,185) : Color.FromRgb(204,213,225)), BorderBrush = Brushes.White, BorderThickness = new Thickness(0,0,1,0), ToolTip = label };
                AutomationProperties.SetName(border, label);
                Grid.SetColumn(border, grid.ColumnDefinitions.Count - 1); grid.Children.Add(border);
            }
            host.Children.Add(grid);
        }
    }
}
