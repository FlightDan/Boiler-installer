using System.IO;
using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace OxpInstaller;

// A host-owned development-session capability, never deserialized from options.
// Its caller keeps the existing InstallationController session lease alive.
public sealed class ResourceGuestBinding : IDisposable
{
    public const string Purpose = "resource-guest-local-test";
    public static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
    private readonly List<FileStream> handles = [];
    private readonly List<(string Name, FileStream Stream, string Sha256)> members = [];
    private readonly List<(string Name, FileStream Stream, string Sha256)> runtimeMembers = [];
    private readonly FileStream sessionLease;
    private FileStream? image;
    private MediaSource? imageIdentity;
    private HelperRequestBinding? binding;
    private ResourceSelection? selection;
    private JsonElement? targetDisk;
    private byte[] preparationBytes = [];
    private string preparationDigest = "";
    private string preparedImageSha256 = "";
    public double LastImageHashSeconds { get; private set; }
    internal long PreparedLength => !disposed && imageIdentity is not null ? imageIdentity.Length
        : throw new ObjectDisposedException(nameof(ResourceGuestBinding));
    private string directory = "", action = "", sessionId = "", nonce = "";
    private bool disposed;
    public string Digest => binding?.Digest ?? throw new ObjectDisposedException(nameof(ResourceGuestBinding));
    public string BindingPath => Path.Combine(directory, "resource-session-" + nonce + ".json");
    private ResourceGuestBinding(FileStream lease) { sessionLease = lease; }

    public static ResourceGuestBinding OpenFirstbootSession(string directory, string sessionId, FileStream sessionLease,
        string sourcePath, string sourceSha256, string imagePath, string poolRoot, ResourceSelection selection,
        string action, string parserTools, ResourcePreparationChain preparation, JsonElement? reviewedTargetDisk = null)
        => OpenDevelopmentSession(directory, sessionId, sessionLease, sourcePath, sourceSha256, imagePath, poolRoot,
            selection, action, JsonSerializer.SerializeToElement(new { execution = "firstboot-install-v1" }), parserTools, preparation, reviewedTargetDisk);

