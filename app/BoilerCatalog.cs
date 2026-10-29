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
    private CheckBox? developerPackages;
    private bool deviceActionBusy;
    private string? deviceActionError;
    private readonly Dictionary<string, CheckBox> deviceComponentChoices = new();
    private StackPanel? deviceComponentsPanel;
    private CheckBox? deviceAdaptationsChoice;
    private bool updatingDeviceAdaptations;

    private void RefreshDeviceAdaptationsChoice()
    {
        if (deviceAdaptationsChoice == null || updatingDeviceAdaptations) return;
        updatingDeviceAdaptations = true;
        try
        {
            var selected = deviceComponentChoices.Values.Count(box => box.IsChecked == true);
            deviceAdaptationsChoice.IsChecked = selected == 0 ? false : selected == deviceComponentChoices.Count ? true : null;
        }
        finally { updatingDeviceAdaptations = false; }
    }

    private void ClearDeviceSelection()
    {
        selectedDevicePackage = selectedDeviceDigest = selectedDeviceTrust = null;
        optionsPanel.SetWirelessContext(installMode, hasPackage: adaptationBranch != "generic");
        displayedDeviceMetadata = null;
        reviewedDeviceMetadata = null;
        if (deviceNotesExpander != null) deviceNotesExpander.Visibility = Visibility.Collapsed;
        if (deviceSourceLink != null) deviceSourceLink.Visibility = Visibility.Collapsed;
        deviceComponentChoices.Clear();
        ClearLegacyDeviceOptions();
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
    private sealed record PreparedDeviceSelection(string Key, string Digest, string Trust, JsonElement Package, JsonElement? Reviewed);
    private async Task<PreparedDeviceSelection> PrepareDeviceSelectionAsync(string folder, string trust, string? expected, CancellationToken token, bool sessionTrusted = false)
    {
        if (trust == "developer-session" && !sessionTrusted && developerPackages?.IsChecked != true) throw new IOException("Enable developer mode explicitly.");
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
        adaptationBranch = "device"; adaptationScreen = AdaptationScreen.Device;
        ClearPackageOptionPreview();
        selectedDevicePackage = selection.Key; selectedDeviceDigest = selection.Digest; selectedDeviceTrust = selection.Trust;
        optionsPanel.SetWirelessContext(installMode, hasPackage: true);
        pendingDeviceDirectory = null;
        if (deviceComponentsPanel != null) ComponentOptionsHost.Children.Remove(deviceComponentsPanel);
        deviceComponentChoices.Clear();
        ClearLegacyDeviceOptions();
        deviceAdaptationsChoice = null;
        deviceComponentsPanel = new StackPanel { Margin = new Thickness(0, 20, 0, 0) };
        deviceComponentsPanel.Children.Add(new TextBlock { Text = PartitionInventory.L("机型专属组件", "Device-specific components"), FontSize = 19, FontWeight = FontWeights.SemiBold });
        foreach (var component in package.GetProperty("components").EnumerateArray())
        {
            var id = component.GetProperty("id").GetString()!;
            var componentText = component.TryGetProperty("name", out var name) ? LocalizedValue(name) : id;
            if (component.TryGetProperty("description", out var description)) componentText += "\n" + LocalizedValue(description);
            var check = new CheckBox { Content = new TextBlock { Text = componentText, TextWrapping = TextWrapping.Wrap },
                IsChecked = priorChoices.TryGetValue(id, out var priorChoice) ? priorChoice : true, MinHeight = 48, Tag = component.Clone() };
            void Changed(object sender, RoutedEventArgs args)
            {
                if (updatingDeviceAdaptations) return;
                RefreshDeviceAdaptationsChoice(); ConfigurationChanged();
            }
            check.Checked += Changed; check.Unchecked += Changed;
            deviceComponentChoices.Add(id, check);
        }
        deviceAdaptationsChoice = new CheckBox { Name = "InstallDeviceAdaptations", MinHeight = 48,
            Content = new TextBlock { Text = PartitionInventory.L("机型适配修复", "Device compatibility fixes"), TextWrapping = TextWrapping.Wrap },
            IsEnabled = deviceComponentChoices.Count > 0 };
        RefreshDeviceAdaptationsChoice();
        void SetAllDeviceAdaptations(object sender, RoutedEventArgs args)
        {
            if (updatingDeviceAdaptations) return;
            updatingDeviceAdaptations = true;
            try { foreach (var box in deviceComponentChoices.Values) box.IsChecked = deviceAdaptationsChoice.IsChecked == true; }
            finally { updatingDeviceAdaptations = false; }
            ConfigurationChanged();
        }
        deviceAdaptationsChoice.Checked += SetAllDeviceAdaptations;
        deviceAdaptationsChoice.Unchecked += SetAllDeviceAdaptations;
        deviceComponentsPanel.Children.Add(deviceAdaptationsChoice);
        BuildLegacyDeviceOptions(package, priorLegacy);
        if (deviceLegacyBridge == null)
            foreach (var check in deviceComponentChoices.Values) deviceComponentsPanel.Children.Add(check);
        ComponentOptionsHost.Children.Insert(0, deviceComponentsPanel);
        ShowDeviceDetails(selection.Reviewed is JsonElement reviewed ? reviewed : package);
        deviceStatus!.Text += "\n" + (selection.Trust == "signed-index" ? PartitionInventory.L("来源：已验签目录中的固定版本", "Source: fixed version from the verified index") : PartitionInventory.L("来源：仅本次信任的开发包", "Source: development package trusted for this session"));
        if (externalDeviceUse != null) externalDeviceUse.Visibility = Visibility.Collapsed;
        ConfigurationChanged();
        if (homeScreen == null && !inputAssociationBusy) ContinueAfterDeviceSelection();
    }
}

internal static class BoilerJsonExtensions
{
    internal static bool TryGetPropertyName(this JsonElement item, string name) => item.TryGetProperty(name, out _);
}
