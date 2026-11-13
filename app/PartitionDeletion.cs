using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Security.Principal;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace OxpInstaller;

public sealed record PartitionDeleteRequest(string Snapshot, bool Example, int Disk, long Start, string Language, long[]? SteamOsStarts = null);

public static class PartitionDeletion
{
    private static string L(string zh, string en) => PartitionInventory.L(zh, en);
    internal static bool Allowed(JsonElement disk, JsonElement part, string? steamOsRole = null)
    {
        bool IsFalse(JsonElement value, string key) => value.TryGetProperty(key, out var flag) && flag.ValueKind == JsonValueKind.False;
        // This only consumes the live policy's allow result, never substitutes
        // role recognition for its host/content checks. The helper rechecks it.
        // Recognition is name + count + contiguity; the native attribute proof is
        // no longer required here, while the live policy still binds the range.
        var ownBoot = steamOsRole is "esp" or "efi-A" or "efi-B";
        return disk.GetProperty("style").GetString() == "GPT"
            && disk.TryGetProperty("guid", out var diskId) && Guid.TryParse(diskId.GetString(), out var diskGuid) && diskGuid != Guid.Empty
            && part.TryGetProperty("guid", out var partId) && Guid.TryParse(partId.GetString(), out var partGuid) && partGuid != Guid.Empty
            && disk.GetProperty("bus").GetString() is "NVMe" or "SATA" or "USB" or "SD" or "MMC"
            && IsFalse(disk, "isOffline") && IsFalse(disk, "isReadOnly")
            && part.TryGetProperty("deletionAllowed", out var allowed) && allowed.ValueKind == JsonValueKind.True
            && IsFalse(part, "isBoot") && IsFalse(part, "isSystem")
            && (ownBoot || IsFalse(part, "isHidden") || part.TryGetProperty("nativeUnrecognizedSafe", out var nativeSafe) && nativeSafe.ValueKind == JsonValueKind.True
                && part.GetProperty("filesystem").GetString() is "" or "Unknown" or "RAW"
                && part.GetProperty("gptType").GetString()?.Trim('{','}').ToUpperInvariant() != "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7")
            && Guid.TryParse(part.GetProperty("gptType").GetString(), out var type)
            && type != Guid.Empty && (!ProtectedTypes.Contains(type) || ownBoot && type == new Guid("C12A7328-F81F-11D2-BA4B-00A0C93EC93B"));
    }

    private static readonly HashSet<Guid> ProtectedTypes = [
        new("C12A7328-F81F-11D2-BA4B-00A0C93EC93B"), // EFI
        new("E3C9E316-0B5C-4DB8-817D-F92DF00215AE"), // MSR
        new("DE94BBA4-06D1-4D40-A16A-BFD50179D6AC"), // Windows recovery
        new("21686148-6449-6E6F-744E-656564454649") // BIOS boot
    ];

    internal static (InventoryDisk Disk, PartitionRow Row) Validate(PartitionDeleteRequest request)
    {
        var (disk, rows) = ValidateTargets(request);
        return (disk, rows[0]);
    }
    internal static (InventoryDisk Disk, PartitionRow[] Rows) ValidateTargets(PartitionDeleteRequest request)
    {
        var disk = PartitionInventory.Read(request.Snapshot).Single(d => d.Number == request.Disk);
        var starts = request.SteamOsStarts ?? [request.Start];
        if (starts.Length is < 1 or > 128 || starts.Distinct().Count() != starts.Length || starts.Min() != request.Start)
            throw new InvalidOperationException("Invalid deletion selection.");
        var rows = starts.Order().Select(start => disk.Rows.SingleOrDefault(r => r.Start == start)
            ?? throw new InvalidOperationException("Selected partition is missing.")).ToArray();
        if (rows.Any(r => !r.CanDelete || request.SteamOsStarts != null && r.SteamOsRole == null))
            throw new InvalidOperationException(L("所选分区受保护，或无法确认属于 SteamOS 布局。请刷新后重新选择。", "A selected partition is protected or cannot be identified as part of a SteamOS layout. Refresh and select again."));
        return (disk, rows);
    }

