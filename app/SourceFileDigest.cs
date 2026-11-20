using System.IO;
using System.Security.Cryptography;

namespace OxpInstaller;

internal sealed record SourceFileDigest(string Path, long Length, long LastWriteUtcTicks, string Sha256)
{
    internal static Task<SourceFileDigest> ComputeAsync(string path, CancellationToken token) => Task.Run(async () =>
    {
        token.ThrowIfCancellationRequested();
        path = InputDiscovery.SafePath(path);
        var stamp = new FileInfo(path); var length = stamp.Length; var ticks = stamp.LastWriteTimeUtc.Ticks;
        using var source = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read, 1024 * 1024,
            FileOptions.SequentialScan);
        if (length == 0 || source.Length != length) throw new IOException("The source file is empty or changed before reading.");
        using var input = RawImageStaging.SparseRead(source, token);
        using var sha256 = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        var buffer = new byte[1024 * 1024];
        int count;
        while ((count = await input.ReadAsync(buffer, token)) != 0)
            sha256.AppendData(buffer, 0, count);
        token.ThrowIfCancellationRequested();
        stamp.Refresh();
        if (input.Position != length || source.Length != length || !stamp.Exists || stamp.Length != length || stamp.LastWriteTimeUtc.Ticks != ticks)
            throw new IOException(PartitionInventory.L("镜像在校验时改变，请重新选择。", "The image changed during verification. Select it again."));
        return new SourceFileDigest(path, length, ticks, Convert.ToHexString(sha256.GetHashAndReset()).ToLowerInvariant());
    }, token);
}
