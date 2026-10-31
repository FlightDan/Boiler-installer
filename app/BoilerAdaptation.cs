using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;

namespace OxpInstaller;

public partial class MainWindow
{
    private enum AdaptationScreen { Choice, Device }
    private AdaptationScreen adaptationScreen = AdaptationScreen.Choice;
    private string? adaptationBranch;
    private Grid? adaptationChoices;
    private StackPanel? adaptationDevicePickerHost;
    private TextBlock? inlineDeviceStatus;
    private Button? inlineDeviceContinue;
    private Button? genericInstallationChoice;
    private StackPanel? adaptationDeviceHost;
    private Border? deviceOptionPreview;
    private JsonElement? previewDeviceMetadata;
    private string? previewDeviceName, previewDevicePath;
    private bool previewFromInputs;
    private bool HasPendingDevicePreview => previewFromInputs || pendingDeviceDirectory != null;
    internal static string DeviceCompatibilityNotice() => PartitionInventory.L(
        "所选机型包可能与目标设备不兼容，仍可继续安装。",
        "This device package may be incompatible with the target device. You can continue installing.");
    internal static string DeviceOptionsDescription() => PartitionInventory.L(
        "机型适配项目已默认全选，可按需取消。",
        "All device adaptation items are selected by default. Clear any you do not need.");

    private void ClearPackageOptionPreview()
    {
        previewDeviceMetadata = null; previewDeviceName = previewDevicePath = null; previewFromInputs = false;
        if (deviceOptionPreview != null) { deviceOptionPreview.Child = null; deviceOptionPreview.Visibility = Visibility.Collapsed; }
        MoveAssociationPanel(toDevice: false);
    }

    private void PromoteDeviceMode()
    {
        var inspected = installMode == "vanilla" && CurrentSourceDigest != null;
        installMode = "custom";
        if (inspected) inspectedSourceKey = SourceInfoKey();
    }

    private void CreateAdaptationFlow()
    {
        adaptationChoices = new Grid { Name = "AdaptationChoices", Margin = new(0, 0, 0, 16) };
        adaptationDeviceHost = new StackPanel { Name = "AdaptationDeviceHost", Visibility = Visibility.Collapsed };
        deviceOptionPreview = new Border { Name = "DeviceOptionPreview", Style = (Style)FindResource("Card"), Visibility = Visibility.Collapsed };
        adaptationDeviceHost.Children.Add(deviceOptionPreview);
        AdaptationPage.Children.Add(adaptationChoices);
        AdaptationPage.Children.Add(adaptationDeviceHost);
        BuildAdaptationCards();
    }

