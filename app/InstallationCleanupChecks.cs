using System.IO;
using System.Text.Json;

namespace OxpInstaller;

internal static class InstallationCleanupChecks
{
    internal static async Task<string> RunAsync()
    {
        var events = new List<string>();
        void Require(bool ok, string label) { if (!ok) throw new IOException(label); events.Add(label); }
        var id = Guid.NewGuid().ToString("N");
        var directory = Path.Combine(InstallationController.Work, "install-session-" + id);
        Directory.CreateDirectory(directory);
        var state = new InstallationSession(1, id, InstallationTargetKind.InternalShared, Path.Combine(directory, "source-" + id + ".img"), false,
            "awaiting-device-boot", "awaiting-device-boot", null, null, DateTime.UtcNow, Physical: true, CleanupTemporaryFiles: true);
        var journal = Path.Combine(directory, "session.json");
        InstallationController.Save(journal, state);
        File.WriteAllText(state.Source, "source snapshot");
        var prepared = Path.Combine(directory, "prepared.img"); File.WriteAllText(prepared, "image");
        var nested = Path.Combine(directory, "virtual-" + id); Directory.CreateDirectory(nested);
        File.WriteAllText(Path.Combine(nested, "windows-fixture.qcow2"), "fixture");
        var payload = Path.Combine(directory, "20261030T120000000-integration", "payload"); Directory.CreateDirectory(payload);
        File.WriteAllText(Path.Combine(payload, "data.raw"), "payload");
        var bundle = Path.Combine(directory, "official-source-" + id + ".raucb"); File.WriteAllText(bundle, "bundle");
        var preparation = Path.Combine(directory, "official-preparation"); Directory.CreateDirectory(preparation);
        File.WriteAllText(Path.Combine(preparation, "rootfs.img"), "expanded bundle");
        string transport;
        using (InstallationTemporaryFiles.UseSession(directory)) transport = InstallationTemporaryFiles.NewTransportDirectory();
        Directory.CreateDirectory(transport); File.WriteAllText(Path.Combine(transport, "component.bin"), "payload copy");
        File.WriteAllText(Path.Combine(directory, "components.json"), "[]");
        File.WriteAllText(Path.Combine(directory, "ui.log"), "diagnostic");
        File.WriteAllText(Path.Combine(directory, "user-note.txt"), "keep");
        using (InstallationTemporaryFiles.UseSession(directory))
            Require(await Task.Run(() => InstallationTemporaryFiles.Root) == directory, "generated-artifacts-follow-async-session-scope");
        Require(InstallationTemporaryFiles.Root == InstallationController.Work, "scope-restored-after-session");
        foreach (var status in new[] { "running", "failed", "cancelled" })
        {
            InstallationTemporaryFiles.Complete(state with { Status = status }, _ => { });
            Require(File.Exists(prepared) && Directory.Exists(nested), status + "-retains-recovery-files");
        }
        InstallationTemporaryFiles.Complete(state with { CleanupTemporaryFiles = false }, _ => { });
        Require(File.Exists(prepared), "unchecked-keeps-images");
        using (var held = new FileStream(prepared, FileMode.Open, FileAccess.Read, FileShare.None))
        {
            state = InstallationTemporaryFiles.Complete(state, _ => { });
            Require(state.Status == "awaiting-device-boot" && !state.TemporaryFilesCleaned && state.CleanupWarning != null && File.Exists(prepared), "locked-file-reported-without-changing-install-result");
        }
        state = InstallationTemporaryFiles.Complete(state, _ => { });
        Require(state.TemporaryFilesCleaned && state.CleanupWarning == null && !File.Exists(prepared) && !File.Exists(state.Source)
            && !Directory.Exists(nested) && !Directory.Exists(Path.GetDirectoryName(payload)), "retry-removes-only-session-temporary-artifacts");
        Require(!File.Exists(bundle) && !Directory.Exists(preparation) && !Directory.Exists(transport), "official-bundle-expansion-and-short-transports-cleaned");
        Require(File.Exists(journal) && File.Exists(Path.Combine(directory, "components.json")) && File.Exists(Path.Combine(directory, "ui.log"))
            && File.Exists(Path.Combine(directory, "user-note.txt")), "journals-logs-and-unrecognized-files-kept");
        state = InstallationTemporaryFiles.Complete(state, _ => { });
        Require(state.TemporaryFilesCleaned && state.CleanupWarning == null, "repeat-cleanup-is-idempotent");
        var authoritative = File.ReadAllBytes(journal);
        InstallationTemporaryFiles.Complete(state with { UpdatedUtc = state.UpdatedUtc.AddSeconds(-1) }, _ => { });
        Require(File.ReadAllBytes(journal).SequenceEqual(authoritative), "stale-state-cannot-overwrite-session");
        using (var heldLease = new FileStream(Path.Combine(directory, "operation.lock"), FileMode.CreateNew, FileAccess.Write, FileShare.None, 4096, FileOptions.DeleteOnClose))
        {
            state = InstallationTemporaryFiles.Complete(state, _ => { });
            Require(File.ReadAllBytes(journal).SequenceEqual(authoritative), "busy-session-cannot-be-overwritten-by-cleanup");
        }
        state = InstallationTemporaryFiles.Complete(state, _ => { });
        Require(state.TemporaryFilesCleaned && state.CleanupWarning == null, "transient-lease-warning-can-retry");
        var legacy = JsonSerializer.Deserialize<InstallationSession>("{\"Schema\":1,\"Id\":\"" + id + "\",\"Status\":\"failed\"}")!;
        Require(!legacy.CleanupTemporaryFiles && !legacy.TemporaryFilesCleaned, "old-session-defaults-preserve-recovery");
        return JsonSerializer.Serialize(new { passed = true, directory, events, scope = "Synthetic tool-owned files only; no disk, firmware, reboot or installation changes." }, new JsonSerializerOptions { WriteIndented = true });
    }
}
