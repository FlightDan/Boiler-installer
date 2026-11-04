using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows.Controls;
using System.Windows;

namespace OxpInstaller;

internal static class SsdUiChecks
{
    private static void CheckBe201Selection()
    {
        var choices = new Be201Selection();
        var first = new Be201Selection.TargetKey(Guid.Parse("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), "NVME", "DISK", "ONE", 100);
        var second = first with { Serial = "TWO", DiskId = Guid.Parse("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb") };
        void Require(bool value, string name) { if (!value) throw new IOException("be201-" + name); }
        foreach (var match in new[] { Be201Selection.TargetMatch.Unknown, Be201Selection.TargetMatch.Candidate, Be201Selection.TargetMatch.Different })
            Require(!choices.Get(first, match, true, true).Selected && !choices.Get(first, match, true, true).Enabled, "unconfirmed-not-selected");
        var confirmed = Be201Selection.TargetMatch.Confirmed;
        Require(!choices.Get(null, confirmed, true, true).Selected, "no-target");
        Require(!choices.Get(first, confirmed, false, false).Enabled, "no-provider");
        var state = choices.Get(first, confirmed, true, true);
        Require(state.Enabled && state.Selected && state.Source == Be201Selection.Provider.Model, "model-first-default");
        choices.Remember(first, confirmed, true, true, false);
        Require(!choices.Get(first, confirmed, true, true).Selected, "refresh-keeps-opt-out");
        state = choices.Get(first, confirmed, false, true);
        Require(state.Source == Be201Selection.Provider.Common && !state.Selected, "provider-switch-keeps-opt-out");
        Require(choices.Get(second, confirmed, true, true).Selected, "other-target-default");
        choices.Remember(second, confirmed, true, true, false);
        Require(!choices.Get(first, confirmed, true, true).Selected && !choices.Get(second, confirmed, true, true).Selected, "target-return-keeps-opt-out");
        choices.Remember(first, Be201Selection.TargetMatch.Unknown, true, true, true);
        Require(!choices.Get(first, confirmed, true, true).Selected, "unknown-cannot-change-preference");
    }

    internal static string Run()
    {
        CheckBe201Selection();
        var window = new MainWindow(rehearsal: false);
        var flags = BindingFlags.Instance | BindingFlags.NonPublic;
        var type = typeof(MainWindow);
        object? Call(string name, params object?[] args) => type.GetMethod(name, flags)!.Invoke(window, args);
        T Field<T>(string name) => (T)type.GetField(name, flags)!.GetValue(window)!;
        void Require(bool value, string name) { if (!value) throw new IOException(name); }
        bool HasSource(CheckBox box) => ((System.Collections.IEnumerable)Call("CustomBuildRows")!).Cast<object>()
            .Any(row => ReferenceEquals(row.GetType().GetProperty("Source")!.GetValue(row), box));
        try
        {
            var route = Field<int[]>("boilerRoute");
            Require(route.SequenceEqual(new[] { MainWindow.AdaptationStep, 1, 0, 2, 4, 3, 5, 6 }), "source-target-content-route");
            var exportPlan = JsonSerializer.SerializeToElement(new { ssd_repair = new { id = SsdBootRepair.Option } });
            try
            {
                BoilerImage.ExportAsync("missing.img", "unused.boiler.zip", exportPlan, CancellationToken.None).GetAwaiter().GetResult();
                throw new InvalidOperationException("Destination-bound SSD image was exportable.");
            }
            catch (IOException error) when (error.Message.Contains("destination-bound SSD records", StringComparison.Ordinal)) { }
            type.GetField("bootPrerequisitesAcknowledged", flags)!.SetValue(window, true);
            ((Grid)window.FindName("BootPrerequisitesOverlay")).Visibility = Visibility.Collapsed;
            ((TextBox)window.FindName("ImagePath")).Text = "";
            window.PreviewStep(0);
            ((Button)window.FindName("NextButton")).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            Require(Field<int>("step") == 1 && ((Grid)window.FindName("SafetyOverlay")).Visibility != Visibility.Visible,
                "unverified-source-cannot-advance-to-target-consent");
            var picker = (PartitionPicker)window.FindName("PartitionSelector"); picker.AutomaticReadEnabled = false;
            var snapshot = JsonNode.Parse(PartitionInventory.ExampleJson)!.AsArray();
            var disk = snapshot[0]!; disk["model"] = "Predator SSD GM7 1TB"; disk["bus"] = "NVMe"; disk["serial"] = "synthetic-serial";
            picker.LoadSnapshot(snapshot.ToJsonString(), false);
            picker.Partitions.SelectedItem = picker.Partitions.Items.Cast<PartitionRow>().First(r => r.Space != null);
            picker.CapacityInput.Text = "128";
            var options = Field<InstallerOptionsPanel>("optionsPanel");
            var fallback = options.OptionBoxes[SsdBootRepair.Option];
            var section = options.CustomComponents;
            Require(section.Visibility == Visibility.Collapsed, "custom-section-hidden-before-review");
            Require(fallback.IsChecked != true && !(bool)Call("SsdTargetMatches")!, "no-choice-before-target-review");
            type.GetField("acknowledgedSelection", flags)!.SetValue(window, picker.Selection);
            Call("SyncSsdSelection");
            Require(fallback.IsChecked == true && section.Visibility == Visibility.Visible, "matching-target-defaults-on");
            Require(options.SsdOptions!.Visibility == Visibility.Collapsed, "fallback-source-hidden-in-settings");
            Require(HasSource(fallback), "searchable-list-reuses-matching-fallback-source");
            window.Show(); window.PreviewStep(2); window.UpdateLayout();
            var settings = Field<Expander>("customSettings");
            settings.IsExpanded = false; window.UpdateLayout();
            Require(Field<Button>("currentHardwareDetectButton").IsVisible, "hardware-detection-visible-with-settings-collapsed");
            settings.IsExpanded = true; window.UpdateLayout();
            Require(BoilerCustomBuildChecks.HasVisibleChoice(window, SsdBootRepair.Option)
                && !fallback.IsVisible && !options.SsdOptions!.IsVisible,
                "expanded-settings-have-one-visible-ssd-choice");
            var fallbackRow = ((System.Collections.IEnumerable)Call("CustomBuildRows")!).Cast<object>()
                .Single(row => ReferenceEquals(row.GetType().GetProperty("Source")!.GetValue(row), fallback));
            Call("SetBuildRow", fallbackRow, false, new HashSet<string>()); Call("SyncSsdSelection");
            Require(fallback.IsChecked == false, "explicit-uncheck-survives-refresh");
            disk["unrelated_inventory_change"] = "changed";
            picker.LoadSnapshot(snapshot.ToJsonString(), false); // Includes the real Clear/Changed event.
            Require(Field<PartitionSelection?>("acknowledgedSelection") == null && fallback.IsChecked != true,
                "refresh-clears-consent-and-selected-repair");
            picker.Partitions.SelectedItem = picker.Partitions.Items.Cast<PartitionRow>().First(r => r.Space != null);
            picker.CapacityInput.Text = "128";
            type.GetField("acknowledgedSelection", flags)!.SetValue(window, picker.Selection);
            Call("SyncSsdSelection");
            Require(fallback.IsChecked == false, "same-target-reack-preserves-explicit-uncheck");
            disk["guid"] = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
            disk["serial"] = "different-synthetic-serial";
            picker.LoadSnapshot(snapshot.ToJsonString(), false);
            picker.Partitions.SelectedItem = picker.Partitions.Items.Cast<PartitionRow>().First(r => r.Space != null);
            picker.CapacityInput.Text = "128";
            type.GetField("acknowledgedSelection", flags)!.SetValue(window, picker.Selection);
            Call("SyncSsdSelection");
            Require(fallback.IsChecked == true, "new-physical-identity-defaults-on-after-reack");
            fallback.IsChecked = false;
            var providerData = JsonSerializer.SerializeToElement(new { id = "device:gm7", minimum_engine_version = "0.1.3",
                optional = true, ssd_boot_policy = new { id = SsdBootRepair.Option, version = 1 }, files = Array.Empty<object>(), hooks = new { } });
            var invalidProvider = JsonSerializer.SerializeToElement(new { id = "device:gm7", minimum_engine_version = "0.1.3",
                optional = true, ssd_boot_policy = new { id = SsdBootRepair.Option, version = 1 }, files = Array.Empty<object>(),
                requires = new[] { "device:other" }, hooks = new { } });
            Require(!SsdBootRepair.IsProvider(invalidProvider), "provider-dependencies-rejected");
            var provider = new CheckBox { Tag = providerData };
            Field<Dictionary<string, CheckBox>>("deviceComponentChoices").Add("device:gm7", provider);
            options.SetSsdProvider(provider);
            Call("SyncSsdSelection");
            Require(provider.IsChecked == false && fallback.IsChecked == false, "unchecked-preference-survives-provider-switch");
            provider.IsChecked = true; Call("RecordSsdPreference", provider); Call("SyncSsdSelection");
            Require(provider.IsChecked == true && fallback.IsChecked == false && provider.Parent == section && provider.Visibility == Visibility.Collapsed && options.SsdOptions!.Visibility == Visibility.Collapsed, "provider-takes-priority-in-custom-section");
            Require(HasSource(provider) && !HasSource(fallback), "searchable-list-reuses-provider-without-fallback");
            window.UpdateLayout();
            Require(BoilerCustomBuildChecks.HasVisibleChoice(window, "device:gm7") && !provider.IsVisible
                && !fallback.IsVisible, "expanded-settings-have-one-visible-provider-choice");
            Field<Dictionary<string, CheckBox>>("deviceComponentChoices").Remove("device:gm7"); options.SetSsdProvider(null); Call("SyncSsdSelection");
            Require(fallback.IsChecked == true, "provider-choice-transfers-to-fallback");
            Call("ResetSsdPreference");
            Require(fallback.IsChecked == false, "changed-target-clears-prior-choice");
            Call("SyncSsdSelection");
            Require(fallback.IsChecked == true, "new-matching-target-defaults-on");
            disk["model"] = "Other SSD";
            picker.LoadSnapshot(snapshot.ToJsonString(), false);
            picker.Partitions.SelectedItem = picker.Partitions.Items.Cast<PartitionRow>().First(r => r.Space != null);
            picker.CapacityInput.Text = "128";
            type.GetField("acknowledgedSelection", flags)!.SetValue(window, picker.Selection);
            Call("SyncSsdSelection");
            Require(!(bool)Call("SsdTargetMatches")! && fallback.IsChecked != true && section.Visibility == Visibility.Collapsed, "other-ssd-has-no-selection");
            Require(!HasSource(fallback), "searchable-list-excludes-unmatched-ssd");
            return "SSD route and target-scoped default, opt-out, provider priority, and target-change checks passed; synthetic inventory only.";
        }
        finally { window.Close(); }
    }
}
