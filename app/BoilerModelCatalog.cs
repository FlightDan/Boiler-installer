using System.Diagnostics;
using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;

namespace OxpInstaller;

public partial class MainWindow
{
    private JsonElement? modelCatalog;
    private string? modelCatalogError;
    private TextBlock? modelCatalogStatus;
    private StackPanel? modelCatalogDetails;
    internal Func<CancellationToken, Task<JsonElement>>? ModelCatalogLoaderForChecks { get; set; }
    internal Func<string, CancellationToken, Task<JsonElement>>? BundledModelLoaderForChecks { get; set; }
    private static bool IsModelGuide(JsonElement entry) => entry.TryGetProperty("guide_id", out _);
    private static string DeviceEntryKey(JsonElement entry) => IsModelGuide(entry) ? entry.GetProperty("guide_id").GetString()!
        : entry.GetProperty("id").GetString() + ":" + entry.GetProperty("sha256").GetString();
    private JsonElement? ChosenModelGuide => deviceList?.SelectedItem is ComboBoxItem { Tag: JsonElement entry } && IsModelGuide(entry) ? entry : null;
    private bool HasBundledModel(string id) => modelCatalog is JsonElement catalog && catalog.TryGetProperty("bundled_packages", out var packages)
        && packages.EnumerateArray().Any(package => package.GetProperty("id").GetString() == id
            && package.TryGetProperty("trust", out var trust) && trust.GetString() == "local-test");
    private bool CanUseChosenDevice => deviceList?.SelectedItem is ComboBoxItem { Tag: JsonElement entry }
        && (!IsModelGuide(entry) || HasBundledModel(entry.GetProperty("id").GetString()!));
    private string ChosenDeviceActionLabel => PartitionInventory.L("使用所选机型包", "Use selected device package");
    private string ChosenDeviceNotice => ChosenModelGuide is JsonElement model
        ? HasBundledModel(model.GetProperty("id").GetString()!)
            ? ""
            : PartitionInventory.L("此机型只有适配指南，暂无随包适配。可查看建议或导入本地机型包。", "This model has guides only and no bundled adaptation. View recommendations or import a local device package.")
        : DeviceCompatibilityNotice();

    private void CreateModelCatalogDetails(StackPanel picker)
    {
        modelCatalogStatus = new TextBlock { TextWrapping = TextWrapping.Wrap, Foreground = BoilerBrush("#485B73"), Margin = new(0, 6, 0, 4) };
        picker.Children.Add(modelCatalogStatus);
        modelCatalogDetails = new StackPanel { Name = "ModelCatalogDetails", Visibility = Visibility.Collapsed, Margin = new(0, 8, 0, 0) };
        picker.Children.Add(modelCatalogDetails);
        RefreshModelCatalogStatus();
    }

    private async Task LoadModelCatalogAsync(int generation, CancellationToken token)
    {
        if (generation != devicePanelGeneration || token.IsCancellationRequested) return;
        // Existing index-only checks own their fixture and must remain isolated from real discovery.
        if (DeviceIndexLoaderForChecks != null && ModelCatalogLoaderForChecks == null) return;
        modelCatalogError = null;
        if (modelCatalogStatus != null) modelCatalogStatus.Text = PartitionInventory.L("正在读取固定版本机型适配清单…", "Loading the pinned model adaptation guides…");
        try
        {
            var result = ModelCatalogLoaderForChecks != null ? await ModelCatalogLoaderForChecks(token)
                : await BoilerProtocol.RunAsync(["model-catalog"], token);
            if (generation != devicePanelGeneration || token.IsCancellationRequested) return;
            modelCatalog = result.Clone();
            PopulateDeviceList();
            if (selectedDevicePackage == null && !HasPendingDevicePreview && ChosenModelGuide is JsonElement guide) ShowModelGuide(guide);
            if (!deviceChoiceChanged) DetectDeviceModel(explicitRequest: false);
        }
        catch (OperationCanceledException) { }
        catch (Exception error)
        {
            if (generation != devicePanelGeneration || token.IsCancellationRequested) return;
            modelCatalogError = error.Message;
        }
        finally
        {
            if (generation == devicePanelGeneration) { RefreshModelCatalogStatus(); RefreshInlineDeviceChoice(); }
        }
    }

    private void RefreshModelCatalogStatus()
    {
        if (modelCatalogStatus == null) return;
        modelCatalogStatus.ToolTip = modelCatalogError;
        if (modelCatalogError != null)
            modelCatalogStatus.Text = PartitionInventory.L("适配清单读取失败。请重新读取目录，或导入本地机型包。", "Could not load adaptation guides. Retry the directory or import a local device package.");
        else if (modelCatalog is JsonElement catalog)
        {
            var offline = catalog.TryGetProperty("offline", out var value) && value.GetBoolean();
            var source = catalog.GetProperty("source");
            var commit = source.GetProperty("commit").GetString()!;
            modelCatalogStatus.Text = (offline ? PartitionInventory.L("离线：使用随包固定清单。", "Offline: using the bundled pinned guides.")
                : PartitionInventory.L("已读取固定版本适配清单。", "Pinned adaptation guides loaded."))
                + " " + source.GetProperty("repository").GetString() + " @ " + commit[..Math.Min(12, commit.Length)];
        }
        else modelCatalogStatus.Text = "";
        if (modelCatalogError != null && deviceIndexRetry != null) deviceIndexRetry.Visibility = Visibility.Visible;
    }

