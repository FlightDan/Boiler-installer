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
        var inputSelection = new ResourceSelection(1, "device:oxp3", new string('a',64),
            ["resource:onexplayer-input", "resource:bosch-bmi260-gyro"], "local-test");
        var inputOptions = new SelectedOptions(1, [], ["core:maintenance"], "en-US", "system", ResourceSelection: inputSelection);
        JsonElement Scheduled(string slot, string id, string status = "firstboot_scheduled") => JsonSerializer.SerializeToElement(new {
            slot, id, status, runtime = "pending_firstboot", handler = "boiler-input-runtime-v1",
            resource_receipt = new string('a',32), bundle_sha256 = new string('b',64) });
        var inputRows = new[] { "A", "B" }.SelectMany(slot => new[] { Row(slot,"core:maintenance"),
            Scheduled(slot,"resource:onexplayer-input"), Scheduled(slot,"resource:bosch-bmi260-gyro") }).ToArray();
        ComponentOutcome.Validate(inputRows,inputOptions);
        var fakeInputCompletion = inputRows.ToArray();
        fakeInputCompletion[1] = Scheduled("A","resource:onexplayer-input","files_installed");
        var refusedFakeCompletion = false;
        try { ComponentOutcome.Validate(fakeInputCompletion,inputOptions); } catch (IOException) { refusedFakeCompletion = true; }
        if (!refusedFakeCompletion) throw new IOException("An unexecuted input lifecycle was accepted as installed files.");
        return "Exact selected component coverage on both slots; empty Vanilla requires no implicit policy. Missing, duplicate, failed, skipped and unexpected results block deployment.";
    }
}
