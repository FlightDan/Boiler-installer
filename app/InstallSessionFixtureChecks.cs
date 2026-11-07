using System.IO;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace OxpInstaller;

// Planning and recognition only: no disk handles, deployment or firmware calls.
internal static class InstallSessionFixtureChecks
{
    internal static string[] Run(string diskRoot, string sessionRoot)
    {
        var checks = new List<string>();
        void Require(bool value, string name)
        {
            if (!value) throw new IOException("Real-session planning regression: " + name);
            checks.Add(name);
        }
        foreach (var sample in new[] {
            (Name: "1106-internal", File: "1106-gm7-internal.json", Disk: 0,
                Gap: 779415977984L, Target: 861000433664L, First: 7, Kind: InstallationTargetKind.InternalShared),
            (Name: "1105-external", File: "1105-session-disks.json", Disk: 1,
                Gap: 1048576L, Target: 886769188864L, First: 1, Kind: InstallationTargetKind.ExternalDisk)
        })
        {
            using var input = JsonDocument.Parse(File.ReadAllText(Path.Combine(diskRoot, sample.File)));
            var snapshot = input.RootElement.ValueKind == JsonValueKind.Array
                ? input.RootElement.GetRawText() : "[" + input.RootElement.GetRawText() + "]";
            var gaps = DiskPlanning.FreeSpaces(snapshot).Where(gap => gap.Disk == sample.Disk).ToArray();
            Require(gaps.Length == 1 && gaps[0].Start == sample.Gap, sample.Name + "-eligible-gap");
            var selection = new PartitionSelection(snapshot, false, sample.Disk, sample.Gap,
                128 * PartitionInventory.GiB, AlignRight: true);
            var target = selection.Target();
            Require(target.Start == sample.Target && target.Size == 128 * PartitionInventory.GiB,
                sample.Name + "-right-aligned-128-gib");
            using var fixture = JsonDocument.Parse(File.ReadAllText(Path.Combine(sessionRoot, sample.Name, "partition-layout.json")));
            var roles = fixture.RootElement.GetProperty("roles").EnumerateArray().ToArray();
            var firstOffset = roles[0].GetProperty("offset").GetInt64();
            var preparedParts = roles.Select((role, i) => new PreparedPartition(i + 1,
                role.GetProperty("name").GetString()!, Guid.Parse(role.GetProperty("type").GetString()!),
                Guid.Parse(role.GetProperty("id").GetString()!),
                role.GetProperty("offset").GetInt64() - firstOffset + PartitionInventory.MiB,
                role.GetProperty("size").GetInt64(), role.GetProperty("attributes").GetUInt64())).ToArray();
            var prepared = new PreparedDisk(Guid.Parse("12345678-1234-4234-8234-123456789abc"),
                128 * PartitionInventory.GiB + 2 * PartitionInventory.MiB, preparedParts);
            var plan = PhysicalInstallation.Plan(selection, sample.Kind, prepared);
            Require(plan.Added.Select(p => p.Number).SequenceEqual(Enumerable.Range(sample.First, 8)),
                sample.Name + "-physical-role-numbering");
            Require(plan.Added.Select(p => p.Name).SequenceEqual(PreparedDisk.Roles)
                && plan.Added.Zip(roles).All(pair => pair.First.Offset == pair.Second.GetProperty("offset").GetInt64()
                    && pair.First.Size == pair.Second.GetProperty("size").GetInt64()), sample.Name + "-physical-exact-role-ranges");
            Require(plan.Preserved.Length == sample.First - 1
                && plan.Added.All(p => plan.Preserved.All(q => !PhysicalInstallation.Overlap(p.Offset, p.Size, q.Offset, q.Size))),
                sample.Name + "-preserved-ranges-do-not-overlap");
            if (sample.Kind == InstallationTargetKind.InternalShared)
                Require(plan.Preserved.Count(p => p.Offset >= target.Start + target.Size) == 2,
                    "gm7-planning-retains-two-trailing-recovery-partitions");
            var left = selection with { AlignRight = false };
            Require(left.Target().Start == sample.Gap,
                sample.Name + "-left-alignment-remains-distinct");
            var node = JsonNode.Parse(snapshot)!.AsArray();
            var disk = node.Single(item => (int)item!["number"]! == sample.Disk)!;
            disk["isReadOnly"] = true;
            Require(!DiskPlanning.FreeSpaces(node.ToJsonString()).Any(gap => gap.Disk == sample.Disk),
                sample.Name + "-readonly-target-rejected");
            disk["isReadOnly"] = false; disk["sectorSize"] = 4096;
            Require(!DiskPlanning.FreeSpaces(node.ToJsonString()).Any(gap => gap.Disk == sample.Disk),
                sample.Name + "-unsupported-sector-target-rejected");
        }
        using var after = JsonDocument.Parse(File.ReadAllText(Path.Combine(diskRoot, "1106-gm7-after-install.json")));
        var detected = SteamOsPartitions.Detect(after.RootElement);
        Require(detected.Count == 8 && detected.OrderBy(pair => pair.Key).Select(pair => pair.Value).SequenceEqual(PreparedDisk.Roles),
            "gm7-after-install-recognized-by-production-detector");
        var inventory = PartitionInventory.Read("[" + after.RootElement.GetRawText() + "]").Single();
        Require(inventory.Rows.Count(row => row.SteamOsRole != null) == 8,
            "gm7-after-install-inventory-exposes-all-eight-roles");
        // The immutable capture records the OLD five-only policy. Model a fresh
        // successful policy evaluation without rewriting that historical evidence.
        Require(inventory.Rows.Count(row => row.SteamOsRole != null && row.CanDelete) == 5,
            "historical-capture-is-not-silently-promoted-to-new-permission");
        var reviewed = JsonNode.Parse(after.RootElement.GetRawText())!;
        foreach (var part in reviewed["partitions"]!.AsArray().Where(p => (string?)p!["gptName"] is "esp" or "efi-A" or "efi-B"))
        { part!["deletionAllowed"] = true; part["deletionBlockReason"] = ""; }
        var reviewedJson = "[" + reviewed.ToJsonString() + "]";
        var reviewedDisk = PartitionInventory.Read(reviewedJson).Single();
        var selected = reviewedDisk.Rows.Where(r => r.SteamOsRole != null && r.CanDelete).Select(r => r.Start).Order().ToArray();
        Require(selected.Length == 8 && reviewed["partitions"]!.AsArray().Where(p => detected.ContainsKey((long)p!["offset"]!))
            .Select(p => (int)p!["number"]!).SequenceEqual(Enumerable.Range(7, 8)),
            "gm7-all-eight-including-original-7-8-9-selectable-after-policy-checks");
        var delete = new PartitionDeleteRequest(reviewedJson, true, reviewedDisk.Number, selected[0], "zh-CN", selected);
        var retained = PartitionInventory.Read(PartitionDeletion.Simulate(delete)).Single().Rows.Where(r => r.Type != "—").ToArray();
        Require(retained.Length == 6 && retained.SequenceEqual(reviewedDisk.Rows.Where(r => r.Type != "—" && r.SteamOsRole == null)),
            "gm7-all-eight-deletion-keeps-six-original-partitions");
        var changed = JsonNode.Parse(after.RootElement.GetRawText())!;
        changed["partitions"]!.AsArray().Single(part => (string?)part!["gptName"] == "rootfs-A")!["gptType"] =
            "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7";
        using var invalid = JsonDocument.Parse(changed.ToJsonString());
        Require(SteamOsPartitions.Detect(invalid.RootElement).Count == 0,
            "gm7-role-name-with-wrong-type-is-not-recognized");
        return checks.ToArray();
    }
}
