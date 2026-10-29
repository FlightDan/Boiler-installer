using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

namespace OxpInstaller;

internal static class BoilerUiChecks
{
    private sealed record FocusTraversalResult(bool Passed, string[] Order, string? Error, int Steps);

    private static readonly Size[] WindowSizes =
    [
        new(1280, 800),
        new(1060, 720),
        new(853, 480),
        new(640, 360)
    ];

    internal static Task<string> RunAsync()
    {
        var dispatcher = Application.Current?.Dispatcher;
        return dispatcher != null && !dispatcher.CheckAccess()
            ? dispatcher.InvokeAsync(RunOnUiThreadAsync, DispatcherPriority.Normal).Task.Unwrap()
            : RunOnUiThreadAsync();
    }

    private static async Task<string> RunOnUiThreadAsync()
    {
        var directory = Path.Combine(InstallationController.Work, "work", "boiler-ui-checks-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        var checks = new List<object>();
        var failures = new List<string>();
        var layouts = new List<object>();
        var focusRuns = new List<object>();
        var screenshots = new List<string>();
        var screenshotFailures = new List<object>();
        var originalLanguage = InstallerI18n.Language;
        var originalRenderMode = RenderOptions.ProcessRenderMode;
        var application = Application.Current;
        var originalShutdownMode = application?.ShutdownMode;
        Dictionary<string, string>? workSnapshotBeforeNavigation = null;
        string? latestSessionBeforeNavigation = null;
        MainWindow? window = null;
        if (application != null) application.ShutdownMode = ShutdownMode.OnExplicitShutdown;

        void Check(bool passed, string name, object? details = null)
        {
            checks.Add(new { name, passed, details });
            if (!passed) failures.Add(name);
        }

        try
        {
            System.Windows.Media.RenderOptions.ProcessRenderMode = System.Windows.Interop.RenderMode.Default;
            window = new MainWindow(rehearsal: true)
            {
                WindowState = WindowState.Normal,
                ShowActivated = false,
                ShowInTaskbar = false
            };
            window.DeviceIndexLoaderForChecks = _ => Task.FromResult(BoilerDeviceChecks.IndexResponse());
            if (window.FindName("PartitionSelector") is PartitionPicker partitionPicker)
                partitionPicker.AutomaticReadEnabled = false;
            else
                Check(false, "partition-picker-found-and-auto-read-disabled");

            window.Width = WindowSizes[0].Width;
            window.Height = WindowSizes[0].Height;
            window.Show();
            await FlushLayoutAsync(window);
            workSnapshotBeforeNavigation = CaptureWorkSnapshot(directory);
            latestSessionBeforeNavigation = InstallationController.Latest()?.Id;

            var showHome = typeof(MainWindow).GetMethod("ShowBoilerHome", BindingFlags.Instance | BindingFlags.NonPublic);
            Check(showHome != null, "private-boiler-home-entry-found");
            if (showHome == null) throw new InvalidOperationException("ShowBoilerHome was not found.");

            foreach (var language in new[] { "zh-CN", "en-US" })
            {
                SetLanguage(window, language);
                foreach (var size in WindowSizes)
                {
                    SetWindowSize(window, size);
                    showHome.Invoke(window, null);
                    await FlushLayoutAsync(window);
                    var home = GetHomeScreen(window);
                    var root = Find<Grid>(window, "WindowRoot");
                    if (home == null)
                    {
                        Check(false, $"home-screen-created-{language}-{FormatSize(size)}");
                        continue;
                    }

                    var homeLayout = home.Child as Grid;
                    var homeScroll = FindDescendant<ScrollViewer>(home, "HomeScroll");
                    var panel = FindDescendant<StackPanel>(home, "HomeContent");
                    var choices = FindDescendant<UniformGrid>(home, "HomeChoices");
                    var languagePanel = FindDescendant<WrapPanel>(home, "HomeLanguages");
                    var modeButtons = choices?.Children.OfType<Button>()
                        .Where(button => button.Tag is string tag && tag is "custom" or "imported" or "vanilla")
                        .ToDictionary(button => (string)button.Tag, StringComparer.Ordinal);
                    var languageButtons = languagePanel?.Children.OfType<Button>().ToArray();
                    var homeExitButton = FindHomeExitButton(home);
                    var wizardShell = Find<Grid>(window, "WizardShell");
                    var sidebarSource = Find<Button>(window, "SourceTab");
                    var mainContent = Find<Grid>(window, "MainContent");
                    Check(home.IsVisible && homeLayout != null && homeScroll != null && panel != null
                        && ReferenceEquals(homeScroll.Content, panel),
                        $"home-visible-and-named-layout-{language}-{FormatSize(size)}");
                    Check(homeLayout?.Name == "HomeLayout",
                        $"home-grid-has-contract-name-{language}-{FormatSize(size)}");
                    Check(IsFullWindow(home, root) && homeLayout != null && IsFullWindow(homeLayout, root),
                        $"home-covers-full-client-area-{language}-{FormatSize(size)}",
                        new { home = BoundsDescription(home, root), layout = homeLayout == null ? null : BoundsDescription(homeLayout, root), clientDip = new { root.ActualWidth, root.ActualHeight } });
                    Check(homeLayout is { RowDefinitions.Count: >= 3 },
                        $"home-has-header-body-footer-layout-{language}-{FormatSize(size)}",
                        new { rows = homeLayout?.RowDefinitions.Count });
                    Check(!wizardShell.IsVisible && !sidebarSource.IsVisible && !mainContent.IsVisible,
                        $"home-hides-wizard-and-sidebar-{language}-{FormatSize(size)}");
                    Check(choices?.Children.Count == 3 && choices.Children.OfType<Button>().Count() == 3
                        && modeButtons is { Count: 3 } && new[] { "custom", "imported", "vanilla" }.All(modeButtons.ContainsKey),
                        $"home-has-three-primary-mode-cards-{language}-{FormatSize(size)}");
                    Check(choices != null && choices.Columns == (size.Width >= 960 ? 3 : 1),
                        $"home-choice-columns-match-window-width-{language}-{FormatSize(size)}",
                        new { columns = choices?.Columns, expectedColumns = size.Width >= 960 ? 3 : 1, width = choices?.ActualWidth });
                    Check(languageButtons is { Length: 2 }, $"home-has-two-language-buttons-{language}-{FormatSize(size)}");
                    Check(homeExitButton != null && homeExitButton.IsVisible,
                        $"home-exit-action-visible-{language}-{FormatSize(size)}");

                    var homeScrollable = homeScroll is { ScrollableHeight: > 1 };
                    Check(homeScroll != null && homeScroll.ExtentWidth <= homeScroll.ViewportWidth + 1,
                        $"home-no-horizontal-overflow-{language}-{FormatSize(size)}",
                        homeScroll == null ? null : new { extentWidth = homeScroll.ExtentWidth, viewportWidth = homeScroll.ViewportWidth });
                    layouts.Add(new
                    {
                        screen = "home",
                        language,
                        requestedWindowDip = ToSize(size),
                        clientAreaDip = new { widthDip = root.ActualWidth, heightDip = root.ActualHeight },
                        scroll = homeScroll == null ? null : new { homeScroll.ViewportWidth, homeScroll.ViewportHeight, homeScroll.ExtentWidth, homeScroll.ExtentHeight, homeScroll.ScrollableHeight },
                        scrollable = homeScrollable
                    });
                    if (homeScroll != null && modeButtons != null && languageButtons != null && homeExitButton != null)
                    {
                        foreach (var (mode, button) in modeButtons)
                        {
                            Check(button.MinHeight >= 48 && button.ActualWidth >= 48 && button.ActualHeight >= 48,
                                $"home-{mode}-target-at-least-48dip-{language}-{FormatSize(size)}",
                                new { minWidth = button.MinWidth, minHeight = button.MinHeight, width = button.ActualWidth, height = button.ActualHeight });
                            Check(NoHorizontalOverflow(button, root),
                                $"home-{mode}-no-horizontal-clipping-{language}-{FormatSize(size)}");
                            var nestedButtons = Descendants(button).OfType<Button>().Count(candidate => !ReferenceEquals(candidate, button));
                            var nestedFocusableControls = Descendants(button).OfType<UIElement>()
                                .Any(element => !ReferenceEquals(element, button) && element.Focusable && KeyboardNavigation.GetIsTabStop(element));
                            Check(nestedButtons == 0 && !nestedFocusableControls,
                                $"home-{mode}-card-is-one-focusable-action-{language}-{FormatSize(size)}",
                                new { nestedButtons, nestedFocusableControls });
                        }
                        foreach (var (button, index) in languageButtons.Select((button, index) => (button, index)))
                        {
                            Check(button.MinHeight >= 48 && button.ActualWidth >= 48 && button.ActualHeight >= 48,
                                $"home-language-{index}-target-at-least-48dip-{language}-{FormatSize(size)}",
                                new { minWidth = button.MinWidth, minHeight = button.MinHeight, width = button.ActualWidth, height = button.ActualHeight });
                        }
                        Check(size.Height >= 480 || homeScrollable,
                            $"home-content-scrollable-at-short-height-{language}-{FormatSize(size)}",
                            new { scrollableHeight = homeScroll.ScrollableHeight, viewportHeight = homeScroll.ViewportHeight });

                        var focusTargets = modeButtons.Values.Cast<FrameworkElement>().Concat(languageButtons)
                            .Append(homeExitButton).ToArray();
                        var focus = await TraverseFocusAsync(modeButtons["custom"], home, root,
                            focusTargets, requireCycle: true, maxSteps: 64);
                        focusRuns.Add(new { screen = "home", language, windowDip = ToSize(size), focus });
                        Check(focus.Passed, $"home-tab-focus-visible-and-reaches-controls-{language}-{FormatSize(size)}", focus);

                        homeScroll.ScrollToTop();
                        await FlushLayoutAsync(window);
                        if (ShouldCapture("home", language, size))
                        {
                            Keyboard.Focus(modeButtons["custom"]);
                            await CaptureForReviewAsync(root, directory, screenshots, screenshotFailures,
                                $"home-{LanguageCode(language)}-{FormatSize(size)}.png", Check);
                        }
                    }

                    var alternateLanguage = language == "en-US" ? "zh-CN" : "en-US";
                    var alternateLanguageButton = FindHomeLanguageButton(home, alternateLanguage);
                    Check(alternateLanguageButton != null,
                        $"home-language-button-found-for-routed-change-{alternateLanguage}-{FormatSize(size)}");
                    if (alternateLanguageButton != null)
                    {
                        RaiseClick(alternateLanguageButton);
                        await FlushLayoutAsync(window);
                        Check(InstallerI18n.Language == alternateLanguage,
                            $"home-language-routed-click-changes-language-{alternateLanguage}-{FormatSize(size)}");
                        var alternateHome = GetHomeScreen(window);
                        var originalLanguageButton = alternateHome == null ? null : FindHomeLanguageButton(alternateHome, language);
                        Check(originalLanguageButton != null,
                            $"home-original-language-button-found-after-routed-change-{language}-{FormatSize(size)}");
                        if (originalLanguageButton != null)
                        {
                            RaiseClick(originalLanguageButton);
                            await FlushLayoutAsync(window);
                            Check(InstallerI18n.Language == language,
                                $"home-language-routed-click-restores-language-{language}-{FormatSize(size)}");
                        }
                    }

                    foreach (var mode in new[] { "custom", "imported", "vanilla" })
                    {
                        var currentHome = GetHomeScreen(window);
                        var currentButton = currentHome == null ? null : FindHomeModeButton(currentHome, mode);
                        Check(currentButton != null && currentButton.IsEnabled,
                            $"home-{mode}-card-ready-for-routed-click-{language}-{FormatSize(size)}");
                        if (currentButton == null) continue;

                        RaiseClick(currentButton);
                        await FlushLayoutAsync(window);
                        if (mode == "custom")
                        {
                            Check(Find<StackPanel>(window, "AdaptationPage").IsVisible && !sidebarSource.IsVisible,
                                $"home-{mode}-opens-device-support-before-source-{language}-{FormatSize(size)}");
                            var generic = FindDescendant<Button>(window, "ChooseGenericInstallation");
                            Check(generic?.IsVisible == true && generic.IsEnabled,
                                $"{mode}-generic-installation-action-is-ready-{language}-{FormatSize(size)}");
                            if (generic != null) RaiseClick(generic);
                            await FlushLayoutAsync(window);
                        }
                        var source = Find<StackPanel>(window, "SourcePage");
                        var returnedHome = GetHomeScreen(window);
                        Check(window.CurrentOptions.InstallMode == mode && mode == (string?)currentButton.Tag,
                            $"home-{mode}-routed-click-propagates-mode-{language}-{FormatSize(size)}",
                            new { mode = window.CurrentOptions.InstallMode, tag = currentButton.Tag });
                        Check(source.IsVisible && returnedHome?.IsVisible != true && wizardShell.IsVisible && sidebarSource.IsVisible,
                            $"home-{mode}-routed-click-opens-source-page-{language}-{FormatSize(size)}");
                        Check(window.CompletedSession == null,
                            $"home-{mode}-source-navigation-does-not-start-an-installation-{language}-{FormatSize(size)}");

                        var backHome = Find<Button>(window, "BackHomeButton");
                        var sourceFooter = Find<Grid>(window, "PageFooter");
                        var sourcePageScroll = Find<ScrollViewer>(window, "PageScroll");
                        var sourceNext = Find<Button>(window, "NextButton");
                        var sourceBack = Find<Button>(window, "BackButton");
                        var sourceLanguagePanel = Find<UniformGrid>(window, "InstallerLanguage");
                        var sourceChinese = Find<RadioButton>(window, "ChineseLanguage");
                        var sourceEnglish = Find<RadioButton>(window, "EnglishLanguage");
                        Check(sourceFooter.IsVisible && sourceNext.IsVisible && sourceBack.IsVisible == (mode != "imported")
                            && sourceLanguagePanel.IsVisible && sourceChinese.IsVisible && sourceEnglish.IsVisible,
                            $"{mode}-source-has-fixed-footer-and-sidebar-back-home-{language}-{FormatSize(size)}");
                        Check(!sourceNext.IsEnabled,
                            $"{mode}-empty-source-keeps-next-disabled-{language}-{FormatSize(size)}");
                        Check(IsWithinRoot(sourceFooter, root) && IsWithinRoot(sourceNext, root),
                            $"{mode}-source-footer-stays-inside-client-area-{language}-{FormatSize(size)}",
                            new { footer = BoundsDescription(sourceFooter, root), next = BoundsDescription(sourceNext, root) });
                        var footerBoundsBeforeScroll = BoundsRelativeTo(sourceFooter, root);
                        sourcePageScroll.ScrollToEnd();
                        await FlushLayoutAsync(window);
                        var footerBoundsAfterScroll = BoundsRelativeTo(sourceFooter, root);
                        Check(!IsDescendantOrSelf(sourceFooter, sourcePageScroll)
                            && IsWithinRoot(sourceFooter, root)
                            && SameBounds(footerBoundsBeforeScroll, footerBoundsAfterScroll),
                            $"{mode}-source-footer-stays-fixed-while-page-scrolls-{language}-{FormatSize(size)}",
                            new { before = RectDescription(footerBoundsBeforeScroll), after = RectDescription(footerBoundsAfterScroll) });
                        sourcePageScroll.ScrollToTop();
                        await FlushLayoutAsync(window);
                        Check(backHome.IsVisible && backHome.IsEnabled && IsWithinRoot(backHome, root),
                            $"{mode}-back-home-visible-and-enabled-when-idle-{language}-{FormatSize(size)}",
                            BoundsDescription(backHome, root));
                        Check(backHome.ActualWidth >= 48 && backHome.ActualHeight >= 48,
                            $"{mode}-back-home-target-at-least-48dip-{language}-{FormatSize(size)}",
                            new { width = backHome.ActualWidth, height = backHome.ActualHeight });
                        var expectedBackHomeLabel = language == "en-US" ? "Back to home" : "返回安装方式";
                        Check(string.Equals(backHome.Content as string, expectedBackHomeLabel, StringComparison.Ordinal),
                            $"{mode}-back-home-label-localized-and-compact-{language}-{FormatSize(size)}",
                            new { actual = backHome.Content, expected = expectedBackHomeLabel, height = backHome.ActualHeight });
                        if (language == "en-US" && size == WindowSizes[2])
                            Check(backHome.ActualHeight <= 56,
                                $"{mode}-english-back-home-label-fits-one-line-{FormatSize(size)}",
                                new { width = backHome.ActualWidth, height = backHome.ActualHeight });

                        if (mode == "imported")
                        {
                            CheckSingleImageControls(window, source, language, FormatSize(size), Check);
                            if (ShouldCapture("img-source", language, size))
                            {
                                Keyboard.Focus(backHome);
                                await CaptureForReviewAsync(root, directory, screenshots, screenshotFailures,
                                    $"img-source-{LanguageCode(language)}-{FormatSize(size)}.png", Check);
                            }
                        }

                        var sourceFilePickers = FindSourceFilePickerButtons(source);
                        Check(sourceFilePickers.Length > 0,
                            $"{mode}-source-has-an-enabled-file-picker-{language}-{FormatSize(size)}");
                        var sourceFocusTargets = new List<FrameworkElement> { backHome, sourceChinese, sourceEnglish };
                        sourceFocusTargets.AddRange(sourceFilePickers);
                        if (sourceNext.IsEnabled) sourceFocusTargets.Add(sourceNext);
                        var sourceNavigationScroll = FindSidebarNavigationScroll(window);
                        sourceNavigationScroll.ScrollToTop();
                        await FlushLayoutAsync(window);
                        Check(sourceNavigationScroll.ViewportHeight >= 48,
                            $"{mode}-source-sidebar-nav-shows-one-full-tab-{language}-{FormatSize(size)}",
                            new { viewportHeight = sourceNavigationScroll.ViewportHeight, extentHeight = sourceNavigationScroll.ExtentHeight });
                        var sourceFocus = await TraverseFocusAsync(Find<Button>(window, "TargetTab"), wizardShell, root,
                            sourceFocusTargets, requireCycle: false, maxSteps: 256);
                        focusRuns.Add(new { screen = mode + "-source", language, windowDip = ToSize(size), focus = sourceFocus });
                        Check(sourceFocus.Passed,
                            $"{mode}-source-tab-focus-reaches-enabled-controls-{language}-{FormatSize(size)}", sourceFocus);

                        var operationField = typeof(MainWindow).GetField("operation", BindingFlags.Instance | BindingFlags.NonPublic);
                        var updateNavigation = typeof(MainWindow).GetMethod("UpdateNavigationState", BindingFlags.Instance | BindingFlags.NonPublic);
                        Check(operationField != null && updateNavigation != null,
                            $"{mode}-busy-navigation-test-hooks-found-{language}-{FormatSize(size)}");
                        if (operationField != null && updateNavigation != null)
                        {
                            var previousOperation = operationField.GetValue(window);
                            using var busyOperation = new CancellationTokenSource();
                            try
                            {
                                operationField.SetValue(window, busyOperation);
                                updateNavigation.Invoke(window, null);
                                await FlushLayoutAsync(window);
                                Check(!backHome.IsEnabled,
                                    $"{mode}-back-home-disabled-while-busy-{language}-{FormatSize(size)}");
                            }
                            finally
                            {
                                operationField.SetValue(window, previousOperation);
                                updateNavigation.Invoke(window, null);
                                await FlushLayoutAsync(window);
                            }
                        }

                        var officialPanel = typeof(MainWindow).GetField("officialDownloadPanel", BindingFlags.Instance | BindingFlags.NonPublic)
                            ?.GetValue(window) as OfficialDownloadPanel;
                        var downloadOperationField = typeof(OfficialDownloadPanel).GetField("operation", BindingFlags.Instance | BindingFlags.NonPublic);
                        Check(officialPanel != null && downloadOperationField != null,
                            $"{mode}-source-download-busy-test-hooks-found-{language}-{FormatSize(size)}");
                        if (officialPanel != null && downloadOperationField != null && updateNavigation != null)
                        {
                            var previousDownloadOperation = downloadOperationField.GetValue(officialPanel);
                            using var busyDownload = new CancellationTokenSource();
                            try
                            {
                                downloadOperationField.SetValue(officialPanel, busyDownload);
                                updateNavigation.Invoke(window, null);
                                await FlushLayoutAsync(window);
                                Check(!backHome.IsEnabled,
                                    $"{mode}-back-home-disabled-during-source-download-{language}-{FormatSize(size)}");
                            }
                            finally
                            {
                                downloadOperationField.SetValue(officialPanel, previousDownloadOperation);
                                updateNavigation.Invoke(window, null);
                                await FlushLayoutAsync(window);
                            }
                        }

                        Check(InstallerI18n.Language == language,
                            $"{mode}-source-language-state-correct-{language}-{FormatSize(size)}");
                        var verifySidebarReentry = mode == "custom" && language == "en-US" && size == WindowSizes[3];
                        if (verifySidebarReentry)
                        {
                            var navigationScroll = FindSidebarNavigationScroll(window);
                            navigationScroll.ScrollToEnd();
                            await FlushLayoutAsync(window);
                            var sourceTab = Find<Button>(window, "SourceTab");
                            Check(navigationScroll.ScrollableHeight > 0 && navigationScroll.VerticalOffset > 0
                                && !IsWithinScrollViewport(sourceTab, navigationScroll),
                                "sidebar-reentry-regression-precondition-source-tab-scrolled-out-of-view",
                                new { navigationScroll.VerticalOffset, navigationScroll.ScrollableHeight, sourceTab = BoundsDescription(sourceTab, root) });
                        }
                        RaiseClick(backHome);
                        await FlushLayoutAsync(window);
                        var homeAfterBack = GetHomeScreen(window);
                        Check(homeAfterBack?.IsVisible == true && !wizardShell.IsVisible && InstallerI18n.Language == language,
                            $"{mode}-back-home-returns-and-preserves-language-{language}-{FormatSize(size)}");
                        Check((language == "en-US" ? Find<RadioButton>(window, "EnglishLanguage") : Find<RadioButton>(window, "ChineseLanguage")).IsChecked == true,
                            $"{mode}-back-home-language-selection-remains-checked-{language}-{FormatSize(size)}");
                        if (verifySidebarReentry)
                        {
                            var localCardAfterReturn = homeAfterBack == null ? null : FindHomeModeButton(homeAfterBack, "imported");
                            Check(localCardAfterReturn != null && localCardAfterReturn.IsEnabled,
                                "sidebar-reentry-home-card-is-ready");
                            if (localCardAfterReturn != null)
                            {
                                RaiseClick(localCardAfterReturn);
                                await FlushLayoutAsync(window);
                                var sourceTabAfterReentry = Find<Button>(window, "SourceTab");
                                var navigationAfterReentry = FindSidebarNavigationScroll(window);
                                Check(window.CurrentOptions.InstallMode == "imported" && Find<StackPanel>(window, "SourcePage").IsVisible
                                    && IsWithinScrollViewport(sourceTabAfterReentry, navigationAfterReentry),
                                    "sidebar-reentry-clicking-home-card-reveals-active-source-tab",
                                    new { mode = window.CurrentOptions.InstallMode, sourceTab = BoundsDescription(sourceTabAfterReentry, root),
                                        viewportHeight = navigationAfterReentry.ViewportHeight, offset = navigationAfterReentry.VerticalOffset });
                                RaiseClick(Find<Button>(window, "BackHomeButton"));
                                await FlushLayoutAsync(window);
                                Check(GetHomeScreen(window)?.IsVisible == true,
                                    "sidebar-reentry-regression-path-returns-home-after-assertion");
                            }
                        }
                    }

                    foreach (var mode in new[] { "custom", "local", "vanilla", "imported" })
                    {
                        window.BoilerModeForChecks(mode);
                        if (mode is "custom" or "vanilla")
                            typeof(MainWindow).GetMethod("ChooseGenericInstallation", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(window, null);
                        if (mode != "imported") window.PreviewStep(2);
                        else window.PreviewStep(1);
                        await FlushLayoutAsync(window);

                        var shell = Find<Grid>(window, "WizardShell");
                        var pageScroll = Find<ScrollViewer>(window, "PageScroll");
                        var footer = Find<Grid>(window, "PageFooter");
                        var back = Find<Button>(window, "BackButton");
                        var next = Find<Button>(window, "NextButton");
                        var installerLanguagePanel = Find<UniformGrid>(window, "InstallerLanguage");
                        var chinese = Find<RadioButton>(window, "ChineseLanguage");
                        var english = Find<RadioButton>(window, "EnglishLanguage");
                        var sidebarBackHome = Find<Button>(window, "BackHomeButton");
                        var activePage = Find<StackPanel>(window, mode == "imported" ? "SourcePage" : "ContentPage");
                        var wirelessPanel = Find<StackPanel>(window, "ComponentOptionsHost").Children.OfType<InstallerOptionsPanel>().Single();
                        Check(wirelessPanel.RepairsExpander.Visibility == (mode is "custom" or "vanilla" ? Visibility.Visible : Visibility.Collapsed),
                            $"{mode}-wireless-group-matches-generic-route-{language}-{FormatSize(size)}");
                        var pageName = mode == "imported" ? "source" : "content";

                        layouts.Add(new
                        {
                            screen = mode,
                            language,
                            page = pageName,
                            requestedWindowDip = ToSize(size),
                            clientAreaDip = new { widthDip = root.ActualWidth, heightDip = root.ActualHeight },
                            footer = new { backVisible = back.IsVisible, nextVisible = next.IsVisible, bounds = BoundsDescription(footer, root) },
                            languageControls = new { chineseVisible = chinese.IsVisible, englishVisible = english.IsVisible, bounds = BoundsDescription(installerLanguagePanel, root) },
                            sidebarBackHomeVisible = sidebarBackHome.IsVisible,
                            pageScroll = new { pageScroll.ViewportWidth, pageScroll.ViewportHeight, pageScroll.ExtentWidth, pageScroll.ExtentHeight, pageScroll.ScrollableHeight }
                        });

                        Check(window.CurrentOptions.InstallMode == mode,
                            $"{mode}-mode-propagated-{language}-{FormatSize(size)}");
                        Check(activePage.IsVisible, $"{mode}-active-page-visible-{language}-{FormatSize(size)}", pageName);
                        Check(footer.IsVisible && next.IsVisible && (mode == "imported" ? !back.IsVisible : back.IsVisible),
                            $"{mode}-footer-visible-{language}-{FormatSize(size)}");
                        Check(installerLanguagePanel.IsVisible && chinese.IsVisible && english.IsVisible,
                            $"{mode}-language-controls-visible-{language}-{FormatSize(size)}");
                        if (mode == "imported") Check(sidebarBackHome.IsVisible && sidebarBackHome.IsEnabled,
                            $"{mode}-sidebar-back-home-visible-{language}-{FormatSize(size)}");

                        var inBounds = mode == "imported"
                            ? new FrameworkElement[] { footer, next, sidebarBackHome, installerLanguagePanel, chinese, english }
                                .Select((element, index) => (element, new[] { "footer", "next", "back-home", "language-panel", "language-zh", "language-en" }[index]))
                            : new FrameworkElement[] { footer, next, back, installerLanguagePanel, chinese, english }
                                .Select((element, index) => (element, new[] { "footer", "next", "back", "language-panel", "language-zh", "language-en" }[index]));
                        foreach (var (element, name) in inBounds)
                        {
                            Check(IsWithinRoot(element, root),
                                $"{mode}-{name}-inside-client-area-{language}-{FormatSize(size)}",
                                BoundsDescription(element, root));
                        }
                        var hitTargets = mode == "imported"
                            ? new FrameworkElement[] { next, sidebarBackHome, chinese, english }
                                .Select((element, index) => (element, new[] { "next", "back-home", "language-zh", "language-en" }[index]))
                            : new FrameworkElement[] { next, back, chinese, english }
                                .Select((element, index) => (element, new[] { "next", "back", "language-zh", "language-en" }[index]));
                        foreach (var (element, name) in hitTargets)
                        {
                            Check(element.IsVisible && element.ActualWidth >= 48 && element.ActualHeight >= 48,
                                $"{mode}-{name}-target-at-least-48dip-{language}-{FormatSize(size)}",
                                new { width = element.ActualWidth, height = element.ActualHeight });
                        }

                        Check(pageScroll.HorizontalScrollBarVisibility == ScrollBarVisibility.Disabled
                            && pageScroll.ExtentWidth <= pageScroll.ViewportWidth + 1,
                            $"{mode}-no-horizontal-overflow-{language}-{FormatSize(size)}",
                            new { extentWidth = pageScroll.ExtentWidth, viewportWidth = pageScroll.ViewportWidth });
                        if (mode is "custom" or "vanilla")
                            Check(pageScroll.ScrollableHeight > 1,
                                $"{mode}-dynamic-content-scrollable-{language}-{FormatSize(size)}",
                                new { scrollableHeight = pageScroll.ScrollableHeight, viewportHeight = pageScroll.ViewportHeight });

                        var focusTargets = mode == "imported"
                            ? new List<FrameworkElement> { sidebarBackHome, chinese, english }
                            : new List<FrameworkElement> { next, back, chinese, english };
                        if (mode == "imported") focusTargets.AddRange(FindSourceFilePickerButtons(activePage));
                        if (mode == "imported" && next.IsEnabled) focusTargets.Add(next);
                        var navigationScroll = FindSidebarNavigationScroll(window);
                        navigationScroll.ScrollToTop();
                        await FlushLayoutAsync(window);
                        Check(navigationScroll.ViewportHeight >= 48,
                            $"{mode}-sidebar-nav-shows-one-full-tab-{language}-{FormatSize(size)}",
                            new { viewportHeight = navigationScroll.ViewportHeight, extentHeight = navigationScroll.ExtentHeight });
                        var focusResult = await TraverseFocusAsync(Find<Button>(window, "TargetTab"), shell, root,
                            focusTargets, requireCycle: false, maxSteps: 256);
                        focusRuns.Add(new { screen = mode, language, windowDip = ToSize(size), focus = focusResult });
                        Check(focusResult.Passed,
                            $"{mode}-wizard-tab-focus-visible-and-reaches-footer-language-{language}-{FormatSize(size)}", focusResult);

                        if (ShouldCapture(mode, language, size))
                        {
                            Keyboard.Focus(next);
                            await CaptureForReviewAsync(root, directory, screenshots, screenshotFailures,
                                $"{mode}-{LanguageCode(language)}-{FormatSize(size)}.png", Check);
                        }
                    }
                }
            }

            if (workSnapshotBeforeNavigation != null)
            {
                var workSnapshotAfterNavigation = CaptureWorkSnapshot(directory);
                var changedEntries = workSnapshotBeforeNavigation.Keys.Union(workSnapshotAfterNavigation.Keys)
                    .Where(path => !workSnapshotBeforeNavigation.TryGetValue(path, out var before)
                        || !workSnapshotAfterNavigation.TryGetValue(path, out var after)
                        || !string.Equals(before, after, StringComparison.Ordinal))
                            .OrderBy(path => path, StringComparer.OrdinalIgnoreCase)
                    .ToArray();
                Check(changedEntries.Length == 0,
                    "home-and-source-navigation-performs-no-workspace-disk-writes",
                    new { changedEntries = changedEntries.Take(20).ToArray(), totalChangedEntries = changedEntries.Length });
                Check(window.CompletedSession == null && InstallationController.Latest()?.Id == latestSessionBeforeNavigation,
                    "home-and-source-navigation-does-not-create-or-change-an-installation-session");
            }
        }
        catch (Exception error)
        {
            failures.Add("run-failed: " + error.GetBaseException().Message);
            checks.Add(new { name = "run-failed", passed = false, details = error.ToString() });
        }
        finally
        {
            try { window?.Close(); } catch { }
            InstallerI18n.SetLanguage(originalLanguage);
            RenderOptions.ProcessRenderMode = originalRenderMode;
            if (application != null && originalShutdownMode.HasValue)
                application.ShutdownMode = originalShutdownMode.Value;
        }

        var passed = failures.Count == 0;
        var result = new
        {
            passed,
            scope = "WPF controls, measured layout bounds, and WPF MoveFocus traversal only. Window sizes and control bounds are DIPs; client-area dimensions are reported. No OS DPI scaling, physical touch, hardware keyboard injection, installation, partition deletion, formatting, or device acceptance is claimed.",
            focusGuidance = "Focus must be reached through MoveFocus and remain inside the visible active screen and its unclipped viewport. This adapts the Focus Not Obscured outcome to native WPF geometry; it is not a web-conformance claim.",
            languages = new[] { "zh-CN", "en-US" },
            modes = new[] { "home", "custom", "local", "vanilla", "imported" },
            requestedWindowSizesDip = WindowSizes.Select(ToSize).ToArray(),
            layouts,
            focusRuns,
            screenshots,
            screenshotEvidenceAvailable = screenshotFailures.Count == 0,
            screenshotReview = "Selected WPF captures are retained only after visible pixels are verified. Screenshot renderer failures are reported as unavailable evidence and do not change geometry, focus, or navigation check results. Screenshots do not establish hardware touch behavior.",
            screenshotFailures,
            checks,
            failures,
            partitionPickerAutomaticReadDisabled = true,
            physicalOrDiskActions = false,
            resultFile = Path.Combine(directory, "result.json"),
            artifactDirectory = directory
        };
        var json = JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true });
        var resultFile = Path.Combine(directory, "result.json");
        await File.WriteAllTextAsync(resultFile, json);
        if (!passed)
            throw new InvalidOperationException($"Boiler UI checks failed ({failures.Count}); report: {resultFile}; failed checks: {string.Join(", ", failures)}");
        return json;
    }

