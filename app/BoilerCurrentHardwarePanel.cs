using System.ComponentModel;
using System.IO;
using System.Security;
using System.Windows;
using System.Windows.Controls;

namespace OxpInstaller;

public partial class MainWindow
{
    private Border? currentHardwarePanel;
    private Button? currentHardwareDetectButton;
    private TextBlock? currentHardwareTitle, currentHardwareIntro;
    private TextBlock? currentHardwareStatus;
    private StackPanel? currentHardwareResults;
    private bool currentHardwareDetectionBusy;
    private BoilerCustomComponentHardware.Detection? currentHardwareDetection;
    private string? currentHardwareDetectionError;
    // Tests replace only this current-PC, read-only SetupAPI input. It never reads a target disk or invokes a guest.
    internal Func<IReadOnlyList<BoilerWirelessHardware.Device>> CurrentHardwareReaderForChecks { get; set; } = BoilerWirelessHardware.ReadPresentDevices;
    private bool CurrentHardwareUiBusy => deviceActionBusy || sourceInspectionBusy || inputInspectionBusy || inputAssociationBusy
        || (downloadPanel?.IsBusy ?? false) || (officialDownloadPanel?.IsBusy ?? false);

    private Border CreateCurrentHardwarePanel()
    {
        var panel = currentHardwarePanel = new Border { Background = BoilerBrush("#F7F9FC"), BorderBrush = BoilerBrush("#DFE6F0"), BorderThickness = new(1), CornerRadius = new(8), Padding = new(10), Margin = new(0, 10, 0, 8) };
        var body = new StackPanel(); panel.Child = body;
        currentHardwareTitle = new TextBlock { FontWeight = FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap };
        currentHardwareIntro = new TextBlock { TextWrapping = TextWrapping.Wrap, Foreground = BoilerBrush("#61728A"), Margin = new(0, 4, 0, 8) };
        body.Children.Add(currentHardwareTitle); body.Children.Add(currentHardwareIntro);
        currentHardwareDetectButton = new Button { Name = "DetectCurrentComputerHardware", MinHeight = 38, HorizontalAlignment = HorizontalAlignment.Left, Padding = new(12, 6, 12, 6) };
        System.Windows.Automation.AutomationProperties.SetName(currentHardwareDetectButton, PartitionInventory.L("识别当前电脑硬件", "Detect this computer's hardware"));
        currentHardwareDetectButton.Click += async (_, _) => await DetectCurrentHardwareAsync();
        currentHardwareStatus = new TextBlock { TextWrapping = TextWrapping.Wrap, Foreground = BoilerBrush("#61728A"), Margin = new(0, 6, 0, 4) };
        currentHardwareResults = new StackPanel();
        body.Children.Add(currentHardwareDetectButton); body.Children.Add(currentHardwareStatus); body.Children.Add(currentHardwareResults);
        RefreshCurrentHardwareUi();
        return panel;
    }

    private async Task DetectCurrentHardwareAsync()
    {
        if (currentHardwareDetectionBusy || CurrentHardwareUiBusy) return;
        currentHardwareDetectionBusy = true; currentHardwareDetectionError = null; RefreshCurrentHardwareUi();
        try
        {
            // SetupAPI is called only for the PC running this UI. The result is neither logged nor put in an install request.
            currentHardwareDetection = await Task.Run(() => BoilerCustomComponentHardware.Match(CurrentHardwareReaderForChecks()));
        }
        catch (Exception error) when (error is Win32Exception or IOException or UnauthorizedAccessException or SecurityException or PlatformNotSupportedException or InvalidOperationException)
        { currentHardwareDetection = null; currentHardwareDetectionError = error.Message; }
        finally { currentHardwareDetectionBusy = false; RefreshCurrentHardwareUi(); }
    }

