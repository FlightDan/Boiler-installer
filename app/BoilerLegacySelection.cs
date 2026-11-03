using System.Text.Json;
using System.Windows;
using System.Windows.Controls;

namespace OxpInstaller;

public partial class MainWindow
{
    private readonly Dictionary<string, CheckBox> deviceLegacyChoices = new();
    private CheckBox? deviceLegacyUpdateHold;
    private string? deviceLegacyBridge;
    private bool updatingLegacyChoices;
    private TextBlock? deviceLegacyHeading, deviceLegacyNotice;

    private DeviceLegacySelection? CurrentDeviceLegacyOptions
    {
        get
        {
            if (deviceLegacyBridge == null) return null;
            string[] Selected(string category) => deviceLegacyChoices.Where(pair => pair.Value.IsChecked == true
                && pair.Value.Tag is JsonElement item && item.GetProperty("category").GetString() == category).Select(pair => pair.Key).ToArray();
            var repairs = Selected("repairs"); var plugins = Selected("plugins");
            return repairs.Length + plugins.Length == 0 ? null : new(1, repairs, plugins, deviceLegacyUpdateHold?.IsChecked == true);
        }
    }

    private void ClearLegacyDeviceOptions()
    {
        deviceLegacyChoices.Clear(); deviceLegacyBridge = null; deviceLegacyUpdateHold = null;
        deviceLegacyHeading = deviceLegacyNotice = null;
    }

    private void BuildLegacyDeviceOptions(JsonElement package, DeviceLegacySelection? prior)
    {
        // Older packages are frozen contracts: their legacy_capabilities are descriptive only.
        if (!package.TryGetProperty("selection_contract", out var contract)
            || contract.GetProperty("kind").GetString() != "oxp3-legacy-options-v1"
            || contract.GetProperty("component").GetString() != "oxp3:legacy-adaptation"
            || !deviceComponentChoices.ContainsKey("oxp3:legacy-adaptation")
            || !package.TryGetProperty("legacy_capabilities", out var capabilities) || capabilities.ValueKind != JsonValueKind.Array) return;
        deviceLegacyBridge = "oxp3:legacy-adaptation";
        deviceAdaptationsChoice!.Visibility = Visibility.Collapsed;
        foreach (var pair in deviceComponentChoices.Where(pair => pair.Key != deviceLegacyBridge && (pair.Value.Tag is not JsonElement item || !SsdBootRepair.IsProvider(item)))) deviceComponentsPanel!.Children.Add(pair.Value);
        deviceLegacyHeading = new TextBlock { FontSize = 17, FontWeight = FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap, Margin = new(0, 12, 0, 6) };
        deviceComponentsPanel!.Children.Add(deviceLegacyHeading);
        deviceLegacyNotice = new TextBlock { TextWrapping = TextWrapping.Wrap, Foreground = BoilerBrush("#485B73"), Margin = new(0, 0, 0, 10) };
        deviceComponentsPanel.Children.Add(deviceLegacyNotice);
        foreach (var item in capabilities.EnumerateArray())
        {
            var id = item.GetProperty("id").GetString()!;
            var check = new CheckBox { Name = "Legacy_" + id.Replace(':', '_').Replace('-', '_'), MinHeight = 48,
                Tag = item.Clone(), Content = new TextBlock { TextWrapping = TextWrapping.Wrap },
                IsEnabled = item.TryGetProperty("available", out var available) && available.ValueKind == JsonValueKind.True };
            deviceLegacyChoices.Add(id, check); deviceComponentsPanel.Children.Add(check);
        }
        // A dependent cannot be selected when its prerequisite is unavailable or absent.
        bool Available(string id, HashSet<string> seen)
        {
            if (!deviceLegacyChoices.TryGetValue(id, out var box) || !box.IsEnabled) return false;
            if (!seen.Add(id)) return true;
            return LegacyDependencies(box).All(dependency => Available(dependency, seen));
        }
        foreach (var pair in deviceLegacyChoices) pair.Value.IsEnabled = Available(pair.Key, new());
        updatingLegacyChoices = true;
        try
        {
            foreach (var pair in deviceLegacyChoices)
            {
                var chosen = prior != null ? prior.Repairs.Concat(prior.Plugins).Contains(pair.Key)
                    : true;
                if (chosen && pair.Value.IsEnabled) SetLegacyClosure(pair.Key, true, new());
            }
        }
        finally { updatingLegacyChoices = false; }
        if (deviceLegacyChoices.TryGetValue("oxp3:decky", out var deviceDecky) && deviceDecky.IsEnabled
            && optionsPanel.OptionBoxes.TryGetValue("core:decky", out var coreDecky))
        {
            updatingLegacyChoices = true;
            try { coreDecky.IsChecked = deviceDecky.IsChecked == true; }
            finally { updatingLegacyChoices = false; }
        }
        foreach (var pair in deviceLegacyChoices)
        {
            void Changed(object sender, RoutedEventArgs args)
            {
                if (updatingLegacyChoices) return;
                updatingLegacyChoices = true;
                try { SetLegacyClosure(pair.Key, pair.Value.IsChecked == true, new()); }
                finally { updatingLegacyChoices = false; }
                if (pair.Key == "oxp3:decky" && pair.Value.IsChecked != true)
                {
                    updatingDeviceAdaptations = true;
                    try { foreach (var box in deviceComponentChoices.Values.Where(IsStaticDeckyConsumer)) box.IsChecked = false; }
                    finally { updatingDeviceAdaptations = false; }
                    if (deviceComponentChoices.TryGetValue(FingerprintId, out var fingerprint)) RecordFingerprintPreference(fingerprint);
                }
                SyncLegacyBridge(); ConfigurationChanged();
            }
            pair.Value.Checked += Changed; pair.Value.Unchecked += Changed;
        }
        deviceLegacyUpdateHold = new CheckBox { Name = "AcknowledgeLegacyUpdateHold", MinHeight = 48, IsChecked = prior?.AcknowledgeLegacyUpdateHold ?? true,
            Content = new TextBlock { TextWrapping = TextWrapping.Wrap }, Margin = new(0, 12, 0, 0) };
        deviceLegacyUpdateHold.Checked += (_, _) => ConfigurationChanged();
        deviceLegacyUpdateHold.Unchecked += (_, _) => ConfigurationChanged();
        deviceComponentsPanel.Children.Add(deviceLegacyUpdateHold);
        RefreshLegacyDeviceLabels(); SyncLegacyBridge();
    }

