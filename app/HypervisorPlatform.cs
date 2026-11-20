using System.Diagnostics;
using System.IO;
using System.Security.Principal;
using System.Windows;
using Microsoft.Win32;

namespace OxpInstaller;

internal enum HypervisorAction { Offer, AwaitRestart, Active, Ineffective }

// Optional host acceleration for installation VMs. Windows Hypervisor Platform lets QEMU use
// WHPX instead of TCG software emulation. The tool enables only this one optional feature
// only after the user selects Enable on the review page.
internal static class HypervisorPlatform
{
    internal const string FeatureName = "HypervisorPlatform";
    internal const string EnableArguments = "/online /enable-feature /featurename:HypervisorPlatform /all /norestart /quiet";
    internal const int RebootRequiredExit = 3010;

    internal const string StatePending = "pending-reboot";
    internal const string StateInactive = "inactive";
    internal const string StateKey = @"SOFTWARE\OXP3Installer";
    internal const string StateValue = "HypervisorPlatformState";
    internal const string EnabledTicksValue = "HypervisorPlatformEnabledUtcTicks";
    internal const string RunOnceKey = @"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce";
    internal const string RunOnceValue = "OXP3InstallerResume";

    // Pure decision table: reads no registry, runs no DISM and shows no UI.
    internal static HypervisorAction Decide(bool accelerated, string? state, bool restartedSinceEnable)
    {
        if (accelerated) return HypervisorAction.Active;
        if (state == StatePending)
            return restartedSinceEnable ? HypervisorAction.Ineffective : HypervisorAction.AwaitRestart;
        if (state == StateInactive) return HypervisorAction.Ineffective;
        return HypervisorAction.Offer;
    }

    // Windows boot time is now minus uptime; a boot later than the enable moment means a restart happened since.
    internal static bool RestartedSince(DateTime enabledUtc, DateTime nowUtc, long uptimeMilliseconds) =>
        nowUtc.AddMilliseconds(-uptimeMilliseconds) > enabledUtc;

    internal static string ResumeCommand(string executablePath) => "\"" + executablePath + "\"";

    internal static HypervisorAction CurrentAction()
    {
        if (GuestResourcesPolicy.WhpxAvailable()) return HypervisorAction.Active;
        var (state, enabledUtc) = ReadState();
        var restarted = enabledUtc is { } since && RestartedSince(since, DateTime.UtcNow, Environment.TickCount64);
        return Decide(false, state, restarted);
    }

    internal static async Task EnableAsync()
    {
        var exit = await Task.Run(RunDism);
        if (exit is not (0 or RebootRequiredExit))
            throw new IOException(InstallerI18n.Format(
                $"无法启用 Windows 虚拟机监控程序平台（DISM 代码 0x{(uint)exit:X8}）。",
                $"Could not enable Windows Hypervisor Platform (DISM code 0x{(uint)exit:X8})."));
        WriteState(StatePending, DateTime.UtcNow);
    }

    private static int RunDism()
    {
        var info = new ProcessStartInfo(Path.Combine(Environment.SystemDirectory, "dism.exe"), EnableArguments)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        using var process = Process.Start(info) ?? throw new IOException("DISM could not be started.");
        var output = process.StandardOutput.ReadToEndAsync();
        var errors = process.StandardError.ReadToEndAsync();
        process.WaitForExit();
        Task.WaitAll(output, errors);
        return process.ExitCode;
    }

    private static (string? State, DateTime? EnabledUtc) ReadState()
    {
        using var key = Registry.LocalMachine.OpenSubKey(StateKey);
        var state = key?.GetValue(StateValue) as string;
        var enabled = key?.GetValue(EnabledTicksValue) is long ticks ? new DateTime(ticks, DateTimeKind.Utc) : (DateTime?)null;
        return (state, enabled);
    }

    private static void WriteState(string state, DateTime enabledUtc)
    {
        using var key = Registry.LocalMachine.CreateSubKey(StateKey, true);
        key.SetValue(StateValue, state, RegistryValueKind.String);
        key.SetValue(EnabledTicksValue, enabledUtc.Ticks, RegistryValueKind.QWord);
    }

    private static void RegisterResume(string executable)
    {
        using var key = Registry.LocalMachine.CreateSubKey(RunOnceKey, true);
        key.SetValue(RunOnceValue, ResumeCommand(executable), RegistryValueKind.String);
    }