    private void RefreshCurrentHardwareUi()
    {
        if (currentHardwareDetectButton == null || currentHardwareTitle == null || currentHardwareIntro == null || currentHardwareStatus == null || currentHardwareResults == null) return;
        currentHardwareTitle.Text = PartitionInventory.L("识别当前电脑硬件", "Detect this computer's hardware");
        currentHardwareIntro.Text = PartitionInventory.L("识别当前 Windows 电脑的部件，辅助选择对应构件。", "Identify components on this Windows computer to help choose matching components.");
        System.Windows.Automation.AutomationProperties.SetName(currentHardwareDetectButton, currentHardwareTitle.Text);
        currentHardwareDetectButton.IsEnabled = !currentHardwareDetectionBusy && !CurrentHardwareUiBusy;
        currentHardwareDetectButton.Content = PartitionInventory.L(currentHardwareDetectionBusy ? "正在识别当前电脑硬件…" : "识别当前电脑硬件", currentHardwareDetectionBusy ? "Detecting this computer's hardware…" : "Detect this computer's hardware");
        currentHardwareResults.Children.Clear();
        if (currentHardwareDetectionBusy) { currentHardwareStatus.Text = PartitionInventory.L("正在读取当前电脑硬件…", "Reading this computer's hardware…"); return; }
        if (currentHardwareDetectionError != null) { currentHardwareStatus.Text = PartitionInventory.L("无法读取当前电脑的硬件清单；现有选择保持不变。请重试或手动选择。", "Could not read this computer's hardware inventory; existing selections were kept. Retry or select manually."); return; }
        if (currentHardwareDetection == null) { currentHardwareStatus.Text = PartitionInventory.L("来源：当前电脑。请按识别结果选择构件。", "Source: this computer. Choose components based on the results."); return; }
        currentHardwareStatus.Text = PartitionInventory.L("来源：当前电脑。请按识别结果选择构件。", "Source: this computer. Choose components based on the results.");
        foreach (var result in currentHardwareDetection.Results)
        {
            var state = result.State switch { BoilerCustomComponentHardware.MatchState.Matched => PartitionInventory.L("已匹配", "Matched"), BoilerCustomComponentHardware.MatchState.Candidate => PartitionInventory.L("候选", "Candidate"), _ => PartitionInventory.L("未找到", "Not found") };
            var color = result.State switch { BoilerCustomComponentHardware.MatchState.Matched => BoilerBrush("#176B3A"), BoilerCustomComponentHardware.MatchState.Candidate => BoilerBrush("#8A5A00"), _ => BoilerBrush("#61728A") };
            var row = new StackPanel { Margin = new(0, 4, 0, 2), Tag = result.ComponentId };
            row.Children.Add(new TextBlock { Text = CurrentHardwareName(result.ComponentId) + " · " + state, FontWeight = FontWeights.SemiBold, Foreground = color, TextWrapping = TextWrapping.Wrap });
            row.Children.Add(new TextBlock { Text = PartitionInventory.L("原始 ID：", "Raw ID: ") + (result.RawId.Length == 0 ? PartitionInventory.L("未发现", "not present") : result.RawId), TextWrapping = TextWrapping.Wrap, Foreground = BoilerBrush("#61728A") });
            row.Children.Add(new TextBlock { Text = CurrentHardwareReason(result.Reason), TextWrapping = TextWrapping.Wrap, Foreground = BoilerBrush("#61728A") });
            row.Children.Add(new TextBlock { Tag = "availability", Text = CurrentHardwareAvailability(result.ComponentId), TextWrapping = TextWrapping.Wrap, Foreground = BoilerBrush("#61728A") });
            currentHardwareResults.Children.Add(row);
        }
    }