    public static ResourceGuestBinding OpenDevelopmentSession(string directory, string sessionId, FileStream sessionLease,
        string sourcePath, string sourceSha256, string imagePath, string poolRoot, ResourceSelection selection,
        string action, JsonElement physicalFacts, string parserTools, ResourcePreparationChain preparation, JsonElement? reviewedTargetDisk = null)
    {
        var value = new ResourceGuestBinding(sessionLease);
        try
        {
            directory = Path.GetFullPath(directory);
            if (!Regex.IsMatch(sessionId, "^[0-9a-f]{32}$") || Path.GetFileName(directory) != "install-session-" + sessionId
                || !sessionLease.CanWrite || sessionLease.SafeFileHandle.IsClosed
                || !string.Equals(Path.GetFullPath(sessionLease.Name), Path.Combine(directory, "operation.lock"), StringComparison.OrdinalIgnoreCase))
                throw new IOException("An active matching installation session lease is required.");
            if (action is not ("apply" or "verify") || selection.SchemaVersion != 1 || selection.Trust != "local-test"
                || !Regex.IsMatch(selection.CatalogSha256 ?? "", "^[0-9a-f]{64}$")
                || !Regex.IsMatch(selection.ModelId ?? "", "^device:[a-z][a-z0-9-]*$")
                || selection.Components is not { Length: > 0 and <= 128 } || selection.Components.Distinct().Count() != selection.Components.Length
                || selection.Components.Any(id => !Regex.IsMatch(id ?? "", "^resource:[a-z0-9][a-z0-9_.-]*$"))
                || !Regex.IsMatch(sourceSha256, "^[0-9a-f]{64}$") || physicalFacts.ValueKind != JsonValueKind.Object)
                throw new IOException("Invalid development-session resource binding.");
            value.directory = directory; value.action = action; value.sessionId = sessionId; value.nonce = Guid.NewGuid().ToString("N");
            value.selection = selection with { Components = selection.Components.ToArray() };
            if (reviewedTargetDisk is { } disk && (disk.ValueKind != JsonValueKind.Object
                || disk.EnumerateObject().Select(p => p.Name).Order().SequenceEqual(new[] { "bus", "model" }) != true
                || disk.GetProperty("bus").ValueKind != JsonValueKind.String || disk.GetProperty("model").ValueKind != JsonValueKind.String))
                throw new IOException("Invalid reviewed physical target descriptor.");
            value.targetDisk = reviewedTargetDisk?.Clone();
            preparation.ValidateForResource(imagePath,sourcePath,sourceSha256,sessionLease);
            value.preparationBytes=preparation.ReceiptBytes;
            value.preparationDigest=preparation.ReceiptDigest;
            using(var provenance=JsonDocument.Parse(value.preparationBytes))
                value.preparedImageSha256=provenance.RootElement.GetProperty("prepared_sha256").GetString()!;
            MediaSource.Ordinary(sourcePath, directory); MediaSource.Ordinary(imagePath, directory);
            if (string.Equals(Path.GetFullPath(sourcePath), Path.GetFullPath(imagePath), StringComparison.OrdinalIgnoreCase))
                throw new IOException("Resource staging requires a distinct owned prepared-image copy.");
            var source = new FileStream(sourcePath, FileMode.Open, FileAccess.Read, FileShare.Read);
            value.handles.Add(source);
            // The operation-issued chain already hashed and keeps this exact
            // reviewed source readonly. Do not hash a multi-GiB source again
            // synchronously on the WPF thread.
            // Keep the mutable prepared image's file identity pinned against
            // deletion/replacement; QEMU must retain its normal raw-image lock.
            PreparedDisk layout;
            using (var prepared = new PreparedDiskImage(imagePath)) layout = prepared.Layout;
            value.image = new FileStream(imagePath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
            value.handles.Add(value.image); value.imageIdentity = MediaSource.FromHandle(imagePath, value.image);
            foreach (var path in FilesWithoutLinks(poolRoot).Order(StringComparer.Ordinal))
            {
                MediaSource.Ordinary(path, poolRoot);
                var name = Path.GetRelativePath(poolRoot, path).Replace('\\', '/');
                if (name.Split('/').Any(p => p is "" or "." or ".." || p.EndsWith('.') || p.EndsWith(' ') || p.Contains(':')))
                    throw new IOException("Unsafe resource pool path.");
                var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
                value.handles.Add(stream); var hash = Convert.ToHexStringLower(SHA256.HashData(stream)); stream.Position = 0;
                value.members.Add((name, stream, hash));
            }
            if (value.members.SingleOrDefault(m => m.Name == "catalog.toon").Sha256 != selection.CatalogSha256)
                throw new IOException("Resource catalog changed after selection.");
            var parserLock = Path.Combine(parserTools, "toon-parser-lock.json"); MediaSource.Ordinary(parserLock, parserTools);
            var lockHandle = new FileStream(parserLock, FileMode.Open, FileAccess.Read, FileShare.Read); value.handles.Add(lockHandle);
            var lockHash = Convert.ToHexStringLower(SHA256.HashData(lockHandle)); lockHandle.Position = 0;
            value.runtimeMembers.Add(("adaptations/toon-parser-lock.json", lockHandle, lockHash));
            using (var parser = JsonDocument.Parse(lockHandle))
            {
                var data = parser.RootElement;
                if (data.GetProperty("package").GetString() != "toon-format" || data.GetProperty("version").GetString() != "1.2.0"
                    || data.GetProperty("spec").GetString() != "4.4") throw new IOException("Unexpected trusted parser lock.");
                foreach (var row in data.GetProperty("files").EnumerateArray())
                {
                    var name = row.GetProperty("path").GetString() ?? throw new IOException("Parser path missing.");
                    if (name.Contains('\\') || name.Split('/').Any(p => p is "" or "." or ".." || p.Contains(':')))
                        throw new IOException("Unsafe trusted parser path.");
                    var path = Path.Combine(parserTools, "vendor", name.Replace('/', Path.DirectorySeparatorChar)); MediaSource.Ordinary(path, parserTools);
                    var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read); value.handles.Add(stream);
                    var hash = Convert.ToHexStringLower(SHA256.HashData(stream)); stream.Position = 0;
                    if (stream.Length != row.GetProperty("size").GetInt64() || hash != row.GetProperty("sha256").GetString())
                        throw new IOException("Trusted parser bytes differ from lock.");
                    value.runtimeMembers.Add(("adaptations/vendor/" + name, stream, hash));
                }
            }
            lockHandle.Position = 0;
            var request = new { schema_version = 1, kind = "BoilerResourceGuestSession", trust = "development-session",
                session_id = sessionId, nonce = value.nonce, action, selection = JsonSerializer.SerializeToElement(value.selection, Json), source_sha256 = sourceSha256,
                image = new { length = value.imageIdentity.Length, disk_guid = value.imageIdentity.DiskGuid.ToString("D"),
                    volume = value.imageIdentity.Volume, file_id = value.imageIdentity.FileId,
                    partitions = layout.Partitions.Select(p => new { number = p.Number, name = p.Name, type = p.Type.ToString("D"),
                        id = p.Id.ToString("D"), offset = p.Offset, size = p.Size }).ToArray() },
                files = value.members.Select(m => new { path = m.Name, size = m.Stream.Length, sha256 = m.Sha256 }).ToArray(),
                runtime_files = value.runtimeMembers.Select(m => new { path = m.Name, size = m.Stream.Length, sha256 = m.Sha256 }).ToArray(),
                preparation_sha256 = value.preparationDigest,
                execution = physicalFacts.TryGetProperty("execution", out var execution) && execution.GetString() == "firstboot-install-v1" ? "firstboot-install-v1" : "legacy-physical-facts",
                target_disk = value.targetDisk,
                physical_facts = physicalFacts.TryGetProperty("execution", out _) ? JsonSerializer.SerializeToElement(new { }) : physicalFacts.Clone() };
            // Serialized exactly once; digest travels in the trusted launch
            // channel, outside the mutable/read-only payload files.
            value.binding = HelperRequestBinding.Create(value.BindingPath, Purpose, request);
            preparation.ReleasePreparedForGuest();
            return value;
        }
        catch { value.Dispose(); throw; }
    }

