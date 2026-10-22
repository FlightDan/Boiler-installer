using System.IO;
using System.Security.Cryptography;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Diagnostics;
using Microsoft.Win32;

namespace OxpInstaller;

internal static class BoilerProtocol
{
    internal static async Task<JsonElement> RunAsync(IEnumerable<string> args, CancellationToken token)
    {
        var python = Path.Combine(Backend.Root, "runtime/python/python.exe");
        if (!File.Exists(python)) throw new IOException("缺少已验证的 Python 运行时，请先准备运行时。 / Prepare the verified Python runtime first.");
        var result = await Backend.ProcessAsync(python, new[] { "-B", "-c", "import sys; sys.path.insert(0,sys.argv.pop(1)); from protocol.__main__ import main; sys.exit(main())", Backend.Root }.Concat(args), _ => { }, token);
        using var json = JsonDocument.Parse(result);
        if (!json.RootElement.GetProperty("ok").GetBoolean()) throw new IOException(result);
        return json.RootElement.GetProperty("result").Clone();
    }
    internal static string RegistryCache => Path.Combine(InstallerWorkspace.DirectoryPath, "device-index");
    internal static string PackageRoot => Path.Combine(Backend.Root, "cache/device-packages");
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
    private TextBox? developerRepo;
    private TextBox? developerRef;
    private TextBox? developerPackagePath;
    private bool deviceActionBusy;
    private TextBlock? hardwareAdvice;
    private readonly Dictionary<string, CheckBox> deviceComponentChoices = new();
    private StackPanel? deviceComponentsPanel;

    private void ClearDeviceSelection()
    {
        selectedDevicePackage = selectedDeviceDigest = selectedDeviceTrust = null;
        reviewedDeviceMetadata = null;
        if (deviceNotesExpander != null) deviceNotesExpander.Visibility = Visibility.Collapsed;
        if (deviceSourceLink != null) deviceSourceLink.Visibility = Visibility.Collapsed;
        deviceComponentChoices.Clear();
        if (deviceComponentsPanel != null) { ComponentOptionsHost.Children.Remove(deviceComponentsPanel); deviceComponentsPanel = null; }
        ConfigurationChanged();
    }