    private static async Task FlushLayoutAsync(Window window)
    {
        await window.Dispatcher.InvokeAsync(() =>
        {
            window.InvalidateMeasure();
            window.UpdateLayout();
        }, DispatcherPriority.Render);
        await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
    }

    private static void SetLanguage(MainWindow window, string language)
    {
        var target = (RadioButton)window.FindName(language == "en-US" ? "EnglishLanguage" : "ChineseLanguage");
        target.IsChecked = true;
        if (InstallerI18n.Language != language) InstallerI18n.SetLanguage(language);
    }

    private static void SetWindowSize(Window window, Size size)
    {
        window.WindowState = WindowState.Normal;
        window.Width = size.Width;
        window.Height = size.Height;
    }

    private static Border? GetHomeScreen(MainWindow window) =>
        typeof(MainWindow).GetField("homeScreen", BindingFlags.Instance | BindingFlags.NonPublic)?.GetValue(window) as Border;

    private static Button? FindHomeModeButton(Border home, string mode) =>
        FindDescendant<UniformGrid>(home, "HomeChoices")?.Children.OfType<Button>()
            .FirstOrDefault(button => button.Tag is string tag && tag == mode);

    private static Button? FindHomeLanguageButton(Border home, string language) =>
        FindDescendant<WrapPanel>(home, "HomeLanguages")?.Children.OfType<Button>()
            .FirstOrDefault(button => button.Tag is string tag && tag == language
                || language == "en-US" && (button.Name.Contains("English", StringComparison.OrdinalIgnoreCase)
                    || ButtonText(button).Contains("English", StringComparison.OrdinalIgnoreCase))
                || language == "zh-CN" && (button.Name.Contains("Chinese", StringComparison.OrdinalIgnoreCase)
                    || ButtonText(button).Contains("中文", StringComparison.Ordinal)));

