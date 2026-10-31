using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Media;
using Microsoft.Win32;

namespace OxpInstaller;

internal enum InputRole { Ignore, Recovery, Image, Upgrade, Device, Resource }
internal sealed class InputAssignment(string path)
{
    internal string Path { get; } = path;
    internal DiscoveredInput? Input { get; set; }
    internal InputRole Role { get; set; }
    internal bool Applied { get; set; }
}
internal sealed record InputAssociationPlan(string Mode, Dictionary<InputRole, DiscoveredInput> Files, string? Conflict);
internal static class InputAssociations
{
    internal static InputRole DefaultRole(InputKind kind) => kind switch
    {
        InputKind.RecoveryImage => InputRole.Recovery, InputKind.DiskImage => InputRole.Image,
        InputKind.UpgradeRoot or InputKind.UpgradeBundle => InputRole.Upgrade,
        InputKind.DevicePackage or InputKind.DeviceScript => InputRole.Device, InputKind.ResourcePool => InputRole.Resource, _ => InputRole.Ignore
    };
    internal static InputRole[] Roles(InputKind kind) => kind == InputKind.RecoveryImage
        ? [InputRole.Recovery, InputRole.Image, InputRole.Ignore]
        : DefaultRole(kind) is InputRole.Ignore ? [InputRole.Ignore] : [DefaultRole(kind), InputRole.Ignore];
    internal static InputAssociationPlan Resolve(IEnumerable<InputAssignment> rows, string mode, bool hasImage, bool prepared)
    {
        var files = new Dictionary<InputRole, DiscoveredInput>();
        string? issue = null;
        foreach (var row in rows.Where(row => row.Role != InputRole.Ignore && row.Input != null))
        {
            var input = row.Input!;
            if (input.Error != null || input.RaucManifest is { IsSupportedByCurrentInstaller: false } || !Roles(input.Kind).Contains(row.Role))
            {
                issue = input.RaucManifest is { IsSupportedByCurrentInstaller: false } manifest
                    ? PartitionInventory.L($"SteamOS {manifest.Version} 更新包已识别，但当前安装流程不支持该版本。", $"SteamOS {manifest.Version} update bundle detected, but this installer does not support it.")
                    : PartitionInventory.L("请移除无法识别的关联项。", "Remove unsupported assignments.");
                break;
            }
            if (!files.TryAdd(row.Role, input)) { issue = PartitionInventory.L("同一位置有多个文件，请保留其中一个，其他设为“暂不关联”。", "Several files target the same location. Keep one and leave the others unassigned."); break; }
        }
        if (files.ContainsKey(InputRole.Resource) && files.Count > 1)
            issue = PartitionInventory.L("外部资源池只用于隔离预览，请单独导入；其他文件可暂不关联。", "Import an external resource pool separately for isolated preview; leave other files unassigned.");
        else if (files.ContainsKey(InputRole.Image) && files.Keys.Any(role => role != InputRole.Image))
            issue = PartitionInventory.L("完整 IMG 原样刷入与恢复构建是两种流程，请选择其中一组文件。", "Flashing a complete IMG and building from recovery are separate flows. Select one set of files.");
        if (!files.ContainsKey(InputRole.Recovery) && !files.ContainsKey(InputRole.Image)
            && files.Keys.Any(role => role is InputRole.Upgrade or InputRole.Device) && (mode == "imported" && hasImage || prepared))
            issue = PartitionInventory.L("当前是完整或已准备镜像。要关联升级或机型适配，请同时提供恢复镜像。", "The current source is a complete or prepared image. Add a recovery image to associate an upgrade or device package.");
        var targetMode = files.ContainsKey(InputRole.Image) ? "imported"
            : files.ContainsKey(InputRole.Device) || files.ContainsKey(InputRole.Recovery) && mode == "imported" || mode == "imported" && !hasImage ? "custom" : mode;
        return new(targetMode, files, issue);
    }
}

public partial class MainWindow
{
    private Border? inputAssociationPanel;
    private Button? inputDropButton;
    private TextBlock? inputAssociationHeading, inputAssociationNote, inputAssociationStatus;
    private StackPanel? inputRowsHost;
    private CheckBox? inputDeviceTrust;
    private CheckBox? inputResourceConsent;
    private TextBlock? inputResourcePreview;
    private JsonElement? importedResourcePreview;
    private Button? inputApplyButton, inputClearButton, inputCancelButton, inputFolderButton;
    private readonly List<InputAssignment> inputAssignments = new();
    private readonly Queue<InputAssignment> inputQueue = new();
    private CancellationTokenSource? inputCancellation;
    private Task? inputInspectionTask;
    private bool inputInspectionBusy, inputAssociationBusy;
    private bool inputDragHighlighted;
    private bool inputAssociationApplied;
    private string? trustedInputDevicePath;
    private string? confirmedInputResourcePath;
    internal Func<CancellationToken, Task>? InputPreparationGateForChecks { get; set; }

