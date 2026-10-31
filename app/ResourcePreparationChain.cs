using System.IO;
using System.Diagnostics;
using System.Security.Cryptography;
using System.Text.Json;

namespace OxpInstaller;

// This capability is issued by operations below, not by deserializing receipts.
// It is a local workflow attestation, never a production signature or a proof
// of real-handheld compatibility.
public sealed class ResourcePreparationChain : IDisposable
{
    public const string Purpose = "resource-preparation-local-test";
    private readonly FileStream sessionLease, source;
    private readonly List<FileStream> retained = [];
    private readonly List<object> steps = [];
    private readonly string directory, sessionId, sourceSha;
    private string phase = "snapshot", current, fixture = "";
    private FileStream? preparedPin;
    private MediaSource? preparedIdentity;
    private string preparedSha = "";
    private double sourceHashSeconds, preparedHashSeconds;
    private HelperRequestBinding? receipt;
    private string receiptPath = "";
    private bool disposed;
    public string ReceiptDigest => receipt?.Digest ?? throw new IOException("Preparation is not complete.");
    public byte[] ReceiptBytes => HelperRequestBinding.ReadVerified(receiptPath, Purpose, ReceiptDigest, HelperRequestBinding.SmallLimit);

    private ResourcePreparationChain(string directory, string sessionId, FileStream lease, FileStream source, string sha)
    { this.directory=directory; this.sessionId=sessionId; sessionLease=lease; this.source=source; sourceSha=sha; current=source.Name; }

    private static Task<string> HashAsync(FileStream stream,CancellationToken token)
        => Task.Run(async () => { using var input=RawImageStaging.SparseRead(stream,token);
            return Convert.ToHexStringLower(await SHA256.HashDataAsync(input,token).ConfigureAwait(false)); },token);

    public static async Task<ResourcePreparationChain> BeginAsync(string directory, string sessionId, FileStream lease,
        string sourcePath, string sourceSha,CancellationToken token)
    {
        directory=Path.GetFullPath(directory); sourcePath=Path.GetFullPath(sourcePath);
        if(Path.GetFileName(directory)!="install-session-"+sessionId || !lease.CanWrite || lease.SafeFileHandle.IsClosed
            || !string.Equals(Path.GetFullPath(lease.Name),Path.Combine(directory,"operation.lock"),StringComparison.OrdinalIgnoreCase))
            throw new IOException("Preparation requires the existing matching session lease.");
        MediaSource.Ordinary(sourcePath,directory);
        var stream=new FileStream(sourcePath,FileMode.Open,FileAccess.Read,FileShare.Read);
        try {
            var watch=Stopwatch.StartNew();
            if(await HashAsync(stream,token).ConfigureAwait(false)!=sourceSha)throw new IOException("Preparation source differs from reviewed snapshot.");
            stream.Position=0;
            return new ResourcePreparationChain(directory,sessionId,lease,stream,sourceSha) { sourceHashSeconds=watch.Elapsed.TotalSeconds };
        } catch { stream.Dispose(); throw; }
    }

    private void Require(params string[] allowed)
    { if(disposed || !sessionLease.CanWrite || sessionLease.SafeFileHandle.IsClosed || !allowed.Contains(phase)) throw new IOException("Invalid or expired preparation stage: "+phase); }

