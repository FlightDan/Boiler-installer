using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace OxpInstaller;

public static class HelperBindingChecks
{
    public static async Task<string> RunAsync()
    {
        var events = new List<string>();
        var root = Path.Combine(InstallerWorkspace.DirectoryPath, "helper-binding-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        HashSet<string> GlobalHelperLogs() => Directory.GetFiles(Path.Combine(Backend.Root, "logs"), "installation-helper-*.log").ToHashSet(StringComparer.OrdinalIgnoreCase);
        void Require(bool value, string name) { if (!value) throw new IOException(name); events.Add(name); }
        async Task Refused(Func<Task> action, string name)
        {
            var beforeLogs = GlobalHelperLogs();
            try { await action(); }
            catch (IOException error) when (error.Message.Contains("digest", StringComparison.OrdinalIgnoreCase) || error.Message.Contains("size", StringComparison.OrdinalIgnoreCase))
            {
                if (!beforeLogs.SetEquals(GlobalHelperLogs())) throw new IOException("Unbound request created a global helper log: " + name);
                events.Add(name + ":no-global-helper-log"); return;
            }
            throw new IOException("Not refused at binding: " + name);
        }
        async Task<(int Exit, string Output)> Child(string executable, params string[] args)
        {
            var start = new ProcessStartInfo(executable) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
            foreach (var argument in args) start.ArgumentList.Add(argument);
            using var process = Process.Start(start) ?? throw new IOException("Check subprocess did not start.");
            var output = process.StandardOutput.ReadToEndAsync(); var error = process.StandardError.ReadToEndAsync();
            await process.WaitForExitAsync();
            return (process.ExitCode, await output + await error);
        }
        var source = new MediaSource(Path.Combine(Backend.Root, "output", "binding-nonexistent-" + Guid.NewGuid().ToString("N") + ".img"), 16 * 1024 * 1024, 1, 1, 1, Guid.NewGuid());
        var usb = new UsbDisk(9876, "binding-test", "binding-test", "binding-test", source.Length, 512, "USB", "GPT", false, false, false, false, "Healthy", ["Online"], []);
        var selection = new PartitionSelection("[]", true, 9876, 1048576, 64L * 1024 * 1024 * 1024);
        var nonce = Guid.NewGuid().ToString("N");
        var cases = new (string Purpose, string Prefix, string Name, object Job, Func<string, string, Task<int>> Run)[] {
            (HelperRequestBinding.Partition, "partition-deploy-", "partition-job.json", new PartitionJob(1, nonce, "new-virtual-disk-only", InstallationTargetKind.ExternalDisk, source), InstallationController.ExecuteHelperAsync),
            (HelperRequestBinding.Media, "media-", "media-job.json", new MediaJob(1, "virtual-media-validation", nonce, source, source.Length, "erase-new-virtual-medium"), MediaHelper.ExecuteAsync),
            (HelperRequestBinding.UsbReview, "usb-review-", "usb-review-job.json", new UsbReviewJob(1, "usb-native-readonly-review", nonce, source, usb), UsbNativeReview.ExecuteAsync),
            (HelperRequestBinding.Delete, "partition-delete-", "delete-request.json", new PartitionDeleteRequest("[]", true, 9876, 1048576, "en-US"), PartitionDeletion.ExecuteHelperAsync),
            (HelperRequestBinding.Format, "partition-format-", "format-request.json", new RemainingFormatRequest(selection, "en-US"), RemainingSpaceFormat.ExecuteHelperAsync)
        };
        foreach (var item in cases)
        {
            var dir = Path.Combine(root, item.Prefix + nonce); Directory.CreateDirectory(dir);
            var path = Path.Combine(dir, item.Name);
            var original = JsonSerializer.SerializeToUtf8Bytes(item.Job, item.Job.GetType());
            var digest = HelperRequestBinding.Compute(item.Purpose, original);
            File.WriteAllBytes(path, original);
            foreach (var bad in new[] { "", "abc", new string('A', 64), new string('g', 64), new string('0', 63), new string('0', 65), new string('0', 64) })
                await Refused(async () => { await item.Run(path, bad); }, item.Purpose + ":missing-malformed-wrong-digest-" + bad.Length + "-" + (bad.Length == 0 ? "empty" : bad[0]));
            foreach (var field in new[] { "disk", "offset", "source", "selection" })
            {
                var changed = JsonNode.Parse(original)!.AsObject();
                // Preserve schema and nonce and alter the same real field when present.
                switch (field)
                {
                    case "disk": if (changed["Reviewed"] is JsonObject reviewed) reviewed["Number"] = 9877; else if (changed["Selection"] is JsonObject selected) selected["Disk"] = 9877; else changed["Disk"] = 9877; break;
                    case "offset": if (changed["Selection"] is JsonObject offsetSelection) offsetSelection["Start"] = 2097152; else changed["Start"] = 2097152; break;
                    case "source": if (changed["Source"] is JsonObject image) image["Path"] = source.Path + ".replaced"; else changed["Snapshot"] = "[{}]"; break;
                    case "selection": if (changed["Selection"] is JsonObject placementSelection) placementSelection["AlignRight"] = true; else changed["Capacity"] = source.Length + 512; break;
                }
                File.WriteAllText(path, changed.ToJsonString());
                await Refused(async () => { await item.Run(path, digest); }, item.Purpose + ":same-schema-nonce-tampered-" + field);
            }
            File.WriteAllBytes(path, original[..(original.Length / 2)]);
            await Refused(async () => { await item.Run(path, digest); }, item.Purpose + ":truncated-before-output");
            File.WriteAllBytes(path, []);
            await Refused(async () => { await item.Run(path, digest); }, item.Purpose + ":empty-before-output");
            File.WriteAllBytes(path, new byte[(item.Purpose is HelperRequestBinding.Delete or HelperRequestBinding.Format ? HelperRequestBinding.SnapshotLimit : HelperRequestBinding.SmallLimit) + 1]);
            await Refused(async () => { await item.Run(path, digest); }, item.Purpose + ":oversized-before-output");
            File.WriteAllBytes(path, original.Concat(new byte[] { 32 }).ToArray());
            await Refused(async () => { await item.Run(path, digest); }, item.Purpose + ":semantically-identical-whitespace-rejected");
            File.WriteAllBytes(path, original);
            Require(Directory.GetFiles(dir).Length == 1, item.Purpose + ":all-binding-rejections-created-no-status-results-or-target");
            var helperSwitch = item.Purpose switch { HelperRequestBinding.Partition => "--partition-helper", HelperRequestBinding.Media => "--media-helper", HelperRequestBinding.UsbReview => "--usb-review-helper", HelperRequestBinding.Delete => "--delete-partition-helper", _ => "--format-remaining-helper" };
            foreach (var extra in new[] { Array.Empty<string>(), new[] { "bad-digest" } })
            {
                var launch = new[] { Backend.AppAssemblyPath, helperSwitch, path }.Concat(extra).ToArray();
                var beforeLogs = GlobalHelperLogs();
                var child = await Child(Path.Combine(Backend.Root, "runtime/dotnet/dotnet.exe"), launch);
                Require(child.Exit == 1 && Directory.GetFiles(dir).Length == 1 && beforeLogs.SetEquals(GlobalHelperLogs()), item.Purpose + ":real-cli-rejects-" + (extra.Length == 0 ? "legacy-missing-digest" : "malformed-digest") + "-before-task-output-or-global-log");
            }
            if (item.Purpose is HelperRequestBinding.Delete or HelperRequestBinding.Format)
            {
                var script = Path.Combine(Backend.Root, "scripts", item.Purpose == HelperRequestBinding.Delete ? "delete-partition.ps1" : "format-remaining.ps1");
                var ps = Path.Combine(Environment.SystemDirectory, "WindowsPowerShell", "v1.0", "powershell.exe");
                var args = new[] { "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script, "-RequestPath", path };
                foreach (var variant in new[] { "missing", "malformed", "mismatch", "valid" })
                {
                    var scriptArgs = variant == "missing" ? args : args.Concat(new[] { "-ExpectedDigest", variant == "malformed" ? "bad" : variant == "mismatch" ? new string('0', 64) : digest }).ToArray();
                    var child = await Child(ps, scriptArgs);
                    var expected = variant == "valid" ? "Example or incomplete" : variant == "mismatch" ? "digest mismatch" : "invalid helper request digest";
                    Require(child.Exit != 0 && child.Output.Contains(expected, StringComparison.OrdinalIgnoreCase) && Directory.GetFiles(dir).Length == 1,
                        item.Purpose + ":windows-powershell-5-script-" + variant + "-refused-before-disk-discovery");
                }
            }
            using (var lease = HelperRequestBinding.Create(Path.Combine(dir, "lease.json"), item.Purpose, item.Job))
            {
                var leased = Path.Combine(dir, "lease.json");
                Require(HelperRequestBinding.ReadVerified(leased, item.Purpose, lease.Digest, HelperRequestBinding.SnapshotLimit).AsSpan().SequenceEqual(original), item.Purpose + ":original-serialized-bytes-roundtrip-with-parent-lease");
                foreach (var action in new Action[] { () => { using var file = File.Open(leased, FileMode.Open, FileAccess.Write, FileShare.ReadWrite); }, () => File.Delete(leased), () => File.Move(leased, leased + ".moved") })
                {
                    var refused = false; try { action(); } catch (IOException) { refused = true; }
                    Require(refused, item.Purpose + ":windows-share-denies-write-delete-or-rename");
                }
                await Refused(() => Task.Run(() => HelperRequestBinding.ReadVerified(leased, item.Purpose == HelperRequestBinding.Media ? HelperRequestBinding.Partition : HelperRequestBinding.Media, lease.Digest, HelperRequestBinding.SnapshotLimit)), item.Purpose + ":cross-purpose-refused");
            }
            using var partitionSignal = item.Purpose == HelperRequestBinding.Partition ? new EventWaitHandle(false, EventResetMode.ManualReset, InstallationController.EventName(nonce)) : null;
            using var mediaSignal = item.Purpose == HelperRequestBinding.Media ? new EventWaitHandle(false, EventResetMode.ManualReset, MediaHelper.EventName(nonce)) : null;
            // Safe valid bindings enter normal validation but cannot access a target:
            // nonexistent image for media/partition/USB, explicit example mode for dialogs.
            Require(await item.Run(path, digest) == 1, item.Purpose + ":valid-binding-reaches-safe-terminal-validation");
            Require(!File.Exists(Path.Combine(dir, "media.vhd")) && !File.Exists(Path.Combine(dir, "format-result.json")), item.Purpose + ":normal-safe-validation-no-disk-action");
            var childDir = Path.Combine(root, "normal-cli", item.Prefix + nonce); Directory.CreateDirectory(childDir);
            var childPath = Path.Combine(childDir, item.Name);
            using (var childLease = HelperRequestBinding.Create(childPath, item.Purpose, item.Job))
            {
                var child = await Child(Path.Combine(Backend.Root, "runtime/dotnet/dotnet.exe"), Backend.AppAssemblyPath, helperSwitch, childPath, childLease.Digest);
                var terminal = item.Purpose == HelperRequestBinding.Partition ? "helper-result.json" : item.Purpose is HelperRequestBinding.Delete or HelperRequestBinding.Format ? "error.txt" : "result.json";
                Require(child.Exit == 1 && File.Exists(Path.Combine(childDir, terminal)), item.Purpose + ":normal-three-argument-cli-consumes-bound-request-and-records-safe-terminal-status");
            }
            if (partitionSignal != null || mediaSignal != null)
            {
                partitionSignal?.Set(); mediaSignal?.Set();
                var cancelledDir = Path.Combine(root, "cancelled-cli", item.Prefix + nonce); Directory.CreateDirectory(cancelledDir);
                var cancelledPath = Path.Combine(cancelledDir, item.Name);
                using var cancelledLease = HelperRequestBinding.Create(cancelledPath, item.Purpose, item.Job);
                var child = await Child(Path.Combine(Backend.Root, "runtime/dotnet/dotnet.exe"), Backend.AppAssemblyPath, helperSwitch, cancelledPath, cancelledLease.Digest);
                var terminal = item.Purpose == HelperRequestBinding.Partition ? "helper-result.json" : "result.json";
                using var outcome = JsonDocument.Parse(File.ReadAllText(Path.Combine(cancelledDir, terminal)));
                Require(child.Exit == 2 && outcome.RootElement.GetProperty("Status").GetString() == "cancelled" && !File.Exists(Path.Combine(cancelledDir, "media.vhd")), item.Purpose + ":verified-cli-request-cancelled-before-admin-source-or-target");
            }
        }
        var noncanonicalPath = Path.Combine(root, "noncanonical.json");
        var noncanonical = Encoding.UTF8.GetBytes(" \n{ \"Disk\" : 42 }\r\n"); File.WriteAllBytes(noncanonicalPath, noncanonical);
        var approvedBytes = HelperRequestBinding.ReadVerified(noncanonicalPath, HelperRequestBinding.Media, HelperRequestBinding.Compute(HelperRequestBinding.Media, noncanonical), HelperRequestBinding.SmallLimit);
        Require(approvedBytes.AsSpan().SequenceEqual(noncanonical) && HelperRequestBinding.Parse<JsonElement>(approvedBytes).GetProperty("Disk").GetInt32() == 42, "valid-noncanonical-json-verifies-and-parses-exact-committed-bytes");
        var statePath = Path.Combine(root, "state.json");
        var stateBytes = Encoding.UTF8.GetBytes("{\"schema\":2,\"attempt\":1}"); File.WriteAllBytes(statePath, stateBytes);
        var stateDigest = HelperRequestBinding.Compute(HelperRequestBinding.Recovery, stateBytes);
        var snapshot = HelperRequestBinding.ReadVerified(statePath, HelperRequestBinding.Recovery, stateDigest, HelperRequestBinding.SmallLimit);
        File.WriteAllText(statePath, "changed");
        await Refused(() => Task.Run(() => HelperRequestBinding.ReadVerified(statePath, HelperRequestBinding.Recovery, stateDigest, HelperRequestBinding.SmallLimit)), "recovery-state-replacement-refused");
        Require(HelperRequestBinding.Parse<JsonElement>(snapshot).GetProperty("attempt").GetInt32() == 1, "verified-recovery-snapshot-remains-original-after-path-replacement");
        var recoveryDir = Path.Combine(root, "recovery-helper", "partition-deploy-" + nonce); Directory.CreateDirectory(recoveryDir);
        var recoveryPath = Path.Combine(recoveryDir, "partition-job.json");
        using (var lease = HelperRequestBinding.Create(recoveryPath, HelperRequestBinding.Partition,
            new PartitionJob(1, nonce, "resume-owned-vhd-only", InstallationTargetKind.ExternalDisk, source, statePath, RecoveryDigest: stateDigest)))
        {
            await Refused(async () => { await InstallationController.ExecuteHelperAsync(recoveryPath, lease.Digest); }, "partition-helper-recovery-tamper-refused-before-admin-event-or-output");
            Require(Directory.GetFiles(recoveryDir).Length == 1, "recovery-tamper-created-no-helper-status-or-target");
        }
        File.WriteAllBytes(statePath + ".tmp", Encoding.UTF8.GetBytes("{\"schema\":2,\"attempt\":2}")); File.Move(statePath + ".tmp", statePath, true);
        Require(File.ReadAllText(statePath).Contains("\"attempt\":2"), "recovery-path-remains-available-for-atomic-state-evolution");
        var cancelPath = Path.Combine(root, "cancelled.json");
        var cancelled = false;
        try { using var lease = HelperRequestBinding.Create(cancelPath, HelperRequestBinding.Media, new { }, new CancellationToken(true)); }
        catch (OperationCanceledException) { cancelled = true; }
        Require(cancelled && !File.Exists(cancelPath), "pre-cancel-creates-no-request");
        var creationPath = Path.Combine(root, "existing.json"); File.WriteAllText(creationPath, "preserve existing request");
        var existingRefused = false;
        try { using var lease = HelperRequestBinding.Create(creationPath, HelperRequestBinding.Media, new { Disk = 42 }); }
        catch (IOException) { existingRefused = true; }
        Require(existingRefused && File.ReadAllText(creationPath) == "preserve existing request", "creation-never-overwrites-existing-request");
        // A renamed parent directory can redirect a pathname on Windows. The original
        // lease still pins its file; a different request at the old path must fail.
        var oldDir = Path.Combine(root, "replace-directory"); Directory.CreateDirectory(oldDir);
        var movedDir = oldDir + ".moved"; var replacementPath = Path.Combine(oldDir, "job.json");
        using (var lease = HelperRequestBinding.Create(replacementPath, HelperRequestBinding.Media, new { Disk = 1 }))
        {
            try { Directory.Move(oldDir, movedDir); }
            catch (IOException) { events.Add("windows-refused-parent-directory-rename-with-live-lease"); }
            if (Directory.Exists(movedDir))
            {
                Directory.CreateDirectory(oldDir); File.WriteAllText(replacementPath, "{\"Disk\":2}");
                await Refused(() => Task.Run(() => HelperRequestBinding.ReadVerified(replacementPath, HelperRequestBinding.Media, lease.Digest, HelperRequestBinding.SmallLimit)), "parent-directory-replacement-refused-by-original-commitment");
            }
        }
        var report = Path.Combine(root, "checks.json");
        File.WriteAllText(report, JsonSerializer.Serialize(new { passed = true, events, scope = "Windows file sharing and five helper binding entry points; safe validation failures only, no physical disk writes, no normal-user UAC acceptance. Native owned-VHD recovery snapshot propagation is covered separately by native-partition-recovery-checks." }, new JsonSerializerOptions { WriteIndented = true }));
        return report;
    }
}
