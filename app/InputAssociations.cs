using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Media;
using Microsoft.Win32;

namespace OxpInstaller;

internal enum InputRole { Ignore, Recovery, Image, Upgrade, Device }
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
        InputKind.DevicePackage or InputKind.DeviceScript => InputRole.Device, _ => InputRole.Ignore
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
        if (files.ContainsKey(InputRole.Image) && files.Keys.Any(role => role != InputRole.Image))
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
    private Button? inputApplyButton, inputClearButton, inputCancelButton, inputFolderButton;
    private readonly List<InputAssignment> inputAssignments = new();
    private readonly Queue<InputAssignment> inputQueue = new();
    private CancellationTokenSource? inputCancellation;
    private Task? inputInspectionTask;
    private bool inputInspectionBusy, inputAssociationBusy;
    private bool inputDragHighlighted;
    private bool inputAssociationApplied;
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
        inputClearButton.Click += (_, _) => { if (inputInspectionBusy || inputAssociationBusy) return; inputAssignments.Clear(); inputQueue.Clear(); inputAssociationApplied = false; RenderInputAssignments(); };
        inputCancelButton = new Button { MinHeight = 48, Visibility = Visibility.Collapsed };
        inputCancelButton.Click += (_, _) => inputCancellation?.Cancel();
        actions.Children.Add(inputFolderButton); actions.Children.Add(inputClearButton); actions.Children.Add(inputCancelButton);
        inputRowsHost = new StackPanel { Margin = new(0, 8, 0, 0) };
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
        var generation = inputSessionGeneration;
        ShowInputSourcePage();
        if (paths.Count is 0 or > 32 || paths.Sum(path => (long)path.Length) > 131072)
        { inputAssociationStatus!.Text = PartitionInventory.L("每次最多添加 32 个文件或文件夹。", "Add up to 32 files or folders at a time."); return; }
        if (inputAssociationApplied) { inputAssignments.Clear(); inputAssociationApplied = false; }
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
        if (generation != inputSessionGeneration) return;
        var plan = CurrentInputPlan;
        if (!inputCancellation!.IsCancellationRequested && !inputAssociationBusy && plan.Conflict == null && plan.Files.Count > 0
            && inputAssignments.All(row => row.Input != null))
        {
            preserveSourceUpgradeSkip = skipUpgradeChosen && Upgrade310.IsChecked != true;
            try { await ApplyInputAssociationsAsync(); }
            finally { preserveSourceUpgradeSkip = false; }
        }
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
                row.Role = InputAssociations.DefaultRole(row.Input.Kind);
                if (row.Role == InputRole.Device) { deviceChoiceChanged = true; deviceDetectionState = "manual"; }
                RenderInputAssignments();
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
        InputRole.Upgrade => PartitionInventory.L("升级包", "Upgrade bundle"), InputRole.Device => PartitionInventory.L("机型适配包", "Device package"), _ => PartitionInventory.L("暂不关联", "Leave unassigned")
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
            InputKind.DeviceScript => PartitionInventory.L("已识别适配脚本，导入时校验内容。", "Device script identified; verify its contents during import."), _ => input.Detail
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
            remove.Click += (_, _) => { inputAssignments.Remove(row); inputAssociationApplied = false; RenderInputAssignments(); };
            DockPanel.SetDock(remove, Dock.Right); line.Children.Add(remove);
            var role = new ComboBox { MinHeight = 48, Width = 150, Margin = new(10, 0, 0, 0), IsEnabled = row.Input != null && row.Input.Kind != InputKind.Unknown && !inputAssociationBusy };
            AutomationProperties.SetName(role, Path.GetFileName(row.Path) + " · " + PartitionInventory.L("关联位置", "Association"));
            foreach (var value in row.Input == null ? new[] { InputRole.Ignore } : InputAssociations.Roles(row.Input.Kind)) role.Items.Add(new ComboBoxItem { Content = InputRoleLabel(value), Tag = value });
            role.SelectedItem = role.Items.OfType<ComboBoxItem>().Single(item => (InputRole)item.Tag == row.Role);
            role.SelectionChanged += (_, e) => { e.Handled = true; row.Role = (InputRole)((ComboBoxItem)role.SelectedItem).Tag; row.Applied = false; inputAssociationApplied = false; UpdateInputAssociationPlan(); RefreshSystemSourceCards(); };
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
        inputApplyButton.Visibility = inputAssignments.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
        inputApplyButton.IsEnabled = !busy && plan.Files.Count > 0 && plan.Conflict == null && !inputAssociationApplied;
        inputClearButton!.Visibility = inputAssignments.Count > 0 ? Visibility.Visible : Visibility.Collapsed; inputClearButton.IsEnabled = !busy;
        inputRowsHost!.IsEnabled = !inputAssociationBusy;
        inputCancelButton!.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
        inputDropButton!.IsEnabled = inputFolderButton!.IsEnabled = !inputAssociationBusy;
        RefreshSourceInputReview();
        if (inputAssociationBusy) return;
        inputAssociationStatus!.Foreground = BoilerBrush(plan.Conflict != null ? "#A34727" : "#485B73");
        inputAssociationStatus.Text = inputInspectionBusy ? PartitionInventory.L("正在按文件内容识别，可以继续添加文件。", "Identifying file contents. You can keep adding files.")
            : inputAssociationApplied ? PartitionInventory.L("已关联到当前配置，可以继续检查或添加其他文件。", "Associated with the current configuration. Review it or add more files.")
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
        var package = input.Kind == InputKind.DevicePackage;
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
        if (plan.Conflict != null || plan.Files.Count == 0) return;
        inputAssociationBusy = true; inputCancellation?.Dispose(); inputCancellation = new(); var token = inputCancellation.Token;
        UpdateInputAssociationPlan(); UpdateNavigationState();
        try
        {
            inputAssociationStatus!.Text = PartitionInventory.L("正在校验并关联所选文件…", "Verifying and associating the selected files…");
            foreach (var input in plan.Files.Values) InputDiscovery.VerifyStamp(input);
            var paths = new Dictionary<InputRole, string>();
            foreach (var (role, input) in plan.Files) paths[role] = await NormalizeAssociationInputAsync(input, token);
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
                reviewedDeviceMetadata = null;
                updatingDeviceList = true;
                try { deviceList!.SelectedIndex = 0; }
                finally { updatingDeviceList = false; }
                ClearModelGuide();
                deviceSourceBundled = false; deviceSourceChosen = true; deviceChoiceChanged = true;
                importedDeviceFileName = Path.GetFileName(paths[InputRole.Device].TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar));
                CommitDeviceSelection(preparedDevice);
                PopulateDeviceList();
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
    private void RefreshInputAssociationLanguage()
    {
        if (inputAssociationPanel == null) return;
        inputAssociationHeading!.Text = PartitionInventory.L("添加文件，自动关联", "Add files and associate them");
        inputAssociationNote!.Text = PartitionInventory.L("把相关文件一起拖入窗口，自动识别并填入当前安装配置。", "Drop related files anywhere in this window to identify and associate them with this installation.");
        inputDropButton!.Content = new TextBlock { Text = PartitionInventory.L("拖入 IMG、恢复镜像、SteamOS 升级包或机型包，也可点击多选", "Drop an IMG, recovery image, SteamOS update bundle or device package, or click to select files"), TextWrapping = TextWrapping.Wrap };
        inputFolderButton!.Content = PartitionInventory.L("添加机型包文件夹", "Add a device package folder"); inputClearButton!.Content = PartitionInventory.L("清空列表", "Clear list"); inputCancelButton!.Content = PartitionInventory.L("取消", "Cancel");
        inputApplyButton!.Content = PartitionInventory.L("一键关联当前配置", "Associate with current configuration"); RenderInputAssignments();
    }
    private void ResetInputAssociationSession()
    {
        inputSessionGeneration++; sourceIntakeActive = false;
        inputAssignments.Clear(); inputQueue.Clear();
        inputAssociationApplied = false;
        inputRowsHost?.Children.Clear();
        UpdateInputAssociationPlan();
    }
}