    private static Button? FindHomeExitButton(Border home) => Descendants(home).OfType<Button>()
        .FirstOrDefault(button => button.Name.Contains("exit", StringComparison.OrdinalIgnoreCase)
            || button.Tag is string tag && tag.Equals("exit", StringComparison.OrdinalIgnoreCase)
            || ButtonText(button).Contains("退出", StringComparison.Ordinal)
            || ButtonText(button).Contains("Exit", StringComparison.OrdinalIgnoreCase)
            || ButtonText(button).Contains("Quit", StringComparison.OrdinalIgnoreCase));

    private static Button[] FindSourceFilePickerButtons(DependencyObject source) => Descendants(source)
        .OfType<Button>()
        .Where(button => button.IsVisible && button.IsEnabled && button.Focusable
            && KeyboardNavigation.GetIsTabStop(button)
            && (button.Name.Contains("browse", StringComparison.OrdinalIgnoreCase)
                || ButtonText(button).Contains("选择", StringComparison.Ordinal)
                || ButtonText(button).Contains("浏览", StringComparison.Ordinal)
                || ButtonText(button).Contains("Browse", StringComparison.OrdinalIgnoreCase)
                || ButtonText(button).Contains("Choose file", StringComparison.OrdinalIgnoreCase)))
        .ToArray();

