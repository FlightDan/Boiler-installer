using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace OxpInstaller;

public partial class MainWindow
{
    private ScrollViewer? homeScroll;
    private StackPanel? homeContent;
    private UniformGrid? homeChoices;
    private Border? homeHeader;
    private Border? homeFooter;
    private TextBlock? homeTitle;
    private Grid? homeIntro;
    private TextBlock? homeDescription;
    private Button? homeAddFiles;

    private static SolidColorBrush BoilerBrush(string color) =>
        (SolidColorBrush)new BrushConverter().ConvertFromString(color)!;

    private static Image BoilerImageAsset(string name, double size) => new()
    {
        Source = new BitmapImage(new Uri($"pack://application:,,,/Assets/{name}.png")),
        Width = size, Height = size, Stretch = Stretch.Uniform,
        VerticalAlignment = VerticalAlignment.Center,
        IsHitTestVisible = false
    };

    private static Viewbox CreateFileDropWatermark()
    {
        var drawing = new Canvas { Width = 64, Height = 48 };
        // Selected option D: open drop-zone corners, one file and an inward arrow.
        foreach (var data in new[]
        {
            "M8 18v-7a3 3 0 0 1 3-3h8 M45 8h8a3 3 0 0 1 3 3v7 M56 31v6a3 3 0 0 1-3 3h-8 M19 40h-8a3 3 0 0 1-3-3v-6",
            "M25 12h10l6 6v16H25Z M35 12v6h6",
            "M17 25h15m-5-5 5 5-5 5"
        })
        {
            drawing.Children.Add(new System.Windows.Shapes.Path
            {
                Data = Geometry.Parse(data), Stroke = BoilerBrush("#647B96"), StrokeThickness = 2,
                StrokeStartLineCap = PenLineCap.Round, StrokeEndLineCap = PenLineCap.Round,
                StrokeLineJoin = PenLineJoin.Round
            });
        }
        return new Viewbox
        {
            Name = "FileDropWatermark", Width = 44, Height = 32, Child = drawing, Stretch = Stretch.Uniform,
            Opacity = 0.50, Margin = new(0, 0, 10, 0), VerticalAlignment = VerticalAlignment.Center,
            IsHitTestVisible = false, Focusable = false
        };
    }

