using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using Microsoft.Win32;

namespace OxpInstaller;

public partial class MainWindow
{
    private string installMode = "custom";
    private Border? homeScreen;
    private string? selectedDevicePackage;
    private readonly int[] boilerRoute = [AdaptationStep, 1, 2, 0, 4, 3, 5, 6];

    private void ChooseBoilerMode(string mode)
    {
        if (mode is not ("custom" or "local" or "vanilla" or "imported")) throw new ArgumentOutOfRangeException(nameof(mode));
        if (operation != null || DownloadsBusy || deviceActionBusy || PartitionSelector.IsBusy) return;
        ClearResourceSelection();
        installMode = mode; selectedDevicePackage = selectedDeviceDigest = selectedDeviceTrust = null;
        deviceActionError = null;
        ResetInputAssociationSession();
        deviceComponentChoices.Clear();
        ClearLegacyDeviceOptions();
        deviceAdaptationsChoice = null;
        if (deviceComponentsPanel != null) { ComponentOptionsHost.Children.Remove(deviceComponentsPanel); deviceComponentsPanel = null; }
        session = null; reviewSeen = false; acknowledgedSelection = null; PhysicalConsent.IsChecked = false;
        Array.Clear(departedSteps); contentSeen = checkedAllSteps = false;
        ResetOfficialSourceInspection();
        sourceSection = 0; UsePrepared.IsChecked = false; ImagePath.Clear();
        ResetImageInspection();
        Upgrade310.IsChecked = mode is "custom" or "local";
        optionsPanel.SetMode(mode);
        skipUpgradeChosen = false;
        RecoverySourceCard.ResetDisclosure(); UpgradeSourceCard.ResetDisclosure();
        ResetAdaptationFlow();
        if (devicePanel?.Parent is Panel priorParent) priorParent.Children.Remove(devicePanel);
        CreateDevicePanel();
        devicePanel!.Visibility = mode == "custom" ? Visibility.Visible : Visibility.Collapsed;
        if (homeScreen != null) { WindowRoot.Children.Remove(homeScreen); homeScreen = null; }
        WizardShell.Visibility = Visibility.Visible;
        RecoveryDownloadCard.Visibility = mode is "imported" or "local" ? Visibility.Collapsed : Visibility.Visible;
        DownloadHost.Visibility = Visibility.Visible;
        officialDownloadPanel.SetLocalOnly(mode == "local");
        ComponentOptionsHost.IsEnabled = mode is not ("imported" or "local");
        if (mode == "vanilla") ChooseGenericInstallation();
        else ShowStep(mode is "imported" or "local" ? 1 : AdaptationStep);
        if (physicalInstallation && !bootPrerequisitesAcknowledged) ShowBootPrerequisites(null, null);
    }

    private void ChoosePreparedImageMode(object sender, RoutedEventArgs e) => ChooseBoilerMode("imported");

    private void AddImageExport()
    {
        var button = new Button { Content = PartitionInventory.L("导出可复用 Boiler 镜像包…", "Export reusable Boiler image package…"), MinHeight = 48, HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(0, 12, 0, 0) };
        button.Click += async (_, _) =>
        {
            if (operation != null || session == null) return;
            var metadata = Path.Combine(InstallationController.SessionDirectory(session), "boiler-image.json");
            if (!File.Exists(metadata)) { ValidationMessage.Text = PartitionInventory.L("此任务尚未生成可导出的镜像。", "This task has no exportable image yet."); return; }
            var picker = new SaveFileDialog { Filter = "Boiler image package|*.boiler.zip", FileName = "steamos.boiler.zip" };
            if (picker.ShowDialog(this) != true) return;
            button.IsEnabled = false;
            try
            {
                using var doc = System.Text.Json.JsonDocument.Parse(await File.ReadAllTextAsync(metadata));
                var plan = doc.RootElement;
                await BoilerImage.ExportAsync(plan.GetProperty("image").GetString()!, picker.FileName,
                    plan.GetProperty("install_plan").Clone(), CancellationToken.None);
                ValidationMessage.Text = PartitionInventory.L("镜像包已导出：", "Image package exported: ") + picker.FileName;
            }
            catch (Exception error) { ValidationMessage.Text = error.Message; }
            finally { button.IsEnabled = true; }
        };
        ResultPage.Children.Add(button);
    }

