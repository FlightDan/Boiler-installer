using System.Buffers.Binary;
using System.Diagnostics;
using System.Formats.Tar;
using System.IO;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace OxpInstaller;

internal enum InputKind { RecoveryImage, DiskImage, UpgradeRoot, UpgradeBundle, DevicePackage, DeviceScript, Unknown }
// HeaderSha256 covers only the first 128 KiB. This is a preview freshness hint,
// never an authenticated full-file digest or permission to execute anything.
internal sealed record InputFileStamp(long Length, long LastWriteUtcTicks, string HeaderSha256);
internal sealed record RaucManifestInfo(string Compatible, string Version, string RootfsFilename)
{
    internal bool IsSupportedByCurrentInstaller => Version == OfficialSystem.Version || Version == OfficialSystem.Version + ".0";
}
internal sealed record DiscoveredInput(string Path, InputKind Kind, string Name, string Detail,
    InputFileStamp? Stamp, JsonElement? DeviceMetadata = null, string? Error = null, RaucManifestInfo? RaucManifest = null);

internal static class InputDiscovery
{
    private const int HeaderLimit = 128 * 1024, ManifestLimit = 8 * 1024 * 1024, EntryLimit = 10000;
    private const long TarBudget = 32L * 1024 * 1024;
    private static readonly string[] RecoveryRoles = ["esp", "efi-A", "rootfs-A", "var-A", "home"];
    private static string L(string zh, string en) => PartitionInventory.L(zh, en);