    private void ShowBoilerHome()
    {
        if (ready && (operation != null || DownloadsBusy || deviceActionBusy || PartitionSelector.IsBusy)) return;
        optionsPanel?.CancelPendingWirelessDetection();
        optionsPanel?.ClearSensitiveInputs();
        WizardShell.Visibility = Visibility.Collapsed;
        if (homeScreen != null) WindowRoot.Children.Remove(homeScreen);

        var layout = new Grid { Name = "HomeLayout" };
        layout.RowDefinitions.Add(new() { Height = GridLength.Auto });
        layout.RowDefinitions.Add(new() { Height = new(1, GridUnitType.Star) });
        layout.RowDefinitions.Add(new() { Height = GridLength.Auto });

        var header = new Grid();
        header.ColumnDefinitions.Add(new() { Width = new(1, GridUnitType.Star) });
        header.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        var brand = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
        brand.Children.Add(new Border
        {
            Width = 44, Height = 44, CornerRadius = new(10), Background = BoilerBrush("#122944"),
            Margin = new(0, 0, 12, 0), Child = BoilerImageAsset("boiler-logo", 36)
        });
        brand.Children.Add(new TextBlock { Text = "Boiler Installer", FontSize = 24, FontWeight = FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center });
        brand.Children.Add(new TextBlock { Name = "HomeVersion", Text = ReleaseInfo.DisplayVersion, FontSize = 12, Foreground = BoilerBrush("#61728A"), Margin = new(20, 0, 12, 0), VerticalAlignment = VerticalAlignment.Center });
        header.Children.Add(brand);

        var controls = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
        var languages = new WrapPanel { Name = "HomeLanguages", VerticalAlignment = VerticalAlignment.Center };
        foreach (var language in new[] { ("zh-CN", "中文"), ("en-US", "English") })
        {
            var button = new Button
            {
                Content = language.Item2, Tag = language.Item1, MinWidth = language.Item1 == "en-US" ? 88 : 72,
                Margin = new(0, 0, 8, 0), Padding = new(12, 8, 12, 8), MinHeight = 48,
                BorderBrush = InstallerI18n.Language == language.Item1 ? BoilerBrush("#2968D9") : Brushes.Transparent
            };
            AutomationProperties.SetName(button, language.Item2);
            button.Click += (_, _) =>
            {
                if (language.Item1 == "en-US") EnglishLanguage.IsChecked = true;
                else ChineseLanguage.IsChecked = true;
                ShowBoilerHome();
            };
            languages.Children.Add(button);
        }
        controls.Children.Add(languages);
        var exit = new Button { Content = PartitionInventory.L("退出", "Exit"), MinWidth = 64, Margin = new(0), Padding = new(12, 8, 12, 8) };
        exit.Click += ExitWindow;
        controls.Children.Add(exit);
        Grid.SetColumn(controls, 1); header.Children.Add(controls);
        homeHeader = new Border
        {
            Name = "HomeHeader", Background = Brushes.White, BorderBrush = BoilerBrush("#DFE6F0"),
            BorderThickness = new(0, 0, 0, 1), Padding = new(48, 16, 48, 16), Child = header
        };
        layout.Children.Add(homeHeader);

        homeContent = new StackPanel { Name = "HomeContent", Margin = new(48, 32, 48, 32) };
        homeTitle = new TextBlock
        {
            Text = PartitionInventory.L("选择这次安装的方式", "Choose how to install SteamOS"),
            FontSize = 40, FontWeight = FontWeights.Bold, TextWrapping = TextWrapping.Wrap, Margin = new(0, 0, 0, 8)
        };
        homeContent.Children.Add(homeTitle);
        homeIntro = new Grid { Name = "HomeIntro", Margin = new(0, 0, 0, 24) };
        homeIntro.ColumnDefinitions.Add(new() { Width = new(1, GridUnitType.Star) });
        homeIntro.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        homeIntro.RowDefinitions.Add(new() { Height = GridLength.Auto });
        homeIntro.RowDefinitions.Add(new() { Height = GridLength.Auto });
        homeDescription = new TextBlock
        {
            Text = PartitionInventory.L("按你的系统来源，开始对应的安装流程。", "Start the installation flow that matches your system source."),
            FontSize = 18, LineHeight = 28, Foreground = BoilerBrush("#61728A"), TextWrapping = TextWrapping.Wrap,
            VerticalAlignment = VerticalAlignment.Center
        };
        homeIntro.Children.Add(homeDescription);
        var fileHintText = PartitionInventory.L("也可以将镜像、升级包和机型包一起拖入窗口，自动关联", "You can also drop images, upgrades and device packages together to associate them");
        var fileHint = new Grid();
        fileHint.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        fileHint.ColumnDefinitions.Add(new() { Width = new(1, GridUnitType.Star) });
        fileHint.Children.Add(CreateFileDropWatermark());
        var fileHintLabel = new TextBlock
        {
            Text = fileHintText, TextWrapping = TextWrapping.Wrap, TextAlignment = TextAlignment.Right,
            FontSize = 14, LineHeight = 20, MaxWidth = 400, VerticalAlignment = VerticalAlignment.Center
        };
        Grid.SetColumn(fileHintLabel, 1); fileHint.Children.Add(fileHintLabel);
        homeAddFiles = new Button
        {
            Name = "HomeAddFiles", Content = fileHint,
            MinHeight = 48, MaxWidth = 520, Padding = new(8, 8, 8, 8),
            Background = Brushes.Transparent, Foreground = BoilerBrush("#51657E"), HorizontalAlignment = HorizontalAlignment.Right,
            VerticalAlignment = VerticalAlignment.Center
        };
        AutomationProperties.SetName(homeAddFiles, fileHintText);
        homeAddFiles.Click += async (_, _) => await BrowseAssociationFilesAsync();
        homeIntro.Children.Add(homeAddFiles);
        homeContent.Children.Add(homeIntro);
        homeChoices = new UniformGrid { Name = "HomeChoices", Columns = 3 };
        foreach (var entry in new[]
        {
            ("custom", "构建并安装自定义 SteamOS", "Build a custom SteamOS image", "默认准备 3.10，也可保留基础版本；选择机型适配和预装内容。", "Use SteamOS 3.10 or keep the base version; choose device support and components.", "开始自定义构建", "Start custom build", "entry-build"),
            ("imported", "刷入本地 IMG 镜像", "Flash a local IMG", "选择已有的 SteamOS IMG 镜像，核对目标分区与容量后刷入。", "Choose an existing SteamOS IMG, then review the destination and capacity before flashing.", "选择 IMG 镜像", "Choose an IMG", "entry-import"),
            ("vanilla", "Vanilla 原版安装", "Vanilla SteamOS", "使用官方系统；可自行选择无线固件、Decky 和维护器。", "Use an official system, with optional wireless firmware, Decky and maintenance.", "开始原版安装", "Start Vanilla install", "entry-vanilla")
        })
        {
            homeChoices.Children.Add(CreateBoilerEntry(entry.Item1,
                PartitionInventory.L(entry.Item2, entry.Item3), PartitionInventory.L(entry.Item4, entry.Item5),
                PartitionInventory.L(entry.Item6, entry.Item7), entry.Item8));
        }
        homeContent.Children.Add(homeChoices);
        homeScroll = new ScrollViewer
        {
            Name = "HomeScroll", Content = homeContent, VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, PanningMode = PanningMode.VerticalOnly,
            VerticalContentAlignment = VerticalAlignment.Center, Background = Brushes.Transparent, Focusable = false
        };
        Grid.SetRow(homeScroll, 1); layout.Children.Add(homeScroll);

        var footer = new Grid();
        footer.ColumnDefinitions.Add(new() { Width = new(1, GridUnitType.Star) });
        footer.ColumnDefinitions.Add(new() { Width = new(2, GridUnitType.Star) });
        footer.Children.Add(new TextBlock
        {
            Text = PartitionInventory.L("安装前请备份重要数据。", "Back up important data before installation."),
            Foreground = BoilerBrush("#61728A"), FontSize = 13, TextWrapping = TextWrapping.Wrap, VerticalAlignment = VerticalAlignment.Center
        });
        var flowNote = new TextBlock
        {
            Text = PartitionInventory.L("系统来源、安装位置与安装方案将在后续步骤确认。", "Review the system source, destination and installation plan in the following steps."),
            Foreground = BoilerBrush("#61728A"), FontSize = 13, TextWrapping = TextWrapping.Wrap,
            TextAlignment = TextAlignment.Right, Margin = new(20, 0, 0, 0), VerticalAlignment = VerticalAlignment.Center
        };
        Grid.SetColumn(flowNote, 1); footer.Children.Add(flowNote);
        homeFooter = new Border { Name = "HomeFooter", Background = Brushes.White, Padding = new(48, 16, 48, 16), Child = footer };
        Grid.SetRow(homeFooter, 2); layout.Children.Add(homeFooter);

        homeScreen = new Border { Background = BoilerBrush("#F4F6FA"), Child = layout };
        KeyboardNavigation.SetTabNavigation(homeScreen, KeyboardNavigationMode.Cycle);
        Panel.SetZIndex(homeScreen, 20); WindowRoot.Children.Add(homeScreen);
        Title = "Boiler Installer";
        UpdateBoilerHomeLayout();
    }

