using System.Diagnostics;
using System.Formats.Tar;
using System.IO;
using System.IO.Compression;
using System.Text;
using System.Text.Json;

namespace OxpInstaller;

internal static class InputDiscoveryChecks
{
    internal static async Task<string> RunBindingAsync()
    {
        var external = Path.Combine(Path.GetTempPath(), "boiler-external-source-" + Guid.NewGuid().ToString("N") + ".raucb");
        var directory = Path.Combine(InstallationController.Work, "input-binding-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(directory);
        var checks = new List<string>();
        void Require(bool value, string name) { if (!value) throw new IOException(name); checks.Add(name); }
        try
        {
            var payload = Encoding.UTF8.GetBytes("owned source-binding fixture; classification is tested separately\n");
            await File.WriteAllBytesAsync(external, payload);
            var file = new FileInfo(external);
            var binding = new OfficialSourceBinding(external, Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(payload)).ToLowerInvariant(), payload.Length, file.LastWriteTimeUtc.Ticks);
            Require(OfficialSystem.SourceKey(external).StartsWith(file.FullName + "|", StringComparison.OrdinalIgnoreCase), "external-local-source-has-a-stable-key");
            var snapshot = await OfficialSystem.SnapshotBoundSourceAsync(binding, directory, CancellationToken.None);
            Require((await File.ReadAllBytesAsync(snapshot)).SequenceEqual(payload), "external-input-snapshot-matches-reviewed-sha256");
            var before = Directory.GetFiles(directory, "official-source-*").Length;
            var changed = payload.ToArray(); changed[^2] ^= 1; await File.WriteAllBytesAsync(external, changed); File.SetLastWriteTimeUtc(external, new DateTime(binding.LastWriteUtcTicks, DateTimeKind.Utc));
            var rejected = false;
            try { await OfficialSystem.SnapshotBoundSourceAsync(binding, directory, CancellationToken.None); } catch (IOException) { rejected = true; }
            Require(rejected && Directory.GetFiles(directory, "official-source-*").Length == before, "preserved-size-and-time-cannot-bypass-full-file-digest");
            await File.WriteAllBytesAsync(external, payload); File.SetLastWriteTimeUtc(external, new DateTime(binding.LastWriteUtcTicks, DateTimeKind.Utc));
            var cancelled = false;
            try { await OfficialSystem.SnapshotBoundSourceAsync(binding, directory, new CancellationToken(true)); } catch (OperationCanceledException) { cancelled = true; }
            Require(cancelled && Directory.GetFiles(directory, "official-source-*").Length == before, "cancelled-external-snapshot-leaves-no-partial-output");
            rejected = false;
            try { await OfficialSystem.InspectPreparedSourceAsync(CancellationToken.None, external); } catch (IOException) { rejected = true; }
            Require(rejected, "external-associated-source-is-content-checked-before-review-binding");
            return JsonSerializer.Serialize(new { passed = true, checks, directory, scope = "Synthetic external-file SHA binding and cleanup; content formats are tested separately. No disk installation, download or device access." });
        }
        finally
        {
            if (File.Exists(external) && Path.GetFileName(external).StartsWith("boiler-external-source-", StringComparison.Ordinal)) File.Delete(external);
        }
    }
    internal static async Task<string> RunAsync()
    {
        var directory = Path.Combine(Path.GetTempPath(), "boiler-input-discovery-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        var checks = new List<string>();
        void Require(bool passed, string label) { if (!passed) throw new IOException("Input discovery check failed: " + label); checks.Add(label); }
        string Write(string name, byte[] bytes) { var path = Path.Combine(directory, name); File.WriteAllBytes(path, bytes); return path; }
        string Text(string name, string text) => Write(name, Encoding.UTF8.GetBytes(text));
        async Task<DiscoveredInput> Kind(string path, InputKind expected, string label)
        {
            var value = await InputDiscovery.InspectAsync(path, CancellationToken.None);
            Require(value.Kind == expected && (expected == InputKind.Unknown ? value.Error != null : value.Error == null), label + ": " + value.Error);
            return value;
        }
        string Zip(string name, params (string Name, byte[] Data)[] entries)
        {
            var path = Path.Combine(directory, name);
            using var file = new FileStream(path, FileMode.CreateNew);
            using var zip = new ZipArchive(file, ZipArchiveMode.Create);
            foreach (var entry in entries) { using var stream = zip.CreateEntry(entry.Name).Open(); stream.Write(entry.Data); }
            return path;
        }
        var model = Encoding.UTF8.GetBytes("""
            {"schema_version":1,"kind":"DevicePackage","id":"device:fixture","version":"1.0.0","name":"Fixture model","source":{"kind":"local","path":"fixture"},"files":[],"components":[]}
            """);
        try
        {
            var recoveryBytes = RecoveryImageChecks.Fixture();
            var recovery = Write("actually-recovery.raucb", recoveryBytes);
            var recovered = await Kind(recovery, InputKind.RecoveryImage, "raw-gpt-content-overrides-extension");
            await Kind(Write("ab-without-extension", RecoveryImageChecks.Fixture(prepared: true)), InputKind.DiskImage, "eight-role-gpt-recognized");
            var corrupt = recoveryBytes.ToArray(); corrupt[528] ^= 1;
            await Kind(Write("corrupt.img", corrupt), InputKind.Unknown, "raw-gpt-crc-rejected");
            await Kind(Zip("renamed.archive", ("system.img", recoveryBytes)), InputKind.RecoveryImage, "zip-content-overrides-extension");
            await Kind(Zip("valid-ab.zip", ("system.img", RecoveryImageChecks.Fixture(prepared: true))), InputKind.DiskImage, "zipped-ab-prefix-recognized");
            await Kind(Zip("fake-img.zip", ("system.img", Encoding.UTF8.GetBytes("not an image"))), InputKind.Unknown, "zip-image-content-must-be-gpt");
            await Kind(Zip("internal-name.zip", ("system.data", recoveryBytes)), InputKind.Unknown, "unsupported-inner-image-name-is-explicit");
            await Kind(Zip("traversal.zip", ("../escape", model)), InputKind.Unknown, "zip-traversal-rejected");
            await Kind(Zip("duplicate.zip", ("manifest.json", model), ("MANIFEST.JSON", model)), InputKind.Unknown, "zip-case-alias-rejected");
            await Kind(Write("truncated.zip", "PK invalid"u8.ToArray()), InputKind.Unknown, "bounded-zip-directory-rejects-corruption");
            await Kind(Zip("model.data", ("manifest.json", model)), InputKind.DevicePackage, "device-manifest-in-zip");
            await Kind(Zip("wrapped.data", ("model/manifest.json", model)), InputKind.DevicePackage, "single-package-wrapper");
            await Kind(Zip("wrapper-extra.zip", ("model/manifest.json", model), ("unlisted", [])), InputKind.Unknown, "wrapper-extra-file-rejected");
            var folder = Path.Combine(directory, "model-folder"); Directory.CreateDirectory(folder); File.WriteAllBytes(Path.Combine(folder, "manifest.json"), model);
            var folderInput = await Kind(folder, InputKind.DevicePackage, "device-folder-metadata");
            Text("model-folder/extra", "unlisted"); await Kind(folder, InputKind.Unknown, "folder-extra-file-rejected"); File.Delete(Path.Combine(folder, "extra"));
            var script = Text("script-disguised.img", "#!/bin/bash\n# boiler-script: 1\n# boiler-name: Test model\nexit 0\n");
            var scriptInput = await Kind(script, InputKind.DeviceScript, "script-contract-not-extension");
            Require(scriptInput.Name == "Test model", "script-name-declaration");
            await Kind(Text("ordinary.sh", "#!/bin/bash\necho not-a-boiler-script\n"), InputKind.Unknown, "ordinary-shell-not-auto-approved");
            await Kind(Text("crlf.sh", "#!/bin/bash\r\n# boiler-script: 1\r\n"), InputKind.Unknown, "crlf-script-rejected");
            await Kind(Text("unknown.img", "arbitrary bytes"), InputKind.Unknown, "extension-is-not-evidence");
            await Kind(@"\\server\share\image.img", InputKind.Unknown, "unc-input-rejected");
            await Kind(@"\\.\PhysicalDrive0", InputKind.Unknown, "physical-device-path-rejected");
            await Kind(Path.Combine(directory, "CON.txt"), InputKind.Unknown, "reserved-device-name-rejected-before-open");
            await Kind(script + ":stream", InputKind.Unknown, "alternate-stream-rejected");
            InputDiscovery.VerifyStamp(recovered); checks.Add("unchanged-header-stamp-accepted");
            File.SetLastWriteTimeUtc(recovery, File.GetLastWriteTimeUtc(recovery).AddSeconds(5));
            try { InputDiscovery.VerifyStamp(recovered); throw new InvalidOperationException("Expected stale stamp rejection"); }
            catch (IOException) { checks.Add("changed-time-invalidates-preview"); }
            var sameLength = InputDiscovery.ReadStamp(script);
            var scriptBytes = File.ReadAllBytes(script); scriptBytes[^2] ^= 1; File.WriteAllBytes(script, scriptBytes); File.SetLastWriteTimeUtc(script, new DateTime(sameLength.LastWriteUtcTicks, DateTimeKind.Utc));
            Require(InputDiscovery.ReadStamp(script) != sameLength, "header-change-detected-even-with-preserved-time-and-size");
            File.AppendAllText(Path.Combine(folder, "manifest.json"), " ");
            try { InputDiscovery.VerifyStamp(folderInput); throw new InvalidOperationException("Expected changed manifest rejection"); }
            catch (IOException) { checks.Add("folder-manifest-change-invalidates-preview"); }
            var cancel = new CancellationToken(true);
            try { await InputDiscovery.InspectAsync(recovery, cancel); throw new IOException("Expected cancellation"); }
            catch (OperationCanceledException) { checks.Add("cancel-propagates-without-unknown-row"); }
            var rauc = Encoding.UTF8.GetBytes("[update]\ncompatible=steamos-amd64\nversion=3.10.0\n[image.rootfs]\nfilename=rootfs.img.caibx\n");
            var parsedRauc = InputDiscovery.ParseRaucManifest(rauc);
            Require(parsedRauc.Version == "3.10.0" && parsedRauc.Compatible == "steamos-amd64"
                && parsedRauc.RootfsFilename == "rootfs.img.caibx" && parsedRauc.IsSupportedByCurrentInstaller,
                "rauc-manifest-version-and-required-fields-are-retained");
            InputDiscovery.CheckRaucManifest(rauc); checks.Add("supported-rauc-declaration-passes-support-gate");
            var shortVersion = InputDiscovery.ParseRaucManifest(Encoding.UTF8.GetBytes(Encoding.UTF8.GetString(rauc).Replace("version=3.10.0", "version=3.10")));
            Require(shortVersion.Version == "3.10" && shortVersion.IsSupportedByCurrentInstaller, "supported-two-part-version-is-retained");
            var unsupportedRauc = InputDiscovery.ParseRaucManifest(Encoding.UTF8.GetBytes(Encoding.UTF8.GetString(rauc).Replace("version=3.10.0", "version=3.8.14")));
            Require(unsupportedRauc.Version == "3.8.14" && !unsupportedRauc.IsSupportedByCurrentInstaller
                && InputDiscovery.UnsupportedRaucVersionError(unsupportedRauc.Version).Contains("3.8.14", StringComparison.Ordinal),
                "unsupported-rauc-version-remains-detectable-and-explicitly-blocked");
            try { InputDiscovery.CheckRaucManifest(Encoding.UTF8.GetBytes(Encoding.UTF8.GetString(rauc).Replace("version=3.10.0", "version=3.8.14"))); throw new InvalidOperationException("Expected unsupported version rejection"); }
            catch (IOException) { checks.Add("unsupported-rauc-version-fails-the-install-support-gate"); }
            try { InputDiscovery.ParseRaucManifest(Encoding.UTF8.GetBytes(Encoding.UTF8.GetString(rauc).Replace("version=3.10.0", "version=3.bad"))); throw new InvalidOperationException("Expected malformed version rejection"); }
            catch (IOException) { checks.Add("malformed-rauc-version-rejected"); }
            try { InputDiscovery.ParseRaucManifest(Encoding.UTF8.GetBytes(Encoding.UTF8.GetString(rauc).Replace("filename=rootfs.img.caibx", "filename=other.img"))); throw new InvalidOperationException("Expected invalid rootfs entry rejection"); }
            catch (IOException) { checks.Add("unexpected-rauc-rootfs-entry-rejected"); }
            try { InputDiscovery.ParseRaucManifest(Encoding.UTF8.GetBytes(Encoding.UTF8.GetString(rauc).Replace("compatible=steamos-amd64", "compatible=other-os"))); throw new InvalidOperationException("Expected incompatible update rejection"); }
            catch (IOException) { checks.Add("incompatible-rauc-update-rejected"); }
            try { InputDiscovery.ParseRaucManifest(Encoding.UTF8.GetBytes(Encoding.UTF8.GetString(rauc).Replace("version=3.10.0", "version=3.8.14\nversion=3.10.0"))); throw new InvalidOperationException("Expected duplicate update version rejection"); }
            catch (IOException) { checks.Add("duplicate-rauc-key-rejected"); }
            var rawRoot = Path.Combine(directory, "root-partition.data");
            using (var root = new FileStream(rawRoot, FileMode.CreateNew))
            { root.SetLength(OfficialSystem.RootSize); root.Position = 0x10040; root.Write("_BHRfS_M"u8); }
            var rootInput = await Kind(rawRoot, InputKind.UpgradeRoot, "5gib-btrfs-root-recognized-without-copying");
            Require(rootInput.RaucManifest == null && rootInput.Detail.Contains("安装准备", StringComparison.Ordinal)
                && !rootInput.Detail.Contains(OfficialSystem.Version, StringComparison.Ordinal)
                || rootInput.RaucManifest == null && rootInput.Detail.Contains("installation preparation", StringComparison.OrdinalIgnoreCase)
                && !rootInput.Detail.Contains(OfficialSystem.Version, StringComparison.Ordinal), "root-version-is-not-guessed-before-preparation");
            File.Delete(rawRoot);
            var tarPath = Path.Combine(directory, "model.tar");
            using (var output = new FileStream(tarPath, FileMode.CreateNew))
            using (var tar = new TarWriter(output))
                tar.WriteEntry(new PaxTarEntry(TarEntryType.RegularFile, "manifest.json") { DataStream = new MemoryStream(model) });
            await Kind(tarPath, InputKind.DevicePackage, "bounded-tar-package");
            var gzipPath = Path.Combine(directory, "gzip-without-extension");
            using (var input = File.OpenRead(tarPath))
            using (var output = File.Create(gzipPath))
            using (var gzip = new GZipStream(output, CompressionLevel.Fastest)) await input.CopyToAsync(gzip);
            await Kind(gzipPath, InputKind.DevicePackage, "gzip-tar-content-not-extension");
            var executable = Path.Combine(Backend.Root, "runtime/archive/7z.exe");
            if (File.Exists(executable))
            {
                // Compress only our disposable fixtures with the bundled tool.
                var bzip = Path.Combine(directory, "recovery.data");
                var start = new ProcessStartInfo(executable) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
                foreach (var arg in new[] { "a", "-tbzip2", "-bd", bzip, recovery }) start.ArgumentList.Add(arg);
                using var process = Process.Start(start)!;
                var outputTask = process.StandardOutput.ReadToEndAsync(); var errorTask = process.StandardError.ReadToEndAsync();
                await process.WaitForExitAsync(); await outputTask; await errorTask;
                Require(process.ExitCode == 0, "owned-bzip-fixture-created");
                await Kind(bzip, InputKind.RecoveryImage, "bounded-bzip-gpt-prefix");
                var xz = Path.Combine(directory, "model-compressed.data");
                var xzStart = new ProcessStartInfo(executable) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
                foreach (var arg in new[] { "a", "-txz", "-bd", xz, tarPath }) xzStart.ArgumentList.Add(arg);
                using var xzProcess = Process.Start(xzStart)!;
                var xzOutput = xzProcess.StandardOutput.ReadToEndAsync(); var xzError = xzProcess.StandardError.ReadToEndAsync();
                await xzProcess.WaitForExitAsync(); await xzOutput; await xzError;
                Require(xzProcess.ExitCode == 0, "owned-xz-tar-fixture-created");
                await Kind(xz, InputKind.DevicePackage, "bounded-xz-tar-model");
            }
            return JsonSerializer.Serialize(new { passed = true, checks, scope = "Disposable content classification fixtures only; no UI, network, package execution, installation or device access. Header stamps are not full-file authentication." });
        }
        finally
        {
            // Only the fresh test directory is owned by this check.
            if (Directory.Exists(directory) && new DirectoryInfo(directory).Name.StartsWith("boiler-input-discovery-", StringComparison.Ordinal)) Directory.Delete(directory, true);
        }
    }
}
