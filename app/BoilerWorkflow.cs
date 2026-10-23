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
    private readonly int[] boilerRoute = [1, 2, 0, 4, 3, 5, 6];

    private void ShowBoilerHome()
    {
        WizardShell.Visibility = Visibility.Collapsed;
        if (homeScreen != null) WindowRoot.Children.Remove(homeScreen);
        var panel = new StackPanel { MaxWidth = 760, Margin = new Thickness(28), VerticalAlignment = VerticalAlignment.Center };
        panel.Children.Add(new TextBlock { Text = "Boiler Installer", FontSize = 30, FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 0, 0, 8) });
        panel.Children.Add(new TextBlock { Text = PartitionInventory.L("选择这次安装的方式", "Choose how to install SteamOS"), FontSize = 18, Margin = new Thickness(0, 0, 0, 28) });
        foreach (var entry in new[] {
            ("custom", "构建并安装自定义 SteamOS", "Build a custom SteamOS image", "默认准备 3.10，也可保留基础版本；选择机型适配和预装内容。", "Use 3.10 or keep the base version; select device support and components."),
            ("imported", "导入并安装预制镜像", "Install a prebuilt image", "导入 IMG 或 Boiler 镜像包，保留已有系统、软件和数据。", "Import an IMG or Boiler package, preserving its systems, software and data."),
            ("vanilla", "Vanilla 原版安装", "Vanilla SteamOS", "使用官方系统；可自行选择无线固件、Decky 和维护器。", "Use an official system, with optional wireless firmware, Decky and maintenance.") })
        {
            var content = new StackPanel();
            content.Children.Add(new TextBlock { Text = PartitionInventory.L(entry.Item2, entry.Item3), FontSize = 19, FontWeight = FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap });
            content.Children.Add(new TextBlock { Text = PartitionInventory.L(entry.Item4, entry.Item5), Foreground = new SolidColorBrush(Color.FromRgb(75, 94, 117)), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 8, 0, 0) });
            var button = new Button { Content = content, Tag = entry.Item1, HorizontalContentAlignment = HorizontalAlignment.Stretch,
                Padding = new Thickness(20, 16, 20, 16), Margin = new Thickness(0, 0, 0, 14), MinHeight = 96, Background = Brushes.White };
            button.Click += (_, _) => ChooseBoilerMode((string)button.Tag);
            panel.Children.Add(button);
        }
        var languages = new WrapPanel();
        foreach (var language in new[] { ("zh-CN", "中文"), ("en-US", "English") })
        {
            var button = new Button { Content = language.Item2, MinHeight = 48, Margin = new Thickness(0, 0, 12, 0) };
            button.Click += (_, _) => { if (language.Item1 == "en-US") EnglishLanguage.IsChecked = true; else ChineseLanguage.IsChecked = true; ShowBoilerHome(); };
            languages.Children.Add(button);
        }
        panel.Children.Add(languages);
        homeScreen = new Border { Background = new SolidColorBrush(Color.FromRgb(244, 246, 250)), Child = new ScrollViewer { Content = panel, VerticalScrollBarVisibility = ScrollBarVisibility.Auto } };
        KeyboardNavigation.SetTabNavigation(homeScreen, KeyboardNavigationMode.Cycle);
        Panel.SetZIndex(homeScreen, 20); WindowRoot.Children.Add(homeScreen);
        Title = "Boiler Installer";
    }

    private void ChooseBoilerMode(string mode)
    {
        installMode = mode; selectedDevicePackage = selectedDeviceDigest = selectedDeviceTrust = null;
        deviceComponentChoices.Clear();
        if (deviceComponentsPanel != null) { ComponentOptionsHost.Children.Remove(deviceComponentsPanel); deviceComponentsPanel = null; }
        session = null; reviewSeen = false; acknowledgedSelection = null; PhysicalConsent.IsChecked = false;
        sourceSection = 0; UsePrepared.IsChecked = false; ImagePath.Clear();
        Upgrade310.IsChecked = mode == "custom";
        optionsPanel.SetMode(mode);
        if (devicePanel != null) RecoverySourceSection.Children.Remove(devicePanel);
        CreateDevicePanel();
        devicePanel!.Visibility = mode == "custom" ? Visibility.Visible : Visibility.Collapsed;
        if (homeScreen != null) { WindowRoot.Children.Remove(homeScreen); homeScreen = null; }
        WizardShell.Visibility = Visibility.Visible;
        DownloadHost.Visibility = mode == "imported" ? Visibility.Collapsed : Visibility.Visible;
        ComponentOptionsHost.IsEnabled = mode != "imported";
        ShowStep(1);
        if (physicalInstallation && !bootPrerequisitesAcknowledged) ShowBootPrerequisites(null, null);
    }

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
        NavSource.Text = PartitionInventory.L("01   系统来源", "01   System source");
        NavContent.Text = PartitionInventory.L("02   安装内容", "02   Components");
        NavTarget.Text = PartitionInventory.L("03   安装位置", "03   Destination");
        NavBoot.Text = PartitionInventory.L("04   启动设置", "04   Boot settings");
        NavReview.Text = PartitionInventory.L("05   确认方案", "05   Review");
        var nav = (Panel)TargetTab.Parent;
        foreach (var child in new UIElement[] { SourceTab, SourceSubitems, ContentTab, TargetTab, BootTab, ReviewTab, ProgressTab, ResultTab })
        { nav.Children.Remove(child); nav.Children.Add(child); }
        BackButton.Visibility = step is 0 or 1 or 2 or 3 or 4 ? Visibility.Visible : Visibility.Hidden;
        NextButton.Content = step == 3 ? PartitionInventory.L("开始安装", "Install SteamOS") : step == 6 ? PartitionInventory.L("返回首页", "Home") : PartitionInventory.L("下一步", "Next");
        if (installMode == "imported")
        {
            ContentTab.Visibility = Visibility.Collapsed;
            SourceSubitems.Visibility = Visibility.Collapsed;
            OfficialSourceSection.Visibility = Visibility.Collapsed;
            RecoverySourceSection.Visibility = Visibility.Visible;
            if (step == 1) { PageTitle.Text = PartitionInventory.L("导入预制镜像", "Import a prebuilt image"); PageSubtitle.Text = PartitionInventory.L("支持标准 SteamOS IMG 与 Boiler 包；保留双槽、软件和用户数据。", "Standard SteamOS IMG and Boiler packages; preserve both slots, software and user data."); }
        }
        else ContentTab.Visibility = Visibility.Visible;
    }
}

public partial class InstallerOptionsPanel
{
    internal void SetMode(string mode)
    {
        // Boiler v1 exposes only implemented generic components. Do not submit hidden legacy settings.
        for (var i = 3; i < Children.Count; i++) Children[i].Visibility = Visibility.Collapsed;
        foreach (var item in catalog.Repairs.Concat(catalog.Plugins))
            if (boxes.TryGetValue(item.Id, out var box)) box.IsChecked = mode == "custom" && item.Available && item.Default;
        kde.SelectedItem = kde.Items.Cast<ComboBoxItem>().Single(x => (string)x.Tag == "system");
        kdeChanged = true; setDeckPassword.IsChecked = false; enableSsh.IsChecked = false;
        UpdateRepairSummary();
    }
}