    internal static string SafePath(string path)
    {
        if (string.IsNullOrWhiteSpace(path) || path.Length > 32767 || path.StartsWith(@"\\", StringComparison.Ordinal)
            || path.StartsWith("//", StringComparison.Ordinal) || path.Any(char.IsControl))
            throw new IOException(L("只接受本地普通文件或文件夹。", "Only ordinary local files or folders are accepted."));
        var full = System.IO.Path.GetFullPath(path);
        if (!Regex.IsMatch(full, @"^[A-Za-z]:[\\/]") || full[2..].Contains(':'))
            throw new IOException("UNC, device and alternate-stream paths are unsupported.");
        if (full[3..].Split(['\\', '/'], StringSplitOptions.RemoveEmptyEntries).Any(part => part.EndsWith('.') || part.EndsWith(' ')
            || Regex.IsMatch(part.Split('.')[0], @"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$", RegexOptions.IgnoreCase)))
            throw new IOException("Reserved device names and ambiguous local paths are unsupported.");
        for (var current = full; current != null; current = System.IO.Path.GetDirectoryName(current))
        {
            try
            {
                if ((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                    throw new IOException("Links and junctions are unsupported.");
            }
            catch (FileNotFoundException) { }
            catch (DirectoryNotFoundException) { }
            if (System.IO.Path.GetPathRoot(current) == current) break;
        }
        return full;
    }

    internal static InputFileStamp ReadStamp(string path)
    {
        path = SafePath(path);
        if (Directory.Exists(path)) path = SafePath(System.IO.Path.Combine(path, "manifest.json"));
        using var source = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        var length = source.Length;
        var bytes = new byte[(int)Math.Min(length, HeaderLimit)];
        source.ReadExactly(bytes);
        var ticks = File.GetLastWriteTimeUtc(path).Ticks;
        if (source.Length != length) throw new IOException("Input changed during inspection.");
        return new(length, ticks, Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant());
    }

    internal static void VerifyStamp(DiscoveredInput input)
    {
        if (input.Stamp == null || ReadStamp(input.Path) != input.Stamp)
            throw new IOException(L("文件已改变，请重新识别。", "The file changed. Inspect it again."));
    }

    internal static Task<DiscoveredInput> InspectAsync(string path, CancellationToken token) => Task.Run(async () =>
    {
        token.ThrowIfCancellationRequested();
        InputFileStamp? stamp = null;
        try
        {
            path = SafePath(path);
            stamp = ReadStamp(path);
            DiscoveredInput Result(InputKind kind, string detail, JsonElement? metadata = null, string? name = null,
                string? error = null, RaucManifestInfo? raucManifest = null)
                => new(path, kind, name ?? System.IO.Path.GetFileName(path), detail, stamp, metadata, error, raucManifest);
            DiscoveredInput result;
            if (Directory.Exists(path))
            {
                var metadata = ParseDevice(await ReadBoundedFileAsync(System.IO.Path.Combine(path, "manifest.json"), ManifestLimit, token));
                var entries = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                var files = new HashSet<string>(StringComparer.Ordinal);
                var directories = new Stack<string>(); directories.Push(path);
                while (directories.TryPop(out var directory))
                foreach (var file in Directory.EnumerateFileSystemEntries(directory))
                {
                    token.ThrowIfCancellationRequested(); SafePath(file);
                    if (entries.Count >= EntryLimit) throw new IOException("Too many package entries.");
                    var relative = System.IO.Path.GetRelativePath(path, file).Replace('\\', '/'); ArchivePath(relative);
                    if (!entries.Add(relative)) throw new IOException("Duplicate package entry.");
                    if (Directory.Exists(file)) directories.Push(file); else files.Add(relative);
                }
                if (!files.SetEquals(metadata.GetProperty("files").EnumerateArray().Select(item => item.GetProperty("path").GetString()!).Append("manifest.json")))
                    throw new IOException("Package folder inventory differs from manifest.");
                result = Result(InputKind.DevicePackage, DeviceDetail(metadata), metadata, DeviceName(metadata));
            }
            else
            {
                var header = await ReadBoundedFileAsync(path, HeaderLimit, token, prefix: true);
                if (header.Length >= 0x10048 && header.AsSpan(0x10040, 8).SequenceEqual("_BHRfS_M"u8))
                {
                    OfficialSystem.ValidateRoot(path);
                    result = Result(InputKind.UpgradeRoot, L("已识别 Btrfs 系统分区；版本将在后续安装准备中检查，不根据文件名或镜像头部推测。", "Btrfs system partition identified. Its version will be checked during installation preparation; it is not inferred from the filename or image header."));
                }
                else if (header.Length >= 520 && header.AsSpan(512, 8).SequenceEqual("EFI PART"u8))
                {
                    RecoveryImage.ValidateRaw(path, true);
                    var prepared = RecoveryImage.IsPrepared(path);
                    result = Result(prepared ? InputKind.DiskImage : InputKind.RecoveryImage,
                        prepared ? L("完整 A/B 磁盘布局；系统版本、内容和容量待后续只读检查。", "Complete A/B disk layout; system version, contents and required capacity need further read-only inspection.")
                            : L("完整恢复磁盘布局；可作恢复来源或整体导入，系统版本待检查。", "Complete recovery disk layout; usable as recovery source or whole-disk import. System version still needs inspection."));
                }
                else if (header.AsSpan().StartsWith("PK"u8)) result = await InspectZipAsync(path, stamp, token);
                else if (header.AsSpan().StartsWith("hsqs"u8))
                {
                    var manifest = await SevenZipAsync(path, ["x", "-so", "-bd", "-bb0", "-y", "-spd", "-p", "--", path, "manifest.raucm"], 65536, false, token);
                    var update = ParseRaucManifest(manifest);
                    var supported = update.IsSupportedByCurrentInstaller;
                    result = Result(InputKind.UpgradeBundle,
                        supported
                            ? L($"升级包版本：SteamOS {update.Version}。实际系统镜像将在安装准备中重建。", $"Upgrade package version: SteamOS {update.Version}. The system image is rebuilt during installation preparation.")
                            : L($"检测到 SteamOS {update.Version} 更新包；当前安装流程不支持此版本。", $"SteamOS {update.Version} update bundle detected; this installer does not support this version."),
                        error: supported ? null : UnsupportedRaucVersionError(update.Version), raucManifest: update);
                }
                else if (header.AsSpan().StartsWith("#!"u8))
                {
                    var script = await ReadBoundedFileAsync(path, ManifestLimit, token);
                    var name = CheckScript(script);
                    result = Result(InputKind.DeviceScript, L("Boiler 三阶段脚本声明；尚未授予本次安装信任，未执行。", "Boiler three-phase script declaration; no installation session trust has been granted and no code has run."), name: name ?? System.IO.Path.GetFileName(path));
                }
                else if (header.AsSpan().StartsWith(new byte[] { 0x1f, 0x8b }))
                {
                    using var input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
                    using var gzip = new GZipStream(input, CompressionMode.Decompress);
                    result = InspectTar(gzip, path, stamp, token);
                }
                else if (IsTar(header))
                {
                    using var input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
                    result = InspectTar(input, path, stamp, token);
                }
                else if (header.AsSpan().StartsWith("BZh"u8) || header.AsSpan().StartsWith(new byte[] { 0xfd, 0x37, 0x7a, 0x58, 0x5a, 0 }))
                {
                    var type = header[0] == (byte)'B' ? "-tbzip2" : "-txz";
                    var prefix = await SevenZipAsync(path, ["x", type, "-so", "-bd", "-bb0", "-y", "-p", "--", path], HeaderLimit, true, token);
                    if (IsTar(prefix))
                    {
                        var unpacked = await SevenZipAsync(path, ["x", type, "-so", "-bd", "-bb0", "-y", "-p", "--", path], (int)TarBudget, false, token);
                        using var tar = new MemoryStream(unpacked, writable: false);
                        result = InspectTar(tar, path, stamp, token);
                    }
                    else
                    {
                        if (type == "-txz") throw new IOException(L("已识别 XZ 压缩内容；当前安装入口不支持 XZ 镜像，请先转换为 IMG、ZIP 或 BZ2。", "XZ compressed content detected. Image installation currently needs IMG, ZIP or BZ2."));
                        var kind = PrefixGpt(prefix, null);
                        result = Result(kind, PartialImageDetail());
                    }
                }
                else throw new IOException(L("无法按内容识别为受支持的镜像、SteamOS 更新包或机型包。", "Content is not a supported disk image, SteamOS update bundle or device package."));
            }
            token.ThrowIfCancellationRequested();
            VerifyStamp(result);
            return result;
        }
        catch (OperationCanceledException) { throw; }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException or JsonException or InvalidOperationException or NotSupportedException or System.ComponentModel.Win32Exception or KeyNotFoundException or FormatException or OverflowException)
        { return new DiscoveredInput(path, InputKind.Unknown, System.IO.Path.GetFileName(path), "", stamp, Error: error.Message); }
    }, token);

    private static string PartialImageDetail() => L("压缩文件内已识别 SteamOS GPT 布局；仅检查前 128 KiB，完整解压、备份 GPT、摘要与系统版本待继续检查。", "SteamOS GPT layout detected inside compressed content. Only the first 128 KiB was inspected; full extraction, backup GPT, digest and system version still need validation.");
    private static bool IsTar(byte[] bytes) => bytes.Length >= 262 && bytes.AsSpan(257, 5).SequenceEqual("ustar"u8);

    private static async Task<byte[]> ReadBoundedFileAsync(string path, int maximum, CancellationToken token, bool prefix = false)
    {
        SafePath(path);
        using var file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        return await ReadBoundedAsync(file, maximum, token, prefix);
    }
    private static async Task<byte[]> ReadBoundedAsync(Stream stream, int maximum, CancellationToken token, bool prefix = false)
    {
        using var result = new MemoryStream();
        var buffer = new byte[8192];
        while (result.Length < maximum + (prefix ? 0L : 1L))
        {
            var read = await stream.ReadAsync(buffer.AsMemory(0, (int)Math.Min(buffer.Length, maximum + (prefix ? 0L : 1L) - result.Length)), token);
            if (read == 0) break;
            result.Write(buffer, 0, read);
        }
        if (result.Length > maximum) throw new IOException("Preview metadata exceeds size limit.");
        return result.ToArray();
    }

    private static string? CheckScript(byte[] raw)
    {
        var text = new UTF8Encoding(false, true).GetString(raw);
        var lines = text.Split('\n');
        if (lines[0] is not ("#!/bin/bash" or "#!/usr/bin/env bash") || text.Contains('\r') || text.Contains('\0'))
            throw new IOException("Boiler scripts require UTF-8/LF Bash and # boiler-script: 1.");
        string? declaration = null, name = null;
        for (var index = 1; index < Math.Min(64, lines.Length); index++)
        {
            var line = lines[index];
            if (string.IsNullOrWhiteSpace(line)) continue;
            if (!line.StartsWith('#')) break;
            if (line.StartsWith("# boiler-script:", StringComparison.Ordinal))
            { if (declaration != null) throw new IOException("Duplicate script declaration."); declaration = line[16..].Trim(); }
            if (line.StartsWith("# boiler-name:", StringComparison.Ordinal))
            { if (name != null) throw new IOException("Duplicate script name."); name = line[14..].Trim(); }
        }
        if (declaration != "1" || name is { Length: > 256 } || name != null && (name.Length == 0 || name.Any(c => c < 32 || c is >= '\x7f' and <= '\x9f')))
            throw new IOException("Adapt the script to apply/verify/restore and declare # boiler-script: 1. See docs/DEVICE_PACKAGES.md.");
        return name;
    }

    private static JsonElement ParseDevice(byte[] raw)
    {
        using var document = JsonDocument.Parse(raw, new JsonDocumentOptions { MaxDepth = 64 });
        var value = document.RootElement;
        CheckJsonDuplicates(value);
        if (value.ValueKind != JsonValueKind.Object || !value.TryGetProperty("schema_version", out var schema) || !schema.TryGetInt32(out var version) || version != 1
            || !value.TryGetProperty("kind", out var kind) || kind.GetString() != "DevicePackage"
            || !value.TryGetProperty("id", out var id) || id.ValueKind != JsonValueKind.String || !Regex.IsMatch(id.GetString()!, @"^[a-z][a-z0-9_-]*:[a-z0-9][a-z0-9_.-]*$")
            || !value.TryGetProperty("version", out var packageVersion) || packageVersion.ValueKind != JsonValueKind.String
            || !Regex.IsMatch(packageVersion.GetString()!, @"^[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.+-]+)?$")
            || !value.TryGetProperty("components", out var components) || components.ValueKind != JsonValueKind.Array || components.GetArrayLength() > EntryLimit
            || !value.TryGetProperty("files", out var files) || files.ValueKind != JsonValueKind.Array || files.GetArrayLength() > EntryLimit)
            throw new IOException("Invalid DevicePackage metadata.");
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var file in files.EnumerateArray())
        {
            if (!file.TryGetProperty("path", out var name) || name.ValueKind != JsonValueKind.String) throw new IOException("Invalid package inventory.");
            ArchivePath(name.GetString()!);
            if (!seen.Add(name.GetString()!)) throw new IOException("Duplicate package inventory.");
        }
        return value.Clone();
    }
    private static void CheckJsonDuplicates(JsonElement value)
    {
        if (value.ValueKind == JsonValueKind.Object)
        {
            var keys = new HashSet<string>(StringComparer.Ordinal);
            foreach (var property in value.EnumerateObject())
            { if (!keys.Add(property.Name)) throw new IOException("Duplicate JSON key."); CheckJsonDuplicates(property.Value); }
        }
        else if (value.ValueKind == JsonValueKind.Array) foreach (var item in value.EnumerateArray()) CheckJsonDuplicates(item);
    }
    private static string? DeviceName(JsonElement metadata)
    {
        if (!metadata.TryGetProperty("name", out var name)) return metadata.GetProperty("id").GetString();
        if (name.ValueKind == JsonValueKind.String) return name.GetString();
        return name.ValueKind == JsonValueKind.Object && name.TryGetProperty(InstallerI18n.Language == "en-US" ? "en" : "zh", out var localized)
            && localized.ValueKind == JsonValueKind.String ? localized.GetString() : metadata.GetProperty("id").GetString();
    }
    private static string DeviceDetail(JsonElement metadata) => metadata.TryGetProperty("purpose", out var purpose) && purpose.ValueKind == JsonValueKind.String && purpose.GetString() == "maintenance"
        ? L("维护专用包；不能关联为新装机型包。", "Maintenance-only package; it cannot be assigned as an installation device package.")
        : L("已识别机型包清单；导入时检查完整文件摘要。", "Device package manifest detected; full file digests are checked during import.");

