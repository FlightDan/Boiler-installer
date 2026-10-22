using System.Buffers.Binary;
using System.IO;
using System.Text;

namespace OxpInstaller;

// Read-only logical disk. Keep every SteamOS partition byte, offset and GUID;
// export no Windows/protected partition bytes, MBR boot code or unused space.
internal sealed class BoilerExportStream : Stream
{
    private readonly PreparedDiskImage image;
    private readonly byte[] prefix = new byte[34 * 512];
    private readonly byte[] suffix = new byte[33 * 512];
    private long position;
    internal BoilerExportStream(string path)
    {
        image = new PreparedDiskImage(path);
        var layout = image.Layout;
        prefix[510] = 0x55; prefix[511] = 0xaa; prefix[450] = 0xee;
        BinaryPrimitives.WriteUInt32LittleEndian(prefix.AsSpan(454), 1);
        BinaryPrimitives.WriteUInt32LittleEndian(prefix.AsSpan(458), (uint)Math.Min(uint.MaxValue, layout.Size / 512 - 1));
        var table = prefix.AsSpan(1024);
        for (var i = 0; i < layout.System.Length; i++)
        {
            var part = layout.System[i]; var entry = table.Slice(i * 128, 128);
            part.Type.TryWriteBytes(entry); part.Id.TryWriteBytes(entry[16..]);
            BinaryPrimitives.WriteUInt64LittleEndian(entry[32..], (ulong)(part.Offset / 512));
            BinaryPrimitives.WriteUInt64LittleEndian(entry[40..], (ulong)((part.Offset + part.Size) / 512 - 1));
            BinaryPrimitives.WriteUInt64LittleEndian(entry[48..], part.Attributes);
            Encoding.Unicode.GetBytes(part.Name).CopyTo(entry[56..]);
        }
        table.CopyTo(suffix);
        var primary = prefix.AsSpan(512, 512); var backup = suffix.AsSpan(32 * 512, 512);
        Header(primary, layout, 1, layout.Size / 512 - 1, 2, PreparedDiskImage.GptChecksum(table));
        Header(backup, layout, layout.Size / 512 - 1, 1, layout.Size / 512 - 33, PreparedDiskImage.GptChecksum(table));
    }
    private static void Header(Span<byte> header, PreparedDisk layout, long current, long other, long table, uint checksum)
    {
        "EFI PART"u8.CopyTo(header);
        BinaryPrimitives.WriteUInt32LittleEndian(header[8..], 0x10000);
        BinaryPrimitives.WriteUInt32LittleEndian(header[12..], 92);
        BinaryPrimitives.WriteInt64LittleEndian(header[24..], current);
        BinaryPrimitives.WriteInt64LittleEndian(header[32..], other);
        BinaryPrimitives.WriteInt64LittleEndian(header[40..], 34);
        BinaryPrimitives.WriteInt64LittleEndian(header[48..], layout.Size / 512 - 34);
        layout.Id.TryWriteBytes(header[56..]);
        BinaryPrimitives.WriteInt64LittleEndian(header[72..], table);
        BinaryPrimitives.WriteUInt32LittleEndian(header[80..], 128);
        BinaryPrimitives.WriteUInt32LittleEndian(header[84..], 128);
        BinaryPrimitives.WriteUInt32LittleEndian(header[88..], checksum);
        BinaryPrimitives.WriteUInt32LittleEndian(header[16..], PreparedDiskImage.GptChecksum(header[..92], true));
    }
    public override int Read(byte[] buffer, int offset, int count) => Read(buffer.AsSpan(offset, count));
    public override int Read(Span<byte> buffer)
    {
        var count = (int)Math.Min(buffer.Length, Length - position);
        if (count == 0) return 0;
        var output = buffer[..count]; output.Clear();
        CopyMetadata(prefix, 0, output); CopyMetadata(suffix, Length - suffix.Length, output);
        foreach (var part in image.Layout.System)
        {
            var start = Math.Max(position, part.Offset); var end = Math.Min(position + count, part.Offset + part.Size);
            if (start >= end) continue;
            image.Stream.Position = start;
            image.Stream.ReadExactly(output.Slice((int)(start - position), (int)(end - start)));
        }
        position += count; return count;
    }
    private void CopyMetadata(byte[] data, long at, Span<byte> output)
    {
        var start = Math.Max(position, at); var end = Math.Min(position + output.Length, at + data.Length);
        if (start < end) data.AsSpan((int)(start - at), (int)(end - start)).CopyTo(output[(int)(start - position)..]);
    }
    public override ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken token = default)
    { token.ThrowIfCancellationRequested(); return ValueTask.FromResult(Read(buffer.Span)); }
    public override long Seek(long offset, SeekOrigin origin)
    { Position = checked(offset + (origin == SeekOrigin.Begin ? 0 : origin == SeekOrigin.Current ? position : Length)); return position; }
    public override long Position { get => position; set { if (value < 0 || value > Length) throw new IOException("Invalid export seek."); position = value; } }
    public override long Length => image.Layout.Size;
    public override bool CanRead => true;
    public override bool CanSeek => true;
    public override bool CanWrite => false;
    public override void Flush() { }
    public override void SetLength(long value) => throw new NotSupportedException();
    public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
    protected override void Dispose(bool disposing) { if (disposing) image.Dispose(); base.Dispose(disposing); }
}
