using System.IO;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Diagnostics;

namespace OxpInstaller;

internal sealed class BoilerProtocolFailure(string message, Exception? inner = null) : IOException(message, inner);

internal static class BoilerProtocol
{
    internal static async Task<JsonElement> RunAsync(IEnumerable<string> args, CancellationToken token)
    {
        var python = Path.Combine(Backend.Root, "runtime/python/python.exe");
        if (!File.Exists(python)) throw new IOException("缺少已验证的 Python 运行时，请先准备运行时。 / Prepare the verified Python runtime first.");
        string result;
        try
        {
            result = await Backend.ProcessAsync(python, new[] { "-B", "-c", "import sys; sys.path.insert(0,sys.argv.pop(1)); from protocol.__main__ import main; sys.exit(main())", Backend.Root }.Concat(args), _ => { }, token,
                new Dictionary<string, string> { ["PYTHONIOENCODING"] = "utf-8", ["PYTHONUTF8"] = "1" }, outputEncoding: Encoding.UTF8);
        }
        catch (InvalidOperationException error)
        {
            foreach (var line in error.Message.Split('\n').Reverse())
            {
                if (!line.TrimStart().StartsWith('{')) continue;
                try
                {
                    using var failure = JsonDocument.Parse(line);
                    if (failure.RootElement.TryGetProperty("error", out var detail)) throw new BoilerProtocolFailure(detail.GetString() ?? "Device package validation failed.", error);
                }
                catch (JsonException) { }
            }
            throw;
        }
        using var json = JsonDocument.Parse(result);
        if (!json.RootElement.GetProperty("ok").GetBoolean()) throw new BoilerProtocolFailure(json.RootElement.GetProperty("error").GetString() ?? result);
        return json.RootElement.GetProperty("result").Clone();
    }
    internal static string RegistryCache => Path.Combine(InstallerWorkspace.DirectoryPath, "device-index");
    internal static string PackageRoot => Path.Combine(Backend.Root, "cache/device-packages");
    internal static async Task<string> CachePackageAsync(string folder, string digest, CancellationToken token)
    {
        if (!System.Text.RegularExpressions.Regex.IsMatch(digest, "^[0-9a-f]{64}$")) throw new IOException("Invalid device manifest digest.");
        var root = Path.GetFullPath(PackageRoot);
        var cached = Path.Combine(root, digest[..24]);
        MediaSource.Ordinary(cached, root, false);
        if (Directory.Exists(cached))
        {
            try { await RunAsync(["package", cached, "--manifest-sha256", digest], token); return cached; }
            catch (BoilerProtocolFailure)
            {
                // Preserve a broken generation for diagnostics. Never merge new
                // files into it or move through a redirected directory.
                if ((File.GetAttributes(cached) & FileAttributes.ReparsePoint) != 0) throw new IOException("Device cache cannot be a redirected directory.");
                var quarantine = cached + ".invalid-" + Guid.NewGuid().ToString("N");
                if (Path.GetDirectoryName(cached) != root || Path.GetDirectoryName(quarantine) != root) throw new IOException("Device cache escapes its root.");
                Directory.Move(cached, quarantine);
            }
        }
        var staging = Path.Combine(root, ".verified-" + Guid.NewGuid().ToString("N"));
        await RunAsync(["import-device", folder, staging, "--developer"], token);
        await RunAsync(["package", staging, "--manifest-sha256", digest], token);
        // Publish only the complete inventory, with no .pyc/__pycache__ filtering.
        MediaSource.Ordinary(staging, root, false);
        Directory.Move(staging, cached);
        return cached;
    }
}

public partial class MainWindow
{
    private string? selectedDeviceDigest;
    private string? selectedDeviceTrust;
    private StackPanel? devicePanel;
    private TextBlock? deviceStatus;
    private TextBlock? deviceNotes;
    private TextBlock? deviceSourceLink;
    private Expander? deviceNotesExpander;
    private JsonElement? reviewedDeviceMetadata;
    private ComboBox? deviceList;
    private bool deviceActionBusy;
    private string? deviceActionError;
    private readonly Dictionary<string, CheckBox> deviceComponentChoices = new();
    private readonly Dictionary<(SsdBootRepair.PreferenceKey? Target, string Model, string Component), bool> modelResourceChoices = new();
    private SsdBootRepair.PreferenceKey? modelResourceTarget;
    private StackPanel? deviceComponentsPanel;
    private CheckBox? deviceAdaptationsChoice;
    private bool updatingDeviceAdaptations;

