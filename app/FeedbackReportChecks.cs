using System.Net;
using System.Net.Http;
using System.Text.Json;

namespace OxpInstaller;

internal static class FeedbackReportChecks
{
    internal static async Task<string> RunAsync()
    {
        var checks = new List<string>();
        void Require(bool value, string name) { if (!value) throw new InvalidOperationException(name); checks.Add(name); }
        void Reject(Action action, string name) { try { action(); } catch (ArgumentException) { checks.Add(name); return; } throw new InvalidOperationException(name); }
        foreach (var failure in Enum.GetValues<FeedbackFailure>())
        {
            var report = FeedbackReport.Automatic(failure, "0.1.0-preview");
            using var document = JsonDocument.Parse(report.Json); var root = document.RootElement;
            var allowed = new HashSet<string> { "schema_version", "report_id", "kind", "mode", "source", "app_version", "os", "component", "consent", "error_code" };
            Require(root.EnumerateObject().Select(p => p.Name).ToHashSet().SetEquals(allowed)
                && root.GetProperty("mode").GetString() == "automatic" && root.GetProperty("consent").GetBoolean(), "automatic-machine-only-" + failure);
            Require(Guid.Parse(report.Id).ToString() == report.Id && report.Id[14] == '4', "uuid4-" + failure);
        }
        Reject(() => FeedbackReport.Automatic((FeedbackFailure)999, "0.1.0"), "unknown-code-rejected");
        Reject(() => FeedbackReport.Automatic(FeedbackFailure.Install, "path/private data"), "unsafe-version-rejected");
        var diagnostic = new FeedbackDiagnostic();
        diagnostic.Stage("01.01"); diagnostic.Append("old stage\n"); diagnostic.Stage("02.03");
        diagnostic.Append("useful error\npassword=private phrase\nC:\\Users\\PrivateUser\\file\n");
        diagnostic.Failure(new System.IO.IOException("ignored"));
        var detailed = FeedbackReport.Automatic(FeedbackFailure.Install, "0.1.0", diagnostic);
        using (var doc = JsonDocument.Parse(detailed.Json))
        {
            var root = doc.RootElement; var evt = root.GetProperty("events")[0]; var excerpt = root.GetProperty("logs").GetString()!;
            Require(root.GetProperty("kind").GetString() == "error" && root.GetProperty("mode").GetString() == "manual"
                && evt.GetProperty("step").GetString() == "02.03" && evt.GetProperty("error_type").GetString() == "IOException", "diagnostic-contract-and-last-stage");
            Require(excerpt.Contains("useful error") && !excerpt.Contains("old stage") && !excerpt.Contains("private phrase") && !excerpt.Contains("PrivateUser"), "diagnostic-redaction-and-stage-boundary");
        }
        var longDiagnostic = new FeedbackDiagnostic(); longDiagnostic.Append(new string('中', 3000));
        Require(System.Text.Encoding.UTF8.GetByteCount(longDiagnostic.Log) <= 4096 && !longDiagnostic.Log.Contains('\uFFFD'), "diagnostic-utf8-tail-bounded");
        var failureDiagnostic = new FeedbackDiagnostic(); failureDiagnostic.FailureType(FeedbackDiagnostic.Classify(new System.IO.IOException("private")));
        failureDiagnostic.FailureType("private-custom-type");
        Require(failureDiagnostic.ErrorType == "IOException", "diagnostic-failure-type-is-closed");
        var redacted = FeedbackReport.Manual("设备反馈", "问题仍存在\n密码：private phrase with spaces\nsecret=another secret phrase\n--password command secret\nC:\\Users\\PrivateUser\\file.txt\n/home/person/config\nowner@example.test 192.168.2.3 2001:db8::1\nghp_fakeToken123456\n-----BEGIN PRIVATE KEY-----\nprivate-material", "reply@example.test", "0.1.0");
        using (var document = JsonDocument.Parse(redacted.Json))
        {
            var text = document.RootElement.GetProperty("message").GetString()!;
            Require(new[] { "private phrase", "another secret", "command secret", "PrivateUser", "person", "owner@example", "192.168", "2001:db8", "fakeToken", "private-material" }.All(secret => !text.Contains(secret)), "manual-redacts-english-chinese-secrets-addresses-paths-and-unclosed-key");
            Require(document.RootElement.GetProperty("contact_email").GetString() == "reply@example.test" && text.Contains("问题仍存在"), "manual-preserves-explicit-contact-and-useful-text");
            Require(!document.RootElement.TryGetProperty("logs", out _), "manual-never-collects-logs");
        }
        var quoted = FeedbackRedaction.Redact("\"password\": \"one two\"\nAuthorization: Basic abc\nCookie: session=abc\nftp://user:pass@host/private\nssid: SecretNetwork");
        Require(new[] { "one two", "Basic", "session=abc", "user:pass", "SecretNetwork" }.All(s => !quoted.Contains(s)), "manual-redacts-json-headers-urls-and-network-labels");
        FeedbackReport.Manual(new string('中', 80), new string('中', 682) + "ab", "", "0.1.0");
        checks.Add("utf8-exact-limits-accepted");
        Reject(() => FeedbackReport.Manual(new string('中', 81), "message", "", "0.1.0"), "utf8-title-overlimit-rejected");
        Reject(() => FeedbackReport.Manual("title", new string('中', 683), "", "0.1.0"), "utf8-message-overlimit-rejected");
        Reject(() => FeedbackReport.Manual("", "message", "", "0.1.0"), "empty-title-rejected");
        Reject(() => FeedbackReport.Manual("title", "message", "bad email", "0.1.0"), "invalid-email-rejected");
        Exception? preSessionFailure = null;
        try { using var absent = System.IO.File.OpenRead(System.IO.Path.Combine(AppContext.BaseDirectory, "absent-pre-session-" + Guid.NewGuid().ToString("N"))); }
        catch (System.IO.IOException error) { preSessionFailure = error; }
        Require(preSessionFailure != null && FeedbackReport.OfferForException(preSessionFailure, CancellationToken.None), "real-pre-session-io-failure-offered-without-session");
        Require(!FeedbackReport.OfferForException(new OperationCanceledException(), CancellationToken.None), "cancelled-operation-never-offered");
        using (var stopped = new CancellationTokenSource())
        { stopped.Cancel(); Require(!FeedbackReport.OfferForException(new System.IO.IOException("stopped"), stopped.Token), "stopped-token-never-offered"); }
        var fake = new FakeHandler(); using var client = new HttpClient(fake);
        var machine = FeedbackReport.Automatic(FeedbackFailure.Install, "0.1.0");
        Require(!(await FeedbackTransport.SendAsync(machine, false, CancellationToken.None, client)).Sent && fake.Calls == 0, "no-consent-no-request");
        Require((await FeedbackTransport.SendAsync(machine, true, CancellationToken.None, client)).Sent && fake.Calls == 1
            && fake.Body == machine.Json && fake.Uri == FeedbackReport.Endpoint && fake.MediaType == "application/json", "confirmed-send-exact-preview-https-json");
        fake.Status = HttpStatusCode.TooManyRequests;
        Require(!(await FeedbackTransport.SendAsync(machine, true, CancellationToken.None, client)).Sent && fake.Calls == 2, "rate-limit-no-retry");
        fake.Status = HttpStatusCode.Found;
        Require(!(await FeedbackTransport.SendAsync(machine, true, CancellationToken.None, client)).Sent && fake.Calls == 3, "redirect-not-success");
        fake.Fault = true;
        Require((await FeedbackTransport.SendAsync(machine, true, CancellationToken.None, client)).Code == "network" && fake.Calls == 4, "network-failure-contained-no-retry");
        using var cancelled = new CancellationTokenSource(); cancelled.Cancel();
        Require(!(await FeedbackTransport.SendAsync(machine, true, cancelled.Token, client)).Sent, "closed-window-cancellation-contained");
        return JsonSerializer.Serialize(new { passed = true, checks });
    }
    private sealed class FakeHandler : HttpMessageHandler
    {
        internal int Calls; internal string? Body; internal Uri? Uri; internal string? MediaType;
        internal bool Fault; internal HttpStatusCode Status = HttpStatusCode.Created;
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested(); Calls++; Body = await request.Content!.ReadAsStringAsync(cancellationToken);
            Uri = request.RequestUri; MediaType = request.Content.Headers.ContentType?.MediaType;
            if (Fault) throw new HttpRequestException("synthetic network failure");
            return new HttpResponseMessage(Status);
        }
    }
}