    private void PinResult(string path,string operation,OfficialSourceBinding? additionalSource=null)
    {
        MediaSource.Ordinary(path,InstallerWorkspace.DirectoryPath);
        var handle=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.Read);
        retained.Add(handle); var identity=MediaSource.FromHandle(path,handle);
        steps.Add(new { operation, input=current, output=identity,additional_source=additionalSource }); current=Path.GetFullPath(path);
    }

    public async Task<string> ImportAsync(Action<string> log,CancellationToken token)
    {
        Require("snapshot"); var output=await Backend.ImportAsync(source.Name,log,token);
        PinResult(output,"Backend.ImportAsync"); phase="imported"; return output;
    }

    public async Task<string> UpgradeAsync(Action<string> log,CancellationToken token,
        Action<OfficialPreparationProgress>? report,OfficialSourceBinding? officialSource)
    {
        Require("imported");
        var reviewed=officialSource ?? await OfficialSystem.InspectPreparedSourceAsync(token);
        var official=new FileStream(reviewed.Path,FileMode.Open,FileAccess.Read,FileShare.Read);
        retained.Add(official);
        if(official.Length!=reviewed.Length || Convert.ToHexStringLower(await SHA256.HashDataAsync(official,token))!=reviewed.Sha256)
            throw new IOException("Additional official system source changed after review.");
        official.Position=0;
        var output=await OfficialSystem.PrepareAsync(current,directory,log,token,report,reviewed);
        PinResult(output,"OfficialSystem.PrepareAsync",reviewed); phase="upgraded"; return output;
    }

    public async Task<string> CreateFixtureAsync(Action<string> log,CancellationToken token)
    {
        Require("imported","upgraded"); fixture=await Backend.CreateFixtureAsync(log,token);
        MediaSource.Ordinary(fixture,InstallerWorkspace.DirectoryPath);
        // QEMU will write this freshly created fixture. Keep its path pinned
        // against replacement, while retaining QEMU's ordinary image locks.
        retained.Add(new FileStream(fixture,FileMode.Open,FileAccess.Read,FileShare.ReadWrite));
        phase="fixture"; return fixture;
    }

    public async Task DeployAsync(Action<string> log,CancellationToken token)
    {
        Require("fixture"); var result=await Backend.GuestAsync("deployment-test",current,false,log,token,fixture:fixture);
        // GuestAsync already requires successful process/task completion. Retain
        // the exact actual transcript commitment as part of the stage record.
        steps.Add(new { operation="Backend.GuestAsync:deployment-test", input=current, fixture,
            transcript_sha256=Convert.ToHexStringLower(SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(result))) });
        phase="deployed";
    }

    public async Task ExportAsync(string output,Action<string> log,CancellationToken token)
    {
        Require("deployed"); output=Path.GetFullPath(output);
        if(output!=Path.Combine(directory,"prepared.img"))throw new IOException("Unexpected preparation output path.");
        // A new flat fixture has no external backing chain. Pin its actual bytes
        // read-only through conversion; the earlier write-sharing guard remains
        // solely to prevent path replacement during the guest operation.
        using var input=new FileStream(fixture,FileMode.Open,FileAccess.Read,FileShare.Read);
        await RawImageStaging.ConvertAsync(QcowReadOnlyGraph.Filename(fixture),output,log,token);
        steps.Add(new { operation="RawImageStaging.ConvertAsync:deployed-fixture", input=fixture, output });
        await SealAsync(output,token).ConfigureAwait(false);
    }

    public async Task CopyPreparedAsync(string output,Action<string> log,CancellationToken token)
    {
        Require("snapshot"); output=Path.GetFullPath(output);
        if(output!=Path.Combine(directory,"selected-options.img"))throw new IOException("Unexpected prepared-copy output path.");
        using(var original=new PreparedDiskImage(source.Name)) { }
        await RawImageStaging.ConvertAsync(source.Name,output,log,token);
        steps.Add(new { operation="RawImageStaging.ConvertAsync:reviewed-prepared-copy", source_sha256=sourceSha, output });
        await SealAsync(output,token,exactCopy:true).ConfigureAwait(false);
    }

    private async Task SealAsync(string output,CancellationToken token,bool exactCopy=false)
    {
        preparedPin?.Dispose(); preparedPin=new FileStream(output,FileMode.Open,FileAccess.Read,FileShare.Read);
        preparedIdentity=MediaSource.FromHandle(output,preparedPin);
        using(var prepared=new PreparedDiskImage(output)) { }
        var watch=Stopwatch.StartNew();
        preparedSha=await HashAsync(preparedPin,token).ConfigureAwait(false); preparedPin.Position=0;
        preparedHashSeconds=watch.Elapsed.TotalSeconds;
        if(exactCopy && preparedSha!=sourceSha)throw new IOException("Prepared copy bytes differ from the held reviewed source.");
        phase="prepared";
        receipt?.Dispose(); receiptPath=Path.Combine(directory,"resource-preparation-"+Guid.NewGuid().ToString("N")+".json");
        receipt=HelperRequestBinding.Create(receiptPath,Purpose,new { schema_version=1,kind="ResourcePreparationReceipt",
            trust="development-session",session_id=sessionId,status=phase,source_sha256=sourceSha,
            prepared_sha256=preparedSha,source_hash_seconds=sourceHashSeconds,prepared_hash_seconds=preparedHashSeconds,
            prepared_image=JsonSerializer.SerializeToElement(preparedIdentity,ResourceGuestBinding.Json),steps=steps.ToArray() },token);
    }

    internal void ValidateForResource(string imagePath,string sourcePath,string sha,FileStream lease)
    {
        Require("prepared");
        if(!ReferenceEquals(lease,sessionLease) || !string.Equals(Path.GetFullPath(sourcePath),source.Name,StringComparison.OrdinalIgnoreCase)
            || sha!=sourceSha || preparedIdentity==null || !string.Equals(Path.GetFullPath(imagePath),preparedIdentity.Path,StringComparison.OrdinalIgnoreCase))
            throw new IOException("Resource session does not match its actual preparation chain.");
        using var probe=new FileStream(imagePath,FileMode.Open,FileAccess.Read,FileShare.ReadWrite);
        if(MediaSource.FromHandle(imagePath,probe)!=preparedIdentity)throw new IOException("Prepared generation changed after actual source conversion.");
        _=ReceiptBytes;
    }

    internal void ReleasePreparedForGuest()
    { Require("prepared"); preparedPin?.Dispose(); preparedPin=null; }

    public async Task<string> IntegrateAsync(string imagePath,string selection,string? secureInput,string payload,
        ResourceGuestBinding binding,Action<string> log,CancellationToken token)
    {
        Require("prepared");
        if(preparedIdentity==null || !string.Equals(Path.GetFullPath(imagePath),preparedIdentity.Path,StringComparison.OrdinalIgnoreCase))
            throw new IOException("Integration must consume the actual prepared generation.");
        var result=await Backend.GuestAsync("integration",imagePath,true,log,token,selection:selection,
            secureInput:secureInput,integrationPayload:payload,resourceBinding:binding);
        steps.Add(new { operation="Backend.GuestAsync:integration", input=imagePath,
            transcript_sha256=Convert.ToHexStringLower(SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(result))) });
        await SealAsync(imagePath,token).ConfigureAwait(false); return result;
    }

    public void Dispose()
    { if(disposed)return; disposed=true; receipt?.Dispose(); preparedPin?.Dispose(); foreach(var stream in retained)stream.Dispose(); source.Dispose(); }
}
