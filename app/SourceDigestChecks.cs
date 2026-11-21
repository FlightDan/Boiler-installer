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
    private static Task Prepare(MainWindow window) => (Task)typeof(MainWindow)
        .GetMethod("PrepareSourceBindingsAsync", PrivateInstance)!.Invoke(window, null)!;
    internal static Task<string> RunAsync() => Application.Current.Dispatcher.CheckAccess() ? RunOnUiAsync()
        : Application.Current.Dispatcher.InvokeAsync(RunOnUiAsync).Task.Unwrap();
    private static async Task<string> RunOnUiAsync()
    {
        var directory = Path.Combine(Backend.Root, "logs", "source-digest-" + DateTime.UtcNow.ToString("yyyyMMddTHHmmssfff"));
        Directory.CreateDirectory(directory);
        var checks = new List<string>();
        void Require(bool condition, string name) { if (!condition) throw new IOException("Source digest check failed: " + name); checks.Add(name); }
        var plain = Path.Combine(directory, "digest.bin"); await File.WriteAllBytesAsync(plain, "abc"u8.ToArray());
        var digest = await SourceFileDigest.ComputeAsync(plain, CancellationToken.None);
        Require(digest.Sha256 == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            "sha256-binding-uses-correct-complete-file-bytes");
        var multichunk = Path.Combine(directory, "multichunk.bin");
        var content = new byte[3 * 1024 * 1024 + 7]; RandomNumberGenerator.Fill(content);
        await File.WriteAllBytesAsync(multichunk, content);
        var complete = await SourceFileDigest.ComputeAsync(multichunk, CancellationToken.None);
        Require(complete.Sha256 == Convert.ToHexString(SHA256.HashData(content)).ToLowerInvariant(),
            "sha256-covers-every-chunk-and-final-partial-buffer");
        using (var cancel = new CancellationTokenSource())
        {
            var cancelled = false;
            cancel.Cancel();
            try { await SourceFileDigest.ComputeAsync(plain, cancel.Token); }
            catch (OperationCanceledException) { cancelled = true; }
            Require(cancelled, "cancellation-before-first-read-returns-no-binding");
        }
        var empty = Path.Combine(directory, "empty.bin"); await File.WriteAllBytesAsync(empty, []);
        var emptyRejected = false;
        try { await SourceFileDigest.ComputeAsync(empty, CancellationToken.None); }
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
            var partitionPicker = (PartitionPicker)window.FindName("PartitionSelector");
            partitionPicker.LoadSnapshot(PartitionPickerChecks.ExampleWithExternalDisk(), true);
            partitionPicker.Disks.SelectedIndex = 1;
            var emptyZip = Path.Combine(directory, "empty.img.zip"); File.WriteAllBytes(emptyZip, []);
            void ClickNext() => ((Button)window.FindName("NextButton")).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            ((TextBox)window.FindName("ImagePath")).Text = emptyZip;
            ClickNext();
            Require(Field<int>(window, "step") == 1 && !(bool)Property(window, "CurrentSourceReviewed")!,
                "empty-archive-is-rejected-on-source-page-without-hashing");
            ((TextBox)window.FindName("ImagePath")).Text = recovery;
            var before = Directory.Exists(InstallationController.Work) ? Directory.GetFileSystemEntries(InstallationController.Work).Order().ToArray() : [];
            ClickNext();
            Require(Field<int>(window, "step") == 0 && (bool)Property(window, "CurrentSourceReviewed")!
                && Property(window, "CurrentSourceDigest") == null && !Field<bool>(window, "sourceInspectionBusy"),
                "imported-source-advances-without-whole-file-hash");
            Require(Property(window, "CurrentImportedCapacity") == null && Field<object?>(window, "inspectedSourceInfo") == null,
                "source-review-does-not-claim-guest-layout-or-capacity-results");
            var after = Directory.Exists(InstallationController.Work) ? Directory.GetFileSystemEntries(InstallationController.Work).Order().ToArray() : [];
            Require(before.SequenceEqual(after) && File.ReadAllBytes(recovery).SequenceEqual(bytes),
                "source-page-does-not-copy-extract-start-a-guest-or-change-input");
            File.SetLastWriteTimeUtc(recovery, File.GetLastWriteTimeUtc(recovery).AddSeconds(2));
            Require(!(bool)Property(window, "CurrentSourceReviewed")!, "changed-image-invalidates-source-review");

            window.BoilerModeForChecks("local");
            ((TextBox)window.FindName("ImagePath")).Text = recovery;
            var panel = Field<OfficialDownloadPanel>(window, "officialDownloadPanel");
            panel.AssociateLocalSource(bundle);
            ClickNext();
            var issue = typeof(MainWindow).GetMethod("SourceIssue", PrivateInstance)!;
            Require(Field<int>(window, "step") == 0 && issue.Invoke(window, [true, true]) == null
                && Property(window, "CurrentSourceDigest") == null && Property(window, "CurrentOfficialSourceBinding") == null,
                "local-pair-advances-without-hashing-either-file");

            var preparation = Prepare(window);
            Require(Field<bool>(window, "sourceInspectionBusy") && !((FrameworkElement)window.FindName("WizardShell")).IsEnabled,
                "binding-preparation-locks-input-and-navigation");
            var previousStep = Field<int>(window, "step");
            ((Button)window.FindName("SourceTab")).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            Require(Field<int>(window, "step") == previousStep, "binding-preparation-prevents-navigation");
            var rejectedReentry = false;
            try { await Prepare(window); } catch (InvalidOperationException) { rejectedReentry = true; }
            Require(rejectedReentry, "binding-preparation-rejects-reentry");
            var closeArgs = new System.ComponentModel.CancelEventArgs();
            typeof(MainWindow).GetMethod("OnClosing", PrivateInstance, null,
                [typeof(object), typeof(System.ComponentModel.CancelEventArgs)], null)!.Invoke(window, [window, closeArgs]);
            var preparationCancelled = false;
            try { await preparation; } catch (OperationCanceledException) { preparationCancelled = true; }
            Require(closeArgs.Cancel && preparationCancelled && !Field<bool>(window, "sourceInspectionBusy")
                && ((FrameworkElement)window.FindName("WizardShell")).IsEnabled
                && Property(window, "CurrentSourceDigest") == null && Property(window, "CurrentOfficialSourceBinding") == null,
                "closing-cancels-binding-preparation-without-creating-a-binding");
            preparation = Prepare(window);
            await preparation;
            var request = (InstallationRequest)Property(window, "Request")!;
            Require(request.ExpectedSourceSha256 == recoverySha && request.OfficialSource?.Sha256 == recoverySha,
                "request-contains-sha256-reference-values-for-installation-snapshots");
            var refusedMissing = false;
            try { InstallationController.ValidateRequest(request with { ExpectedSourceSha256 = null }); } catch (IOException) { refusedMissing = true; }
            Require(refusedMissing, "local-controller-still-rejects-missing-binding");
            File.SetLastWriteTimeUtc(bundle, File.GetLastWriteTimeUtc(bundle).AddSeconds(1));
            Require(Property(window, "CurrentOfficialSourceBinding") == null && issue.Invoke(window, [true, true]) is string,
                "changed-upgrade-source-requires-review-again");
        }
        finally { window?.Close(); }
        var result = JsonSerializer.Serialize(new { passed = true, directory, checks,
            scope = "Source-page navigation, install-time SHA-256 binding and cancellation only; no VM, device write or physical installation acceptance." });
        await File.WriteAllTextAsync(Path.Combine(directory, "result.json"), result);
        return result;
    }
}