    internal static string Simulate(PartitionDeleteRequest request)
    {
        if (!request.Example) throw new InvalidOperationException("Simulation requires an example layout.");
        var (_, rows) = ValidateTargets(request);
        var root = JsonNode.Parse(request.Snapshot)!.AsArray();
        var disk = root.Single(d => (int)d!["number"]! == request.Disk)!;
        var parts = disk["partitions"]!.AsArray();
        foreach (var row in rows) parts.Remove(parts.Single(p => (long)p!["offset"]! == row.Start));
        return root.ToJsonString();
    }

    internal static Window Confirmation(PartitionDeleteRequest request)
    {
        var (disk, rows) = ValidateTargets(request);
        var bulk = request.SteamOsStarts != null;
        var window = new Window { Title = bulk ? L("删除 SteamOS 分区", "Delete SteamOS partitions") : L("删除选定分区", "Delete selected partition"), Width = 580, SizeToContent = SizeToContent.Height,
            MaxHeight = SystemParameters.WorkArea.Height, WindowStartupLocation = WindowStartupLocation.CenterScreen,
            ResizeMode = ResizeMode.NoResize, FontSize = 15, Background = Brushes.White, Foreground = new SolidColorBrush(Color.FromRgb(25,43,67)) };
        var panel = new StackPanel { Margin = new Thickness(24) };
        void Text(string value, bool title = false) => panel.Children.Add(new TextBlock { Text = value, TextWrapping = TextWrapping.Wrap,
            FontSize = title ? 21 : 15, FontWeight = title ? FontWeights.SemiBold : FontWeights.Normal, Margin = new Thickness(0,0,0,16) });
        Text(request.Example ? L("模拟删除 · 不影响真实磁盘", "Simulate deletion · no real disk changes") : L("所选分区中的所有文件都将丢失", "All files in the selected partitions will be lost"), true);
        Text(disk.Title + "\n" + L($"已选 {rows.Length} 个分区 · 共 {PartitionInventory.SizeText(rows.Sum(r => r.Size))}", $"{rows.Length} selected partitions · {PartitionInventory.SizeText(rows.Sum(r => r.Size))} total"));
        foreach (var row in rows) Text(row.Name + " · " + row.Type + " · " + row.CapacityText + "\n" + L($"字节范围：{row.Start}–{checked(row.Start + row.Size)}（不含末尾）", $"Bytes: {row.Start}–{checked(row.Start + row.Size)} (exclusive end)"));
        Text(L("删除的是整个分区，包括系统、游戏和个人文件。确认后立即删除，不会等到开始安装；返回或退出不能撤销。", "Whole partitions are erased, including their system, games and personal files. Deletion happens immediately, before installation. Going back or exiting cannot undo it."));
        if (bulk) Text(L("以上所选 SteamOS 分区（包括其自有启动分区）将删除；未选分区、Windows / 宿主 / 恢复分区保持不变。分区将逐个删除；中途失败可能已删除部分分区，程序会停止并刷新布局。", "Selected SteamOS partitions, including their own boot partitions, will be deleted. Unselected, Windows, host and recovery partitions are kept. Partitions are deleted one at a time; a failure can leave a partial deletion. The process stops and refreshes the layout."));
        var consent = new CheckBox { Name = "DeleteConsent", Content = new TextBlock { Text = L("我已备份需要的文件，确认清空以上全部所选分区。", "I have backed up my files and agree to erase every selected partition shown above."), TextWrapping = TextWrapping.Wrap } };
        panel.Children.Add(consent);
        var actions = new WrapPanel { HorizontalAlignment = HorizontalAlignment.Right, Margin = new Thickness(0,16,0,0) };
        var cancel = new Button { Content = L("取消", "Cancel"), IsCancel = true, IsDefault = true };
        var confirm = new Button { Name = "DeleteConfirm", Content = request.Example ? L("模拟删除", "Simulate deletion") : bulk ? L($"立即删除 {rows.Length} 个分区", $"Delete {rows.Length} partitions now") : L("立即删除此分区", "Delete partition now"), IsEnabled = false,
            Background = new SolidColorBrush(Color.FromRgb(163,47,35)), Foreground = Brushes.White };
        consent.Checked += (_, _) => confirm.IsEnabled = true; consent.Unchecked += (_, _) => confirm.IsEnabled = false;
        confirm.Click += (_, _) => { if (consent.IsChecked == true) window.DialogResult = true; };
        actions.Children.Add(cancel); actions.Children.Add(confirm); panel.Children.Add(actions);
        window.Content = new ScrollViewer { Content = panel, Background = Brushes.White, VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
        window.Loaded += (_, _) => cancel.Focus();
        return window;
    }

    // The elevated process owns final confirmation; no command-line switch bypasses it.
    public static async Task<int> ExecuteHelperAsync(string path, string expectedDigest)
    {
        path = Path.GetFullPath(path); MediaSource.Ordinary(path, InstallerWorkspace.DirectoryPath);
        if (Path.GetFileName(path) != "delete-request.json") throw new IOException("Invalid helper request path.");
        var request = HelperRequestBinding.Parse<PartitionDeleteRequest>(HelperRequestBinding.ReadVerified(path, HelperRequestBinding.Delete, expectedDigest, HelperRequestBinding.SnapshotLimit));
        Application.Current.ShutdownMode = ShutdownMode.OnExplicitShutdown;
        InstallerI18n.SetLanguage(request.Language);
        try
        {
            if (request.Example || !new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator)) throw new IOException("Real deletion requires administrator access.");
            Validate(request);
            // The script performs a read-only rebind before the dialog, then repeats it at deletion.
            await Backend.PowerShellAsync("delete-partition.ps1", ["-RequestPath", path, "-ExpectedDigest", expectedDigest, "-ValidateOnly"], _ => { }, CancellationToken.None);
            if (Confirmation(request).ShowDialog() != true) return 2;
            await Backend.PowerShellAsync("delete-partition.ps1", ["-RequestPath", path, "-ExpectedDigest", expectedDigest], _ => { }, CancellationToken.None);
            return 0;
        }
        catch (Exception error)
        {
            await File.WriteAllTextAsync(Path.Combine(Path.GetDirectoryName(path)!, "error.txt"), error.Message);
            return 1;
        }
    }

