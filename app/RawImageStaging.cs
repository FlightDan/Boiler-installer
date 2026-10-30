using System.IO;
using System.Buffers.Binary;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using Microsoft.Win32.SafeHandles;

namespace OxpInstaller;

internal static class RawImageStaging
{
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool DeviceIoControl(SafeFileHandle file, uint code, IntPtr input, uint inputSize,
        IntPtr output, uint outputSize, out uint returned, IntPtr overlapped);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool DeviceIoControl(SafeFileHandle file, uint code, byte[] input, uint inputSize,
        byte[] output, uint outputSize, out uint returned, IntPtr overlapped);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool GetVolumePathName(string path, StringBuilder volume, uint length);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool GetDiskFreeSpace(string root, out uint sectorsPerCluster, out uint bytesPerSector,
        out uint freeClusters, out uint totalClusters);

    internal static bool TryMakeSparse(FileStream output) =>
        DeviceIoControl(output.SafeFileHandle, 0x900c4, IntPtr.Zero, 0, IntPtr.Zero, 0, out _, IntPtr.Zero);

    // A sparse flag alone does not preserve holes: writing zero bytes allocates
    // real clusters on Windows. Skip zero runs while retaining logical position.
    internal static async Task WriteSparseAsync(FileStream output, ReadOnlyMemory<byte> data, bool sparse, CancellationToken token)
    {
        token.ThrowIfCancellationRequested();
        if (sparse && data.Span.IndexOfAnyExcept((byte)0) < 0)
        { output.Seek(data.Length, SeekOrigin.Current); return; }
        const int unit = 65536;
        for (var offset = 0; offset < data.Length; offset += unit)
        {
            token.ThrowIfCancellationRequested();
            var part = data.Slice(offset, Math.Min(unit, data.Length - offset));
            if (sparse && part.Span.IndexOfAnyExcept((byte)0) < 0) output.Seek(part.Length, SeekOrigin.Current);
            else await output.WriteAsync(part, token);
        }
    }

    private static ulong? AllocationUnit(string directory)
    {
        var volume = new StringBuilder(32768);
        if (!GetVolumePathName(directory, volume, (uint)volume.Capacity)
            || !GetDiskFreeSpace(volume.ToString(), out var sectors, out var bytes, out _, out _)
            || sectors == 0 || bytes == 0) return null;
        // Sparse NTFS ranges may consume a 64-KiB unit even on 4-KiB clusters.
        return Math.Max(65536ul, (ulong)sectors * bytes);
    }

    // Windows cached reads through sparse holes can reserve their full logical
    // size without allocating file ranges. Read only native allocated ranges;
    // synthesize the same zero bytes in memory for hashing and copying.
    internal static Stream SparseRead(FileStream source, CancellationToken token)
    {
        var ranges = SparseRanges(source, token);
        return new SparseReadStream(source, ranges ?? [(0, source.Length)]);
    }

