using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Automation;

namespace OxpInstaller;

// A reviewed local-test choice only. It carries no production trust or permission
// to write a target. The candidate installer rejects it at its execution boundary.
public sealed record ResourceSelection(int SchemaVersion, string ModelId, string CatalogSha256,
    string[] Components, string Trust);

internal static class ResourceSelections
{
    internal static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
    internal static void Validate(ResourceSelection value)
    {
        if (value.SchemaVersion != 1 || value.Trust != "local-test"
            || !System.Text.RegularExpressions.Regex.IsMatch(value.ModelId ?? "", "^device:[a-z][a-z0-9-]*$")
            || !System.Text.RegularExpressions.Regex.IsMatch(value.CatalogSha256 ?? "", "^[0-9a-f]{64}$")
            || value.Components is not { Length: > 0 and <= 128 }
            || value.Components.Distinct().Count() != value.Components.Length
            || value.Components.Any(id => !System.Text.RegularExpressions.Regex.IsMatch(id ?? "", "^resource:[a-z][a-z0-9-]*$")))
            throw new IOException("Invalid local-test resource selection.");
    }
}

public partial class MainWindow
{
    private JsonElement? resourceCatalog;
    private string? resourceCatalogError;
    private ResourceSelection? selectedResourceSelection;
    private CheckBox? resourceConsent;
    private readonly Dictionary<string, CheckBox> resourceChoices = new();
    private readonly Dictionary<(string Digest, string Model), (HashSet<string> Choices, bool Consent)> resourceDrafts = new();
    internal Func<CancellationToken, Task<JsonElement>>? ResourceCatalogLoaderForChecks { get; set; }
    internal Func<string[], CancellationToken, Task<JsonElement>>? ResourceSelectionLoaderForChecks { get; set; }
    private IEnumerable<JsonElement> ResourceModels => resourceCatalog is JsonElement catalog
        ? catalog.GetProperty("models").EnumerateArray().ToArray() : [];
    private JsonElement? ResourceModel(string id) => ResourceModels.Cast<JsonElement?>().FirstOrDefault(row => row!.Value.GetProperty("id").GetString() == id);
    private bool HasResourceModel(string id) => ResourceModel(id) != null;
    private bool CanChooseResources => resourceConsent?.IsChecked == true && resourceChoices.Values.Any(box => box.IsEnabled && box.IsChecked == true);

    private async Task LoadResourceCatalogAsync(int generation, CancellationToken token)
    {
        resourceCatalogError = null;
        if (ResourceCatalogLoaderForChecks == null && !File.Exists(Path.Combine(Backend.Root, "cache/integration/resource-pool/catalog.toon")))
        { if (generation == devicePanelGeneration) { resourceCatalog = null; ClearResourceSelection(); } return; }
        try
        {
            var result = ResourceCatalogLoaderForChecks != null ? await ResourceCatalogLoaderForChecks(token)
                : await BoilerProtocol.RunAsync(["resource-catalog"], token);
            if (generation != devicePanelGeneration || token.IsCancellationRequested) return;
            if (result.GetProperty("kind").GetString() != "LocalResourceSelectionCatalog"
                || result.GetProperty("schema_version").GetInt32() != 1
                || !result.GetProperty("deployment_available").GetBoolean())
                throw new IOException("Unsupported resource selection catalog.");
            resourceCatalog = result.Clone();
            if (selectedResourceSelection != null && selectedResourceSelection.CatalogSha256 != result.GetProperty("catalog_sha256").GetString())
                ClearResourceSelection();
        }
        catch (OperationCanceledException) { throw; }
        catch (Exception error)
        {
            if (generation != devicePanelGeneration || token.IsCancellationRequested) return;
            resourceCatalog = null; resourceCatalogError = error.Message; ClearResourceSelection();
        }
    }

    private void ClearResourceSelection()
    {
        selectedResourceSelection = null;
        if (resourceConsent != null) resourceConsent.IsChecked = false;
    }

