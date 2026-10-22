using System.IO;
using System.Security.Cryptography;
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
        var originalFixture = fixture ? image : null;
        string? originalFixtureDigest = null;
        if (originalFixture != null)
        {
            using var input = File.OpenRead(originalFixture);
            originalFixtureDigest = Convert.ToHexString(await SHA256.HashDataAsync(input)).ToLowerInvariant();
        }
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
        if (originalFixture != null)
        {
            using var input = File.OpenRead(originalFixture);
            if (Convert.ToHexString(await SHA256.HashDataAsync(input)).ToLowerInvariant() != originalFixtureDigest)
                throw new IOException("Original disposable source fixture changed during image QA.");
        }
        var json = JsonSerializer.Serialize(new { passed = true, image, mode,
            original_fixture = originalFixture, original_fixture_sha256 = originalFixtureDigest,
            original_fixture_unchanged = fixture ? true : (bool?)null,
            outcome = JsonSerializer.Deserialize<JsonElement>(proof), plans,
            source = readiness.Split('\n').Select(value => value.Trim()).Single(value => value.StartsWith("BOILER_SOURCE_JSON=", StringComparison.Ordinal)),
            scope = "Existing disposable prepared image, same installation outcome gate, readonly verification; no physical disk acceptance" }, new JsonSerializerOptions { WriteIndented = true });
        await File.WriteAllTextAsync(Path.Combine(directory, name + "-result.json"), json);
        return json;
    }

    internal static async Task<string> ModelAsync(string fixture, string packageDirectory)
    {
        fixture = Path.GetFullPath(fixture);
        MediaSource.Ordinary(fixture, InstallationController.Work);
        if (!fixture.EndsWith(".qcow2", StringComparison.OrdinalIgnoreCase)) throw new IOException("Model checks require an owned disposable QCOW2 fixture.");
        Backend.VerifyFixture(fixture);
        packageDirectory = Path.GetFullPath(packageDirectory);
        var directory = Path.Combine(InstallationController.Work, "boiler-model-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        using var progress = new StreamWriter(new FileStream(Path.Combine(directory, "progress.log"), FileMode.CreateNew, FileAccess.Write, FileShare.Read)) { AutoFlush = true };
        void Log(string value) => progress.WriteLine(value);
        var token = CancellationToken.None;
        var steps = new List<string>();
        var image = Path.Combine(directory, "prepared.img");
        async Task Check(string name, Func<Task> action)
        {
            Log("BOILER_CHECK_BEGIN=" + name);
            await action(); steps.Add(name); Log("BOILER_CHECK_PASSED=" + name);
            await File.WriteAllTextAsync(Path.Combine(directory, "progress.json"), JsonSerializer.Serialize(new { fixture, image, packageDirectory, steps }, new JsonSerializerOptions { WriteIndented = true }));
        }
        async Task<string> FixtureHash()
        {
            using var stream = File.OpenRead(fixture);
            return Convert.ToHexString(await SHA256.HashDataAsync(stream, token)).ToLowerInvariant();
        }
        var originalFixtureDigest = await FixtureHash();
        await File.WriteAllTextAsync(Path.Combine(directory, "fixture-source.json"), JsonSerializer.Serialize(new { fixture, sha256 = originalFixtureDigest }, new JsonSerializerOptions { WriteIndented = true }));
        try
        {
            var digest = Convert.ToHexString(SHA256.HashData(await File.ReadAllBytesAsync(Path.Combine(packageDirectory, "manifest.json"), token))).ToLowerInvariant();
            JsonElement package = default;
            var cacheKey = digest[..24];
            var cached = Path.Combine(BoilerProtocol.PackageRoot, cacheKey);
            await Check("explicit-developer-package-validation-and-cache", async () =>
            {
                package = await BoilerProtocol.RunAsync(["package", packageDirectory, "--manifest-sha256", digest], token);
                if (package.GetProperty("id").GetString() != "device:oxp3") throw new IOException("Expected the explicitly selected OXP3 device package.");
                var adaptation = package.GetProperty("components").EnumerateArray().Single(c => c.GetProperty("id").GetString() == "oxp3:legacy-adaptation");
                if (!adaptation.GetProperty("hooks").TryGetProperty("verify", out _)) throw new IOException("Model QA requires the declared guest verification hook.");
                if (!Directory.Exists(cached)) Backend.CopyDirectory(packageDirectory, cached);
                await BoilerProtocol.RunAsync(["package", cached, "--manifest-sha256", digest], token);
            });
            await Check("fresh-sparse-image-conversion", () => RawImageStaging.ConvertAsync(fixture, image, Log, token));
            var options = new SelectedOptions(1, [], ["core:decky", "core:maintenance"], "en-US", "system",
                InstallMode: "custom", DevicePackage: cacheKey, DeviceManifestSha256: digest,
                DeviceTrust: "developer-session", DeviceComponents: ["oxp3:legacy-adaptation"]);
            var selection = JsonSerializer.Serialize(options, new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower });
            await File.WriteAllTextAsync(Path.Combine(directory, "selection.json"), selection);
            string output = "", outcome = "", readiness = "";
            JsonElement[] modelVerification = [];
            await Check("model-and-common-component-integration", async () =>
            {
                output = await Backend.GuestAsync("integration", image, true, Log, token, selection);
                await File.WriteAllTextAsync(Path.Combine(directory, "integration-output.log"), output);
                outcome = ValidateOutput(output, options);
            });
            await Check("readonly-component-verification", async () =>
            {
                var verified = await Backend.GuestAsync("integration-verify", image, false, Log, token, selection);
                await File.WriteAllTextAsync(Path.Combine(directory, "verification-output.log"), verified);
                modelVerification = verified.Split('\n').Select(value => value.Trim()).Where(value => value.StartsWith("BOILER_DEVICE_VERIFY_JSON=", StringComparison.Ordinal))
                    .Select(value => JsonSerializer.Deserialize<JsonElement>(value["BOILER_DEVICE_VERIFY_JSON=".Length..])).ToArray();
                if (modelVerification.Length != 2 || !modelVerification.Select(p => p.GetProperty("slot").GetString()).ToHashSet().SetEquals(["A", "B"])
                    || modelVerification.Any(p => p.GetProperty("schema").GetInt32() != 1 || p.GetProperty("id").GetString() != "oxp3:legacy-adaptation"
                        || !p.GetProperty("verified").GetBoolean() || p.GetProperty("files").GetInt32() <= 0 || p.GetProperty("journals").GetInt32() <= 0
                        || p.GetProperty("sha256").GetString() is not string proofHash || proofHash.Length != 64 || proofHash.Any(c => !"0123456789abcdef".Contains(c))))
                    throw new IOException("Both slots must report actual model file and journal verification; status-only results are insufficient.");
            });
            await Check("readonly-physical-readiness", async () =>
            {
                readiness = await Backend.GuestAsync("physical-readiness", image, false, Log, token);
                await File.WriteAllTextAsync(Path.Combine(directory, "readiness-output.log"), readiness);
            });
            var plans = output.Split('\n').Select(value => value.Trim()).Where(value => value.StartsWith("BOILER_INSTALL_PLAN_JSON=", StringComparison.Ordinal))
                .Select(value => JsonSerializer.Deserialize<JsonElement>(value["BOILER_INSTALL_PLAN_JSON=".Length..])).ToArray();
            if (plans.Length != 2 || !plans.Select(plan => plan.GetProperty("slot").GetString()).ToHashSet().SetEquals(["A", "B"])
                || plans.Any(plan => plan.GetProperty("device_package").GetProperty("manifest_sha256").GetString() != digest
                || !plan.GetProperty("components").EnumerateArray().Any(c => c.GetProperty("id").GetString() == "oxp3:legacy-adaptation")))
                throw new IOException("Expected both slots to record the exact model package and selected adaptation.");
            var sourceLine = readiness.Split('\n').Select(value => value.Trim()).Single(value => value.StartsWith("BOILER_SOURCE_JSON=", StringComparison.Ordinal));
            var source = JsonSerializer.Deserialize<JsonElement>(sourceLine["BOILER_SOURCE_JSON=".Length..]);
            var allowedKernels = package.GetProperty("compatibility").GetProperty("kernels").EnumerateArray().Select(k => k.GetString()).ToHashSet();
            foreach (var slot in source.GetProperty("slots").EnumerateObject())
                if (slot.Value.GetProperty("version").GetString() != "3.10" || slot.Value.GetProperty("kernels").EnumerateArray().Any(k => !allowedKernels.Contains(k.GetString())))
                    throw new IOException("Model QA requires the declared actual SteamOS 3.10 kernel in both image slots.");
            if (await FixtureHash() != originalFixtureDigest) throw new IOException("Original disposable fixture changed during model QA.");
            var result = new { passed = true, fixture, original_fixture_sha256 = originalFixtureDigest, original_fixture_unchanged = true,
                image, directory, steps, selection = options, package = new { id = package.GetProperty("id").GetString(), version = package.GetProperty("version").GetString(), manifest_sha256 = digest, trust = "developer-session" },
                outcome = JsonSerializer.Deserialize<JsonElement>(outcome), model_file_verification = modelVerification, source, plans,
                scope = "Fresh disposable image; model apply/verify hooks and common component file checks; no physical disk, firstboot or hardware acceptance" };
            var json = JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true });
            await File.WriteAllTextAsync(Path.Combine(directory, "result.json"), json);
            return json;
        }
        catch (Exception error)
        {
            await File.WriteAllTextAsync(Path.Combine(directory, "failure.json"), JsonSerializer.Serialize(new { passed = false, fixture, image, steps, error = error.ToString() }, new JsonSerializerOptions { WriteIndented = true }));
            throw;
        }
    }
}
