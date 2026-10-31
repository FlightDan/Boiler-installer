using System.IO;
using System.Security.Cryptography;
using System.Text.Json;

namespace OxpInstaller;
internal static class IntegrationPayloadChecks
{
    internal static string Run()
    {
        var fixture = Path.Combine(Path.GetTempPath(), "boiler-payload-check-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(fixture);
        try
        {
            var root = Path.Combine(fixture, "root");
            var integration = Path.Combine(root, "cache", "integration");
            Directory.CreateDirectory(Path.Combine(integration, "resource-pool"));
            byte[] content = [1, 2, 3, 4];
            var manifest = JsonSerializer.SerializeToUtf8Bytes(new
            {
                schema = 1, local_test_resource_pool = true,
                components = new Dictionary<string, object>
                {
                    ["core:maintenance"] = new { available = true, files = new[] {
                        new { source = "resource-pool/shared.bin", size = content.Length,
                            sha256 = Convert.ToHexStringLower(SHA256.HashData(content)) } } }
                }
            });
            File.WriteAllBytes(Path.Combine(integration, "manifest.json"), manifest);
            var canonical = Path.Combine(integration, "resource-pool", "shared.bin");
            File.WriteAllBytes(canonical, content);
            Directory.CreateDirectory(Path.Combine(root, "cache", "resource-pool"));
            File.WriteAllText(Path.Combine(root, "cache", "resource-pool", "shared.bin"), "obsolete different bytes");
            var output = Path.Combine(fixture, "output");
            Backend.CopyIntegrationPayloadAtRoot(root, null, output);
            if (!File.ReadAllBytes(Path.Combine(output, "integration", "resource-pool", "shared.bin")).SequenceEqual(content)
                || Directory.GetFiles(output, "*", SearchOption.AllDirectories).Length != 2)
                throw new IOException("Canonical pool was not copied exactly once.");
            var metadata = Path.Combine(fixture, "metadata");
            Backend.CopyIntegrationPayloadAtRoot(root, null, metadata, metadataOnly: true);
            if (Directory.GetFiles(metadata, "*", SearchOption.AllDirectories).Length != 1)
                throw new IOException("Metadata-only handoff copied resource payloads.");
            File.WriteAllBytes(canonical, [4, 3, 2, 1]);
            var corrupt = Path.Combine(fixture, "corrupt");
            try { Backend.CopyIntegrationPayloadAtRoot(root, null, corrupt); }
            catch (IOException)
            {
                if (Directory.Exists(corrupt)) throw new IOException("Changed pool wrote a partial destination.");
                return "Canonical integration pool checks passed (3 cases).";
            }
            throw new IOException("Changed canonical pool was accepted.");
        }
        finally { Directory.Delete(fixture, true); }
    }
}
