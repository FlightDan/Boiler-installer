using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace OxpInstaller;

internal enum FeedbackFailure { Install, Resume, ImageDownload, UpdateDownload }

// Keeps only the current operation's most recent stage in memory. Free-form
// output is shown to the user after best-effort redaction, before any send.
internal sealed class FeedbackDiagnostic
{
    private string stage = "unknown";
    private string log = "";
    private string? errorType;
    private readonly object gate = new();
    internal void Stage(string? value)
    {
        if (value == null || !(value is "download-probe" or "download-transfer" or "update-preparation" || Regex.IsMatch(value, @"\A[0-9]{2}(?:\.[0-9]{2}){1,3}\z"))) return;
        lock (gate) { if (value != stage) { stage = value; log = ""; } }
    }
    internal void Append(string value)
    {
        lock (gate) { log += value; if (log.Length > 65536) log = log[^65536..]; }
    }
    internal void Failure(Exception error)
    {
        FailureType(Classify(error));
    }
    internal static string Classify(Exception error) => error switch { HttpRequestException => "HttpRequestException", UnauthorizedAccessException => "UnauthorizedAccessException", System.IO.IOException => "IOException", TimeoutException => "TimeoutException", _ => "OtherException" };
    internal void FailureType(string? value)
    {
        if (value is "HttpRequestException" or "UnauthorizedAccessException" or "IOException" or "TimeoutException" or "OtherException") lock (gate) errorType = value;
    }
    internal string Step { get { lock (gate) return stage; } }
    internal string? ErrorType { get { lock (gate) return errorType; } }
    internal string Log { get { lock (gate) return FeedbackRedaction.Tail(FeedbackRedaction.Redact(log).Trim(), 4096); } }
}

// Automatic reports take an enum, never an exception or a log. The immutable
// JSON shown in the preview is also the exact UTF-8 request body.
internal sealed record FeedbackReport(string Json, string Id)
{
    internal static readonly Uri Endpoint = new("https://feedback.devdan.cc/api/v1/reports");
    internal const int MaximumBytes = 12288;
    internal static bool OfferForException(Exception error, CancellationToken token) => error is not OperationCanceledException && !token.IsCancellationRequested;
    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true, Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping };
    private static void Version(string version)
    {
        if (!Regex.IsMatch(version, @"\A[A-Za-z0-9_.:+-]{1,64}\z")) throw new ArgumentException("Invalid application version.");
    }
    private static FeedbackReport Create(Dictionary<string, object> fields, string version)
    {
        Version(version);
        var id = Guid.NewGuid().ToString();
        fields["schema_version"] = 1; fields["report_id"] = id;
        fields["source"] = "windows-installer"; fields["app_version"] = version;
        fields["os"] = "windows"; fields["component"] = "installer"; fields["consent"] = true;
        var json = JsonSerializer.Serialize(fields, JsonOptions);
        if (Encoding.UTF8.GetByteCount(json) > MaximumBytes) throw new ArgumentException("Report is too large.");
        return new(json, id);
    }
    internal static FeedbackReport Automatic(FeedbackFailure failure, string version, FeedbackDiagnostic? diagnostic = null)
    {
        var code = failure switch { FeedbackFailure.Install => "INSTALL_FAILED", FeedbackFailure.Resume => "INSTALL_RESUME_FAILED", FeedbackFailure.ImageDownload => "IMAGE_DOWNLOAD_FAILED", FeedbackFailure.UpdateDownload => "UPDATE_DOWNLOAD_FAILED", _ => throw new ArgumentOutOfRangeException(nameof(failure)) };
        var fields = new Dictionary<string, object> { ["kind"] = "error", ["mode"] = "automatic", ["error_code"] = code };
        if (diagnostic != null)
        {
            var item = new Dictionary<string, object> { ["level"] = "error", ["code"] = code, ["step"] = diagnostic.Step };
            if (diagnostic.ErrorType != null) item["error_type"] = diagnostic.ErrorType;
            fields["events"] = new[] { item };
            var log = diagnostic.Log;
            if (log.Length > 0)
            {
                // The v1 service accepts logs only for an explicitly confirmed manual submission.
                fields["mode"] = "manual"; fields["title"] = "Installer error";
                fields["message"] = "User approved diagnostic report for the last stage."; fields["logs"] = log;
            }
        }
        return Create(fields, version);
    }
    internal static FeedbackReport Manual(string title, string message, string email, string version)
    {
        title = FeedbackRedaction.Redact(title).Trim(); message = FeedbackRedaction.Redact(message).Trim(); email = email.Trim();
        if (title.Length < 1 || Encoding.UTF8.GetByteCount(title) > 240 || message.Length < 1 || Encoding.UTF8.GetByteCount(message) > 2048)
            throw new ArgumentException("请填写标题（最多 240 UTF-8 字节）及正文（最多 2048 UTF-8 字节）。 / Enter a title (up to 240 UTF-8 bytes) and message (up to 2048 UTF-8 bytes).");
        var fields = new Dictionary<string, object> { ["kind"] = "feedback", ["mode"] = "manual", ["title"] = title, ["message"] = message };
        if (email.Length > 0)
        {
            if (email.Length > 254 || !Regex.IsMatch(email, @"\A[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}\z"))
                throw new ArgumentException("邮箱格式无效。 / Enter a valid email address.");
            fields["contact_email"] = email;
        }
        return Create(fields, version);
    }
}