    private void RefreshDeviceAdaptationsChoice()
    {
        if (deviceAdaptationsChoice == null || updatingDeviceAdaptations) return;
        updatingDeviceAdaptations = true;
        try
        {
            var bulk = deviceComponentChoices.Values.Where(box => box.IsEnabled && !IsExplicitDeviceOption(box)).ToArray();
            var selected = bulk.Count(box => box.IsChecked == true);
            deviceAdaptationsChoice.IsChecked = selected == 0 ? false : selected == bulk.Length ? true : null;
        }
        finally { updatingDeviceAdaptations = false; }
    }

    private void ClearDeviceSelection()
    {
        importedDeviceFileName = null;
        selectedDevicePackage = selectedDeviceDigest = selectedDeviceTrust = null;
        optionsPanel.SetWirelessContext(installMode, hasPackage: adaptationBranch != "generic");
        displayedDeviceMetadata = null;
        reviewedDeviceMetadata = null;
        if (deviceNotesExpander != null) deviceNotesExpander.Visibility = Visibility.Collapsed;
        if (deviceSourceLink != null) deviceSourceLink.Visibility = Visibility.Collapsed;
        optionsPanel.SetSsdProvider(null);
        deviceComponentChoices.Clear();
        RefreshCurrentHardwareUi();
        ClearLegacyDeviceOptions();
        SyncSsdSelection();
        deviceAdaptationsChoice = null;
        if (deviceComponentsPanel != null) { ComponentOptionsHost.Children.Remove(deviceComponentsPanel); deviceComponentsPanel = null; }
        if (adaptationBranch == "device")
        {
            if (previewDeviceName != null) RenderPackageOptionPreview();
        }
        ConfigurationChanged();
    }

