using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace OxpInstaller;

public static class OptionsChecks
{
    public static string Run(MainWindow window)
    {
        var events = new List<string>();
        var desktopLanguageDefaults = CheckDesktopLanguageDefaults(events);
        void Require(bool condition, string name) { if (!condition) throw new IOException(name); events.Add(name); }
        // This UI check uses synthetic layouts, so suppress the automatic hardware inventory.
        typeof(PartitionPicker).GetField("automaticReadAttempted", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)!
            .SetValue((PartitionPicker)window.FindName("PartitionSelector"), true);
        window.BoilerModeForChecks("custom");
        window.WindowState = WindowState.Normal; window.ShowActivated = false; window.Show();
        BootSetupChecks.Acknowledge(window);
        typeof(MainWindow).GetMethod("ChooseGenericInstallation", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)!.Invoke(window, null);
        Color ContentColor() => ((SolidColorBrush)((TextBlock)window.FindName("NavContent")).Foreground).Color;
        Require(ContentColor() == Color.FromRgb(148, 169, 195), "unvisited-content-starts-neutral");
        ((Button)window.FindName("ContentTab")).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Require(ContentColor() == Colors.White
            && ((SolidColorBrush)((Button)window.FindName("ContentTab")).Background).Color == Color.FromRgb(41, 104, 217), "active-content-keeps-original-blue-and-white-style");
        ((Button)window.FindName("TargetTab")).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Require(ContentColor() == Color.FromRgb(134, 239, 172), "visited-content-keeps-completed-state");
        var panel = BoilerCustomBuildChecks.Options(window);
        var catalog = InstallerOptions.Catalog;
        Require(InstallerOptionsPanel.MatchCpuVendor("GenuineIntel") == "microcode:intel"
            && InstallerOptionsPanel.MatchCpuVendor("AuthenticAMD") == "microcode:amd"
            && InstallerOptionsPanel.MatchCpuVendor("unknown") == null, "cpu-vendor-exact-match-only");
        Require(InstallerOptions.CpuComponents.All(id => !panel.Selection.Repairs.Contains(id)), "cpu-microcode-starts-unselected");
        Require(FirmwareDownloads.Selected("{\"repairs\":[\"microcode:intel\"]}"), "cpu-microcode-enters-download-planning");
        Require(InstallerOptions.CpuFrequencyComponents.All(id => !panel.Selection.Repairs.Contains(id))
            && !FirmwareDownloads.Selected("{\"repairs\":[\"cpufreq:intel\"]}"), "cpu-frequency-starts-unselected-and-needs-no-download");
        InstallerOptions.Validate(new SelectedOptions(1, ["microcode:intel", "cpufreq:intel"], ["core:maintenance"], "en-US", "system"));
        try { InstallerOptions.Validate(new SelectedOptions(1, ["cpufreq:amd", "cpufreq:intel"], ["core:maintenance"], "en-US", "system")); throw new InvalidOperationException("Both frequency vendors accepted"); }
        catch (IOException) { events.Add("cpu-frequency-vendors-exclusive"); }
        try { InstallerOptions.Validate(new SelectedOptions(1, ["cpufreq:intel"], [], "en-US", "system")); throw new InvalidOperationException("Frequency without maintenance accepted"); }
        catch (IOException) { events.Add("cpu-frequency-requires-maintenance"); }
        foreach (var mode in new[] { "local", "imported" })
        {
            try { InstallerOptions.Validate(new SelectedOptions(1, ["cpufreq:intel"], ["core:maintenance"], "en-US", "system", InstallMode: mode)); throw new InvalidOperationException("Frequency injection accepted"); }
            catch (IOException) { events.Add(mode + "-rejects-cpu-frequency"); }
        }
        var digest = new string('a', 64);
        try { InstallerOptions.Validate(new SelectedOptions(1, ["cpufreq:intel"], ["core:maintenance"], "en-US", "system", DevicePackage: digest[..24], DeviceManifestSha256: digest, DeviceTrust: "developer-session")); throw new InvalidOperationException("Package with frequency accepted"); }
        catch (IOException) { events.Add("package-rejects-cpu-frequency"); }
        try { InstallerOptions.ValidateDeploymentPolicy(new SelectedOptions(1, ["microcode:intel"], [], "en-US", "system"), Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString("N"))); throw new InvalidOperationException("Missing CPU metadata accepted"); }
        catch (IOException) { events.Add("cpu-microcode-requires-metadata"); }
        try { InstallerOptions.Validate(new SelectedOptions(1, ["microcode:amd", "microcode:intel"], [], "en-US", "system")); throw new InvalidOperationException("Both CPU vendors accepted"); }
        catch (IOException) { events.Add("cpu-vendors-exclusive"); }
        try { InstallerOptions.Validate(new SelectedOptions(1, ["microcode:amd"], [], "en-US", "system", InstallMode: "local")); throw new InvalidOperationException("CPU injection into local image accepted"); }
        catch (IOException) { events.Add("cpu-microcode-rejects-local-image"); }
        Require(window.FindName("PhysicalMode") == null,"physical-mode-checkbox-removed");
        Require(((CheckBox)window.FindName("Upgrade310")).IsChecked==true,"official-3.10-source-selected-by-default");
        var modeBanner = ((TextBlock)window.FindName("ModeBanner")).Text;
        Require((modeBanner.Contains("备份") || modeBanner.Contains("Back up")) && !modeBanner.Contains("Beta"),"backup-and-formatting-are-explicit-in-release-banner");
        Require(panel.Selection.Repairs.Order().SequenceEqual(InstallerOptions.Defaults.Repairs.Order()), "repair-defaults-match-language");
        Require(!panel.RepairsExpander.IsExpanded, "device-repairs-start-collapsed");
        Require(!panel.Children.OfType<TextBlock>().Any(x => x.Text.Contains("系统更新") || x.Text.Contains("system updates"))
            && !panel.Describe().Contains("系统更新") && !panel.Describe().Contains("system updates"), "options-and-plan-do-not-promise-system-update-pause");
        Require(catalog.Repairs.Where(x => x.Available && InstallerOptions.WirelessComponents.Contains(x.Id))
            .All(x => !x.Default && !panel.Selection.Repairs.Contains(x.Id)), "optional-wireless-firmware-starts-unselected");
        Require(panel.Selection.Plugins.Order().SequenceEqual(catalog.Plugins.Where(x => x.Default).Select(x => x.Id).Order()), "plugin-defaults-match-catalog");
        Require(catalog.Repairs.Concat(catalog.Plugins).All(x => x.Id is not ("gamescope-env" or "flathub-ustc" or "waydroid" or "hdr" or "windows-reboot")),
            "model-specific-and-third-party-options-stay-out-of-generic-catalog");
        var boot = (BootOptionsPanel)((StackPanel)window.FindName("BootOptionsHost")).Children[0];
        Require(boot.InstallChoice.IsEnabled && boot.InstallChoice.IsChecked == true
            && boot.Selection == new RefindChoice("SteamOS", 2), "refind-install-selected-by-default-with-two-second-timeout");
        boot.InstallChoice.IsChecked = false;
        Require(boot.Selection == null, "refind-install-can-be-opted-out");
        boot.InstallChoice.IsChecked = true;
        Require(boot.Selection == new RefindChoice("SteamOS", 2), "refind-install-settings-reach-request");
        try { RefindInstallation.Validate(new("Windows", 5),null); throw new InvalidOperationException("Unbound Windows default accepted"); }
        catch (IOException) { events.Add("unidentified-windows-default-refused"); }
        var windowsSnapshot = "[{\"partitions\":[{\"isSystem\":true,\"gptType\":\"c12a7328-f81f-11d2-ba4b-00a0c93ec93b\",\"guid\":\"12345678-1234-1234-1234-123456789abc\"}]}]";
        var identified = new PartitionSelection(windowsSnapshot,false,0,0,0);
        Require(RefindInstallation.WindowsEsp(identified)=="12345678-1234-1234-1234-123456789abc","windows-esp-bound-to-gpt-identity");
        RefindInstallation.Validate(new("Windows",0),identified);
        Require(boot.Configuration().Contains("default_selection \"SteamOS\"") && boot.Configuration().Contains("timeout 2"), "refind-default-settings");
        boot.InstallChoice.IsChecked = true;
        boot.WindowsChoice.IsChecked = true;
        boot.Plus.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Require(boot.Configuration().Contains("default_selection \"Windows\"") && boot.Configuration().Contains("timeout 3"), "refind-windows-and-touch-plus");
        boot.Minus.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Require(boot.Seconds.Text == "2", "refind-touch-minus");
        boot.Seconds.Text = "1"; boot.Minus.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Require(boot.Seconds.Text == "1", "refind-timeout-lower-bound");
        boot.Seconds.Text = "3600"; boot.Plus.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Require(boot.Seconds.Text == "3600", "refind-timeout-upper-bound");
        boot.Seconds.Text = "bad";
        try { boot.Configuration(); throw new IOException("invalid-refind-timeout-accepted"); }
        catch (InvalidOperationException) { events.Add("invalid-refind-timeout-rejected"); }
        boot.Plus.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        Require(boot.Seconds.Text == "3", "refind-invalid-timeout-adjustment-starts-from-two");
        boot.ForeverChoice.IsChecked = true;
        Require(boot.Configuration().Contains("timeout 0") && !boot.Seconds.IsEnabled && !boot.Plus.IsEnabled, "refind-wait-forever-disables-timer");
        var exported = Path.Combine(Backend.Root, "logs", "refind-settings-ui-check.conf");
        boot.WriteConfiguration(exported);
        Require(File.ReadAllText(exported) == boot.Configuration(), "refind-export-matches-settings");
        boot.Seconds.Text = "2"; boot.TimedChoice.IsChecked = true;
        var boxes = panel.OptionBoxes;
        Require(new[] { "steamgriddb", "protondb", "tabmaster", "css-loader", "autoflatpaks", "deck-settings", "hltb", "animation-changer" }
            .All(id => !boxes.ContainsKey(id)), "user-installed-third-party-plugins-are-not-bundled-options");
        ((ComboBox)window.FindName("InstallerLanguage")).SelectedValue = "zh-CN";
        Require(boxes["wifi:intel"].IsChecked == false, "chinese-language-does-not-select-wireless-firmware");
        ((ComboBox)window.FindName("InstallerLanguage")).SelectedValue = "en-US";
        Require(boxes["wifi:intel"].IsChecked == false, "english-language-does-not-select-wireless-firmware");
        Require(panel.Selection.AutomaticSystemLanguage && panel.Selection.KdeLanguage == "en_US",
            "user-interface-switch-updates-automatic-target-language");
        boxes["wifi:intel"].IsChecked = true;
        ((ComboBox)window.FindName("InstallerLanguage")).SelectedValue = "zh-CN";
        ((ComboBox)window.FindName("InstallerLanguage")).SelectedValue = "en-US";
        Require(boxes["wifi:intel"].IsChecked == true, "explicit-wireless-choice-survives-language-change");
        boxes["wifi:intel"].IsChecked = false;
        ((ComboBox)window.FindName("InstallerLanguage")).SelectedValue = "zh-CN";
        Require(boxes["wifi:intel"].IsChecked == false, "explicit-wireless-opt-out-survives-chinese");
        Require(!panel.Selection.Repairs.Contains("wifi:intel"), "disabled-repair-is-absent-from-request");
        boxes["core:decky"].IsChecked = false;
        Require(!panel.Selection.Plugins.Contains("core:decky"), "disabled-project-plugin-is-absent-from-request");
        try
        {
            InstallerOptions.Validate(panel.Selection with { Plugins = ["unknown:plugin"] });
            throw new IOException("unknown-option-was-accepted");
        }
        catch (IOException error) when (error.Message != "unknown-option-was-accepted") { events.Add("unknown-option-refused"); }
        boxes["core:decky"].IsChecked = true;
        ((ComboBox)window.FindName("InstallerLanguage")).SelectedValue = "en-US";
        Require(InstallerI18n.Language == "en-US" && ((Button)window.FindName("NextButton")).Content.ToString() == "Next", "english-navigation-applied");
        Require(((TextBlock)window.FindName("ModeBanner")).Text.Contains("Back up"),"backup-warning-translated");
        Require(((TextBlock)boxes["wifi:intel"].Content).Text.Contains("Intel Wi-Fi firmware"), "english-repair-label-applied");
        Require(boxes["wifi:intel"].IsChecked == false && boxes["core:decky"].IsChecked == true, "language-switch-preserves-user-choices");
        Require(boot.WindowsChoice.IsChecked == true && boot.Seconds.Text == "2" && boot.InstallChoice.IsChecked == true, "language-switch-preserves-refind-settings");
        var kde = panel.KdeChoice; kde.SelectedIndex = 1;
        Require(panel.Selection.KdeLanguage == "zh_CN" && panel.Selection.InstallerLanguage == "en-US" && !panel.Selection.AutomaticSystemLanguage,
            "manual-kde-language-independent-of-installer-language");
        var json = JsonSerializer.Serialize(panel.Selection); var roundtrip = JsonSerializer.Deserialize<SelectedOptions>(json)!;
        InstallerOptions.Validate(roundtrip); Require(roundtrip.KdeLanguage == "zh_CN" && !roundtrip.Repairs.Contains("wifi:intel"), "selection-roundtrip-retains-language-and-opt-outs");
        var languagePicker = (ComboBox)window.FindName("InstallerLanguage");
        var next = (Button)window.FindName("NextButton");
        Require(languagePicker.Items.Count == 8 && catalog.Languages.ToHashSet().SetEquals(InstallerI18n.Languages.Select(x => x.Tag)),
            "eight-language-picker-and-validation-agree");
        foreach (var (input, expected) in new[]
        {
            ("ja-JP", "ja-JP"), ("ko-KR", "ko-KR"), ("de-CH", "de-DE"), ("fr-CA", "fr-FR"),
            ("es-419", "es-ES"), ("zh-HK", "zh-TW"), ("zh-Hant", "zh-TW"), ("zh-Hans-SG", "zh-CN"),
            ("en-GB", "en-US"), ("ru-RU", "en-US"), ("", "en-US")
        })
            Require(InstallerI18n.DetectLanguage(input) == expected, "detects-ui-culture-" + input);
        var recentField = typeof(MainWindow).GetField("recent", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)!;
        var originalRecent = recentField.GetValue(window);
        recentField.SetValue(window, new InstallationSession(1, "fixture", InstallationTargetKind.InternalShared,
            "fixture.img", true, "awaiting-device-boot", "complete", null, null, DateTime.UtcNow, Physical: true));
        foreach (var option in InstallerI18n.Languages.Where(option => option.Tag is not ("en-US" or "zh-CN")))
        {
            languagePicker.SelectedValue = option.Tag;
            Require(InstallerI18n.Language == option.Tag && window.Language.IetfLanguageTag.Equals(option.Tag, StringComparison.OrdinalIgnoreCase)
                && next.Content is string nextLabel && nextLabel != "Next"
                && languagePicker.ToolTip is string pickerLabel && pickerLabel != "Interface language" && pickerLabel != "界面语言",
                "switches-ui-to-" + option.Tag);
            Require(panel.Selection.InstallerLanguage == option.Tag && panel.Selection.KdeLanguage == "zh_CN"
                && !panel.Selection.Repairs.Contains("wifi:intel") && panel.Selection.Plugins.Contains("core:decky"),
                "preserves-selections-in-" + option.Tag);
            Require(((TextBlock)window.FindName("BootPrerequisitesMessage")).Text.Contains("BitLocker", StringComparison.Ordinal),
                "boot-safety-notice-remains-visible-in-" + option.Tag);
            Require(((TextBlock)window.FindName("RecentText")).Text == InstallerI18n.L(
                "上次安装写入完成，可以启动 SteamOS。", "The last installation was written successfully. You can boot SteamOS."),
                "recent-task-status-remains-visible-in-" + option.Tag);
            var decky = catalog.Plugins.Single(item => item.Id == "core:decky");
            Require(panel.Describe().Contains(InstallerI18n.L(decky.Zh, decky.En), StringComparison.Ordinal),
                "review-label-uses-current-language-" + option.Tag);
            InstallerOptions.Validate(panel.Selection);
            Require(InstallerI18n.L("未收录的标签", "Unlisted test label") == "Unlisted test label",
                "missing-translation-falls-back-to-english-" + option.Tag);
            var modelName = JsonSerializer.SerializeToElement(new { zh = "机型描述", en = "Device description" });
            Require(InstallerI18n.TryLocalizedValue(modelName, out var name) && name.GetString() == "Device description",
                "model-metadata-falls-back-to-english-" + option.Tag);
        }
        recentField.SetValue(window, originalRecent);
        ((ComboBox)window.FindName("InstallerLanguage")).SelectedValue = "zh-CN"; Require(((Button)window.FindName("NextButton")).Content.ToString() == "下一步", "chinese-navigation-restored");
        foreach (var size in new[] { new Size(850, 620), new Size(1060, 790), new Size(1280, 800) })
        {
            window.Width = size.Width; window.Height = size.Height; window.PreviewStep(2); window.UpdateLayout();
            Require(((ScrollViewer)window.FindName("PageScroll")).ActualHeight > 140, "options-scrollable-at-" + size.Width);
        }
        CheckAcceptedReview(window, events);
        return JsonSerializer.Serialize(new { passed = true, events, desktopLanguageDefaults }, new JsonSerializerOptions { WriteIndented = true });
    }
    internal static List<JsonElement> CheckDesktopLanguageDefaults(List<string> events)
    {
        var original = InstallerI18n.Language;
        var defaults = new List<JsonElement>();
        void Require(bool condition, string name) { if (!condition) throw new IOException(name); events.Add(name); }
        try
        {
            foreach (var language in new[] { "zh-CN", "en-US" })
            {
                InstallerI18n.SetLanguage(language);
                var panel = new InstallerOptionsPanel();
                Require(!panel.Children.OfType<TextBlock>().Any(x => x.Text.Contains("系统更新") || x.Text.Contains("system updates"))
                    && !panel.Describe().Contains("系统更新") && !panel.Describe().Contains("system updates"),
                    "no-system-update-pause-disclosure-" + language);
                var expected = InstallerOptions.DefaultKdeLanguage;
                Require(panel.Selection.KdeLanguage == expected && InstallerOptions.Defaults.KdeLanguage == expected && panel.Selection.AutomaticSystemLanguage,
                    "desktop-default-follows-windows-" + language);
                var selection = panel.Selection;
                InstallerOptions.Validate(selection);
                defaults.Add(JsonSerializer.SerializeToElement(selection, new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower }));
                var changes = 0; panel.Changed += (_, _) => changes++;
                InstallerI18n.SetLanguage(language == "zh-CN" ? "en-US" : "zh-CN"); panel.Refresh();
                Require(panel.Selection.KdeLanguage == expected,
                    "untouched-desktop-keeps-windows-language-" + language);
                Require(changes == 0, "translating-labels-does-not-edit-desktop-plan-" + language);
                panel.FollowInterfaceLanguage("ja-JP");
                Require(panel.Selection.KdeLanguage == "ja_JP" && panel.Selection.AutomaticSystemLanguage,
                    "automatic-target-follows-explicit-interface-choice-" + language);
                changes = 0;
                foreach (var manual in new[] { "system", "zh_TW", "zh_CN", "en_US" })
                {
                    var previous = panel.Selection.KdeLanguage;
                    panel.KdeChoice.SelectedItem = panel.KdeChoice.Items.Cast<ComboBoxItem>().Single(x => (string)x.Tag == manual);
                    Require(previous == manual ? changes == 0 : changes > 0, "manual-desktop-language-notifies-plan-" + language + "-" + manual);
                    changes = 0;
                    panel.FollowInterfaceLanguage("ko-KR");
                    InstallerI18n.SetLanguage("zh-CN"); panel.Refresh();
                    InstallerI18n.SetLanguage("en-US"); panel.Refresh();
                    Require(panel.Selection.KdeLanguage == manual && changes == 0,
                        "manual-desktop-language-survives-switches-" + language + "-" + manual);
                    var roundtrip = JsonSerializer.Deserialize<SelectedOptions>(JsonSerializer.Serialize(panel.Selection))!;
                    Require(roundtrip.KdeLanguage == manual, "desktop-language-roundtrip-" + language + "-" + manual);
                }
                panel.KdeChoice.SelectedItem = panel.KdeChoice.Items.Cast<ComboBoxItem>().Single(x => (string)x.Tag == "auto");
                Require(panel.Selection.KdeLanguage == "ko_KR" && panel.Selection.AutomaticSystemLanguage,
                    "return-to-automatic-uses-latest-interface-choice-" + language);
            }
        }
        finally { InstallerI18n.SetLanguage(original); }
        return defaults;
    }
    private static void CheckAcceptedReview(MainWindow window, List<string> events)
    {
        T Find<T>(string name) => (T)window.FindName(name);
        void Click(string name) => Find<Button>(name).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
        void Require(bool condition, string name) { if (!condition) throw new IOException(name); events.Add(name); }
        var panel = BoilerCustomBuildChecks.Options(window);
        const System.Reflection.BindingFlags flags = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic;
        T Field<T>(string name) => (T)typeof(InstallerOptionsPanel).GetField(name, flags)!.GetValue(panel)!;
        var source = Path.Combine(Backend.Root, "logs", "consent-check-" + Guid.NewGuid().ToString("N") + ".img");
        File.WriteAllBytes(source, RecoveryImageChecks.Fixture());
        try
        {
            var picker = Find<PartitionPicker>("PartitionSelector");
            picker.LoadSnapshot(PartitionInventory.ExampleJson, false);
            picker.Partitions.SelectedItem = picker.Partitions.Items.Cast<PartitionRow>().First(r => r.Space != null);
            picker.CapacityInput.Text = "128";
            Find<CheckBox>("UsePrepared").IsChecked = false;
            // Consent is independent of downloaded 3.10 resources. Keep this
            // fixture portable to a freshly extracted package with no cache.
            Find<CheckBox>("Upgrade310").IsChecked = false;
            Find<TextBox>("ImagePath").Text = source;
            // This check isolates consent behavior; source inspection is covered separately.
            var sourceKey = (string)typeof(MainWindow).GetMethod("SourceInfoKey", flags)!.Invoke(window, null)!;
            var sourceDigest = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(source))).ToLowerInvariant();
            typeof(MainWindow).GetField("inspectedSourceKey", flags)!.SetValue(window, sourceKey);
            typeof(MainWindow).GetField("inspectedSourceDigest", flags)!.SetValue(window, sourceDigest);
            var boot = (BootOptionsPanel)Find<StackPanel>("BootOptionsHost").Children[0];
            boot.InstallChoice.IsChecked = false;
            Field<CheckBox>("setDeckPassword").IsChecked = true;
            var password = Field<PasswordBox>("deckPassword");
            var confirmation = Field<PasswordBox>("deckPasswordConfirm");
            password.Password = confirmation.Password = "fixture-password";
            Click("TargetTab"); Click("NextButton"); Click("SafetyContinue"); Click("ReviewTab");
            var consent = Find<CheckBox>("PhysicalConsent"); consent.IsChecked = true;
            bool ReviewGreen()
            {
                var label = Find<TextBlock>("NavReview");
                var color = ((SolidColorBrush)label.Foreground).Color;
                return label.ToolTip?.ToString() == PartitionInventory.L("已完成配置。", "Setup complete.")
                    && (color == Color.FromRgb(134, 239, 172) || color == Colors.White);
            }
            Require(ReviewGreen(), "valid-physical-plan-is-confirmed-before-start");
            var request = (InstallationRequest)typeof(MainWindow).GetProperty("Request", flags)!.GetValue(window)!;
            var changes = 0;
            EventHandler changed = (_, _) => changes++;
            panel.Changed += changed;
            try
            {
                panel.ClearSensitiveInputs();
                Require(password.Password.Length == 0 && confirmation.Password.Length == 0 && request.InitialDeckPassword == "fixture-password",
                    "password-ui-cleared-but-captured-request-retains-password");
                Require(changes == 0 && consent.IsChecked == true && ReviewGreen(), "internal-password-cleanup-preserves-plan-consent");
                typeof(MainWindow).GetField("operation", flags)!.SetValue(window, new CancellationTokenSource());
                window.PreviewStep(5);
                Require(ReviewGreen() && consent.IsChecked == true && !consent.IsEnabled, "accepted-review-stays-green-on-running-progress-page");
                window.FinishOperation();
                password.Password = "changed-password";
                Require(changes == 1 && consent.IsChecked != true && !ReviewGreen(), "user-password-edit-still-invalidates-plan-consent");
            }
            finally { panel.Changed -= changed; }
        }
        finally { window.FinishOperation(); File.Delete(source); }
    }
}
