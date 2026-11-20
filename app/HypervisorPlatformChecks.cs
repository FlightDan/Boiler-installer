using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;

namespace OxpInstaller;

internal static class HypervisorPlatformChecks
{
    // Read-only: exercises the decision table and command strings. Never runs DISM or writes the registry.
    internal static async Task<string> RunAsync()
    {
        var checks = new List<string>();
        void Require(bool value, string label) { if (!value) throw new IOException(label); checks.Add(label); }

        Require(HypervisorPlatform.Decide(true, null, false) == HypervisorAction.Active, "available-host-can-accelerate");
        Require(HypervisorPlatform.Decide(false, null, false) == HypervisorAction.Offer, "unconfigured-machine-is-offered-enable");
        Require(HypervisorPlatform.Decide(false, HypervisorPlatform.StatePending, false) == HypervisorAction.AwaitRestart, "pending-before-restart-asks-for-restart");
        Require(HypervisorPlatform.Decide(false, HypervisorPlatform.StatePending, true) == HypervisorAction.Ineffective, "pending-after-restart-without-whpx-is-reported");
        Require(HypervisorPlatform.Decide(true, HypervisorPlatform.StatePending, false) == HypervisorAction.Active, "pending-with-whpx-is-active");
        Require(HypervisorPlatform.Decide(false, "unsupported", false) == HypervisorAction.Offer, "previously-failed-enablement-can-be-retried-explicitly");
        Require(HypervisorPlatform.Decide(false, HypervisorPlatform.StateInactive, false) == HypervisorAction.Ineffective, "ineffective-machine-is-reported");

        var enabled = new DateTime(2026, 11, 18, 3, 0, 0, DateTimeKind.Utc);
        Require(HypervisorPlatform.RestartedSince(enabled, enabled.AddMinutes(1), 30_000), "boot-after-enable-counts-as-restart");
        Require(!HypervisorPlatform.RestartedSince(enabled, enabled.AddMinutes(1), 7_200_000), "boot-before-enable-does-not-count");

        Require(HypervisorPlatform.ResumeCommand(@"C:\Program Files\OXP3\Installer.exe") == "\"C:\\Program Files\\OXP3\\Installer.exe\"", "resume-command-quotes-path-with-spaces");
        Require(HypervisorPlatform.EnableArguments.Contains("/featurename:HypervisorPlatform", StringComparison.Ordinal)
            && HypervisorPlatform.EnableArguments.Contains("/norestart", StringComparison.Ordinal)
            && !HypervisorPlatform.EnableArguments.Contains("Hyper-V", StringComparison.OrdinalIgnoreCase), "enable-command-is-one-optional-feature-without-restart");
        Require(HypervisorPlatform.RunOnceKey.EndsWith(@"CurrentVersion\RunOnce", StringComparison.Ordinal), "resume-uses-runonce-key");
        Require(InstallerRestart.WindowsBootNextStartInfo().ArgumentList.SequenceEqual(["/set", "{fwbootmgr}", "bootsequence", "{bootmgr}"])
            && InstallerRestart.StartInfo(defaultBoot: true).Arguments == "/r /t 0", "targets-windows-boot-manager-and-restarts-immediately");

        byte[]? bootNext = [9, 0];
        var selected = 0;
        var restarted = 0;
        Task SelectWindows() { selected++; bootNext = [1, 0]; return Task.CompletedTask; }
        Task Restart() { restarted++; return Task.CompletedTask; }
        async Task ExpectFailure(Func<Task> choose, Func<Task> reboot)
        {
            try { await FirmwareBoot.WithWindowsBootNextAsync(() => bootNext, () => bootNext = null, choose, reboot); }
            catch (IOException) { return; }
            throw new IOException("Expected Windows restart to fail.");
        }
        await ExpectFailure(SelectWindows, Restart);
        Require(selected == 0 && restarted == 0 && bootNext[0] == 9, "existing-one-time-entry-is-preserved");
        bootNext = null;
        await ExpectFailure(() => Task.CompletedTask, Restart);
        Require(restarted == 0, "missing-firmware-readback-blocks-restart");
        await ExpectFailure(SelectWindows, () => throw new IOException("fixture shutdown failure"));
        Require(bootNext == null, "failed-shutdown-clears-owned-one-time-entry");
        await FirmwareBoot.WithWindowsBootNextAsync(() => bootNext, () => bootNext = null, SelectWindows, Restart);
        Require(restarted == 1 && bootNext is [1, 0], "windows-entry-is-set-before-restart");

        var legacyJob = new PartitionJob(1, Guid.NewGuid().ToString("N"), "new-virtual-disk-only", InstallationTargetKind.ExternalDisk,
            new("fixture.img", 1, 0, 0, 0, Guid.Empty));
        var legacyBytes = JsonSerializer.SerializeToUtf8Bytes(legacyJob);
        using var legacyJson = JsonDocument.Parse(legacyBytes);
        var optedOut = HelperRequestBinding.Parse<PartitionJob>(JsonSerializer.SerializeToUtf8Bytes(legacyJob with { UseVirtualizationAcceleration = false }));
        Require(HelperRequestBinding.Parse<PartitionJob>(legacyBytes).UseVirtualizationAcceleration == null
            && !legacyJson.RootElement.TryGetProperty(nameof(PartitionJob.UseVirtualizationAcceleration), out _)
            && optedOut.UseVirtualizationAcceleration == false, "helper-request-preserves-the-opt-out-and-accepts-legacy-jobs");
        Require(await Task.Run(() =>
        {
            GuestResourcesPolicy.InstallationAcceleration.Value = optedOut.UseVirtualizationAcceleration;
            return GuestResourcesPolicy.ForHost("physical-readiness").Accel == GuestResourcesPolicy.Tcg;
        }), "helper-guest-uses-tcg-with-explicit-opt-out");

        var window = new MainWindow(rehearsal: true) { Width = 1060, Height = 720, WindowState = WindowState.Normal, ShowActivated = false };
        var directory = Path.Combine(Backend.Root, "logs", "acceleration-review-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        var captures = new List<string>();
        async Task Capture(string name)
        {
            var path = Path.Combine(directory, name + ".png");
            try { await BoilerUiChecks.CaptureAsync((FrameworkElement)window.Content, path); captures.Add(path); }
            catch (IOException error) { captures.Add(error.Message); }
        }
        try
        {
            var status = HypervisorAction.Active;
            window.AccelerationStatusForChecks = () => status;
            ((PartitionPicker)window.FindName("PartitionSelector")).AutomaticReadEnabled = false;
            window.Show();
            await Capture("home");
            window.BoilerModeForChecks("vanilla");
            ((RadioButton)window.FindName("ExternalTarget")).IsChecked = true;
            var checkbox = (CheckBox)window.FindName("ReviewAcceleration");
            var action = (Button)window.FindName("ReviewAccelerationAction");
            var hint = (Control)window.FindName("InstallerLanguageHint");
            var language = (ComboBox)window.FindName("InstallerLanguage");
            Require(hint.Parent == language.Parent && ((Panel)hint.Parent).Children.IndexOf(hint) < ((Panel)hint.Parent).Children.IndexOf(language),
                "language-icon-precedes-wizard-picker");
            window.PreviewStep(3);
            Require(checkbox.IsChecked == true && action.Visibility == Visibility.Collapsed, "available-acceleration-is-checked-by-default-on-review");
            await Capture("review-available");
            checkbox.IsChecked = false;
            checkbox.RaiseEvent(new RoutedEventArgs(CheckBox.ClickEvent));
            window.PreviewStep(4);
            window.PreviewStep(3);
            var request = (InstallationRequest)typeof(MainWindow).GetProperty("Request", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(window)!;
            Require(checkbox.IsChecked == false && request.UseVirtualizationAcceleration == false, "opt-out-survives-navigation-and-reaches-request");
            status = HypervisorAction.Offer;
            window.PreviewStep(3);
            Require(checkbox.IsChecked == false && action.Visibility == Visibility.Collapsed, "unavailable-acceleration-starts-unchecked");
            checkbox.IsChecked = true;
            checkbox.RaiseEvent(new RoutedEventArgs(CheckBox.ClickEvent));
            Require(checkbox.IsChecked == false && action.Visibility == Visibility.Visible, "unavailable-acceleration-reveals-inline-enablement");
            status = HypervisorAction.AwaitRestart;
            window.PreviewStep(3);
            Require(action.Visibility == Visibility.Visible && ((TextBlock)action.Content).Text == PartitionInventory.L("立即重启到 Windows", "Restart into Windows now"),
                "pending-enablement-offers-immediate-windows-restart-on-review");
            window.Width = 640;
            await Capture("review-restart-compact");
        }
        finally { window.Close(); }

        return "hypervisor-checks passed: " + checks.Count + "\n" + string.Join("\n", checks.Concat(captures));
    }
}