    private void BuildAdaptationCards()
    {
        if (adaptationChoices == null) return;
        if (deviceQuickPicker?.Parent is Panel oldParent) oldParent.Children.Remove(deviceQuickPicker);
        if (inlineDeviceContinue?.Parent is Panel oldActionParent) oldActionParent.Children.Remove(inlineDeviceContinue);
        if (inlineDeviceStatus?.Parent is Panel oldStatusParent) oldStatusParent.Children.Remove(inlineDeviceStatus);
        adaptationChoices.Children.Clear();
        var deviceBody = new StackPanel();
        deviceBody.Children.Add(new TextBlock { Text = PartitionInventory.L("选择机型", "Choose a device"), FontSize = 24,
            FontWeight = FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap, Margin = new(0, 0, 0, 4) });
        deviceBody.Children.Add(new TextBlock { Text = PartitionInventory.L("选择适合目标设备的机型包。", "Choose a device package for your target computer."),
            FontSize = 16, LineHeight = 26, Foreground = BoilerBrush("#61728A"), TextWrapping = TextWrapping.Wrap, Margin = new(0, 0, 0, 12) });
        adaptationDevicePickerHost = new StackPanel(); deviceBody.Children.Add(adaptationDevicePickerHost);
        inlineDeviceContinue = new Button { Name = "UseInlineDevice", MinHeight = 52,
            HorizontalContentAlignment = HorizontalAlignment.Center, Background = BoilerBrush("#2968D9"),
            Foreground = System.Windows.Media.Brushes.White, Margin = new(0, 12, 0, 0) };
        inlineDeviceContinue.Click += async (_, _) =>
        {
            if (operation != null || DownloadsBusy || deviceActionBusy) return;
            if (selectedDevicePackage != null && !HasPendingDevicePreview) { ContinueAfterDeviceSelection(); return; }
            if (!CanUseChosenDevice) return;
            if (installMode == "vanilla") PromoteDeviceMode();
            await DeviceAction(SelectChosenDeviceAsync);
        };
        deviceBody.Children.Add(inlineDeviceContinue);
        inlineDeviceStatus = new TextBlock { TextWrapping = TextWrapping.Wrap, Foreground = BoilerBrush("#485B73"), Margin = new(0, 8, 0, 0), Visibility = Visibility.Collapsed };
        deviceBody.Children.Add(inlineDeviceStatus);
        var device = new Border { Name = "ChooseDeviceInstallation", Child = deviceBody, Background = System.Windows.Media.Brushes.White,
            BorderBrush = BoilerBrush("#DFE6F0"), BorderThickness = new(1), CornerRadius = new(12), Padding = new(20) };
        var generic = CreateBoilerEntry("generic", PartitionInventory.L("通用安装", "Generic installation"),
            PartitionInventory.L("不加载机型脚本，按需选择无线固件和通用组件。", "Choose optional wireless firmware and common components without a device script."),
            PartitionInventory.L("通用安装", "Use generic installation"), "entry-vanilla", ChooseGenericInstallation);
        generic.Name = "ChooseGenericInstallation";
        genericInstallationChoice = generic;
        adaptationChoices.Children.Add(device); adaptationChoices.Children.Add(generic);
        PlaceDeviceQuickPicker(adaptationScreen == AdaptationScreen.Choice);
        RefreshInlineDeviceChoice();
        UpdateAdaptationLayout();
    }

    private void ResetAdaptationFlow()
    {
        adaptationBranch = null; adaptationScreen = AdaptationScreen.Choice;
        ClearPackageOptionPreview();
        optionsPanel.SetWirelessContext(installMode, hasPackage: true);
        MoveAssociationPanel(toDevice: false);
    }

    private void ChooseDeviceInstallation()
    {
        if (operation != null || DownloadsBusy || deviceActionBusy) return;
        if (installMode == "vanilla") PromoteDeviceMode();
        adaptationBranch = "device";
        adaptationScreen = AdaptationScreen.Device;
        optionsPanel.SetWirelessContext(installMode, hasPackage: true);
        ShowStep(AdaptationStep);
    }

    private void PlaceDeviceQuickPicker(bool inline)
    {
        if (deviceQuickPicker == null || devicePanel == null || adaptationDevicePickerHost == null) return;
        if (inlineDeviceContinue != null && !ReferenceEquals(inlineDeviceContinue.Parent, deviceQuickPicker))
        {
            if (inlineDeviceContinue.Parent is Panel actionParent) actionParent.Children.Remove(inlineDeviceContinue);
            var actionIndex = deviceDirectoryDetails == null ? deviceQuickPicker.Children.Count : deviceQuickPicker.Children.IndexOf(deviceDirectoryDetails);
            deviceQuickPicker.Children.Insert(actionIndex < 0 ? deviceQuickPicker.Children.Count : actionIndex, inlineDeviceContinue);
        }
        if (inlineDeviceContinue != null) inlineDeviceContinue.Visibility = inline ? Visibility.Visible : Visibility.Collapsed;
        if (inlineDeviceStatus != null && !ReferenceEquals(inlineDeviceStatus.Parent, deviceQuickPicker))
        {
            if (inlineDeviceStatus.Parent is Panel statusParent) statusParent.Children.Remove(inlineDeviceStatus);
            deviceQuickPicker.Children.Insert(deviceQuickPicker.Children.IndexOf(inlineDeviceContinue!) + 1, inlineDeviceStatus);
        }
        if (inlineDeviceStatus != null && !inline) inlineDeviceStatus.Visibility = Visibility.Collapsed;
        var destination = inline ? adaptationDevicePickerHost : devicePanel;
        if (ReferenceEquals(deviceQuickPicker.Parent, destination)) return;
        if (deviceQuickPicker.Parent is Panel parent) parent.Children.Remove(deviceQuickPicker);
        destination.Children.Insert(0, deviceQuickPicker);
    }

