using System.IO;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;

namespace OxpInstaller;

internal static class SteamOsPartitionChecks
{
    internal static string ExampleJson() => SteamOsPartitions.ExampleJson();
    internal static Task<string> RunAsync() => Application.Current.Dispatcher.CheckAccess()
        ? RunUi() : Application.Current.Dispatcher.InvokeAsync(RunUi).Task.Unwrap();
    private static async Task<string> RunUi()
    {
        var directory = Path.Combine(Backend.Root, "logs", "steamos-reinstall-" + DateTime.UtcNow.ToString("yyyyMMddTHHmmssfff")); Directory.CreateDirectory(directory);
        var checks = new List<string>(); var captures = new List<string>(); var captureErrors = new List<string>();
        void Require(bool ok, string name) { if (!ok) throw new IOException("SteamOS partition check failed: " + name); checks.Add(name); }
        void Reject(Action action, string name) { var rejected = false; try { action(); } catch (InvalidOperationException) { rejected = true; } Require(rejected, name); }
        var json = ExampleJson(); var disk = PartitionInventory.Read(json).Single();
        var steam = disk.Rows.Where(r => r.SteamOsRole != null).ToArray(); var deletable = steam.Where(r => r.CanDelete).ToArray();
        Require(steam.Length == 8 && deletable.Length == 5 && steam[0].SteamOsRole == "esp", "exact-eight-roles-and-existing-boot-protection");
        var starts = deletable.Select(r => r.Start).Order().ToArray();
        var request = new PartitionDeleteRequest(json, true, disk.Number, starts[0], "zh-CN", starts);
        Require(PartitionDeletion.ValidateTargets(request).Rows.Length == 5, "batch-contains-only-deletable-steamos-roles");
        foreach (var mutation in new[] { "gptName", "gptType", "nativeGptVerified", "missing-role", "gap", "label-only", "duplicate-role" })
        {
            var changed = JsonNode.Parse(json)!; var parts = changed[0]!["partitions"]!.AsArray();
            var part = parts.Single(p => (int)p!["number"]! == 13)!;
            switch (mutation)
            {
                case "gptName": part["gptName"] = "rootfs-a"; break;
                case "gptType": part["gptType"] = "0fc63daf-8483-4772-8e79-3d69d8477de4"; break;
                case "nativeGptVerified": part["nativeGptVerified"] = false; break;
                case "missing-role": parts.Remove(part); break;
                case "gap": part["size"] = (long)part["size"]! - PartitionInventory.MiB; break;
                case "label-only": foreach (var p in parts) { p!["label"] = p["gptName"]!.DeepClone(); p["gptName"] = ""; } break;
                case "duplicate-role": part["gptName"] = "rootfs-B"; break;
            }
            Require(PartitionInventory.Read(changed.ToJsonString()).Single().Rows.All(r => r.SteamOsRole == null), "reject-unverified-layout-" + mutation);
        }
        Require(PartitionInventory.Read(PartitionInventory.ExampleJson).Single().Rows.All(r => r.SteamOsRole == null), "ordinary-data-and-steam-library-not-auto-detected");
        foreach (var row in disk.Rows.Where(r => r.Type != "—" && (!r.CanDelete || r.SteamOsRole == null)))
            Reject(() => PartitionDeletion.ValidateTargets(request with { Start = row.Start, SteamOsStarts = [row.Start] }), "cannot-inject-windows-data-or-protected-" + row.Start);
        Reject(() => PartitionDeletion.ValidateTargets(request with { SteamOsStarts = [] }), "empty-selection-refused");
        Reject(() => PartitionDeletion.ValidateTargets(request with { SteamOsStarts = [starts[0], starts[0]] }), "duplicate-target-refused");
        Reject(() => PartitionDeletion.Simulate(request with { Example = false }), "physical-layout-cannot-enter-simulation");
        var result = PartitionDeletion.Simulate(request);
        var after = PartitionInventory.Read(result).Single();
        Require(after.Rows.Where(r => r.Type != "—").SequenceEqual(disk.Rows.Where(r => r.Type != "—" && !starts.Contains(r.Start)).Select(r => r with { SteamOsRole = null, Name = r.Name.Split(" · SteamOS / ")[0] })), "simulation-preserves-all-unselected-and-protected-partitions");
        Require(after.Rows.Any(r => r.Space?.Size >= 95 * PartitionInventory.GiB), "released-space-is-contiguous-and-installable");
        await File.WriteAllTextAsync(Path.Combine(directory, "fixture.json"), json);
        MainWindow? window = null; var originalLanguage = InstallerI18n.Language;
        try
        {
            foreach (var language in new[] { "zh-CN", "en-US" })
            {
                InstallerI18n.SetLanguage(language);
                window = new MainWindow(true) { Width = 1060, Height = 720, WindowState = WindowState.Normal, ShowActivated = false };
                var picker = (PartitionPicker)window.FindName("PartitionSelector"); picker.AutomaticReadEnabled = false;
                window.Show(); window.BoilerModeForChecks("imported"); window.PreviewStep(0); picker.LoadSnapshot(json, true);
                Require(picker.DeleteSteamOsButton.IsEnabled && !picker.SteamOsDetails.IsExpanded, language + "-empty-selection-offers-selection");
                picker.DeleteSteamOsButton.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                Require(picker.SteamOsDeleteRequest().SteamOsStarts!.SequenceEqual(starts) && picker.SteamOsDetails.IsExpanded, language + "-delete-action-selects-all-eligible-only");
                var choices = picker.SteamOsPartitionsHost.Children.OfType<CheckBox>().ToArray();
                Require(choices.Count(c => c.IsEnabled) == 5 && choices.Count(c => !c.IsEnabled && c.IsChecked != true) == 3, language + "-protected-boot-rows-visible-and-unselectable");
                var protectedText = string.Join(" ", choices.Where(c => !c.IsEnabled).Select(c => ((TextBlock)c.Content).Text));
                Require(protectedText.Contains(language == "en-US" ? "EFI, MSR, recovery and boot partitions are protected." : "EFI、MSR、恢复和启动分区受保护。")
                    && protectedText.Contains(language == "en-US" ? "Volume has no verifiable access path." : "卷没有可验证的访问路径。"), language + "-protected-reasons-visible");
                var unknownReason = JsonNode.Parse(json)!;
                unknownReason[0]!["partitions"]!.AsArray().Single(p => (string?)p!["gptName"] == "esp")!["deletionBlockReason"] = "Unrecognized protection reason.";
                var unknownStatus = PartitionInventory.Read(unknownReason.ToJsonString()).Single().Rows.Single(r => r.SteamOsRole == "esp").Status;
                Require(unknownStatus.Contains("Unrecognized protection reason.") && unknownStatus.Contains(language == "en-US" ? "Protected" : "受保护"), language + "-unknown-reason-remains-protected");
                unknownReason[0]!["partitions"]!.AsArray().Single(p => (string?)p!["gptName"] == "rootfs-A")!["deletionBlockReason"] = "Unrecognized protection reason.";
                Require(!PartitionInventory.Read(unknownReason.ToJsonString()).Single().Rows.Single(r => r.SteamOsRole == "rootfs-A").CanDelete, language + "-unknown-reason-blocks-conflicting-allow");
                choices.First(c => c.IsEnabled).IsChecked = false;
                Require(picker.SteamOsDeleteRequest().SteamOsStarts!.Length == 4 && picker.SelectSteamOsButton.IsEnabled, language + "-individual-opt-out");
                var confirmationSeen = false;
                _ = window.Dispatcher.BeginInvoke(DispatcherPriority.ApplicationIdle, new Action(() => {
                    var confirmation = Application.Current.Windows.OfType<Window>().Single(w => w != window && w.IsVisible);
                    confirmationSeen = true; confirmation.Close();
                }));
                picker.DeleteSteamOsButton.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                Require(confirmationSeen && picker.SteamOsDeleteRequest().SteamOsStarts!.Length == 4, language + "-delete-preserves-manual-subset");
                choices.First(c => c.IsEnabled).IsChecked = true;
                picker.RefreshLanguage(); Require(picker.SteamOsDeleteRequest().SteamOsStarts!.SequenceEqual(starts), language + "-language-refresh-retains-selection");
                foreach (var size in new[] { new Size(1060, 720), new Size(640, 360) })
                {
                    window.Width = size.Width; window.Height = size.Height;
                    picker.SteamOsDetails.IsExpanded = size.Width > 640;
                    picker.SteamOsHeading.BringIntoView(); await Flush(window);
                    var scroll = (ScrollViewer)window.FindName("PageScroll");
                    Require(scroll.ExtentWidth <= scroll.ViewportWidth + 1, language + "-no-horizontal-overflow-" + size.Width);
                    foreach (var button in new[] { picker.SelectSteamOsButton, picker.ClearSteamOsButton, picker.DeleteSteamOsButton })
                    {
                        button.BringIntoView(); await Flush(window);
                        var bounds = button.TransformToAncestor(scroll).TransformBounds(new Rect(0, 0, button.ActualWidth, button.ActualHeight));
                        Require(bounds.Left >= -1 && bounds.Right <= scroll.ActualWidth + 1 && bounds.Top >= -1 && bounds.Bottom <= scroll.ActualHeight + 1, language + "-action-reachable-" + button.Name + "-" + size.Width);
                    }
                    picker.SteamOsHeading.BringIntoView(); await Flush(window);
                    var capture = Path.Combine(directory, language + "-picker-" + size.Width + ".png");
                    try { await BoilerUiChecks.CaptureAsync((FrameworkElement)window.Content, capture); captures.Add(capture); } catch (IOException error) { captureErrors.Add(error.Message); }
                }
                var dialog = PartitionDeletion.Confirmation(request with { Language = language });
                dialog.ShowActivated = false; dialog.MaxHeight = 540; dialog.Show(); await Flush(dialog);
                var panel = (StackPanel)((ScrollViewer)dialog.Content).Content;
                var consent = panel.Children.OfType<CheckBox>().Single();
                var buttons = panel.Children.OfType<WrapPanel>().Single().Children.OfType<Button>().ToArray();
                Require(buttons[0].IsDefault && buttons[0].IsCancel && !buttons[1].IsEnabled && !buttons[1].IsDefault, language + "-cancel-default-and-explicit-consent-required");
                consent.IsChecked = true; Require(buttons[1].IsEnabled, language + "-consent-enables-delete");
                consent.IsChecked = false; Require(!buttons[1].IsEnabled, language + "-unchecking-consent-disables-delete");
                dialog.Close();
                picker.SelectReleasedSpace(result, true, request);
                Require(picker.Selection.Capacity >= PartitionInventory.Minimum && !picker.DeleteSteamOsButton.IsEnabled, language + "-simulated-deletion-selects-released-space");
                var sparseStarts = steam.Where(r => r.SteamOsRole is "var-A" or "home").Select(r => r.Start).ToArray();
                var sparseRequest = request with { Start = sparseStarts.Min(), SteamOsStarts = sparseStarts };
                picker.SelectReleasedSpace(PartitionDeletion.Simulate(sparseRequest), true, sparseRequest);
                Require(picker.Partitions.SelectedItem == null && picker.CapacityInput.Text.Length == 0,
                    language + "-partial-release-below-128-gib-is-not-installable");
                var roomy = JsonNode.Parse(json)!;
                roomy[0]!["size"] = (long)roomy[0]!["size"]! + 128 * PartitionInventory.GiB;
                var recoveryPartition = roomy[0]!["partitions"]!.AsArray().Single(p => (int)p!["number"]! == 4)!;
                recoveryPartition["offset"] = (long)recoveryPartition["offset"]! + 128 * PartitionInventory.GiB;
                var roomyRequest = sparseRequest with { Snapshot = roomy.ToJsonString() };
                picker.SelectReleasedSpace(PartitionDeletion.Simulate(roomyRequest), true, roomyRequest);
                Require(picker.Selection.Start == steam.Single(r => r.SteamOsRole == "home").Start, language + "-partial-selection-chooses-largest-released-installable-region");
                var disks = JsonNode.Parse(json)!.AsArray(); var second = disks[0]!.DeepClone(); second["number"] = 1; second["guid"] = Guid.NewGuid().ToString(); disks.Add(second);
                picker.LoadSnapshot(disks.ToJsonString(), true); picker.SelectSteamOsButton.RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); picker.Disks.SelectedIndex = 1;
                Require(picker.DeleteSteamOsButton.IsEnabled && picker.SteamOsPartitionsHost.Children.OfType<CheckBox>().All(c => c.IsChecked != true), language + "-disk-change-clears-batch-selection");
                picker.SelectSteamOsButton.RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); picker.LoadSnapshot(json, true);
                Require(picker.DeleteSteamOsButton.IsEnabled && picker.SteamOsPartitionsHost.Children.OfType<CheckBox>().All(c => c.IsChecked != true), language + "-refresh-clears-batch-selection");
                picker.AllowPhysicalDeletion = false; picker.LoadSnapshot(json, false);
                Require(!picker.DeleteSteamOsButton.IsEnabled, language + "-rehearsal-blocks-physical-empty-selection");
                picker.SelectSteamOsButton.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                Require(!picker.DeleteSteamOsButton.IsEnabled, language + "-rehearsal-blocks-physical-batch");
                window.Close(); window = null;
            }
        }
        finally { window?.Close(); InstallerI18n.SetLanguage(originalLanguage); }
        var report = JsonSerializer.Serialize(new { passed = true, checks, captures, captureErrors, directory,
            scope = "Synthetic GPT snapshots, batch simulation, WPF routed controls and layout only. No native disk queries, deletion, installation, firmware writes or device acceptance." }, new JsonSerializerOptions { WriteIndented = true });
        await File.WriteAllTextAsync(Path.Combine(directory, "checks.json"), report); return report;
    }
    private static async Task Flush(Window window) { window.UpdateLayout(); await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle); }
}
