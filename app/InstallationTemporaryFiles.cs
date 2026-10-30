using System.IO;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace OxpInstaller;

internal static class InstallationTemporaryFiles
{
    private static readonly AsyncLocal<string?> current = new();
    internal static string Root => current.Value ?? InstallationController.Work;
    internal static string NewTransportDirectory()
    {
        // vvfat needs a short path. Record this exact owned directory instead
        // of moving its deep component paths under the session directory.
        var name = ".p-" + Guid.NewGuid().ToString("N")[..12];
        var path = Path.Combine(InstallationController.Work, name);
        MediaSource.Ordinary(path, InstallationController.Work, false);
        if (current.Value is { } session)
        {
            var ledger = Path.Combine(session, "temporary-transports.txt");
            MediaSource.Ordinary(ledger, InstallationController.Work, false);
            File.AppendAllText(ledger, name + "\n");
        }
        return path;
    }
    internal static IDisposable UseSession(string directory)
    {
        var previous = current.Value;
        current.Value = directory;
        return new Scope(() => current.Value = previous);
    }
    private sealed class Scope(Action dispose) : IDisposable { public void Dispose() => dispose(); }

    internal static InstallationSession Complete(InstallationSession state, Action<string> log)
    {
        try { return CompleteCore(state, log); }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or JsonException)
        {
            log("Temporary file cleanup could not finish: " + error.Message + "\n");
            var result = state with { CleanupWarning = error.Message };
            // A refused binding/lease never persists the stale caller state.
            return result;
        }
    }
    private static InstallationSession CompleteCore(InstallationSession state, Action<string> log)
    {
        if (!state.CleanupTemporaryFiles || !state.Physical || state.Status is not ("awaiting-device-boot" or "complete")) return state;
        var directory = InstallationController.SessionDirectory(state);
        if (state.Schema != 1 || !Regex.IsMatch(state.Id, "^[0-9a-f]{32}$")) throw new IOException("Invalid cleanup session.");
        MediaSource.Ordinary(Path.Combine(directory, "session.json"), InstallationController.Work);
        using var lease = new FileStream(Path.Combine(directory, "operation.lock"), FileMode.CreateNew, FileAccess.Write, FileShare.None, 4096, FileOptions.DeleteOnClose);
        var saved = JsonSerializer.Deserialize<InstallationSession>(File.ReadAllText(Path.Combine(directory, "session.json"))) ?? throw new IOException("Missing cleanup session.");
        if (JsonSerializer.Serialize(saved with { CleanupWarning = null }) != JsonSerializer.Serialize(state with { CleanupWarning = null })) throw new IOException("Cleanup session changed.");
        var errors = new List<string>();
        var removed = new List<string>();
        foreach (var path in Directory.EnumerateFileSystemEntries(directory))
        {
            var name = Path.GetFileName(path);
            var temporaryFile = name is "prepared.img" or "imported.img" or "selected-options.img" or "refind-prepared.img" or "capacity-prepared.img" or "recovery-3.10.img"
                || Regex.IsMatch(name, "^(source|official-source)-[0-9a-f]{32}(\\.img(\\.bz2|\\.zip)?|\\.raucb)$");
            var temporaryDirectory = name is "import" or "firmware-payload" or "official-preparation"
                || Regex.IsMatch(name, "^\\.p-[0-9a-f]{12}$")
                || Regex.IsMatch(name, "^(virtual|firmware-session)-[0-9a-f]{32}$")
                || Regex.IsMatch(name, "^[0-9]{8}T[0-9]{9}-[a-z0-9-]+$");
            if (!temporaryFile && !temporaryDirectory) continue;
            try
            {
                MediaSource.Ordinary(path, directory, false);
                if (Directory.Exists(path))
                {
                    if (!temporaryDirectory) throw new IOException("Unexpected temporary directory.");
                    RemoveTree(path, directory);
                }
                else if (temporaryFile) File.Delete(path);
                else throw new IOException("Unexpected temporary file.");
                removed.Add(name);
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException)
            { errors.Add(name + ": " + error.Message); }
        }
        var ledger = Path.Combine(directory, "temporary-transports.txt");
        if (File.Exists(ledger))
        {
            MediaSource.Ordinary(ledger, directory);
            if (new FileInfo(ledger).Length > 65536) throw new IOException("Oversized temporary transport ledger.");
            foreach (var name in File.ReadAllLines(ledger).Distinct())
            {
                if (!Regex.IsMatch(name, "^\\.p-[0-9a-f]{12}$")) { errors.Add("Invalid temporary transport entry."); continue; }
                var path = Path.Combine(InstallationController.Work, name);
                try
                {
                    MediaSource.Ordinary(path, InstallationController.Work, false);
                    if (Directory.Exists(path)) RemoveTree(path, InstallationController.Work);
                    else if (File.Exists(path)) throw new IOException("Unexpected transport file.");
                    removed.Add(name);
                }
                catch (Exception error) when (error is IOException or UnauthorizedAccessException) { errors.Add(name + ": " + error.Message); }
            }
        }
        var warning = errors.Count == 0 ? null : string.Join("\n", errors);
        var result = state with { TemporaryFilesCleaned = errors.Count == 0, CleanupWarning = warning, UpdatedUtc = DateTime.UtcNow };
        var report = Path.Combine(directory, "temporary-cleanup-" + Guid.NewGuid().ToString("N") + ".json");
        MediaSource.Ordinary(report, directory, false);
        File.WriteAllText(report, JsonSerializer.Serialize(new { removed, errors, utc = DateTime.UtcNow }));
        InstallationController.Save(Path.Combine(directory, "session.json"), result);
        log(PartitionInventory.L("临时文件清理：", "Temporary file cleanup: ") + (warning ?? PartitionInventory.L("完成；下载缓存和任务日志已保留。", "complete; download caches and task logs kept.")) + "\n");
        return result;
    }

    private static void RemoveTree(string directory, string scope)
    {
        // Inspect each level before entering it; redirected children are never followed.
        MediaSource.Ordinary(directory, scope, false);
        foreach (var path in Directory.EnumerateFileSystemEntries(directory))
        {
            MediaSource.Ordinary(path, scope, false);
            if (Directory.Exists(path)) RemoveTree(path, scope);
            else File.Delete(path);
        }
        Directory.Delete(directory, false);
    }
}
