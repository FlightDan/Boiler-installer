using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows.Controls;
using System.Windows;

namespace OxpInstaller;

internal static class SsdUiChecks
{
    internal static string Run()
    {
        var window = new MainWindow(rehearsal: false);
        var flags = BindingFlags.Instance | BindingFlags.NonPublic;
        var type = typeof(MainWindow);
        object? Call(string name, params object?[] args) => type.GetMethod(name, flags)!.Invoke(window, args);
        T Field<T>(string name) => (T)type.GetField(name, flags)!.GetValue(window)!;
        void Require(bool value, string name) { if (!value) throw new IOException(name); }
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
            Require(options.SsdOptions!.Visibility == Visibility.Visible, "fallback-visible-in-custom-section");
            fallback.IsChecked = false; Call("SyncSsdSelection");
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
            Require(provider.IsChecked == true && fallback.IsChecked == false && provider.Parent == section && provider.Visibility == Visibility.Visible && options.SsdOptions!.Visibility == Visibility.Collapsed, "provider-takes-priority-in-custom-section");
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
            return "SSD route and target-scoped default, opt-out, provider priority, and target-change checks passed; synthetic inventory only.";
        }
        finally { window.Close(); }
    }
}
