using System.IO;

namespace OxpInstaller;

internal static class PayloadVolumes
{
    // QEMU vvfat has a fixed geometry. Keep each volume well below its FAT16 limit.
    internal static async Task<string[]> PrepareAsync(string source, string destination, CancellationToken token, long limit = 400L * 1024 * 1024)
    {
        var root = Path.GetFullPath(source);
        var files = Directory.GetFiles(root, "*", SearchOption.AllDirectories).OrderBy(path => Path.GetRelativePath(root, path).StartsWith("apks" + Path.DirectorySeparatorChar, StringComparison.Ordinal) ? 0 : 1).ThenBy(path => path, StringComparer.Ordinal).ToArray();
        foreach (var path in Directory.EnumerateFileSystemEntries(root, "*", SearchOption.AllDirectories).Prepend(root))
            if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0) throw new IOException("Payload volumes cannot contain links.");
        long Weight(string path) => checked(((new FileInfo(path).Length + 8191) / 8192 + 1) * 8192);
        if (files.Where(path => Path.GetRelativePath(root, path).StartsWith("apks" + Path.DirectorySeparatorChar, StringComparison.Ordinal)).Sum(Weight) > limit)
            throw new IOException("Bootstrap packages exceed one readonly transport volume.");
        if (files.Any(path => Weight(path) > limit)) throw new IOException("A payload file exceeds the readonly transport limit; split the package's large payload file.");
        if (files.Sum(Weight) < limit) return [root];
        if (Directory.Exists(destination)) throw new IOException("Payload volume staging already exists.");
        var volumes = new List<string>(); long size = 0;
        string NewVolume()
        {
            if (volumes.Count >= 8) throw new IOException("Payload exceeds the supported readonly volume count.");
            var path = Path.Combine(destination, volumes.Count.ToString()); Directory.CreateDirectory(path); volumes.Add(path); size = 0; return path;
        }
        var volume = NewVolume();
        foreach (var path in Directory.EnumerateDirectories(root, "*", SearchOption.AllDirectories)) Directory.CreateDirectory(Path.Combine(volume, Path.GetRelativePath(root, path)));
        foreach (var file in files)
        {
            token.ThrowIfCancellationRequested(); var weight = Weight(file);
            if (size + weight > limit) volume = NewVolume();
            var target = Path.Combine(volume, Path.GetRelativePath(root, file)); Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            using var input = new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.Read, 1024 * 1024, true);
            using var output = new FileStream(target, FileMode.CreateNew, FileAccess.Write, FileShare.None, 1024 * 1024, true);
            var length = input.Length; await input.CopyToAsync(output, token); await output.FlushAsync(token);
            if (input.Length != length || output.Length != length) throw new IOException("Payload changed while staging its readonly volume.");
            size += weight;
        }
        return volumes.ToArray();
    }

    internal static string MountCommand(int count)
    {
        if (count == 1) return "mkdir -p /mnt/payload && mount -t vfat -o ro /dev/vdb1 /mnt/payload";
        if (count is < 1 or > 8) throw new ArgumentOutOfRangeException(nameof(count));
        var commands = new List<string> { "mkdir -p /mnt/payload" };
        for (var i = 0; i < count; i++)
        {
            // Match the explicit transport serial; never infer a target disk from size or labels.
            commands.Add($"d=; for p in /sys/block/vd*; do s=$(cat \"$p/serial\" 2>/dev/null || cat \"$p/device/serial\" 2>/dev/null); if [ \"$s\" = boiler-payload-{i} ]; then [ -z \"$d\" ] || exit 1; d=/dev/${{p##*/}}1; fi; done; [ -n \"$d\" ] || exit 1");
            commands.Add($"mkdir -p /mnt/boiler-volume-{i} && mount -t vfat -o ro \"$d\" /mnt/boiler-volume-{i}");
        }
        // FAT cannot be an overlayfs lower layer. Assemble only on a dedicated,
        // disposable disk, then make the complete payload readonly before scripts run.
        commands.Add("printf '%s\\n' /media/sr0/apks > /etc/apk/repositories; mkdir -p /var/cache/apk && apk --cache-dir /var/cache/apk add --no-network --no-progress /mnt/boiler-volume-0/apks/*.apk");
        commands.Add("modprobe ext4");
        commands.Add("d=; for p in /sys/block/vd*; do s=$(cat \"$p/serial\" 2>/dev/null || cat \"$p/device/serial\" 2>/dev/null); if [ \"$s\" = boiler-payload-merge ]; then [ -z \"$d\" ] || exit 1; d=/dev/${p##*/}; fi; done; [ -n \"$d\" ] || exit 1");
        commands.Add("mkfs.ext4 -q -F \"$d\" && mount -t ext4 \"$d\" /mnt/payload");
        for (var i = 0; i < count; i++) commands.Add($"cp -R /mnt/boiler-volume-{i}/. /mnt/payload/");
        commands.Add("sync && mount -o remount,ro /mnt/payload");
        return "( " + string.Join(" && ", commands.Select(command => "{ " + command + "; }")) + " )";
    }
}