    private void CreateDevicePanel()
    {
        devicePanel = new StackPanel { Margin = new Thickness(0, 0, 0, 16) };
        devicePanel.Children.Add(new TextBlock { Text = PartitionInventory.L("目标机型与适配说明", "Target device and compatibility"), FontSize = 19, FontWeight = FontWeights.SemiBold });
        deviceStatus = new TextBlock { Text = PartitionInventory.L("可选择通用配置；尚未加载已审核机型目录。", "Generic configuration is available; the reviewed device index has not been loaded."), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 8, 0, 8) };
        devicePanel.Children.Add(deviceStatus);
        var fetch = new Button { Content = PartitionInventory.L("读取 GitHub 机型目录", "Load GitHub device index"), HorizontalAlignment = HorizontalAlignment.Left, MinHeight = 48 };
        fetch.Click += async (_, _) => await DeviceAction(async () =>
        {
            var response = await BoilerProtocol.RunAsync(["fetch-index", "https://github.com/FlightDan/Boiler-devices/releases/latest/download/index.json", "https://github.com/FlightDan/Boiler-devices/releases/latest/download/index.sig", BoilerProtocol.RegistryCache], CancellationToken.None);
            ShowDeviceIndex(response.GetProperty("index"), false);
        });
        devicePanel.Children.Add(fetch);
        var offline = new Button { Content = PartitionInventory.L("使用已验证的缓存目录", "Use verified cached index"), HorizontalAlignment = HorizontalAlignment.Left, MinHeight = 48 };
        offline.Click += async (_, _) => await DeviceAction(async () => ShowDeviceIndex(await BoilerProtocol.RunAsync(["cached-index", BoilerProtocol.RegistryCache], CancellationToken.None), true));
        devicePanel.Children.Add(offline);
        var detect = new Button { Content = PartitionInventory.L("检测本机并查看机型建议", "Detect this PC for device suggestions"), HorizontalAlignment = HorizontalAlignment.Left, MinHeight = 48 };
        hardwareAdvice = new TextBlock { TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 8, 0, 8),
            Text = PartitionInventory.L("为外部设备制作系统时，请按目标设备手选机型。", "When preparing a system for another device, select that target model manually.") };
        detect.Click += (_, _) =>
        {
            var computer = BoilerHardware.ReadThisComputer();
            if (computer == null) { hardwareAdvice.Text = PartitionInventory.L("无法读取本机型号，请手选目标机型。", "This PC's model is unavailable. Select the target model manually."); return; }
            var matches = deviceList!.Items.OfType<ComboBoxItem>().Where(choice => choice.Tag is JsonElement entry && BoilerHardware.Matches(entry, computer)).Select(choice => choice.Content?.ToString()).ToArray();
            hardwareAdvice.Text = computer.Manufacturer + " · " + computer.Model + "\n"
                + (deviceList.Items.Count == 0 ? PartitionInventory.L("请先加载已验签机型目录。", "Load a verified device index first.")
                    : matches.Length == 0 ? PartitionInventory.L("目录中没有精确匹配，请手选目标机型。", "No exact match in this index. Select the target model manually.")
                    : PartitionInventory.L("本机建议：", "Suggestions for this PC: ") + string.Join(" / ", matches))
                + "\n" + PartitionInventory.L("此建议只描述当前电脑。机型和组件仍需手选；为其他设备制作系统时按目标设备选择。", "This suggestion describes the current PC. Choose the model and components manually, using the target device when preparing another computer.");
        };
        devicePanel.Children.Add(detect);
        devicePanel.Children.Add(hardwareAdvice);
        deviceList = new ComboBox { MinHeight = 48, Margin = new Thickness(0, 8, 0, 8) };
        deviceList.SelectionChanged += (_, _) => { if (deviceList.SelectedItem is ComboBoxItem { Tag: JsonElement item }) ShowDeviceDetails(item); };
        devicePanel.Children.Add(deviceList);
        deviceNotes = new TextBlock { TextWrapping = TextWrapping.Wrap, Margin = new Thickness(12, 8, 12, 12) };
        deviceNotesExpander = new Expander { Header = PartitionInventory.L("开发者完整备注与验证记录", "Developer notes and validation records"), Content = deviceNotes, MinHeight = 48, Visibility = Visibility.Collapsed };
        devicePanel.Children.Add(deviceNotesExpander);
        deviceSourceLink = new TextBlock { MinHeight = 48, TextWrapping = TextWrapping.Wrap, Visibility = Visibility.Collapsed };
        devicePanel.Children.Add(deviceSourceLink);
        var choose = new Button { Content = PartitionInventory.L("使用所选机型包", "Use selected device package"), HorizontalAlignment = HorizontalAlignment.Left, MinHeight = 48 };
        choose.Click += async (_, _) => await DeviceAction(async () =>
        {
            if (deviceList.SelectedItem is not ComboBoxItem { Tag: JsonElement item }) throw new IOException("请选择机型。 / Select a device.");
            var id = item.GetProperty("id").GetString()!;
            reviewedDeviceMetadata = item.Clone();
            var digest = item.GetProperty("sha256").GetString()!;
            var current = await BoilerProtocol.RunAsync(["cached-index", BoilerProtocol.RegistryCache], CancellationToken.None);
            if (!current.GetProperty("packages").EnumerateArray().Any(p => p.GetProperty("id").GetString() == id && p.GetProperty("sha256").GetString() == digest)
                || current.GetProperty("revocations").EnumerateArray().Any(p => p.GetString() == digest)) throw new IOException("Selected package is no longer approved.");
            var cached = Path.Combine(BoilerProtocol.PackageRoot, digest[..24]);
            if (Directory.Exists(cached)) { await SelectDeviceDirectory(cached, "signed-index", digest); return; }
            var folder = Path.Combine(BoilerProtocol.PackageRoot, Guid.NewGuid().ToString("N"));
            var package = await BoilerProtocol.RunAsync(["fetch-package", BoilerProtocol.RegistryCache, id, item.GetProperty("version").GetString()!, folder], CancellationToken.None);
            await SelectDeviceDirectory(folder, "signed-index", item.GetProperty("sha256").GetString());
        });
        devicePanel.Children.Add(choose);
        developerPackages = new CheckBox { Content = PartitionInventory.L("开发者模式：本次信任外部机型脚本", "Developer mode: trust external device scripts for this session"), MinHeight = 48, Margin = new Thickness(0, 12, 0, 0) };
        devicePanel.Children.Add(developerPackages);
        var development = new StackPanel { Visibility = Visibility.Collapsed };
        developerPackages.Checked += (_, _) => development.Visibility = Visibility.Visible;
        developerPackages.Unchecked += (_, _) => { development.Visibility = Visibility.Collapsed; if (selectedDeviceTrust == "developer-session") { ClearDeviceSelection(); } };
        var local = new Button { Content = PartitionInventory.L("导入本地机型包文件夹", "Import a local device package folder"), HorizontalAlignment = HorizontalAlignment.Left, MinHeight = 48 };
        local.Click += async (_, _) => { var picker = new OpenFolderDialog(); if (picker.ShowDialog(this) == true) await DeviceAction(() => SelectDeviceDirectory(picker.FolderName, "developer-session", null)); };
        development.Children.Add(local);
        development.Children.Add(new TextBlock { Text = "GitHub repository URL", Margin = new Thickness(0, 10, 0, 4) });
        developerRepo = new TextBox { MinHeight = 48 }; development.Children.Add(developerRepo);
        development.Children.Add(new TextBlock { Text = "Tag / branch / commit", Margin = new Thickness(0, 8, 0, 4) });
        developerRef = new TextBox { Text = "main", MinHeight = 48 }; development.Children.Add(developerRef);
        development.Children.Add(new TextBlock { Text = PartitionInventory.L("包所在子目录（可留空）", "Package subdirectory (optional)"), Margin = new Thickness(0, 8, 0, 4) });
        developerPackagePath = new TextBox { MinHeight = 48 }; development.Children.Add(developerPackagePath);
        var github = new Button { Content = PartitionInventory.L("解析并导入固定提交", "Resolve and import pinned commit"), HorizontalAlignment = HorizontalAlignment.Left, MinHeight = 48, Margin = new Thickness(0, 8, 0, 0) };
        github.Click += async (_, _) => await DeviceAction(async () =>
        {
            var folder = Path.Combine(BoilerProtocol.PackageRoot, Guid.NewGuid().ToString("N"));
            var arguments = new List<string> { "fetch-github", developerRepo.Text.Trim(), folder, "--ref", developerRef.Text.Trim(), "--developer" };
            if (!string.IsNullOrWhiteSpace(developerPackagePath.Text)) arguments.AddRange(["--package-path", developerPackagePath.Text.Trim()]);
            await BoilerProtocol.RunAsync(arguments, CancellationToken.None);
            await SelectDeviceDirectory(folder, "developer-session", null);
        });
        development.Children.Add(github); devicePanel.Children.Add(development);
        var clear = new Button { Content = PartitionInventory.L("使用通用配置，不加载机型脚本", "Use generic configuration without device scripts"), MinHeight = 48, Margin = new Thickness(0, 8, 0, 0) };
        clear.Click += (_, _) => { ClearDeviceSelection(); deviceStatus.Text = PartitionInventory.L("当前使用通用配置。", "Using generic configuration."); ConfigurationChanged(); };
        devicePanel.Children.Add(clear);
        RecoverySourceSection.Children.Insert(0, devicePanel);
    }
    private void ShowDeviceIndex(JsonElement index, bool cached)
    {
        if (hardwareAdvice != null) hardwareAdvice.Text = PartitionInventory.L("目录已更新，可重新检测本机；为外部设备制作系统时请手选目标机型。", "Index updated. You can detect this PC again; select another target device manually.");
        deviceList!.Items.Clear();
        foreach (var entry in index.GetProperty("packages").EnumerateArray())
                deviceList.Items.Add(new ComboBoxItem { Content = entry.TryGetProperty("name", out var name) ? LocalizedValue(name) : entry.GetProperty("id").GetString(), Tag = entry.Clone() });
        deviceStatus!.Text = (cached ? PartitionInventory.L("使用已验签缓存；离线时无法获知新的撤销记录。", "Using verified cache; newer revocations cannot be checked offline.")
            : PartitionInventory.L("目录已验签。选择机型以查看适配情况。", "Index verified. Select a device to inspect compatibility."))
            + "\n" + PartitionInventory.L("目录生成时间：", "Snapshot generated: ") + index.GetProperty("generated_at").GetString()
            + "\n" + PartitionInventory.L("序列：", "Sequence: ") + index.GetProperty("sequence");
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
        var status = item.TryGetProperty("status", out var state) ? state.GetString() : "unverified";
        lines.Add(status switch { "supported" => PartitionInventory.L("适配状态：已支持（请查看验证范围）", "Compatibility: supported within the recorded validation scope"), "experimental" => PartitionInventory.L("适配状态：实验性，仍有待验证功能", "Compatibility: experimental; hardware validation is incomplete"), "blocked" => PartitionInventory.L("适配状态：存在阻塞问题", "Compatibility: blocked by known issues"), _ => PartitionInventory.L("适配状态：尚未完成实机验证", "Compatibility: hardware validation pending") });
        lines.Add(PartitionInventory.L("允许构建不代表全部硬件功能已通过。", "Build availability does not mean every hardware feature has passed."));
        if (item.TryGetProperty("known_issues", out var issues)) lines.Add(PartitionInventory.L("已知问题：\n", "Known issues:\n") + DeviceText(issues));
        return string.Join("\n", lines);
    }
    private void ShowDeviceDetails(JsonElement item)
    {
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
        deviceActionBusy = true; devicePanel!.IsEnabled = false;
        try { await action(); }
        catch (Exception error) { deviceStatus!.Text = error.Message; }
        finally { deviceActionBusy = false; devicePanel.IsEnabled = true; UpdateNavigationState(); }
    }
    private async Task SelectDeviceDirectory(string folder, string trust, string? expected)
    {
        if (trust == "developer-session" && developerPackages?.IsChecked != true) throw new IOException("Enable developer mode explicitly.");
        if (trust == "signed-index" && expected == null) throw new IOException("Signed package anchor is missing.");
        var observed = Convert.ToHexString(SHA256.HashData(await File.ReadAllBytesAsync(Path.Combine(folder, "manifest.json")))).ToLowerInvariant();
        var digest = expected ?? observed;
        if (observed != digest) throw new IOException("Selected manifest differs from its approved anchor.");
        var package = await BoilerProtocol.RunAsync(["package", folder, "--manifest-sha256", digest], CancellationToken.None);
        if (package.TryGetProperty("purpose", out var purpose) && purpose.GetString() == "maintenance")
            throw new IOException(PartitionInventory.L("这是维护专用包，请在通用维护器中使用，不能作为新装机型包。", "This package is for maintenance. Use the generic maintainer; it cannot be used as an installation device package."));
        var key = digest[..24]; var cached = Path.Combine(BoilerProtocol.PackageRoot, key);
        if (!Directory.Exists(cached)) Backend.CopyDirectory(folder, cached);
        await BoilerProtocol.RunAsync(["package", cached, "--manifest-sha256", digest], CancellationToken.None);
        selectedDevicePackage = key; selectedDeviceDigest = digest; selectedDeviceTrust = trust;
        if (deviceComponentsPanel != null) ComponentOptionsHost.Children.Remove(deviceComponentsPanel);
        deviceComponentChoices.Clear();
        deviceComponentsPanel = new StackPanel { Margin = new Thickness(0, 20, 0, 0) };
        deviceComponentsPanel.Children.Add(new TextBlock { Text = PartitionInventory.L("机型专属组件", "Device-specific components"), FontSize = 19, FontWeight = FontWeights.SemiBold });
        foreach (var component in package.GetProperty("components").EnumerateArray())
        {
            var id = component.GetProperty("id").GetString()!;
            var componentText = component.TryGetProperty("name", out var name) ? LocalizedValue(name) : id;
            if (component.TryGetProperty("description", out var description)) componentText += "\n" + LocalizedValue(description);
            var check = new CheckBox { Content = new TextBlock { Text = componentText, TextWrapping = TextWrapping.Wrap },
                IsChecked = component.TryGetProperty("default", out var value) && value.ValueKind == JsonValueKind.True, MinHeight = 48 };
            check.Checked += (_, _) => ConfigurationChanged(); check.Unchecked += (_, _) => ConfigurationChanged();
            deviceComponentChoices.Add(id, check); deviceComponentsPanel.Children.Add(check);
        }
        ComponentOptionsHost.Children.Add(deviceComponentsPanel);
        ShowDeviceDetails(trust == "signed-index" && reviewedDeviceMetadata is JsonElement reviewed ? reviewed : package);
        deviceStatus!.Text += "\n" + (trust == "signed-index" ? PartitionInventory.L("来源：已验签目录中的固定版本", "Source: fixed version from the verified index") : PartitionInventory.L("来源：仅本次信任的开发包", "Source: development package trusted for this session"));
        ConfigurationChanged();
    }
}

internal static class BoilerJsonExtensions
{
    internal static bool TryGetPropertyName(this JsonElement item, string name) => item.TryGetProperty(name, out _);
}
