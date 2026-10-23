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
        var originalLanguage = InstallerI18n.Language;
        var originalRenderMode = RenderOptions.ProcessRenderMode;
        var application = Application.Current;
        var originalShutdownMode = application?.ShutdownMode;
        MainWindow? window = null;
        if (application != null) application.ShutdownMode = ShutdownMode.OnExplicitShutdown;

        void Check(bool passed, string name, object? details = null)
        {
            checks.Add(new { name, passed, details });
            if (!passed) failures.Add(name);
        }

        try
        {
            System.Windows.Media.RenderOptions.ProcessRenderMode = System.Windows.Interop.RenderMode.SoftwareOnly;
            window = new MainWindow(rehearsal: true)
            {
                WindowState = WindowState.Normal,
                ShowActivated = false,
                ShowInTaskbar = false
            };
            if (window.FindName("PartitionSelector") is PartitionPicker partitionPicker)
                partitionPicker.AutomaticReadEnabled = false;
            else
                Check(false, "partition-picker-found-and-auto-read-disabled");

            window.Width = WindowSizes[0].Width;
            window.Height = WindowSizes[0].Height;
            window.Show();
            await FlushLayoutAsync(window);

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

                    var homeScroll = home.Child as ScrollViewer;
                    var panel = homeScroll?.Content as StackPanel;
                    var modeButtons = panel?.Children.OfType<Button>()
                        .Where(button => button.Tag is string tag && tag is "custom" or "vanilla" or "imported")
                        .ToDictionary(button => (string)button.Tag, StringComparer.Ordinal);
                    var languageButtons = panel?.Children.OfType<WrapPanel>().SingleOrDefault()?.Children.OfType<Button>().ToArray();
                    Check(home.IsVisible && homeScroll != null && panel != null,
                        $"home-visible-{language}-{FormatSize(size)}");
                    Check(modeButtons is { Count: 3 } && new[] { "custom", "vanilla", "imported" }.All(modeButtons.ContainsKey),
                        $"home-has-three-mode-choices-{language}-{FormatSize(size)}");
                    Check(languageButtons is { Length: 2 }, $"home-has-two-language-buttons-{language}-{FormatSize(size)}");

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
                    if (homeScroll != null && modeButtons != null && languageButtons != null)
                    {
                        foreach (var (mode, button) in modeButtons)
                        {
                            Check(button.MinHeight >= 48 && button.ActualWidth >= 48 && button.ActualHeight >= 48,
                                $"home-{mode}-target-at-least-48dip-{language}-{FormatSize(size)}",
                                new { minWidth = button.MinWidth, minHeight = button.MinHeight, width = button.ActualWidth, height = button.ActualHeight });
                            Check(NoHorizontalOverflow(button, root),
                                $"home-{mode}-no-horizontal-clipping-{language}-{FormatSize(size)}");
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

                        var focus = await TraverseFocusAsync(modeButtons["custom"], home, root,
                            modeButtons.Values.Cast<FrameworkElement>().Concat(languageButtons).ToArray(), requireCycle: true, maxSteps: 32);
                        focusRuns.Add(new { screen = "home", language, windowDip = ToSize(size), focus });
                        Check(focus.Passed, $"home-tab-focus-visible-and-reaches-controls-{language}-{FormatSize(size)}", focus);

                        homeScroll.ScrollToTop();
                        await FlushLayoutAsync(window);
                        if (ShouldCapture("home", language, size))
                        {
                            Keyboard.Focus(modeButtons["custom"]);
                            AddCapture(root, directory, screenshots, $"home-{LanguageCode(language)}-{FormatSize(size)}.png");
                        }
                    }

                    foreach (var mode in new[] { "custom", "vanilla", "imported" })
                    {
                        window.BoilerModeForChecks(mode);
                        if (mode != "imported") window.PreviewStep(2);
                        else window.PreviewStep(1);
                        await FlushLayoutAsync(window);

                        var shell = Find<Grid>(window, "WizardShell");
                        var pageScroll = Find<ScrollViewer>(window, "PageScroll");
                        var footer = Find<Grid>(window, "PageFooter");
                        var back = Find<Button>(window, "BackButton");
                        var next = Find<Button>(window, "NextButton");
                        var languagePanel = Find<UniformGrid>(window, "InstallerLanguage");
                        var chinese = Find<RadioButton>(window, "ChineseLanguage");
                        var english = Find<RadioButton>(window, "EnglishLanguage");
                        var activePage = Find<StackPanel>(window, mode == "imported" ? "SourcePage" : "ContentPage");
                        var pageName = mode == "imported" ? "source" : "content";

                        layouts.Add(new
                        {
                            screen = mode,
                            language,
                            page = pageName,
                            requestedWindowDip = ToSize(size),
                            clientAreaDip = new { widthDip = root.ActualWidth, heightDip = root.ActualHeight },
                            footer = new { backVisible = back.IsVisible, nextVisible = next.IsVisible, bounds = BoundsDescription(footer, root) },
                            languageControls = new { chineseVisible = chinese.IsVisible, englishVisible = english.IsVisible, bounds = BoundsDescription(languagePanel, root) },
                            pageScroll = new { pageScroll.ViewportWidth, pageScroll.ViewportHeight, pageScroll.ExtentWidth, pageScroll.ExtentHeight, pageScroll.ScrollableHeight }
                        });

                        Check(window.CurrentOptions.InstallMode == mode,
                            $"{mode}-mode-propagated-{language}-{FormatSize(size)}");
                        Check(activePage.IsVisible, $"{mode}-active-page-visible-{language}-{FormatSize(size)}", pageName);
                        Check(footer.IsVisible && next.IsVisible && back.IsVisible,
                            $"{mode}-footer-visible-{language}-{FormatSize(size)}");
                        Check(languagePanel.IsVisible && chinese.IsVisible && english.IsVisible,
                            $"{mode}-language-controls-visible-{language}-{FormatSize(size)}");

                        foreach (var (element, name) in new FrameworkElement[] { footer, next, back, languagePanel, chinese, english }
                            .Select((element, index) => (element, new[] { "footer", "next", "back", "language-panel", "language-zh", "language-en" }[index])))
                        {
                            Check(IsWithinRoot(element, root),
                                $"{mode}-{name}-inside-client-area-{language}-{FormatSize(size)}",
                                BoundsDescription(element, root));
                        }
                        foreach (var (element, name) in new FrameworkElement[] { next, back, chinese, english }
                            .Select((element, index) => (element, new[] { "next", "back", "language-zh", "language-en" }[index])))
                        {
                            Check(element.ActualWidth >= 48 && element.ActualHeight >= 48,
                                $"{mode}-{name}-target-at-least-48dip-{language}-{FormatSize(size)}",
                                new { width = element.ActualWidth, height = element.ActualHeight });
                        }

                        Check(pageScroll.HorizontalScrollBarVisibility == ScrollBarVisibility.Disabled
                            && pageScroll.ExtentWidth <= pageScroll.ViewportWidth + 1,
                            $"{mode}-no-horizontal-overflow-{language}-{FormatSize(size)}",
                            new { extentWidth = pageScroll.ExtentWidth, viewportWidth = pageScroll.ViewportWidth });
                        if (mode != "imported")
                            Check(pageScroll.ScrollableHeight > 1,
                                $"{mode}-dynamic-content-scrollable-{language}-{FormatSize(size)}",
                                new { scrollableHeight = pageScroll.ScrollableHeight, viewportHeight = pageScroll.ViewportHeight });

                        var focusTargets = new FrameworkElement[] { next, back, chinese, english };
                        var focusResult = await TraverseFocusAsync(Find<Button>(window, "TargetTab"), shell, root,
                            focusTargets, requireCycle: false, maxSteps: 256);
                        focusRuns.Add(new { screen = mode, language, windowDip = ToSize(size), focus = focusResult });
                        Check(focusResult.Passed,
                            $"{mode}-wizard-tab-focus-visible-and-reaches-footer-language-{language}-{FormatSize(size)}", focusResult);

                        if (ShouldCapture(mode, language, size))
                        {
                            Keyboard.Focus(next);
                            AddCapture(root, directory, screenshots, $"{mode}-{LanguageCode(language)}-{FormatSize(size)}.png");
                        }
                    }
                }
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
            modes = new[] { "home", "custom", "vanilla", "imported" },
            requestedWindowSizesDip = WindowSizes.Select(ToSize).ToArray(),
            layouts,
            focusRuns,
            screenshots,
            screenshotReview = "Selected WPF VisualBrush captures are retained for human review; screenshots do not establish hardware touch behavior.",
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

    private static T Find<T>(MainWindow window, string name) where T : FrameworkElement =>
        window.FindName(name) as T ?? throw new InvalidOperationException($"WPF element {name} was not found.");

    private static bool ShouldCapture(string screen, string language, Size size) =>
        (screen == "home" && language == "en-US" && size == WindowSizes[0])
        || (screen == "home" && language == "zh-CN" && size == WindowSizes[2])
        || (screen == "custom" && language == "en-US" && size == WindowSizes[2])
        || (screen == "vanilla" && language == "zh-CN" && size == WindowSizes[1])
        || (screen == "imported" && language == "en-US" && size == WindowSizes[0]);

    private static void AddCapture(FrameworkElement root, string directory, List<string> screenshots, string name)
    {
        var path = Path.Combine(directory, name);
        Capture(root, path);
        screenshots.Add(path);
    }

    private static void Capture(FrameworkElement target, string path)
    {
        target.UpdateLayout();
        var width = Math.Max(1, (int)Math.Ceiling(target.ActualWidth));
        var height = Math.Max(1, (int)Math.Ceiling(target.ActualHeight));
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

        var bitmap = new RenderTargetBitmap(width, height, 96, 96, PixelFormats.Pbgra32);
        bitmap.Render(drawing);
        var pixels = new byte[width * height * 4];
        bitmap.CopyPixels(pixels, width * 4, 0);
        if (!pixels.Where((_, index) => index % 4 == 3).Any(alpha => alpha > 0))
            throw new IOException("VisualBrush capture was empty: " + Path.GetFileName(path));

        var encoder = new PngBitmapEncoder();
        encoder.Frames.Add(BitmapFrame.Create(bitmap));
        using var file = File.Create(path);
        encoder.Save(file);
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
            error = "A focused control is outside the visible client or scroll viewport.";
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
