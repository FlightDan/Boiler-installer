using System.Net;
using System.Net.Http;
using System.Text.Json;
using System.IO;
using System.Text;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

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
        var logDirectory = Path.Combine(Backend.Root, "logs", "feedback-checks-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(logDirectory);
        var logPath = Path.Combine(logDirectory, "private-name.log");
        await File.WriteAllTextAsync(logPath, "stage 32% waiting\npassword=private-log-secret\nC:\\Users\\PrivateOwner\\trace\n", new UTF8Encoding(true));
        var attachment = FeedbackLog.Read(logPath);
        var logReport = FeedbackReport.Manual("", "", "", "0.1.0", attachment);
        using (var document = JsonDocument.Parse(logReport.Json))
        {
            var root = document.RootElement;
            Require(root.GetProperty("logs").GetString()!.Contains("stage 32% waiting")
                && root.GetProperty("kind").GetString() == "error" && root.GetProperty("mode").GetString() == "manual"
                && root.GetProperty("title").GetString()!.Length > 0 && root.GetProperty("message").GetString()!.Length > 0,
                "selected-log-needs-no-title-or-description");
            Require(new[] { "private-name", "PrivateOwner", "private-log-secret", logDirectory }.All(value => !logReport.Json.Contains(value)), "selected-log-redacts-secrets-and-omits-file-path-and-name");
        }
        await File.WriteAllTextAsync(logPath, "changed after preview", Encoding.UTF8);
        Require(FeedbackReport.Manual("", "", "", "0.1.0", attachment).Json.Contains("stage 32% waiting"), "selected-log-is-snapshot-not-reread-for-send");
        await File.WriteAllTextAsync(logPath, string.Concat(Enumerable.Repeat("中😀 continuing\n", 7000)) + "last stage waiting", Encoding.UTF8);
        var large = FeedbackLog.Read(logPath);
        Require(large.Truncated && Encoding.UTF8.GetByteCount(large.Text) <= 4096 && large.Text.EndsWith("last stage waiting") && !large.Text.Contains('\uFFFD'), "selected-large-log-redacted-utf8-tail-bounded");
        await File.WriteAllTextAsync(logPath, "UTF16日志\nlast stage waiting", Encoding.Unicode);
        Require(FeedbackLog.Read(logPath).Text.Contains("UTF16日志"), "selected-log-supports-utf16-bom");
        await File.WriteAllTextAsync(logPath, "\x1b[31mERROR waiting\x1b[0m\n", Encoding.UTF8);
        Require(FeedbackLog.Read(logPath).Text == "ERROR waiting", "selected-log-accepts-and-strips-ansi-colors");
        await File.WriteAllTextAsync(logPath, string.Concat(Enumerable.Repeat("中😀", 14000)) + "last single line", Encoding.UTF8);
        var singleLine = FeedbackLog.Read(logPath);
        Require(singleLine.Truncated && singleLine.Text.EndsWith("last single line") && !singleLine.Text.Contains('\uFFFD'), "selected-large-single-line-log-retains-valid-tail");
        await File.WriteAllBytesAsync(logPath, [0, 1, 2, 255]);
        Reject(() => FeedbackLog.Read(logPath), "selected-binary-log-rejected");
        await File.WriteAllTextAsync(logPath, " \n\t");
        Reject(() => FeedbackLog.Read(logPath), "selected-empty-log-rejected");
        await File.WriteAllTextAsync(logPath, "stage 32% waiting\npassword=private-log-secret\n");
        await CheckLogEditorAsync(logPath, logDirectory, Require);
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
        fake.Fault = false; fake.Status = HttpStatusCode.Created;
        Require((await FeedbackTransport.SendAsync(logReport, true, CancellationToken.None, client)).Sent && fake.Body == logReport.Json,
            "selected-log-send-is-exact-preview-with-fake-http-only");
        return JsonSerializer.Serialize(new { passed = true, checks });
    }
    private static async Task CheckLogEditorAsync(string logPath, string directory, Action<bool, string> require)
    {
        var owner = new Window { Width = 640, Height = 650, ShowInTaskbar = false };
        owner.Show();
        var window = new FeedbackWindow(owner);
        try
        {
            window.Show(); await window.SelectLogAsync(logPath);
            var body = (StackPanel)((ScrollViewer)window.Content).Content;
            var editorFields = body.Children.OfType<TextBox>().ToArray();
            require(editorFields.Length == 3 && editorFields.All(field => field.Text.Length == 0), "log-editor-keeps-title-description-email-optional");
            var preview = body.Children.OfType<WrapPanel>().Last().Children.OfType<Button>().First();
            preview.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            var reportText = body.Children.OfType<TextBox>().Single().Text;
            require(reportText.Contains("stage 32% waiting") && !reportText.Contains("private-log-secret"), "log-editor-preview-includes-selected-redacted-log");
            var actions = body.Children.OfType<WrapPanel>().Single();
            var send = actions.Children.OfType<Button>().First();
            require(!send.IsEnabled, "selected-log-preview-still-requires-consent");
            body.Children.OfType<CheckBox>().Single().IsChecked = true;
            require(send.IsEnabled, "selected-log-confirmation-enables-send-without-description");
            actions.Children.OfType<Button>().ElementAt(1).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            require(body.Children.OfType<TextBlock>().Any(text => text.Text.Contains("private-name.log")), "back-to-edit-retains-selected-log");
            await window.Dispatcher.InvokeAsync(() => { }, DispatcherPriority.Render);
            var root = (FrameworkElement)window.Content;
            root.UpdateLayout();
            var bitmap = new RenderTargetBitmap((int)root.ActualWidth, (int)root.ActualHeight, 96, 96, PixelFormats.Pbgra32);
            bitmap.Render(root);
            var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap));
            using var output = File.Create(Path.Combine(directory, "feedback-editor.png")); encoder.Save(output);
            // Never send to the real service from a source check.
            body.Children.OfType<WrapPanel>().First().Children.OfType<Button>().Last().RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            require(body.Children.OfType<TextBlock>().All(text => !text.Text.Contains("private-name.log")), "remove-log-clears-selection");
        }
        finally { window.Close(); owner.Close(); }
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
