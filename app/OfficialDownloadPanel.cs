using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using Microsoft.Win32;
using System.Diagnostics;

namespace OxpInstaller;

public sealed class OfficialDownloadPanel : UserControl
{
    private readonly TextBlock heading = new() { FontSize = 19, FontWeight = FontWeights.SemiBold };
    private readonly ComboBox sources = new() { MinHeight = 48, Margin = new(0, 8, 0, 8) };
    private readonly TextBox address = new() { IsReadOnly = true, Height = 48, MinHeight = 48, TextWrapping = TextWrapping.NoWrap, HorizontalScrollBarVisibility = ScrollBarVisibility.Auto, VerticalContentAlignment = VerticalAlignment.Center, Margin = new(0, 0, 0, 8), Text = OfficialSystem.BaseUrl + ".raucb" };
    private readonly Button copyAddress = new() { Margin = new(8, 0, 0, 8), VerticalAlignment = VerticalAlignment.Top };
    private readonly CheckBox agreement = new() { Margin = new(0, 4, 0, 8) };
    private readonly Button terms = new() { HorizontalAlignment = HorizontalAlignment.Left, Margin = new(8, 0, 0, 0) };
    private readonly Button restart = new() { Margin = new(0) };
    private readonly TextBlock localHeading = new() { FontSize = 19, FontWeight = FontWeights.SemiBold, Margin = new(0, 0, 0, 10) };
    private readonly TextBlock localNote = new() { TextWrapping = TextWrapping.Wrap, Foreground = Brushes.SlateGray, LineHeight = 23 };
    private readonly TextBlock localHint = new() { TextWrapping = TextWrapping.Wrap, Foreground = Brushes.SlateGray, FontSize = 12, LineHeight = 23 };
    private readonly TextBox localPath = new() { IsReadOnly = true, VerticalContentAlignment = VerticalAlignment.Center };
    private readonly TextBlock localStatus = new() { TextWrapping = TextWrapping.Wrap, Margin = new(0, 10, 0, 0), Visibility = Visibility.Collapsed };
    private readonly Button browse = new() { Margin = new(10, 0, 0, 0) };
    private readonly TextBlock note = new() { TextWrapping = TextWrapping.Wrap, Foreground = Brushes.SlateGray, Margin = new(0, 4, 0, 10) };
    private readonly Button action = new() { HorizontalAlignment = HorizontalAlignment.Left, MinHeight = 48 };
    private readonly TextBlock status = new() { TextWrapping = TextWrapping.Wrap, Margin = new(0, 10, 0, 0) };
    private readonly ProgressBar progress = new() { Height = 7, Margin = new(0, 12, 0, 0), Visibility = Visibility.Collapsed };
    private readonly Border downloadCard;
    private CancellationTokenSource? operation;
    private bool required = true;
    private bool localOnly;
    private bool sourceCardLayout;
    private DiscoveredInput? associatedInspection;
    public bool IsBusy => operation != null;
    internal string? AssociatedSourcePath { get; private set; }
    internal DiscoveredInput? CurrentAssociatedInspection
    {
        get
        {
            if (AssociatedSourcePath == null || associatedInspection == null) return null;
            try
            {
                if (!string.Equals(Path.GetFullPath(AssociatedSourcePath), Path.GetFullPath(associatedInspection.Path), StringComparison.OrdinalIgnoreCase)) return null;
                InputDiscovery.VerifyStamp(associatedInspection);
                return associatedInspection;
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException) { return null; }
        }
    }
    internal RaucManifestInfo? AssociatedRaucManifest => CurrentAssociatedInspection?.RaucManifest;
    internal string? SelectedSourcePath => AssociatedSourcePath ?? (string.IsNullOrWhiteSpace(localPath.Text) ? null : localPath.Text);
    internal event Action? SourceReady;
    internal void UseSourceCardLayout()
    {
        if (sourceCardLayout) return;
        sourceCardLayout = true;
        var download = downloadCard.Child; downloadCard.Child = null; Content = download;
        heading.FontSize = 14; heading.FontWeight = FontWeights.SemiBold;
        copyAddress.MinHeight = sources.MinHeight = address.MinHeight = action.MinHeight = restart.MinHeight = 44; address.Height = 44;
        agreement.MinHeight = terms.MinHeight = 32; agreement.FontSize = terms.FontSize = note.FontSize = status.FontSize = 12;
        sources.Style = TryFindResource("SourceDropdown") as Style; address.Style = TryFindResource("SourceAddress") as Style;
        action.Background = new SolidColorBrush(Color.FromRgb(41, 104, 217)); action.Foreground = Brushes.White;
        restart.Background = copyAddress.Background = terms.Background = Brushes.Transparent; terms.Foreground = new SolidColorBrush(Color.FromRgb(41, 104, 217));
        RefreshLanguage();
    }
    internal void AssociateLocalSource(string path, DiscoveredInput? inspected = null)
    {
        AssociatedSourcePath = Path.GetFullPath(path);
        associatedInspection = null;
        if (inspected?.Stamp != null)
        {
            try
            {
                InputDiscovery.VerifyStamp(inspected);
                var targetStamp = InputDiscovery.ReadStamp(AssociatedSourcePath);
                if (targetStamp.Length == inspected.Stamp.Length && targetStamp.HeaderSha256 == inspected.Stamp.HeaderSha256)
                    associatedInspection = inspected with { Path = AssociatedSourcePath, Stamp = targetStamp };
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException) { }
        }
        localStatus.Visibility = Visibility.Visible;
        RefreshStatus(); SourceChanged?.Invoke();
    }
    public event Action<bool>? BusyChanged;
    internal event Action<FeedbackDiagnostic>? DownloadFailed;
    public event Action? SourceChanged;
    private static string L(string zh, string en) => PartitionInventory.L(zh, en);
    private static Border Card(UIElement content) => new()
    {
        Child = content, Background = Brushes.White, CornerRadius = new(12), Padding = new(16), Margin = new(0, 0, 0, 12),
        BorderBrush = new SolidColorBrush(Color.FromRgb(223, 230, 240)), BorderThickness = new(1)
    };
    public OfficialDownloadPanel()
    {
        var statusStyle = new Style(typeof(TextBlock));
        var emptyStatus = new Trigger { Property = TextBlock.TextProperty, Value = "" };
        emptyStatus.Setters.Add(new Setter(VisibilityProperty, Visibility.Collapsed));
        statusStyle.Triggers.Add(emptyStatus); status.Style = statusStyle;
        var body = new StackPanel(); var download = new StackPanel();
        download.Children.Add(heading); download.Children.Add(sources);
        var addressRow = new DockPanel(); DockPanel.SetDock(copyAddress, Dock.Right);
        addressRow.Children.Add(copyAddress); addressRow.Children.Add(address); download.Children.Add(addressRow); download.Children.Add(note);
        var consent = new WrapPanel(); consent.Children.Add(agreement); consent.Children.Add(terms); download.Children.Add(consent);
        var buttons = new WrapPanel(); action.Margin = new(0, 0, 10, 0); buttons.Children.Add(action); buttons.Children.Add(restart); download.Children.Add(buttons);
        download.Children.Add(progress); download.Children.Add(status); downloadCard = Card(download); body.Children.Add(downloadCard);
        var local = new StackPanel(); local.Children.Add(localHeading); local.Children.Add(localNote);
        var localRow = new DockPanel { Margin = new(0, 8, 0, 8) }; DockPanel.SetDock(browse, Dock.Right);
        localRow.Children.Add(browse); localRow.Children.Add(localPath); local.Children.Add(localRow); local.Children.Add(localHint);
        local.Children.Add(localStatus);
        body.Children.Add(Card(local)); Content = body;
        action.Click += async (_, _) => { if (IsBusy) Pause(); else await DownloadAsync(); };
        restart.Click += async (_, _) => { if (!IsBusy) await DownloadAsync(fresh: true); };
        browse.Click += async (_, _) =>
        {
            var dialog = new OpenFileDialog { Filter = L("SteamOS 系统升级源|*.img;*.raucb", "SteamOS system upgrade source|*.img;*.raucb") };
            if (dialog.ShowDialog(Window.GetWindow(this)) == true) await DownloadAsync(dialog.FileName);
        };
        copyAddress.Click += (_, _) =>
        {
            try { Clipboard.SetText(address.Text); if (!IsBusy) SetStatus(L("地址已复制。下载后，在下方选择本地更新包即可继续。", "URL copied. Select the downloaded bundle below to continue.")); }
            catch (Exception error) { if (!IsBusy) SetStatus(error.Message); }
        };
        terms.Click += (_, _) =>
        {
            try { Process.Start(new ProcessStartInfo("https://store.steampowered.com/steamos/download/?ver=steamdeck") { UseShellExecute = true }); }
            catch (Exception error) { if (!IsBusy) SetStatus(error.Message); }
        };
        RefreshLanguage();
    }
    public void SetRequired(bool value)
    {
        var changed = required != value;
        required = value;
        if (!IsBusy) { SetControls(); if (changed) RefreshStatus(); }
    }
    public void SetLocalOnly(bool value)
    {
        if (localOnly == value) return;
        localOnly = value;
        downloadCard.Visibility = value ? Visibility.Collapsed : Visibility.Visible;
        if (Content is StackPanel body && body.Children.OfType<Border>().LastOrDefault() is { } localCard)
        {
            localCard.Padding = new(value ? 24 : 16);
            localCard.Margin = new(0, 0, 0, value ? 24 : 12);
        }
        RefreshLanguage();
    }
    private void SetControls()
    {
        action.IsEnabled = required;
        browse.IsEnabled = restart.IsEnabled = agreement.IsEnabled = required && !IsBusy;
        sources.IsEnabled = !IsBusy;
    }
    public void RefreshWorkspace() { if (!IsBusy) { AssociatedSourcePath = null; associatedInspection = null; localPath.Text = ""; localStatus.Visibility = Visibility.Collapsed; progress.Visibility = Visibility.Collapsed; RefreshStatus(); SourceChanged?.Invoke(); } }
    public void RefreshLanguage()
    {
        heading.Text = sourceCardLayout ? L("下载来源", "Download source") : L("下载官方系统升级源", "Download the official system upgrade source");
        sources.Items.Clear(); sources.Items.Add(L("Valve 官方系统镜像 · 3.10 · build 20260924.1000（推荐）", "Valve system image · 3.10 · build 20260924.1000 (Recommended)")); sources.SelectedIndex = 0;
        copyAddress.Content = sourceCardLayout ? L("复制地址", "Copy URL") : L("复制地址 · 手动下载", "Copy URL · download manually");
        note.Text = L("下载 .raucb 更新包后即可继续。开始安装后，再联网获取系统数据并重建镜像。", "Download the .raucb update bundle to continue. System data is downloaded and the image rebuilt after installation starts.");
        agreement.Content = L("我已阅读并同意 SteamOS 下载条款", "I have read and accept the SteamOS download terms");
        terms.Content = L("查看 Valve 下载条款 ↗", "View Valve download terms ↗");
        restart.Content = L("重新下载", "Start fresh");
        localHeading.Text = localOnly ? L("2  SteamOS 系统升级源", "2  SteamOS system upgrade source") : L("选择本地系统升级源", "Select a local system upgrade source");
        localNote.Text = localOnly
            ? L("第二个文件：SteamOS 系统升级源，与上方官方恢复镜像配合使用。", "Second file: a SteamOS system upgrade source, used together with the official recovery image above.")
            : L("使用本地 SteamOS 系统升级源；导入后，安装时使用工作空间中的副本。", "Import a local SteamOS system upgrade source for installation from your workspace.");
        browse.Content = L("选择文件…", "Choose file…");
        localHint.Text = L("支持 .raucb 更新包或 rootfs.img。升级包版本会自动识别；rootfs.img 版本将在安装准备时识别。", "Accepts a .raucb bundle or rootfs.img. Upgrade package versions are identified automatically; rootfs.img is identified during installation preparation.");
        System.Windows.Automation.AutomationProperties.SetName(localPath, L("系统升级源路径", "System upgrade source path"));
        System.Windows.Automation.AutomationProperties.SetName(sources, heading.Text);
        RefreshStatus();
    }
    private void SetStatus(string message) { status.Text = localStatus.Text = message; }
    private void RefreshStatus()
    {
        if (IsBusy) return;
        if (AssociatedSourcePath != null)
        {
            localPath.Text = AssociatedSourcePath;
            action.Content = L("下载 / 继续", "Download / resume"); SetControls();
            var inspected = CurrentAssociatedInspection;
            var status = inspected?.RaucManifest is { } manifest
                ? manifest.IsSupportedByCurrentInstaller
                    ? L($"检测到 SteamOS {manifest.Version} 更新包；继续后将完成内容校验。", $"SteamOS {manifest.Version} update bundle detected; continue for complete content checks.")
                    : L($"检测到 SteamOS {manifest.Version} 更新包；当前安装流程不支持此版本。", $"SteamOS {manifest.Version} update bundle detected; this installer does not support it.")
                : inspected?.Kind == InputKind.UpgradeRoot
                    ? L("已选择 rootfs.img，版本将在安装准备时识别。", "rootfs.img selected; its version will be identified during installation preparation.")
                    : L("本地文件已关联。继续时检查其声明版本与内容。", "Local file associated. Its declared version and contents will be checked when you continue.");
            SetStatus(status);
            return;
        }
        var cached = OfficialSystem.HasPreparedSource();
        localPath.Text = cached ? OfficialSystem.RequirePreparedSource() : "";
        action.Content = L("下载 / 继续", "Download / resume");
        SetControls();
        SetStatus(cached ? ""
            : required ? localOnly ? L("请选择第二个文件：SteamOS 系统升级源。", "Select the second file: a SteamOS system upgrade source.")
                : L("尚未准备。请下载镜像或选择本地文件。", "Not ready. Download the image or select a local file.")
            : L("当前方案无需准备官方系统升级源。", "This configuration does not need an official system upgrade source."));
    }
    public void Pause()
    {
        operation?.Cancel(); action.IsEnabled = false;
        SetStatus(L("正在停止下载并保留已有系统数据…", "Stopping the download and retaining existing system data…"));
    }
    internal async Task DownloadAsync(string? local = null, bool fresh = false)
    {
        if (IsBusy || !required || localOnly && local == null) return;
        var importing = local != null && local.EndsWith(".img", StringComparison.OrdinalIgnoreCase);
        localStatus.Visibility = local != null ? Visibility.Visible : Visibility.Collapsed;
        if (local == null && agreement.IsChecked != true) { SetStatus(L("请先阅读并接受下载条款。", "Read and accept the download terms first.")); return; }
        operation = new CancellationTokenSource();
        SourceChanged?.Invoke();
        if (local != null)
        {
            localPath.Text = local;
            SetStatus(L("正在导入本地文件：", "Importing local file: ") + Path.GetFileName(local));
        }
        SetControls(); action.Content = L("暂停", "Pause");
        progress.Value = 0; progress.Visibility = Visibility.Visible; progress.IsIndeterminate = true;
        BusyChanged?.Invoke(true);
        var logPath = Path.Combine(InstallerWorkspace.DirectoryPath, "official-3.10-download.log");
        var completed = false;
        var failed = false;
        var diagnostic = new FeedbackDiagnostic(); diagnostic.Stage("update-preparation");
        try
        {
            Directory.CreateDirectory(InstallerWorkspace.DirectoryPath);
            MediaSource.Ordinary(logPath, InstallationController.Work, false);
            using var log = new StreamWriter(new FileStream(logPath, FileMode.Append, FileAccess.Write, FileShare.Read)) { AutoFlush = true };
            void Report(OfficialPreparationProgress update) => Dispatcher.Invoke(() =>
            {
                diagnostic.Stage(update.Step);
                progress.IsIndeterminate = update.Step == "01.02.02" || update.Fraction == 0;
                progress.Value = Math.Max(progress.Value, update.Fraction * 100);
                SetStatus(update.Message);
                System.Windows.Automation.AutomationProperties.SetName(progress, update.Message);
            });
            localPath.Text = importing
                ? await OfficialSystem.ImportRootAsync(local!, operation.Token, Report)
                : await OfficialSystem.StageBundleAsync(line => { lock (log) { log.WriteLine(line); diagnostic.Append(line + "\n"); } }, operation.Token, Report, local, fresh);
            AssociatedSourcePath = null;
            associatedInspection = null;
            completed = true;
            progress.IsIndeterminate = false; progress.Value = 100;
            SetStatus(L("文件已就绪，可以点击“下一步”。", "File ready. Click Next to continue."));
        }
        catch (OperationCanceledException)
        {
            SetStatus(L("准备已暂停。再次点击可继续；已有系统数据块会重新检查并复用。", "Preparation paused. Click again to continue; existing system chunks will be checked and reused."));
        }
        catch (Exception error) { SetStatus(L("准备未完成：", "Preparation incomplete: ") + error.Message); diagnostic.Failure(error); diagnostic.Append(error.ToString()); failed = local == null; }
        finally
        {
            operation.Dispose(); operation = null; progress.IsIndeterminate = false;
            localPath.Text = AssociatedSourcePath ?? (OfficialSystem.HasPreparedSource() ? OfficialSystem.RequirePreparedSource() : "");
            action.Content = L("下载 / 继续", "Download / resume");
            SetControls();
            SourceChanged?.Invoke();
            BusyChanged?.Invoke(false);
            if (completed) SourceReady?.Invoke();
            if (local != null) localStatus.BringIntoView();
        }
        if (failed) DownloadFailed?.Invoke(diagnostic);
    }
}