    private void RefreshInlineDeviceChoice()
    {
        RefreshDeviceSourcePresentation();
        var canContinue = (selectedDevicePackage != null && !HasPendingDevicePreview || CanUseChosenDevice)
            && operation == null && !DownloadsBusy && !deviceActionBusy && !inputInspectionBusy && !inputAssociationBusy;
        if (inlineDeviceContinue != null)
        {
            inlineDeviceContinue.IsEnabled = canContinue;
            inlineDeviceContinue.Content = selectedDevicePackage != null && !HasPendingDevicePreview
                ? PartitionInventory.L("继续：系统来源", "Continue: system source") : ChosenDeviceActionLabel;
        }
        if (step == AdaptationStep) NextButton.IsEnabled = canContinue;
        if (genericInstallationChoice != null)
        {
            var selected = adaptationBranch == "generic";
            genericInstallationChoice.BorderBrush = BoilerBrush(selected ? "#2968D9" : "#DFE6F0");
            if (genericInstallationChoice.Content is Grid container && container.Children[0] is Grid full
                && full.Children.OfType<Border>().LastOrDefault()?.Child is TextBlock action)
                action.Text = selected ? PartitionInventory.L("已选择 · 继续通用安装", "Selected · continue generic installation")
                    : PartitionInventory.L("通用安装", "Use generic installation");
        }
        if (signedDeviceUse != null)
        {
            signedDeviceUse.IsEnabled = CanUseChosenDevice;
            if (signedDeviceUse.Content is TextBlock signedLabel) signedLabel.Text = ChosenDeviceActionLabel;
        }
        if (inlineDeviceStatus != null)
        {
            inlineDeviceStatus.Text = deviceActionBusy ? PartitionInventory.L("正在准备机型包…", "Preparing the device package…")
                : deviceActionError ?? (selectedDevicePackage != null && displayedDeviceMetadata is JsonElement selected
                    ? PartitionInventory.L("当前机型包：", "Current device package: ") + PreviewText(selected, "name", selected.GetProperty("id").GetString()!)
                    : deviceList?.SelectedItem is ComboBoxItem { Tag: JsonElement } ? ChosenDeviceNotice : "");
            inlineDeviceStatus.Visibility = adaptationScreen != AdaptationScreen.Choice || string.IsNullOrEmpty(inlineDeviceStatus.Text) ? Visibility.Collapsed : Visibility.Visible;
        }
    }

    private void ChooseGenericInstallation()
    {
        if (operation != null || DownloadsBusy || deviceActionBusy) return;
        if (adaptationBranch == "generic" && selectedDevicePackage == null && !HasPendingDevicePreview)
        {
            ClearModelGuide();
            displayedDeviceMetadata = null;
            updatingDeviceList = true;
            try { deviceList!.SelectedIndex = 0; }
            finally { updatingDeviceList = false; }
            if (signedDeviceUse != null) signedDeviceUse.Visibility = Visibility.Collapsed;
            ShowStep(1);
            return;
        }
        adaptationBranch = "generic";
        ClearModelGuide();
        ClearPackageOptionPreview();
        pendingDeviceDirectory = null;
        if (deviceOptionPreview != null) deviceOptionPreview.Visibility = Visibility.Collapsed;
        ClearDeviceSelection();
        if (signedDeviceUse != null) signedDeviceUse.Visibility = Visibility.Collapsed;
        if (deviceList != null) { updatingDeviceList = true; deviceList.SelectedIndex = 0; updatingDeviceList = false; }
        adaptationScreen = AdaptationScreen.Choice;
        optionsPanel.SetWirelessContext(installMode, hasPackage: false);
        MoveAssociationPanel(toDevice: false);
        ShowStep(1);
    }