    private void ClearModelGuide()
    {
        modelCatalogDetails?.Children.Clear();
        if (modelCatalogDetails != null) modelCatalogDetails.Visibility = Visibility.Collapsed;
    }

    private void ShowModelGuide(JsonElement model)
    {
        if (modelCatalogDetails == null) return;
        displayedDeviceMetadata = model.Clone();
        ClearModelGuide(); modelCatalogDetails.Visibility = Visibility.Visible;
        var bundled = HasBundledModel(model.GetProperty("id").GetString()!);
        TextBlock Text(string text) => new() { Text = text, TextWrapping = TextWrapping.Wrap, Foreground = BoilerBrush("#485B73"), Margin = new(0, 0, 0, 8) };
        var notice = bundled
            ? ""
            : PartitionInventory.L("此机型仅提供适配清单，暂无随包适配，不能安装这些项目。可查看指南或导入本地包。", "This model has guides only, without a bundled adaptation. These items cannot be installed yet. Read the guide or import a local package.");
        if (notice.Length > 0) modelCatalogDetails.Children.Add(Text(notice));
        var recommendations = new StackPanel { Margin = new(12, 8, 0, 4) };
        recommendations.Children.Add(Text(PartitionInventory.L("以下仅为固定指南中的建议，不会自行运行脚本。通用核心组件在安装选项中单独选择。", "These are recommendations from pinned guides and do not run scripts. Choose common core components separately in installation options.")));
        if (model.TryGetProperty("guide_url", out var url) && Uri.TryCreate(url.GetString(), UriKind.Absolute, out var address)
            && address.Scheme == Uri.UriSchemeHttps && string.IsNullOrEmpty(address.UserInfo))
        {
            var block = Text(""); block.MinHeight = 36;
            var link = new Hyperlink(new Run(PartitionInventory.L("查看固定版本机型指南", "Read the pinned model guide"))) { NavigateUri = address };
            link.RequestNavigate += (_, e) =>
            {
                try { Process.Start(new ProcessStartInfo(e.Uri.AbsoluteUri) { UseShellExecute = true }); }
                catch (Exception error) { deviceStatus!.Text = error.Message; }
                e.Handled = true;
            };
            block.Inlines.Add(link); recommendations.Children.Add(block);
        }
        if (model.TryGetProperty("components", out var components))
            foreach (var component in components.EnumerateArray())
            {
                var label = PreviewText(component, "name", component.GetProperty("id").GetString()!);
                var recommendation = component.TryGetProperty("recommendation", out var recommendationValue) ? recommendationValue.GetString() : "pending";
                var status = recommendation switch
                {
                    "required" => PartitionInventory.L("必需", "Required"), "recommended" => PartitionInventory.L("建议", "Recommended"),
                    "optional" => PartitionInventory.L("可选", "Optional"), "conditional" => PartitionInventory.L("按条件选择", "Conditional"),
                    "excluded" => PartitionInventory.L("排除，不安装", "Excluded; do not install"), _ => PartitionInventory.L("待补，不安装", "Pending; do not install")
                };
                var item = Text(label + " · " + status + "\n" + PreviewText(component, "notes", ""));
                recommendations.Children.Add(item);
            }
        var count = model.TryGetProperty("components", out var allComponents) ? allComponents.GetArrayLength() : 0;
        modelCatalogDetails.Children.Add(new Expander { Name = "ModelGuideRecommendations", MinHeight = 48, Content = recommendations,
            Header = PartitionInventory.L($"查看适配建议（{count} 项）", $"View adaptation recommendations ({count})") });
        if (deviceStatus != null) deviceStatus.Text = notice;
        if (deviceNotesExpander != null) deviceNotesExpander.Visibility = Visibility.Collapsed;
        if (deviceSourceLink != null) deviceSourceLink.Visibility = Visibility.Collapsed;
        RefreshInlineDeviceChoice();
    }

    private async Task SelectChosenDeviceAsync()
    {
        if (ChosenModelGuide is not JsonElement model) { await SelectSignedDeviceAsync(); return; }
        var id = model.GetProperty("id").GetString()!;
        if (!HasBundledModel(id)) throw new IOException(PartitionInventory.L("此机型尚无随包测试适配。", "This model has no bundled test adaptation."));
        var generation = devicePanelGeneration;
        var chosenKey = DeviceEntryKey(model);
        var token = deviceIndexCancellation?.Token ?? CancellationToken.None;
        var response = BundledModelLoaderForChecks != null ? await BundledModelLoaderForChecks(id, token)
            : await BoilerProtocol.RunAsync(["bundled-model-package", id, "--trust-local-test"], token);
        if (generation != devicePanelGeneration || ChosenModelGuide is not JsonElement chosen || DeviceEntryKey(chosen) != chosenKey) return;
        var selection = await PrepareDeviceSelectionAsync(response.GetProperty("directory").GetString()!, "developer-session",
            response.GetProperty("manifest_sha256").GetString(), token, sessionTrusted: true);
        if (generation != devicePanelGeneration || ChosenModelGuide is not JsonElement current || DeviceEntryKey(current) != chosenKey) return;
        if (selection.Package.GetProperty("id").GetString() != id) throw new IOException("Bundled package does not match the selected model.");
        if (installMode == "vanilla") PromoteDeviceMode();
        ClearModelGuide(); CommitDeviceSelection(selection);
    }
}
