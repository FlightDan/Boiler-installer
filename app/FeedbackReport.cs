using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.IO;
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

// The immutable JSON shown in the preview is also the exact UTF-8 request body.
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
    internal static FeedbackReport Manual(string title, string message, string email, string version, FeedbackLog? attachment = null)
    {
        title = FeedbackRedaction.Redact(title).Trim(); message = FeedbackRedaction.Redact(message).Trim(); email = email.Trim();
        if (attachment != null)
        {
            if (title.Length == 0) title = "Installer log feedback";
            if (message.Length == 0) message = "User selected a log file for review.";
        }
        if (title.Length < 1 || Encoding.UTF8.GetByteCount(title) > 240 || message.Length < 1 || Encoding.UTF8.GetByteCount(message) > 2048)
            throw new ArgumentException("请填写标题（最多 240 UTF-8 字节）及正文（最多 2048 UTF-8 字节）。 / Enter a title (up to 240 UTF-8 bytes) and message (up to 2048 UTF-8 bytes).");
        var fields = new Dictionary<string, object> { ["kind"] = "feedback", ["mode"] = "manual", ["title"] = title, ["message"] = message };
        if (attachment != null)
        {
            fields["kind"] = "error"; fields["error_code"] = "INSTALLER_LOG_FEEDBACK";
            fields["logs"] = attachment.Text;
        }
        if (email.Length > 0)
        {
            if (email.Length > 254 || !Regex.IsMatch(email, @"\A[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}\z"))
                throw new ArgumentException("邮箱格式无效。 / Enter a valid email address.");
            fields["contact_email"] = email;
        }
        return Create(fields, version);
    }
}

// A local snapshot: sending never reopens a selected or still-growing file.
internal sealed record FeedbackLog
{
    internal string Text { get; }
    internal bool Truncated { get; }
    private FeedbackLog(string text, bool truncated) { Text = text; Truncated = truncated; }
    internal static FeedbackLog Read(string path)
    {
        using var file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
        if (!file.CanSeek || (File.GetAttributes(path) & (FileAttributes.Directory | FileAttributes.Device)) != 0)
            throw new ArgumentException("请选择普通文本日志文件。 / Select a regular text log file.");
        var length = file.Length;
        var prefix = new byte[3]; var prefixLength = file.Read(prefix);
        var utf16 = prefixLength >= 2 && ((prefix[0] == 0xff && prefix[1] == 0xfe) || (prefix[0] == 0xfe && prefix[1] == 0xff));
        Encoding encoding = utf16 ? new UnicodeEncoding(prefix[0] == 0xfe, false, true) : new UTF8Encoding(false, true);
        var bom = utf16 ? 2 : prefixLength == 3 && prefix[0] == 0xef && prefix[1] == 0xbb && prefix[2] == 0xbf ? 3 : 0;
        var offset = Math.Max(bom, length - 65536);
        if (utf16 && (offset - bom) % 2 != 0) offset++;
        file.Position = offset;
        var bytes = new byte[(int)(length - offset)]; file.ReadExactly(bytes);
        // Skip a clipped first line before decoding, including any split UTF-8 character.
        var start = 0;
        if (offset > bom)
        {
            var unit = utf16 ? 2 : 1;
            var foundNewline = false;
            while (start + unit <= bytes.Length)
            {
                var newline = utf16 ? (prefix[0] == 0xff ? bytes[start] is 10 or 13 && bytes[start + 1] == 0 : bytes[start] == 0 && bytes[start + 1] is 10 or 13) : bytes[start] is 10 or 13;
                start += unit;
                if (newline) { foundNewline = true; break; }
            }
            if (!foundNewline)
            {
                start = 0;
                if (!utf16) while (start < bytes.Length && (bytes[start] & 0xc0) == 0x80) start++;
                else if (bytes.Length >= 2)
                {
                    var first = prefix[0] == 0xff ? bytes[0] | bytes[1] << 8 : bytes[0] << 8 | bytes[1];
                    if (first is >= 0xdc00 and <= 0xdfff) start = 2;
                }
            }
        }
        string text;
        try { text = encoding.GetString(bytes, start, bytes.Length - start); }
        catch (DecoderFallbackException) { throw new ArgumentException("日志编码无效，请选择 UTF-8 或带 BOM 的 UTF-16 文本。 / Select UTF-8 or BOM-marked UTF-16 text."); }
        if (text.Any(c => char.IsControl(c) && c is not '\r' and not '\n' and not '\t' and not '\x1b'))
            throw new ArgumentException("文件包含二进制内容，请选择文本日志。 / Select a text log, not a binary file.");
        text = FeedbackRedaction.Redact(text).Trim();
        if (text.Length == 0) throw new ArgumentException("日志为空或没有可发送的文本，请选择其他日志。 / This log has no text to send; select another log.");
        var excerpt = FeedbackRedaction.Tail(text, 4096);
        return new(excerpt, offset > bom || excerpt.Length < text.Length);
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
    // Best-effort scrubbing. The user reviews the exact result before sending.
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
