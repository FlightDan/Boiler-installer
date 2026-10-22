using System.IO;
using System.Security.Cryptography;
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
    private TextBlock? sourceInfoText;
    private string SourceInfoKey()
    {
        var file = new FileInfo(ImagePath.Text);
        return file.FullName + "|" + file.Length + "|" + file.LastWriteTimeUtc.Ticks + "|" + installMode;
    }
    private async Task InspectSourceAsync()
    {
        var key = SourceInfoKey();
        if (key == inspectedSourceKey && inspectedSourceInfo != null) return;
        sourceInfoText ??= new TextBlock { TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 12, 0, 12) };
        if (!RecoverySourceSection.Children.Contains(sourceInfoText)) RecoverySourceSection.Children.Add(sourceInfoText);
        sourceInfoText.Text = PartitionInventory.L("正在只读检查镜像版本、分区和内核…", "Inspecting image version, partitions and kernels read-only…");
        sourceInspectionBusy = true; sourceInspection = new CancellationTokenSource(); UpdateNavigationState();
        try
        {
            var snapshotDirectory = Path.Combine(InstallationController.Work, "source-inspection-" + Guid.NewGuid().ToString("N"));
            var snapshot = await BoilerImage.SnapshotAsync(ImagePath.Text, snapshotDirectory, sourceInspection.Token);
            var digest = snapshot.Sha256;
            var image = snapshot.Path;
            if (installMode == "imported" && image.EndsWith(".zip", StringComparison.OrdinalIgnoreCase))
            {
                var directory = Path.Combine(InstallationController.Work, "source-inspection-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(directory);
                image = (await BoilerImage.ImportAsync(image, directory, _ => { }, sourceInspection.Token)).Path;
            }
            else if (UsePrepared.IsChecked != true) image = await Backend.ImportAsync(image, _ => { }, sourceInspection.Token, allowPrepared: installMode == "imported");
            var output = await Backend.GuestAsync(UsePrepared.IsChecked == true ? "physical-readiness" : "source-info", image, false, _ => { }, sourceInspection.Token);
            var value = output.Split('\n').Select(line => line.Trim()).Single(line => line.StartsWith("BOILER_SOURCE_JSON=", StringComparison.Ordinal));
            using var doc = JsonDocument.Parse(value["BOILER_SOURCE_JSON=".Length..]);
            inspectedSourceInfo = doc.RootElement.Clone(); inspectedSourceKey = key; inspectedSourceDigest = digest;
            sourceInfoText.Text = DescribeSourceInfo(inspectedSourceInfo.Value);
        }
        catch { inspectedSourceKey = inspectedSourceDigest = null; inspectedSourceInfo = null; throw; }
        finally { sourceInspectionBusy = false; sourceInspection.Dispose(); sourceInspection = null; UpdateNavigationState(); }
    }
    private static string DescribeSourceInfo(JsonElement info)
    {
        var slots = info.GetProperty("slots").EnumerateObject().Select(slot => slot.Name + ": SteamOS " + slot.Value.GetProperty("version").GetString()
            + " · " + slot.Value.GetProperty("build").GetString() + " · " + string.Join(", ", slot.Value.GetProperty("kernels").EnumerateArray().Select(k => k.GetString())));
        return (info.TryGetProperty("layout", out var layout) ? layout.GetString() : "steamos-ab") + "\n" + string.Join("\n", slots);
    }
    private string? CurrentSourceDigest => inspectedSourceKey != null && File.Exists(ImagePath.Text) && SourceInfoKey() == inspectedSourceKey ? inspectedSourceDigest : null;
}