    private static string CurrentHardwareName(string id) => id switch { "intel-xevma" => "Intel xeVMA", "intel-be201-firmware" => "Intel BE201", "realtek-alc245-speaker" => PartitionInventory.L("Realtek ALC245 扬声器", "Realtek ALC245 speaker"), "bmi260-gyro" => PartitionInventory.L("BMI260 陀螺仪", "BMI260 gyro"), "fingerprint-bf63112" => PartitionInventory.L("指纹传感器 BF63112", "Fingerprint sensor BF63112"), "fingerprint-fte3600" => PartitionInventory.L("指纹传感器 FTE3600", "Fingerprint sensor FTE3600"), _ => id };
    private string CurrentHardwareAvailability(string id)
    {
        if (id == "intel-be201-firmware")
            return FindCurrentHardwareChoice("resource:" + id) is { IsEnabled: true }
                ? PartitionInventory.L("所选机型包含 BE201 固件，可在构件列表调整。", "The selected model includes BE201 firmware. Adjust it in the component list.")
                : PartitionInventory.L("目标是否为 BE201 尚未确认，暂不安装固件。", "BE201 on the destination is unconfirmed; firmware will not be installed.");
        var aliases = id switch
        {
            "intel-xevma" => new[] { "resource:intel-xevmafix-724", "oxp3:xevmafix", "x2-mini:xevmafix", "generic:xevmafix" },
            "realtek-alc245-speaker" => new[] { "resource:oxp3-speaker-route", "oxp3:speaker-route" },
            "bmi260-gyro" => new[] { "resource:bmi260-gyro" },
            // A fingerprint HID read from this Windows computer says nothing about the
            // sensor on the destination SteamOS device. Until provider selection works
            // from authenticated target evidence, these observations map to no
            // installable component at all.
            _ => []
        };
        var found = aliases.Select(FindCurrentHardwareChoice).FirstOrDefault(choice => choice is { IsEnabled: true });
        if (found is not null) return PartitionInventory.L("对应构件：当前可选（请手动选择）。", "Related component: currently available (select it manually).");
        return PartitionInventory.L("对应构件：尚未提供。识别结果不代表 Linux 驱动或硬件支持。", "Related component: not currently provided. Detection does not establish Linux-driver or hardware support.");
    }

    private CheckBox? FindCurrentHardwareChoice(string id)
    {
        if (deviceComponentChoices.TryGetValue(id, out var component)) return component;
        if (deviceLegacyChoices.TryGetValue(id, out var legacy)) return legacy;
        return optionsPanel.OptionBoxes.TryGetValue(id, out var generic)
            && (!InstallerOptions.WirelessComponents.Contains(id) || optionsPanel.RepairsExpander.Visibility == Visibility.Visible)
            ? generic : null;
    }
    private static string CurrentHardwareReason(string reason) => reason switch
    {
        "exact-intel-g3-extreme-pci" => PartitionInventory.L("已确认 Intel Arc G3 Extreme PCI 身份。SteamOS 的 xe 驱动、精确内核和 xeVMA 载荷兼容性仍需单独核验。", "Confirmed Intel Arc G3 Extreme PCI identity. SteamOS xe driver, exact kernel, and xeVMA payload compatibility still need separate verification."),
        "intel-display-driver-unconfirmed" => PartitionInventory.L("已识别 Intel 显卡；需在 SteamOS 中核验 xe 驱动、内核版本及修复兼容性。", "Intel graphics identified; verify the xe driver, kernel version and repair compatibility in SteamOS."),
        "shared-intel-connectivity-pci" => PartitionInventory.L("此 Intel 连接 PCI 身份由多个无线产品共用；它不是 BE201 的证明，因此保持候选。", "This Intel connectivity PCI identity is shared by multiple wireless products; it does not prove BE201, so it remains a candidate."),
        "exact-alc245-codec-speaker-route-unproven" => PartitionInventory.L("已识别 ALC245；扬声器路由适配需单独核验。", "ALC245 identified; speaker-route adaptation needs separate verification."),
        "acpi-id-ambiguous-not-chip-proof" => PartitionInventory.L("ACPI 10EC5280 身份本身不足以证明是 BMI260 芯片；Linux 芯片、驱动和内核兼容性需单独核验。", "ACPI 10EC5280 alone does not prove a BMI260 chip; Linux chip, driver, and kernel compatibility need separate verification."),
        "sensor-acpi-hid-candidate-runtime-unverified" => PartitionInventory.L("已识别指纹传感器 ACPI 身份；实际芯片型号、Linux 驱动运行时和采集功能需在 SteamOS 中单独核验。", "Fingerprint sensor ACPI identity identified; the actual chip variant, Linux driver runtime, and capture support need separate verification in SteamOS."),
        _ => PartitionInventory.L("当前电脑上未发现符合此检测规则的硬件 ID。", "No hardware ID matching this detection rule was found on this computer.")
    };
}