    private sealed class SparseReadStream(FileStream source, List<(long Start, long End)> ranges) : Stream
    {
        private readonly long length = source.Length;
        private long position = source.Position;
        private int index;
        private int Next(Memory<byte> buffer, out bool data)
        {
            while (index < ranges.Count && ranges[index].End <= position) index++;
            var end = index == ranges.Count ? length : ranges[index].Start > position ? ranges[index].Start : ranges[index].End;
            data = index < ranges.Count && ranges[index].Start <= position;
            var count = (int)Math.Min(buffer.Length, Math.Max(0, end - position));
            if (!data) buffer.Span[..count].Clear();
            return count;
        }
        public override int Read(byte[] buffer, int offset, int count) => Read(buffer.AsSpan(offset, count));
        public override int Read(Span<byte> buffer)
        {
            while (index < ranges.Count && ranges[index].End <= position) index++;
            var data = index < ranges.Count && ranges[index].Start <= position;
            var end = index == ranges.Count ? length : data ? ranges[index].End : ranges[index].Start;
            var count = (int)Math.Min(buffer.Length, Math.Max(0, end - position));
            if (data && count > 0) { count = RandomAccess.Read(source.SafeFileHandle, buffer[..count], position); if (count == 0) throw new EndOfStreamException(); }
            else buffer[..count].Clear();
            position += count; return count;
        }
        public override async ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken token = default)
        {
            token.ThrowIfCancellationRequested();
            var count = Next(buffer, out var data);
            if (data && count > 0) { count = await RandomAccess.ReadAsync(source.SafeFileHandle, buffer[..count], position, token); if (count == 0) throw new EndOfStreamException(); }
            position += count; return count;
        }
        public override long Length => length;
        public override long Position { get => position; set { if (value < 0) throw new ArgumentOutOfRangeException(nameof(value)); position = value; index = 0; } }
        public override long Seek(long offset, SeekOrigin origin) { Position = checked(offset + (origin == SeekOrigin.Begin ? 0 : origin == SeekOrigin.Current ? position : length)); return position; }
        public override bool CanRead => true;
        public override bool CanSeek => true;
        public override bool CanWrite => false;
        public override void Flush() { }
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        // The caller retains ownership of the immutable source handle.
    }

    private static List<(long Start, long End)>? SparseRanges(FileStream source, CancellationToken token)
    {
        if (!File.GetAttributes(source.SafeFileHandle).HasFlag(FileAttributes.SparseFile)) return null;
        var capacity = source.Length;
        var input = new byte[16]; var output = new byte[4096];
        long cursor = 0;
        var ranges = new List<(long Start, long End)>();
        while (cursor < capacity)
        {
            token.ThrowIfCancellationRequested();
            BinaryPrimitives.WriteInt64LittleEndian(input, cursor);
            BinaryPrimitives.WriteInt64LittleEndian(input.AsSpan(8), capacity - cursor);
            var complete = DeviceIoControl(source.SafeFileHandle, 0x940cf, input, (uint)input.Length,
                output, (uint)output.Length, out var returned, IntPtr.Zero);
            if (!complete && Marshal.GetLastWin32Error() != 234) return null;
            if (returned > output.Length || returned % 16 != 0 || !complete && returned == 0)
                throw new IOException("Invalid sparse allocation query result.");
            for (var i = 0; i < returned; i += 16)
            {
                var start = BinaryPrimitives.ReadInt64LittleEndian(output.AsSpan(i));
                var length = BinaryPrimitives.ReadInt64LittleEndian(output.AsSpan(i + 8));
                if (start < cursor || start >= capacity || length <= 0 || length > long.MaxValue - start)
                    throw new IOException("Invalid sparse allocation range.");
                var end = Math.Min(capacity, start + length);
                ranges.Add((start, end)); cursor = end;
            }
            if (complete) return ranges;
        }
        return ranges;
    }

    internal static ulong? SparseDataUpperBound(FileStream source, CancellationToken token, ulong allocationUnit = 65536)
    {
        if (allocationUnit == 0 || allocationUnit > long.MaxValue) throw new ArgumentOutOfRangeException(nameof(allocationUnit));
        var ranges = SparseRanges(source, token);
        if (ranges == null) return null;
        ulong allocated = 0, allocatedEnd = 0;
        foreach (var (start, end) in ranges)
        {
            var roundedStart = (ulong)start / allocationUnit * allocationUnit;
            var roundedEnd = checked(((ulong)end + allocationUnit - 1) / allocationUnit * allocationUnit);
            allocated = checked(allocated + roundedEnd - Math.Max(roundedStart, allocatedEnd));
            allocatedEnd = roundedEnd;
        }
        return allocated;
    }

    internal static async Task ConvertAsync(string source, string destination, Action<string> log, CancellationToken token)
    {
        MediaSource.Ordinary(destination, InstallationController.Work, false);
        var qemu = Path.Combine(Backend.Root, "runtime/qemu-min/qemu-img.exe");
        // Lock ordinary inputs before inspecting their format; a same-length
        // replacement must not invalidate the raw-file classification.
        using var ordinary = File.Exists(source) ? new FileStream(source, FileMode.Open, FileAccess.Read, FileShare.Read) : null;
        using var info = JsonDocument.Parse(await Backend.ProcessAsync(qemu, ["info", "--output=json", source], _ => { }, token));
        var capacity = info.RootElement.GetProperty("virtual-size").GetInt64();
        // Keep an ordinary raw input immutable until conversion completes.
        // qcow2 graph inputs continue to use their format-aware QEMU extent map.
        var raw = info.RootElement.GetProperty("format").GetString() == "raw" ? ordinary : null;
        if (raw != null && raw.Length != capacity) throw new IOException("Source image size changed before staging.");
        using var map = JsonDocument.Parse(await Backend.ProcessAsync(qemu, ["map", "--output=json", source], _ => { }, token));
        ulong allocated = 0;
        foreach (var extent in map.RootElement.EnumerateArray())
            if (!extent.TryGetProperty("zero", out var zero) || !zero.GetBoolean())
                allocated = checked(allocated + extent.GetProperty("length").GetUInt64());
        var allocationUnit = AllocationUnit(Path.GetDirectoryName(destination)!);
        var sparseAllocated = raw == null || !allocationUnit.HasValue ? null : SparseDataUpperBound(raw, token, allocationUnit.Value);
        if (sparseAllocated.HasValue) allocated = Math.Min(allocated, sparseAllocated.Value);
        const ulong reserve = 8ul * 1024 * 1024 * 1024;
        bool sparse;
        using (var output = new FileStream(destination, FileMode.CreateNew, FileAccess.Write, FileShare.None))
        {
            sparse = TryMakeSparse(output);
            MediaSpace.RequireAvailable(Path.GetDirectoryName(destination)!, checked((sparse ? allocated : (ulong)capacity) + reserve));
            output.SetLength(capacity);
        }
        log($"Image staging: logical {capacity / 1073741824d:F1} GiB; data upper bound {allocated / 1073741824d:F1} GiB; sparse={sparse}; allocation source={(sparseAllocated.HasValue ? "Windows sparse ranges" : "QEMU map")}.");
        if (raw != null)
        {
            using var input = SparseRead(raw, token);
            using var output = new FileStream(destination, FileMode.Open, FileAccess.Write, FileShare.None, 1024 * 1024, true);
            var buffer = new byte[1024 * 1024]; int count;
            while ((count = await input.ReadAsync(buffer, token)) != 0)
            {
                MediaSpace.RequireAvailable(Path.GetDirectoryName(destination)!, reserve + (ulong)count + 65536);
                await WriteSparseAsync(output, buffer.AsMemory(0, count), sparse, token);
            }
            await output.FlushAsync(token);
            return;
        }
        // Keep the explicitly sparse file. -n prevents qemu from recreating it.
        await Backend.ProcessAsync(qemu, ["convert", "-n", "--target-is-zero", "-S", "4k", "-O", "raw", source, destination], log, token);
    }
}
