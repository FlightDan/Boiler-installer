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
        await Reject(async () => { await PayloadVolumes.PrepareAsync(transport, Path.Combine(directory, "too-small"), CancellationToken.None, 8192); }, "oversized-payload-file-refused");
        var source = Path.Combine(directory, "source.img");
        await File.WriteAllBytesAsync(source, RecoveryImageChecks.Fixture());
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
        Require(ab.Prepared, "eight-partition-export-import");
        using (var original = new PreparedDiskImage(protectedSource))
        using (var canonical = new PreparedDiskImage(ab.Path))
        {
            Require(canonical.Layout.Protected.Length == 0, "export-removes-protected-partition-entries");
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
        var window = new MainWindow(rehearsal: true);
        window.Width = 1060; window.Height = 720; window.WindowState = WindowState.Normal;
        window.Show(); window.UpdateLayout();
        await window.Dispatcher.InvokeAsync(() => { }, System.Windows.Threading.DispatcherPriority.ApplicationIdle);
        if (!Capture(window, Path.Combine(directory, "home.png"))) visualFailures.Add("home");
        foreach (var mode in new[] { "custom", "vanilla", "imported" })
        {
            window.BoilerModeForChecks(mode);
            Require(window.CurrentOptions.InstallMode == mode, mode + "-mode-propagated");
            Require(mode == "custom" || window.CurrentOptions.Repairs.Length + window.CurrentOptions.Plugins.Length == 0, mode + "-safe-defaults");
            window.UpdateLayout();
            await window.Dispatcher.InvokeAsync(() => { }, System.Windows.Threading.DispatcherPriority.ApplicationIdle);
            if (!Capture(window, Path.Combine(directory, mode + ".png"))) visualFailures.Add(mode);
        }
        window.Close();
        var result = new { passed = true, scope = "Synthetic image and WPF state checks; no physical disk write or real-device acceptance", visual_acceptance = visualFailures.Count == 0 ? "captures-produced-needs-review" : "blocked-empty-render", visualFailures, checks, directory };
        await File.WriteAllTextAsync(Path.Combine(directory, "result.json"), JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true }));
        return JsonSerializer.Serialize(result);
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
