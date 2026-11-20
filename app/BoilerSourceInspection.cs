using System.IO;
using System.Text.Json;

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
    private OfficialSourceBinding? inspectedOfficialSource;
    private string? inspectedOfficialKey;
    private void ResetImageInspection()
    {
        inspectedSourceKey = inspectedSourceDigest = null;
        inspectedSourceInfo = null; inspectedSourceCapacity = null;
        PartitionSelector.DefaultCapacityGiB = 128;
    }
    private string SourceInfoKey()
    {
        var file = new FileInfo(InputDiscovery.SafePath(ImagePath.Text));
        if (file.Length == 0) throw new IOException(PartitionInventory.L("系统镜像为空，请重新选择。", "The system image is empty. Select another file."));
        return file.FullName + "|" + file.Length + "|" + file.LastWriteTimeUtc.Ticks + "|" + installMode;
    }
    private void ReviewSourceSelection(bool includeOfficial)
    {
        var sourceKey = SourceInfoKey();
        var officialKey = includeOfficial ? OfficialSystem.SourceKey(SelectedOfficialSourcePath()) : null;
        if (sourceKey != inspectedSourceKey)
        {
            ResetImageInspection();
            inspectedSourceKey = sourceKey;
        }
        if (officialKey != inspectedOfficialKey)
        {
            ResetOfficialSourceInspection();
            inspectedOfficialKey = officialKey;
        }
    }

    private void ResetOfficialSourceInspection()
    {
        inspectedOfficialSource = null;
        inspectedOfficialKey = null;
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
            var path = SelectedOfficialSourcePath();
            if (requireInspection && OfficialSystem.SourceKey(path) != inspectedOfficialKey)
                return PartitionInventory.L("请返回系统来源页，确认系统升级源后点击下一步。", "Return to System source, confirm the upgrade source, and continue.");
            return null;
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException) { return error.Message; }
    }
    private bool CurrentSourceReviewed
    {
        get
        {
            try { return inspectedSourceKey != null && SourceInfoKey() == inspectedSourceKey; }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException) { return false; }
        }
    }

    private async Task PrepareSourceBindingsAsync()
    {
        if (sourceInspectionBusy) throw new InvalidOperationException("Source verification is already in progress.");
        var needsImage = CurrentSourceReviewed && inspectedSourceDigest == null;
        var needsUpgrade = Upgrade310.IsChecked == true && installMode != "imported" && UsePrepared.IsChecked != true
            && inspectedOfficialKey != null && CurrentOfficialSourceBinding == null;
        if (!needsImage && !needsUpgrade) return;
        sourceInspectionBusy = true;
        sourceInspection = new CancellationTokenSource();
        WizardShell.IsEnabled = false;
        ValidationMessage.Text = PartitionInventory.L("正在校验安装源，请稍候…", "Verifying installation sources; please wait…");
        try
        {
            if (needsImage)
            {
                var key = SourceInfoKey();
                if (key != inspectedSourceKey) throw new IOException("The selected image changed after review.");
                var digest = await SourceFileDigest.ComputeAsync(ImagePath.Text, sourceInspection.Token);
                sourceInspection.Token.ThrowIfCancellationRequested();
                if (key != SourceInfoKey()) throw new IOException("The selected image changed during verification.");
                inspectedSourceDigest = digest.Sha256;
            }
            if (needsUpgrade)
            {
                var path = SelectedOfficialSourcePath();
                var key = OfficialSystem.SourceKey(path);
                if (key != inspectedOfficialKey) throw new IOException("The upgrade source changed after review.");
                var digest = await SourceFileDigest.ComputeAsync(path, sourceInspection.Token);
                sourceInspection.Token.ThrowIfCancellationRequested();
                if (key != OfficialSystem.SourceKey(SelectedOfficialSourcePath())) throw new IOException("The upgrade source changed during verification.");
                inspectedOfficialSource = new(digest.Path, digest.Sha256, digest.Length, digest.LastWriteUtcTicks);
            }
        }
        finally
        {
            sourceInspection.Dispose(); sourceInspection = null; sourceInspectionBusy = false;
            WizardShell.IsEnabled = true; UpdateNavigationState();
        }
    }
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
            return InstallerI18n.Format($"升级包版本：SteamOS {manifest.Version}；当前安装目标为 SteamOS {OfficialSystem.Version} · build {OfficialSystem.Build}。",
                $"Upgrade package version: SteamOS {manifest.Version}; the current installer target is SteamOS {OfficialSystem.Version} · build {OfficialSystem.Build}.");
        if (officialDownloadPanel.AssociatedSourcePath != null && Path.GetExtension(path).Equals(".raucb", StringComparison.OrdinalIgnoreCase))
            return PartitionInventory.L("升级包版本已变化或未识别；请重新拖入以检查当前文件。", "The update bundle version changed or is unavailable; drop the current file again to inspect it.");
        if (Path.GetExtension(path).Equals(".img", StringComparison.OrdinalIgnoreCase))
            return PartitionInventory.L("已选择 rootfs.img，版本将在安装准备时识别。",
                "rootfs.img selected; its version will be identified during installation preparation.");
        return InstallerI18n.Format($"Valve 官方下载源：SteamOS {OfficialSystem.Version} · build {OfficialSystem.Build}。",
            $"Valve official download source: SteamOS {OfficialSystem.Version} · build {OfficialSystem.Build}.");
    }
    private string? CurrentSourceDigest => CurrentSourceReviewed ? inspectedSourceDigest : null;
    private string SelectedOfficialSourcePath()
    {
        var path = officialDownloadPanel.AssociatedSourcePath ?? OfficialSystem.RequirePreparedSource();
        if (new FileInfo(InputDiscovery.SafePath(path)).Length == 0)
            throw new IOException(PartitionInventory.L("系统升级源为空，请重新选择。", "The upgrade source is empty. Select another file."));
        return path;
    }
}
