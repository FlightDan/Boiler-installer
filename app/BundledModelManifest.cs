using System.IO;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text.Json;

namespace OxpInstaller;

/// <summary>
/// Reads the manifest of a bundled model package for QA checks, without
/// extracting the full package. Both the retired directory layout and the
/// single-copy brand archive are supported; the returned bytes are pinned to
/// the descriptor's manifest hash.
/// </summary>
internal static class BundledModelManifest
{
    private const long MaximumManifest = 2 * 1024 * 1024;

    internal static byte[] Read(string modelId)
    {
        var descriptorPath = Path.Combine(Backend.Root, "cache", "model-bundles", "manifest.json");
        using var descriptor = JsonDocument.Parse(File.ReadAllBytes(descriptorPath));
        var root = descriptor.RootElement;
        var entry = root.GetProperty("packages").EnumerateArray()
            .Single(item => item.GetProperty("id").GetString() == modelId);
        var expected = entry.GetProperty("manifest_sha256").GetString()!;
        var size = entry.GetProperty("manifest_size").GetInt64();
        if (size <= 0 || size > MaximumManifest)
            throw new IOException("Bundled model manifest size is outside the reviewed bound.");
        if (root.GetProperty("kind").GetString() != "LocalTestModelBrandBundle")
        {
            var directory = Path.Combine(Backend.Root, entry.GetProperty("directory").GetString()!, "manifest.json");
            return Verified(File.ReadAllBytes(directory), size, expected);
        }
        var archive = root.GetProperty("archive");
        var path = Path.Combine(Backend.Root, archive.GetProperty("path").GetString()!);
        if (new FileInfo(path).Length != archive.GetProperty("size").GetInt64())
            throw new IOException("Bundled brand archive size differs from its descriptor.");
        using var zip = ZipFile.OpenRead(path);
        foreach (var member in zip.Entries)
        {
            if (member.Length != size || !member.FullName.EndsWith("/manifest.json", StringComparison.Ordinal))
                continue;
            using var stream = member.Open();
            using var buffer = new MemoryStream((int)size);
            stream.CopyTo(buffer);
            if (Convert.ToHexString(SHA256.HashData(buffer.ToArray())).ToLowerInvariant() == expected)
                return buffer.ToArray();
        }
        throw new IOException("Bundled brand archive does not contain the selected model manifest.");
    }

    private static byte[] Verified(byte[] bytes, long size, string expected)
    {
        if (bytes.Length != size
            || Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant() != expected)
            throw new IOException("Bundled model manifest differs from its descriptor.");
        return bytes;
    }
}