    internal static async Task<bool> RunAsync(PartitionDeleteRequest request)
    {
        if (request.Example) throw new InvalidOperationException("Example requests cannot reach the physical helper.");
        Validate(request);
        var directory = Path.Combine(InstallerWorkspace.DirectoryPath, "partition-delete-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(directory);
        var path = Path.Combine(directory, "delete-request.json");
        using var boundJob = HelperRequestBinding.Create(path, HelperRequestBinding.Delete, request);
        var start = new ProcessStartInfo(Path.Combine(Backend.Root, "runtime/dotnet/dotnet.exe")) { UseShellExecute = true, Verb = "runas", WindowStyle = ProcessWindowStyle.Hidden };
        start.ArgumentList.Add(typeof(App).Assembly.Location); start.ArgumentList.Add("--delete-partition-helper"); start.ArgumentList.Add(path); start.ArgumentList.Add(boundJob.Digest);
        try
        {
            using var process = Process.Start(start) ?? throw new IOException("Deletion helper did not start.");
            await process.WaitForExitAsync();
            if (process.ExitCode == 2) return false;
            if (process.ExitCode != 0) throw new IOException(File.Exists(Path.Combine(directory, "error.txt")) ? await File.ReadAllTextAsync(Path.Combine(directory, "error.txt")) : L("删除进程未正常完成，请重新读取磁盘核对。", "Deletion did not finish normally. Refresh disks to check the layout."));
            return true;
        }
        catch (Win32Exception error) when (error.NativeErrorCode == 1223) { return false; }
    }
}
