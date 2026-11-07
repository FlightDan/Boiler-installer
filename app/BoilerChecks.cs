using System.IO;
using System.Buffers.Binary;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text.Json;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace OxpInstaller;

internal static class BoilerChecks
{
    internal static async Task<string> RunAsync()
    {
        var directory = Path.Combine(InstallationController.Work, "boiler-checks-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        var checks = new List<string>();
        var visualFailures = new List<string>();
        void Require(bool value, string name) { if (!value) throw new IOException(name); checks.Add(name); }
        async Task Reject(Func<Task> action, string name) { try { await action(); } catch (Exception e) when (e is IOException or InvalidOperationException or JsonException) { checks.Add(name); return; } throw new IOException("Expected rejection: " + name); }
        Require(File.Exists(Backend.AppAssemblyPath) && Path.GetFullPath(Backend.AppAssemblyPath) == Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "OxpInstaller.dll")), "helpers-bind-current-loaded-application-assembly");
        GuestResourcesChecks.Run(Require);
        // The packaged runtime must keep both the software path and WHPX, even
        // when this particular host cannot enter the hypervisor.
        {
            var acceleratorHelp = await Backend.ProcessAsync(
                Path.Combine(Backend.Root, "runtime/qemu-min/qemu-system-x86_64.exe"), ["-accel", "help"], _ => { }, CancellationToken.None);
            Require(acceleratorHelp.Contains("whpx", StringComparison.Ordinal)
                && acceleratorHelp.Contains("tcg", StringComparison.Ordinal), "qemu-runtime-advertises-whpx-and-tcg");
        }
        // The component payload is verified while it is copied, from one held read.
        {
            var catalogPath = Path.Combine(Backend.Root, "cache/integration/manifest.json");
            using var catalog = JsonDocument.Parse(File.ReadAllBytes(catalogPath));
            var smallest = catalog.RootElement.GetProperty("components").EnumerateObject()
                .SelectMany(component => new[] { "files", "licenses", "notices" }.SelectMany(key =>
                    component.Value.TryGetProperty(key, out var entries) ? entries.EnumerateArray().ToArray() : []))
                .Where(entry => entry.GetProperty("size").GetInt64() > 0
                    && File.Exists(Path.Combine(Backend.Root, "cache/integration", entry.GetProperty("source").GetString()!)))
                .OrderBy(entry => entry.GetProperty("size").GetInt64()).First();
            var relative = smallest.GetProperty("source").GetString()!;
            string Stage(string root, bool tamper)
            {
                var source = Path.Combine(root, "integration");
                var copy = Path.Combine(source, relative);
                Directory.CreateDirectory(Path.GetDirectoryName(copy)!);
                File.Copy(Path.Combine(Backend.Root, "cache/integration", relative), copy);
                File.Copy(catalogPath, Path.Combine(source, "manifest.json"));
                if (tamper)
                {
                    var bytes = File.ReadAllBytes(copy); bytes[^1] ^= 0xff; File.WriteAllBytes(copy, bytes);
                }
                return copy;
            }
            var pristine = Path.Combine(directory, "payload-private"); Directory.CreateDirectory(pristine);
            var expected = File.ReadAllBytes(Stage(pristine, tamper: false));
            var published = Path.Combine(directory, "payload-published");
            Backend.CopyIntegrationPayload(pristine, published);
            Require(File.ReadAllBytes(Path.Combine(published, "integration", relative)).SequenceEqual(expected)
                && File.Exists(Path.Combine(published, "integration", "manifest.json")), "component-payload-verified-and-published-in-one-read");
            var tampered = Path.Combine(directory, "payload-tampered"); Directory.CreateDirectory(tampered);
            Stage(tampered, tamper: true);
            var refusedOutput = Path.Combine(directory, "payload-refused");
            await Reject(() => Task.Run(() => Backend.CopyIntegrationPayload(tampered, refusedOutput)), "component-payload-hash-mismatch-refused");
            Require(!File.Exists(Path.Combine(refusedOutput, "integration", relative))
                && !Directory.EnumerateFiles(refusedOutput, "*", SearchOption.AllDirectories).Any(file => file.Contains(".partial-", StringComparison.Ordinal)),
                "component-payload-mismatch-publishes-no-partial-or-verified-bytes");
        }
        using (var advisory = JsonDocument.Parse("""{"hardware_matching":{"mode":"advisory-exact-dmi-v1","pairs":[{"manufacturer":"ONE-NETBOOK","model":"ONEXPLAYER 3"}]}}"""))
        {
            Require(BoilerHardware.Matches(advisory.RootElement, new(" one-netbook ", "onexplayer 3")), "hardware-advice-exact-pair-case-insensitive");
            Require(!BoilerHardware.Matches(advisory.RootElement, new("OTHER", "ONEXPLAYER 3"))
                && !BoilerHardware.Matches(advisory.RootElement, new("ONE-NETBOOK", "ONEXPLAYER 3 Pro")), "hardware-advice-rejects-partial-and-cross-vendor-match");
            Require(!BoilerHardware.Matches(advisory.RootElement, new("ONE-NETBOOK", "ONEXPLAYER 3\n")), "hardware-advice-rejects-control-characters");
        }
        var transport = Path.Combine(directory, "transport"); Directory.CreateDirectory(Path.Combine(transport, "nested"));
        for (var index = 0; index < 5; index++) await File.WriteAllBytesAsync(Path.Combine(transport, "nested", index + ".bin"), Enumerable.Repeat((byte)(index + 1), 20000).ToArray());
        var volumes = await PayloadVolumes.PrepareAsync(transport, Path.Combine(directory, "volumes"), CancellationToken.None, 65536);
        Require(volumes.Length == 3, "payload-splits-beyond-vvfat-capacity");
        var delivered = volumes.SelectMany(volume => Directory.GetFiles(volume, "*", SearchOption.AllDirectories).Select(file => (file, relative: Path.GetRelativePath(volume, file)))).ToArray();
        Require(delivered.Length == 5 && delivered.Select(file => file.relative).Distinct().Count() == 5
            && delivered.All(file => File.ReadAllBytes(file.file).SequenceEqual(File.ReadAllBytes(Path.Combine(transport, file.relative)))), "payload-volumes-preserve-every-file-and-source");
        Require(PayloadVolumes.MountCommand(3).Contains("remount,ro") && PayloadVolumes.MountCommand(3).Contains("boiler-payload-merge") && PayloadVolumes.MountCommand(3).Contains("boiler-payload-2"), "payload-volumes-use-readonly-identified-mounts");
        Require(PayloadVolumes.MountCommand(3).Contains("touch /var/cache/apk/.boiler-apk-prepared")
            && !PayloadVolumes.MountCommand(1).Contains(".boiler-apk-prepared"), "payload-volumes-mark-packages-already-installed");
        foreach (var count in new[] { 1, 3, 8 })
        foreach (var secret in new[] { false, true })
        {
            var mount = PayloadVolumes.MountCommand(count);
            var command = Backend.GuestCommand("integration", mount, secret);
            Require(command.Split('\n').All(line => System.Text.Encoding.UTF8.GetByteCount(line) < 1024)
                && command.EndsWith("; poweroff\n", StringComparison.Ordinal)
                && command.Replace(" &&\n", " && ", StringComparison.Ordinal).Contains(mount),
                $"guest-command-fits-interactive-lines-{count}-volumes-secret-{secret}");
        }
        await Reject(async () => { await PayloadVolumes.PrepareAsync(transport, Path.Combine(directory, "too-small"), CancellationToken.None, 8192); }, "oversized-payload-file-refused");
        if (OperatingSystem.IsWindows())
        {
            var longRoot = Path.Combine(directory, "long-" + new string('x', Math.Clamp(265 - directory.Length, 40, 200)));
            Directory.CreateDirectory(Path.Combine(longRoot, "n"));
            var longFile = Path.Combine(longRoot, "n/item.bin");
            await File.WriteAllBytesAsync(longFile, [1, 2, 3]);
            Require(Path.GetFullPath(longFile).Length >= 260, "vvfat-deep-path-fixture-reaches-maxpath");
            var shortened = await PayloadVolumes.PrepareAsync(longRoot, Path.Combine(directory, "short-transport"), CancellationToken.None);
            Require(shortened.Length == 1 && Path.GetFullPath(Path.Combine(shortened[0], "n/item.bin")).Length < 260
                && File.ReadAllBytes(Path.Combine(shortened[0], "n/item.bin")).SequenceEqual(new byte[] { 1, 2, 3 }), "vvfat-relocates-small-deep-payload-without-changing-bytes");
            await Reject(async () => { await PayloadVolumes.PrepareAsync(longRoot, Path.Combine(longRoot, "still-too-long"), CancellationToken.None); }, "vvfat-refuses-unexportable-destination-before-running-qemu");
        }
        var source = Path.Combine(directory, "source.img");
        await File.WriteAllBytesAsync(source, RecoveryImageChecks.Fixture());
        await OwnedImageFailureChecks(directory, source, checks);
        await ImportChecks(directory, source, checks);
        var before = SHA256.HashData(await File.ReadAllBytesAsync(source));
        Require(!RecoveryImage.IsPrepared(source), "five-partition-recovery-classification");
        var copy = Path.Combine(directory, "copy"); Directory.CreateDirectory(copy);
        var imported = await BoilerImage.ImportAsync(source, copy, _ => { }, CancellationToken.None);
        Require(!imported.Prepared && source != imported.Path, "imported-recovery-copied-to-session");
        Require(SHA256.HashData(await File.ReadAllBytesAsync(source)).SequenceEqual(before), "import-preserves-original");
        var zip = Path.Combine(directory, "image.boiler.zip");
        var plan = new { schema_version = 1, kind = "InstallPlan", mode = "vanilla", components = Array.Empty<object>() };
        await BoilerImage.ExportAsync(source, zip, plan, CancellationToken.None);
        var roundtrip = Path.Combine(directory, "roundtrip"); Directory.CreateDirectory(roundtrip);
        var restored = await BoilerImage.ImportAsync(zip, roundtrip, _ => { }, CancellationToken.None);
        Require(imported.Sha256 == restored.Sha256, "export-import-hash-roundtrip");
        var protectedSource = Path.Combine(directory, "protected.img");
        var protectedBytes = RecoveryImageChecks.Fixture(prepared: true, withProtected: true);
        await File.WriteAllBytesAsync(protectedSource, protectedBytes);
        var protectedZip = Path.Combine(directory, "system-only.boiler.zip");
        await BoilerImage.ExportAsync(protectedSource, protectedZip, plan, CancellationToken.None);
        var abDirectory = Path.Combine(directory, "ab"); Directory.CreateDirectory(abDirectory);
        var ab = await BoilerImage.ImportAsync(protectedZip, abDirectory, _ => { }, CancellationToken.None);
        using (var raw = new PreparedDiskImage(ab.Path))
        {
            Require(BoilerImage.RequiredTargetCapacity(raw.Layout) == PartitionInventory.Minimum, "small-layout-retains-target-minimum");
            var first = raw.Layout.System[0].Offset;
            var larger = raw.Layout with { Partitions = raw.Layout.Partitions.Select(p => p.Name == "home"
                ? p with { Size = 96 * PartitionInventory.GiB - p.Offset + first } : p).ToArray() };
            Require(BoilerImage.RequiredTargetCapacity(larger) == 96 * PartitionInventory.GiB, "target-minimum-follows-larger-source-layout");
        }
        Require(BoilerImage.RawImportBudget(32 * PartitionInventory.GiB, true) == 72ul * PartitionInventory.GiB
            && BoilerImage.RawImportBudget(96 * PartitionInventory.GiB, true) == 200ul * PartitionInventory.GiB,
            "raw-import-workspace-budget-scales-with-image-size");
        Require(BoilerImage.RawImportBudget(96 * PartitionInventory.GiB, true, true) == 296ul * PartitionInventory.GiB,
            "raw-import-workspace-budget-includes-pending-inspection-copy");
        Require(BoilerImage.ArchiveImportBudget(PartitionInventory.GiB, 96 * PartitionInventory.GiB, true, true) == MediaSpace.MinimumWorkspace
            && BoilerImage.ArchiveImportBudget(PartitionInventory.GiB, 256 * PartitionInventory.GiB, true) == MediaSpace.MinimumWorkspace,
            "zip-workspace-budget-excludes-sparsely-streamed-logical-capacity");
        var localImages = new List<string> { ab.Path };
        foreach (var capacityMiB in new[] { 8, 24 })
        {
            var rawImage = Path.Combine(directory, $"local-{capacityMiB}MiB.img");
            await File.WriteAllBytesAsync(rawImage, RecoveryImageChecks.Fixture(prepared: true, capacityMiB: capacityMiB));
            var digest = SHA256.HashData(await File.ReadAllBytesAsync(rawImage));
            var importedDirectory = Path.Combine(directory, $"local-{capacityMiB}MiB"); Directory.CreateDirectory(importedDirectory);
            BoilerImage.ValidateSelection(rawImage);
            var rawImport = await BoilerImage.ImportAsync(rawImage, importedDirectory, _ => { }, CancellationToken.None);
            Require(rawImport.Prepared && new FileInfo(rawImport.Path).Length == capacityMiB * 1024L * 1024
                && SHA256.HashData(await File.ReadAllBytesAsync(rawImport.Path)).SequenceEqual(digest)
                && SHA256.HashData(await File.ReadAllBytesAsync(rawImage)).SequenceEqual(digest),
                $"raw-img-different-capacity-preserved-{capacityMiB}MiB");
            localImages.Add(rawImage);
        }
        Require(ab.Prepared, "eight-partition-export-import");
        using (var original = new PreparedDiskImage(protectedSource))
        using (var canonical = new PreparedDiskImage(ab.Path))
        {
            Require(canonical.Layout.Protected.Length == 0, "export-removes-protected-partition-entries");
            await Reject(() => Task.Run(() => canonical.Layout.For(InstallationTargetKind.InternalShared)), "canonical-direct-shared-layout-still-refused");
            await Reject(() => Task.Run(() => VirtualSharedTarget.Plan(canonical.Layout, InstallationTargetKind.InternalShared, Guid.NewGuid())), "canonical-shared-no-leading-fixture-space-refused");
            var spacious = canonical.Layout with { Size = 512L * 1024 * 1024,
                Partitions = canonical.Layout.System.Select(p => p with { Offset = p.Offset + 160L * 1024 * 1024 }).ToArray() };
            var id = Guid.NewGuid(); var target = VirtualSharedTarget.Plan(spacious, InstallationTargetKind.InternalShared, id);
            var repeated = VirtualSharedTarget.Plan(spacious, InstallationTargetKind.InternalShared, id);
            var other = VirtualSharedTarget.Plan(spacious, InstallationTargetKind.InternalShared, Guid.NewGuid());
            Require(target.Protected.Length == 2 && target.Protected.SequenceEqual(repeated.Protected)
                && !target.Protected.Select(p => p.Id).SequenceEqual(other.Protected.Select(p => p.Id))
                && target.System.Select(p => p with { Number = p.Number - 2 }).SequenceEqual(spacious.System), "canonical-virtual-target-plan-is-bound-and-preserves-system");
            Require(VirtualSharedTarget.Plan(spacious, InstallationTargetKind.ExternalDisk, id) == spacious, "canonical-external-plan-unchanged");
            foreach (var part in original.Layout.System)
            {
                var bytesA = new byte[(int)part.Size]; var bytesB = new byte[(int)part.Size];
                original.Stream.Position = part.Offset; original.Stream.ReadExactly(bytesA);
                canonical.Stream.Position = part.Offset; canonical.Stream.ReadExactly(bytesB);
                Require(bytesA.SequenceEqual(bytesB) && canonical.Layout.System.Single(p => p.Name == part.Name).Id == part.Id, "export-preserves-" + part.Name);
            }
            var forbidden = original.Layout.Protected.Single(); var zero = new byte[(int)forbidden.Size];
            canonical.Stream.Position = forbidden.Offset; canonical.Stream.ReadExactly(zero);
            Require(zero.All(value => value == 0), "export-redacts-protected-content");
        }
        Require((await File.ReadAllBytesAsync(protectedSource)).SequenceEqual(protectedBytes), "export-source-immutable");
        await Reject(() => BoilerImage.ExportAsync(source, zip, new { }, CancellationToken.None), "export-does-not-overwrite");
        var bad = Path.Combine(directory, "bad.zip");
        using (var archive = ZipFile.Open(bad, ZipArchiveMode.Create)) { using var writer = new StreamWriter(archive.CreateEntry("../outside.img").Open()); writer.Write("unsafe"); }
        await Reject(() => BoilerImage.ImportAsync(bad, directory, _ => { }, CancellationToken.None), "zip-path-traversal-refused");
        var bytes = await File.ReadAllBytesAsync(source); bytes[^512] ^= 1;
        var corrupt = Path.Combine(directory, "corrupt.img"); await File.WriteAllBytesAsync(corrupt, bytes);
        await Reject(() => Task.Run(() => RecoveryImage.ValidateRaw(corrupt, true)), "backup-gpt-corruption-refused");
        System.Windows.Media.RenderOptions.ProcessRenderMode = System.Windows.Interop.RenderMode.SoftwareOnly;
        var window = new MainWindow(rehearsal: true) { DeviceIndexLoaderForChecks = _ => Task.FromResult(BoilerDeviceChecks.IndexResponse()) };
        window.Width = 1060; window.Height = 720; window.WindowState = WindowState.Normal;
        window.Show(); window.UpdateLayout();
        await window.Dispatcher.InvokeAsync(() => { }, System.Windows.Threading.DispatcherPriority.ApplicationIdle);
        if (!Capture(window, Path.Combine(directory, "home.png"))) visualFailures.Add("home");
        foreach (var mode in new[] { "custom", "vanilla", "imported" })
        {
            window.BoilerModeForChecks(mode);
            Require(window.CurrentOptions.InstallMode == mode, mode + "-mode-propagated");
            Require(mode == "custom" || window.CurrentOptions.Repairs.Length == 0
                && window.CurrentOptions.Plugins.SequenceEqual(mode == "vanilla" ? ["core:decky", "core:maintenance"] : Array.Empty<string>()), mode + "-safe-defaults");
            if (mode == "imported")
            {
                var upgrade = (System.Windows.Controls.CheckBox)window.FindName("Upgrade310");
                Require(upgrade.IsChecked == false, "single-img-does-not-require-official-310");
                var sourceIssue = typeof(MainWindow).GetMethod("SourceIssue", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)!;
                var imagePath = (System.Windows.Controls.TextBox)window.FindName("ImagePath");
                foreach (var localImage in localImages)
                {
                    imagePath.Text = localImage;
                    Require(sourceIssue.Invoke(window, [true, false]) == null, "single-img-source-valid-without-second-image-" + Path.GetFileName(localImage));
                }
                var selector = (PartitionPicker)window.FindName("PartitionSelector");
                selector.AutomaticReadEnabled = false;
                selector.LoadSnapshot(PartitionInventory.ExampleJson, true);
                var partitions = (System.Windows.Controls.DataGrid)selector.FindName("Partitions");
                partitions.SelectedItem = partitions.Items.Cast<PartitionRow>().First(p => p.Space?.Size >= 100 * PartitionInventory.GiB);
                var capacityInput = (System.Windows.Controls.TextBox)selector.FindName("CapacityInput");
                capacityInput.Text = "128";
                var flags = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic;
                typeof(MainWindow).GetField("inspectedSourceKey", flags)!.SetValue(window, typeof(MainWindow).GetMethod("SourceInfoKey", flags)!.Invoke(window, null));
                typeof(MainWindow).GetField("inspectedSourceDigest", flags)!.SetValue(window, "synthetic-checked-source");
                typeof(MainWindow).GetField("inspectedSourceCapacity", flags)!.SetValue(window, (long?)(129 * PartitionInventory.GiB));
                var targetIssue = typeof(MainWindow).GetMethod("TargetIssue", flags)!;
                Require((targetIssue.Invoke(window, null) as string)?.Contains("129 GiB") == true, "larger-img-rejects-128-GiB-target-before-install");
                typeof(MainWindow).GetField("inspectedSourceCapacity", flags)!.SetValue(window, (long?)(96 * PartitionInventory.GiB));
                Require(targetIssue.Invoke(window, null) == null, "larger-img-accepts-sufficient-target-before-install");
                imagePath.Text = localImages[0];
                Require(selector.DefaultCapacityGiB == 128
                    && typeof(MainWindow).GetField("inspectedSourceCapacity", flags)!.GetValue(window) == null
                    && typeof(MainWindow).GetField("inspectedSourceKey", flags)!.GetValue(window) == null,
                    "replacement-img-clears-old-capacity-default-and-inspection");
                imagePath.Clear();
            }
            window.UpdateLayout();
            await window.Dispatcher.InvokeAsync(() => { }, System.Windows.Threading.DispatcherPriority.ApplicationIdle);
            if (!Capture(window, Path.Combine(directory, mode + ".png"))) visualFailures.Add(mode);
        }
        window.Close();
        using (var replay = JsonDocument.Parse(InstallationSessionReplayChecks.Run()))
            checks.Add(replay.RootElement.TryGetProperty("skipped", out _)
                ? "install-session-fixtures-unavailable" : "real-install-session-fixtures-replayed");
        var result = new { passed = true, scope = "Synthetic image and WPF state checks; no physical disk write or real-device acceptance", visual_acceptance = visualFailures.Count == 0 ? "captures-produced-needs-review" : "blocked-empty-render", visualFailures, checks, directory };
        await File.WriteAllTextAsync(Path.Combine(directory, "result.json"), JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true }));
        return JsonSerializer.Serialize(result);
    }
    private sealed class FailingExportPlan
    {
        private int reads;
        public int schema_version => 1;
        public string kind => "InstallPlan";
        public string mode => ++reads == 1 ? "vanilla" : throw new IOException("Injected export metadata failure.");
        public object[] components => [];
    }

    private static async Task OwnedImageFailureChecks(string directory, string source, List<string> checks)
    {
        void Require(bool value, string name) { if (!value) throw new IOException(name); checks.Add(name); }
        var snapshotDirectory = Path.Combine(directory, "cancelled-snapshot");
        using (var cancellation = new CancellationTokenSource())
        {
            cancellation.Cancel();
            try { await BoilerImage.SnapshotAsync(source, snapshotDirectory, cancellation.Token); throw new IOException("Expected cancelled snapshot."); }
            catch (OperationCanceledException) { }
        }
        Require(!Directory.Exists(snapshotDirectory) || Directory.GetFiles(snapshotDirectory).Length == 0,
            "cancelled-source-snapshot-does-not-leave-untracked-image");
        var sessionsBefore = Directory.GetDirectories(InstallationController.Work, "install-session-*").ToHashSet(StringComparer.OrdinalIgnoreCase);
        try
        {
            await InstallationController.RunAsync(new(InstallationTargetKind.ExternalDisk, source, false,
                Mode: "imported", ExpectedSourceSha256: new string('0', 64)), _ => { }, _ => { }, CancellationToken.None);
            throw new IOException("Expected reviewed source mismatch.");
        }
        catch (IOException error) when (error.Message == "Source image changed after review; inspect and confirm again.") { }
        var rejectedSessions = Directory.GetDirectories(InstallationController.Work, "install-session-*").Where(path => !sessionsBefore.Contains(path)).ToArray();
        Require(rejectedSessions.Length == 1 && Directory.GetFiles(rejectedSessions[0]).Length == 0,
            "rejected-source-digest-does-not-leave-untracked-image");
        var export = Path.Combine(directory, "failed-export.boiler.zip");
        try { await BoilerImage.ExportAsync(source, export, new FailingExportPlan(), CancellationToken.None); throw new IOException("Expected failed export."); }
        catch (IOException error) when (error.Message == "Injected export metadata failure.") { }
        Require(!File.Exists(export) && !File.Exists(export + ".partial"), "failed-export-cleans-owned-partial-and-allows-retry");
        var plan = new { schema_version = 1, kind = "InstallPlan", mode = "vanilla", components = Array.Empty<object>() };
        await BoilerImage.ExportAsync(source, export, plan, CancellationToken.None);
        Require(File.Exists(export), "failed-export-can-retry-same-destination");
        var existing = Path.Combine(directory, "existing-export.boiler.zip");
        await File.WriteAllTextAsync(existing + ".partial", "preexisting");
        try { await BoilerImage.ExportAsync(source, existing, plan, CancellationToken.None); throw new IOException("Expected occupied export refusal."); }
        catch (IOException error) when (error.Message == "Export destination already exists.") { }
        Require(await File.ReadAllTextAsync(existing + ".partial") == "preexisting", "failed-export-never-removes-unowned-existing-partial");
    }

    private static async Task ImportChecks(string directory, string source, List<string> checks)
    {
        var input = await File.ReadAllBytesAsync(source);
        void Require(bool value, string name) { if (!value) throw new IOException(name); checks.Add(name); }
        async Task Refuse(Func<Task> action, string name)
        {
            try { await action(); }
            catch (Exception error) when (error is IOException or InvalidOperationException) { checks.Add(name); return; }
            throw new IOException("Expected image import rejection: " + name);
        }
        async Task<string> Zip(string name, params (string Name, byte[] Bytes)[] entries)
        {
            var path = Path.Combine(directory, name);
            using var bundle = ZipFile.Open(path, ZipArchiveMode.Create);
            foreach (var entry in entries)
            { using var target = bundle.CreateEntry(entry.Name, CompressionLevel.SmallestSize).Open(); await target.WriteAsync(entry.Bytes); }
            return path;
        }
        string Session(string name) { var path = Path.Combine(directory, name); Directory.CreateDirectory(path); return path; }
        async Task Misdeclare(string path, string name, uint declared)
        {
            var bytes = await File.ReadAllBytesAsync(path);
            for (var offset = 0; offset < bytes.Length - 46; offset++)
            {
                var signature = BinaryPrimitives.ReadUInt32LittleEndian(bytes.AsSpan(offset));
                var central = signature == 0x02014b50; var local = signature == 0x04034b50;
                if (!central && !local) continue;
                var length = BinaryPrimitives.ReadUInt16LittleEndian(bytes.AsSpan(offset + (central ? 28 : 26)));
                var start = offset + (central ? 46 : 30);
                if (start + length <= bytes.Length && System.Text.Encoding.UTF8.GetString(bytes, start, length) == name)
                    BinaryPrimitives.WriteUInt32LittleEndian(bytes.AsSpan(offset + (central ? 24 : 22)), declared);
            }
            await File.WriteAllBytesAsync(path, bytes);
        }
        var legacy = await Zip("bounded.img.zip", ("system.img", input));
        var extracted = await Backend.ImportAsync(legacy, _ => { }, CancellationToken.None);
        Require((await File.ReadAllBytesAsync(extracted)).SequenceEqual(input), "legacy-zip-flat-image-roundtrip");
        var zipBytes = await File.ReadAllBytesAsync(legacy);
        var endOffset = zipBytes.Length - 22;
        var zip64Bytes = new byte[zipBytes.Length + 76];
        zipBytes.AsSpan(0, endOffset).CopyTo(zip64Bytes);
        var end64 = zip64Bytes.AsSpan(endOffset, 56);
        BinaryPrimitives.WriteUInt32LittleEndian(end64, 0x06064b50);
        BinaryPrimitives.WriteUInt64LittleEndian(end64[4..], 44);
        BinaryPrimitives.WriteUInt16LittleEndian(end64[12..], 45); BinaryPrimitives.WriteUInt16LittleEndian(end64[14..], 45);
        BinaryPrimitives.WriteUInt64LittleEndian(end64[24..], 1); BinaryPrimitives.WriteUInt64LittleEndian(end64[32..], 1);
        BinaryPrimitives.WriteUInt64LittleEndian(end64[40..], BinaryPrimitives.ReadUInt32LittleEndian(zipBytes.AsSpan(endOffset + 12)));
        BinaryPrimitives.WriteUInt64LittleEndian(end64[48..], BinaryPrimitives.ReadUInt32LittleEndian(zipBytes.AsSpan(endOffset + 16)));
        var locator = zip64Bytes.AsSpan(endOffset + 56, 20);
        BinaryPrimitives.WriteUInt32LittleEndian(locator, 0x07064b50); BinaryPrimitives.WriteUInt64LittleEndian(locator[8..], (ulong)endOffset);
        BinaryPrimitives.WriteUInt32LittleEndian(locator[16..], 1);
        zipBytes.AsSpan(endOffset).CopyTo(zip64Bytes.AsSpan(endOffset + 76));
        var finalEnd = zip64Bytes.AsSpan(endOffset + 76);
        BinaryPrimitives.WriteUInt16LittleEndian(finalEnd[8..], ushort.MaxValue); BinaryPrimitives.WriteUInt16LittleEndian(finalEnd[10..], ushort.MaxValue);
        BinaryPrimitives.WriteUInt32LittleEndian(finalEnd[12..], uint.MaxValue); BinaryPrimitives.WriteUInt32LittleEndian(finalEnd[16..], uint.MaxValue);
        var zip64 = Path.Combine(directory, "valid-zip64.img.zip"); await File.WriteAllBytesAsync(zip64, zip64Bytes);
        var extracted64 = await Backend.ImportAsync(zip64, _ => { }, CancellationToken.None);
        Require((await File.ReadAllBytesAsync(extracted64)).SequenceEqual(input), "bounded-central-directory-allows-valid-zip64-metadata");
        var many = await Zip("many-entries.img.zip", Enumerable.Range(0, 128).Select(index => (index + ".img", new byte[1])).ToArray());
        var manyBytes = await File.ReadAllBytesAsync(many);
        foreach (var dishonestCount in new[] { false, true })
        {
            if (dishonestCount)
            {
                BinaryPrimitives.WriteUInt16LittleEndian(manyBytes.AsSpan(manyBytes.Length - 14), 1);
                BinaryPrimitives.WriteUInt16LittleEndian(manyBytes.AsSpan(manyBytes.Length - 12), 1);
                await File.WriteAllBytesAsync(many, manyBytes);
            }
            await Refuse(() => Backend.ImportAsync(many, _ => { }, CancellationToken.None), "bounded-directory-rejects-many-records-" + dishonestCount);
        }
        var centralOffset = (int)BinaryPrimitives.ReadUInt32LittleEndian(zipBytes.AsSpan(endOffset + 16));
        foreach (var malformed in new[] { "truncated-record", "metadata-budget", "eocd-trailing-data", "zip64-offset" })
        {
            var altered = (malformed == "zip64-offset" ? zip64Bytes : zipBytes).ToArray();
            if (malformed == "truncated-record") BinaryPrimitives.WriteUInt16LittleEndian(altered.AsSpan(centralOffset + 28), ushort.MaxValue);
            else if (malformed == "metadata-budget") BinaryPrimitives.WriteUInt32LittleEndian(altered.AsSpan(endOffset + 12), 1024 * 1024 + 1);
            else if (malformed == "eocd-trailing-data") altered = [.. altered, 0];
            else BinaryPrimitives.WriteUInt64LittleEndian(altered.AsSpan(endOffset + 64), ulong.MaxValue);
            var invalid = Path.Combine(directory, malformed + ".img.zip"); await File.WriteAllBytesAsync(invalid, altered);
            await Refuse(() => Backend.ImportAsync(invalid, _ => { }, CancellationToken.None), "bounded-directory-rejects-" + malformed);
        }
        // The earlier one-entry EOCD claims the later 128-entry EOCD as its
        // comment. A preflight that skips the final signature disagrees with
        // ZipArchive, which selects that final EOCD despite the trailing byte.
        var firstPath = await Zip("differential-first.zip", Enumerable.Range(0, 128).Select(index => (index + ".img", new byte[1])).ToArray());
        var firstZip = await File.ReadAllBytesAsync(firstPath);
        var secondZip = zipBytes.ToArray();
        BinaryPrimitives.WriteUInt32LittleEndian(secondZip.AsSpan(secondZip.Length - 6),
            checked((uint)firstZip.Length + BinaryPrimitives.ReadUInt32LittleEndian(secondZip.AsSpan(secondZip.Length - 6))));
        BinaryPrimitives.WriteUInt16LittleEndian(secondZip.AsSpan(secondZip.Length - 2), 23);
        byte[] differential = [.. firstZip, .. secondZip, .. firstZip.AsSpan(firstZip.Length - 22).ToArray(), 0];
        var differentialPath = Path.Combine(directory, "eocd-parser-differential.img.zip");
        await File.WriteAllBytesAsync(differentialPath, differential);
        using (var frameworkArchive = ZipFile.OpenRead(differentialPath))
            Require(frameworkArchive.Entries.Count == 128, "eocd-differential-fixture-reaches-framework-many-entry-directory");
        await Refuse(() => Backend.ImportAsync(differentialPath, _ => { }, CancellationToken.None), "preflight-rejects-later-eocd-parser-differential");
        var differentialSession = Session("eocd-parser-differential");
        await Refuse(() => BoilerImage.ImportAsync(differentialPath, differentialSession, _ => { }, CancellationToken.None), "boiler-rejects-later-eocd-parser-differential");
        Require(Directory.GetFiles(differentialSession).Length == 0, "eocd-parser-differential-creates-no-output");
        foreach (var (name, entries) in new[] {
            ("multiple", new[] { ("one.img", input), ("two.img", input) }),
            ("traversal", new[] { ("../outside.img", input) }),
            ("extra", new[] { ("system.img", input), ("extra.txt", new byte[1]) }),
            ("directory", new[] { ("system.img", input), ("sub/", Array.Empty<byte>()) }) })
        {
            var archive = await Zip(name + ".img.zip", entries);
            await Refuse(() => Backend.ImportAsync(archive, _ => { }, CancellationToken.None), "legacy-zip-rejects-" + name);
            var session = Session("boiler-" + name);
            await Refuse(() => BoilerImage.ImportAsync(archive, session, _ => { }, CancellationToken.None), "boiler-zip-rejects-" + name);
            Require(Directory.GetFiles(session).Length == 0, "invalid-zip-no-output-" + name);
        }
        var dishonest = await Zip("false-image-length.zip", ("system.img", input));
        await Misdeclare(dishonest, "system.img", 32768);
        var falseSession = Session("false-image-length");
        await Refuse(() => BoilerImage.ImportAsync(dishonest, falseSession, _ => { }, CancellationToken.None), "zip-actual-image-exceeds-declared-length");
        Require(Directory.GetFiles(falseSession).Length == 0, "false-image-length-output-cleaned");
        var falseMetadata = await Zip("false-metadata-length.zip", ("system.img", input), ("image-manifest.json", new byte[2 * 1024 * 1024]));
        await Misdeclare(falseMetadata, "image-manifest.json", 128);
        var metadataSession = Session("false-metadata-length");
        await Refuse(() => BoilerImage.ImportAsync(falseMetadata, metadataSession, _ => { }, CancellationToken.None), "zip-actual-manifest-exceeds-declared-length");
        Require(Directory.GetFiles(metadataSession).Length == 0, "false-metadata-length-output-cleaned");
        var bounded = Path.Combine(directory, "bounded-stream.img");
        await Refuse(() => ImageImportStreams.CopyOwnedAsync(new MemoryStream(input), bounded, CancellationToken.None, maximum: 1024), "actual-stream-budget-overshoot-rejected");
        Require(!File.Exists(bounded), "overshoot-partial-output-cleaned");
        await Refuse(() => ImageImportStreams.CopyOwnedAsync(new MemoryStream(input), bounded, CancellationToken.None, expected: 1024), "actual-stream-exceeds-declared-exact-length");
        Require(!File.Exists(bounded), "declared-length-overshoot-output-cleaned");
        var spaceCalls = 0;
        await Refuse(() => ImageImportStreams.CopyOwnedAsync(new MemoryStream(input), bounded, CancellationToken.None,
            available: () => ++spaceCalls < 3 ? ulong.MaxValue : ImageImportStreams.ReserveBytes), "periodic-workspace-reserve-rechecked");
        Require(!File.Exists(bounded) && spaceCalls == 3, "reserve-failure-partial-output-cleaned");
        using (var cancellation = new CancellationTokenSource())
        {
            var calls = 0;
            try
            {
                await ImageImportStreams.CopyOwnedAsync(new MemoryStream(input), bounded, cancellation.Token,
                    available: () => { if (++calls == 3) cancellation.Cancel(); return ulong.MaxValue; });
                throw new IOException("Expected image stream cancellation");
            }
            catch (OperationCanceledException) { checks.Add("stream-cancelled-during-copy"); }
            Require(!File.Exists(bounded), "cancelled-stream-partial-output-cleaned");
        }
        await File.WriteAllTextAsync(bounded, "existing");
        await Refuse(() => ImageImportStreams.CopyOwnedAsync(new MemoryStream(input), bounded, CancellationToken.None), "existing-output-never-overwritten");
        Require(await File.ReadAllTextAsync(bounded) == "existing", "existing-output-preserved-after-failure");
        File.Delete(bounded);
        var bzip = Path.Combine(directory, "fixture.img.bz2");
        await Backend.ProcessAsync(Path.Combine(Backend.Root, "runtime/archive/7z.exe"), ["a", "-tbzip2", "--", bzip, source], _ => { }, CancellationToken.None);
        var bzipDigest = SHA256.HashData(await File.ReadAllBytesAsync(bzip));
        var bzipSession = Session("bzip-roundtrip");
        var imported = await BoilerImage.ImportAsync(bzip, bzipSession, _ => { }, CancellationToken.None);
        Require((await File.ReadAllBytesAsync(imported.Path)).SequenceEqual(input), "bundled-bzip-binary-stream-roundtrip");
        await Refuse(() => ImageImportStreams.ExtractBzipAsync(bzip, bounded, CancellationToken.None, maximum: 1024), "bundled-bzip-expanded-budget-rejected");
        Require(!File.Exists(bounded), "bzip-budget-failure-partial-output-cleaned");
        var invalidBzip = Path.Combine(directory, "invalid.img.bz2"); await File.WriteAllBytesAsync(invalidBzip, new byte[128]);
        await Refuse(() => ImageImportStreams.ExtractBzipAsync(invalidBzip, bounded, CancellationToken.None), "bundled-bzip-stdin-decompressor-failure-rejected");
        Require(!File.Exists(bounded), "bzip-decompressor-failure-output-cleaned");
        using (var cancellation = new CancellationTokenSource())
        {
            var calls = 0;
            try
            {
                await ImageImportStreams.ExtractBzipAsync(bzip, bounded, cancellation.Token,
                    available: () => { if (++calls == 3) cancellation.Cancel(); return ulong.MaxValue; });
                throw new IOException("Expected BZip2 stream cancellation");
            }
            catch (OperationCanceledException) { checks.Add("bundled-bzip-cancelled-during-copy"); }
            Require(!File.Exists(bounded), "bzip-cancellation-partial-output-cleaned");
        }
        Require(SHA256.HashData(await File.ReadAllBytesAsync(bzip)).SequenceEqual(bzipDigest)
            && (await File.ReadAllBytesAsync(source)).SequenceEqual(input), "compressed-and-raw-import-sources-unchanged");
    }

    private static bool Capture(MainWindow window, string path)
    {
        var surface = (FrameworkElement)window.Content;
        surface.Measure(new Size(1060, 720)); surface.Arrange(new Rect(0, 0, 1060, 720)); surface.UpdateLayout();
        var bitmap = new RenderTargetBitmap((int)surface.ActualWidth, (int)surface.ActualHeight, 96, 96, PixelFormats.Pbgra32);
        var drawing = new DrawingVisual();
        using (var context = drawing.RenderOpen()) context.DrawRectangle(new VisualBrush(surface), null, new Rect(0, 0, surface.ActualWidth, surface.ActualHeight));
        bitmap.Render(drawing); var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap));
        var pixels = new byte[bitmap.PixelWidth * bitmap.PixelHeight * 4]; bitmap.CopyPixels(pixels, bitmap.PixelWidth * 4, 0);
        if (!pixels.Where((_, index) => index % 4 == 3).Any(alpha => alpha != 0))
        { File.WriteAllText(path + ".diagnostics.json", JsonSerializer.Serialize(new { failure = "empty-render", surface.IsVisible, surface.IsLoaded, surface.ActualWidth, surface.ActualHeight })); return false; }
        using var file = File.Create(path); encoder.Save(file); return true;
    }
}

public partial class MainWindow
{
    internal void BoilerModeForChecks(string mode) => ChooseBoilerMode(mode);
}