    private static ScrollViewer FindSidebarNavigationScroll(MainWindow window) =>
        Descendants(Find<Grid>(window, "SidebarBody")).OfType<ScrollViewer>().First();

    private static bool IsWithinScrollViewport(FrameworkElement element, ScrollViewer scroll)
    {
        if (!element.IsVisible || element.ActualWidth <= 0 || element.ActualHeight <= 0
            || scroll.ViewportWidth <= 0 || scroll.ViewportHeight <= 0) return false;
        var bounds = BoundsRelativeTo(element, scroll);
        return ContainsWithTolerance(new Rect(0, 0, scroll.ViewportWidth, scroll.ViewportHeight), bounds);
    }

    private static string ButtonText(Button button) => string.Join(" ", Descendants(button)
        .OfType<TextBlock>()
        .Select(text => text.Text)
        .Append(button.Content as string ?? "")
        .Append(AutomationProperties.GetName(button))
        .Where(text => !string.IsNullOrWhiteSpace(text)));

    private static void RaiseClick(Button button) =>
        button.RaiseEvent(new RoutedEventArgs(ButtonBase.ClickEvent, button));

    private static void CheckSingleImageControls(
        MainWindow window,
        FrameworkElement source,
        string language,
        string size,
        Action<bool, string, object?> check)
    {
        var recoverySource = Find<StackPanel>(window, "RecoverySourceSection");
        var imageCard = Find<SystemSourceCard>(window, "RecoverySourceCard");
        var officialSource = Find<StackPanel>(window, "OfficialSourceSection");
        var pickers = FindSourceFilePickerButtons(recoverySource);
        check(source.IsVisible && recoverySource.IsVisible && imageCard.IsVisible && imageCard.BrowseButton.IsVisible
            && !imageCard.DownloadButton.IsVisible && !officialSource.IsVisible && pickers.Length > 0
            && !Find<Border>(window, "RecoveryDownloadCard").IsVisible,
            $"img-mode-requires-one-local-image-{language}-{size}", new { pickers = pickers.Length });
        check(Find<CheckBox>(window, "Upgrade310").IsChecked == false
            && !Find<StackPanel>(window, "SourceVersionOptions").IsVisible
            && !Find<Button>(window, "ContentTab").IsVisible
            && window.CurrentOptions.Repairs.Length + window.CurrentOptions.Plugins.Length == 0,
            $"img-mode-preserves-source-without-upgrade-or-components-{language}-{size}", null);
        var hint = Find<TextBlock>(window, "RecoveryImageHint").Text;
        check(!hint.Contains("64") && hint.Contains(language == "en-US" ? "different sizes" : "不同容量"),
            $"img-mode-does-not-imply-fixed-image-capacity-{language}-{size}", new { hint });
    }

