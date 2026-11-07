using System.IO;
using System.Security.Cryptography;
using System.Text.Json;

namespace OxpInstaller;

// Replays the retained real install sessions through the same progress tracker
// used by the wizard. Text-only; no disk, VM, firmware or service is touched.
internal static class InstallationSessionReplayChecks
{
    private static readonly string[] PartitionSteps =
        PreparedDisk.Roles.SelectMany(role => new[]
        {
            InstallationProgressTracker.PartitionStep(role, "writing"),
            InstallationProgressTracker.PartitionStep(role, "verifying")
        }).ToArray();

    internal static string Run(string? root = null)
    {
        var fixtureRoot = Path.GetFullPath(root ?? Path.Combine(Backend.Root, "tests/fixtures/install-sessions"));
        var diskRoot = Path.GetFullPath(Path.Combine(Backend.Root, "tests/fixtures/disks"));
        if (!Directory.Exists(fixtureRoot))
            return JsonSerializer.Serialize(new { passed = true, skipped = "install-session-fixtures-unavailable" });
        var checks = new List<string>();
        var sessions = new List<object>();
        foreach (var directory in Directory.GetDirectories(fixtureRoot).OrderBy(value => value, StringComparer.Ordinal))
        {
            var name = Path.GetFileName(directory);
            VerifyManifest(directory);
            checks.Add("fixture-manifest-hashes-" + name);
            using var layout = JsonDocument.Parse(File.ReadAllText(Path.Combine(directory, "partition-layout.json")));
            var kind = layout.RootElement.GetProperty("kind").GetString()!;
            var roles = layout.RootElement.GetProperty("roles").EnumerateArray()
                .ToDictionary(item => item.GetProperty("name").GetString()!, item => item.GetProperty("size").GetInt64());
            roles["home"] = layout.RootElement.GetProperty("home_used_bytes").GetInt64();
            var tracker = new InstallationProgressTracker();
            tracker.Advance("bootloader", 1);
            tracker.SetPartitions(roles.Select(pair => (pair.Key, pair.Value)));
            var finals = new Dictionary<string, (long Written, long Verified, long Total)>();
            var seenSteps = new HashSet<string>();
            var events = 0;
            var previous = tracker.Percent;
            foreach (var line in File.ReadLines(Path.Combine(directory, "full-transcript.log")))
            {
                if (!line.StartsWith("PARTITION_PROGRESS=", StringComparison.Ordinal)) continue;
                var fields = line[19..].Split(':');
                if (fields.Length != 5) throw new IOException("Malformed retained partition progress: " + line);
                var role = fields[0];
                if (!roles.TryGetValue(role, out var expected) || !long.TryParse(fields[4], out var total) || total != expected)
                    throw new IOException("Retained partition progress differs from its reviewed layout: " + line);
                if (!tracker.ObservePartition(line)) throw new IOException("Retained partition progress was rejected: " + line);
                if (tracker.Percent + .000001 < previous) throw new IOException("Retained partition progress regressed: " + line);
                previous = tracker.Percent;
                events++;
                seenSteps.Add(InstallationProgressTracker.PartitionStep(role, fields[1]));
                finals[role] = (long.Parse(fields[2]), long.Parse(fields[3]), total);
            }
            if (events < 8 || !finals.Keys.ToHashSet().SetEquals(PreparedDisk.Roles))
                throw new IOException("Retained partition transcript does not cover all eight roles: " + name);
            foreach (var role in PreparedDisk.Roles)
            {
                var final = finals[role];
                if (final.Written != final.Total || final.Verified != final.Total)
                    throw new IOException("Retained partition transcript did not finish " + role + ": " + name);
            }
            if (!seenSteps.SetEquals(PartitionSteps))
                throw new IOException("Retained partition transcript is missing write/readback steps: " + name);
            if (Math.Abs(tracker.Percent - 99) > .000001)
                throw new IOException("Retained partition transcript did not reach the pending-success ceiling: " + name);
            checks.Add("retained-partition-transcript-" + name);

            var journal = ReplayJournal(directory, kind, name);
            checks.AddRange(journal.Steps);
            if (File.Exists(Path.Combine(directory, "verify-markers.log")))
            {
                var markers = File.ReadAllLines(Path.Combine(directory, "verify-markers.log"))
                    .Where(line => line.StartsWith("OXP_INTEGRATION_VERIFY=", StringComparison.Ordinal)).ToArray();
                if (markers.Length > 0)
                {
                    var componentPath = Path.Combine(directory, "components.json");
                    var rows = File.Exists(componentPath)
                        ? JsonDocument.Parse(File.ReadAllText(componentPath)).RootElement.EnumerateArray()
                            .Select(row => "OXP_COMPONENT_RESULT=" + JsonSerializer.Serialize(row) + "\n").ToArray()
                        : [];
                    var componentTracker = new InstallationProgressTracker();
                    componentTracker.Advance("preparing", 1);
                    var observed = 0;
                    foreach (var row in rows)
                        if (componentTracker.ObserveComponents(row, rows.Length, true)) observed++;
                    foreach (var marker in markers) componentTracker.ObserveComponents(marker + "\n", rows.Length, true);
                    if (componentTracker.ComponentStep != "02.04" || Math.Abs(componentTracker.Percent - 75) > .000001)
                        throw new IOException("Retained verification markers did not advance the independent verify step: " + name
                            + " step=" + componentTracker.ComponentStep + " percent=" + componentTracker.Percent
                            + " rows=" + rows.Length + " observed=" + observed + " markers=" + markers.Length);
                    checks.Add("retained-verification-markers-" + name);
                }
            }
            sessions.Add(new { name, kind, events, percent = tracker.Percent, journal_steps = journal.Count });
        }
        var disks = VerifyDisks(diskRoot);
        checks.AddRange(disks);
        checks.AddRange(InstallSessionFixtureChecks.Run(diskRoot, fixtureRoot));
        return JsonSerializer.Serialize(new { passed = true, checks, sessions, disks },
            new JsonSerializerOptions { WriteIndented = true });
    }

