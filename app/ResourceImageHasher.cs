using System.IO;
using System.Diagnostics;
using System.Security.Cryptography;

namespace OxpInstaller;

internal static class ResourceImageHasher
{
    // Caller owns the deny-write pin and validates metadata before/after this
    // complete byte pass. Large reads avoid per-4KiB async IO on sparse files.
    internal static Task<string> HashAsync(Stream input,long expectedSize,CancellationToken token,Action<long>? progress=null)
        => Task.Run(async () => {
            if(expectedSize<=0)throw new IOException("Invalid image hash size.");
            using var digest=IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
            var buffer=new byte[1024*1024];long done=0;var last=Stopwatch.StartNew();int count;
            progress?.Invoke(0);
            while((count=await input.ReadAsync(buffer,token).ConfigureAwait(false))!=0)
            {
                token.ThrowIfCancellationRequested();digest.AppendData(buffer,0,count);done+=count;
                if(done>expectedSize)throw new IOException("Prepared image exceeds its committed size.");
                if(last.ElapsedMilliseconds>=1000) { progress?.Invoke(done);last.Restart(); }
            }
            token.ThrowIfCancellationRequested();
            if(done!=expectedSize)throw new IOException("Prepared image ended before its committed size.");
            return Convert.ToHexStringLower(digest.GetHashAndReset());
        },token);
}
