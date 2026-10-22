using System.IO;
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
