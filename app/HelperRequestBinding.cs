using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace OxpInstaller;

// The digest is carried outside the mutable request file. Purpose separation prevents
// a valid request for one helper from being replayed to another helper.
public sealed class HelperRequestBinding : IDisposable
{
    public const string Partition = "partition";
    public const string Media = "media";
    public const string UsbReview = "usb-review";
    public const string Delete = "delete-partition";
    public const string Format = "format-remaining";
    public const string Recovery = "partition-recovery-state";
    public const int SmallLimit = 65536;
    public const int SnapshotLimit = 4 * 1024 * 1024;
    private readonly FileStream lease;
    public string Digest { get; }
    private HelperRequestBinding(FileStream lease, string digest) { this.lease = lease; Digest = digest; }

    public static string Compute(string purpose, ReadOnlySpan<byte> bytes)
    {
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        hash.AppendData(Encoding.UTF8.GetBytes("BoilerHelperRequest:v1:" + purpose + "\0"));
        hash.AppendData(bytes);
        return Convert.ToHexStringLower(hash.GetHashAndReset());
    }

    public static void ValidateDigest(string? digest)
    {
        if (digest == null || digest.Length != 64 || digest.Any(c => c is not (>= '0' and <= '9') and not (>= 'a' and <= 'f')))
            throw new IOException("Missing or invalid helper request digest.");
    }

    public static HelperRequestBinding Create<T>(string path, string purpose, T request, CancellationToken token = default)
    {
        token.ThrowIfCancellationRequested();
        var bytes = JsonSerializer.SerializeToUtf8Bytes(request);
        var limit = purpose is Delete or Format ? SnapshotLimit : SmallLimit;
        if (bytes.Length == 0 || bytes.Length > limit) throw new IOException("Oversized helper request.");
        var digest = Compute(purpose, bytes); // Never hash a possibly replaced path after serialization.
        MediaSource.Ordinary(path, InstallerWorkspace.DirectoryPath, false);
        var lease = new FileStream(path, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.Read);
        try
        {
            lease.Write(bytes); lease.Flush(true); lease.Position = 0;
            token.ThrowIfCancellationRequested();
            return new(lease, digest);
        }
        catch { lease.Dispose(); throw; }
    }

    // The parent's retained CreateNew handle denies writers and deletes. ShareWrite
    // here accommodates that handle's write access, without granting another opener
    // write permission against the parent's FileShare.Read lease.
    public static byte[] ReadBytes(string path, int limit)
    {
        using var input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
        var length = input.Length;
        if (length <= 0 || length > limit) throw new IOException("Invalid helper request size.");
        var bytes = new byte[checked((int)length)];
        input.ReadExactly(bytes);
        if (input.ReadByte() != -1) throw new IOException("Helper request grew during read.");
        return bytes;
    }

    public static byte[] ReadVerified(string path, string purpose, string? digest, int limit)
    {
        ValidateDigest(digest); // Before opening paths, admin checks or creating any output.
        var bytes = ReadBytes(path, limit);
        if (!CryptographicOperations.FixedTimeEquals(Convert.FromHexString(digest!), Convert.FromHexString(Compute(purpose, bytes))))
            throw new IOException("Helper request digest mismatch.");
        return bytes;
    }

    public static T Parse<T>(byte[] bytes) => JsonSerializer.Deserialize<T>(bytes,
        new JsonSerializerOptions { RespectRequiredConstructorParameters = true }) ?? throw new IOException("Empty helper request.");
    public void Dispose() => lease.Dispose();
}
