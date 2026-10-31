using System.IO;
using System.Text.Json;

namespace OxpInstaller;

internal static class ComponentOutcome
{
    internal static HashSet<string> Expected(SelectedOptions options)
    {
        var expected = options.Repairs.Concat(options.Plugins).ToHashSet();
        if (options.ResourceSelection is { } resources)
        {
            ResourceSelections.Validate(resources);
            if (options.DevicePackage != null || options.DeviceComponents is { Length: > 0 } || options.DeviceLegacyOptions != null)
                throw new IOException("Resource results cannot be combined with a legacy model package.");
            expected.UnionWith(resources.Components);
        }
        if (options.SetDeckPassword || options.EnableSsh || options.KdeLanguage != "system") expected.Add("core:system-presets");
        if (options.GraphicsProfile != null) expected.Add(options.GraphicsProfile);
        foreach (var vendor in options.Wireless ?? []) expected.Add(vendor.Contains(':') ? vendor : "wifi:" + vendor);
        var selected = options.DeviceComponents ?? [];
        if (selected.Length == 0) return expected;
        if (options.DevicePackage == null || options.DeviceManifestSha256 == null) throw new IOException("Device package identity is missing.");
        using var doc = JsonDocument.Parse(File.ReadAllText(Path.Combine(BoilerProtocol.PackageRoot, options.DevicePackage, "manifest.json")));
        var components = doc.RootElement.GetProperty("components").EnumerateArray().ToDictionary(c => c.GetProperty("id").GetString()!);
        var visiting = new HashSet<string>();
        void Add(string id)
        {
            if (expected.Contains(id)) return;
            if (!visiting.Add(id) || !components.TryGetValue(id, out var component)) throw new IOException("Invalid device dependency graph.");
            if (component.TryGetProperty("requires", out var requires)) foreach (var dependency in requires.EnumerateArray()) Add(dependency.GetString()!);
            visiting.Remove(id); expected.Add(id);
        }
        foreach (var id in selected) Add(id);
        return expected;
    }
    internal static void Validate(JsonElement[] results, SelectedOptions options)
    {
        var expected = Expected(options);
        foreach (var slot in new[] { "A", "B" })
        {
            var records = results.Where(x => x.GetProperty("slot").GetString() == slot).ToArray();
            var ids = records.Select(x => x.GetProperty("id").GetString()!).ToArray();
            if (ids.Length != expected.Count || !expected.SetEquals(ids))
                throw new IOException("组件报告缺失或重复，不能继续安装。 / Missing or duplicate component results.");
            foreach (var record in records)
                if (record.GetProperty("status").GetString() != "files_installed"
                    && !(options.ResourceSelection?.Components.Contains(record.GetProperty("id").GetString()!) == true
                        && record.GetProperty("id").GetString() is "resource:onexplayer-input" or "resource:bosch-bmi260-gyro" or "resource:common-kde-keyboard"
                        && record.GetProperty("status").GetString() == "firstboot_scheduled"))
                    throw new IOException("所选组件未完成，不能继续写盘。 / A selected component did not complete; deployment is blocked.");
        }
        if (results.Length != expected.Count * 2) throw new IOException("Unexpected component result slot.");
        if (options.ResourceSelection is not { } resources) return;
        var groups = new HashSet<string>();
        var shared = new Dictionary<string, string>();
        foreach (var record in results.Where(r => resources.Components.Contains(r.GetProperty("id").GetString()!)))
        {
            var slot = record.GetProperty("slot").GetString()!;
            var id = record.GetProperty("id").GetString()!;
            if (id is "resource:onexplayer-input" or "resource:bosch-bmi260-gyro" or "resource:common-kde-keyboard")
            {
                var generation = record.GetProperty("resource_receipt").GetString() ?? "";
                if (record.GetProperty("status").GetString() != "firstboot_scheduled"
                    || record.GetProperty("runtime").GetString() != "pending_firstboot"
                    || record.GetProperty("handler").GetString() != (id == "resource:common-kde-keyboard" ? "boiler-kde-keyboard-v1" : "boiler-input-runtime-v1")
                    || !System.Text.RegularExpressions.Regex.IsMatch(generation, "^[0-9a-f]{32}$")
                    || !System.Text.RegularExpressions.Regex.IsMatch(record.GetProperty("bundle_sha256").GetString() ?? "", "^[0-9a-f]{64}$"))
                    throw new IOException("Input requires its complete bound bundle and automatic first-boot lifecycle.");
                groups.Add(generation); continue;
            }
            if (id == "resource:intel-be201-firmware" &&
                (!record.TryGetProperty("firmware", out var firmware) || firmware.ValueKind != JsonValueKind.Object
                 || !firmware.TryGetProperty("written", out var written) || written.ValueKind != JsonValueKind.Array
                 || !firmware.TryGetProperty("preserved", out var preserved) || preserved.ValueKind != JsonValueKind.Array
                 || !firmware.TryGetProperty("retained", out var retained) || retained.ValueKind != JsonValueKind.Array
                 || !firmware.TryGetProperty("policy_sha256", out var policy) || policy.ValueKind != JsonValueKind.String
                 || !System.Text.RegularExpressions.Regex.IsMatch(policy.GetString() ?? "", "^[0-9a-f]{64}$")))
                throw new IOException("Firmware results require explicit written/preserved/retained slot decisions.");
            var group = record.GetProperty("resource_receipt").GetString() ?? "";
            if (!System.Text.RegularExpressions.Regex.IsMatch(group, "^[0-9a-f]{32}$")
                || record.GetProperty("runtime").GetString() != "not_validated")
                throw new IOException("Resource completion requires a durable receipt and an explicit runtime acceptance boundary.");
            groups.Add(group);
            var domains = record.GetProperty("installed_domains").EnumerateArray().Select(v => v.GetString()!).ToArray();
            if (domains.Length == 0 || domains.Distinct().Count() != domains.Length
                || domains.Any(v => v != slot && v != "shared-home")) throw new IOException("Invalid resource result transaction domains.");
            var transactions = record.GetProperty("transactions").EnumerateArray().ToArray();
            if (transactions.Length != domains.Length
                || !domains.ToHashSet().SetEquals(transactions.Select(v => v.GetProperty("scope").GetString()!)))
                throw new IOException("Resource result does not exactly cover its transaction domains.");
            foreach (var transaction in transactions)
            {
                var identity = transaction.GetProperty("transaction").GetString() ?? "";
                if (transaction.GetProperty("component").GetString() != id
                    || transaction.GetProperty("status").GetString() != "committed"
                    || !System.Text.RegularExpressions.Regex.IsMatch(identity, "^[0-9a-f]{32}$"))
                    throw new IOException("Resource result refers to an incomplete or mismatched transaction.");
                if (transaction.GetProperty("scope").GetString() == "shared-home")
                {
                    if (shared.TryGetValue(id, out var prior) && prior != identity)
                        throw new IOException("Both slots must reference the same single shared-home transaction.");
                    shared[id] = identity;
                }
            }
        }
        if (groups.Count != 1) throw new IOException("Both slots and every selected resource require one resource group receipt.");
    }

