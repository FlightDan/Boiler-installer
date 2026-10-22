using System.IO;
using System.Text.Json;

namespace OxpInstaller;

/// <summary>Disposable image tests. This path never invokes a physical disk helper.</summary>
internal static class BoilerImageBuildChecks
{
    internal static async Task<string> RunAsync(string source, string mode)
    {
        if (mode is not ("vanilla" or "custom" or "wireless")) throw new IOException("Expected vanilla, custom or wireless image test mode.");
        source = Path.GetFullPath(source);
        RecoveryImage.ValidateRaw(source);
        var directory = Path.Combine(InstallationController.Work, "boiler-image-" + mode + "-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        var progressPath = Path.Combine(directory, "progress.log");
        using var progress = new StreamWriter(new FileStream(progressPath, FileMode.CreateNew, FileAccess.Write, FileShare.Read)) { AutoFlush = true };
        void Log(string text) => progress.WriteLine(text);
        var token = CancellationToken.None;
        var steps = new List<string>();
        async Task Check(string name, Func<Task> action)
        {
            Log("BOILER_CHECK_BEGIN=" + name);
            await action(); steps.Add(name); Log("BOILER_CHECK_PASSED=" + name);
            await File.WriteAllTextAsync(Path.Combine(directory, "progress.json"), JsonSerializer.Serialize(new { source, mode, steps, directory }, new JsonSerializerOptions { WriteIndented = true }));
        }
        var fixture = "";
        await Check("isolated-windows-partition-fixture", async () => fixture = await Backend.CreateFixtureAsync(Log, token));
        await Check("source-to-two-slot-deployment-and-failure-recovery", async () =>
            await Backend.GuestAsync("deployment-test", source, false, Log, token, fixture: fixture));
        var image = Path.Combine(directory, "prepared.img");
        await Check("sparse-image-conversion", async () => await RawImageStaging.ConvertAsync(fixture, image, Log, token));
        var options = new SelectedOptions(1,
            mode == "wireless" ? ["wifi:intel", "wifi:realtek", "wifi:mediatek"] : [],
            mode == "vanilla" ? [] : ["core:decky", "core:maintenance"], "en-US", "system", InstallMode: mode == "vanilla" ? "vanilla" : "custom");
        var selection = JsonSerializer.Serialize(options, new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower });
        await Check("generic-component-integration", async () =>
        {
            var output = await Backend.GuestAsync("integration", image, true, Log, token, selection);
            await File.WriteAllTextAsync(Path.Combine(directory, "integration-output.log"), output);
            ValidateOutput(output, options);
        });
        await Check("readonly-component-verification", async () => await Backend.GuestAsync("integration-verify", image, false, Log, token, selection));
        var readiness = "";
        await Check("readonly-physical-readiness", async () => readiness = await Backend.GuestAsync("physical-readiness", image, false, Log, token));
        var sourceLine = readiness.Split('\n').Select(value => value.Trim()).LastOrDefault(value => value.StartsWith("BOILER_SOURCE_JSON=", StringComparison.Ordinal));
        if (sourceLine == null) throw new IOException("Guest did not report actual source metadata.");
        using var sourceMetadata = JsonDocument.Parse(sourceLine["BOILER_SOURCE_JSON=".Length..]);
        var result = new { passed = true, mode, source, actual_source = sourceMetadata.RootElement.Clone(), steps, image, fixture, directory,
            scope = "Disposable QEMU images and existing source read-only; no physical disk or real-device acceptance" };
        var json = JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true });
        await File.WriteAllTextAsync(Path.Combine(directory, "result.json"), json);
        return json;
    }

    internal static string ValidateOutput(string output, SelectedOptions options)
    {
        var rows = output.Split('\n').Select(value => value.Trim()).Where(value => value.StartsWith("OXP_COMPONENT_RESULT=", StringComparison.Ordinal))
            .Select(value => JsonSerializer.Deserialize<JsonElement>(value["OXP_COMPONENT_RESULT=".Length..])).ToArray();
        ComponentOutcome.Validate(rows, options);
        return JsonSerializer.Serialize(new { passed = true, scope = "Same component outcome gate used by installation", rows = rows.Length });
    }

    internal static async Task<string> RecheckAsync(string image, string mode)
    {
        if (mode is not ("vanilla" or "custom" or "wireless")) throw new IOException("Unknown prepared-image test mode.");
        image = Path.GetFullPath(image);
        MediaSource.Ordinary(image, InstallationController.Work);
        var fixture = image.EndsWith(".qcow2", StringComparison.OrdinalIgnoreCase);
        if (fixture) Backend.VerifyFixture(image);
        var directory = fixture ? Path.Combine(InstallationController.Work, "boiler-recheck-" + mode + "-" + Guid.NewGuid().ToString("N")) : Path.GetDirectoryName(image)!;
        Directory.CreateDirectory(directory);
        var name = "recheck-" + Guid.NewGuid().ToString("N");
        using var progress = new StreamWriter(new FileStream(Path.Combine(directory, name + ".log"), FileMode.CreateNew, FileAccess.Write, FileShare.Read)) { AutoFlush = true };
        if (fixture)
        {
            var prepared = Path.Combine(directory, "prepared.img");
            await RawImageStaging.ConvertAsync(image, prepared, progress.Write, CancellationToken.None);
            image = prepared;
        }
        var options = new SelectedOptions(1, mode == "wireless" ? ["wifi:intel", "wifi:realtek", "wifi:mediatek"] : [],
            mode == "vanilla" ? [] : ["core:decky", "core:maintenance"], "en-US", "system", InstallMode: mode == "vanilla" ? "vanilla" : "custom");
        var selection = JsonSerializer.Serialize(options, new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower });
        var output = await Backend.GuestAsync("integration", image, true, progress.Write, CancellationToken.None, selection);
        await File.WriteAllTextAsync(Path.Combine(directory, name + "-integration.log"), output);
        var proof = ValidateOutput(output, options);
        var verified = await Backend.GuestAsync("integration-verify", image, false, progress.Write, CancellationToken.None, selection);
        var readiness = await Backend.GuestAsync("physical-readiness", image, false, progress.Write, CancellationToken.None);
        var plans = output.Split('\n').Select(value => value.Trim()).Where(value => value.StartsWith("BOILER_INSTALL_PLAN_JSON=", StringComparison.Ordinal))
            .Select(value => JsonSerializer.Deserialize<JsonElement>(value["BOILER_INSTALL_PLAN_JSON=".Length..])).ToArray();
        if (plans.Length != 2) throw new IOException("Expected source-bound install plans for both slots.");
        var json = JsonSerializer.Serialize(new { passed = true, image, mode, outcome = JsonSerializer.Deserialize<JsonElement>(proof), plans,
            source = readiness.Split('\n').Select(value => value.Trim()).Single(value => value.StartsWith("BOILER_SOURCE_JSON=", StringComparison.Ordinal)),
            scope = "Existing disposable prepared image, same installation outcome gate, readonly verification; no physical disk acceptance" }, new JsonSerializerOptions { WriteIndented = true });
        await File.WriteAllTextAsync(Path.Combine(directory, name + "-result.json"), json);
        return json;
    }
}
