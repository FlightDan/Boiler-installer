using System.Text.Json;

namespace OxpInstaller;

// Progress is display-only. Completion still requires the authenticated
// receipt, full digest and the normal operation result checks.
internal sealed class ResourceIntegrityProgress(long expectedBytes)
{
    internal const string Marker = "BOILER_RESOURCE_INTEGRITY=";
    private string pending = "";
    private readonly Dictionary<string, long> seen = new();
    private readonly HashSet<string> completed = new();
    internal string Detail { get; private set; } = "";
    internal static string Report(string action,string phase,long done,long total,bool verified)
        => Marker+JsonSerializer.Serialize(new { action,phase,bytes=done,total,verified })+"\n";
    internal bool Observe(string chunk)
    {
        var changed=false; pending+=chunk; var lines=pending.Split('\n'); pending=lines[^1];
        if(pending.Length>65536)pending=pending[^65536..];
        foreach(var line in lines[..^1])
        {
            var index=line.IndexOf(Marker,StringComparison.Ordinal);if(index<0)continue;
            try
            {
                using var json=JsonDocument.Parse(line[(index+Marker.Length)..]);var value=json.RootElement;
                var action=value.GetProperty("action").GetString();var phase=value.GetProperty("phase").GetString();
                var done=value.GetProperty("bytes").GetInt64();var total=value.GetProperty("total").GetInt64();
                var verified=value.GetProperty("verified").GetBoolean();var key=action+":"+phase;
                if(action is not ("apply" or "verify") || phase is not ("host" or "guest")
                    || total!=expectedBytes || done<0 || done>total || (verified && done!=total)
                    || completed.Contains(key)
                    || (seen.TryGetValue(key,out var old) && done<old))continue;
                seen[key]=done;
                if(verified)completed.Add(key);
                var size=$"{done/1073741824d:F1} / {total/1073741824d:F1} GiB · {done/(double)total:P0}";
                Detail=(phase=="host"
                    ? PartitionInventory.L(verified ? "源镜像完整性检查完成" : "正在检查源镜像完整性",
                        verified ? "Source image integrity verified" : "Checking source image integrity")
                    : PartitionInventory.L(verified ? "镜像独立完整性复核完成" : "正在独立复核镜像完整性",
                        verified ? "Independent image integrity check complete" : "Independently checking image integrity"))+" · "+size;
                changed=true;
            }
            catch(Exception error) when(error is JsonException or KeyNotFoundException or InvalidOperationException or FormatException or OverflowException) { }
        }
        return changed;
    }
}