    private bool CanReceiveInputs => operation == null && !inputAssociationBusy && !sourceInspectionBusy && !deviceActionBusy
        && !downloadPanel.IsBusy && !officialDownloadPanel.IsBusy && !PartitionSelector.IsBusy;

    private void CreateInputAssociationPanel()
    {
        var body = new StackPanel();
        inputAssociationHeading = new TextBlock { FontSize = 19, FontWeight = FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap };
        body.Children.Add(inputAssociationHeading);
        inputAssociationNote = new TextBlock { TextWrapping = TextWrapping.Wrap, Foreground = BoilerBrush("#485B73"), Margin = new(0, 6, 0, 10) };
        body.Children.Add(inputAssociationNote);
        inputDropButton = new Button { Name = "InputDropTarget", MinHeight = 64, Margin = new(0), HorizontalContentAlignment = HorizontalAlignment.Left, HorizontalAlignment = HorizontalAlignment.Stretch };
        inputDropButton.Click += async (_, _) => await BrowseAssociationFilesAsync();
        body.Children.Add(inputDropButton);
        var actions = new WrapPanel { Margin = new(0, 6, 0, 0) };
        inputFolderButton = new Button { MinHeight = 48 };
        inputFolderButton.Click += async (_, _) => { var picker = new OpenFolderDialog(); if (picker.ShowDialog(this) == true) await ReceiveInputFilesAsync([picker.FolderName]); };
        inputClearButton = new Button { MinHeight = 48 };
        inputClearButton.Click += (_, _) => { if (inputInspectionBusy || inputAssociationBusy) return; inputAssignments.Clear(); inputQueue.Clear(); inputAssociationApplied = false; inputDeviceTrust!.IsChecked = false; inputResourceConsent!.IsChecked = false; importedResourcePreview = null; RefreshInputResourcePreview(); RenderInputAssignments(); };
        inputCancelButton = new Button { MinHeight = 48, Visibility = Visibility.Collapsed };
        inputCancelButton.Click += (_, _) => inputCancellation?.Cancel();
        actions.Children.Add(inputFolderButton); actions.Children.Add(inputClearButton); actions.Children.Add(inputCancelButton);
        inputRowsHost = new StackPanel { Margin = new(0, 8, 0, 0) };
        inputDeviceTrust = new CheckBox { Name = "InputDeviceTrust", MinHeight = 48, Visibility = Visibility.Collapsed };
        inputDeviceTrust.Checked += (_, _) => { trustedInputDevicePath = CurrentInputPlan.Files.GetValueOrDefault(InputRole.Device)?.Path; UpdateInputAssociationPlan(); };
        inputDeviceTrust.Unchecked += (_, _) => { trustedInputDevicePath = null; UpdateInputAssociationPlan(); }; body.Children.Add(inputDeviceTrust);
        inputResourceConsent = new CheckBox { Name = "InputResourceConsent", MinHeight = 48, Visibility = Visibility.Collapsed };
        inputResourceConsent.Checked += (_, _) => { confirmedInputResourcePath = CurrentInputPlan.Files.GetValueOrDefault(InputRole.Resource)?.Path; UpdateInputAssociationPlan(); };
        inputResourceConsent.Unchecked += (_, _) => { confirmedInputResourcePath = null; UpdateInputAssociationPlan(); }; body.Children.Add(inputResourceConsent);
        inputResourcePreview = new TextBlock { Name = "InputResourcePreview", TextWrapping = TextWrapping.Wrap, Margin = new(0, 8, 0, 8), Foreground = BoilerBrush("#485B73") };
        body.Children.Add(inputResourcePreview);
        inputAssociationStatus = new TextBlock { TextWrapping = TextWrapping.Wrap, Margin = new(0, 8, 0, 8), Foreground = BoilerBrush("#485B73") };
        body.Children.Add(inputAssociationStatus);
        inputApplyButton = new Button { Name = "AssociateInputs", MinHeight = 48, HorizontalAlignment = HorizontalAlignment.Left, Background = BoilerBrush("#2968D9"), Foreground = Brushes.White, IsEnabled = false };
        inputApplyButton.Click += async (_, _) => await ApplyInputAssociationsAsync();
        actions.Children.Insert(0, inputApplyButton); body.Children.Add(actions); body.Children.Add(inputRowsHost);
        inputAssociationPanel = new Border { Name = "InputAssociationPanel", Child = body, Padding = new(16), Margin = new(0, 0, 0, 20), Background = Brushes.White, BorderBrush = BoilerBrush("#A8B5C7"), BorderThickness = new(1), CornerRadius = new(8) };
        SourceInputReviewHost.Children.Add(inputAssociationPanel);
        WindowRoot.AllowDrop = true;
        WindowRoot.PreviewDragOver += (_, e) =>
        {
            var accepted = CanReceiveInputs && InputDropPaths(e.Data, out var paths);
            e.Effects = accepted ? DragDropEffects.Copy : DragDropEffects.None; e.Handled = true;
            HighlightInputDrop(accepted);
        };
        WindowRoot.DragLeave += (_, e) => { var p = e.GetPosition(WindowRoot); if (p.X <= 0 || p.Y <= 0 || p.X >= WindowRoot.ActualWidth || p.Y >= WindowRoot.ActualHeight) HighlightInputDrop(false); };
        WindowRoot.PreviewDrop += async (_, e) =>
        {
            HighlightInputDrop(false); e.Handled = true;
            if (InputDropPaths(e.Data, out var paths)) await ReceiveDroppedInputFilesAsync(paths);
        };
        Deactivated += (_, _) => HighlightInputDrop(false);
        InitializeDeviceFileDrop(); RefreshInputAssociationLanguage();
    }