    private Button CreateBoilerEntry(string mode, string title, string description, string action, string icon, Action? activate = null)
    {
        var content = new Grid();
        for (var i = 0; i < 5; i++) content.RowDefinitions.Add(new() { Height = i == 3 ? new(1, GridUnitType.Star) : GridLength.Auto });
        var glyph = new Border { Width = 48, Height = 48, HorizontalAlignment = HorizontalAlignment.Left, Background = BoilerBrush("#EFF5FF"), CornerRadius = new(12), Margin = new(0, 0, 0, 16), Child = BoilerImageAsset(icon, 28) };
        content.Children.Add(glyph);
        var heading = new TextBlock { Text = title, FontSize = 24, LineHeight = 34, FontWeight = FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap, Margin = new(0, 0, 0, 16) };
        Grid.SetRow(heading, 1); content.Children.Add(heading);
        var note = new TextBlock { Text = description, FontSize = 16, LineHeight = 26, Foreground = BoilerBrush("#61728A"), TextWrapping = TextWrapping.Wrap, Margin = new(0, 0, 0, 24) };
        Grid.SetRow(note, 2); content.Children.Add(note);
        // One focus/activation target per card. The CTA is a visual label, not a nested button.
        var callToAction = new Border
        {
            Background = BoilerBrush("#2968D9"), CornerRadius = new(8), MinHeight = 52, Padding = new(8),
            Child = new TextBlock { Text = action, Foreground = Brushes.White, FontSize = 16, TextAlignment = TextAlignment.Center, TextWrapping = TextWrapping.Wrap, VerticalAlignment = VerticalAlignment.Center }
        };
        Grid.SetRow(callToAction, 4); content.Children.Add(callToAction);
        content.Name = "FullEntryContent";
        var compact = new Grid { Name = "CompactEntryContent", Visibility = Visibility.Collapsed };
        compact.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        compact.ColumnDefinitions.Add(new() { Width = new(1, GridUnitType.Star) });
        compact.ColumnDefinitions.Add(new() { Width = GridLength.Auto });
        compact.Children.Add(new Border { Width = 48, Height = 48, Background = BoilerBrush("#EFF5FF"), CornerRadius = new(10), Margin = new(0, 0, 16, 0), VerticalAlignment = VerticalAlignment.Center, Child = BoilerImageAsset(icon, 28) });
        var compactText = new StackPanel { VerticalAlignment = VerticalAlignment.Center };
        compactText.Children.Add(new TextBlock { Text = title, FontSize = 18, LineHeight = 26, FontWeight = FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap, Margin = new(0, 0, 0, 6) });
        compactText.Children.Add(new TextBlock { Text = description, FontSize = 14, LineHeight = 22, Foreground = BoilerBrush("#61728A"), TextWrapping = TextWrapping.Wrap });
        Grid.SetColumn(compactText, 1); compact.Children.Add(compactText);
        var arrow = new TextBlock { Text = "→", FontSize = 24, Foreground = BoilerBrush("#2968D9"), Margin = new(12, 0, 0, 0), VerticalAlignment = VerticalAlignment.Center };
        Grid.SetColumn(arrow, 2); compact.Children.Add(arrow);
        var adaptiveContent = new Grid();
        adaptiveContent.Children.Add(content); adaptiveContent.Children.Add(compact);
        var button = new Button
        {
            Tag = mode, Content = adaptiveContent, MinHeight = 392, Padding = new(24), Margin = new(0, 0, 20, 0),
            HorizontalContentAlignment = HorizontalAlignment.Stretch, VerticalContentAlignment = VerticalAlignment.Stretch,
            Style = (Style)FindResource("HomeEntry"), Cursor = Cursors.Hand
        };
        AutomationProperties.SetName(button, title);
        AutomationProperties.SetHelpText(button, description);
        button.Click += (_, _) => { if (activate != null) activate(); else ChooseBoilerMode(mode); };
        return button;
    }