    private static string LocalizedValue(JsonElement value) => value.ValueKind == JsonValueKind.Object && value.TryGetProperty(InstallerI18n.Language == "en-US" ? "en" : "zh", out var text) ? text.ToString() : value.ToString();
    private static string DeviceText(JsonElement value)
    {
        if (value.ValueKind == JsonValueKind.Array) return string.Join("\n", value.EnumerateArray().Select(DeviceText));
        if (value.ValueKind == JsonValueKind.Object)
        {
            if (value.TryGetProperty("zh", out _) || value.TryGetProperty("en", out _)) return LocalizedValue(value);
            return string.Join(" · ", new[] { "severity", "status", "state", "issue", "note", "scope", "date", "architecture", "os_version", "build", "kernel", "evidence" }.Where(key => value.TryGetProperty(key, out _)).Select(key => DeviceText(value.GetProperty(key))));
        }
        return value.ToString();
    }
    private static string DescribeDevice(JsonElement item)
    {
        var lines = new List<string>();
        if (item.TryGetProperty("name", out var name)) lines.Add(LocalizedValue(name));
        if (item.TryGetProperty("version", out var version)) lines.Add(PartitionInventory.L("机型包版本：", "Package version: ") + version);
        var status = item.TryGetProperty("status", out var state) && state.ValueKind == JsonValueKind.String ? state.GetString() : "unverified";
            lines.Add(status switch { "supported" => PartitionInventory.L("适配状态：已支持（请查看验证范围）", "Compatibility: supported within the recorded validation scope"), "experimental" => PartitionInventory.L("适配状态：实验性，仍有待验证功能", "Compatibility: experimental; hardware validation is incomplete"), "blocked" => PartitionInventory.L("适配状态：存在已知兼容问题", "Compatibility: known issues"), _ => PartitionInventory.L("适配状态：尚未完成实机验证", "Compatibility: hardware validation pending") });
        lines.Add(PartitionInventory.L("允许构建不代表全部硬件功能已通过。", "Build availability does not mean every hardware feature has passed."));
        if (item.TryGetProperty("known_issues", out var issues)) lines.Add(PartitionInventory.L("已知问题：\n", "Known issues:\n") + DeviceText(issues));
        return string.Join("\n", lines);
    }
    private void ShowDeviceDetails(JsonElement item)
    {
        displayedDeviceMetadata = item.Clone();
        deviceStatus!.Text = DescribeDevice(item);
        deviceNotes!.Text = string.Join("\n\n", new[] { "notes", "compatibility_records", "verified_at", "source_url" }.Where(key => item.TryGetProperty(key, out _)).Select(key => DeviceText(item.GetProperty(key))));
        deviceNotesExpander!.Visibility = string.IsNullOrWhiteSpace(deviceNotes.Text) ? Visibility.Collapsed : Visibility.Visible;
        deviceNotesExpander.IsExpanded = false;
        deviceSourceLink!.Inlines.Clear(); deviceSourceLink.Visibility = Visibility.Collapsed;
        if (item.TryGetProperty("source_url", out var source) && source.ValueKind == JsonValueKind.String
            && source.GetString() is { Length: > 0 and <= 2048 } url
            && Uri.TryCreate(url, UriKind.Absolute, out var address) && address.Scheme == Uri.UriSchemeHttps
            && string.IsNullOrEmpty(address.UserInfo))
        {
            var link = new Hyperlink(new Run(PartitionInventory.L("查看固定版本源码：", "View pinned source: ") + address.Host)) { NavigateUri = address };
            link.RequestNavigate += (_, e) =>
            {
                try { Process.Start(new ProcessStartInfo(e.Uri.AbsoluteUri) { UseShellExecute = true }); }
                catch (Exception error) { deviceStatus.Text = PartitionInventory.L("无法打开来源链接：", "Cannot open source link: ") + error.Message; }
                e.Handled = true;
            };
            deviceSourceLink.Inlines.Add(link); deviceSourceLink.Visibility = Visibility.Visible;
        }
    }
    private async Task DeviceAction(Func<Task> action)
    {
        if (operation != null || DownloadsBusy) return;
        deviceActionError = null; deviceActionBusy = true; devicePanel!.IsEnabled = false;
        UpdateNavigationState();
        try { await action(); }
        catch (Exception error)
        {
            deviceStatus!.Text = error.Message.Contains("# boiler-script: 1", StringComparison.Ordinal)
                ? PartitionInventory.L("脚本需要适配 Boiler 接口：提供 apply、verify、restore 三个阶段，并声明 # boiler-script: 1。请查看机型包制作说明。", "Adapt the script to the Boiler apply/verify/restore contract and declare # boiler-script: 1. See the device package guide.")
                : error.Message;
            deviceActionError = deviceStatus.Text;
        }
        finally { deviceActionBusy = false; devicePanel.IsEnabled = true; UpdateNavigationState(); }
    }
    private const string FingerprintId = "oxp3:fingerprint-unlock";
    private sealed record FingerprintTarget(Guid DiskId, string Bus, string DiskModel, string Serial, int? UnidentifiedDiskNumber, long DiskSize, long Start, long Capacity, bool AlignRight, string ModelId);
    private readonly Dictionary<FingerprintTarget, bool> fingerprintPreferences = new();
    private readonly Dictionary<FingerprintTarget, bool> candidateFingerprintPreferences = new();
    private string? fingerprintModel;
    private readonly Dictionary<string, bool> pendingFingerprintPreferences = new(StringComparer.Ordinal);
    private bool fingerprintTargetReviewed;
    private FingerprintTarget? FingerprintTargetForSelection(PartitionSelection? selection)
    {
        if (!physicalInstallation || selection == null || selection.Example || fingerprintModel is not ("device:oxp3" or "device:x2-mini")) return null;
        try
        {
            var target = selection.Target();
            using var snapshot = JsonDocument.Parse(selection.Snapshot);
            var disk = snapshot.RootElement.EnumerateArray().Single(d => d.GetProperty("number").GetInt32() == selection.Disk);
            if (!disk.TryGetProperty("guid", out var value) || value.ValueKind != JsonValueKind.String
                || !Guid.TryParse(value.GetString(), out var guid) || guid == Guid.Empty) return null;
            string Field(string name) => disk.TryGetProperty(name, out var item) && item.ValueKind == JsonValueKind.String
                ? (item.GetString() ?? "").Trim().ToUpperInvariant() : "";
            var serial = Field("serial");
            return new(guid, Field("bus"), Field("model"), serial, serial.Length == 0 ? selection.Disk : null,
                target.DiskSize, target.Start, selection.Capacity, selection.AlignRight, fingerprintModel);
        }
        catch (Exception error) when (error is InvalidOperationException or IOException or JsonException) { return null; }
    }
    private FingerprintTarget? CandidateFingerprintTarget()
    {
        try { return FingerprintTargetForSelection(PartitionSelector.Selection); }
        catch (Exception error) when (error is InvalidOperationException or IOException or JsonException) { return null; }
    }
    private FingerprintTarget? CurrentFingerprintTarget()
    {
        try { return acknowledgedSelection == PartitionSelector.Selection ? FingerprintTargetForSelection(acknowledgedSelection) : null; }
        catch (Exception error) when (error is InvalidOperationException or IOException or JsonException) { return null; }
    }
    private bool FingerprintChoice(FingerprintTarget target, JsonElement component)
    {
        if (candidateFingerprintPreferences.TryGetValue(target, out var candidate)) return candidate;
        if (fingerprintPreferences.TryGetValue(target, out var saved)) return saved;
        if (!fingerprintTargetReviewed && fingerprintModel != null && pendingFingerprintPreferences.TryGetValue(fingerprintModel, out var pending)) return pending;
        return !IsExplicitDeviceOption(component);
    }
    private void SyncFingerprintCandidateSelection()
    {
        if (CandidateFingerprintTarget() is not FingerprintTarget target || !deviceComponentChoices.TryGetValue(FingerprintId, out var box)
            || box.Tag is not JsonElement component) return;
        updatingDeviceAdaptations = true;
        try { box.IsChecked = box.IsEnabled && FingerprintChoice(target, component); }
        finally { updatingDeviceAdaptations = false; }
        EnsureStaticDeckyProvider(); RefreshDeviceAdaptationsChoice();
    }
    private void RecordFingerprintPreference(CheckBox box)
    {
        if (CurrentFingerprintTarget() is FingerprintTarget target) fingerprintPreferences[target] = box.IsChecked == true;
        else if (CandidateFingerprintTarget() is FingerprintTarget candidate) candidateFingerprintPreferences[candidate] = box.IsChecked == true;
        else if (!fingerprintTargetReviewed && fingerprintModel is "device:oxp3" or "device:x2-mini")
            pendingFingerprintPreferences[fingerprintModel] = box.IsChecked == true;
    }
    private void SyncFingerprintSelection()
    {
        var target = CurrentFingerprintTarget();
        if (target != null && candidateFingerprintPreferences.Remove(target, out var candidate))
            fingerprintPreferences[target] = candidate;
        if (physicalInstallation && acknowledgedSelection != null && !acknowledgedSelection.Example
            && acknowledgedSelection == PartitionSelector.Selection && !fingerprintTargetReviewed)
        {
            if (target != null && fingerprintModel != null && pendingFingerprintPreferences.TryGetValue(fingerprintModel, out var pending)
                && !fingerprintPreferences.ContainsKey(target)) fingerprintPreferences[target] = pending;
            pendingFingerprintPreferences.Clear();
            fingerprintTargetReviewed = true;
        }
        if (!deviceComponentChoices.TryGetValue(FingerprintId, out var box) || box.Tag is not JsonElement component) return;
        var selected = target != null && fingerprintPreferences.TryGetValue(target, out var preference)
            ? preference : !IsExplicitDeviceOption(component);
        updatingDeviceAdaptations = true;
        try { box.IsChecked = box.IsEnabled && selected; }
        finally { updatingDeviceAdaptations = false; }
        EnsureStaticDeckyProvider(); RefreshDeviceAdaptationsChoice();
    }