    private static bool IsFullWindow(FrameworkElement element, FrameworkElement root)
    {
        try
        {
            var bounds = BoundsRelativeTo(element, root);
            return ContainsWithTolerance(new Rect(0, 0, root.ActualWidth, root.ActualHeight), bounds)
                && Math.Abs(bounds.Left) <= 1
                && Math.Abs(bounds.Top) <= 1
                && Math.Abs(bounds.Width - root.ActualWidth) <= 1
                && Math.Abs(bounds.Height - root.ActualHeight) <= 1;
        }
        catch (InvalidOperationException) { return false; }
    }

    private static bool SameBounds(Rect first, Rect second, double tolerance = 1) =>
        Math.Abs(first.X - second.X) <= tolerance
        && Math.Abs(first.Y - second.Y) <= tolerance
        && Math.Abs(first.Width - second.Width) <= tolerance
        && Math.Abs(first.Height - second.Height) <= tolerance;

    private static string RectDescription(Rect bounds) =>
        $"boundsDip=[{bounds.X:0.##},{bounds.Y:0.##},{bounds.Width:0.##},{bounds.Height:0.##}]";

    private static IEnumerable<DependencyObject> Descendants(DependencyObject root)
    {
        var pending = new Stack<DependencyObject>();
        var seen = new HashSet<DependencyObject>();
        pending.Push(root);
        while (pending.Count > 0)
        {
            var current = pending.Pop();
            if (!seen.Add(current)) continue;
            yield return current;
            if (current is not Visual and not System.Windows.Media.Media3D.Visual3D) continue;
            for (var index = VisualTreeHelper.GetChildrenCount(current) - 1; index >= 0; index--)
                pending.Push(VisualTreeHelper.GetChild(current, index));
        }
    }

