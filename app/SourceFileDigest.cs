using System.Diagnostics;
using System.IO;
using System.Security.Cryptography;
using System.Text.RegularExpressions;

namespace OxpInstaller;

internal sealed record SourceDigestProgress(long Completed, long Total, double BytesPerSecond);
internal sealed record SourceFileDigest(string Path, long Length, long LastWriteUtcTicks, string Md5, string Sha256, bool ComparedToReference)
{
    // SHA-256 remains the installation protocol's reviewed-byte identity. MD5 is
    // displayed for the user's file check; both come from one sequential read.
    internal static Task<SourceFileDigest> ComputeAsync(string path, Action<SourceDigestProgress>? report,
        CancellationToken token, string? expectedMd5 = null) => Task.Run(async () =>
    {
        token.ThrowIfCancellationRequested();
        if (expectedMd5 != null && !Regex.IsMatch(expectedMd5, "^[a-fA-F0-9]{32}$"))
            throw new ArgumentException("Invalid reference MD5.", nameof(expectedMd5));
        path = InputDiscovery.SafePath(path);
        var stamp = new FileInfo(path); var length = stamp.Length; var ticks = stamp.LastWriteTimeUtc.Ticks;
        using var source = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read, 1024 * 1024,
            FileOptions.Asynchronous | FileOptions.SequentialScan);
        if (length == 0 || source.Length != length) throw new IOException("The source file is empty or changed before reading.");
        using var md5 = IncrementalHash.CreateHash(HashAlgorithmName.MD5);
        using var sha256 = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        var watch = Stopwatch.StartNew(); var last = TimeSpan.Zero; long completed = 0;
        report?.Invoke(new(0, length, 0));
        var buffer = new byte[1024 * 1024]; int count;
        while ((count = await source.ReadAsync(buffer, token)) != 0)
        {
            token.ThrowIfCancellationRequested();
            md5.AppendData(buffer, 0, count); sha256.AppendData(buffer, 0, count); completed += count;
            if (watch.Elapsed - last < TimeSpan.FromMilliseconds(250)) continue;
            report?.Invoke(new(completed, length, completed / Math.Max(watch.Elapsed.TotalSeconds, .001)));
            last = watch.Elapsed;
        }
        token.ThrowIfCancellationRequested();
        stamp.Refresh();
        if (completed != length || source.Length != length || !stamp.Exists || stamp.Length != length || stamp.LastWriteTimeUtc.Ticks != ticks)
            throw new IOException(PartitionInventory.L("镜像在计算摘要时改变，请重新选择。", "The image changed while calculating its digest. Select it again."));
        var result = new SourceFileDigest(path, length, ticks, Convert.ToHexString(md5.GetHashAndReset()).ToLowerInvariant(),
            Convert.ToHexString(sha256.GetHashAndReset()).ToLowerInvariant(), expectedMd5 != null);
        if (expectedMd5 != null && !string.Equals(result.Md5, expectedMd5, StringComparison.OrdinalIgnoreCase))
            throw new IOException(PartitionInventory.L("镜像 MD5 与参考值不匹配。", "The image MD5 does not match the reference value."));
        report?.Invoke(new(completed, length, completed / Math.Max(watch.Elapsed.TotalSeconds, .001)));
        token.ThrowIfCancellationRequested();
        return result;
    }, token);
}
