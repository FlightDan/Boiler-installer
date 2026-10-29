using System.Windows;
using System.Windows.Controls;
using Microsoft.Win32;

namespace OxpInstaller;

public partial class MainWindow
{
    private bool sourceCardsReady, sourceIntakeActive, sourceReviewNeedsAttention, skipUpgradeChosen, preserveSourceUpgradeSkip;
    private int inputSessionGeneration;
    private bool HasPendingSourceAssociation => sourceIntakeActive && inputAssignments.Any(row => !row.Applied && row.Role != InputRole.Ignore);
    private bool IsSourceCardMirror(object? source) => ReferenceEquals(source, UpgradeSourceCard.SkipUpgrade);
    internal void PreviewSystemSources() { BoilerModeForChecks("custom"); ChooseGenericInstallation(); }

    private void CreateSystemSourceCards()
    {
        downloadPanel.UseSourceCardLayout(); officialDownloadPanel.UseSourceCardLayout();
        RecoverySourceCard.BrowseRequested += async () => await BrowseSourceCardAsync(false);
        UpgradeSourceCard.BrowseRequested += async () => await BrowseSourceCardAsync(true);
        UpgradeSourceCard.SkipChanged += skip =>
        {
            if (operation != null || DownloadsBusy || installMode is "local" or "imported" || UsePrepared.IsChecked == true) return;
            skipUpgradeChosen = skip; Upgrade310.IsChecked = !skip; ResetOfficialSourceInspection(); ConfigurationChanged();
        };
        downloadPanel.SourceReady += () => { RecoverySourceCard.CollapseForAssociation(); RefreshSystemSourceCards(); };
        officialDownloadPanel.SourceReady += () => { UpgradeSourceCard.CollapseForAssociation(); RefreshSystemSourceCards(); };
        sourceCardsReady = true; RefreshSystemSourceCards();
    }

    private async Task BrowseSourceCardAsync(bool upgrade)
    {
        if (!CanReceiveInputs || upgrade && Upgrade310.IsChecked != true) return;
        var picker = new OpenFileDialog { Multiselect = false,
            Filter = upgrade ? PartitionInventory.L("SteamOS 升级包或系统镜像|*.raucb;*.img", "SteamOS update bundle or system image|*.raucb;*.img")
                : installMode == "imported" ? "SteamOS disk image|*.img;*.img.bz2;*.img.zip;*.boiler.zip|All files|*.*"
                : PartitionInventory.L("官方恢复镜像|*.img;*.img.bz2;*.img.zip|所有文件|*.*", "Official recovery image|*.img;*.img.bz2;*.img.zip|All files|*.*") };
        if (picker.ShowDialog(this) != true) return;
        if (installMode == "imported") { ImagePath.Text = picker.FileName; RefreshSystemSourceCards(); }
        else await ReceiveDroppedInputFilesAsync(picker.FileNames);
    }

    private async Task ReceiveDroppedInputFilesAsync(IReadOnlyList<string> paths)
    {
        if (!CanReceiveInputs) return;
        var generation = inputSessionGeneration;
        inputAssignments.RemoveAll(row => row.Applied);
        inputAssociationApplied = false; sourceIntakeActive = true;
        await ReceiveInputFilesAsync(paths);
        if (generation != inputSessionGeneration || !CanReceiveInputs) return;
        var plan = CurrentInputPlan;
        var ordinarySources = (installMode is "custom" or "vanilla" or "local") && UsePrepared.IsChecked != true
            && plan.Mode == installMode && plan.Conflict == null && plan.Files.Count > 0
            && plan.Files.Keys.All(role => role is InputRole.Recovery or InputRole.Upgrade)
            && inputAssignments.All(row => row.Input is { Error: null } && row.Input.Kind != InputKind.Unknown);
        if (ordinarySources)
        {
            preserveSourceUpgradeSkip = skipUpgradeChosen && Upgrade310.IsChecked != true;
            try { await ApplyInputAssociationsAsync(); }
            finally { preserveSourceUpgradeSkip = false; }
            if (inputAssociationApplied && adaptationBranch == null && installMode is "custom" or "vanilla") ShowStep(AdaptationStep);
        }
        RefreshSystemSourceCards(); RefreshSourceInputReview();
        if (SourceInputReview.Visibility == Visibility.Visible) SourceInputReview.IsExpanded = true;
    }