    private void RefreshBoilerNavigation()
    {
        Title = "Boiler Installer";
        var pairedOrPrepared = installMode is "local" or "imported";
        NavAdaptation.Text = PartitionInventory.L("01   设备适配", "01   Device support");
        NavSource.Text = PartitionInventory.L("01   系统来源", "01   System source");
        NavContent.Text = PartitionInventory.L("02   安装选项", "02   Installation options");
        NavTarget.Text = pairedOrPrepared ? PartitionInventory.L("02   安装位置", "02   Destination") : PartitionInventory.L("03   安装位置", "03   Destination");
        NavBoot.Text = pairedOrPrepared ? PartitionInventory.L("03   启动设置", "03   Boot settings") : PartitionInventory.L("04   启动设置", "04   Boot settings");
        NavReview.Text = pairedOrPrepared ? PartitionInventory.L("04   确认方案", "04   Review") : PartitionInventory.L("05   确认方案", "05   Review");
        NavProgress.Text = pairedOrPrepared ? PartitionInventory.L("05   安装进度", "05   Progress") : PartitionInventory.L("06   安装进度", "06   Progress");
        NavResult.Text = pairedOrPrepared ? PartitionInventory.L("06   安装结果", "06   Result") : PartitionInventory.L("07   安装结果", "07   Result");
        var nav = (Panel)TargetTab.Parent;
        var ordered = new UIElement[] { AdaptationTab, SourceTab, SourceSubitems, ContentTab, TargetTab, BootTab, ReviewTab, ProgressTab, ResultTab };
        for (var index = 0; index < ordered.Length; index++)
            if (nav.Children.IndexOf(ordered[index]) != index) { nav.Children.Remove(ordered[index]); nav.Children.Insert(index, ordered[index]); }
        SourceSubitems.Visibility = Visibility.Collapsed;
        ContentTab.Visibility = pairedOrPrepared ? Visibility.Collapsed : Visibility.Visible;
        AdaptationTab.Visibility = Visibility.Collapsed;
        BackHomeButton.Content = PartitionInventory.L("返回安装方式", "Back to home");
        BackHomeButton.IsEnabled = operation == null && !DownloadsBusy && !PartitionSelector.IsBusy;
        BackButton.Visibility = step is 0 or 2 or 3 or 4 or AdaptationStep || step == 1 && !pairedOrPrepared ? Visibility.Visible : Visibility.Collapsed;
        SourceFooterHint.Visibility = step == 1 ? Visibility.Visible : Visibility.Collapsed;
        SourceFooterHint.Text = installMode == "local" ? PartitionInventory.L("两个镜像均选择并校验后继续。", "Select and verify both images to continue.") : PartitionInventory.L("选择并核对镜像后继续。", "Select and verify the image to continue.");
        NextButton.Content = step == 3 ? PartitionInventory.L("开始安装", "Install SteamOS") : step == 6 ? PartitionInventory.L("返回首页", "Home") : step == 1 ? pairedOrPrepared ? PartitionInventory.L("下一步：安装位置", "Next: destination") : PartitionInventory.L("下一步：安装选项", "Next: installation options") : PartitionInventory.L("下一步", "Next");
        SourceVersionOptions.Visibility = pairedOrPrepared ? Visibility.Collapsed : Visibility.Visible;
        RecoveryDownloadDetails.Header = PartitionInventory.L("下载官方恢复镜像", "Download the official recovery image");
        RecoveryImageHeading.Text = installMode == "imported" ? PartitionInventory.L("本地 IMG 镜像", "Local IMG image") : installMode == "local" ? PartitionInventory.L("1  官方恢复镜像", "1  Official recovery image") : PartitionInventory.L("SteamOS 官方恢复镜像", "SteamOS official recovery image");
        RecoveryImageNote.Text = installMode == "imported" ? PartitionInventory.L("选择一份完整的 SteamOS 磁盘镜像，下一步选择要刷入的目标空间。", "Choose a complete SteamOS disk image, then select the destination space in the next step.") : installMode == "local" ? PartitionInventory.L("提供恢复环境与基础布局；请选择官方 SteamOS 恢复镜像。", "Provides the recovery environment and base layout; select an official SteamOS recovery image.") : PartitionInventory.L("下载官方恢复镜像，或选择已下载的本地文件。", "Download the official recovery image or choose a local file.");
        RecoveryImageHint.Text = installMode == "imported" ? PartitionInventory.L("支持不同容量的 SteamOS .img、.img.bz2、.img.zip 与 Boiler 包；目标空间须能容纳镜像内的系统布局。", "Accepts SteamOS .img, .img.bz2, .img.zip and Boiler packages of different sizes; the destination must fit the system layout.") : PartitionInventory.L("支持 .img、.img.bz2 和 .img.zip。", "Accepts .img, .img.bz2 and .img.zip.");
        ImportPreparedImageButton.Content = PartitionInventory.L("已有 Boiler 预制镜像包？使用镜像包安装…", "Have a Boiler image package? Install the package…");
        ImportPreparedImageButton.Visibility = installMode == "local" ? Visibility.Visible : Visibility.Collapsed;
        SidebarSubtitle.Text = installMode switch { "local" => PartitionInventory.L("本地双镜像", "Local image pair"), "imported" => PartitionInventory.L("本地 IMG 刷入", "Local IMG flashing"), "vanilla" => PartitionInventory.L("Vanilla 原版", "Vanilla SteamOS"), _ => PartitionInventory.L("自定义构建", "Custom build") };
        if (installMode == "imported")
        {
            ContentTab.Visibility = Visibility.Collapsed;
            SourceSubitems.Visibility = Visibility.Collapsed;
            OfficialSourceSection.Visibility = Visibility.Collapsed;
            RecoverySourceSection.Visibility = Visibility.Visible;
            if (step == 1) { PageTitle.Text = PartitionInventory.L("刷入本地 IMG 镜像", "Flash a local IMG"); PageSubtitle.Text = PartitionInventory.L("核对镜像后选择安装位置；完整 A/B 镜像沿用其中的系统、软件和用户数据。", "Review the image, then choose the destination; complete A/B images retain their systems, software and user data."); }
        }
        else if (installMode == "local")
        {
            RecoverySourceSection.Visibility = OfficialSourceSection.Visibility = Visibility.Visible;
            if (step == 1) { PageTitle.Text = PartitionInventory.L("导入本地镜像", "Import local images"); PageSubtitle.Text = PartitionInventory.L("分别选择官方恢复镜像与 SteamOS 3.10 系统镜像，核对后继续。", "Select the official recovery image and the SteamOS 3.10 system image separately, then continue."); }
        }
        RefreshSystemSourceCards(); RefreshAdaptationPage();
    }
}