    private static T? FindDescendant<T>(DependencyObject root, string name) where T : FrameworkElement =>
        Descendants(root).OfType<T>().FirstOrDefault(element => element.Name == name);

    private static Dictionary<string, string> CaptureWorkSnapshot(string excludedDirectory)
    {
        var root = Path.GetFullPath(InstallationController.Work);
        var excludedRoot = Path.GetFullPath(excludedDirectory).TrimEnd(Path.DirectorySeparatorChar);
        var excludedPrefix = excludedRoot + Path.DirectorySeparatorChar;
        var snapshot = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        if (!Directory.Exists(root)) return snapshot;

        var pending = new Stack<string>();
        pending.Push(root);
        while (pending.Count > 0)
        {
            var directory = pending.Pop();
            foreach (var path in Directory.EnumerateFileSystemEntries(directory))
            {
                var fullPath = Path.GetFullPath(path);
                if (string.Equals(fullPath, excludedRoot, StringComparison.OrdinalIgnoreCase)
                    || fullPath.StartsWith(excludedPrefix, StringComparison.OrdinalIgnoreCase)) continue;
                var relative = Path.GetRelativePath(root, fullPath);
                if (IsKnownDependencyCacheEntry(relative)) continue;
                var attributes = File.GetAttributes(fullPath);
                if ((attributes & FileAttributes.Directory) != 0)
                {
                    var info = new DirectoryInfo(fullPath);
                    snapshot[relative] = $"dir:{info.LastWriteTimeUtc.Ticks}";
                    if ((attributes & FileAttributes.ReparsePoint) == 0) pending.Push(fullPath);
                }
                else
                {
                    var info = new FileInfo(fullPath);
                    snapshot[relative] = $"file:{info.Length}:{info.LastWriteTimeUtc.Ticks}";
                }
            }
        }
        return snapshot;
    }