    private void RefreshSystemSourceCards()
    {
        if (!sourceCardsReady) return;
        var imported = installMode == "imported"; var local = installMode == "local";
        var interactive = operation == null && !DownloadsBusy;
        if (Upgrade310.IsChecked == true) skipUpgradeChosen = false;
        RecoverySourceCard.Configure(imported ? PartitionInventory.L("本地 IMG 镜像", "Local IMG image") : PartitionInventory.L("官方恢复镜像", "Official recovery image"),
            imported ? PartitionInventory.L("完整磁盘镜像，保留其中已有内容。", "Complete disk image with its existing contents.") : PartitionInventory.L("恢复环境与基础系统布局", "Recovery environment and base system layout"),
            imported ? PartitionInventory.L("支持 .img、.img.bz2、.img.zip 和 Boiler 镜像包", "Accepts .img, .img.bz2, .img.zip and Boiler image packages")
                : PartitionInventory.L("支持 .img、.img.bz2、.img.zip", "Accepts .img, .img.bz2 and .img.zip"), false, false, !imported && !local);
        RecoverySourceCard.SetSelection(ImagePath.Text, false, downloadPanel.IsBusy, interactive);
        var associatedInput = officialDownloadPanel.CurrentAssociatedInspection;
        var update = officialDownloadPanel.AssociatedSourcePath != null
            ? associatedInput?.RaucManifest
            : PendingRaucManifest;
        var upgradeDescription = update is { IsSupportedByCurrentInstaller: true }
            ? PartitionInventory.L($"升级包版本：SteamOS {update.Version}。安装时仍会重建并检查系统镜像。", $"Upgrade package version: SteamOS {update.Version}. The system image is still rebuilt and checked during installation.")
            : update is { } unsupported
                ? PartitionInventory.L($"已识别 SteamOS {unsupported.Version}；当前安装流程不支持此版本。", $"SteamOS {unsupported.Version} detected; this installer does not support this version.")
                : associatedInput?.Kind == InputKind.UpgradeRoot
                    ? PartitionInventory.L("已选择 rootfs.img，版本将在安装准备时识别。", "rootfs.img selected; its version will be identified during installation preparation.")
                    : officialDownloadPanel.AssociatedSourcePath != null
                        ? PartitionInventory.L("升级包文件已改变；请重新拖入以识别当前版本。", "The update bundle changed; drop it again to identify its current version.")
                    : PartitionInventory.L($"官方下载源：SteamOS {OfficialSystem.Version} · build {OfficialSystem.Build}", $"Official download source: SteamOS {OfficialSystem.Version} · build {OfficialSystem.Build}");
        UpgradeSourceCard.Configure(PartitionInventory.L("升级包", "Upgrade bundle"),
            upgradeDescription,
            PartitionInventory.L("支持 .raucb 更新包或 rootfs.img；rootfs.img 版本将在安装准备时识别。", "Accepts .raucb update bundles or rootfs.img; rootfs.img version is identified during installation preparation."),
            !local && !imported, interactive && Upgrade310.IsEnabled, !local && !imported);
        UpgradeSourceCard.SetSelection(officialDownloadPanel.SelectedSourcePath, Upgrade310.IsChecked != true, officialDownloadPanel.IsBusy, interactive);
        SourceInputReview.Header = PartitionInventory.L("检查拖入的文件", "Review dropped files"); RefreshSourceInputReview();
    }

    private void RefreshSourceInputReview()
    {
        if (inputAssociationPanel == null) return;
        var inSource = ReferenceEquals(inputAssociationPanel.Parent, SourceInputReviewHost);
        var pending = inputAssignments.Any(row => !row.Applied && (row.Role != InputRole.Ignore || row.Input?.Error != null || row.Input == null));
        SourceInputReview.Visibility = inSource && pending ? Visibility.Visible : Visibility.Collapsed;
        var needsAttention = inSource && pending && !inputInspectionBusy && !inputAssociationBusy;
        if (needsAttention && !sourceReviewNeedsAttention) SourceInputReview.IsExpanded = true;
        sourceReviewNeedsAttention = needsAttention;
        inputAssociationHeading!.Visibility = inputAssociationNote!.Visibility = inputDropButton!.Visibility = inputFolderButton!.Visibility = inSource ? Visibility.Collapsed : Visibility.Visible;
        inputAssociationPanel.Padding = inSource ? new(0) : new(16); inputAssociationPanel.BorderThickness = inSource ? new(0) : new(1);
    }
}