    private static void ArchivePath(string name)
    {
        if (string.IsNullOrEmpty(name) || name.Length > 4096 || name.Contains('\\') || name.Contains(':') || name.Any(char.IsControl)
            || name.StartsWith('/') || name.Split('/').Any(part => part is "" or "." or ".." || part.EndsWith('.') || part.EndsWith(' ')
                || Regex.IsMatch(part.Split('.')[0], @"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$", RegexOptions.IgnoreCase)))
            throw new IOException("Unsafe archive path.");
    }

    private static async Task<DiscoveredInput> InspectZipAsync(string path, InputFileStamp stamp, CancellationToken token)
    {
        using var file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        CheckZipDirectory(file);
        file.Position = 0;
        using var zip = new ZipArchive(file, ZipArchiveMode.Read);
        var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var entry in zip.Entries)
        {
            token.ThrowIfCancellationRequested();
            var name = entry.FullName.TrimEnd('/'); ArchivePath(name);
            if (!names.Add(name) || ((entry.ExternalAttributes >> 16) & 0xf000) is not (0 or 0x8000 or 0x4000))
                throw new IOException("Duplicate archive path or archive link.");
            if (!entry.FullName.EndsWith('/') && ((entry.ExternalAttributes >> 16) & 0xf000) == 0x4000)
                throw new IOException("Archive entry type and filename disagree.");
            if (entry.Length < 0 || entry.Length > ImageImportStreams.MaximumImageBytes) throw new IOException("Oversized archive entry.");
        }
        var files = zip.Entries.Where(entry => !entry.FullName.EndsWith('/')).ToArray();
        var manifests = files.Where(entry => entry.FullName == "manifest.json" || entry.FullName.Count(c => c == '/') == 1 && entry.Name == "manifest.json").ToArray();
        if (manifests.Length == 1)
        {
            var manifest = manifests[0];
            var prefix = manifest.FullName[..^"manifest.json".Length];
            if (files.Any(entry => !entry.FullName.StartsWith(prefix, StringComparison.Ordinal))) throw new IOException("Package has extra wrapper siblings.");
            using var stream = manifest.Open();
            var metadata = ParseDevice(await ReadBoundedAsync(stream, ManifestLimit, token));
            var inventory = metadata.GetProperty("files").EnumerateArray().Select(entry => prefix + entry.GetProperty("path").GetString()).Append(manifest.FullName).ToHashSet(StringComparer.Ordinal);
            if (!inventory.SetEquals(files.Select(entry => entry.FullName))) throw new IOException("Package archive inventory differs from manifest.");
            return new(path, InputKind.DevicePackage, DeviceName(metadata) ?? System.IO.Path.GetFileName(path), DeviceDetail(metadata), stamp, metadata);
        }
        if (manifests.Length > 1 || files.Length is < 1 or > 2) throw new IOException("ZIP has ambiguous or unsupported contents.");
        var imageManifest = files.SingleOrDefault(entry => entry.FullName == "image-manifest.json");
        var images = files.Where(entry => entry != imageManifest).ToArray();
        if (images.Length != 1 || images[0].FullName != images[0].Name) throw new IOException("Expected exactly one root disk image.");
        var image = images[0];
        if (imageManifest != null)
        {
            using var stream = imageManifest.Open();
            using var metadata = JsonDocument.Parse(await ReadBoundedAsync(stream, 1024 * 1024, token));
            var value = metadata.RootElement; CheckJsonDuplicates(value);
            if (value.GetProperty("kind").GetString() != "ImageManifest" || value.GetProperty("schema_version").GetInt32() != 1
                || value.GetProperty("architecture").GetString() != "x86_64" || value.GetProperty("image").GetProperty("path").GetString() != image.FullName
                || value.GetProperty("image").GetProperty("size").GetInt64() != image.Length)
                throw new IOException("Image manifest and ZIP entry differ.");
        }
        using var imageStream = image.Open();
        var prefixBytes = await ReadBoundedAsync(imageStream, HeaderLimit, token, prefix: true);
        var detected = PrefixGpt(prefixBytes, image.Length);
        if (!image.Name.EndsWith(".img", StringComparison.OrdinalIgnoreCase))
            throw new IOException(L("ZIP 内已识别磁盘镜像，但当前导入要求内部文件使用 .img 扩展名。请重新制作归档。", "Disk image detected inside ZIP, but the current importer requires its internal filename to end in .img. Recreate the archive."));
        return new(path, detected, System.IO.Path.GetFileName(path), PartialImageDetail(), stamp);
    }

    private static DiscoveredInput InspectTar(Stream input, string path, InputFileStamp stamp, CancellationToken token)
    {
        using var bounded = new BudgetStream(input, TarBudget, token);
        using var tar = new TarReader(bounded, leaveOpen: true);
        var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var files = new List<string>();
        JsonElement? metadata = null; string? prefix = null;
        TarEntry? entry;
        while ((entry = tar.GetNextEntry()) != null)
        {
            token.ThrowIfCancellationRequested();
            var name = entry.Name.TrimEnd('/'); ArchivePath(name);
            if (names.Count >= EntryLimit || !names.Add(name)) throw new IOException("Too many or duplicate TAR entries.");
            if (entry.EntryType == TarEntryType.Directory) continue;
            if (entry.EntryType is not (TarEntryType.RegularFile or TarEntryType.V7RegularFile)) throw new IOException("TAR links and special entries are unsupported.");
            files.Add(name);
            if (name == "manifest.json" || name.Count(c => c == '/') == 1 && name.EndsWith("/manifest.json", StringComparison.Ordinal))
            {
                if (metadata != null || entry.Length > ManifestLimit || entry.DataStream == null) throw new IOException("Ambiguous TAR manifest.");
                var bytes = new byte[checked((int)entry.Length)]; entry.DataStream.ReadExactly(bytes);
                metadata = ParseDevice(bytes); prefix = name[..^"manifest.json".Length];
            }
        }
        if (metadata == null) throw new IOException("No DevicePackage manifest in bounded TAR preview.");
        var inventory = metadata.Value.GetProperty("files").EnumerateArray().Select(item => prefix + item.GetProperty("path").GetString()).Append(prefix + "manifest.json").ToHashSet(StringComparer.Ordinal);
        if (!inventory.SetEquals(files)) throw new IOException("TAR package inventory differs from manifest.");
        return new(path, InputKind.DevicePackage, DeviceName(metadata.Value) ?? System.IO.Path.GetFileName(path), DeviceDetail(metadata.Value), stamp, metadata);
    }

    private sealed class BudgetStream(Stream inner, long maximum, CancellationToken token) : Stream
    {
        private long consumed;
        public override bool CanRead => true;
        public override bool CanSeek => false;
        public override bool CanWrite => false;
        public override long Length => throw new NotSupportedException();
        public override long Position { get => consumed; set => throw new NotSupportedException(); }
        public override int Read(byte[] buffer, int offset, int count) => Read(buffer.AsSpan(offset, count));
        public override int Read(Span<byte> buffer)
        {
            token.ThrowIfCancellationRequested();
            if (consumed >= maximum) throw new IOException("TAR preview exceeds 32 MiB. Use a package folder or ZIP.");
            var count = inner.Read(buffer[..(int)Math.Min(buffer.Length, maximum - consumed)]);
            consumed += count; return count;
        }
        public override void Flush() { }
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
    }

    private static InputKind PrefixGpt(byte[] bytes, long? length)
    {
        if (bytes.Length < 1024 || bytes[510] != 0x55 || bytes[511] != 0xaa || !bytes.AsSpan(512, 8).SequenceEqual("EFI PART"u8)) throw new IOException("Compressed content has no supported GPT header.");
        var header = bytes.AsSpan(512, 512).ToArray();
        uint U32(int offset) => BinaryPrimitives.ReadUInt32LittleEndian(header.AsSpan(offset));
        ulong U64(int offset) => BinaryPrimitives.ReadUInt64LittleEndian(header.AsSpan(offset));
        var size = U32(12); var count = U32(80); var width = U32(84);
        if (U32(8) != 0x10000 || size is < 92 or > 512 || U64(24) != 1 || U64(32) < 34 || U64(40) < 34 || U64(40) > U64(48)
            || U64(48) >= U64(32) || U64(72) < 2 || count is < 1 or > 128 || width != 128 || new Guid(header.AsSpan(56, 16)) == Guid.Empty
            || length != null && (length < 34 * 512 || length % 512 != 0 || U64(32) != (ulong)(length / 512 - 1))) throw new IOException("Invalid compressed GPT geometry.");
        var crc = U32(16); header.AsSpan(16, 4).Clear();
        if (Crc(header.AsSpan(0, (int)size)) != crc) throw new IOException("Invalid primary GPT checksum.");
        if (U64(72) > U64(40) || ((ulong)count * 128 + 511) / 512 > U64(40) - U64(72)) throw new IOException("GPT table overlaps partitions.");
        var tableOffset = checked(U64(72) * 512);
        if (tableOffset > (ulong)bytes.Length || (ulong)count * 128 > (ulong)bytes.Length - tableOffset) throw new IOException("GPT table lies beyond bounded preview.");
        var table = bytes.AsSpan((int)tableOffset, (int)count * 128);
        if (Crc(table) != U32(88)) throw new IOException("Invalid GPT table checksum.");
        var roles = new List<string>(); var ids = new HashSet<Guid>(); var previous = U64(40) - 1;
        for (var index = 0; index < count; index++)
        {
            var entry = table.Slice(index * 128, 128);
            if (new Guid(entry[..16]) == Guid.Empty) continue;
            var start = BinaryPrimitives.ReadUInt64LittleEndian(entry[32..]); var end = BinaryPrimitives.ReadUInt64LittleEndian(entry[40..]);
            if (index != roles.Count || new Guid(entry.Slice(16, 16)) == Guid.Empty || !ids.Add(new Guid(entry.Slice(16, 16))) || start <= previous || end < start || end > U64(48))
                throw new IOException("Invalid GPT partition order.");
            previous = end; roles.Add(Encoding.Unicode.GetString(entry[56..]).TrimEnd('\0'));
        }
        return roles.SequenceEqual(RecoveryRoles) ? InputKind.RecoveryImage : roles.SequenceEqual(PreparedDisk.Roles) ? InputKind.DiskImage : throw new IOException("Unsupported GPT partition roles.");
    }
    private static uint Crc(ReadOnlySpan<byte> bytes)
    { uint crc = uint.MaxValue; foreach (var value in bytes) { crc ^= value; for (var bit = 0; bit < 8; bit++) crc = (crc >> 1) ^ ((crc & 1) == 0 ? 0 : 0xedb88320u); } return ~crc; }

    private static void CheckZipDirectory(FileStream file)
    {
        const long maxDirectory = 4 * 1024 * 1024;
        byte[] At(long offset, int count)
        { if (offset < 0 || offset > file.Length || count > file.Length - offset) throw new IOException("Invalid ZIP directory range."); file.Position = offset; var value = new byte[count]; file.ReadExactly(value); return value; }
        var tailStart = Math.Max(0, file.Length - 65557); var tail = At(tailStart, (int)(file.Length - tailStart));
        var end = -1;
        for (var index = tail.Length - 4; index >= 0; index--) if (BinaryPrimitives.ReadUInt32LittleEndian(tail.AsSpan(index)) == 0x06054b50) { end = index; break; }
        if (end < 0 || end > tail.Length - 22 || end + 22 + BinaryPrimitives.ReadUInt16LittleEndian(tail.AsSpan(end + 20)) != tail.Length) throw new IOException("Invalid ZIP end record.");
        var record = tail.AsSpan(end);
        if (BinaryPrimitives.ReadUInt16LittleEndian(record[4..]) != 0 || BinaryPrimitives.ReadUInt16LittleEndian(record[6..]) != 0) throw new IOException("Multipart ZIP unsupported.");
        ulong countHere = BinaryPrimitives.ReadUInt16LittleEndian(record[8..]), count = BinaryPrimitives.ReadUInt16LittleEndian(record[10..]);
        ulong size = BinaryPrimitives.ReadUInt32LittleEndian(record[12..]), offset = BinaryPrimitives.ReadUInt32LittleEndian(record[16..]);
        var directoryEnd = tailStart + end;
        var locator = directoryEnd >= 20 ? At(directoryEnd - 20, 20) : [];
        if (locator.Length == 20 && BinaryPrimitives.ReadUInt32LittleEndian(locator) == 0x07064b50)
        {
            var start64 = BinaryPrimitives.ReadUInt64LittleEndian(locator.AsSpan(8));
            if (start64 > (ulong)Math.Max(0, directoryEnd - 76) || BinaryPrimitives.ReadUInt32LittleEndian(locator.AsSpan(4)) != 0 || BinaryPrimitives.ReadUInt32LittleEndian(locator.AsSpan(16)) != 1) throw new IOException("Invalid ZIP64 locator.");
            var info = At((long)start64, 56); var recordSize = BinaryPrimitives.ReadUInt64LittleEndian(info.AsSpan(4));
            if (BinaryPrimitives.ReadUInt32LittleEndian(info) != 0x06064b50 || recordSize < 44 || recordSize > maxDirectory || start64 + recordSize + 12 != (ulong)directoryEnd - 20
                || BinaryPrimitives.ReadUInt32LittleEndian(info.AsSpan(16)) != 0 || BinaryPrimitives.ReadUInt32LittleEndian(info.AsSpan(20)) != 0) throw new IOException("Invalid ZIP64 end record.");
            var here64 = BinaryPrimitives.ReadUInt64LittleEndian(info.AsSpan(24)); var count64 = BinaryPrimitives.ReadUInt64LittleEndian(info.AsSpan(32));
            var size64 = BinaryPrimitives.ReadUInt64LittleEndian(info.AsSpan(40)); var offset64 = BinaryPrimitives.ReadUInt64LittleEndian(info.AsSpan(48));
            if (countHere != ushort.MaxValue && countHere != here64 || count != ushort.MaxValue && count != count64 || size != uint.MaxValue && size != size64 || offset != uint.MaxValue && offset != offset64) throw new IOException("ZIP64 directory disagreement.");
            countHere = here64; count = count64; size = size64; offset = offset64; directoryEnd = (long)start64;
        }
        if (countHere != count || count is < 1 or > EntryLimit || size > maxDirectory || offset > (ulong)directoryEnd || size != (ulong)directoryEnd - offset) throw new IOException("ZIP directory exceeds preview limits.");
        var position = (long)offset; var actual = 0;
        while (position < directoryEnd)
        {
            if (++actual > EntryLimit || directoryEnd - position < 46) throw new IOException("ZIP directory count exceeded.");
            var central = At(position, 46);
            if (BinaryPrimitives.ReadUInt32LittleEndian(central) != 0x02014b50 || BinaryPrimitives.ReadUInt16LittleEndian(central.AsSpan(34)) != 0) throw new IOException("Invalid central ZIP entry.");
            var width = 46L + BinaryPrimitives.ReadUInt16LittleEndian(central.AsSpan(28)) + BinaryPrimitives.ReadUInt16LittleEndian(central.AsSpan(30)) + BinaryPrimitives.ReadUInt16LittleEndian(central.AsSpan(32));
            if (width > directoryEnd - position) throw new IOException("Invalid ZIP entry length.");
            position += width;
        }
        if (position != directoryEnd || (ulong)actual != count) throw new IOException("ZIP entry count differs.");
    }

    internal static RaucManifestInfo ParseRaucManifest(byte[] bytes)
    {
        var text = new UTF8Encoding(false, true).GetString(bytes);
        if (text.Contains('\0')) throw new IOException("Invalid update manifest.");
        var values = new Dictionary<string, string>(StringComparer.Ordinal);
        var sections = new HashSet<string>(StringComparer.Ordinal);
        var section = "";
        foreach (var rawLine in text.Split('\n'))
        {
            var line = rawLine.Trim();
            if (line.Length == 0 || line.StartsWith('#') || line.StartsWith(';')) continue;
            if (line.StartsWith('[') && line.EndsWith(']'))
            { section = line[1..^1]; if (!sections.Add(section)) throw new IOException("Duplicate update manifest section."); continue; }
            var equals = line.IndexOf('=');
            if (equals < 1 || section.Length == 0 || !values.TryAdd(section + "/" + line[..equals].Trim(), line[(equals + 1)..].Trim()))
                throw new IOException("Invalid or duplicate update manifest key.");
        }
        if (!values.TryGetValue("update/compatible", out var compatible) || compatible != "steamos-amd64"
            || !values.TryGetValue("update/version", out var version) || !Regex.IsMatch(version, @"^[0-9]{1,3}(?:\.[0-9]{1,3}){1,2}$")
            || !values.TryGetValue("image.rootfs/filename", out var filename) || filename != "rootfs.img.caibx")
            throw new IOException("Invalid SteamOS RAUC update manifest declaration.");
        return new(compatible, version, filename);
    }

    internal static string UnsupportedRaucVersionError(string version) =>
        $"SteamOS {version} update bundles are detected but not supported. This installer currently supports SteamOS {OfficialSystem.Version} only.";

    internal static void CheckRaucManifest(byte[] bytes)
    {
        var manifest = ParseRaucManifest(bytes);
        if (!manifest.IsSupportedByCurrentInstaller)
            throw new IOException(UnsupportedRaucVersionError(manifest.Version));
    }

    private static async Task<byte[]> SevenZipAsync(string path, string[] arguments, int maximum, bool prefix, CancellationToken token)
    {
        // Use the reviewed binary and DLL from the runtime lock, never PATH.
        var executable = SafePath(System.IO.Path.Combine(Backend.Root, "runtime/archive/7z.exe"));
        foreach (var (name, length, digest) in new[] {
            ("7z.exe", 577536L, "6ee3c0ed0b27663c1b948ae85a7c0bb073aed1498983182f3f0df1f6a8c30b2f"),
            ("7z.dll", 1906688L, "65e4c1f855f9ef6e8f0f5df8e3f27d9eb5f07311408639da0a1ca0b8f4871b0d") })
        {
            using var file = new FileStream(SafePath(System.IO.Path.Combine(System.IO.Path.GetDirectoryName(executable)!, name)), FileMode.Open, FileAccess.Read, FileShare.Read);
            if (file.Length != length || Convert.ToHexString(await SHA256.HashDataAsync(file, token)).ToLowerInvariant() != digest) throw new IOException("Bundled archive reader differs from its reviewed lock.");
        }
        using var heldInput = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(token); timeout.CancelAfter(TimeSpan.FromSeconds(30));
        var start = new ProcessStartInfo(executable) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true, RedirectStandardInput = true };
        foreach (var argument in arguments) start.ArgumentList.Add(argument);
        using var process = new Process { StartInfo = start };
        process.Start(); process.StandardInput.Close();
        void Stop() { try { if (!process.HasExited) process.Kill(true); } catch (InvalidOperationException) { } }
        using var registration = timeout.Token.Register(Stop);
        async Task DrainError()
        { var buffer = new char[1024]; while (await process.StandardError.ReadAsync(buffer.AsMemory(), timeout.Token) != 0) { } }
        var drain = DrainError();
        try
        {
            var bytes = await ReadBoundedAsync(process.StandardOutput.BaseStream, maximum, timeout.Token, prefix);
            var intentionallyStopped = prefix && bytes.Length == maximum;
            if (intentionallyStopped) Stop();
            await process.WaitForExitAsync(timeout.Token); await drain;
            if (!intentionallyStopped && process.ExitCode != 0) throw new IOException("Archive metadata reader rejected the input.");
            return bytes;
        }
        catch (OperationCanceledException) when (!token.IsCancellationRequested) { throw new IOException("Archive preview timed out."); }
        finally
        {
            Stop();
            try { await process.WaitForExitAsync(CancellationToken.None); } catch (InvalidOperationException) { }
            try { await drain; } catch (OperationCanceledException) { }
        }
    }
}
