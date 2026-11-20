using System.Globalization;
using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Markup;

namespace OxpInstaller;

public static class InstallerI18n
{
    internal sealed record LanguageOption(string Tag, string Name);

    internal static IReadOnlyList<LanguageOption> Languages { get; } = Array.AsReadOnly<LanguageOption>(
    [
        new("en-US", "English"), new("zh-CN", "简体中文"), new("zh-TW", "繁體中文"),
        new("ja-JP", "日本語"), new("ko-KR", "한국어"), new("de-DE", "Deutsch"),
        new("fr-FR", "Français"), new("es-ES", "Español")
    ]);

    internal static string WindowsUiLanguage { get; } = CultureInfo.CurrentUICulture.Name;
    public static string Language { get; private set; } = DetectLanguage(WindowsUiLanguage);
    private static readonly Lazy<Dictionary<string, string>> English = new(() => ReadCatalog("en-US"));
    private static readonly Dictionary<string, Lazy<Dictionary<string, string>>> Translations =
        Languages.Where(option => option.Tag is not ("zh-CN" or "en-US"))
            .ToDictionary(option => option.Tag, option => new Lazy<Dictionary<string, string>>(() => ReadCatalog(option.Tag)));
    private static readonly System.Runtime.CompilerServices.ConditionalWeakTable<DependencyObject, Dictionary<string, string>> Originals = new();

    internal static string DetectLanguage(string cultureName)
    {
        var parts = cultureName.Replace('_', '-').ToLowerInvariant().Split('-');
        if (parts[0] == "zh")
        {
            if (parts.Contains("hant") || parts.Contains("cht")) return "zh-TW";
            if (parts.Contains("hans") || parts.Contains("chs")) return "zh-CN";
            return parts.Length > 1 && parts[1] is "tw" or "hk" or "mo" ? "zh-TW" : "zh-CN";
        }
        return Languages.FirstOrDefault(option => option.Tag.StartsWith(parts[0] + "-", StringComparison.Ordinal))?.Tag ?? "en-US";
    }

    private static Dictionary<string, string> ReadCatalog(string tag) =>
        JsonSerializer.Deserialize<Dictionary<string, string>>(
            File.ReadAllText(Path.Combine(Backend.Root, "components", "i18n", tag + ".json")))
        ?? throw new IOException("Missing installer translation catalog: " + tag);

    private static string TranslateEnglish(string english, string language) =>
        Translations.TryGetValue(language, out var catalog) && catalog.Value.TryGetValue(english, out var value) ? value : english;

    public static string T(string text) => Language == "zh-CN" ? text :
        TranslateEnglish(English.Value.GetValueOrDefault(text, text), Language);

    internal static string L(string zh, string en) => L(zh, en, Language);
    internal static string L(string zh, string en, string language) =>
        language == "zh-CN" ? zh : TranslateEnglish(en, language);

    internal static string Format(string zh, FormattableString en) => Language == "zh-CN" ? zh :
        string.Format(CultureInfo.CurrentCulture, TranslateEnglish(en.Format, Language), en.GetArguments());

    internal static bool TryLocalizedValue(JsonElement value, out JsonElement text)
    {
        text = default;
        if (value.ValueKind != JsonValueKind.Object) return false;
        var alias = Language switch { "zh-CN" => "zh-Hans", "zh-TW" => "zh-Hant", _ => Language[..2] };
        return value.TryGetProperty(Language, out text)
            || value.TryGetProperty(alias, out text)
            || (Language == "zh-CN" && value.TryGetProperty("zh", out text))
            || value.TryGetProperty("en-US", out text)
            || value.TryGetProperty("en", out text);
    }

    public static string Diagnostic(string text)
    {
        if (Language != "zh-CN") return T(text);
        return text.Replace("Requires SteamOS 3.10 build 20260924.1000 and exact 7.2.4 kernel", "需要 SteamOS 3.10 build 20260924.1000 和匹配的 7.2.4 内核")
            .Replace("Waydroid r2 requires the accepted SteamOS 3.10 build", "Waydroid r2 需要已适配的 SteamOS 3.10 版本")
            .Replace("Plasma Keyboard dependency is not installed", "系统镜像未安装 Plasma Keyboard 依赖")
            .Replace("Dependency not installed: ", "依赖尚未安装：")
            .Replace("Component resource missing: ", "组件资源缺失（可补齐资源后重试）：")
            .Replace("Component resource manifest is missing: ", "组件资源清单缺失：")
            .Replace("No install payload", "没有可安装的资源")
            .Replace("Invalid Linux Decky binary", "Decky 安装资源无效")
            .Replace("File is not a zip file", "插件归档不是有效的 ZIP 文件")
            .Replace("Incompatible package: ", "软件包版本不兼容：")
            .Replace("Incompatible Gamescope", "Gamescope 版本不兼容")
            .Replace("Existing Flathub remote is required; add it in Discover first", "需要已有 Flathub 源；请先在 Discover 中添加")
            .Replace("Plugin resource is missing or ambiguous", "插件资源缺失或存在多个版本");
    }
    public static void SetLanguage(string language)
    {
        if (!Languages.Any(option => option.Tag == language)) throw new ArgumentException("Unsupported installer language.");
        Language = language;
    }
    public static void Apply(DependencyObject root)
    {
        if (root is ComboBoxItem item && Languages.Any(option => Equals(option.Tag, item.Tag))) return;
        var saved = Originals.GetOrCreateValue(root);
        void Translate(string key, string text, Action<string> write)
        {
            if (!saved.TryGetValue(key, out var original)) saved[key] = original = text;
            write(T(original));
        }
        if (root is Window window)
        {
            window.Language = XmlLanguage.GetLanguage(Language);
            Translate("Title", window.Title, x => window.Title = x);
        }
        if (root is TextBlock block) Translate("Text", block.Text, x => block.Text = x);
        if (root is TextBox box && box.IsReadOnly && (saved.ContainsKey("Text") || English.Value.ContainsKey(box.Text)))
            Translate("Text", box.Text, x => box.Text = x);
        if (root is HeaderedContentControl header && header.Header is string title) Translate("Header", title, x => header.Header = x);
        if (root is ContentControl control && control.Content is string content) Translate("Content", content, x => control.Content = x);
        if (root is FrameworkElement element && element.ToolTip is string tip)
            Translate("ToolTip", tip, x => element.ToolTip = x);
        if (root.ReadLocalValue(AutomationProperties.NameProperty) is string name)
            Translate("AutomationName", name, x => AutomationProperties.SetName(root, x));
        foreach (var child in LogicalTreeHelper.GetChildren(root).OfType<DependencyObject>()) Apply(child);
    }
}
