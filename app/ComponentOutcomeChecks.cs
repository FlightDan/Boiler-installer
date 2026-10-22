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
        return "Exact selected component coverage on both slots; empty Vanilla requires no implicit policy. Missing, duplicate, failed, skipped and unexpected results block deployment.";
    }
}