    private static bool IsKnownDependencyCacheEntry(string relativePath)
    {
        var segments = relativePath.Split(
            [Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar],
            StringSplitOptions.RemoveEmptyEntries);
        return segments.Length >= 2
            && segments[0].StartsWith("ui-implementation-", StringComparison.OrdinalIgnoreCase)
            && string.Equals(segments[1], "dependency-cache", StringComparison.OrdinalIgnoreCase);
    }

    private static T Find<T>(MainWindow window, string name) where T : FrameworkElement =>
        window.FindName(name) as T ?? throw new InvalidOperationException($"WPF element {name} was not found.");

    private static bool ShouldCapture(string screen, string language, Size size) =>
        (screen == "home" && language == "en-US" && size == WindowSizes[0])
        || (screen == "home" && language == "zh-CN" && size == WindowSizes[0])
        || (screen == "home" && language == "zh-CN" && size == WindowSizes[2])
        || (screen == "img-source" && language == "en-US" && size == WindowSizes[0])
        || (screen == "img-source" && language == "en-US" && size == WindowSizes[2])
        || (screen == "img-source" && language == "zh-CN" && size == WindowSizes[0])
        || (screen == "custom" && language == "en-US" && size == WindowSizes[2])
        || (screen == "vanilla" && language == "zh-CN" && size == WindowSizes[1])
        || (screen == "imported" && language == "en-US" && size == WindowSizes[0]);

    private static async Task AddCaptureAsync(FrameworkElement root, string directory, List<string> screenshots, string name)
    {
        var path = Path.Combine(directory, name);
        await CaptureAsync(root, path);
        screenshots.Add(path);
    }

    private static async Task CaptureForReviewAsync(
        FrameworkElement root,
        string directory,
        List<string> screenshots,
        List<object> screenshotFailures,
        string name,
        Action<bool, string, object?> check)
    {
        var checkName = $"screenshot-{Path.GetFileNameWithoutExtension(name)}-contains-rendered-content";
        try
        {
            await AddCaptureAsync(root, directory, screenshots, name);
            check(true, checkName, new { path = Path.Combine(directory, name), visibleContentVerified = true });
        }
        catch (Exception error)
        {
            screenshotFailures.Add(new { name, error = error.ToString() });
        }
    }

    internal static async Task CaptureAsync(FrameworkElement target, string path)
    {
        var frameRendered = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        EventHandler renderingHandler = (_, _) => frameRendered.TrySetResult(true);
        CompositionTarget.Rendering += renderingHandler;
        try
        {
            await target.Dispatcher.InvokeAsync(() =>
            {
                target.InvalidateVisual();
                target.UpdateLayout();
            }, DispatcherPriority.Render);
            try { await frameRendered.Task.WaitAsync(TimeSpan.FromSeconds(2)); }
            catch (TimeoutException) { }
            await target.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
        }
        finally { CompositionTarget.Rendering -= renderingHandler; }
        await target.Dispatcher.InvokeAsync(() => CaptureOnDispatcher(target, path), DispatcherPriority.Render);
    }

    private static void CaptureOnDispatcher(FrameworkElement target, string path)
    {
        var width = Math.Max(1, (int)Math.Ceiling(target.ActualWidth));
        var height = Math.Max(1, (int)Math.Ceiling(target.ActualHeight));
        BitmapSource bitmap;
        var direct = new RenderTargetBitmap(width, height, 96, 96, PixelFormats.Pbgra32);
        direct.Render(target);
        if (HasVisiblePixels(direct))
        {
            bitmap = direct;
        }
        else
        {
            var drawing = new DrawingVisual();
            var brush = new VisualBrush(target)
            {
                Stretch = Stretch.Fill,
                AlignmentX = AlignmentX.Left,
                AlignmentY = AlignmentY.Top,
                ViewboxUnits = BrushMappingMode.Absolute,
                Viewbox = new Rect(0, 0, width, height),
                ViewportUnits = BrushMappingMode.Absolute,
                Viewport = new Rect(0, 0, width, height)
            };
            using (var context = drawing.RenderOpen())
                context.DrawRectangle(brush, null, new Rect(0, 0, width, height));

            var visualBrushFallback = new RenderTargetBitmap(width, height, 96, 96, PixelFormats.Pbgra32);
            visualBrushFallback.Render(drawing);
            if (!HasVisiblePixels(visualBrushFallback))
                throw new IOException($"WPF bitmap rendering produced no visible pixels, including a solid DrawingVisual probe: {Path.GetFileName(path)}; directVisiblePixels={CountVisiblePixels(direct)}; visualBrushVisiblePixels={CountVisiblePixels(visualBrushFallback)}; solidProbeVisiblePixels={DrawSolidVisualProbeVisiblePixels()}; bounds={width}x{height}; targetVisible={target.IsVisible}; dispatcherAccess={target.Dispatcher.CheckAccess()}; presentationSource={PresentationSource.FromVisual(target) != null}");
            else bitmap = visualBrushFallback;
        }

        var encoder = new PngBitmapEncoder();
        encoder.Frames.Add(BitmapFrame.Create(bitmap));
        using var file = File.Create(path);
        encoder.Save(file);
    }

    private static bool HasVisiblePixels(BitmapSource bitmap) =>
        CountVisiblePixels(bitmap) >= Math.Max(16, Math.Min(1024, bitmap.PixelWidth * bitmap.PixelHeight / 1000));

    private static int CountVisiblePixels(BitmapSource bitmap)
    {
        var width = bitmap.PixelWidth;
        var height = bitmap.PixelHeight;
        if (width <= 0 || height <= 0) return 0;
        var pixels = new byte[width * height * 4];
        bitmap.CopyPixels(pixels, width * 4, 0);
        var visiblePixels = 0;
        for (var index = 3; index < pixels.Length; index += 4)
            if (pixels[index] > 0) visiblePixels++;
        return visiblePixels;
    }

    private static int DrawSolidVisualProbeVisiblePixels()
    {
        var drawing = new DrawingVisual();
        using (var context = drawing.RenderOpen())
            context.DrawRectangle(Brushes.Magenta, null, new Rect(0, 0, 32, 32));
        var bitmap = new RenderTargetBitmap(32, 32, 96, 96, PixelFormats.Pbgra32);
        bitmap.Render(drawing);
        return CountVisiblePixels(bitmap);
    }