    internal static async Task RestartAsync()
    {
        var executable = Environment.ProcessPath;
        var canResume = executable != null && !Path.GetFileName(executable).Equals("dotnet.exe", StringComparison.OrdinalIgnoreCase);
        var directory = Path.Combine(Backend.Root, "logs");
        Directory.CreateDirectory(directory);
        var log = Path.Combine(directory, "acceleration-restart-" + Guid.NewGuid().ToString("N") + ".jsonl");
        if (canResume) RegisterResume(executable!);
        try { await InstallerRestart.RestartWindowsAsync(log); }
        catch
        {
            if (canResume)
            {
                using var key = Registry.LocalMachine.OpenSubKey(RunOnceKey, true);
                key?.DeleteValue(RunOnceValue, false);
            }
            throw;
        }
    }
}

public partial class MainWindow
{
    private bool? reviewAccelerationChoice;
    private bool accelerationHelpOpen, accelerationActionBusy;
    private string? accelerationError;
    private HypervisorAction accelerationAction;
    internal Func<HypervisorAction> AccelerationStatusForChecks { get; set; } = HypervisorPlatform.CurrentAction;

    private void RefreshReviewAcceleration()
    {
        accelerationAction = AccelerationStatusForChecks();
        ReviewAcceleration.IsChecked = accelerationAction == HypervisorAction.Active && (reviewAccelerationChoice ?? true);
        ReviewAccelerationLabel.Text = PartitionInventory.L("使用虚拟化加速", "Use virtualization acceleration");
        ReviewAccelerationStatus.Text = accelerationError ?? (accelerationActionBusy
            ? PartitionInventory.L("正在执行，请稍候…", "Working, please wait…")
            : accelerationAction switch
            {
                HypervisorAction.Active => ReviewAcceleration.IsChecked == true
                    ? PartitionInventory.L("安装准备将优先使用 Windows 虚拟化加速。", "Installation preparation will prefer Windows virtualization acceleration.")
                    : PartitionInventory.L("本次安装使用软件模拟。", "This installation will use software emulation."),
                HypervisorAction.AwaitRestart => PartitionInventory.L("已启用 Windows 虚拟机监控程序平台。请保存工作，再重启到 Windows 使其生效。", "Windows Hypervisor Platform is enabled. Save your work, then restart into Windows to apply it."),
                HypervisorAction.Ineffective => PartitionInventory.L("重启后加速仍不可用。请检查 BIOS 中的虚拟化设置，也可继续使用软件模拟。", "Acceleration is still unavailable after restart. Check virtualization in BIOS, or continue with software emulation."),
                _ when accelerationHelpOpen => PartitionInventory.L("请启用 Windows 虚拟机监控程序平台，然后重启到 Windows。也可继续使用软件模拟。", "Enable Windows Hypervisor Platform, then restart into Windows. You can also continue with software emulation."),
                _ => PartitionInventory.L("当前不可用。选择此项可查看启用方式。", "Unavailable. Select this option to see how to enable it.")
            });
        ReviewAccelerationAction.Visibility = (accelerationHelpOpen && accelerationAction is HypervisorAction.Offer or HypervisorAction.Ineffective)
            || accelerationAction == HypervisorAction.AwaitRestart ? Visibility.Visible : Visibility.Collapsed;
        ReviewAccelerationActionLabel.Text = accelerationAction == HypervisorAction.AwaitRestart
            ? PartitionInventory.L("立即重启到 Windows", "Restart into Windows now")
            : PartitionInventory.L("启用 Windows 虚拟化功能", "Enable Windows virtualization");
    }

    private void ReviewAccelerationClicked(object sender, RoutedEventArgs e)
    {
        if (accelerationAction == HypervisorAction.Active) reviewAccelerationChoice = ReviewAcceleration.IsChecked == true;
        else accelerationHelpOpen = true;
        RefreshReviewAcceleration();
    }

    private async void ReviewAccelerationActionClicked(object sender, RoutedEventArgs e)
    {
        if (step != 3 || DownloadsBusy || operation != null || ReviewAccelerationAction.Visibility != Visibility.Visible) return;
        accelerationError = null;
        accelerationActionBusy = true;
        WizardShell.IsEnabled = false;
        try
        {
            RefreshReviewAcceleration();
            if (accelerationAction == HypervisorAction.AwaitRestart) await HypervisorPlatform.RestartAsync();
            else if (accelerationAction is HypervisorAction.Offer or HypervisorAction.Ineffective) await HypervisorPlatform.EnableAsync();
        }
        catch (Exception error) { accelerationError = error.Message; }
        finally
        {
            accelerationActionBusy = false;
            WizardShell.IsEnabled = true;
            RefreshReviewAcceleration();
            UpdateNavigationState();
            (ReviewAccelerationAction.Visibility == Visibility.Visible ? (FrameworkElement)ReviewAccelerationAction : ReviewAcceleration).Focus();
        }
    }
}