    internal static void ValidatePlans(JsonElement[] plans, JsonElement[] results, SelectedOptions options)
    {
        if (options.ResourceSelection is not { } resources) return;
        Validate(results, options);
        var expected = Expected(options);
        var group = results.First(r => resources.Components.Contains(r.GetProperty("id").GetString()!))
            .GetProperty("resource_receipt").GetString();
        if (plans.Length != 2) throw new IOException("Both resource installation plans are required.");
        foreach (var slot in new[] { "A", "B" })
        {
            var matches = plans.Where(p => p.GetProperty("slot").GetString() == slot).ToArray();
            if (matches.Length != 1) throw new IOException("Each resource installation plan must name its exact slot once.");
            var plan = matches[0]; var pool = plan.GetProperty("resource_pool");
            var firstboot = plan.GetProperty("firstboot");
            if (firstboot.GetProperty("status").GetString() != "scheduled"
                || firstboot.GetProperty("runtime").GetString() != "not_validated"
                || firstboot.GetProperty("generation").GetString() != group
                || !System.Text.RegularExpressions.Regex.IsMatch(firstboot.GetProperty("root_partuuid").GetString() ?? "", "^[0-9a-fA-F-]{36}$")
                || firstboot.GetProperty("manifest").GetProperty("id").GetString() != "core:resource-firstboot"
                || firstboot.GetProperty("bootstrap").GetProperty("status").GetString() != "committed")
                throw new IOException("The complete first-boot runtime and target-bound activation intent were not installed.");
            if (pool.GetProperty("catalog_sha256").GetString() != resources.CatalogSha256
                || pool.GetProperty("model_id").GetString() != resources.ModelId
                || pool.GetProperty("trust").GetString() != "development-session"
                || !pool.GetProperty("shared_home_once").GetBoolean()
                || pool.GetProperty("receipt_id").GetString() != group)
                throw new IOException("Resource installation plan differs from the reviewed catalog/model/group.");
            var ids = plan.GetProperty("components").EnumerateArray().Select(c => c.GetProperty("id").GetString()!).ToArray();
            if (ids.Length != expected.Count || !expected.SetEquals(ids))
                throw new IOException("Resource installation plan is missing or duplicating selected components.");
            foreach (var input in resources.Components.Where(id => id is "resource:onexplayer-input" or "resource:bosch-bmi260-gyro" or "resource:common-kde-keyboard"))
            {
                var scheduled = plan.GetProperty("components").EnumerateArray().Single(c => c.GetProperty("id").GetString() == input);
                var result = results.Single(r => r.GetProperty("slot").GetString() == slot && r.GetProperty("id").GetString() == input);
                if (scheduled.GetProperty("files").GetArrayLength() != 0
                    || scheduled.GetProperty("firstboot_handler").GetString() != (input == "resource:common-kde-keyboard" ? "boiler-kde-keyboard-v1" : "boiler-input-runtime-v1")
                    || scheduled.GetProperty("scheduled_payload_sha256").GetString() != result.GetProperty("bundle_sha256").GetString())
                    throw new IOException("The scheduled input bundle differs from the exact bound deployment plan.");
            }
            if (resources.Components.Contains("resource:intel-be201-firmware"))
            {
                var manifest = plan.GetProperty("components").EnumerateArray()
                    .Single(c => c.GetProperty("id").GetString() == "resource:intel-be201-firmware");
                var evidence = results.Single(r => r.GetProperty("slot").GetString() == slot
                    && r.GetProperty("id").GetString() == "resource:intel-be201-firmware").GetProperty("firmware");
                foreach (var (planField, resultField) in new[] {
                    ("files", "written"), ("firmware_preserved", "preserved"),
                    ("firmware_retained", "retained"), ("firmware_policy_sha256", "policy_sha256") })
                    if (!manifest.TryGetProperty(planField, out var value)
                        || !JsonElement.DeepEquals(value, evidence.GetProperty(resultField)))
                        throw new IOException("Firmware plan and result disagree on the actual slot decisions.");
            }
        }
    }
}