    private static void VerifyManifest(string directory)
    {
        using var manifest = JsonDocument.Parse(File.ReadAllText(Path.Combine(directory, "manifest.json")));
        var members = manifest.RootElement.GetProperty("files").EnumerateObject().ToArray();
        var actualNames = Directory.GetFiles(directory).Select(Path.GetFileName).Where(name => name != "manifest.json").ToHashSet();
        if (!actualNames.SetEquals(members.Select(member => member.Name)))
            throw new IOException("Retained fixture inventory differs: " + Path.GetFileName(directory));
        foreach (var member in members)
        {
            if (member.Name != Path.GetFileName(member.Name) || member.Name.Contains('\\') || member.Name.Contains('/'))
                throw new IOException("Invalid retained fixture member: " + member.Name);
            var path = Path.Combine(directory, member.Name);
            var bytes = File.ReadAllBytes(path);
            var actual = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
            if (bytes.LongLength != member.Value.GetProperty("size").GetInt64()
                || actual != member.Value.GetProperty("sha256").GetString())
                throw new IOException("Retained fixture size/hash differs: " + Path.GetFileName(directory) + "/" + member.Name);
        }
    }

    private static (List<string> Steps, int Count) ReplayJournal(string directory, string kind, string name)
    {
        var percents = new List<double>();
        var steps = new List<string>();
        foreach (var line in File.ReadLines(Path.Combine(directory, "progress.jsonl")))
        {
            using var row = JsonDocument.Parse(line);
            percents.Add(row.RootElement.GetProperty("Percent").GetDouble());
            steps.Add(row.RootElement.GetProperty("Step").GetString()!);
        }
        for (var index = 1; index < percents.Count; index++)
            if (percents[index] + .000001 < percents[index - 1])
                throw new IOException("Retained progress journal regressed: " + name);
        if (percents.Count == 0 || percents[^1] < 99)
            throw new IOException("Retained progress journal did not reach completion: " + name);
        var required = (kind == "internal"
            ? new[] { "02.01", "02.02", "02.03", "03.01", "01.06", "01.07" }
            : new[] { "01.01", "01.03", "01.04", "01.05", "01.06", "01.07" })
            .Concat(new[] { "04.00" }).Concat(PartitionSteps).Concat(new[] { "04.90", "04.99" }).ToArray();
        // Check chronology, not numeric sorting: capacity preparation 01.06
        // intentionally follows bootloader 03.01 in the historical product flow.
        var relevant = new List<string>();
        foreach (var step in steps.Where(required.Contains))
            if (relevant.Count == 0 || relevant[^1] != step) relevant.Add(step);
        if (!relevant.SequenceEqual(required))
            throw new IOException("Retained progress phase order differs: " + name + " observed=" + string.Join(",", relevant));
        var checks = new List<string> { "retained-progress-journal-" + name };
        if (kind == "internal")
        {
            var component = Enumerable.Range(0, steps.Count).Where(index => steps[index] == "02.01").ToArray();
            if (component.Length < 2) throw new IOException("Retained journal lost the component phase boundary: " + name);
        }
        return (checks, steps.Count);
    }

    private static List<string> VerifyDisks(string root)
    {
        var checks = new List<string>();
        if (!Directory.Exists(root)) return ["disk-fixtures-unavailable"];
        using var internalDisk = JsonDocument.Parse(File.ReadAllText(Path.Combine(root, "1106-gm7-internal.json")));
        var internalParts = internalDisk.RootElement.GetProperty("partitions").EnumerateArray().ToArray();
        if (internalParts.Length != 6 || internalParts[0].GetProperty("offset").GetInt64() != 1048576
            || internalParts[^1].GetProperty("size").GetInt64() != 2147483648)
            throw new IOException("Retained GM7 pre-install snapshot shape changed.");
        checks.Add("retained-gm7-preinstall-snapshot");
        using var after = JsonDocument.Parse(File.ReadAllText(Path.Combine(root, "1106-gm7-after-install.json")));
        var afterParts = after.RootElement.GetProperty("partitions").EnumerateArray().ToArray();
        var roleNames = afterParts.Select(part => part.GetProperty("gptName").GetString())
            .Where(value => value != null && PreparedDisk.Roles.Contains(value)).ToArray();
        if (afterParts.Length != 14 || !roleNames.SequenceEqual(PreparedDisk.Roles))
            throw new IOException("Retained GM7 after-install snapshot is incomplete.");
        checks.Add("retained-gm7-after-install-roles");
        using var external = JsonDocument.Parse(File.ReadAllText(Path.Combine(root, "1105-session-disks.json")));
        var disks = external.RootElement.EnumerateArray().ToArray();
        if (!disks.Any(disk => disk.GetProperty("number").GetInt32() == 1
                && disk.GetProperty("partitions").GetArrayLength() == 0))
            throw new IOException("Retained external blank disk snapshot changed.");
        checks.Add("retained-external-blank-disk-snapshot");
        return checks;
    }
}
