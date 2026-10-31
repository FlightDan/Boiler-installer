using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Threading;

namespace OxpInstaller;

internal static class SystemSourceCardChecks
{
    private static T Field<T>(object o, string n) => (T)o.GetType().GetField(n, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(o)!;
    private static object? Call(MainWindow w, string n, params object?[] a) => typeof(MainWindow).GetMethod(n, BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(w, a);
    private static async Task Flush(MainWindow w) { w.UpdateLayout(); await w.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.ApplicationIdle); }
    private static InstallationRequest Request(MainWindow w) => (InstallationRequest)typeof(MainWindow).GetProperty("Request", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(w)!;
    internal static Task<string> RunAsync() => Application.Current.Dispatcher.CheckAccess() ? RunUi() : Application.Current.Dispatcher.InvokeAsync(RunUi).Task.Unwrap();
    private static async Task<string> RunUi()
    {
        var dir = Path.Combine(Backend.Root, "logs", "source-cards-" + DateTime.UtcNow.ToString("yyyyMMddTHHmmssfff")); Directory.CreateDirectory(dir);
        var checks = new List<string>(); var captures = new List<string>(); var captureErrors = new List<string>();
        void Require(bool ok, string name) { if (!ok) throw new IOException("Source card check failed: " + name); checks.Add(name); }
        var before = InstallerI18n.Language; MainWindow? w = null;
        var recovery = Path.Combine(dir, "recovery.img"); var other = Path.Combine(dir, "other-recovery.img");
        await File.WriteAllBytesAsync(recovery, RecoveryImageChecks.Fixture()); await File.WriteAllBytesAsync(other, RecoveryImageChecks.Fixture());
        var root = Path.Combine(dir, "rootfs.img");
        using (var f = new FileStream(root, FileMode.CreateNew)) { f.SetLength(OfficialSystem.RootSize); f.Position = 0x10040; f.Write("_BHRfS_M"u8); }
        try
        {
            foreach (var lang in new[] { "zh-CN", "en-US" })
            {
                InstallerI18n.SetLanguage(lang);
                w = new MainWindow(true) { Width = 1280, Height = 900, WindowState = WindowState.Normal, ShowActivated = false, DeviceIndexLoaderForChecks = _ => Task.FromResult(BoilerDeviceChecks.IndexResponse()) };
                ((PartitionPicker)w.FindName("PartitionSelector")).AutomaticReadEnabled = false;
                w.Show(); w.BoilerModeForChecks("custom"); Call(w, "ChooseGenericInstallation");
                ((RadioButton)w.FindName("ExternalTarget")).IsChecked = true;
                var image = (TextBox)w.FindName("ImagePath"); image.Clear();
                var panel = Field<OfficialDownloadPanel>(w, "officialDownloadPanel"); Field<TextBox>(panel, "localPath").Clear();
                var a = (SystemSourceCard)w.FindName("RecoverySourceCard"); var b = (SystemSourceCard)w.FindName("UpgradeSourceCard");
                var upgrade = (CheckBox)w.FindName("Upgrade310"); var review = (Expander)w.FindName("SourceInputReview");
                Call(w, "RefreshSystemSourceCards"); await Flush(w);
                Require(a.IsVisible && b.IsVisible && !review.IsVisible && !Field<Border>(w, "inputAssociationPanel").IsVisible, lang + "-only-two-default-source-cards");
                foreach (var card in new[] { a, b })
                {
                    var label = ReferenceEquals(card, a) ? "recovery" : "upgrade";
                    var header = Field<Grid>(card, "header"); var titles = Field<StackPanel>(card, "titles"); var titleRow = Field<WrapPanel>(card, "titleRow");
                    var actions = Field<StackPanel>(card, "actions"); var sourceRow = Field<Grid>(card, "sourceRow");
                    Require(header.Children.Contains(Field<Border>(card, "icon")) && header.Children.Contains(titles)
                        && titles.Children.Contains(titleRow) && titles.Children.Contains(Field<TextBlock>(card, "description"))
                        && titleRow.Children.Contains(Field<TextBlock>(card, "title")) && titleRow.Children.Contains(card.SkipUpgrade), lang + "-" + label + "-header-retains-icon-title-subtitle-and-skip");
                    Require(!header.Children.Contains(actions) && ReferenceEquals(actions.Parent, sourceRow)
                        && actions.Children.Contains(card.BrowseButton) && actions.Children.Contains(card.DownloadButton), lang + "-" + label + "-actions-moved-to-source-row");
                }
                Require(a.BrowseButton.IsEnabled && a.DownloadButton.IsEnabled && b.BrowseButton.IsEnabled && b.DownloadButton.IsEnabled, lang + "-browse-and-download-actions");
                Require(!a.IsExpanded && !b.IsExpanded && b.SkipUpgrade.IsChecked != true, lang + "-closed-downloads-and-default-upgrade");
                foreach (var size in new[] { new Size(1280,900), new Size(640,480) })
                {
                    w.Width = size.Width; w.Height = size.Height; await Flush(w);
                    var s = (ScrollViewer)w.FindName("PageScroll");
                    Require(Math.Abs(a.ActualWidth-b.ActualWidth)<1 && s.ExtentWidth <= s.ViewportWidth+1, lang + "-aligned-without-horizontal-overflow-"+size.Width);
                    foreach (var card in new[] { a, b })
                    {
                        var label = ReferenceEquals(card, a) ? "recovery" : "upgrade"; var narrow = card.ActualWidth > 0 && card.ActualWidth < 660;
                        var sourceContent = Field<Grid>(card, "sourceContent"); var actions = Field<StackPanel>(card, "actions");
                        Require(Grid.GetRow(sourceContent) == 0 && Grid.GetColumn(sourceContent) == 0 && Grid.GetColumnSpan(sourceContent) == (narrow ? 2 : 1)
                            && Grid.GetRow(actions) == (narrow ? 1 : 0) && Grid.GetColumn(actions) == (narrow ? 0 : 1)
                            && Grid.GetColumnSpan(actions) == (narrow ? 2 : 1) && actions.HorizontalAlignment == HorizontalAlignment.Right,
                            lang + "-" + label + "-source-actions-reflow-" + size.Width);
                    }
                    await Capture(w, dir, lang+"-empty-"+size.Width, captures, captureErrors);
                }
                w.Width=1280; w.Height=900; await Flush(w);
                a.DownloadButton.RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); await Flush(w);
                Require(a.IsExpanded && !b.IsExpanded && !Field<ImageDownloadPanel>(w,"downloadPanel").IsBusy, lang+"-recovery-disclosure-does-not-download");
                await Capture(w, dir, lang+"-recovery-download", captures, captureErrors);
                a.DownloadButton.RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); b.DownloadButton.RaiseEvent(new RoutedEventArgs(Button.ClickEvent)); await Flush(w);
                Require(b.IsExpanded && !panel.IsBusy, lang+"-upgrade-disclosure-does-not-download");
                await Capture(w, dir, lang+"-upgrade-download", captures, captureErrors);
                b.SkipUpgrade.IsChecked=true; await Flush(w);
                Require(upgrade.IsChecked==false && b.IsVisible && !b.IsExpanded && !b.BrowseButton.IsEnabled && !b.DownloadButton.IsEnabled, lang+"-skip-stays-visible-and-disables-upgrade");
                Require(!Request(w).UpgradeTo310 && Request(w).OfficialSource==null, lang+"-skip-request-excludes-official-source");
                await Capture(w, dir, lang+"-skip", captures, captureErrors);
                b.SkipUpgrade.IsChecked=false; await Flush(w);
                Require(upgrade.IsChecked==true && b.BrowseButton.IsEnabled, lang+"-unskip-restores-actions");
                await (Task)Call(w,"ReceiveDroppedInputFilesAsync",new object?[]{new[]{recovery,root}})!; await Flush(w);
                Require(image.Text==recovery && panel.AssociatedSourcePath==root && a.IsAssociated && b.IsAssociated && !a.IsExpanded && !b.IsExpanded, lang+"-drop-auto-associates-and-collapses");
                var rootDescription = Field<TextBlock>(b,"description").Text;
                Require((rootDescription.Contains("installation preparation",StringComparison.OrdinalIgnoreCase) || rootDescription.Contains("安装准备",StringComparison.Ordinal))
                    && !rootDescription.Contains(OfficialSystem.Version,StringComparison.Ordinal), lang+"-rootfs-does-not-invent-a-version");
                Require(!review.IsVisible && w.CurrentOptions.DevicePackage==null,lang+"-ordinary-source-drop-needs-no-extra-association-box");
                foreach (var size in new[] { new Size(1280,900), new Size(640,480) })
                {
                    w.Width = size.Width; w.Height = size.Height; await Flush(w); var s = (ScrollViewer)w.FindName("PageScroll");
                    Require(s.ExtentWidth <= s.ViewportWidth + 1, lang + "-associated-fits-" + size.Width);
                    foreach (var card in new[] { a, b })
                    {
                        var label = ReferenceEquals(card, a) ? "recovery" : "upgrade"; var narrow = card.ActualWidth > 0 && card.ActualWidth < 660;
                        var sourceContent = Field<Grid>(card, "sourceContent"); var actions = Field<StackPanel>(card, "actions");
                        Require(Field<Grid>(card, "fileInfo").Visibility == Visibility.Visible && sourceContent.Visibility == Visibility.Visible
                            && Grid.GetColumnSpan(sourceContent) == (narrow ? 2 : 1) && Grid.GetRow(actions) == (narrow ? 1 : 0)
                            && Grid.GetColumn(actions) == (narrow ? 0 : 1) && Grid.GetColumnSpan(actions) == (narrow ? 2 : 1),
                            lang + "-" + label + "-associated-file-and-actions-share-source-row-" + size.Width);
                    }
                    await Capture(w, dir, lang + "-associated-" + size.Width, captures, captureErrors);
                }
                w.Width=1280;w.Height=900;b.SkipUpgrade.IsChecked=true;await Flush(w);
                Require(panel.AssociatedSourcePath==root && !Request(w).UpgradeTo310 && Request(w).OfficialSource==null,lang+"-skip-retains-file-but-never-submits-it");
                await (Task)Call(w,"ReceiveDroppedInputFilesAsync",new object?[]{new[]{root}})!;await Flush(w);
                Require(b.SkipUpgrade.IsChecked==true && !Request(w).UpgradeTo310 && Request(w).OfficialSource==null,lang+"-new-drop-respects-explicit-skip");
                b.SkipUpgrade.IsChecked=false;await Flush(w);Require(b.IsAssociated && panel.AssociatedSourcePath==root,lang+"-unskip-retains-associated-file");
                await (Task)Call(w,"ReceiveDroppedInputFilesAsync",new object?[]{new[]{recovery,other}})!;await Flush(w);
                Require(review.IsVisible && review.IsExpanded && Call(w,"SourceIssue",true,false) is string && image.Text==recovery,lang+"-conflict-needs-review-without-overwrite");
                Field<Button>(w,"inputClearButton").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                var script=Path.Combine(dir,lang+"-device.sh");await File.WriteAllTextAsync(script,"#!/bin/bash\n# boiler-script: 1\n# boiler-name: Source card fixture\nexit 0\n");
                await (Task)Call(w,"ReceiveDroppedInputFilesAsync",new object?[]{new[]{script}})!;await Flush(w);
                Require(w.CurrentOptions.DevicePackage!=null && w.CurrentOptions.DeviceTrust=="developer-session" && !Field<Button>(w,"inputApplyButton").IsEnabled,lang+"-explicit-device-drop-auto-imports-with-local-trust");
                Field<Button>(w,"inputClearButton").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                w.BoilerModeForChecks("local");await Flush(w);
                Require(a.IsVisible && b.IsVisible && !b.SkipUpgrade.IsVisible && a.DownloadButton.Visibility==Visibility.Collapsed && b.DownloadButton.Visibility==Visibility.Collapsed,lang+"-local-pair-requires-upgrade-and-local-files");
                w.BoilerModeForChecks("imported");await Flush(w);Require(a.IsVisible && !b.IsVisible && a.DownloadButton.Visibility==Visibility.Collapsed,lang+"-img-mode-retains-single-source");
                w.BoilerModeForChecks("custom");Call(w,"ChooseGenericInstallation");image.Clear();NativeDrop(w,recovery);
                for(var i=0;i<200 && (image.Text!=recovery||Field<bool>(w,"inputInspectionBusy")||Field<bool>(w,"inputAssociationBusy"));i++){await Task.Delay(20);await Flush(w);}
                Require(image.Text==recovery && a.IsAssociated,lang+"-native-drop-enters-auto-association");

                var bundle = Path.Combine(dir,lang+"-supported.raucb"); await File.WriteAllBytesAsync(bundle,"RAUC UI preview fixture"u8.ToArray());
                var bundleInput = new DiscoveredInput(bundle,InputKind.UpgradeBundle,Path.GetFileName(bundle),"",InputDiscovery.ReadStamp(bundle),
                    RaucManifest:new RaucManifestInfo("steamos-amd64","3.10.0","rootfs.img.caibx"));
                panel.AssociateLocalSource(bundle,bundleInput); Call(w,"RefreshSystemSourceCards"); await Flush(w);
                var declaredDescription = Field<TextBlock>(b,"description").Text;
                Require(declaredDescription.Contains("3.10.0",StringComparison.Ordinal) && panel.AssociatedRaucManifest?.Version=="3.10.0",
                    lang+"-supported-rauc-version-appears-in-upgrade-card");
                InstallerI18n.SetLanguage(lang=="zh-CN"?"en-US":"zh-CN"); panel.RefreshLanguage(); Call(w,"RefreshInputAssociationLanguage"); Call(w,"RefreshSystemSourceCards"); await Flush(w);
                Require(Field<TextBlock>(b,"description").Text.Contains("3.10.0",StringComparison.Ordinal) && panel.AssociatedRaucManifest?.Version=="3.10.0",
                    lang+"-language-refresh-retains-bound-rauc-version");
                InstallerI18n.SetLanguage(lang); panel.RefreshLanguage(); Call(w,"RefreshInputAssociationLanguage"); Call(w,"RefreshSystemSourceCards"); await Flush(w);
                File.SetLastWriteTimeUtc(bundle,File.GetLastWriteTimeUtc(bundle).AddSeconds(3)); Call(w,"RefreshSystemSourceCards"); await Flush(w);
                Require(panel.AssociatedRaucManifest==null && !Field<TextBlock>(b,"description").Text.Contains("3.10.0",StringComparison.Ordinal),
                    lang+"-changed-source-hides-stale-manifest-version");

                var unsupportedPath=Path.Combine(dir,lang+"-unsupported.raucb"); await File.WriteAllBytesAsync(unsupportedPath,"unsupported RAUC UI preview fixture"u8.ToArray());
                var unsupportedInput=new DiscoveredInput(unsupportedPath,InputKind.UpgradeBundle,Path.GetFileName(unsupportedPath),"",InputDiscovery.ReadStamp(unsupportedPath),
                    Error:InputDiscovery.UnsupportedRaucVersionError("3.8.14"),RaucManifest:new RaucManifestInfo("steamos-amd64","3.8.14","rootfs.img.caibx"));
                var unsupportedRow=new InputAssignment(unsupportedPath){Input=unsupportedInput,Role=InputRole.Upgrade};
                var unsupportedPlan=InputAssociations.Resolve([unsupportedRow],"custom",false,false);
                Require(unsupportedPlan.Conflict!=null,"unsupported-rauc-version-blocks-association-plan");
                Field<List<InputAssignment>>(w,"inputAssignments").Add(unsupportedRow); Call(w,"RenderInputAssignments"); await Flush(w);
                Require(!Field<TextBlock>(b,"description").Text.Contains("3.8.14",StringComparison.Ordinal),
                    lang+"-pending-bundle-does-not-overwrite-current-source");
                panel.RefreshWorkspace(); Call(w,"RefreshSystemSourceCards"); await Flush(w);
                var rows=Field<StackPanel>(w,"inputRowsHost");
                var unsupportedText=rows.Children.OfType<DockPanel>().SelectMany(item=>item.Children.OfType<StackPanel>())
                    .SelectMany(item=>item.Children.OfType<TextBlock>()).Select(item=>item.Text).FirstOrDefault(text=>text.Contains("3.8.14",StringComparison.Ordinal));
                Require(unsupportedText!=null && !Field<Button>(w,"inputApplyButton").IsEnabled
                    && Field<TextBlock>(b,"description").Text.Contains("3.8.14",StringComparison.Ordinal),
                    lang+"-unsupported-version-is-shown-and-remains-blocked");
                var roleChoice=rows.Children.OfType<DockPanel>().SelectMany(item=>item.Children.OfType<ComboBox>()).Last();
                roleChoice.SelectedItem=roleChoice.Items.OfType<ComboBoxItem>().Single(item=>(InputRole)item.Tag==InputRole.Ignore); await Flush(w);
                Require(!Field<TextBlock>(b,"description").Text.Contains("3.8.14",StringComparison.Ordinal),lang+"-ignored-bundle-refreshes-card-immediately");
                roleChoice.SelectedItem=roleChoice.Items.OfType<ComboBoxItem>().Single(item=>(InputRole)item.Tag==InputRole.Upgrade); await Flush(w);
                Require(Field<TextBlock>(b,"description").Text.Contains("3.8.14",StringComparison.Ordinal),lang+"-restored-upgrade-role-refreshes-card-immediately");
                File.SetLastWriteTimeUtc(unsupportedPath,File.GetLastWriteTimeUtc(unsupportedPath).AddSeconds(3)); Call(w,"RenderInputAssignments"); await Flush(w);
                var staleDescription=rows.Children.OfType<DockPanel>().SelectMany(item=>item.Children.OfType<StackPanel>())
                    .SelectMany(item=>item.Children.OfType<TextBlock>()).First(item=>item.Text.Contains("File changed",StringComparison.OrdinalIgnoreCase)||item.Text.Contains("文件已改变",StringComparison.Ordinal));
                Require(!(staleDescription.ToolTip as string ?? "").Contains("3.8.14",StringComparison.Ordinal)
                    && !staleDescription.Text.Contains("3.8.14",StringComparison.Ordinal),lang+"-stale-tooltip-and-row-hide-old-version");
                w.Close();w=null;
            }
        }
        finally {w?.Close();InstallerI18n.SetLanguage(before);if(File.Exists(root)&&Path.GetDirectoryName(root)==dir&&Path.GetFileName(root)=="rootfs.img")File.Delete(root);}
        var result=JsonSerializer.Serialize(new{passed=true,visualCapturePassed=captures.Count>0&&captureErrors.Count==0,checks,captures,captureErrors,directory=dir,scope="Synthetic WPF/native intake, source flags, skip/request and layout. No downloads, package execution, installation or device changes."},new JsonSerializerOptions{WriteIndented=true});
        await File.WriteAllTextAsync(Path.Combine(dir,"result.json"),result);return result;
    }
    private static async Task Capture(MainWindow w,string dir,string name,List<string> captures,List<string> errors){((ScrollViewer)w.FindName("PageScroll")).ScrollToTop();await Flush(w);var path=Path.Combine(dir,name+".png");try{await BoilerUiChecks.CaptureAsync((FrameworkElement)w.Content,path);captures.Add(path);}catch(IOException e)when(e.Message.Contains("solidProbeVisiblePixels=0")){errors.Add(name+": "+e.Message);}}
    private static void NativeDrop(MainWindow w,string path){var bytes=Encoding.Unicode.GetBytes(path+"\0\0");var h=GlobalAlloc(0x0042,(nuint)(20+bytes.Length));var p=GlobalLock(h);Marshal.WriteInt32(p,0,20);Marshal.WriteInt32(p,16,1);Marshal.Copy(bytes,0,IntPtr.Add(p,20),bytes.Length);GlobalUnlock(h);SendMessage(new WindowInteropHelper(w).Handle,0x233,h,IntPtr.Zero);}
    [DllImport("kernel32.dll")]private static extern IntPtr GlobalAlloc(uint f,nuint b);
    [DllImport("kernel32.dll")]private static extern IntPtr GlobalLock(IntPtr h);
    [DllImport("kernel32.dll")]private static extern bool GlobalUnlock(IntPtr h);
    [DllImport("user32.dll")]private static extern IntPtr SendMessage(IntPtr w,uint m,IntPtr p,IntPtr l);
}
