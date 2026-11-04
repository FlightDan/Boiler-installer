using System.IO;
using System.Text.Json;
namespace OxpInstaller;
internal static class ComponentOutcomeChecks
{
    internal static string Run()
    {
        var options = new SelectedOptions(1, ["wifi:intel", "wifi:realtek"], ["core:decky"], "zh-CN", "system");
        JsonElement Row(string slot, string id, string status = "files_installed") => JsonSerializer.SerializeToElement(new { slot, id, status });
        var rows = new[] { "A", "B" }.SelectMany(slot => options.Repairs.Concat(options.Plugins).Select(id => Row(slot, id))).ToArray();
        ComponentOutcome.Validate(rows, options);
        ComponentOutcome.Validate([], new(1, [], [], "zh-CN", "system", InstallMode: "vanilla"));
        void Reject(JsonElement[] invalid)
        { try { ComponentOutcome.Validate(invalid, options); } catch (IOException) { return; } throw new IOException("Unsafe component report accepted"); }
        Reject([]); Reject(rows[..^1]); Reject([..rows, rows[0]]);
        Reject([..rows[..^1], Row("C", "core:decky")]);
        Reject([..rows[..^1], Row("B", "core:decky", "not_installed")]);
        Reject([..rows[..^1], Row("B", "core:decky", "failed")]);
        Reject([..rows, Row("A", "system-update-hold"), Row("B", "system-update-hold")]);
        var gpuOptions = new SelectedOptions(1, [], [], "en-US", "system", GraphicsProfile: "graphics:amd-780m");
        ComponentOutcome.Validate([Row("A", "graphics:amd-780m"), Row("B", "graphics:amd-780m")], gpuOptions);
        var missingGpuRefused = false;
        try { ComponentOutcome.Validate([], gpuOptions); } catch (IOException) { missingGpuRefused = true; }
        if (!missingGpuRefused) throw new IOException("Missing GPU candidate reports accepted.");
        foreach (var preset in new[] {
            new SelectedOptions(1, [], [], "en-US", "zh_CN"),
            new SelectedOptions(1, [], [], "en-US", "system", EnableSsh: true),
            new SelectedOptions(1, [], [], "en-US", "system", SetDeckPassword: true) })
        {
            var presetRows = new[] { Row("A", "core:system-presets"), Row("B", "core:system-presets") };
            ComponentOutcome.Validate(presetRows, preset);
            foreach (var invalid in new[] { Array.Empty<JsonElement>(), presetRows[..1],
                new[] { presetRows[0], Row("B", "core:system-presets", "failed") } })
            {
                var refused = false;
                try { ComponentOutcome.Validate(invalid, preset); } catch (IOException) { refused = true; }
                if (!refused) throw new IOException("Incomplete system preset reports accepted.");
            }
        }
        var ssdOptions = new SelectedOptions(1, [SsdBootRepair.Option], ["core:maintenance"], "en-US", "system");
        var binding = new SsdRepairBinding("NVMe", "Predator SSD GM7 1TB", "synthetic-serial", "match");
        JsonElement SsdRow(string slot, string serial, bool changed) => JsonSerializer.SerializeToElement(new {
            slot, id=SsdBootRepair.Option, status="files_installed", activation="first_boot_required",
            outcome=changed?"applied":"already_configured", operation=new {ok=true,action="apply",changed},
            target=new {slot,disposition="match",root_partuuid=Guid.NewGuid(),esp_partuuid=Guid.NewGuid(),
                physical=new {kind="physical",bus=binding.Bus,model=binding.Model,serial,disposition="match"}} });
        var ssdRows = new[] { SsdRow("A", binding.Serial, true), Row("A", "core:maintenance"),
                              SsdRow("B", binding.Serial, false), Row("B", "core:maintenance") };
        ComponentOutcome.Validate(ssdRows, ssdOptions, binding, SsdBootRepair.Option);
        foreach (var invalid in new[] { new[] { SsdRow("A", "other-serial", true), ssdRows[1], ssdRows[2], ssdRows[3] },
                                        new[] { ssdRows[0], ssdRows[1], ssdRows[0], ssdRows[3] } })
        {
            var refused = false;
            try { ComponentOutcome.Validate(invalid, ssdOptions, binding, SsdBootRepair.Option); }
            catch (IOException) { refused = true; }
            if (!refused) throw new IOException("SSD result without matching two-slot target evidence was accepted.");
        }
        try { ComponentOutcome.Validate(ssdRows, ssdOptions); throw new IOException("SSD result without host binding was accepted."); }
        catch (IOException error) when (!error.Message.Contains("was accepted", StringComparison.Ordinal)) { }
        var resourcePackage = JsonSerializer.SerializeToElement(new {
            components = new object[] {
                new { id = "device:base", requires = new[] { "device:dependency" } },
                new { id = "device:dependency" },
                new { id = "device:unselected" } },
            resources = new object[] { new { id = "resource:intel-be201-firmware", files = Array.Empty<object>() } } });
        var resourceOptions = new SelectedOptions(1, [], ["core:maintenance"], "en-US", "system",
            DeviceComponents: ["device:base", "resource:intel-be201-firmware"]);
        var resourceExpected = ComponentOutcome.Expected(resourceOptions, resourcePackage);
        if (!resourceExpected.SetEquals(["core:maintenance", "device:base", "device:dependency", "resource:intel-be201-firmware"]))
            throw new IOException("Selected model resource was not resolved as an install component.");
        var unknownSelectionRefused = false;
        try
        {
            ComponentOutcome.Expected(new SelectedOptions(1, [], ["core:maintenance"], "en-US", "system",
                DeviceComponents: ["resource:other"]), resourcePackage);
        }
        catch (IOException) { unknownSelectionRefused = true; }
        if (!unknownSelectionRefused) throw new IOException("Unknown device selection was accepted.");
        return "Exact selected component coverage on both slots; empty Vanilla requires no implicit policy. Missing, duplicate, failed, skipped and unexpected results block deployment.";
    }
}
