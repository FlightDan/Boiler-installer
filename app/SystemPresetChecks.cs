using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;

namespace OxpInstaller;

internal static class SystemPresetChecks
{
    internal static Task<string> RunAsync() => Application.Current.Dispatcher.CheckAccess()
        ? OnUiThread() : Application.Current.Dispatcher.InvokeAsync(OnUiThread).Task.Unwrap();

    private static async Task<string> OnUiThread()
    {
        var directory = Path.Combine(InstallationController.Work, "system-preset-checks-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        var checks = new List<string>();
        var captures = new List<string>();
        var captureErrors = new List<string>();
        var originalLanguage = InstallerI18n.Language;
        MainWindow? window = null;
        void Require(bool condition, string name) { if (!condition) throw new IOException(name); checks.Add(name); }
        void Refuse(Action action, string name)
        {
            var refused = false;
            try { action(); } catch (IOException) { refused = true; }
            Require(refused, name);
        }
        foreach (var (culture, expected) in new[] { ("zh-CN", "zh_CN"), ("zh-SG", "zh_CN"), ("zh-Hans", "zh_CN"),
            ("zh-TW", "zh_TW"), ("zh-HK", "zh_TW"), ("zh-Hant-HK", "zh_TW"), ("en-GB", "en_US"), ("en-US", "en_US"),
            ("ja-JP", "ja_JP"), ("ko-KR", "ko_KR"), ("de-AT", "de_DE"), ("fr-CA", "fr_FR"), ("es-MX", "es_ES"),
            ("ru-RU", "ru_RU"), ("it-IT", "system"), ("", "system") })
            Require(InstallerOptions.KdeLanguageForWindows(culture) == expected, "windows-language-map-" + culture);
        _ = OptionsChecks.CheckDesktopLanguageDefaults(checks);
        async Task Flush() { window!.UpdateLayout(); await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle); }
        try
        {
            foreach (var language in new[] { "zh-CN", "en-US" })
            {
                InstallerI18n.SetLanguage(language);
                window = new MainWindow(rehearsal: true) { WindowState = WindowState.Normal, ShowActivated = false, ShowInTaskbar = false,
                    DeviceIndexLoaderForChecks = _ => Task.FromResult(BoilerDeviceChecks.IndexResponse()) };
                ((PartitionPicker)window.FindName("PartitionSelector")).AutomaticReadEnabled = false;
                window.Show();
                foreach (var mode in new[] { "custom", "vanilla" })
                {
                    window.BoilerModeForChecks(mode);
                    typeof(MainWindow).GetMethod("ChooseGenericInstallation", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)!.Invoke(window, null);
                    window.PreviewStep(2); await Flush();
                    var panel = ((StackPanel)window.FindName("ComponentOptionsHost")).Children.OfType<InstallerOptionsPanel>().Single();
                    var prefix = language + "-" + mode;
                    Require(panel.SystemPresetsPanel.IsVisible && panel.KdeChoice.IsVisible && panel.SshChoice.IsVisible && panel.PasswordChoice.IsVisible, prefix + "-presets-visible");
                    Require(panel.Selection.KdeLanguage == InstallerOptions.DefaultKdeLanguage && !panel.Selection.EnableSsh && !panel.Selection.SetDeckPassword, prefix + "-defaults-follow-windows-language");
                    InstallerI18n.SetLanguage(language == "zh-CN" ? "en-US" : "zh-CN"); panel.Refresh();
                    Require(panel.Selection.KdeLanguage == InstallerOptions.DefaultKdeLanguage, prefix + "-installer-language-switch-does-not-change-windows-default");
                    InstallerI18n.SetLanguage(language); panel.Refresh();
                    Require(panel.Children[^1] == panel.OptionsNotesPanel && panel.OptionsNotesPanel.IsVisible
                        && panel.OptionsNotesPanel.Children.OfType<TextBlock>().All(note => note.Text.Length < 110), prefix + "-short-notes-at-bottom");
                    Require(panel.PasswordInput.Visibility == Visibility.Collapsed && panel.PasswordConfirmation.Visibility == Visibility.Collapsed, prefix + "-password-collapsed-by-default");
                    panel.PasswordChoice.IsChecked = true;
                    panel.PasswordInput.Password = "short"; panel.PasswordConfirmation.Password = "short";
                    Refuse(panel.ValidateDeckPassword, prefix + "-short-password-refused");
                    const string secret = "fixture-only-password";
                    panel.PasswordInput.Password = secret; panel.PasswordConfirmation.Password = "different-password";
                    Refuse(panel.ValidateDeckPassword, prefix + "-mismatch-refused");
                    panel.PasswordConfirmation.Password = secret; panel.ValidateDeckPassword();
                    panel.SshChoice.IsChecked = true;
                    panel.KdeChoice.SelectedIndex = 1;
                    Require(window.CurrentOptions.SetDeckPassword && window.CurrentOptions.EnableSsh && window.CurrentOptions.KdeLanguage == "zh_CN", prefix + "-choices-reach-current-plan");
                    InstallerOptions.Validate(window.CurrentOptions);
                    Require(!JsonSerializer.Serialize(window.CurrentOptions).Contains(secret) && !panel.Describe().Contains(secret), prefix + "-no-password-in-serialized-plan-or-summary");
                    foreach (var size in new[] { new Size(1060, 720), new Size(853, 480), new Size(640, 360) })
                    {
                        window.Width = size.Width; window.Height = size.Height;
                        panel.KdeChoice.BringIntoView(); await Flush();
                        var scroll = (ScrollViewer)window.FindName("PageScroll");
                        Require(scroll.ExtentWidth <= scroll.ViewportWidth + 1, prefix + "-no-horizontal-overflow-" + size.Width);
                        Require(panel.PasswordInput.ActualHeight >= 48 && panel.PasswordConfirmation.ActualHeight >= 48, prefix + "-password-touch-height-" + size.Width);
                        foreach (var control in new Control[] { panel.KdeChoice, panel.PasswordChoice, panel.PasswordInput, panel.PasswordConfirmation, panel.SshChoice })
                        {
                            control.BringIntoView(); await Flush();
                            var bounds = control.TransformToAncestor(scroll).TransformBounds(new Rect(0, 0, control.ActualWidth, control.ActualHeight));
                            Require(bounds.Top >= -1 && bounds.Bottom <= scroll.ActualHeight + 1, prefix + "-control-scroll-reachable-" + control.GetType().Name + "-" + size.Width);
                        }
                        panel.KdeChoice.BringIntoView(); await Flush();
                        if (mode == "custom" && size.Width is 1060 or 640)
                        {
                            var path = Path.Combine(directory, language + "-presets-" + size.Width + ".png");
                            try { await BoilerUiChecks.CaptureAsync((FrameworkElement)window.Content, path); captures.Add(path); }
                            catch (IOException error) { captureErrors.Add(error.Message); }
                            scroll.ScrollToBottom(); await Flush();
                            var notesPath = Path.Combine(directory, language + "-notes-" + size.Width + ".png");
                            try { await BoilerUiChecks.CaptureAsync((FrameworkElement)window.Content, notesPath); captures.Add(notesPath); }
                            catch (IOException error) { captureErrors.Add(error.Message); }
                        }
                    }
                    window.PreviewStep(3); await Flush();
                    var summary = ((TextBlock)window.FindName("ReviewLayout")).Text;
                    Require(summary.Contains("SSH:") && summary.Contains("简体中文") && !summary.Contains(secret), prefix + "-review-discloses-presets-without-password");
                    var changes = 0; panel.Changed += Changed;
                    void Changed(object? sender, EventArgs e) => changes++;
                    panel.ClearSensitiveInputs();
                    Require(changes == 0 && panel.PasswordInput.Password.Length == 0 && panel.PasswordConfirmation.Password.Length == 0, prefix + "-internal-secret-cleanup-does-not-edit-plan");
                    panel.PasswordInput.Password = panel.PasswordConfirmation.Password = secret;
                    Require(changes > 0, prefix + "-user-password-edit-notifies-plan");
                    InstallerI18n.SetLanguage(language == "zh-CN" ? "en-US" : "zh-CN"); panel.Refresh();
                    Require(panel.Selection.SetDeckPassword && panel.Selection.EnableSsh && panel.Selection.KdeLanguage == "zh_CN", prefix + "-language-switch-retains-choices");
                    InstallerI18n.SetLanguage(language); panel.Refresh();
                    panel.PasswordChoice.IsChecked = false;
                    Require(panel.PasswordInput.Password.Length == 0 && panel.PasswordConfirmation.Password.Length == 0
                        && panel.InitialDeckPassword == null && panel.PasswordInput.Visibility == Visibility.Collapsed,
                        prefix + "-password-opt-out-clears-hidden-inputs");
                    panel.PasswordChoice.IsChecked = true;
                    Refuse(panel.ValidateDeckPassword, prefix + "-reenabling-password-requires-fresh-input");
                    panel.PasswordInput.Password = panel.PasswordConfirmation.Password = secret;
                    panel.SetWirelessContext(mode, hasPackage: true);
                    Require(panel.SystemPresetsPanel.Visibility == Visibility.Visible && panel.Selection.EnableSsh, prefix + "-device-route-retains-system-presets");
                    foreach (var selection in new[]
                    {
                        new SelectedOptions(1, [], [], language, "zh_CN", InstallMode: mode),
                        new SelectedOptions(1, [], [], language, "system", EnableSsh: true, InstallMode: mode),
                        new SelectedOptions(1, [], [], language, "system", SetDeckPassword: true, InstallMode: mode)
                    })
                    {
                        InstallerOptions.Validate(selection);
                        InstallerOptions.ValidateDeploymentPolicy(selection);
                        Require(true, prefix + "-preset-deployment-enabled-" + selection.KdeLanguage + selection.EnableSsh + selection.SetDeckPassword);
                        var controllerRefused = false;
                        try { InstallationController.ValidateRequest(new(InstallationTargetKind.ExternalDisk, "", false, selection,
                            InitialDeckPassword: selection.SetDeckPassword ? secret : null, Mode: mode)); }
                        catch (IOException error) { controllerRefused = error.Message.Contains("请选择存在的系统镜像") || error.Message.Contains("Select an existing"); }
                        Require(controllerRefused, prefix + "-controller-reaches-source-validation-with-presets");
                    }
                    var passwordSelection = new SelectedOptions(1, [], [], language, "system", SetDeckPassword: true, InstallMode: mode);
                    void RejectPasswordRequest(SelectedOptions selected, string? value, string name)
                    {
                        var rejected = false;
                        try { InstallationController.ValidateRequest(new(InstallationTargetKind.ExternalDisk, "", false, selected, InitialDeckPassword: value, Mode: mode)); }
                        catch (IOException error) { rejected = error.Message.Contains("密码") || error.Message.Contains("password", StringComparison.OrdinalIgnoreCase); }
                        Require(rejected, name);
                    }
                    foreach (var invalidSecret in new string?[] { null, "short", "password\ninvalid", new string('密', 100) })
                        RejectPasswordRequest(passwordSelection, invalidSecret, prefix + "-invalid-password-request-refused-" + (invalidSecret?.Length ?? 0));
                    RejectPasswordRequest(new SelectedOptions(1, [], [], language, "system", InstallMode: mode), secret,
                        prefix + "-unselected-secret-refused");
                    panel.Changed -= Changed;
                    ((Button)window.FindName("BackHomeButton")).RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); await Flush();
                    Require(panel.PasswordInput.Password.Length == 0 && panel.PasswordConfirmation.Password.Length == 0,
                        prefix + "-returning-home-clears-password-inputs");
                    window.BoilerModeForChecks("imported");
                    Require(!window.CurrentOptions.SetDeckPassword && !window.CurrentOptions.EnableSsh && window.CurrentOptions.KdeLanguage == "system"
                        && panel.InitialDeckPassword == null && panel.PasswordInput.Password.Length == 0 && panel.PasswordConfirmation.Password.Length == 0,
                        prefix + "-new-image-route-clears-presets-and-secrets");
                    Refuse(() => InstallerOptions.Validate(new(1, [], [], language, "system", EnableSsh: true, InstallMode: "imported")), prefix + "-imported-cannot-inject-presets");
                }
                window.Close(); window = null;
            }
        }
        finally { window?.Close(); InstallerI18n.SetLanguage(originalLanguage); }
        var json = JsonSerializer.Serialize(new { passed = true, checks, captures, captureErrors, directory,
            scope = "WPF preset selection reaches deployment validation; secret exclusion, validation, reset, summary and layout. Image execution and hardware acceptance have separate evidence." }, new JsonSerializerOptions { WriteIndented = true });
        await File.WriteAllTextAsync(Path.Combine(directory, "result.json"), json);
        return json;
    }
}
