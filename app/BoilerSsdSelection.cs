using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;

namespace OxpInstaller;

public partial class MainWindow
{
    private bool updatingSsdSelection;
    private bool? ssdPreference;
    private SsdBootRepair.PreferenceKey? ssdPreferenceTarget;
    private bool HasSsdProvider() => deviceComponentChoices.Values.Any(box => box.IsEnabled && box.Tag is JsonElement item && SsdBootRepair.IsProvider(item));
    private void RecordSsdPreference(CheckBox box)
    {
        if (!updatingSsdSelection && SsdTargetMatches()) ssdPreference = box.IsChecked == true;
    }
    private void ResetSsdPreference()
    {
        ssdPreference = null; ssdPreferenceTarget = null;
        ClearSsdSelection();
    }
    private void ClearSsdSelection()
    {
        updatingSsdSelection = true;
        try
        {
            if (optionsPanel.OptionBoxes.TryGetValue(SsdBootRepair.Option, out var fallback)) fallback.IsChecked = false;
            foreach (var box in deviceComponentChoices.Values.Where(box => box.Tag is JsonElement item && SsdBootRepair.IsProvider(item))) box.IsChecked = false;
        }
        finally { updatingSsdSelection = false; }
        UpdateSsdVisibility();
    }
    private void SyncSsdSelection()
    {
        UpdateSsdVisibility();
        if (!SsdTargetMatches()) return;
        var target = SsdBootRepair.PreferenceTarget(acknowledgedSelection!);
        if (ssdPreferenceTarget != target) { ssdPreferenceTarget = target; ssdPreference = true; }
        var provider = deviceComponentChoices.Values.SingleOrDefault(box => box.IsEnabled && box.Tag is JsonElement item && SsdBootRepair.IsProvider(item));
        updatingSsdSelection = true;
        try
        {
            if (optionsPanel.OptionBoxes.TryGetValue(SsdBootRepair.Option, out var fallback)) fallback.IsChecked = provider == null && ssdPreference == true;
            if (provider != null) provider.IsChecked = ssdPreference == true;
            if (ssdPreference == true && optionsPanel.OptionBoxes.TryGetValue("core:maintenance", out var maintenance)) maintenance.IsChecked = true;
        }
        finally { updatingSsdSelection = false; }
    }
    private bool SsdTargetMatches()
    {
        if (installMode != "custom" || !physicalInstallation || acknowledgedSelection == null) return false;
        try
        {
            return acknowledgedSelection == PartitionSelector.Selection
                && SsdBootRepair.FromSelection(acknowledgedSelection).Disposition == "match";
        }
        catch (Exception error) when (error is IOException or InvalidOperationException or ArgumentException or JsonException)
        { return false; }
    }
    private void UpdateSsdVisibility()
    {
        var matches = SsdTargetMatches();
        var provider = deviceComponentChoices.Values.SingleOrDefault(box => box.IsEnabled && box.Tag is JsonElement item && SsdBootRepair.IsProvider(item));
        optionsPanel.SsdOptionsVisibility(matches, provider);
    }
}