    private void EnterInstallationOptionsAfterSource()
    {
        ShowStep(DeviceSupportIssue() == null ? 2 : AdaptationStep);
    }

    private void ContinueAfterDeviceSelection()
    {
        adaptationScreen = AdaptationScreen.Choice;
        ShowStep(1);
    }

    private bool BackWithinAdaptation()
    {
        if (step != AdaptationStep || adaptationScreen == AdaptationScreen.Choice) return false;
        adaptationScreen = AdaptationScreen.Choice;
        ShowStep(AdaptationStep); return true;
    }

    private void MoveAssociationPanel(bool toDevice)
    {
        if (inputAssociationPanel == null || adaptationDeviceHost == null) return;
        var destination = toDevice ? adaptationDeviceHost : SourceInputReviewHost;
        if (ReferenceEquals(inputAssociationPanel.Parent, destination)) return;
        if (inputAssociationPanel.Parent is Panel parent) parent.Children.Remove(inputAssociationPanel);
        destination.Children.Insert(0, inputAssociationPanel);
        RefreshSourceInputReview();
    }

    private static string PreviewText(JsonElement owner, string property, string fallback)
    {
        if (!owner.TryGetProperty(property, out var value)) return fallback;
        if (value.ValueKind == JsonValueKind.Object && value.TryGetProperty(InstallerI18n.Language == "en-US" ? "en" : "zh", out var localized)) value = localized;
        if (value.ValueKind != JsonValueKind.String) return fallback;
        var text = value.GetString() ?? fallback;
        return text.Length <= 512 ? text : text[..512] + "…";
    }

    private void ShowPackageOptionPreview(JsonElement? metadata, string name, string? path, bool fromInputs)
    {
        previewDeviceMetadata = metadata?.Clone(); previewDeviceName = name; previewDevicePath = path; previewFromInputs = fromInputs;
        RenderPackageOptionPreview();
    }

    private void RenderPackageOptionPreview()
    {
        if (deviceOptionPreview == null) return;
        var body = new StackPanel();
        body.Children.Add(new TextBlock { Text = PartitionInventory.L("机型安装选项 · 预览", "Device installation options · preview"), FontSize = 19, FontWeight = FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap });
        body.Children.Add(new TextBlock { Text = previewDeviceName ?? "", TextWrapping = TextWrapping.Wrap, Margin = new(0, 6, 0, 8) });
        body.Children.Add(new TextBlock { Name = "DeviceCompatibilityNotice", Text = DeviceCompatibilityNotice(), TextWrapping = TextWrapping.Wrap,
            Foreground = BoilerBrush("#485B73"), Margin = new(0, 0, 0, 8) });
        body.Children.Add(new TextBlock { Text = PartitionInventory.L("校验通过后自动加入配置，可在安装选项中调整。", "After validation, this package is added automatically. Adjust it in Installation options."), TextWrapping = TextWrapping.Wrap, Foreground = BoilerBrush("#485B73"), Margin = new(0, 0, 0, 8) });
        var count = 0;
        if (previewDeviceMetadata is JsonElement package && package.ValueKind == JsonValueKind.Object
            && package.TryGetProperty("components", out var components) && components.ValueKind == JsonValueKind.Array)
            foreach (var component in components.EnumerateArray().Take(64))
            {
                if (component.ValueKind != JsonValueKind.Object || !component.TryGetProperty("id", out var id) || id.ValueKind != JsonValueKind.String) continue;
                var label = PreviewText(component, "name", id.GetString()!);
                body.Children.Add(new CheckBox { Content = new TextBlock { Text = label, TextWrapping = TextWrapping.Wrap },
                    IsChecked = true, IsEnabled = false, MinHeight = 48 });
                count++;
            }
        if (count == 0) body.Children.Add(new TextBlock { Text = PartitionInventory.L("机型脚本适配；完整校验并选用后显示安装项目。", "Device script adaptation. Installation items appear after complete validation and selection."), TextWrapping = TextWrapping.Wrap });
        deviceOptionPreview.Child = body;
        deviceOptionPreview.Visibility = Visibility.Visible;
    }

