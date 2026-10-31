using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Automation;
using System.IO;
using Microsoft.Win32;

namespace OxpInstaller;

internal sealed class FeedbackWindow : Window
{
    private readonly StackPanel body = new() { Margin = new(24) };
    private readonly TextBox title = new() { MaxLength = 240 };
    private readonly TextBox message = new() { AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, Height = 90, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, MaxLength = 2048 };
    private readonly TextBox email = new() { MaxLength = 254 };
    private readonly TextBox preview = new() { IsReadOnly = true, TextWrapping = TextWrapping.Wrap, AcceptsReturn = true, Height = 240, VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
    private CheckBox consent = new();
    private readonly TextBlock status = new() { TextWrapping = TextWrapping.Wrap, Margin = new(0, 12, 0, 8), Foreground = new SolidColorBrush(Color.FromRgb(163, 71, 39)) };
    private readonly CancellationTokenSource cancellation = new();
    private FeedbackReport? report;
    private bool sending;
    private bool sent;
    private readonly bool automatic;
    private readonly FeedbackFailure failure;
    private FeedbackLog? attachment;
    private string? attachmentName;
    private readonly TextBlock attachmentStatus = new() { TextWrapping = TextWrapping.Wrap, Margin = new(0, 8, 0, 0) };
    private static string L(string zh, string en) => PartitionInventory.L(zh, en);

    internal FeedbackWindow(Window owner, FeedbackFailure? failure = null, FeedbackDiagnostic? diagnostic = null)
    {
        Owner = owner; this.failure = failure ?? FeedbackFailure.Install; automatic = failure.HasValue;
        Title = automatic ? L("发送脱敏错误报告", "Send a redacted error report") : L("手动反馈", "Send feedback");
        Width = 640; Height = 650; MinWidth = 360; MinHeight = 360; WindowStartupLocation = WindowStartupLocation.CenterOwner;
        FontFamily = owner.FontFamily; FontSize = 14; Background = new SolidColorBrush(Color.FromRgb(244, 246, 250));
        Content = new ScrollViewer { Content = body, Background = Background, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };
        AutomationProperties.SetName(preview, L("实际发送的脱敏报告", "Exact redacted report to send"));
        Closed += (_, _) => cancellation.Cancel();
        if (automatic) ShowPreview(FeedbackReport.Automatic(this.failure, ReleaseInfo.DisplayVersion, diagnostic)); else ShowEditor();
    }
    private TextBlock Label(string text, bool heading = false) => new() { Text = text, TextWrapping = TextWrapping.Wrap, FontSize = heading ? 22 : 14, FontWeight = heading ? FontWeights.SemiBold : FontWeights.Normal, Margin = new(0, heading ? 0 : 14, 0, 8) };
    private Button Button(string text, RoutedEventHandler click)
    {
        var button = new Button { Content = text, MinHeight = 48, Padding = new(16, 8, 16, 8), Margin = new(0, 8, 12, 0) };
        button.Click += click; return button;
    }
    private void ShowEditor()
    {
        body.Children.Clear(); report = null; consent.IsChecked = false;
        body.Children.Add(Label(L("反馈问题或建议", "Report an issue or suggestion"), true));
        body.Children.Add(Label(L("安装卡住或报错时，选择日志即可反馈，无需填写描述。只读取你选择的文件，脱敏后由你预览确认。", "If installation stalls or fails, select a log to send feedback without a description. Only your selected file is read, then redacted for your review.")));
        var logActions = new WrapPanel();
        logActions.Children.Add(Button(L("选择日志文件…", "Choose log file…"), async (_, _) =>
        {
            var picker = new OpenFileDialog { Title = L("选择要反馈的日志", "Choose a log for feedback"), Filter = L("文本日志|*.log;*.txt;*.jsonl|所有文件|*.*", "Text logs|*.log;*.txt;*.jsonl|All files|*.*"), CheckFileExists = true, Multiselect = false };
            var directory = Path.Combine(Backend.Root, "logs");
            if (Directory.Exists(directory)) picker.InitialDirectory = directory;
            if (picker.ShowDialog(this) != true) return;
            body.IsEnabled = false; status.Text = L("正在读取并脱敏日志…", "Reading and redacting log…");
            try { await SelectLogAsync(picker.FileName); status.Text = ""; }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException or ArgumentException or System.Security.SecurityException)
            { status.Text = L("未能读取所选日志，请检查文件权限或选择其他文本日志。", "Could not read the selected log. Check access or choose another text log.") + "\n" + (error is ArgumentException ? error.Message : ""); }
            finally { body.IsEnabled = true; }
        }));
        var remove = Button(L("移除日志", "Remove log"), (_, _) => { attachment = null; attachmentName = null; UpdateAttachmentStatus(); });
        logActions.Children.Add(remove); body.Children.Add(logActions); body.Children.Add(attachmentStatus); UpdateAttachmentStatus();
        body.Children.Add(Label(L("服务最多接收 4 KiB 日志，将发送脱敏后的末尾片段，不上传完整文件或本地路径。", "The service accepts up to 4 KiB of log text. Only the redacted tail is sent, without the full file or local path.")));
        body.Children.Add(Label(L("标题（已选日志时可不填）", "Title (optional with a log)"))); body.Children.Add(title); AutomationProperties.SetName(title, L("反馈标题", "Feedback title"));
        body.Children.Add(Label(L("补充说明（已选日志时可不填，最多 2048 UTF-8 字节）", "Description (optional with a log; up to 2048 UTF-8 bytes)"))); body.Children.Add(message); AutomationProperties.SetName(message, L("反馈正文", "Feedback message"));
        body.Children.Add(Label(L("邮箱（选填，会随报告发送）", "Email (optional; included in the report)"))); body.Children.Add(email); AutomationProperties.SetName(email, L("联系邮箱（选填）", "Contact email (optional)"));
        status.Text = ""; body.Children.Add(status);
        var actions = new WrapPanel();
        actions.Children.Add(Button(L("预览脱敏内容", "Preview redacted report"), (_, _) =>
        {
            try { ShowPreview(FeedbackReport.Manual(title.Text, message.Text, email.Text, ReleaseInfo.DisplayVersion, attachment)); }
            catch (ArgumentException error) { status.Text = error.Message; }
        }));
        actions.Children.Add(Button(L("取消", "Cancel"), (_, _) => Close())); body.Children.Add(actions);
    }
    internal async Task SelectLogAsync(string path)
    {
        var selected = await Task.Run(() => FeedbackLog.Read(path));
        if (cancellation.IsCancellationRequested) return;
        attachment = selected; attachmentName = Path.GetFileName(path); UpdateAttachmentStatus();
    }
    private void UpdateAttachmentStatus() => attachmentStatus.Text = attachment == null
        ? L("尚未选择日志。也可以只填写文字反馈。", "No log selected. You can also send text feedback.")
        : attachmentName + L(" · 已读取并脱敏", " · Read and redacted") + (attachment.Truncated ? L(" · 仅发送末尾片段", " · Tail excerpt only") : "");
    private void ShowPreview(FeedbackReport value)
    {
        // Detach shared controls before placing them into the preview layout.
        body.Children.Clear(); report = value;
        consent = new CheckBox { Margin = new(0, 16, 0, 8), MinHeight = 48, IsChecked = false,
            Content = new TextBlock { Text = L("我已检查上述内容，同意本次发送。", "I reviewed the content above and agree to send it now."), TextWrapping = TextWrapping.Wrap } };
        body.Children.Add(Label(automatic ? L("是否发送本次错误报告？", "Send this error report?") : L("确认要发送的内容", "Review what will be sent"), true));
        body.Children.Add(Label(automatic
            ? L("以下是本次实际发送的内容，可能包含最后阶段的日志片段。已尝试脱敏，但仍可能包含敏感信息，请检查后决定是否发送；取消或关闭不会发送。", "The complete report below may contain a log excerpt from the last stage. Redaction is best effort and sensitive information may remain. Review it before sending; cancel or close to skip.")
            : L("以下是实际发送的全部 JSON。请检查仍可能遗漏的敏感内容；需要修改时返回编辑。", "This is the complete JSON that will be sent. Check for sensitive content that redaction may have missed; return to edit if needed.")));
        body.Children.Add(Label(FeedbackReport.Endpoint.ToString()));
        preview.Text = value.Json; body.Children.Add(preview);
        if (!automatic) body.Children.Add(consent);
        status.Text = ""; body.Children.Add(status);
        var actions = new WrapPanel();
        var send = Button(automatic ? L("同意发送匿名报告", "Agree to send anonymous report") : L("确认发送", "Send report"), async (_, _) =>
        {
            if (sending || sent || (!automatic && consent.IsChecked != true) || report == null) return;
            sending = true; actions.IsEnabled = false; consent.IsEnabled = false;
            status.Text = L("正在发送…", "Sending…");
            var result = await FeedbackTransport.SendAsync(report, true, cancellation.Token);
            if (cancellation.IsCancellationRequested) return;
            sending = false; sent = result.Sent;
            status.Text = result.Sent ? L("已发送。报告编号：", "Sent. Report ID: ") + report.Id
                : L("发送未成功，安装结果不受影响。可稍后手动反馈。", "Report was not sent. The installation result is unchanged. You can send feedback later.");
            actions.IsEnabled = true;
            // One request per consent. Failed reports are never retried silently.
            foreach (var action in actions.Children.OfType<Button>()) action.IsEnabled = false;
            actions.Children.Add(Button(L("关闭", "Close"), (_, _) => Close()));
        });
        send.IsEnabled = automatic;
        consent.Checked += (_, _) => send.IsEnabled = !sending && !sent;
        consent.Unchecked += (_, _) => send.IsEnabled = false;
        actions.Children.Add(send);
        if (!automatic) actions.Children.Add(Button(L("返回编辑", "Back to edit"), (_, _) => ShowEditor()));
        actions.Children.Add(Button(L("不发送", "Do not send"), (_, _) => Close())); body.Children.Add(actions);
    }
}

public partial class MainWindow
{
    private void OpenFeedback(object sender, RoutedEventArgs args) => ShowFeedbackWindow(null);
    internal void OfferFailureFeedback(FeedbackFailure failure, FeedbackDiagnostic? diagnostic = null) => ShowFeedbackWindow(failure, diagnostic);
    private void ShowFeedbackWindow(FeedbackFailure? failure, FeedbackDiagnostic? diagnostic = null)
    {
        // Reporting is best effort and never changes or re-runs installation.
        if (!IsLoaded || !IsVisible || closeAfterCleanup) return;
        try { new FeedbackWindow(this, failure, diagnostic).Show(); }
        catch { /* The original operation result and existing log export remain available. */ }
    }
}