public partial class InstallerOptionsPanel
{
    internal void SetMode(string mode)
    {
        // Set group visibility explicitly so adding a section cannot hide presets.
        foreach (var section in GenericSections) section.Visibility = mode is "custom" or "vanilla" ? Visibility.Visible : Visibility.Collapsed;
        SystemPresetsPanel.Visibility = mode is "custom" or "vanilla" ? Visibility.Visible : Visibility.Collapsed;
        OptionsNotesPanel.Visibility = mode is "custom" or "vanilla" ? Visibility.Visible : Visibility.Collapsed;
        foreach (var item in catalog.Repairs.Concat(catalog.Plugins))
            if (boxes.TryGetValue(item.Id, out var box)) box.IsChecked = item.Available && item.Default
                && (mode == "custom" || mode == "vanilla" && item.Id == "core:maintenance");
        applyingLanguageDefault = true;
        try { kde.SelectedItem = kde.Items.Cast<ComboBoxItem>().Single(x => (string)x.Tag == (mode is "custom" or "vanilla" ? InstallerOptions.DefaultKdeLanguage : "system")); }
        finally { applyingLanguageDefault = false; }
        kdeChanged = mode is "local" or "imported"; setDeckPassword.IsChecked = false; enableSsh.IsChecked = false;
        ClearSensitiveInputs();
        ResetGraphicsChoice();
        SetWirelessContext(mode, hasPackage: false);
        UpdateRepairSummary();
    }
}