    private void RefreshDeviceOptionLabels()
    {
        RefreshLegacyDeviceLabels();
        if (deviceComponentsPanel?.Children.OfType<TextBlock>().FirstOrDefault() is TextBlock heading)
            heading.Text = PartitionInventory.L("机型专属组件", "Device-specific components");
        if (deviceAdaptationsChoice?.Content is TextBlock allLabel)
            allLabel.Text = PartitionInventory.L("机型适配修复", "Device compatibility fixes");
        foreach (var box in deviceComponentChoices.Values)
            if (box.Tag is JsonElement component && box.Content is TextBlock label)
            {
                label.Text = PreviewText(component, "name", component.GetProperty("id").GetString()!);
                var description = PreviewText(component, "description", "");
                if (description.Length > 0) label.Text += "\n" + description;
            }
    }

    private void PreviewSingleInputDevice()
    {
        if (adaptationChoices == null || inputInspectionBusy || inputAssociationBusy || installMode is "local" or "imported") return;
        var active = inputAssignments.Where(row => row.Role != InputRole.Ignore).ToArray();
        if (active.Length != 1 || active[0].Role != InputRole.Device || active[0].Input is not DiscoveredInput input || input.Error != null) return;
        deviceChoiceChanged = true; deviceDetectionState = "manual";
        adaptationBranch = "device"; adaptationScreen = AdaptationScreen.Device;
        ShowPackageOptionPreview(input.DeviceMetadata, input.Name, input.Path, fromInputs: true);
        MoveAssociationPanel(toDevice: true);
        ShowStep(AdaptationStep);
    }

    private void SyncInputDevicePreview(InputAssociationPlan plan)
    {
        if (!previewFromInputs || inputInspectionBusy || inputAssociationBusy) return;
        if (plan.Files.TryGetValue(InputRole.Device, out var input) && input.Path == previewDevicePath) return;
        ClearPackageOptionPreview();
        RefreshAdaptationPage();
    }