    private sealed record PreparedDeviceSelection(string Key, string Digest, string Trust, JsonElement Package, JsonElement? Reviewed);
    private async Task<PreparedDeviceSelection> PrepareDeviceSelectionAsync(string folder, string trust, string? expected, CancellationToken token, bool sessionTrusted = false)
    {
        if (trust == "developer-session" && !sessionTrusted) throw new IOException("A local import must be explicitly requested.");
        if (trust == "signed-index" && expected == null) throw new IOException("Signed package anchor is missing.");
        var manifestPath = Path.Combine(folder, "manifest.json");
        if (new FileInfo(manifestPath).Length > 8 * 1024 * 1024) throw new IOException("Device manifest exceeds size limit.");
        var observed = Convert.ToHexString(SHA256.HashData(await File.ReadAllBytesAsync(manifestPath, token))).ToLowerInvariant();
        var digest = expected ?? observed;
        if (observed != digest) throw new IOException("Selected manifest differs from its approved anchor.");
        var package = await BoilerProtocol.RunAsync(["package", folder, "--manifest-sha256", digest], token);
        _ = DescribeDevice(package);
        if (package.TryGetProperty("purpose", out var purpose) && purpose.GetString() == "maintenance")
            throw new IOException(PartitionInventory.L("这是维护专用包，请在通用维护器中使用，不能作为新装机型包。", "This package is for maintenance. Use the generic maintainer; it cannot be used as an installation device package."));
        var key = digest[..24];
        await BoilerProtocol.CachePackageAsync(folder, digest, token);
        token.ThrowIfCancellationRequested();
        return new(key, digest, trust, package, trust == "signed-index" ? reviewedDeviceMetadata : null);
    }
    private async Task SelectDeviceDirectory(string folder, string trust, string? expected)
        => CommitDeviceSelection(await PrepareDeviceSelectionAsync(folder, trust, expected, CancellationToken.None));
    private void CommitDeviceSelection(PreparedDeviceSelection selection)
    {
        var samePackage = selectedDeviceDigest == selection.Digest;
        var priorChoices = samePackage ? deviceComponentChoices.ToDictionary(pair => pair.Key, pair => pair.Value.IsChecked == true) : new Dictionary<string, bool>();
        var priorLegacy = samePackage && deviceLegacyBridge != null ? CurrentDeviceLegacyOptions
            ?? new DeviceLegacySelection(1, [], [], deviceLegacyUpdateHold?.IsChecked == true) : null;
        var package = selection.Package;
        var modelId = package.GetProperty("id").GetString()!;
        fingerprintModel = modelId;
        adaptationBranch = "device"; adaptationScreen = AdaptationScreen.Device;
        ClearPackageOptionPreview();
        selectedDevicePackage = selection.Key; selectedDeviceDigest = selection.Digest; selectedDeviceTrust = selection.Trust;
        optionsPanel.SetWirelessContext(installMode, hasPackage: true);
        pendingDeviceDirectory = null;
        if (deviceComponentsPanel != null) ComponentOptionsHost.Children.Remove(deviceComponentsPanel);
        optionsPanel.SetSsdProvider(null);
        deviceComponentChoices.Clear();
        if (!samePackage) { currentHardwareDetection = null; currentHardwareDetectionError = null; RefreshCurrentHardwareUi(); }
        ClearLegacyDeviceOptions();
        deviceAdaptationsChoice = null;
        deviceComponentsPanel = new StackPanel { Margin = new Thickness(0, 20, 0, 0) };
        deviceComponentsPanel.Children.Add(new TextBlock { Text = PartitionInventory.L("机型专属组件", "Device-specific components"), FontSize = 19, FontWeight = FontWeights.SemiBold });
        foreach (var component in DeviceOptionRows(package, selection.Trust))
        {
            var id = component.GetProperty("id").GetString()!;
            var componentText = component.TryGetProperty("name", out var name) ? LocalizedValue(name) : id;
            if (component.TryGetProperty("description", out var description)) componentText += "\n" + LocalizedValue(description);
            var available = !component.TryGetProperty("available", out var availability) || availability.ValueKind != JsonValueKind.False;
            if (!available) componentText += "\n" + (component.TryGetProperty("reason", out var reason) ? LocalizedValue(reason)
                : PartitionInventory.L("此组件暂不可安装。", "This component is currently unavailable."));
            var modelResource = component.TryGetProperty("resource_path", out _);
            var initial = modelResource ? modelResourceChoices.GetValueOrDefault((modelResourceTarget, modelId, id), !IsExplicitDeviceOption(component))
                : priorChoices.TryGetValue(id, out var priorChoice) ? priorChoice : !IsExplicitDeviceOption(component);
            if (id == FingerprintId)
            {
                if (CandidateFingerprintTarget() is FingerprintTarget fingerprintTarget)
                    initial = FingerprintChoice(fingerprintTarget, component);
                else if (!fingerprintTargetReviewed && fingerprintModel != null && pendingFingerprintPreferences.TryGetValue(fingerprintModel, out var pending))
                    initial = pending;
            }
            var check = new CheckBox { Content = new TextBlock { Text = componentText, TextWrapping = TextWrapping.Wrap },
                IsChecked = available && initial,
                IsEnabled = available, MinHeight = 48, Tag = component.Clone() };
            void Changed(object sender, RoutedEventArgs args)
            {
                if (updatingDeviceAdaptations) return;
                if (modelResource) RecordModelResourceChoice(modelId, id, check.IsChecked == true);
                if (id == FingerprintId) RecordFingerprintPreference(check);
                if (SsdBootRepair.IsProvider(component)) RecordSsdPreference(check);
                if (check.IsChecked == true && (SsdBootRepair.IsProvider(component) || modelResource)
                    && optionsPanel.OptionBoxes.TryGetValue("core:maintenance", out var maintenance)) maintenance.IsChecked = true;
                EnsureStaticDeckyProvider();
                RefreshDeviceAdaptationsChoice(); ConfigurationChanged();
            }
            check.Checked += Changed; check.Unchecked += Changed;
            deviceComponentChoices.Add(id, check);
            if (modelResource && check.IsChecked == true && optionsPanel.OptionBoxes.TryGetValue("core:maintenance", out var core))
                core.IsChecked = true;
        }
        optionsPanel.SetSsdProvider(deviceComponentChoices.Values.SingleOrDefault(box => box.IsEnabled && box.Tag is JsonElement item && SsdBootRepair.IsProvider(item)));
        SyncSsdSelection();
        RefreshCurrentHardwareUi();
        deviceAdaptationsChoice = new CheckBox { Name = "InstallDeviceAdaptations", MinHeight = 48,
            Content = new TextBlock { Text = PartitionInventory.L("机型适配修复", "Device compatibility fixes"), TextWrapping = TextWrapping.Wrap },
            IsEnabled = deviceComponentChoices.Values.Any(box => box.IsEnabled && !IsExplicitDeviceOption(box)) };
        RefreshDeviceAdaptationsChoice();
        void SetAllDeviceAdaptations(object sender, RoutedEventArgs args)
        {
            if (updatingDeviceAdaptations) return;
            updatingDeviceAdaptations = true;
            try { foreach (var box in deviceComponentChoices.Values.Where(box => box.IsEnabled && !IsExplicitDeviceOption(box))) box.IsChecked = deviceAdaptationsChoice.IsChecked == true; }
            finally { updatingDeviceAdaptations = false; }
            foreach (var pair in deviceComponentChoices)
                if (pair.Value.Tag is JsonElement row && row.TryGetProperty("resource_path", out _))
                    RecordModelResourceChoice(modelId, pair.Key, pair.Value.IsChecked == true);
            if (deviceComponentChoices.TryGetValue(FingerprintId, out var fingerprint) && fingerprint.IsEnabled && !IsExplicitDeviceOption(fingerprint)) RecordFingerprintPreference(fingerprint);
            EnsureStaticDeckyProvider();
            ConfigurationChanged();
        }
        deviceAdaptationsChoice.Checked += SetAllDeviceAdaptations;
        deviceAdaptationsChoice.Unchecked += SetAllDeviceAdaptations;
        deviceComponentsPanel.Children.Add(deviceAdaptationsChoice);
        BuildLegacyDeviceOptions(package, priorLegacy);
        EnsureStaticDeckyProvider();
        if (deviceLegacyBridge == null)
            foreach (var check in deviceComponentChoices.Values.Where(box => box.Tag is not JsonElement item || !SsdBootRepair.IsProvider(item))) deviceComponentsPanel.Children.Add(check);
        ComponentOptionsHost.Children.Add(deviceComponentsPanel);
        ShowDeviceDetails(selection.Reviewed is JsonElement reviewed ? reviewed : package);
        deviceStatus!.Text += "\n" + PartitionInventory.L("已加入安装配置。可在安装选项中调整。", "Added to the installation. Adjust it in Installation options.");
        ConfigurationChanged();
        if (homeScreen == null && !inputAssociationBusy && !customBuildSwitchingDevice) ContinueAfterDeviceSelection();
    }

