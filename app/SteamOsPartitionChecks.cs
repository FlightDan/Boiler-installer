using System.IO;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Interop;
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
        async Task Capture(Window owner, string name)
        {
            await Flush(owner);
            var path = Path.Combine(directory, name + ".png");
            try { await BoilerUiChecks.CaptureAsync((FrameworkElement)owner.Content, path); captures.Add(path); }
            catch (IOException error) { captureErrors.Add(error.Message); }
        }
        var json = ExampleJson(); var disk = PartitionInventory.Read(json).Single();
        var steam = disk.Rows.Where(r => r.SteamOsRole != null).ToArray(); var deletable = steam.Where(r => r.CanDelete).ToArray();
        Require(steam.Length == 8 && deletable.Length == 8 && steam[0].SteamOsRole == "esp", "all-eight-steamos-roles-including-own-boot-are-deletable");
        using (var roles = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(Backend.Root, "scripts/steam-partition-roles.json"))))
            Require(roles.RootElement.EnumerateArray().Take(3).All(role => role.GetProperty("bootSize").GetInt64()
                == steam.Single(row => row.SteamOsRole == role.GetProperty("name").GetString()).Size), "example-layout-matches-production-boot-sizes");
        var starts = deletable.Select(r => r.Start).Order().ToArray();
        var request = new PartitionDeleteRequest(json, true, disk.Number, starts[0], "zh-CN", starts);
        var crowded = JsonNode.Parse(json)![0]!;
        var diskSize = (long)crowded["size"]!;
        var crowdedParts = crowded["partitions"]!.AsArray();
        var originals = crowdedParts.Where(p => starts.Contains((long)p!["offset"]!)).ToArray();
        for (var group = 1; group <= 2; group++)
            foreach (var original in originals)
            {
                var copy = original!.DeepClone();
                copy["number"] = (int)copy["number"]! + group * 8;
                copy["offset"] = (long)copy["offset"]! + group * diskSize;
                copy["guid"] = Guid.NewGuid().ToString("D");
                crowdedParts.Add(copy);
            }
        crowded["size"] = diskSize * 3;
        var crowdedSnapshot = "[" + crowded.ToJsonString() + "]";
        var crowdedStarts = PartitionInventory.Read(crowdedSnapshot).Single().Rows.Where(r => r.SteamOsRole != null && r.CanDelete).Select(r => r.Start).ToArray();
        Require(crowdedStarts.Length == 24, "fixture-contains-three-steamos-groups");
        var crowdedRequest = request with { Snapshot = crowdedSnapshot, SteamOsStarts = crowdedStarts };
        Require(PartitionDeletion.ValidateTargets(request).Rows.Length == 8, "batch-includes-all-steamos-boot-and-linux-roles");
        foreach (var mutation in new[] { "gptName", "missing-role", "gap", "label-only", "duplicate-role" })
        {
            var changed = JsonNode.Parse(json)!; var parts = changed[0]!["partitions"]!.AsArray();
            var part = parts.Single(p => (int)p!["number"]! == 13)!;
            switch (mutation)
            {
                case "gptName": part["gptName"] = "rootfs-a"; break;
                case "missing-role": parts.Remove(part); break;
                case "gap": part["size"] = (long)part["size"]! - PartitionInventory.MiB; break;
                case "label-only": foreach (var p in parts) { p!["label"] = p["gptName"]!.DeepClone(); p["gptName"] = ""; } break;
                case "duplicate-role": part["gptName"] = "rootfs-B"; break;
            }
            Require(PartitionInventory.Read(changed.ToJsonString()).Single().Rows.All(r => r.SteamOsRole == null), "reject-unverified-layout-" + mutation);
        }
        // Recognition is name + count + contiguity only. Partition type and the
        // native GPT attribute proof no longer decide ownership.
        foreach (var tolerated in new[] { "gptType", "nativeGptVerified", "boot-size" })
        {
            var changed = JsonNode.Parse(json)!;
            // The last role keeps contiguity intact; resizing a middle role
            // legitimately breaks the contiguous run.
            var part = changed[0]!["partitions"]!.AsArray().Single(p => (string?)p!["gptName"] == "home")!;
            switch (tolerated)
            {
                case "gptType": part["gptType"] = "0fc63daf-8483-4772-8e79-3d69d8477de4"; break;
                case "nativeGptVerified": part["nativeGptVerified"] = false; break;
                case "boot-size": part["size"] = (long)part["size"]! - PartitionInventory.MiB; break;
            }
            Require(PartitionInventory.Read(changed.ToJsonString()).Single().Rows.Count(r => r.SteamOsRole != null) == 8, "name-count-contiguity-only-recognition-" + tolerated);
        }
        foreach (var broken in new[] { "gptType-middle", "boot-size-middle" })
        {
            var changed = JsonNode.Parse(json)!; var part = changed[0]!["partitions"]!.AsArray().Single(p => (string?)p!["gptName"] == "rootfs-A")!;
            if (broken == "boot-size-middle") part["size"] = (long)part["size"]! - PartitionInventory.MiB;
            else part["gptName"] = "rootfs-a";
            Require(PartitionInventory.Read(changed.ToJsonString()).Single().Rows.All(r => r.SteamOsRole == null), "middle-role-breaks-recognition-" + broken);
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
        var linuxStarts = steam.Where(r => r.SteamOsRole is not ("esp" or "efi-A" or "efi-B")).Select(r => r.Start).ToArray();
        var remnants = PartitionDeletion.Simulate(request with { Start = linuxStarts.Min(), SteamOsStarts = linuxStarts });
        var bootRows = PartitionInventory.Read(remnants).Single().Rows.Where(r => r.SteamOsRole != null).ToArray();
        Require(bootRows.Length == 3 && bootRows.All(r => r.CanDelete), "previous-five-deleted-leaves-three-selectable-boot-roles");
        var retypedSlot = JsonNode.Parse(remnants)!;
        var efiA = retypedSlot[0]!["partitions"]!.AsArray().Single(p => (string?)p!["gptName"] == "efi-A")!;
        efiA["gptType"] = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b";
        efiA["gptAttributes"] = "9223372036854775808";
        Require(PartitionInventory.Read(retypedSlot.ToJsonString()).Single().Rows.Where(r => r.SteamOsRole != null).All(r => r.CanDelete),
            "reviewed-efi-type-slot-with-nondefault-attributes-is-selectable");
        efiA["isSystem"] = true;
        Require(!PartitionInventory.Read(retypedSlot.ToJsonString()).Single().Rows.Single(r => r.SteamOsRole == "efi-A").CanDelete,
            "efi-type-slot-in-use-by-windows-stays-protected");
        var bootStarts = bootRows.Select(r => r.Start).ToArray();
        var residualRequest = request with { Snapshot = remnants, Start = bootStarts.Min(), SteamOsStarts = bootStarts };
        Require(PartitionDeletion.Simulate(residualRequest) == result, "residual-three-deletion-reaches-identical-preserved-layout");
        foreach (var mutation in new[] { "isSystem", "deletionAllowed" })
        {
            var changed = JsonNode.Parse(remnants)!;
            var part = changed[0]!["partitions"]!.AsArray().Single(p => (string?)p!["gptName"] == "esp")!;
            switch (mutation)
            {
                case "isSystem": part[mutation] = true; break;
                case "deletionAllowed": part[mutation] = false; break;
            }
            Reject(() => PartitionDeletion.ValidateTargets(residualRequest with { Snapshot = changed.ToJsonString() }), "residual-protection-not-bypassed-" + mutation);
        }
        await File.WriteAllTextAsync(Path.Combine(directory, "fixture.json"), json);
        MainWindow? window = null; var originalLanguage = InstallerI18n.Language;
        try
        {
            foreach (var language in new[] { "zh-CN", "en-US" })
            {
                InstallerI18n.SetLanguage(language);
                window = new MainWindow(true) { Width = 1060, Height = 720, WindowState = WindowState.Normal, ShowActivated = false };
                var picker = (PartitionPicker)window.FindName("PartitionSelector"); picker.AutomaticReadEnabled = false;
                window.Show(); window.BoilerModeForChecks("imported"); window.PreviewStep(0);
                await Flush(window);
                Require(window.FindName("TargetChoices") == null && window.FindName("ExternalTarget") == null && window.FindName("BootPrerequisitesCard") == null,
                    language + "-redundant-target-and-prerequisites-controls-removed");
                Require(picker.Disks.IsVisible, language + "-disk-list-visible-on-location-page");
                if (language == "zh-CN")
                    Require(((TextBlock)window.FindName("PageTitle")).Text == "在你选择的位置安装 SteamOS", "location-page-title");
                void ReviewSelection(Func<Window, CheckBox[], Button, Task> inspect, bool proceed = false)
                {
                    Exception? dialogError = null;
                    var selectionSeen = false;
                    var confirmationSeen = false;
                    Window OpenDialog() => Application.Current.Windows.OfType<Window>().Single(w => w != window && w.IsVisible);
                    var interaction = window.Dispatcher.BeginInvoke(DispatcherPriority.ApplicationIdle, new Action(async () =>
                    {
                        Window? selection = null;
                        try
                        {
                            selection = OpenDialog();
                            var root = (Grid)selection.Content;
                            var list = (UniformGrid)root.Children.OfType<ScrollViewer>().Single().Content;
                            var review = root.Children.OfType<WrapPanel>().Single().Children.OfType<Button>().Single(b => b.Name == "SteamOsReview");
                            await inspect(selection, list.Children.OfType<CheckBox>().ToArray(), review);
                            selectionSeen = true;
                            if (!proceed) { selection.Close(); return; }
                            _ = window.Dispatcher.BeginInvoke(DispatcherPriority.ApplicationIdle, new Action(() =>
                            {
                                var activeConfirmation = OpenDialog();
                                confirmationSeen = true;
                                activeConfirmation.Close();
                            }));
                            review.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                        }
                        catch (Exception error) { dialogError = error; selection?.Close(); }
                    }));
                    picker.ReinstallSteamOsButton.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                    interaction.Abort();
                    if (dialogError != null) throw dialogError;
                    if (!selectionSeen || proceed && !confirmationSeen) throw new IOException("The SteamOS partition workflow did not open the expected dialogs.");
                }
                picker.LoadSnapshot(remnants, true);
                ReviewSelection(async (dialog, choices, _) =>
                {
                    Require(choices.Where(c => c.IsEnabled && c.IsChecked == true).Select(c => (long)c.Tag).SequenceEqual(bootStarts),
                        language + "-residual-three-boot-partitions-all-selected");
                    await Capture(dialog, language + "-residual-three");
                });
                var protectedBoot = JsonNode.Parse(json)!;
                var systemEsp = protectedBoot[0]!["partitions"]!.AsArray().Single(p => (string?)p!["gptName"] == "esp")!;
                systemEsp["isSystem"] = true; systemEsp["deletionAllowed"] = false;
                systemEsp["deletionBlockReason"] = "System, boot, hidden, offline, or read-only partition is protected.";
                picker.LoadSnapshot(protectedBoot.ToJsonString(), true);
                ReviewSelection(async (dialog, choices, _) =>
                {
                    Require(choices.Count(c => c.IsEnabled && c.IsChecked == true) == 7 && choices.Count(c => !c.IsEnabled && c.IsChecked != true) == 1,
                        language + "-real-system-esp-remains-disabled-while-seven-eligible-select");
                    await Capture(dialog, language + "-protected-system-esp");
                });
                picker.LoadSnapshot(json, true);
                Require(picker.FindName("Heading") == null && picker.InventoryStatus.Visibility == Visibility.Collapsed,
                    language + "-disk-list-has-no-static-intro");
                Require(picker.ReinstallSteamOsButton.IsEnabled, language + "-empty-selection-offers-reinstallation");
                var unknownReason = JsonNode.Parse(json)!;
                unknownReason[0]!["partitions"]!.AsArray().Single(p => (string?)p!["gptName"] == "esp")!["deletionBlockReason"] = "Unrecognized protection reason.";
                var unknownStatus = PartitionInventory.Read(unknownReason.ToJsonString()).Single().Rows.Single(r => r.SteamOsRole == "esp").Status;
                Require(unknownStatus.Contains("Unrecognized protection reason.") && unknownStatus.Contains(language == "en-US" ? "Protected" : "受保护"), language + "-unknown-reason-remains-protected");
                unknownReason[0]!["partitions"]!.AsArray().Single(p => (string?)p!["gptName"] == "rootfs-A")!["deletionBlockReason"] = "Unrecognized protection reason.";
                Require(!PartitionInventory.Read(unknownReason.ToJsonString()).Single().Rows.Single(r => r.SteamOsRole == "rootfs-A").CanDelete, language + "-unknown-reason-blocks-conflicting-allow");
                ReviewSelection((dialog, choices, review) =>
                {
                    Require(choices.Select(c => (long)c.Tag).SequenceEqual(starts) && choices.All(c => c.IsEnabled && c.IsChecked == true),
                        language + "-all-steamos-boot-rows-selectable");
                    var actions = ((Grid)dialog.Content).Children.OfType<WrapPanel>().Single().Children.OfType<Button>().ToArray();
                    actions.Single(b => b.Name == "SteamOsClear").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                    Require(!review.IsEnabled && choices.All(c => c.IsChecked != true), language + "-empty-selection-cannot-proceed");
                    actions.Single(b => b.Name == "SteamOsSelectAll").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                    choices[0].IsChecked = false;
                    return Task.CompletedTask;
                }, proceed: true);
                Require(picker.SteamOsDeleteRequest().SteamOsStarts!.SequenceEqual(starts.Skip(1)), language + "-delete-preserves-manual-subset");
                picker.RefreshLanguage();
                Require(picker.SteamOsDeleteRequest().SteamOsStarts!.SequenceEqual(starts.Skip(1)), language + "-language-refresh-retains-selection");
                foreach (var size in new[] { new Size(1060, 720), new Size(640, 360) })
                {
                    window.Width = size.Width; window.Height = size.Height;
                    picker.Disks.BringIntoView(); await Flush(window);
                    var scroll = (ScrollViewer)window.FindName("PageScroll");
                    Require(scroll.ExtentWidth <= scroll.ViewportWidth + 1, language + "-no-horizontal-overflow-" + size.Width);
                    Require(Fits(picker.RefreshButton, scroll) && SameRowOnRight(picker.RefreshButton, picker.Disks),
                        language + "-refresh-is-to-the-right-of-the-disk-list-" + size.Width);
                    picker.ReinstallSteamOsButton.BringIntoView(); await Flush(window);
                    Require(Fits(picker.ReinstallSteamOsButton, scroll) && SameRowOnRight(picker.ReinstallSteamOsButton, picker.SteamOsStatus),
                        language + "-reinstall-is-to-the-right-of-the-steamos-status-" + size.Width);
                    await Capture(window, language + "-picker-" + size.Width);
                }
                picker.LoadSnapshot(crowdedSnapshot, true);
                ReviewSelection(async (dialog, choices, review) =>
                {
                    dialog.MaxHeight = 360; await Flush(dialog);
                    var root = (Grid)dialog.Content;
                    var list = root.Children.OfType<ScrollViewer>().Single();
                    Require(dialog.Width > 580 && ((UniformGrid)list.Content).Columns == 2 && choices.Length == 24, language + "-wide-two-column-selection");
                    Require(list.ScrollableHeight > 0 && Fits(review, root), language + "-selection-review-visible-without-scrolling");
                    await Capture(dialog, language + "-selection-24-short");
                });
                // Use the real helper's window configuration with synthetic data only.
                var confirmation = PartitionDeletion.Confirmation(crowdedRequest with { Example = false, Language = language });
                confirmation.ShowActivated = false; confirmation.MaxHeight = 360; confirmation.Show(); await Flush(confirmation);
                const uint noMoveSizeOrActivation = 0x0001 | 0x0002 | 0x0010;
                Require(SetWindowPos(new WindowInteropHelper(window).Handle, IntPtr.Zero, 0, 0, 0, 0, noMoveSizeOrActivation),
                    language + "-raise-installer-in-normal-window-order");
                Require(IsAbove(confirmation, window), language + "-elevated-confirmation-is-above-installer-without-activation");
                var confirmationRoot = (Grid)confirmation.Content;
                var details = confirmationRoot.Children.OfType<ScrollViewer>().Single();
                var columns = ((StackPanel)details.Content).Children.OfType<UniformGrid>().Single();
                var panel = confirmationRoot.Children.OfType<StackPanel>().Last();
                var consent = panel.Children.OfType<CheckBox>().Single();
                var buttons = panel.Children.OfType<WrapPanel>().Single().Children.OfType<Button>().ToArray();
                Require(confirmation.Width > 580 && columns.Columns == 2 && columns.Children.Count == 24
                    && details.ScrollableHeight > 0, language + "-wide-two-column-confirmation");
                Require(Fits(consent, confirmationRoot) && buttons.All(b => Fits(b, confirmationRoot)), language + "-confirmation-controls-visible-without-scrolling");
                await Capture(confirmation, language + "-confirmation-24-short");
                details.ScrollToBottom(); await Flush(confirmation);
                Require(details.VerticalOffset > 0 && buttons.All(b => Fits(b, confirmationRoot)), language + "-confirmation-buttons-stay-visible-after-scrolling");
                Require(buttons[0].IsDefault && buttons[0].IsCancel && !buttons[1].IsEnabled && !buttons[1].IsDefault, language + "-cancel-default-and-explicit-consent-required");
                consent.IsChecked = true; Require(buttons[1].IsEnabled, language + "-consent-enables-delete");
                consent.IsChecked = false; Require(!buttons[1].IsEnabled, language + "-unchecking-consent-disables-delete");
                confirmation.Close();
                picker.SelectReleasedSpace(result, true, request);
                Require(picker.Selection.Capacity >= PartitionInventory.Minimum
                    && ((InventoryDisk)picker.Disks.SelectedItem).Rows.All(r => r.SteamOsRole == null),
                    language + "-simulated-deletion-selects-released-space");
                Require(picker.InventoryStatus.Visibility == Visibility.Visible, language + "-deletion-result-remains-visible");
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
                picker.LoadSnapshot(disks.ToJsonString(), true);
                ReviewSelection((_, choices, _) => { choices[0].IsChecked = false; return Task.CompletedTask; }, proceed: true);
                picker.Disks.SelectedIndex = 1;
                Reject(() => picker.SteamOsDeleteRequest(), language + "-disk-change-clears-batch-selection");
                ReviewSelection((_, choices, _) => { choices[0].IsChecked = false; return Task.CompletedTask; }, proceed: true);
                picker.LoadSnapshot(json, true);
                Reject(() => picker.SteamOsDeleteRequest(), language + "-refresh-clears-batch-selection");
                picker.AllowPhysicalDeletion = false; picker.LoadSnapshot(json, false);
                Require(!picker.ReinstallSteamOsButton.IsEnabled, language + "-rehearsal-blocks-physical-empty-selection");
                picker.ReinstallSteamOsButton.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                Reject(() => picker.SteamOsDeleteRequest(), language + "-rehearsal-blocks-physical-batch");

                // Run the real reinstall click handler against synthetic inventory. Cancel before any delete request.
                var beforeScan = JsonNode.Parse(disks.ToJsonString())!.AsArray();
                beforeScan[1]!["partitions"] = new JsonArray();
                var scans = 0;
                picker.AllowPhysicalDeletion = true;
                picker.ReadDiskSnapshotAsync = _ => { scans++; return Task.FromResult(disks.ToJsonString()); };
                picker.LoadSnapshot(beforeScan.ToJsonString(), false);
                picker.Disks.SelectedIndex = 1;
                Require(picker.ReinstallSteamOsButton.IsEnabled && ((InventoryDisk)picker.Disks.SelectedItem).Rows.All(r => r.SteamOsRole == null),
                    language + "-reinstall-allows-a-fresh-scan-without-cached-steamos-rows");
                ReviewSelection((_, choices, _) =>
                {
                    Require(scans == 1 && ((InventoryDisk)picker.Disks.SelectedItem).Number == 1 && choices.Length == 8,
                        language + "-click-scans-selected-disk-and-opens-selection-directly");
                    return Task.CompletedTask;
                });
                var replaced = JsonNode.Parse(disks.ToJsonString())!.AsArray();
                replaced[1]!["guid"] = Guid.NewGuid().ToString();
                picker.ReadDiskSnapshotAsync = _ => Task.FromResult(replaced.ToJsonString());
                picker.ReinstallSteamOsButton.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                Require(picker.Disks.SelectedItem == null && !picker.ReinstallSteamOsButton.IsEnabled,
                    language + "-scan-stops-if-selected-disk-is-replaced");
                picker.ReadDiskSnapshotAsync = _ => Task.FromResult(beforeScan.ToJsonString());
                picker.LoadSnapshot(beforeScan.ToJsonString(), false);
                picker.Disks.SelectedIndex = 1;
                picker.ReinstallSteamOsButton.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                Require(picker.Disks.SelectedItem is InventoryDisk { Number: 1 } scanned && scanned.Rows.All(r => r.SteamOsRole == null)
                    && picker.SteamOsStatus.Text.StartsWith(language == "en-US" ? "No verifiable" : "未识别到"),
                    language + "-no-steamos-displays-result-without-switching-to-other-disk");
                window.Close(); window = null;
            }
        }
        finally { window?.Close(); InstallerI18n.SetLanguage(originalLanguage); }
        var report = JsonSerializer.Serialize(new { passed = true, checks, captures, captureErrors, directory,
            scope = "Synthetic GPT snapshots, batch simulation, WPF routed controls and layout only. No native disk queries, deletion, installation, firmware writes or device acceptance." }, new JsonSerializerOptions { WriteIndented = true });
        await File.WriteAllTextAsync(Path.Combine(directory, "checks.json"), report); return report;
    }
    private static bool Fits(FrameworkElement element, FrameworkElement viewport)
    {
        var bounds = element.TransformToAncestor(viewport).TransformBounds(new Rect(0, 0, element.ActualWidth, element.ActualHeight));
        return bounds.Left >= -1 && bounds.Right <= viewport.ActualWidth + 1 && bounds.Top >= -1 && bounds.Bottom <= viewport.ActualHeight + 1;
    }
    private static bool SameRowOnRight(FrameworkElement button, FrameworkElement adjacent)
    {
        var parent = (Grid)button.Parent;
        var buttonAt = button.TranslatePoint(new Point(), parent);
        var adjacentAt = adjacent.TranslatePoint(new Point(), parent);
        return button.Parent == adjacent.Parent && buttonAt.X >= adjacentAt.X + adjacent.ActualWidth - 1
            && Math.Abs(buttonAt.Y + button.ActualHeight / 2 - adjacentAt.Y - adjacent.ActualHeight / 2) <= 1;
    }
    private static bool IsAbove(Window dialog, Window installer)
    {
        const uint nextWindow = 2;
        var main = new WindowInteropHelper(installer).Handle;
        for (var handle = new WindowInteropHelper(dialog).Handle; handle != IntPtr.Zero; handle = GetWindow(handle, nextWindow))
            if (handle == main) return true;
        return false;
    }

    [DllImport("user32.dll")]
    private static extern IntPtr GetWindow(IntPtr handle, uint command);

    [DllImport("user32.dll")]
    private static extern bool SetWindowPos(IntPtr handle, IntPtr insertAfter, int x, int y, int width, int height, uint flags);

    private static async Task Flush(Window window) { window.UpdateLayout(); await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle); }
}
