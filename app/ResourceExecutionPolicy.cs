using System.IO;

namespace OxpInstaller;

// Only Backend's already authenticated resourceBinding branch calls this.
// Allowances cover both complete passes; neither pass may be skipped.
internal static class ResourceExecutionPolicy
{
    internal const long HostBytesPerSecond = 128L * 1024 * 1024;
    internal const long GuestBytesPerSecond = 32L * 1024 * 1024;
    internal static TimeSpan ForResourceImage(string task, long bytes)
    {
        var normal = task switch { "integration" => 1200, "integration-verify" => 300,
            _ => throw new IOException("Resource timeout requires integration or readonly integration verification.") };
        if (bytes <= 0)throw new IOException("Resource image size must be positive.");
        var host = (bytes - 1) / HostBytesPerSecond + 1;
        var guest = (bytes - 1) / GuestBytesPerSecond + 1;
        var seconds=checked(normal + host + guest);
        // Match the timer's actual representable range; do not introduce a
        // raw-image size ceiling based on a compressed-import expansion limit.
        if(seconds>((long)uint.MaxValue-1)/1000)
            throw new IOException("Resource verification budget exceeds the cancellation timer range.");
        return TimeSpan.FromSeconds(seconds);
    }
}