    private void ShowResourceModel(JsonElement model)
    {
        if (modelCatalogDetails == null) return;
        var prior = selectedResourceSelection;
        var digest = resourceCatalog!.Value.GetProperty("catalog_sha256").GetString()!;
        var modelId = model.GetProperty("id").GetString()!;
        if (prior == null || prior.ModelId != modelId || prior.CatalogSha256 != digest) prior = null;
        resourceDrafts.TryGetValue((digest, modelId), out var draft);
        var hasDraft = draft.Choices != null;
        selectedResourceSelection = prior;
        resourceChoices.Clear(); resourceConsent = null;
        ClearModelGuide(); modelCatalogDetails.Visibility = Visibility.Visible;
        displayedDeviceMetadata = model.Clone();
        TextBlock Text(string text) => new() { Text = text, TextWrapping = TextWrapping.Wrap, Foreground = BoilerBrush("#485B73"), Margin = new(0, 0, 0, 8) };
        modelCatalogDetails.Children.Add(Text(PartitionInventory.L(
            "共享资源池 · 本地测试。完整资源随系统部署，首次启动按实际接口安装并启用；结果可在维护器查询。",
            "Shared resource pool · local test. Complete resources deploy with the system; first boot checks actual interfaces, installs and activates them. Results are retained for maintenance.")));
        resourceConsent = new CheckBox { Name = "TrustLocalResources", MinHeight = 48, IsChecked = prior != null || (hasDraft && draft.Consent),
            Content = Text(PartitionInventory.L("我了解这是未签名的本地测试资源，仅确认本次选择", "I understand these are unsigned local-test resources; confirm choices for this session")) };
        modelCatalogDetails.Children.Add(resourceConsent);
        foreach (var item in model.GetProperty("components").EnumerateArray())
        {
            var id = item.GetProperty("id").GetString()!;
            var selectable = item.GetProperty("selectable").GetBoolean();
            var displayName = id switch
            {
                "resource:gamma22-hdr-game" => PartitionInventory.L("游戏模式 HDR", "Game HDR"),
                "resource:gamma22-hdr-desktop" => PartitionInventory.L("桌面模式 HDR", "Desktop HDR"),
                "resource:common-opti2xmx" => PartitionInventory.L("Opti2XMX 游戏增强", "Opti2XMX game enhancements"),
                "resource:common-waydroid" => PartitionInventory.L("Waydroid 安卓环境", "Waydroid Android environment"),
                _ => id
            };
            var label = displayName + " · " + item.GetProperty("version").GetString() + "\n"
                + (selectable ? PartitionInventory.L("首次启动安装及接口检查", "First-boot installation and interface checks")
                    : PartitionInventory.L("未实现，不可选", "Not implemented; unavailable"));
            if ((id == "resource:gamma22-hdr-game" || id == "resource:gamma22-hdr-desktop") && item.TryGetProperty("parameter_data", out var parameters))
            {
                label += "\n" + PartitionInventory.L("HDR Gamma 2.2 · 参数配置：", "HDR Gamma 2.2 · profile: ")
                    + parameters.GetProperty("profile").GetString()
                    + "\n" + PartitionInventory.L("全屏映射：", "Full-frame mapping: ") + parameters.GetProperty("full_frame").GetInt32()
                    + PartitionInventory.L("；峰值映射：", "; peak mapping: ") + parameters.GetProperty("peak").GetInt32()
                    + "\n" + PartitionInventory.L("软件映射参数，不代表面板实测亮度或硬件验收。", "Software mapping values; no measured panel brightness or hardware acceptance.");
                if (!parameters.GetProperty("panel_model_known").GetBoolean())
                    label += "\n" + PartitionInventory.L("首次启动绑定实际内屏，并检查显示接口。", "First boot binds the internal panel and checks display interfaces.");
            }
            var dependencies = item.GetProperty("dependencies").EnumerateArray().Select(value => value.GetString()!).ToArray();
            if (dependencies.Length > 0) label += "\n" + PartitionInventory.L("需要：", "Requires: ") + string.Join(", ", dependencies);
            var initial = prior != null ? prior.Components.Contains(id) : hasDraft ? draft.Choices!.Contains(id)
                : item.TryGetProperty("default", out var defaultValue) && defaultValue.GetBoolean();
            var box = new CheckBox { MinHeight = 48, IsEnabled = selectable && resourceConsent.IsChecked == true,
                IsChecked = selectable && initial, Tag = item.Clone(), Content = Text(label),
                ToolTip = item.GetProperty("reason").GetString() };
            AutomationProperties.SetName(box, label);
            void Changed(object sender, RoutedEventArgs args)
            { selectedResourceSelection = null; SaveDraft(); ConfigurationChanged(); RefreshInlineDeviceChoice(); }
            box.Checked += Changed; box.Unchecked += Changed;
            resourceChoices.Add(id, box); modelCatalogDetails.Children.Add(box);
        }
        void SaveDraft() => resourceDrafts[(digest, modelId)] =
            (resourceChoices.Where(row => row.Value.IsChecked == true).Select(row => row.Key).ToHashSet(), resourceConsent.IsChecked == true);
        void ConsentChanged(object sender, RoutedEventArgs args)
        {
            selectedResourceSelection = null;
            foreach (var box in resourceChoices.Values)
            {
                box.IsEnabled = resourceConsent.IsChecked == true && ((JsonElement)box.Tag).GetProperty("selectable").GetBoolean();
                if (!box.IsEnabled) box.IsChecked = false;
            }
            SaveDraft();
            ConfigurationChanged(); RefreshInlineDeviceChoice();
        }
        resourceConsent.Checked += ConsentChanged; resourceConsent.Unchecked += ConsentChanged;
        var pending = new StackPanel();
        foreach (var item in model.GetProperty("pending").EnumerateArray())
            pending.Children.Add(new CheckBox { IsEnabled = false, MinHeight = 48,
                Content = Text(item.GetProperty("component").GetString() + " · " + PartitionInventory.L("未实现，不可选", "Not implemented; unavailable")),
                ToolTip = item.TryGetProperty("reason", out var reason) ? reason.GetString() : null });
        if (pending.Children.Count > 0)
            modelCatalogDetails.Children.Add(new Expander { MinHeight = 48, Content = pending,
                Header = PartitionInventory.L($"待实现项目（{pending.Children.Count}）", $"Unavailable items ({pending.Children.Count})") });
        RefreshInlineDeviceChoice();
    }