    private void RefreshAdaptationPage()
    {
        if (adaptationChoices == null || adaptationDeviceHost == null) return;
        var adaptation = step == AdaptationStep && installMode is not ("local" or "imported");
        var options = step == 2 && installMode is not ("local" or "imported");
        adaptationChoices.Visibility = adaptation && adaptationScreen == AdaptationScreen.Choice ? Visibility.Visible : Visibility.Collapsed;
        adaptationDeviceHost.Visibility = adaptation && adaptationScreen == AdaptationScreen.Device ? Visibility.Visible : Visibility.Collapsed;
        PlaceDeviceQuickPicker(adaptationScreen == AdaptationScreen.Choice);
        RefreshInlineDeviceChoice();
        ComponentOptionsCard.Visibility = options ? Visibility.Visible : Visibility.Collapsed;
        if (devicePanel != null) devicePanel.Visibility = !previewFromInputs && installMode == "custom" ? Visibility.Visible : Visibility.Collapsed;
        MoveAssociationPanel(toDevice: adaptation && adaptationScreen == AdaptationScreen.Device);
        if (options)
        {
            PageTitle.Text = adaptationBranch == "device" ? PartitionInventory.L("机型安装选项", "Device installation options") : PartitionInventory.L("通用安装选项", "Generic installation options");
            PageSubtitle.Text = adaptationBranch == "device" ? DeviceOptionsDescription() : PartitionInventory.L("选择通用组件、无线固件及核显候选补全。", "Choose common components, wireless firmware and optional graphics candidates.");
        }
        UpdateAdaptationLayout();
        if (!adaptation) return;
        AdaptationBackButton.Content = adaptationScreen == AdaptationScreen.Choice
            ? PartitionInventory.L("← 返回安装方式", "← Back to installation modes")
            : PartitionInventory.L("← 返回适配方式", "← Back to device support");
        BackButton.Visibility = Visibility.Collapsed;
        PageFooter.Visibility = adaptationScreen == AdaptationScreen.Choice ? Visibility.Collapsed : Visibility.Visible;
        ModeBannerCard.Visibility = Visibility.Collapsed;
        PageTitle.Text = adaptationScreen == AdaptationScreen.Choice ? PartitionInventory.L("选择设备适配方式", "Choose device support") : PartitionInventory.L("选择机型", "Choose a device");
        PageSubtitle.Text = adaptationScreen == AdaptationScreen.Choice ? PartitionInventory.L("选择机型适配，或按通用配置安装。", "Choose a device package or use generic installation.") : PartitionInventory.L("选择目录中的机型，也可以拖入自己的机型包。", "Choose a device from the directory or drop your own package.");
        NextButton.Visibility = adaptationScreen == AdaptationScreen.Choice ? Visibility.Collapsed : Visibility.Visible;
        NextButton.Content = PartitionInventory.L("下一步：系统来源", "Next: system source");
        NextButton.IsEnabled = (selectedDevicePackage != null && !HasPendingDevicePreview
            || adaptationScreen == AdaptationScreen.Choice && CanUseChosenDevice)
            && operation == null && !DownloadsBusy && !deviceActionBusy && !inputInspectionBusy && !inputAssociationBusy;
        UpdateAdaptationLayout();
    }

    private void UpdateAdaptationLayout()
    {
        if (adaptationChoices == null) return;
        var wide = (WindowRoot.ActualWidth > 0 ? WindowRoot.ActualWidth : Width) >= 960;
        var firstLayer = step == AdaptationStep;
        SidebarPanel.Visibility = firstLayer ? Visibility.Collapsed : Visibility.Visible;
        SidebarColumn.Width = new GridLength(firstLayer ? 0 : wide ? 216 : 152);
        AdaptationBackButton.Visibility = firstLayer ? Visibility.Visible : Visibility.Collapsed;
        AdaptationBackButton.IsEnabled = operation == null && !DownloadsBusy && !deviceActionBusy;
        var columns = wide ? 2 : 1;
        if (adaptationChoices.ColumnDefinitions.Count != columns)
        {
            adaptationChoices.ColumnDefinitions.Clear(); adaptationChoices.RowDefinitions.Clear();
            for (var i = 0; i < columns; i++) adaptationChoices.ColumnDefinitions.Add(new());
            for (var i = 0; i < (wide ? 1 : 2); i++) adaptationChoices.RowDefinitions.Add(new() { Height = GridLength.Auto });
        }
        foreach (var (card, index) in adaptationChoices.Children.OfType<FrameworkElement>().Select((card, index) => (card, index)))
        {
            Grid.SetColumn(card, wide ? index : 0); Grid.SetRow(card, wide ? 0 : index);
            card.MinHeight = wide ? 392 : index == 0 ? 0 : 112;
            card.Margin = wide ? new Thickness(index == 0 ? 0 : 10, 0, index == 0 ? 10 : 0, 0) : new(0, 0, 0, index == 0 ? 16 : 0);
            if (card is Border border) border.Padding = new(wide ? 24 : 16);
            if (card is Button button)
            {
                button.Padding = new(wide ? 24 : 12);
                if (button.Content is Grid adaptive)
                {
                    adaptive.Children[0].Visibility = wide ? Visibility.Visible : Visibility.Collapsed;
                    adaptive.Children[1].Visibility = wide ? Visibility.Collapsed : Visibility.Visible;
                }
            }
        }
    }
}
