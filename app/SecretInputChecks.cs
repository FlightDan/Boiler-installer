using System.IO;

namespace OxpInstaller;

internal static class SecretInputChecks
{
    internal static async Task<string> RunAsync()
    {
        const string prefix = "Handshake-r-中文-42!";
        var secret = prefix + new string('x', 255 - System.Text.Encoding.UTF8.GetByteCount(prefix));
        var transcript = await Backend.GuestAsync("secret-input-check", null, false,
            _ => { }, CancellationToken.None, secureInput: secret);
        if (!transcript.Contains("OXP_SECRET_CHECK=passed"))
            throw new IOException("Guest did not receive the complete secret.");
        if (!transcript.Contains("OXP_TASK_PERMISSIONS=passed"))
            throw new IOException("Password input changed subsequent task permissions.");
        if (transcript.Contains(secret) || transcript.Contains(Convert.ToBase64String(System.Text.Encoding.UTF8.GetBytes(secret))))
            throw new IOException("Secret leaked to guest console.");
        var withoutPassword = await Backend.GuestAsync("secret-input-check", null, false,
            _ => { }, CancellationToken.None);
        if (!withoutPassword.Contains("OXP_TASK_PERMISSIONS=passed") || withoutPassword.Contains("OXP_SECRET_CHECK=passed"))
            throw new IOException("Task permissions differ without password input.");
        foreach (var value in new[] { null, secret })
        {
            var split = await Backend.CheckSplitGuestPayloadAsync(value, CancellationToken.None);
            if (!split.Contains("OXP_PAYLOAD_VOLUMES=2") || !split.Contains("OXP_SPLIT_PAYLOAD_CHECK=passed")
                || split.Contains("OXP_SECRET_CHECK=passed") != (value != null)
                || split.Contains(secret) || split.Contains(Convert.ToBase64String(System.Text.Encoding.UTF8.GetBytes(secret))))
                throw new IOException("Two-volume payload launch or secret input check failed.");
        }
        return "SECRET_INPUT_CHECK=passed (real QEMU with one and two payload volumes, each with and without password; read-only transport and private secret input)";
    }
}