    private static async Task<FocusTraversalResult> TraverseFocusAsync(
        FrameworkElement start,
        FrameworkElement activeScreen,
        FrameworkElement root,
        IReadOnlyCollection<FrameworkElement> requiredTargets,
        bool requireCycle,
        int maxSteps)
    {
        var order = new List<string>();
        if (Keyboard.Focus(start) is null)
            return new(false, order.ToArray(), "WPF refused to focus the starting control.", 0);

        await FlushAfterFocusAsync(root);

        var startObject = Keyboard.FocusedElement as DependencyObject;
        if (startObject == null)
            return new(false, order.ToArray(), "Keyboard.FocusedElement was empty after focus was set.", 0);

        var required = new HashSet<DependencyObject>(requiredTargets);
        var reached = new HashSet<DependencyObject>();
        var seen = new HashSet<DependencyObject>();
        for (var step = 0; step < maxSteps; step++)
        {
            var current = Keyboard.FocusedElement as DependencyObject;
            if (current == null) return new(false, order.ToArray(), "Keyboard focus became empty during traversal.", step);
            if (!IsDescendantOrSelf(current, activeScreen))
                return new(false, order.ToArray(), "Tab moved focus outside the active screen; the new control is visually covered or belongs to a hidden screen.", step);
            if (!IsVisibleAndUnclipped(current, activeScreen, root, out var visibilityError))
                return new(false, order.ToArray(), visibilityError, step);

            order.Add(FocusName(current));
            if (required.Contains(current)) reached.Add(current);
            if (!requireCycle && reached.Count == required.Count)
                return new(true, order.ToArray(), null, step + 1);
            if (!seen.Add(current) && !ReferenceEquals(current, startObject))
                return new(false, order.ToArray(), "Tab traversal entered a focus cycle before visiting every required control.", step + 1);

            var mover = current as UIElement ?? FindUiElementAncestor(current);
            if (mover == null) return new(false, order.ToArray(), "Focused control has no UIElement to receive MoveFocus.", step + 1);
            if (!mover.MoveFocus(new TraversalRequest(FocusNavigationDirection.Next)))
            {
                var missing = required.Count - reached.Count;
                return new(missing == 0, order.ToArray(), missing == 0 ? null : $"MoveFocus stopped with {missing} required controls unreached.", step + 1);
            }

            await FlushAfterFocusAsync(root);
            var next = Keyboard.FocusedElement as DependencyObject;
            if (ReferenceEquals(next, startObject))
            {
                var missing = required.Count - reached.Count;
                return new(missing == 0, order.ToArray(), missing == 0 ? null : $"Traversal returned to its start with {missing} required controls unreached.", step + 1);
            }
        }

        return new(false, order.ToArray(), "MoveFocus exceeded its bounded traversal step count.", maxSteps);
    }

    private static async Task FlushAfterFocusAsync(FrameworkElement root)
    {
        await root.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.Background);
        await root.Dispatcher.InvokeAsync(root.UpdateLayout, DispatcherPriority.Render);
        await root.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle);
    }

    private static UIElement? FindUiElementAncestor(DependencyObject element)
    {
        for (var parent = ParentOf(element); parent != null; parent = ParentOf(parent))
            if (parent is UIElement uiElement) return uiElement;
        return null;
    }

    private static bool IsVisibleAndUnclipped(DependencyObject element, FrameworkElement activeScreen, FrameworkElement root, out string? error)
    {
        error = null;
        var frameworkElement = element as FrameworkElement ?? FindFrameworkElementAncestor(element);
        if (frameworkElement == null || !frameworkElement.IsVisible || frameworkElement.ActualWidth <= 0 || frameworkElement.ActualHeight <= 0)
        {
            error = "A focused element is not laid out and visible.";
            return false;
        }

        var scroll = FindNearestScrollViewer(element);
        var bounds = BoundsRelativeTo(frameworkElement, scroll ?? root);
        var viewport = scroll is null
            ? new Rect(0, 0, root.ActualWidth, root.ActualHeight)
            : new Rect(0, 0, scroll.ViewportWidth, scroll.ViewportHeight);
        if (bounds.IsEmpty || !ContainsWithTolerance(viewport, bounds))
        {
            var viewportOwner = scroll == null ? "window client" : $"ScrollViewer {scroll.Name}";
            var scrollDetails = scroll == null ? null : new
            {
                viewportWidth = scroll.ViewportWidth,
                viewportHeight = scroll.ViewportHeight,
                extentWidth = scroll.ExtentWidth,
                extentHeight = scroll.ExtentHeight,
                scrollOffset = scroll.VerticalOffset
            };
            error = $"Focused control {FocusName(element)} is outside {viewportOwner}: {RectDescription(bounds)}; viewport={RectDescription(viewport)}; scroll={JsonSerializer.Serialize(scrollDetails)}.";
            return false;
        }
        if (!IsDescendantOrSelf(frameworkElement, activeScreen))
        {
            error = "The focused control is not inside the active screen.";
            return false;
        }
        return true;
    }

    private static bool NoHorizontalOverflow(FrameworkElement element, FrameworkElement root)
    {
        var bounds = BoundsRelativeTo(element, root);
        return !bounds.IsEmpty && bounds.Left >= -1 && bounds.Right <= root.ActualWidth + 1;
    }

    private static bool IsWithinRoot(FrameworkElement element, FrameworkElement root)
    {
        if (!element.IsVisible || element.ActualWidth <= 0 || element.ActualHeight <= 0) return false;
        var bounds = BoundsRelativeTo(element, root);
        return !bounds.IsEmpty && ContainsWithTolerance(new Rect(0, 0, root.ActualWidth, root.ActualHeight), bounds);
    }

    private static string BoundsDescription(FrameworkElement element, FrameworkElement root)
    {
        try
        {
            var bounds = BoundsRelativeTo(element, root);
            return $"boundsDip=[{bounds.X:0.##},{bounds.Y:0.##},{bounds.Width:0.##},{bounds.Height:0.##}], clientDip=[{root.ActualWidth:0.##},{root.ActualHeight:0.##}]";
        }
        catch (InvalidOperationException error) { return error.Message; }
    }

    private static Rect BoundsRelativeTo(FrameworkElement element, Visual ancestor) =>
        element.TransformToAncestor(ancestor).TransformBounds(new Rect(0, 0, element.ActualWidth, element.ActualHeight));

    private static bool ContainsWithTolerance(Rect outer, Rect inner, double tolerance = 1) =>
        !outer.IsEmpty && !inner.IsEmpty
        && inner.Left >= outer.Left - tolerance
        && inner.Top >= outer.Top - tolerance
        && inner.Right <= outer.Right + tolerance
        && inner.Bottom <= outer.Bottom + tolerance;

    private static ScrollViewer? FindNearestScrollViewer(DependencyObject element)
    {
        for (var current = element; current != null; current = ParentOf(current))
            if (current is ScrollViewer scrollViewer) return scrollViewer;
        return null;
    }

    private static FrameworkElement? FindFrameworkElementAncestor(DependencyObject element)
    {
        for (var current = element; current != null; current = ParentOf(current))
            if (current is FrameworkElement frameworkElement) return frameworkElement;
        return null;
    }

    private static bool IsDescendantOrSelf(DependencyObject element, DependencyObject ancestor)
    {
        for (var current = element; current != null; current = ParentOf(current))
            if (ReferenceEquals(current, ancestor)) return true;
        return false;
    }

    private static DependencyObject? ParentOf(DependencyObject element)
    {
        if (element is Visual or System.Windows.Media.Media3D.Visual3D)
        {
            var visualParent = VisualTreeHelper.GetParent(element);
            if (visualParent != null) return visualParent;
        }
        return LogicalTreeHelper.GetParent(element);
    }

    private static string FocusName(DependencyObject element)
    {
        if (element is FrameworkElement frameworkElement && !string.IsNullOrWhiteSpace(frameworkElement.Name))
            return frameworkElement.Name;
        var automationName = AutomationProperties.GetName(element);
        return !string.IsNullOrWhiteSpace(automationName) ? automationName : element.GetType().Name;
    }

    private static object ToSize(Size size) => new { widthDip = size.Width, heightDip = size.Height };
    private static string FormatSize(Size size) => $"{(int)size.Width}x{(int)size.Height}";
    private static string LanguageCode(string language) => language == "en-US" ? "en" : "zh";
}
