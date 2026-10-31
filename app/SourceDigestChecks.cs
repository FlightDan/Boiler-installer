using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;

namespace OxpInstaller;

internal static class SourceDigestChecks
{
    private static readonly BindingFlags PrivateInstance = BindingFlags.NonPublic | BindingFlags.Instance;
    private static T Field<T>(MainWindow window, string name) => (T)typeof(MainWindow).GetField(name, PrivateInstance)!.GetValue(window)!;
    private static object? Property(MainWindow window, string name) => typeof(MainWindow).GetProperty(name, PrivateInstance)!.GetValue(window);
    private static Task Inspect(MainWindow window, bool official = false) => (Task)typeof(MainWindow)
        .GetMethod(official ? "InspectOfficialSourceAsync" : "InspectSourceAsync", PrivateInstance)!
        .Invoke(window, official ? null : [false])!;

    internal static Task<string> RunAsync() => Application.Current.Dispatcher.CheckAccess() ? RunOnUiAsync()
        : Application.Current.Dispatcher.InvokeAsync(RunOnUiAsync).Task.Unwrap();
    private static async Task<string> RunOnUiAsync()
    {
        var directory = Path.Combine(Backend.Root, "logs", "source-digest-" + DateTime.UtcNow.ToString("yyyyMMddTHHmmssfff"));
        Directory.CreateDirectory(directory);
        var checks = new List<string>();
        void Require(bool condition, string name) { if (!condition) throw new IOException("Source digest check failed: " + name); checks.Add(name); }
        var plain = Path.Combine(directory, "digest.bin"); await File.WriteAllBytesAsync(plain, "abc"u8.ToArray());
        var progress = new List<SourceDigestProgress>();
        var digest = await SourceFileDigest.ComputeAsync(plain, progress.Add, CancellationToken.None);
        Require(digest.Md5 == "900150983cd24fb0d6963f7d28e17f72" && digest.Sha256 == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            "md5-and-existing-sha256-binding-use-correct-complete-file-bytes");
        Require(!digest.ComparedToReference, "calculation-without-reference-never-claims-a-match");
        var shaOnly = await SourceFileDigest.ComputeAsync(plain, null, CancellationToken.None, new string('0', 32), computeMd5: false);
        Require(shaOnly.Md5 == "" && shaOnly.Sha256 == digest.Sha256 && !shaOnly.ComparedToReference,
            "sha-only-uses-known-value-and-does-not-compare-md5");
        Require(progress[0].Completed == 0 && progress[^1].Completed == 3 && progress.All(p => p.Total == 3 && p.BytesPerSecond >= 0),
            "progress-includes-start-completion-actual-byte-count-and-speed");
        var multichunk = Path.Combine(directory, "multichunk.bin");
        var content = new byte[3 * 1024 * 1024 + 7]; RandomNumberGenerator.Fill(content);
        await File.WriteAllBytesAsync(multichunk, content);
        var complete = await SourceFileDigest.ComputeAsync(multichunk, null, CancellationToken.None);
        Require(complete.Md5 == Convert.ToHexString(MD5.HashData(content)).ToLowerInvariant()
            && complete.Sha256 == Convert.ToHexString(SHA256.HashData(content)).ToLowerInvariant(),
            "both-digests-cover-every-chunk-and-final-partial-buffer");
        var matched = await SourceFileDigest.ComputeAsync(plain, null, CancellationToken.None, digest.Md5.ToUpperInvariant());
        Require(matched.ComparedToReference, "explicit-reference-md5-compares-case-insensitively");
        var mismatch = false;
        try { await SourceFileDigest.ComputeAsync(plain, null, CancellationToken.None, new string('0', 32)); }
        catch (IOException) { mismatch = true; }
        Require(mismatch, "mismatched-reference-md5-is-rejected");
        using (var cancel = new CancellationTokenSource())
        {
            var cancelled = false;
            try { await SourceFileDigest.ComputeAsync(plain, _ => cancel.Cancel(), cancel.Token); }
            catch (OperationCanceledException) { cancelled = true; }
            Require(cancelled, "cancellation-before-first-read-returns-no-binding");
        }
        var empty = Path.Combine(directory, "empty.bin"); await File.WriteAllBytesAsync(empty, []);
        var emptyRejected = false;
        try { await SourceFileDigest.ComputeAsync(empty, null, CancellationToken.None); }
        catch (IOException) { emptyRejected = true; }
        Require(emptyRejected, "empty-source-does-not-get-a-completed-check");

        var recovery = Path.Combine(directory, "recovery.img");
        var bytes = RecoveryImageChecks.Fixture(); await File.WriteAllBytesAsync(recovery, bytes);
        var recoverySha = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
        var bundle = Path.Combine(directory, "system.raucb"); await File.WriteAllBytesAsync(bundle, bytes);
        MainWindow? window = null;
        try
        {
            window = new MainWindow(true);
            ((PartitionPicker)window.FindName("PartitionSelector")).AutomaticReadEnabled = false;
            window.BoilerModeForChecks("imported");
            ((RadioButton)window.FindName("ExternalTarget")).IsChecked = true;
            ((TextBox)window.FindName("ImagePath")).Text = recovery;
            var before = Directory.Exists(InstallationController.Work) ? Directory.GetFileSystemEntries(InstallationController.Work).Order().ToArray() : [];
            await Inspect(window);
            await window.Dispatcher.InvokeAsync(() => { }, System.Windows.Threading.DispatcherPriority.ApplicationIdle);
            Require((string?)Property(window, "CurrentSourceDigest") == recoverySha && Field<string>(window, "inspectedSourceMd5") == Convert.ToHexString(MD5.HashData(bytes)).ToLowerInvariant(),
                "source-page-retains-sha256-installation-binding-and-displays-md5");
            var text = Field<TextBlock>(window, "sourceInfoText").Text;
            Require(text.Contains("MD5:") && (text.Contains("未提供参考值") || text.Contains("no reference value")) && !text.Contains("SteamOS"),
                "source-page-does-not-invent-reference-match-version-or-kernel-inspection");
            Require(Property(window, "CurrentImportedCapacity") == null && Field<object?>(window, "inspectedSourceInfo") == null,
                "digest-only-review-does-not-claim-guest-layout-or-capacity-results");
            var after = Directory.Exists(InstallationController.Work) ? Directory.GetFileSystemEntries(InstallationController.Work).Order().ToArray() : [];
            Require(before.SequenceEqual(after) && File.ReadAllBytes(recovery).SequenceEqual(bytes),
                "source-page-does-not-copy-extract-start-a-guest-or-change-input");
            ((Button)window.FindName("NextButton")).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            Require(Field<int>(window, "step") == 0, "cached-digest-only-review-can-advance-to-destination");
            File.SetLastWriteTimeUtc(recovery, File.GetLastWriteTimeUtc(recovery).AddSeconds(1));
            Require(Property(window, "CurrentSourceDigest") == null, "changed-recovery-source-invalidates-reviewed-binding");

            window.PreviewStep(1);
            ((TextBox)window.FindName("ImagePath")).Text = plain;
            var pending = Inspect(window);
            var controls = Field<StackPanel>(window, "sourceDigestControls");
            Require(Field<bool>(window, "sourceInspectionBusy") && controls.Visibility == Visibility.Visible
                && ((Button)controls.Children[1]).IsEnabled, "digest-progress-and-cancel-control-are-available-while-reading");
            ((Button)controls.Children[1]).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            await pending;
            Require((bool)Property(window, "CurrentSourceSkipped")! && !Field<bool>(window, "sourceInspectionBusy") && Property(window, "CurrentSourceDigest") == null
                && controls.Visibility == Visibility.Collapsed, "skip-control-clears-source-binding-and-restores-navigation");
            File.SetLastWriteTimeUtc(plain, File.GetLastWriteTimeUtc(plain).AddSeconds(1));
            Require(!(bool)Property(window, "CurrentSourceSkipped")!, "changed-source-invalidates-skip");
            var originalLanguage = InstallerI18n.Language;
            try
            {
                InstallerI18n.SetLanguage("en-US");
                pending = Inspect(window);
                Require((string)((Button)controls.Children[1]).Content == "Skip calculation", "reused-skip-control-follows-current-language");
                ((Button)controls.Children[1]).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                await pending;
            }
            finally { InstallerI18n.SetLanguage(originalLanguage); }

            var panel = Field<OfficialDownloadPanel>(window, "officialDownloadPanel"); panel.AssociateLocalSource(bundle);
            await Inspect(window, true);
            var official = (OfficialSourceBinding?)Property(window, "CurrentOfficialSourceBinding");
            Require(official?.Sha256 == recoverySha && Field<string>(window, "inspectedOfficialMd5") == Convert.ToHexString(MD5.HashData(bytes)).ToLowerInvariant(),
                "upgrade-source-also-uses-one-digest-read-and-existing-sha256-binding");
            Require(Field<TextBlock>(window, "officialSourceInfoText").Text.Contains("MD5:") && File.ReadAllBytes(bundle).SequenceEqual(bytes),
                "upgrade-source-displays-md5-without-guest-check-or-content-change");
            File.SetLastWriteTimeUtc(bundle, File.GetLastWriteTimeUtc(bundle).AddSeconds(1));
            Require(Property(window, "CurrentOfficialSourceBinding") == null, "changed-upgrade-source-invalidates-reviewed-binding");

            window.BoilerModeForChecks("local");
            ((TextBox)window.FindName("ImagePath")).Text = recovery;
            panel.AssociateLocalSource(bundle);
            var both = (Task)typeof(MainWindow).GetMethod("InspectSourceAsync", PrivateInstance)!.Invoke(window, [true])!;
            ((Button)Field<StackPanel>(window, "sourceDigestControls").Children[1]).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            for (var attempt = 0; attempt < 1000 && (!Field<bool>(window, "sourceInspectionBusy") || Field<StackPanel>(window, "officialDigestControls").Visibility != Visibility.Visible); attempt++)
                await window.Dispatcher.InvokeAsync(() => { }, System.Windows.Threading.DispatcherPriority.Background);
            Require(Field<bool>(window, "sourceInspectionBusy") && Field<StackPanel>(window, "officialDigestControls").Visibility == Visibility.Visible,
                "skipping-recovery-continues-to-upgrade-digest");
            ((Button)Field<StackPanel>(window, "officialDigestControls").Children[1]).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            await both;
            Require((bool)Property(window, "CurrentSourceSkipped")! && (bool)Property(window, "CurrentOfficialSkipped")!
                && Property(window, "CurrentSourceDigest") == null && Property(window, "CurrentOfficialSourceBinding") == null,
                "both-sources-can-be-skipped-independently-in-one-flow");
            var issue = typeof(MainWindow).GetMethod("SourceIssue", PrivateInstance)!;
            Require(issue.Invoke(window, [true, true]) == null, "current-skips-allow-source-navigation");
            await Inspect(window); await Inspect(window, true);
            Require(Property(window, "CurrentSourceDigest") == null && Property(window, "CurrentOfficialSourceBinding") == null,
                "returning-to-source-does-not-repeat-skipped-md5");
            ((Button)window.FindName("NextButton")).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            Require(Field<int>(window, "step") == 0, "both-skips-advance-from-source-page");
            var prepare = typeof(MainWindow).GetMethod("PrepareSkippedBindingsAsync", PrivateInstance)!;
            var preparation = (Task)prepare.Invoke(window, null)!;
            Require(Field<bool>(window, "bindingPreparationBusy") && !((FrameworkElement)window.FindName("WizardShell")).IsEnabled,
                "binding-preparation-locks-input-and-navigation");
            var previousStep = Field<int>(window, "step");
            ((Button)window.FindName("SourceTab")).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            Require(Field<int>(window, "step") == previousStep && Field<StackPanel>(window, "sourceDigestControls").Visibility == Visibility.Collapsed
                && Field<StackPanel>(window, "officialDigestControls").Visibility == Visibility.Collapsed,
                "binding-preparation-prevents-navigation-and-has-no-skip-control");
            var rejectedReentry = false;
            try { await (Task)prepare.Invoke(window, null)!; } catch (InvalidOperationException) { rejectedReentry = true; }
            Require(rejectedReentry, "binding-preparation-rejects-reentry");
            var closeArgs = new System.ComponentModel.CancelEventArgs();
            typeof(MainWindow).GetMethod("OnClosing", PrivateInstance, null,
                [typeof(object), typeof(System.ComponentModel.CancelEventArgs)], null)!.Invoke(window, [window, closeArgs]);
            var preparationCancelled = false;
            try { await preparation; } catch (OperationCanceledException) { preparationCancelled = true; }
            Require(closeArgs.Cancel && preparationCancelled && !Field<bool>(window, "bindingPreparationBusy")
                && ((FrameworkElement)window.FindName("WizardShell")).IsEnabled
                && Property(window, "CurrentSourceDigest") == null && Property(window, "CurrentOfficialSourceBinding") == null,
                "closing-cancels-binding-preparation-without-creating-a-binding");
            preparation = (Task)prepare.Invoke(window, null)!;
            await preparation;
            var request = (InstallationRequest)Property(window, "Request")!;
            Require(request.ExpectedSourceSha256 == recoverySha && request.OfficialSource?.Sha256 == recoverySha,
                "request-contains-real-sha256-bindings-after-skips");
            var refusedMissing = false;
            try { InstallationController.ValidateRequest(request with { ExpectedSourceSha256 = null }); } catch (IOException) { refusedMissing = true; }
            Require(refusedMissing, "local-controller-still-rejects-missing-binding");
            File.SetLastWriteTimeUtc(bundle, File.GetLastWriteTimeUtc(bundle).AddSeconds(1));
            Require(!(bool)Property(window, "CurrentOfficialSkipped")! && Property(window, "CurrentOfficialSourceBinding") == null,
                "changed-upgrade-source-invalidates-skip-and-binding");
            File.Move(bundle, bundle + ".missing");
            try
            {
                var unavailable = false;
                try { await Inspect(window, true); } catch (IOException) { unavailable = true; }
                Require(unavailable && !Field<bool>(window, "sourceInspectionBusy")
                    && Field<StackPanel>(window, "officialDigestControls").Visibility == Visibility.Collapsed,
                    "missing-upgrade-source-does-not-leave-an-active-skip-control");
            }
            finally { File.Move(bundle + ".missing", bundle); }
        }
        finally { window?.Close(); }
        var result = JsonSerializer.Serialize(new { passed = true, directory, checks,
            scope = "Local MD5/SHA256 read, progress, cancellation and UI navigation only; no VM, device write or physical installation acceptance." });
        await File.WriteAllTextAsync(Path.Combine(directory, "result.json"), result);
        return result;
    }
}