    private static IEnumerable<string> LegacyDependencies(CheckBox box) => box.Tag is JsonElement item
        && item.TryGetProperty("dependencies", out var dependencies) ? dependencies.EnumerateArray().Select(value => value.GetString()!) : [];

    private void SetLegacyClosure(string id, bool selected, HashSet<string> seen)
    {
        if (!seen.Add(id) || !deviceLegacyChoices.TryGetValue(id, out var box)) return;
        box.IsChecked = selected && box.IsEnabled;
        if (selected)
        {
            foreach (var dependency in LegacyDependencies(box)) SetLegacyClosure(dependency, true, seen);
        }
        else
            foreach (var dependent in deviceLegacyChoices.Where(pair => LegacyDependencies(pair.Value).Contains(id)).Select(pair => pair.Key))
                SetLegacyClosure(dependent, false, seen);
        if (id == "oxp3:decky" && box.IsEnabled && optionsPanel.OptionBoxes.TryGetValue("core:decky", out var coreDecky))
            coreDecky.IsChecked = box.IsChecked == true;
    }

    private static bool IsStaticDeckyConsumer(CheckBox box)
    {
        if (box.Tag is JsonElement component && component.TryGetProperty("id", out var id)
            && id.GetString() == "oxp3:fingerprint-unlock") return true;
        const string prefix = "home/deck/homebrew/plugins/";
        if (box.Tag is not JsonElement item || !item.TryGetProperty("files", out var files)
            || files.ValueKind != JsonValueKind.Array) return false;
        foreach (var file in files.EnumerateArray())
        {
            if (file.ValueKind != JsonValueKind.Object || !file.TryGetProperty("target", out var target)
                || target.ValueKind != JsonValueKind.String) continue;
            var path = target.GetString()!;
            if (!path.StartsWith(prefix, StringComparison.Ordinal)) continue;
            var separator = path.IndexOf('/', prefix.Length);
            if (separator > prefix.Length && separator < path.Length - 1) return true;
        }
        return false;
    }