    private void HighlightInputDrop(bool highlighted)
    {
        if (inputAssociationPanel == null || inputDragHighlighted == highlighted) return;
        inputDragHighlighted = highlighted;
        inputAssociationPanel.BorderBrush = BoilerBrush(highlighted ? "#2968D9" : "#A8B5C7");
        inputAssociationPanel.Background = BoilerBrush(highlighted ? "#EAF2FF" : "#FFFFFF");
    }
    private static bool InputDropPaths(IDataObject data, out string[] paths)
    {
        paths = [];
        try
        {
            paths = data.GetDataPresent(DataFormats.FileDrop) && data.GetData(DataFormats.FileDrop) is string[] items ? items : [];
            return paths.Length is > 0 and <= 32 && paths.All(path => path != null) && paths.Sum(path => (long)path.Length) <= 131072;
        }
        catch (Exception error) when (error is System.Runtime.InteropServices.ExternalException or InvalidOperationException) { return false; }
    }
    private bool IsInputPreviewElement(object? value)
    {
        for (var node = value as DependencyObject; node != null; node = node is FrameworkElement element ? element.Parent ?? VisualTreeHelper.GetParent(node) : null)
            if (ReferenceEquals(node, inputAssociationPanel)) return true;
        return false;
    }
    private async Task BrowseAssociationFilesAsync()
    {
        if (!CanReceiveInputs) return;
        var picker = new OpenFileDialog { Multiselect = true, Filter = "SteamOS images / device files|*.img;*.zip;*.bz2;*.raucb;*.sh;*.tar;*.gz;*.xz;*.tgz;*.txz|All files|*.*" };
        if (picker.ShowDialog(this) == true) await ReceiveInputFilesAsync(picker.FileNames);
    }
    private void ShowInputSourcePage()
    {
        if (devicePanel == null) { optionsPanel.SetMode(installMode); CreateDevicePanel(); devicePanel!.Visibility = installMode == "custom" ? Visibility.Visible : Visibility.Collapsed; }
        if (homeScreen != null) { WindowRoot.Children.Remove(homeScreen); homeScreen = null; }
        WizardShell.Visibility = Visibility.Visible; ShowStep(1); inputAssociationPanel!.BringIntoView();
    }
    private async Task ReceiveInputFilesAsync(IReadOnlyList<string> paths)
    {
        if (!CanReceiveInputs) return;
        ShowInputSourcePage();
        if (paths.Count is 0 or > 32 || paths.Sum(path => (long)path.Length) > 131072)
        { inputAssociationStatus!.Text = PartitionInventory.L("每次最多添加 32 个文件或文件夹。", "Add up to 32 files or folders at a time."); return; }
        if (inputAssociationApplied) { inputAssignments.Clear(); inputAssociationApplied = false; inputDeviceTrust!.IsChecked = false; inputResourceConsent!.IsChecked = false; importedResourcePreview = null; RefreshInputResourcePreview(); }
        foreach (var path in paths)
        {
            string key;
            try { key = Path.GetFullPath(path); } catch { key = path; }
            if (inputAssignments.Any(row => string.Equals(row.Path, key, StringComparison.OrdinalIgnoreCase))) continue;
            if (inputAssignments.Count >= 32) break;
            var row = new InputAssignment(key); inputAssignments.Add(row); inputQueue.Enqueue(row);
        }
        RenderInputAssignments();
        if (inputInspectionTask == null || inputInspectionTask.IsCompleted)
        {
            inputCancellation?.Dispose(); inputCancellation = new();
            inputInspectionTask = InspectQueuedInputsAsync(inputCancellation.Token);
        }
        await inputInspectionTask;
    }
    private async Task InspectQueuedInputsAsync(CancellationToken token)
    {
        inputInspectionBusy = true; UpdateInputAssociationPlan(); UpdateNavigationState();
        try
        {
            while (inputQueue.TryDequeue(out var row))
            {
                token.ThrowIfCancellationRequested();
                if (!inputAssignments.Contains(row)) continue;
                row.Input = await Task.Run(() => InputDiscovery.InspectAsync(row.Path, token), token);
                row.Role = InputAssociations.DefaultRole(row.Input.Kind); RenderInputAssignments();
            }
        }
        catch (OperationCanceledException)
        {
            inputQueue.Clear();
            foreach (var row in inputAssignments.Where(row => row.Input == null))
                row.Input = new(row.Path, InputKind.Unknown, Path.GetFileName(row.Path), "", null, Error: PartitionInventory.L("已取消识别，可移除后重新拖入。", "Inspection cancelled. Remove and drop again to retry."));
        }
        finally { inputInspectionBusy = false; RenderInputAssignments(); PreviewSingleInputDevice(); UpdateNavigationState(); }
    }

