using System.IO;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text.Json;

namespace OxpInstaller;

internal sealed record ImportedImage(string Path, bool Prepared, string Sha256, JsonElement? Manifest);

internal static class BoilerImage
{
    internal static async Task<(string Path, string Sha256)> SnapshotAsync(string input, string directory, CancellationToken token)
    {
        var suffix = input.EndsWith(".img.bz2", StringComparison.OrdinalIgnoreCase) ? ".img.bz2" : input.EndsWith(".zip", StringComparison.OrdinalIgnoreCase) ? ".img.zip" : ".img";
        Directory.CreateDirectory(directory);
        var output = System.IO.Path.Combine(directory, "source-" + Guid.NewGuid().ToString("N") + suffix);
        MediaSource.Ordinary(output, InstallationController.Work, false);
        // Copy and hash the same held handle. A parent junction may be rebound
        // while that handle is open, so never reopen the input by path afterward.
        using var source = new FileStream(input, FileMode.Open, FileAccess.Read, FileShare.Read, 1024 * 1024, true);
        MediaSpace.RequireAvailable(directory, checked((ulong)source.Length + PartitionInventory.GiB));
        using var target = new FileStream(output, FileMode.CreateNew, FileAccess.Write, FileShare.None, 1024 * 1024, true);
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        var buffer = new byte[1024 * 1024]; int size;
        while ((size = await source.ReadAsync(buffer, token)) != 0)
        { hash.AppendData(buffer, 0, size); await target.WriteAsync(buffer.AsMemory(0, size), token); }
        await target.FlushAsync(token);
        if (target.Length != source.Length) throw new IOException("Source size changed during snapshot.");
        return (output, Convert.ToHexString(hash.GetHashAndReset()).ToLowerInvariant());
    }
    internal static void ValidateSelection(string path)
    {
        if (!File.Exists(path)) throw new IOException("请选择现有镜像。 / Select an existing image.");
        if (path.EndsWith(".img", StringComparison.OrdinalIgnoreCase)) RecoveryImage.ValidateRaw(path, true);
        else if (!path.EndsWith(".zip", StringComparison.OrdinalIgnoreCase) && !path.EndsWith(".img.bz2", StringComparison.OrdinalIgnoreCase))
            throw new IOException("支持 IMG、IMG.ZIP、IMG.BZ2 和 Boiler ZIP 包。 / Supported: IMG, IMG.ZIP, IMG.BZ2 and Boiler ZIP.");
    }

