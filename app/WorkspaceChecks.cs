using System.IO;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace OxpInstaller;

internal static class WorkspaceChecks
{
    internal static async Task<string> RunAsync()
    {
        var results = new List<string>();
        void Require(bool ok, string label) { if (!ok) throw new IOException(label); results.Add(label); }
        var original = InstallerWorkspace.DirectoryPath;
        var settings = Path.Combine(Backend.Root, "workspace.json");
        var previous = File.Exists(settings) ? File.ReadAllBytes(settings) : null;
        var directory = Path.Combine(original, "workspace-check-" + Guid.NewGuid().ToString("N"), "自选 空间");
        try {
            InstallerWorkspace.Select(directory);
            Require(InstallationController.Work == directory && AriaDownload.Root == Path.Combine(directory, "downloads"), "selected-workspace-routes-sessions-and-downloads");
            await Backend.ProcessAsync(Path.Combine(Backend.Root, "runtime/dotnet/dotnet.exe"),
                [typeof(App).Assembly.Location, "--cli", "workspace-path"], _ => { }, CancellationToken.None);
            Require(File.ReadAllText(Path.Combine(Backend.Root, "logs/native-cli-result.txt")).Contains(directory), "new-process-loads-selected-workspace");
            bool rejected = false;
            try { MediaSource.Ordinary(Path.Combine(original, "outside.img"), directory, false); } catch (IOException) { rejected = true; }
            Require(rejected, "selected-workspace-retains-path-containment");
            rejected = false;
            try { InstallerWorkspace.Normalize(Path.GetPathRoot(directory)!); } catch (IOException) { rejected = true; }
            Require(rejected, "drive-root-is-not-a-workspace");
            await SparseReaderChecksAsync(directory, Require);
            var qemu = Path.Combine(Backend.Root, "runtime/qemu-min/qemu-img.exe");
            var empty = Path.Combine(directory, "empty.qcow2");
            await Backend.ProcessAsync(qemu, ["create", "-f", "qcow2", empty, "80G"], _ => { }, CancellationToken.None);
            var sparse = Path.Combine(directory, "empty.img");
            await RawImageStaging.ConvertAsync(empty, sparse, _ => { }, CancellationToken.None);
            Require(new FileInfo(sparse).Length == InstallationController.Capacity && (File.GetAttributes(sparse) & FileAttributes.SparseFile) != 0,
                "80-GiB-image-keeps-sparse-allocation");
            using (var info = JsonDocument.Parse(await Backend.ProcessAsync(qemu, ["info", "--output=json", sparse], _ => { }, CancellationToken.None)))
                Require(info.RootElement.GetProperty("actual-size").GetInt64() < 1024 * 1024, "empty-80-GiB-image-uses-less-than-1-MiB");
            using (var file = new FileStream(sparse, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
            {
                Require(RawImageStaging.SparseDataUpperBound(file, CancellationToken.None) == 0,
                    "empty-sparse-raw-needs-no-logical-capacity-budget");
                // More than one native query page, with widely separated data
                // and a final extent beyond a typical workspace's free space.
                const long allSpace = 512L * 1024 * 1024 * 1024;
                file.SetLength(allSpace);
                for (var i = 0; i < 300; i++) { file.Position = 1024 * 1024 + i * 128 * 1024; file.WriteByte(0x5a); }
                file.Position = allSpace - 1; file.WriteByte(0xa5); file.Flush(true);
                var bound = RawImageStaging.SparseDataUpperBound(file, CancellationToken.None);
                Require(bound >= 301ul * 4096 && bound < 64ul * 1024 * 1024,
                    "512-GiB-fragmented-sparse-raw-budget-covers-all-pages-and-final-extent");
                var largeClusters = RawImageStaging.SparseDataUpperBound(file, CancellationToken.None, 2ul * 1024 * 1024);
                Require(largeClusters >= 40ul * 1024 * 1024 && largeClusters < 64ul * 1024 * 1024,
                    "sparse-budget-rounds-and-merges-larger-destination-clusters");
            }
            foreach (var gib in new[] { 128, 256 })
            {
                var large = Path.Combine(directory, $"sparse-{gib}.img");
                using (var file = new FileStream(large, FileMode.CreateNew, FileAccess.Write))
                {
                    Require(RawImageStaging.TryMakeSparse(file), $"{gib}-GiB-fixture-supports-sparse");
                    file.SetLength(gib * PartitionInventory.GiB);
                    file.WriteByte(0x31);
                    file.Position = file.Length / 2; file.WriteByte(0x72);
                    file.Position = file.Length - 1; file.WriteByte(0x93);
                }
                Require(BoilerImage.WorkspaceBudget(large, true, true) == MediaSpace.MinimumWorkspace,
                    $"{gib}-GiB-sparse-input-budget-uses-allocated-data");
                var snapshotDirectory = Path.Combine(directory, $"snapshot-{gib}");
                var freeBefore = MediaSpace.Available(directory);
                var capacitySnapshot = await BoilerImage.SnapshotAsync(large, snapshotDirectory, CancellationToken.None);
                Require(MediaSpace.Available(directory) + PartitionInventory.GiB >= freeBefore,
                    $"{gib}-GiB-snapshot-does-not-reserve-logical-holes");
                var imported = Path.Combine(directory, $"import-{gib}.img");
                using (var input = File.OpenRead(capacitySnapshot.Path))
                    await ImageImportStreams.CopyOwnedAsync(input, imported, CancellationToken.None, expected: input.Length,
                        // Sparse data fits despite less space than logical capacity.
                        available: () => 2ul * PartitionInventory.GiB);
                Require(MediaSpace.Available(directory) + PartitionInventory.GiB >= freeBefore,
                    $"{gib}-GiB-import-does-not-reserve-logical-holes");
                foreach (var path in new[] { capacitySnapshot.Path, imported })
                {
                    using var file = File.OpenRead(path);
                    var bound = RawImageStaging.SparseDataUpperBound(file, CancellationToken.None);
                    Require(file.Length == gib * PartitionInventory.GiB && bound is > 0 and < 4ul * 1024 * 1024,
                        $"{gib}-GiB-{Path.GetFileName(path)}-native-allocation-under-4-MiB");
                    foreach (var (offset, value) in new[] { (0L, 0x31), (file.Length / 2, 0x72), (file.Length - 1, 0x93), (file.Length / 4, 0) })
                    { file.Position = offset; Require(file.ReadByte() == value, $"{gib}-GiB-{Path.GetFileName(path)}-byte-{offset}-preserved"); }
                }
            }
            var source = Path.Combine(directory, "data.img");
            using (var stream = new FileStream(source, FileMode.CreateNew, FileAccess.Write)) {
                stream.SetLength(16 * 1024 * 1024);
                stream.Write(new byte[] { 1, 2, 3, 4 });
                stream.Position = stream.Length - 4; stream.Write(new byte[] { 5, 6, 7, 8 });
            }
            var smallSnapshot = await BoilerImage.SnapshotAsync(source, Path.Combine(directory, "small-snapshot"), CancellationToken.None);
            using (var file = File.OpenRead(source))
            using (var input = RawImageStaging.SparseRead(file, CancellationToken.None))
                Require(smallSnapshot.Sha256 == Convert.ToHexString(await System.Security.Cryptography.SHA256.HashDataAsync(input)).ToLowerInvariant(),
                    "sparse-source-snapshot-hash-covers-all-logical-zero-bytes");
            using (var file = File.OpenRead(smallSnapshot.Path))
            using (var input = RawImageStaging.SparseRead(file, CancellationToken.None))
                Require(smallSnapshot.Sha256 == Convert.ToHexString(await System.Security.Cryptography.SHA256.HashDataAsync(input)).ToLowerInvariant(),
                    "sparse-source-snapshot-bytes-match-returned-digest");
            var tail = new byte[2 * 1024 * 1024 + 17]; tail[65537] = 0x64;
            var tailCopy = Path.Combine(directory, "trailing-zero-import.img");
            await ImageImportStreams.CopyOwnedAsync(new MemoryStream(tail), tailCopy, CancellationToken.None, expected: tail.Length,
                available: () => 2ul * PartitionInventory.GiB);
            Require((await File.ReadAllBytesAsync(tailCopy)).SequenceEqual(tail), "sparse-stream-retains-unaligned-data-and-trailing-zero-length");
            var copy = Path.Combine(directory, "data-copy.img");
            await RawImageStaging.ConvertAsync(source, copy, _ => { }, CancellationToken.None);
            await Backend.ProcessAsync(qemu, ["compare", "-f", "raw", "-F", "raw", source, copy], _ => { }, CancellationToken.None);
            results.Add("sparse-copy-preserves-start-end-data-and-zero-regions");
            using (var file = File.OpenRead(source))
                Require(RawImageStaging.SparseDataUpperBound(file, CancellationToken.None) == null,
                    "ordinary-raw-retains-conservative-data-budget");
            var staged = Path.Combine(directory, "sparse-data-copy.img");
            var stagingLog = new List<string>();
            await RawImageStaging.ConvertAsync(copy, staged, stagingLog.Add, CancellationToken.None);
            Require(stagingLog.Any(line => line.Contains("allocation source=Windows sparse ranges")),
                "raw-staging-uses-native-sparse-budget");
            await Backend.ProcessAsync(qemu, ["compare", "-f", "raw", "-F", "raw", source, staged], _ => { }, CancellationToken.None);
            results.Add("native-sparse-budget-copy-retains-data-and-zero-regions");
            var snapshot = JsonNode.Parse(PartitionInventory.ExampleJson)!;
            snapshot[0]!["partitionReadError"] = "incomplete";
            Require(PartitionInventory.Read(snapshot.ToJsonString()).Single().Rows.All(row => row.Space == null && !row.CanDelete), "incomplete-inventory-cannot-install-or-delete");
            Require(DiskPlanning.FreeSpaces(snapshot.ToJsonString()).Count == 0, "incomplete-inventory-cannot-create-a-plan");
            Require(MediaSpace.InstallationBudget(true, false) == 45ul * 1024 * 1024 * 1024
                && MediaSpace.InstallationBudget(true, true) == 45ul * 1024 * 1024 * 1024
                && MediaSpace.InstallationBudget(false, false) == 125ul * 1024 * 1024 * 1024, "45-GiB-minimum-excludes-rehearsal-target");
            return JsonSerializer.Serialize(new { passed = true, directory, results }, new JsonSerializerOptions { WriteIndented = true });
        } finally {
            InstallerWorkspace.Select(original);
            if (previous != null) File.WriteAllBytes(settings, previous); else File.Delete(settings);
        }
    }

    private static async Task SparseReaderChecksAsync(string directory, Action<bool, string> require)
    {
        // Expected bytes and digest are independent of SparseRead. Place data
        // at allocation-unit edges, separated by genuine filesystem holes.
        var expected = new byte[8 * 1024 * 1024];
        var markers = new[] { 0, 65535, 65536, 131071, 131072, 4 * 1024 * 1024 - 1,
            4 * 1024 * 1024, 4 * 1024 * 1024 + 65535, expected.Length - 1 };
        for (var i = 0; i < markers.Length; i++) expected[markers[i]] = (byte)(i + 17);
        var path = Path.Combine(directory, "known-sparse-reader.img");
        using (var fixture = new FileStream(path, FileMode.CreateNew, FileAccess.Write))
        {
            require(RawImageStaging.TryMakeSparse(fixture), "known-byte-reader-fixture-is-sparse");
            fixture.SetLength(expected.Length);
            foreach (var offset in markers) { fixture.Position = offset; fixture.WriteByte(expected[offset]); }
            fixture.Flush(true);
        }
        var expectedHash = System.Security.Cryptography.SHA256.HashData(expected);
        using var held = File.OpenRead(path);
        using (var reader = RawImageStaging.SparseRead(held, CancellationToken.None))
        {
            var actual = new byte[expected.Length];
            await reader.ReadExactlyAsync(actual);
            require(actual.SequenceEqual(expected), "sparse-reader-async-full-bytes-match-independent-array");
            reader.Position = 0;
            reader.ReadExactly(actual);
            require(actual.SequenceEqual(expected), "sparse-reader-sync-full-bytes-match-independent-array");
            reader.Position = 0;
            require((await System.Security.Cryptography.SHA256.HashDataAsync(reader)).SequenceEqual(expectedHash),
                "sparse-reader-hash-matches-independent-array-hash");
            foreach (var offset in new[] { 4 * 1024 * 1024 - 65537, 131070, 2 * 1024 * 1024, 65534, expected.Length - 31, 0 })
            {
                reader.Seek(offset, SeekOrigin.Begin);
                var slice = new byte[Math.Min(131074, expected.Length - offset)];
                reader.ReadExactly(slice);
                require(slice.AsSpan().SequenceEqual(expected.AsSpan(offset, slice.Length)),
                    $"sparse-reader-backseek-data-and-hole-boundaries-{offset}");
            }
            reader.Seek(-3, SeekOrigin.End);
            var tail = new byte[3]; await reader.ReadExactlyAsync(tail);
            require(tail.AsSpan().SequenceEqual(expected.AsSpan(expected.Length - 3)) && reader.ReadByte() == -1,
                "sparse-reader-end-relative-seek-and-eof");
        }
        require(held.CanRead && held.Length == expected.Length, "sparse-reader-dispose-retains-borrowed-source-handle");
    }
}