    private async Task SelectResourceModelAsync(JsonElement model)
    {
        if (!CanChooseResources || resourceCatalog == null) throw new IOException("Confirm local-test consent and select resources first.");
        var id = model.GetProperty("id").GetString()!;
        var digest = resourceCatalog.Value.GetProperty("catalog_sha256").GetString()!;
        var components = resourceChoices.Where(row => row.Value.IsEnabled && row.Value.IsChecked == true).Select(row => row.Key).Order().ToArray();
        var generation = devicePanelGeneration;
        var arguments = new[] { "resource-selection", id, digest }.Concat(components).Append("--trust-local-test").ToArray();
        var token = deviceIndexCancellation?.Token ?? CancellationToken.None;
        var response = ResourceSelectionLoaderForChecks != null ? await ResourceSelectionLoaderForChecks(arguments, token)
            : await BoilerProtocol.RunAsync(arguments, token);
        if (generation != devicePanelGeneration || ChosenModelGuide is not JsonElement current
            || current.GetProperty("id").GetString() != id || resourceConsent?.IsChecked != true
            || resourceCatalog?.GetProperty("catalog_sha256").GetString() != digest
            || !components.SequenceEqual(resourceChoices.Where(row => row.Value.IsEnabled && row.Value.IsChecked == true).Select(row => row.Key).Order())) return;
        var selection = response.Deserialize<ResourceSelection>(ResourceSelections.Json) ?? throw new IOException("Missing resource selection.");
        ResourceSelections.Validate(selection);
        if (selection.ModelId != id || selection.CatalogSha256 != digest || components.Except(selection.Components).Any()
            || selection.Components.Any(component => !resourceChoices.TryGetValue(component, out var choice) || !((JsonElement)choice.Tag).GetProperty("selectable").GetBoolean()))
            throw new IOException("Resource selection does not match the reviewed model and pool.");
        foreach (var component in selection.Components) resourceChoices[component].IsChecked = true;
        var genericDependencies = selection.Components.SelectMany(component =>
            ((JsonElement)resourceChoices[component].Tag).TryGetProperty("external_component_dependencies", out var required)
                ? required.EnumerateArray().Select(value => value.GetString()!).ToArray() : Array.Empty<string>())
            .Append("core:maintenance").Distinct();
        foreach (var dependency in genericDependencies)
        {
            var optionId = dependency == "decky" ? "core:decky" : dependency;
            if (!optionsPanel.OptionBoxes.TryGetValue(optionId, out var dependencyBox))
                throw new IOException("The selected resource requires an unavailable installer component: " + dependency);
            dependencyBox.IsChecked = true;
        }
        selectedResourceSelection = selection;
        deviceStatus!.Text = PartitionInventory.L("已确认本地测试资源选择；安装将部署完整文件，首次启动检查实际接口并启用。", "Local-test resources confirmed. Installation deploys all files; first boot checks actual interfaces and activates features.");
        ConfigurationChanged(); RefreshInlineDeviceChoice();
    }
}