    private static IEnumerable<JsonElement> DeviceOptionRows(JsonElement package, string trust)
    {
        foreach (var component in package.GetProperty("components").EnumerateArray()) yield return component;
        if (trust != "developer-session" || !package.TryGetProperty("resources", out var resources)) yield break;
        foreach (var resource in resources.EnumerateArray())
        {
            var id = resource.GetProperty("id").GetString()!;
            yield return JsonSerializer.SerializeToElement(new
            {
                id,
                name = CurrentHardwareName(id["resource:".Length..]),
                description = PartitionInventory.L(
                    "所选机型包含的共用构件。", "Shared component included for the selected model."),
                selection_policy = resource.TryGetProperty("default", out var initial) && initial.ValueKind == JsonValueKind.False
                    ? "explicit-opt-in" : null,
                resource_path = resource.GetProperty("path").GetString()
            });
        }
    }

    private void RecordModelResourceChoice(string model, string component, bool selected)
    {
        modelResourceChoices[(modelResourceTarget, model, component)] = selected;
    }

    private void SyncModelResourceTarget(PartitionSelection selection)
    {
        SsdBootRepair.PreferenceKey target;
        try { target = SsdBootRepair.PreferenceTarget(selection); }
        catch (Exception error) when (error is IOException or InvalidOperationException or JsonException or ArgumentException) { return; }
        SetModelResourceTarget(target);
    }