    private void UpdateBoilerHomeLayout()
    {
        if (homeChoices == null || homeContent == null || homeHeader == null || homeFooter == null || homeTitle == null
            || homeIntro == null || homeDescription == null || homeAddFiles == null) return;
        var width = WindowRoot.ActualWidth > 0 ? WindowRoot.ActualWidth : Width;
        var wide = width >= 960;
        homeChoices.Columns = wide ? 3 : 1;
        homeChoices.Rows = wide ? 1 : 3;
        var padding = wide ? 48 : 24;
        homeContent.Margin = new(padding, wide ? 32 : 24, padding, wide ? 32 : 24);
        homeHeader.Padding = new(padding, 16, padding, 16);
        homeFooter.Padding = new(padding, 12, padding, 12);
        homeTitle.FontSize = wide ? 40 : 28;
        var inlineIntro = width >= 1100;
        Grid.SetColumnSpan(homeDescription, inlineIntro ? 1 : 2);
        homeDescription.Margin = inlineIntro ? new(0) : new(0, 0, 0, 12);
        Grid.SetRow(homeAddFiles, inlineIntro ? 0 : 1);
        Grid.SetColumn(homeAddFiles, inlineIntro ? 1 : 0);
        Grid.SetColumnSpan(homeAddFiles, inlineIntro ? 1 : 2);
        homeAddFiles.Margin = new(inlineIntro ? 24 : 0, 0, 0, 0);
        foreach (var (button, index) in homeChoices.Children.OfType<Button>().Select((button, index) => (button, index)))
        {
            button.MinHeight = wide ? 392 : 112;
            button.Padding = new(wide ? 24 : 12);
            button.Margin = wide ? new Thickness(0, 0, index == 2 ? 0 : 20, 0) : new(0, 0, 0, index == 2 ? 0 : 16);
            if (button.Content is Grid adaptive)
            {
                adaptive.Children[0].Visibility = wide ? Visibility.Visible : Visibility.Collapsed;
                adaptive.Children[1].Visibility = wide ? Visibility.Collapsed : Visibility.Visible;
            }
        }
        // On narrow windows, keep the brand and language/exit controls on separate rows.
        if (homeHeader.Child is Grid header && header.Children[1] is FrameworkElement controls)
        {
            var narrow = width < 800;
            if (header.RowDefinitions.Count == 0)
            {
                header.RowDefinitions.Add(new() { Height = GridLength.Auto });
                header.RowDefinitions.Add(new() { Height = GridLength.Auto });
            }
            Grid.SetRow(controls, narrow ? 1 : 0);
            Grid.SetColumn(controls, narrow ? 0 : 1);
            Grid.SetColumnSpan(controls, narrow ? 2 : 1);
            controls.HorizontalAlignment = HorizontalAlignment.Right;
            controls.Margin = new(0, narrow ? 12 : 0, 0, 0);
            if (header.Children[0] is StackPanel brand
                && brand.Children.OfType<TextBlock>().FirstOrDefault(item => item.Name == "HomeVersion") is TextBlock version)
                version.Visibility = width < 800 ? Visibility.Collapsed : Visibility.Visible;
        }
    }

    private void BackToBoilerHome(object sender, RoutedEventArgs e)
    {
        if (operation != null || DownloadsBusy || PartitionSelector.IsBusy) return;
        ShowBoilerHome();
    }
}