    private void EnsureStaticDeckyProvider()
    {
        if (!deviceComponentChoices.Values.Any(box => box.IsChecked == true && IsStaticDeckyConsumer(box))) return;
        if (deviceLegacyBridge != null)
        {
            if (!deviceLegacyChoices.TryGetValue("oxp3:decky", out var provider) || !provider.IsEnabled)
            {
                updatingDeviceAdaptations = true;
                try { foreach (var box in deviceComponentChoices.Values.Where(IsStaticDeckyConsumer)) box.IsChecked = false; }
                finally { updatingDeviceAdaptations = false; }
                return;
            }
            updatingLegacyChoices = true;
            try { SetLegacyClosure("oxp3:decky", true, new()); }
            finally { updatingLegacyChoices = false; }
            SyncLegacyBridge();
        }
        else if (optionsPanel.OptionBoxes.TryGetValue("core:decky", out var coreDecky)) coreDecky.IsChecked = true;
    }

    private void CoreDeckyProviderChecked(object sender, RoutedEventArgs args)
    {
        if (updatingLegacyChoices || updatingDeviceAdaptations) return;
        if (deviceLegacyBridge == null)
        {
            if (((CheckBox)sender).IsChecked == true) return;
            updatingDeviceAdaptations = true;
            try { foreach (var box in deviceComponentChoices.Values.Where(IsStaticDeckyConsumer)) box.IsChecked = false; }
            finally { updatingDeviceAdaptations = false; }
            if (deviceComponentChoices.TryGetValue(FingerprintId, out var fingerprint)) RecordFingerprintPreference(fingerprint);
            RefreshDeviceAdaptationsChoice(); ConfigurationChanged();
            return;
        }
        if (!deviceLegacyChoices.TryGetValue("oxp3:decky", out var deviceDecky) || !deviceDecky.IsEnabled) return;
        updatingLegacyChoices = true;
        try { SetLegacyClosure("oxp3:decky", ((CheckBox)sender).IsChecked == true, new()); }
        finally { updatingLegacyChoices = false; }
        if (((CheckBox)sender).IsChecked != true)
        {
            updatingDeviceAdaptations = true;
            try { foreach (var box in deviceComponentChoices.Values.Where(IsStaticDeckyConsumer)) box.IsChecked = false; }
            finally { updatingDeviceAdaptations = false; }
            if (deviceComponentChoices.TryGetValue(FingerprintId, out var fingerprint)) RecordFingerprintPreference(fingerprint);
        }
        SyncLegacyBridge(); ConfigurationChanged();
    }

    private void SyncLegacyBridge()
    {
        if (deviceLegacyBridge == null) return;
        updatingDeviceAdaptations = true;
        try { deviceComponentChoices[deviceLegacyBridge].IsChecked = deviceLegacyChoices.Values.Any(box => box.IsChecked == true); }
        finally { updatingDeviceAdaptations = false; }
    }

    private void RefreshLegacyDeviceLabels()
    {
        if (deviceLegacyHeading == null) return;
        deviceLegacyHeading.Text = PartitionInventory.L("OXP3 适配项目", "OXP3 adaptation items");
        deviceLegacyNotice!.Text = PartitionInventory.L("默认勾选全部可用项目，可按需取消。依赖自动联动；Opti2XMX 使用机型包 Decky。", "All available items are selected by default. Adjust as needed; dependencies follow automatically. Opti2XMX uses device-package Decky.");
        foreach (var pair in deviceLegacyChoices)
        {
            var item = (JsonElement)pair.Value.Tag;
            var text = PreviewText(item, "name", pair.Key);
            var description = PreviewText(item, "description", "");
            if (description.Length > 0) text += "\n" + description;
            if (!pair.Value.IsEnabled) text += "\n" + PreviewText(item, "reason", PartitionInventory.L("此项目或其依赖暂不可安装。", "This item or its dependency is unavailable."));
            ((TextBlock)pair.Value.Content).Text = text;
        }
        ((TextBlock)deviceLegacyUpdateHold!.Content).Text = PartitionInventory.L("为保留适配，暂停安装后进入系统时的首次更新。", "Pause the first system update after installation to preserve device support.");
        deviceLegacyUpdateHold.ToolTip = PartitionInventory.L("此选项会暂停 SteamOS 系统镜像更新，直到恢复更新策略；应用更新不受影响。", "SteamOS image updates remain paused until the update policy is restored. Application updates are unaffected.");
    }
}