    private void SetModelResourceTarget(SsdBootRepair.PreferenceKey target)
    {
        if (target == modelResourceTarget) return;
        if (modelResourceTarget == null)
        {
            foreach (var entry in modelResourceChoices.Where(pair => pair.Key.Target == null).ToArray())
            {
                modelResourceChoices[(target, entry.Key.Model, entry.Key.Component)] = entry.Value;
                modelResourceChoices.Remove(entry.Key);
            }
        }
        modelResourceTarget = target;
        if (fingerprintModel == null) return;
        updatingDeviceAdaptations = true;
        try
        {
            foreach (var pair in deviceComponentChoices)
                if (pair.Value.Tag is JsonElement row && row.TryGetProperty("resource_path", out _))
                {
                    pair.Value.IsChecked = pair.Value.IsEnabled && modelResourceChoices.GetValueOrDefault(
                        (target, fingerprintModel, pair.Key), !IsExplicitDeviceOption(row));
                    if (pair.Value.IsChecked == true && optionsPanel.OptionBoxes.TryGetValue("core:maintenance", out var core))
                        core.IsChecked = true;
                }
        }
        finally { updatingDeviceAdaptations = false; }
        RefreshDeviceAdaptationsChoice();
    }

    private static bool IsExplicitDeviceOption(JsonElement item) => item.TryGetProperty("ssd_boot_policy", out _) || item.TryGetProperty("selection_policy", out var policy)
        && policy.ValueKind == JsonValueKind.String && policy.GetString() == "explicit-opt-in";
    private static bool IsExplicitDeviceOption(CheckBox box) => box.Tag is JsonElement item && IsExplicitDeviceOption(item);
}

internal static class BoilerJsonExtensions
{
    internal static bool TryGetPropertyName(this JsonElement item, string name) => item.TryGetProperty(name, out _);
}