    public void Revalidate(string imagePath, string task, string options)
        => RevalidateAsync(imagePath,task,options,CancellationToken.None).GetAwaiter().GetResult();

    internal void ValidateForGuest(string imagePath, string task, string options)
    {
        if (disposed || !sessionLease.CanWrite || sessionLease.SafeFileHandle.IsClosed || image == null || imageIdentity == null)
            throw new IOException("Resource session lease is no longer alive.");
        if (task != (action == "apply" ? "integration" : "integration-verify")
            || !string.Equals(Path.GetFullPath(imagePath), imageIdentity.Path, StringComparison.OrdinalIgnoreCase))
            throw new IOException("Resource capability purpose or image changed.");
        var current = MediaSource.FromHandle(imagePath, image);
        if (current != imageIdentity)
            throw new IOException("Prepared image identity changed.");
        var parsed = JsonNode.Parse(options) ?? throw new IOException("Missing options.");
        if (!JsonNode.DeepEquals(parsed["resource_selection"], JsonSerializer.SerializeToNode(selection, Json)))
            throw new IOException("Resource choices changed after session binding.");
        if (!JsonNode.DeepEquals(parsed["_target_disk"], targetDisk is { } disk ? JsonSerializer.SerializeToNode(disk) : null))
            throw new IOException("The reviewed physical target descriptor changed after image binding.");
        HelperRequestBinding.ReadVerified(BindingPath, Purpose, Digest, HelperRequestBinding.SmallLimit);
    }

    public Task RevalidateAsync(string imagePath,string task,string options,CancellationToken token)
        => RevalidateAsync(imagePath,task,options,token,null);