    internal static async Task<ImportedImage> ImportAsync(string input, string session, Action<string> log, CancellationToken token)
    {
        ValidateSelection(input);
        var output = System.IO.Path.Combine(session, "imported.img");
        JsonElement? manifest = null;
        if (input.EndsWith(".zip", StringComparison.OrdinalIgnoreCase))
        {
            using var archive = ZipFile.OpenRead(input);
            var files = archive.Entries.Where(e => !e.FullName.EndsWith('/')).ToArray();
            if (files.Length is < 1 or > 2 || files.Any(e => e.FullName != e.Name || e.Name.Contains(':') || e.Name.Contains('\\')))
                throw new IOException("ZIP 必须只含一个根目录 IMG 和可选 image-manifest.json。 / ZIP must contain one root IMG and optional image-manifest.json.");
            var images = files.Where(e => e.Name.EndsWith(".img", StringComparison.OrdinalIgnoreCase)).ToArray();
            if (images.Length != 1 || files.Any(e => e != images[0] && e.Name != "image-manifest.json")) throw new IOException("Invalid Boiler image archive.");
            if (images[0].Length < 34 * 512 || images[0].Length % 512 != 0) throw new IOException("Invalid archived image length.");
            MediaSpace.RequireAvailable(session, checked((ulong)images[0].Length + PartitionInventory.GiB));
            if (files.SingleOrDefault(e => e.Name == "image-manifest.json") is { } metadata)
            {
                if (metadata.Length > 1024 * 1024) throw new IOException("Image manifest is too large.");
                var manifestFile = System.IO.Path.Combine(session, "import-image-manifest.json");
                using (var stream = metadata.Open())
                using (var outputManifest = new FileStream(manifestFile, FileMode.CreateNew)) await stream.CopyToAsync(outputManifest, token);
                await BoilerProtocol.RunAsync(["validate", manifestFile], token);
                using var doc = JsonDocument.Parse(await File.ReadAllTextAsync(manifestFile, token));
                manifest = doc.RootElement.Clone();
                if (manifest.Value.GetProperty("kind").GetString() != "ImageManifest" || manifest.Value.GetProperty("architecture").GetString() != "x86_64" || manifest.Value.GetProperty("image").GetProperty("path").GetString() != images[0].Name
                    || manifest.Value.GetProperty("image").GetProperty("size").GetInt64() != images[0].Length)
                    throw new IOException("Unsupported or inconsistent image manifest.");
            }
            using var source = images[0].Open();
            using var target = new FileStream(output, FileMode.CreateNew, FileAccess.Write, FileShare.None, 1024 * 1024, true);
            await source.CopyToAsync(target, token);
            if (target.Length != images[0].Length) throw new IOException("Truncated image archive.");
        }
        else if (input.EndsWith(".img.bz2", StringComparison.OrdinalIgnoreCase))
        {
            var extracted = await Backend.ImportAsync(input, log, token, allowPrepared: true);
            await Backend.CopyImageAsync(extracted, output, token);
        }
        else
        {
            MediaSpace.RequireAvailable(session, checked((ulong)new FileInfo(input).Length + PartitionInventory.GiB));
            await Backend.CopyImageAsync(input, output, token);
        }
        RecoveryImage.ValidateRaw(output, true);
        using var file = File.OpenRead(output);
        var hash = Convert.ToHexString(await SHA256.HashDataAsync(file, token)).ToLowerInvariant();
        if (manifest is { } value && !string.Equals(value.GetProperty("image").GetProperty("sha256").GetString(), hash, StringComparison.Ordinal))
            throw new IOException("镜像哈希不匹配。 / Image hash does not match manifest.");
        var prepared = RecoveryImage.IsPrepared(output);
        if (manifest is { } m && m.GetProperty("layout").GetString() != (prepared ? "steamos-ab" : "steamos-recovery"))
            throw new IOException("Image layout differs from manifest.");
        log($"Imported {(prepared ? "A/B image" : "recovery image")}; SHA256={hash}\n");
        return new(output, prepared, hash, manifest);
    }

    internal static async Task<string> ExportAsync(string image, string destination, object plan, CancellationToken token)
    {
        if (File.Exists(destination) || File.Exists(destination + ".partial")) throw new IOException("Export destination already exists.");
        bool prepared;
        try { RecoveryImage.ValidateRaw(image, true); prepared = RecoveryImage.IsPrepared(image); }
        catch (IOException) { using var owned = new PreparedDiskImage(image); prepared = true; }
        using Stream source = prepared ? new BoilerExportStream(image) : File.OpenRead(image);
        var hash = Convert.ToHexString(await SHA256.HashDataAsync(source, token)).ToLowerInvariant();
        source.Position = 0;
        var manifest = new { schema_version = 1, kind = "ImageManifest", id = "image:steamos", version = "0.1.0", architecture = "x86_64", layout = prepared ? "steamos-ab" : "steamos-recovery",
            image = new { path = "system.img", size = source.Length, sha256 = hash }, install_plan = plan };
        var validation = System.IO.Path.Combine(InstallationController.Work, "export-manifest-" + Guid.NewGuid().ToString("N") + ".json");
        await File.WriteAllTextAsync(validation, JsonSerializer.Serialize(manifest), token);
        try { await BoilerProtocol.RunAsync(["validate", validation], token); }
        finally { File.Delete(validation); }
        using (var file = new FileStream(destination + ".partial", FileMode.CreateNew))
        using (var archive = new ZipArchive(file, ZipArchiveMode.Create))
        {
            using (var entry = archive.CreateEntry("system.img", CompressionLevel.Fastest).Open()) await source.CopyToAsync(entry, token);
            using (var entry = archive.CreateEntry("image-manifest.json").Open()) await JsonSerializer.SerializeAsync(entry, manifest, cancellationToken: token);
        }
        File.Move(destination + ".partial", destination);
        return destination;
    }
}