internal static class FeedbackRedaction
{
    internal static string Tail(string text, int maxBytes)
    {
        if (Encoding.UTF8.GetByteCount(text) <= maxBytes) return text;
        var start = text.Length;
        var bytes = 0;
        while (start > 0)
        {
            var width = start >= 2 && char.IsLowSurrogate(text[start - 1]) && char.IsHighSurrogate(text[start - 2]) ? 2 : 1;
            var count = Encoding.UTF8.GetByteCount(text.AsSpan(start - width, width));
            if (bytes + count > maxBytes) break;
            bytes += count; start -= width;
        }
        return text[start..];
    }
    // Best-effort scrubbing of user-written text; no files, logs, hardware IDs,
    // credentials or local paths are collected by this feature.
    internal static string Redact(string text)
    {
        text = Regex.Replace(text, @"\x1b\][\s\S]*?(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]", "");
        text = Regex.Replace(text, @"-----BEGIN [^-\r\n]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-\r\n]*PRIVATE KEY-----|\z)", "[REDACTED_KEY]", RegexOptions.IgnoreCase);
        text = Regex.Replace(text, @"(?im)\b(?:authorization|proxy-authorization|cookie|set-cookie)\s*:[^\r\n]*", "[REDACTED_HEADER]");
        text = Regex.Replace(text, @"(?im)(?:[\w.-]*(?:password|passwd|secret|token|api[_-]?key|private[_-]?key|serial|partuuid|machine[_-]?id|username|hostname)[\w.-]*|pwd|ssid|bssid|mac|密码|口令|令牌|密钥|序列号|用户名|主机名)\s*[""']?\s*[:=：]\s*[^\r\n]*", "[REDACTED_SECRET]");
        text = Regex.Replace(text, @"(?im)--?(?:password|passwd|pwd|token|secret|api[_-]?key)\s+[^\r\n]*", "[REDACTED_SECRET]");
        text = Regex.Replace(text, @"(?i)\b(?:bearer|basic)\s+[^\s,""'}]+", "[REDACTED_TOKEN]");
        text = Regex.Replace(text, @"(?i)\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|AKIA[A-Z0-9]{16})\b|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[REDACTED_TOKEN]");
        text = Regex.Replace(text, @"(?i)\b[a-z][a-z0-9+.-]*://[^\s<>""']+", "[REDACTED_URL]");
        text = Regex.Replace(text, @"(?i)(?:[A-Z]:[\\/]|\\\\)[^\r\n<>""|]*", "[REDACTED_PATH]");
        text = Regex.Replace(text, @"(?<![\w:])/(?:[^\s/""'<>]+/)*[^\s""'<>]*", "[REDACTED_PATH]");
        text = Regex.Replace(text, @"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]");
        text = Regex.Replace(text, @"(?<![\w])(?:\d{1,3}\.){3}\d{1,3}(?![\w])", "[REDACTED_IP]");
        text = Regex.Replace(text, @"(?i)(?<![\w:])(?:[0-9a-f]{0,4}:){2,}[0-9a-f:]{0,39}(?:%[\w.-]+)?(?![\w:])", "[REDACTED_IP]");
        text = Regex.Replace(text, @"(?i)\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b", "[REDACTED_MAC]");
        return Regex.Replace(text, @"[\x00-\x08\x0b-\x1f\x7f-\x9f]", "");
    }
}

internal sealed record FeedbackSendResult(bool Sent, string Code);

internal static class FeedbackTransport
{
    private static readonly HttpClient Client = new(new HttpClientHandler { AllowAutoRedirect = false, UseCookies = false, UseProxy = false }) { Timeout = TimeSpan.FromSeconds(12) };
    // There is no background upload, queue or retry. Consent is supplied only
    // by the visible confirmation control for this immutable preview.
    internal static async Task<FeedbackSendResult> SendAsync(FeedbackReport report, bool consent, CancellationToken token, HttpClient? client = null)
    {
        if (!consent) return new(false, "consent-required");
        if (Encoding.UTF8.GetByteCount(report.Json) > FeedbackReport.MaximumBytes) return new(false, "too-large");
        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Post, FeedbackReport.Endpoint);
            request.Content = new StringContent(report.Json, Encoding.UTF8, "application/json");
            using var response = await (client ?? Client).SendAsync(request, HttpCompletionOption.ResponseHeadersRead, token);
            // Do not read/echo a server response or propagate transport details
            // into installation results. A bounded fixed status is enough.
            return response.StatusCode is HttpStatusCode.Created or HttpStatusCode.OK
                ? new(true, "sent") : new(false, response.StatusCode == HttpStatusCode.TooManyRequests ? "rate-limited" : "rejected");
        }
        catch (OperationCanceledException) { return new(false, token.IsCancellationRequested ? "cancelled" : "timeout"); }
        catch (HttpRequestException) { return new(false, "network"); }
        catch (Exception) { return new(false, "unavailable"); }
    }
}