    internal async Task RevalidateAsync(string imagePath,string task,string options,CancellationToken token,Action<string>? report)
    {
        ValidateForGuest(imagePath,task,options);
        var watch=Stopwatch.StartNew();
        // Hash the actual file through the same held deny-write handle. A
        // same-length writer that restores mtime cannot satisfy this check.
        using var pin=new FileStream(imagePath,FileMode.Open,FileAccess.Read,FileShare.Read);
        report?.Invoke(ResourceIntegrityProgress.Report(action,"host",0,pin.Length,false));
        using var bytes=RawImageStaging.SparseRead(pin,token);
        var hash=await ResourceImageHasher.HashAsync(bytes,pin.Length,token,
            done=>report?.Invoke(ResourceIntegrityProgress.Report(action,"host",done,pin.Length,false))).ConfigureAwait(false);
        LastImageHashSeconds=watch.Elapsed.TotalSeconds;
        if(hash!=preparedImageSha256)throw new IOException("Prepared image bytes differ from the actual preparation receipt.");
        ValidateForGuest(imagePath,task,options);
        report?.Invoke(ResourceIntegrityProgress.Report(action,"host",pin.Length,pin.Length,true));
    }

    public void StagePayload(string payload)
    {
        if (disposed) throw new ObjectDisposedException(nameof(ResourceGuestBinding));
        foreach (var member in members.Select(m => (Name: "integration/resource-pool/" + m.Name, m.Stream, m.Sha256)).Concat(runtimeMembers))
        {
            var target = Path.Combine(payload, member.Name.Replace('/', Path.DirectorySeparatorChar));
            MediaSource.Ordinary(target, payload, false); Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            if (File.Exists(target))
            {
                using var existing = new FileStream(target, FileMode.Open, FileAccess.Read, FileShare.Read);
                if (existing.Length != member.Stream.Length || Convert.ToHexStringLower(SHA256.HashData(existing)) != member.Sha256)
                    throw new IOException("Existing payload copy differs from retained resource session.");
                continue;
            }
            using var output = new FileStream(target, FileMode.CreateNew, FileAccess.Write, FileShare.None);
            member.Stream.Position = 0; member.Stream.CopyTo(output); output.Flush(true); member.Stream.Position = 0;
        }
        var bytes = HelperRequestBinding.ReadVerified(BindingPath, Purpose, Digest, HelperRequestBinding.SmallLimit);
        var destination = Path.Combine(payload, "resource-session.json"); MediaSource.Ordinary(destination, payload, false);
        using var document = new FileStream(destination, FileMode.CreateNew, FileAccess.Write, FileShare.None);
        document.Write(bytes); document.Flush(true);
        var provenance=Path.Combine(payload,"resource-preparation.json"); MediaSource.Ordinary(provenance,payload,false);
        using var prepared=new FileStream(provenance,FileMode.CreateNew,FileAccess.Write,FileShare.None);
        prepared.Write(preparationBytes); prepared.Flush(true);
    }

    // Called only while Backend owns the running VM and the same session lease.
    // This fixed command carries the commitment outside the payload. It does
    // not turn unsigned development bytes into a production signed release.
    public string AnchorCommand()
    {
        if (disposed || !sessionLease.CanWrite) throw new IOException("Resource session ended.");
        var anchor = Convert.ToBase64String(JsonSerializer.SerializeToUtf8Bytes(new { schema_version = 1,
            kind = "BoilerResourceGuestAnchor", purpose = Purpose, session_id = sessionId, nonce,
            action, binding_sha256 = Digest }, Json));
        return "( umask 077; set -C; printf '%s' '" + anchor + "' | base64 -d > /run/boiler-resource-anchor.json )";
    }

    public void Dispose()
    {
        if (disposed) return; disposed = true; binding?.Dispose(); binding = null;
        foreach (var handle in handles) handle.Dispose(); handles.Clear();
    }

    private static IEnumerable<string> FilesWithoutLinks(string root)
    {
        var directories = new Stack<string>(); directories.Push(root);
        while (directories.TryPop(out var directory))
        {
            if ((File.GetAttributes(directory) & FileAttributes.ReparsePoint) != 0) throw new IOException("Linked resource directory.");
            foreach (var path in Directory.EnumerateFileSystemEntries(directory))
            {
                var attributes = File.GetAttributes(path);
                if ((attributes & FileAttributes.ReparsePoint) != 0) throw new IOException("Linked resource member.");
                if ((attributes & FileAttributes.Directory) != 0) directories.Push(path); else yield return path;
            }
        }
    }
}
