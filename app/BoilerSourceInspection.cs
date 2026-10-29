using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;

namespace OxpInstaller;

public partial class MainWindow
{
    private bool sourceInspectionBusy;
    private CancellationTokenSource? sourceInspection;
    private string? inspectedSourceKey;
    private string? inspectedSourceDigest;
    private JsonElement? inspectedSourceInfo;
    private long? inspectedSourceCapacity;
    private long? CurrentImportedCapacity => installMode == "imported" && CurrentSourceDigest != null ? inspectedSourceCapacity : null;
    private TextBlock? sourceInfoText;
    private OfficialSourceBinding? inspectedOfficialSource;
    private string? inspectedOfficialKey;
    private TextBlock? officialSourceInfoText;
    private string? inspectedSourceMd5, inspectedOfficialMd5;
    private StackPanel? sourceDigestControls, officialDigestControls;
    private void ResetImageInspection()
    {
        inspectedSourceKey = inspectedSourceDigest = null;
        inspectedSourceMd5 = null;
        inspectedSourceInfo = null; inspectedSourceCapacity = null;
        if (sourceInfoText != null) sourceInfoText.Text = "";
        PartitionSelector.DefaultCapacityGiB = physicalInstallation ? 64 : 128;
    }
    private string SourceInfoKey()
    {
        var file = new FileInfo(ImagePath.Text);
        return file.FullName + "|" + file.Length + "|" + file.LastWriteTimeUtc.Ticks + "|" + installMode;
    }
    private async Task InspectSourceAsync(bool includeOfficial = false)
    {
        var key = SourceInfoKey();
        if (key == inspectedSourceKey && inspectedSourceDigest != null)
        { if (includeOfficial) await InspectOfficialSourceAsync(); return; }
        sourceInfoText ??= new TextBlock { TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 12, 0, 12) };
        if (!RecoverySourceSection.Children.Contains(sourceInfoText)) RecoverySourceSection.Children.Add(sourceInfoText);
        sourceDigestControls = CreateDigestControls(RecoverySourceSection, sourceDigestControls);
        sourceInfoText.Text = PartitionInventory.L("正在计算镜像 MD5…", "Calculating image MD5…");
        sourceInspectionBusy = true; sourceInspection = new CancellationTokenSource(); UpdateNavigationState();
        try
        {
            var digest = await ReadSourceDigestAsync(ImagePath.Text, sourceInfoText, sourceDigestControls, sourceInspection.Token);
            if (key != SourceInfoKey()) throw new IOException("The selected image changed during MD5 calculation.");
            inspectedSourceInfo = null; inspectedSourceCapacity = null;
            inspectedSourceKey = key; inspectedSourceDigest = digest.Sha256; inspectedSourceMd5 = digest.Md5;
            sourceInfoText.Text = DescribeSourceDigest(digest);
        }
        catch (OperationCanceledException) { ResetImageInspection(); sourceInfoText.Text = PartitionInventory.L("MD5 计算已取消。", "MD5 calculation cancelled."); throw; }
        catch { ResetImageInspection(); throw; }
        finally { sourceDigestControls.Visibility = Visibility.Collapsed; sourceInspectionBusy = false; sourceInspection.Dispose(); sourceInspection = null; UpdateNavigationState(); }
        if (includeOfficial) await InspectOfficialSourceAsync();
    }
    private void ResetOfficialSourceInspection()
    {
        inspectedOfficialSource = null; inspectedOfficialKey = null;
        inspectedOfficialMd5 = null;
        if (officialSourceInfoText != null) officialSourceInfoText.Text = "";
    }
    private OfficialSourceBinding? CurrentOfficialSourceBinding
    {
        get
        {
            try
            {
                var path = SelectedOfficialSourcePath();
                return inspectedOfficialSource != null && OfficialSystem.SourceKey(path) == inspectedOfficialKey ? inspectedOfficialSource : null;
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException) { return null; }
        }
    }
    private string? OfficialSourceIssue(bool requireInspection)
    {
        try
        {
            _ = SelectedOfficialSourcePath();
            if (requireInspection && CurrentOfficialSourceBinding == null)
                return PartitionInventory.L("请在系统来源页点击下一步，计算系统升级源的 MD5。", "Continue from System source to calculate the system upgrade source MD5.");
            return null;
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException) { return error.Message; }
    }
    private async Task InspectOfficialSourceAsync()
    {
        if (CurrentOfficialSourceBinding != null) return;
        officialSourceInfoText ??= new TextBlock { TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 12, 0, 12) };
        if (!OfficialSourceSection.Children.Contains(officialSourceInfoText)) OfficialSourceSection.Children.Add(officialSourceInfoText);
        officialDigestControls = CreateDigestControls(OfficialSourceSection, officialDigestControls);
        officialSourceInfoText.Text = PartitionInventory.L("正在计算系统升级源 MD5…", "Calculating system upgrade source MD5…");
        sourceInspectionBusy = true; sourceInspection = new CancellationTokenSource(); UpdateNavigationState();
        try
        {
            var path = SelectedOfficialSourcePath(); var key = OfficialSystem.SourceKey(path);
            var digest = await ReadSourceDigestAsync(path, officialSourceInfoText, officialDigestControls, sourceInspection.Token);
            if (key != OfficialSystem.SourceKey(path)) throw new IOException("The upgrade source changed during MD5 calculation.");
            inspectedOfficialKey = key;
            inspectedOfficialSource = new(digest.Path, digest.Sha256, digest.Length, digest.LastWriteUtcTicks);
            inspectedOfficialMd5 = digest.Md5;
            officialSourceInfoText.Text = DescribeSourceDigest(digest);
        }
        catch (OperationCanceledException) { ResetOfficialSourceInspection(); officialSourceInfoText.Text = PartitionInventory.L("MD5 计算已取消。", "MD5 calculation cancelled."); throw; }
        catch { ResetOfficialSourceInspection(); throw; }
        finally { officialDigestControls.Visibility = Visibility.Collapsed; sourceInspectionBusy = false; sourceInspection.Dispose(); sourceInspection = null; UpdateNavigationState(); }
    }
    private StackPanel CreateDigestControls(Panel host, StackPanel? controls)
    {
        if (controls == null)
        {
            controls = new StackPanel { Margin = new Thickness(0, 0, 0, 12) };
            controls.Children.Add(new ProgressBar { Height = 7, Minimum = 0, Maximum = 100 });
            var cancel = new Button { Content = PartitionInventory.L("取消校验", "Cancel check"), HorizontalAlignment = HorizontalAlignment.Left,
                MinHeight = 48, Margin = new Thickness(0, 8, 0, 0) };
            cancel.Click += (_, _) => { sourceInspection?.Cancel(); cancel.IsEnabled = false; };
            controls.Children.Add(cancel); host.Children.Add(controls);
        }
        ((ProgressBar)controls.Children[0]).Value = 0;
        ((Button)controls.Children[1]).IsEnabled = true;
        controls.Visibility = Visibility.Visible;
        return controls;
    }
    private async Task<SourceFileDigest> ReadSourceDigestAsync(string path, TextBlock text, StackPanel controls, CancellationToken token)
    {
        var reading = true;
        var progress = new Progress<SourceDigestProgress>(update =>
        {
            if (!reading || token.IsCancellationRequested) return;
            ((ProgressBar)controls.Children[0]).Value = update.Total > 0 ? update.Completed * 100d / update.Total : 0;
            text.Text = PartitionInventory.L("正在计算 MD5", "Calculating MD5")
                + $" · {update.Completed * 100d / update.Total:F1}% · {update.Completed / 1048576d:F1} / {update.Total / 1048576d:F1} MiB · {update.BytesPerSecond / 1048576d:F1} MiB/s";
        });
        try
        {
            var digest = await SourceFileDigest.ComputeAsync(path, update => ((IProgress<SourceDigestProgress>)progress).Report(update), token);
            token.ThrowIfCancellationRequested();
            return digest;
        }
        finally { reading = false; }
    }
    private static string DescribeSourceDigest(SourceFileDigest digest) => Path.GetFileName(digest.Path)
        + $" · {digest.Length / 1073741824d:F2} GiB\nMD5: {digest.Md5}\n"
        + (digest.ComparedToReference ? PartitionInventory.L("MD5 与参考值一致。", "MD5 matches the reference value.")
            : PartitionInventory.L("已计算 MD5；未提供参考值，未核对匹配。", "MD5 calculated; no reference value was provided for comparison."));
    private static string DescribeSourceInfo(JsonElement info)
    {
        var slots = info.GetProperty("slots").EnumerateObject().Select(slot => slot.Name + ": SteamOS " + slot.Value.GetProperty("version").GetString()
            + " · " + slot.Value.GetProperty("build").GetString() + " · " + string.Join(", ", slot.Value.GetProperty("kernels").EnumerateArray().Select(k => k.GetString())));
        return (info.TryGetProperty("layout", out var layout) ? layout.GetString() : "steamos-ab") + "\n" + string.Join("\n", slots);
    }
    private string OfficialSourceVersionDescription(string path)
    {
        var manifest = officialDownloadPanel.CurrentAssociatedInspection?.RaucManifest;
        if (manifest != null)
            return PartitionInventory.L($"升级包版本：SteamOS {manifest.Version}；当前安装目标为 SteamOS {OfficialSystem.Version} · build {OfficialSystem.Build}。",
                $"Upgrade package version: SteamOS {manifest.Version}; the current installer target is SteamOS {OfficialSystem.Version} · build {OfficialSystem.Build}.");
        if (officialDownloadPanel.AssociatedSourcePath != null && Path.GetExtension(path).Equals(".raucb", StringComparison.OrdinalIgnoreCase))
            return PartitionInventory.L("升级包版本已变化或未识别；请重新拖入以检查当前文件。", "The update bundle version changed or is unavailable; drop the current file again to inspect it.");
        if (Path.GetExtension(path).Equals(".img", StringComparison.OrdinalIgnoreCase))
            return PartitionInventory.L("已选择 rootfs.img，版本将在安装准备时识别。",
                "rootfs.img selected; its version will be identified during installation preparation.");
        return PartitionInventory.L($"Valve 官方下载源：SteamOS {OfficialSystem.Version} · build {OfficialSystem.Build}。",
            $"Valve official download source: SteamOS {OfficialSystem.Version} · build {OfficialSystem.Build}.");
    }
    private string? CurrentSourceDigest => inspectedSourceKey != null && File.Exists(ImagePath.Text) && SourceInfoKey() == inspectedSourceKey ? inspectedSourceDigest : null;
    private string SelectedOfficialSourcePath()
    {
        var path = officialDownloadPanel.AssociatedSourcePath ?? OfficialSystem.RequirePreparedSource();
        _ = OfficialSystem.SourceKey(path);
        return path;
    }
}