    private static string InputRoleLabel(InputRole role) => role switch
    {
        InputRole.Recovery => PartitionInventory.L("恢复镜像", "Recovery image"), InputRole.Image => PartitionInventory.L("完整 IMG 刷入", "Full IMG"),
        InputRole.Upgrade => PartitionInventory.L("升级包", "Upgrade bundle"), InputRole.Device => PartitionInventory.L("机型适配包", "Device package"), InputRole.Resource => PartitionInventory.L("资源池预览", "Pool preview"), _ => PartitionInventory.L("暂不关联", "Leave unassigned")
    };
    private static string InputDescription(DiscoveredInput input)
    {
        if (input.Stamp != null)
        {
            try { InputDiscovery.VerifyStamp(input); }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException)
            { return PartitionInventory.L("文件已改变，请移除后重新拖入识别。", "File changed after inspection. Remove it and drop it again to identify it."); }
        }
        if (input.RaucManifest is { } update)
            return update.IsSupportedByCurrentInstaller
                ? PartitionInventory.L($"升级包版本：SteamOS {update.Version}；安装时仍会重建并检查系统镜像。", $"Upgrade package version: SteamOS {update.Version}; the system image will still be rebuilt and checked during installation.")
                : PartitionInventory.L($"已识别 SteamOS {update.Version}；当前安装流程仅支持 SteamOS {OfficialSystem.Version}。", $"SteamOS {update.Version} detected; this installer currently supports only SteamOS {OfficialSystem.Version}.");
        if (input.Error != null) return input.Error;
        return input.Kind switch
        {
            InputKind.RecoveryImage => PartitionInventory.L("已识别恢复镜像，继续时检查系统内容。", "Recovery image identified; inspect system contents when continuing."),
            InputKind.DiskImage => PartitionInventory.L("已识别完整 A/B 镜像，可原样刷入。", "Complete A/B disk image identified for flashing."),
            InputKind.UpgradeRoot => PartitionInventory.L("已选择 rootfs.img，版本将在安装准备时识别。", "rootfs.img selected; its version will be identified during installation preparation."),
            InputKind.UpgradeBundle => PartitionInventory.L("已识别 SteamOS 升级包；继续时完整检查。", "SteamOS update bundle identified; complete inspection when continuing."),
            InputKind.DevicePackage => PartitionInventory.L("已识别机型包，关联时校验内容。", "Device package identified; verify its contents when associating."),
            InputKind.ResourcePool => PartitionInventory.L("外部资源池候选；确认后校验并复制到隔离预览目录，不执行或替换内置资源。", "External resource pool candidate. Confirm to validate and copy into an isolated preview; no execution or bundled resource replacement."),
            InputKind.DeviceScript => PartitionInventory.L("已识别适配脚本，需要本次信任。", "Device script identified; session trust is required."), _ => input.Detail
        };
    }
    private RaucManifestInfo? PendingRaucManifest
    {
        get
        {
            foreach (var input in inputAssignments.Where(row => !row.Applied && row.Role != InputRole.Ignore).Select(row => row.Input).Where(input => input?.RaucManifest != null))
            {
                try { InputDiscovery.VerifyStamp(input!); return input!.RaucManifest; }
                catch (IOException) { }
            }
            return null;
        }
    }
    private void RenderInputAssignments()
    {
        inputRowsHost!.Children.Clear();
        foreach (var row in inputAssignments)
        {
            var line = new DockPanel { Margin = new(0, 6, 0, 6), LastChildFill = true };
            var remove = new Button { Content = PartitionInventory.L("移除", "Remove"), MinHeight = 48, Padding = new(8), Margin = new(6, 0, 0, 0), IsEnabled = !inputAssociationBusy };
            remove.Click += (_, _) => { inputAssignments.Remove(row); inputAssociationApplied = false; if (row.Input?.Kind == InputKind.ResourcePool) { importedResourcePreview = null; RefreshInputResourcePreview(); } RenderInputAssignments(); };
            DockPanel.SetDock(remove, Dock.Right); line.Children.Add(remove);
            var role = new ComboBox { MinHeight = 48, Width = 150, Margin = new(10, 0, 0, 0), IsEnabled = row.Input != null && row.Input.Kind != InputKind.Unknown && !inputAssociationBusy };
            AutomationProperties.SetName(role, Path.GetFileName(row.Path) + " · " + PartitionInventory.L("关联位置", "Association"));
            foreach (var value in row.Input == null ? new[] { InputRole.Ignore } : InputAssociations.Roles(row.Input.Kind)) role.Items.Add(new ComboBoxItem { Content = InputRoleLabel(value), Tag = value });
            role.SelectedItem = role.Items.OfType<ComboBoxItem>().Single(item => (InputRole)item.Tag == row.Role);
            role.SelectionChanged += (_, e) => { e.Handled = true; row.Role = (InputRole)((ComboBoxItem)role.SelectedItem).Tag; row.Applied = false; inputAssociationApplied = false; if (row.Input?.Kind == InputKind.ResourcePool) { importedResourcePreview = null; RefreshInputResourcePreview(); } UpdateInputAssociationPlan(); RefreshSystemSourceCards(); };
            DockPanel.SetDock(role, Dock.Right); line.Children.Add(role);
            var text = new StackPanel { VerticalAlignment = VerticalAlignment.Center };
            var name = row.Input?.DeviceMetadata is JsonElement metadata && metadata.TryGetProperty("name", out var deviceName) ? LocalizedValue(deviceName) : row.Input?.Name ?? Path.GetFileName(row.Path);
            text.Children.Add(new TextBlock { Text = name, TextWrapping = TextWrapping.Wrap, TextTrimming = TextTrimming.CharacterEllipsis, MaxHeight = 42, FontWeight = FontWeights.SemiBold, ToolTip = row.Path });
            var unsupportedUpdate = row.Input?.RaucManifest is { IsSupportedByCurrentInstaller: false };
            var rowDescription = row.Input == null ? PartitionInventory.L("正在识别…", "Inspecting…") : InputDescription(row.Input);
            text.Children.Add(new TextBlock { Text = rowDescription, TextWrapping = TextWrapping.Wrap, TextTrimming = TextTrimming.CharacterEllipsis, FontSize = 12, LineHeight = 18, MaxHeight = 36, ToolTip = rowDescription, Foreground = BoilerBrush(row.Input?.Error != null || unsupportedUpdate ? "#A34727" : "#485B73"), Margin = new(0, 4, 0, 0) });
            line.Children.Add(text); inputRowsHost.Children.Add(line);
        }
        UpdateInputAssociationPlan(); RefreshSystemSourceCards();
    }
    private InputAssociationPlan CurrentInputPlan => InputAssociations.Resolve(inputAssignments, installMode, !string.IsNullOrWhiteSpace(ImagePath.Text), UsePrepared.IsChecked == true);
    private void UpdateInputAssociationPlan()
    {
        if (inputApplyButton == null) return;
        var plan = CurrentInputPlan; var busy = inputInspectionBusy || inputAssociationBusy;
        SyncInputDevicePreview(plan);
        if (inputDeviceTrust!.IsChecked == true && !string.Equals(trustedInputDevicePath, plan.Files.GetValueOrDefault(InputRole.Device)?.Path, StringComparison.OrdinalIgnoreCase)) inputDeviceTrust.IsChecked = false;
        inputDeviceTrust!.Visibility = plan.Files.ContainsKey(InputRole.Device) ? Visibility.Visible : Visibility.Collapsed;
        inputDeviceTrust.IsEnabled = !busy;
        if (inputResourceConsent!.IsChecked == true && !string.Equals(confirmedInputResourcePath, plan.Files.GetValueOrDefault(InputRole.Resource)?.Path, StringComparison.OrdinalIgnoreCase)) inputResourceConsent.IsChecked = false;
        inputResourceConsent.Visibility = plan.Files.ContainsKey(InputRole.Resource) ? Visibility.Visible : Visibility.Collapsed;
        inputResourceConsent.IsEnabled = !busy;
        inputApplyButton.Visibility = inputAssignments.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
        inputApplyButton.Content = plan.Files.ContainsKey(InputRole.Resource) ? PartitionInventory.L("校验并导入隔离预览", "Validate and import preview") : PartitionInventory.L("一键关联当前配置", "Associate with current configuration");
        inputApplyButton.IsEnabled = !busy && plan.Files.Count > 0 && plan.Conflict == null && (!plan.Files.ContainsKey(InputRole.Device) || inputDeviceTrust.IsChecked == true) && (!plan.Files.ContainsKey(InputRole.Resource) || inputResourceConsent.IsChecked == true) && !inputAssociationApplied;
        inputClearButton!.Visibility = inputAssignments.Count > 0 ? Visibility.Visible : Visibility.Collapsed; inputClearButton.IsEnabled = !busy;
        inputRowsHost!.IsEnabled = !inputAssociationBusy;
        inputCancelButton!.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
        inputDropButton!.IsEnabled = inputFolderButton!.IsEnabled = !inputAssociationBusy;
        RefreshSourceInputReview();
        RefreshSourceInputReview();
        if (inputAssociationBusy) return;
        inputAssociationStatus!.Foreground = BoilerBrush(plan.Conflict != null ? "#A34727" : "#485B73");
        inputAssociationStatus.Text = inputInspectionBusy ? PartitionInventory.L("正在按文件内容识别，可以继续添加文件。", "Identifying file contents. You can keep adding files.")
            : inputAssociationApplied ? plan.Files.ContainsKey(InputRole.Resource) ? PartitionInventory.L("外部资源池已通过校验并导入隔离预览；安装配置保持原有选择。", "External pool validated and imported for isolated preview; installation choices are retained.") : PartitionInventory.L("已关联到当前配置，可以继续检查或添加其他文件。", "Associated with the current configuration. Review it or add more files.")
            : plan.Files.ContainsKey(InputRole.Resource) && plan.Conflict == null ? PartitionInventory.L("仅导入资源池预览。未获得生产执行信任，不用于本次安装。", "Import resource pool preview only. No production execution trust is granted; it will not be used for installation.")
            : plan.Conflict ?? (plan.Files.Count == 0 ? "" : PartitionInventory.L($"将关联 {plan.Files.Count} 项；其他安装设置保持当前选择。", $"Associate {plan.Files.Count} item(s); retain the current choices for other settings.")
                + (plan.Mode != installMode ? "\n" + (plan.Mode == "imported" ? PartitionInventory.L("将切换到完整 IMG 原样刷入，组件配置保留供自定义构建使用。", "Switch to flashing the complete IMG; retain component choices for custom builds.") : PartitionInventory.L("将切换到自定义构建以使用机型适配。", "Switch to custom build to use device adaptation.")) : ""));
    }

    private async Task<string> NormalizeAssociationInputAsync(DiscoveredInput input, CancellationToken token)
    {
        if (Directory.Exists(input.Path) || input.Kind == InputKind.UpgradeRoot && !input.Path.EndsWith(".raucb", StringComparison.OrdinalIgnoreCase) || input.Kind == InputKind.UpgradeBundle && input.Path.EndsWith(".raucb", StringComparison.OrdinalIgnoreCase)) return input.Path;
        using var source = new FileStream(input.Path, FileMode.Open, FileAccess.Read, FileShare.Read, 1024 * 1024, true);
        var header = new byte[6]; var read = await source.ReadAsync(header, token); source.Position = 0;
        var zip = read >= 2 && header[0] == 'P' && header[1] == 'K';
        var bz = read >= 3 && header.AsSpan(0, 3).SequenceEqual("BZh"u8);
        var gz = read >= 2 && header[0] == 0x1f && header[1] == 0x8b;
        var xz = read >= 6 && header.AsSpan().SequenceEqual(new byte[] { 0xfd, 0x37, 0x7a, 0x58, 0x5a, 0x00 });
        var package = input.Kind is InputKind.DevicePackage or InputKind.ResourcePool;
        var suffix = input.Kind == InputKind.DeviceScript ? ".sh" : input.Kind == InputKind.UpgradeBundle ? ".raucb" : zip ? package ? ".zip" : ".img.zip" : bz ? package ? ".tar.bz2" : ".img.bz2" : gz ? ".tar.gz" : xz ? ".tar.xz" : package ? ".tar" : ".img";
        if (input.Path.EndsWith(suffix, StringComparison.OrdinalIgnoreCase) || input.Kind == InputKind.DevicePackage && input.Path.EndsWith(".zip", StringComparison.OrdinalIgnoreCase)
            || input.Kind == InputKind.DiskImage && zip && input.Path.EndsWith(".zip", StringComparison.OrdinalIgnoreCase)) return input.Path;
        var directory = Path.Combine(InstallationController.Work, "associated-inputs", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory); var snapshot = Path.Combine(directory, "input" + suffix); MediaSource.Ordinary(snapshot, InstallationController.Work, false);
        MediaSpace.RequireAvailable(directory, checked((ulong)source.Length + PartitionInventory.GiB));
        using var target = new FileStream(snapshot, FileMode.CreateNew, FileAccess.Write, FileShare.None, 1024 * 1024, true);
        try { await source.CopyToAsync(target, token); await target.FlushAsync(token); }
        catch { target.Dispose(); if (File.Exists(snapshot)) { MediaSource.Ordinary(snapshot, InstallationController.Work); File.Delete(snapshot); } throw; }
        InputDiscovery.VerifyStamp(input); return snapshot;
    }

    private async Task ApplyInputAssociationsAsync()
    {
        if (operation != null || DownloadsBusy || inputAssociationApplied) return;
        var plan = CurrentInputPlan;
        if (plan.Conflict != null || plan.Files.Count == 0 || plan.Files.ContainsKey(InputRole.Device) && inputDeviceTrust!.IsChecked != true
            || plan.Files.ContainsKey(InputRole.Resource) && inputResourceConsent!.IsChecked != true) return;
        inputAssociationBusy = true; inputCancellation?.Dispose(); inputCancellation = new(); var token = inputCancellation.Token;
        UpdateInputAssociationPlan(); UpdateNavigationState();
        try
        {
            inputAssociationStatus!.Text = PartitionInventory.L("正在校验并关联所选文件…", "Verifying and associating the selected files…");
            foreach (var input in plan.Files.Values) InputDiscovery.VerifyStamp(input);
            var paths = new Dictionary<InputRole, string>();
            foreach (var (role, input) in plan.Files) paths[role] = await NormalizeAssociationInputAsync(input, token);
            if (paths.TryGetValue(InputRole.Resource, out var resource))
            {
                importedResourcePreview = null; RefreshInputResourcePreview();
                var destination = Path.Combine(InstallationController.Work, "resource-imports", Guid.NewGuid().ToString("N"));
                var preview = await BoilerProtocol.RunAsync(["import-resource", resource, destination, "--trust-local-test"], token);
                if (preview.GetProperty("kind").GetString() != "LocalResourcePoolPreview" || preview.GetProperty("trust").GetString() != "local-session-test"
                    || preview.GetProperty("deployment_available").ValueKind != JsonValueKind.False || preview.GetProperty("directory").GetString() != destination)
                    throw new IOException("Invalid isolated resource preview response.");
                token.ThrowIfCancellationRequested(); InputDiscovery.VerifyStamp(plan.Files[InputRole.Resource]);
                importedResourcePreview = preview; RefreshInputResourcePreview();
                foreach (var row in inputAssignments.Where(row => row.Path == plan.Files[InputRole.Resource].Path)) row.Applied = true;
                inputAssociationApplied = true; return;
            }
            PreparedDeviceSelection? preparedDevice = null;
            if (paths.TryGetValue(InputRole.Device, out var device))
            {
                var deviceDirectory = Path.Combine(InstallationController.Work, "device-imports", Guid.NewGuid().ToString("N"));
                var result = await BoilerProtocol.RunAsync(["import-device", device, deviceDirectory, "--developer"], token);
                if (result.GetProperty("package").TryGetProperty("purpose", out var purpose) && purpose.GetString() == "maintenance") throw new IOException(PartitionInventory.L("维护包不能作为机型适配包使用。", "A maintenance package cannot be used as a device package."));
                preparedDevice = await PrepareDeviceSelectionAsync(deviceDirectory, "developer-session", result.GetProperty("manifest_sha256").GetString(), token, sessionTrusted: true);
            }
            if (InputPreparationGateForChecks != null) await InputPreparationGateForChecks(token);
            token.ThrowIfCancellationRequested();
            foreach (var input in plan.Files.Values) InputDiscovery.VerifyStamp(input);
            // Set only the fields covered by the proposal. Choosing a file is not
            // a new installation session and must not reset targets or options.
            session = null; reviewSeen = contentSeen = checkedAllSteps = false; Array.Clear(departedSteps); PhysicalConsent.IsChecked = false;
            if (plan.Mode == "custom" && installMode == "vanilla" && !paths.ContainsKey(InputRole.Image) && !paths.ContainsKey(InputRole.Recovery)) PromoteDeviceMode();
            else installMode = plan.Mode;
            if (paths.TryGetValue(InputRole.Image, out var image)) { UsePrepared.IsChecked = false; ImagePath.Text = image; Upgrade310.IsChecked = false; }
            if (paths.TryGetValue(InputRole.Recovery, out var recovery)) { UsePrepared.IsChecked = false; ImagePath.Text = recovery; }
            if (paths.TryGetValue(InputRole.Upgrade, out var upgrade)) { officialDownloadPanel.AssociateLocalSource(upgrade, plan.Files[InputRole.Upgrade]); if (!preserveSourceUpgradeSkip) Upgrade310.IsChecked = true; }
            if (preparedDevice != null)
            {
                developerPackages!.IsChecked = true; reviewedDeviceMetadata = null;
                CommitDeviceSelection(preparedDevice);
                developerPackages.Visibility = Visibility.Visible;
            }
            if (paths.ContainsKey(InputRole.Image) || paths.ContainsKey(InputRole.Recovery)) ResetImageInspection();
            if (paths.ContainsKey(InputRole.Upgrade)) ResetOfficialSourceInspection();
            devicePanel!.Visibility = installMode == "custom" ? Visibility.Visible : Visibility.Collapsed;
            officialDownloadPanel.SetLocalOnly(installMode == "local"); ComponentOptionsHost.IsEnabled = installMode is not ("imported" or "local");
            RecoveryDownloadCard.Visibility = installMode is "imported" or "local" ? Visibility.Collapsed : Visibility.Visible;
            foreach (var row in inputAssignments.Where(row => plan.Files.Values.Any(input => input.Path == row.Path))) row.Applied = true;
            inputAssociationApplied = true; RefreshBoilerNavigation();
            if (preparedDevice != null) ContinueAfterDeviceSelection(); else ShowStep(1);
            ConfigurationChanged();
        }
        catch (OperationCanceledException) { inputAssociationStatus!.Text = PartitionInventory.L("关联已取消。", "Association cancelled."); }
        catch (Exception error) { inputAssociationStatus!.Text = PartitionInventory.L("关联未完成：", "Association incomplete: ") + error.Message; }
        finally
        {
            var status = inputAssociationStatus!.Text;
            inputAssociationBusy = false; UpdateInputAssociationPlan();
            if (!inputAssociationApplied) inputAssociationStatus.Text = status;
            UpdateNavigationState();
        }
    }
    private void RefreshInputResourcePreview()
    {
        if (inputResourcePreview == null) return;
        inputResourcePreview.Text = importedResourcePreview is JsonElement preview ? PartitionInventory.L(
            $"已校验 {preview.GetProperty("resource_count").GetInt32()} 项资源、{preview.GetProperty("model_count").GetInt32()} 个机型。\n目录：{preview.GetProperty("directory").GetString()}\n目录摘要：{preview.GetProperty("catalog_sha256").GetString()}\n仅本次预览，不能部署。",
            $"Validated {preview.GetProperty("resource_count").GetInt32()} resources and {preview.GetProperty("model_count").GetInt32()} models.\nDirectory: {preview.GetProperty("directory").GetString()}\nCatalog SHA-256: {preview.GetProperty("catalog_sha256").GetString()}\nSession preview only; deployment unavailable.") : "";
    }
    private void RefreshInputAssociationLanguage()
    {
        if (inputAssociationPanel == null) return;
        inputAssociationHeading!.Text = PartitionInventory.L("添加文件，自动关联", "Add files and associate them");
        inputAssociationNote!.Text = PartitionInventory.L("拖入文件识别并关联；外部资源池文件夹或 ZIP 需单独确认，仅校验并导入隔离预览。", "Drop files to identify and associate them. Confirm an external resource folder or ZIP separately to validate and import an isolated preview.");
        inputDropButton!.Content = new TextBlock { Text = PartitionInventory.L("拖入 IMG、恢复镜像、SteamOS 升级包、机型包或资源池，也可点击多选", "Drop an IMG, recovery image, SteamOS update bundle, device package or resource pool, or click to select files"), TextWrapping = TextWrapping.Wrap };
        inputFolderButton!.Content = PartitionInventory.L("添加机型包或资源池文件夹", "Add device package or resource folder"); inputClearButton!.Content = PartitionInventory.L("清空列表", "Clear list"); inputCancelButton!.Content = PartitionInventory.L("取消", "Cancel");
        inputDeviceTrust!.Content = new TextBlock { Text = PartitionInventory.L("本次信任所选外部机型脚本，并关联到当前配置", "Trust the selected external device scripts for this session"), TextWrapping = TextWrapping.Wrap };
        inputResourceConsent!.Content = new TextBlock { Text = PartitionInventory.L("允许本次读取外部资源池，完整校验后复制到新的隔离目录，仅供预览", "Allow reading this external pool and copying validated files into a new isolated directory for session preview only"), TextWrapping = TextWrapping.Wrap };
        RefreshInputResourcePreview();
        inputApplyButton!.Content = PartitionInventory.L("一键关联当前配置", "Associate with current configuration"); RenderInputAssignments();
    }
    private void ResetInputAssociationSession()
    {
        inputSessionGeneration++; sourceIntakeActive = false;
        inputAssociationApplied = false; trustedInputDevicePath = null;
        if (inputDeviceTrust != null) inputDeviceTrust.IsChecked = false;
        if (inputResourceConsent != null) inputResourceConsent.IsChecked = false;
        importedResourcePreview = null; RefreshInputResourcePreview();
        UpdateInputAssociationPlan();
    }
}
