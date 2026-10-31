using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Automation;

namespace OxpInstaller;

internal sealed class FeedbackWindow : Window
{
    private readonly StackPanel body = new() { Margin = new(24) };
    private readonly TextBox title = new() { MaxLength = 240 };
    private readonly TextBox message = new() { AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, Height = 150, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, MaxLength = 2048 };
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
    private static string L(string zh, string en) => PartitionInventory.L(zh, en);

    internal FeedbackWindow(Window owner, FeedbackFailure? failure = null, FeedbackDiagnostic? diagnostic = null)
    {
        Owner = owner; this.failure = failure ?? FeedbackFailure.Install; automatic = failure.HasValue;
        Title = automatic ? L("发送脱敏错误报告", "Send a redacted error report") : L("手动反馈", "Send feedback");
        Width = 640; Height = 650; MinWidth = 360; MinHeight = 360; WindowStartupLocation = WindowStartupLocation.CenterOwner;
        FontFamily = owner.FontFamily; FontSize = 14; Background = new SolidColorBrush(Color.FromRgb(244, 246, 250));
        Content = new ScrollViewer { Content = body, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };
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
        body.Children.Add(Label(L("不收集日志或设备信息。输入内容会先脱敏，再由你预览确认。邮箱为自愿提供的联系方式。", "No logs or device information are collected. Review the redacted content before sending. Email is optional contact information.")));
        body.Children.Add(Label(L("标题", "Title"))); body.Children.Add(title); AutomationProperties.SetName(title, L("反馈标题", "Feedback title"));
        body.Children.Add(Label(L("发生了什么？（最多 2048 UTF-8 字节）", "What happened? (up to 2048 UTF-8 bytes)"))); body.Children.Add(message); AutomationProperties.SetName(message, L("反馈正文", "Feedback message"));
        body.Children.Add(Label(L("邮箱（选填，会随报告发送）", "Email (optional; included in the report)"))); body.Children.Add(email); AutomationProperties.SetName(email, L("联系邮箱（选填）", "Contact email (optional)"));
        status.Text = ""; body.Children.Add(status);
        var actions = new WrapPanel();
        actions.Children.Add(Button(L("预览脱敏内容", "Preview redacted report"), (_, _) =>
        {
            try { ShowPreview(FeedbackReport.Manual(title.Text, message.Text, email.Text, ReleaseInfo.DisplayVersion)); }
            catch (ArgumentException error) { status.Text = error.Message; }
        }));
        actions.Children.Add(Button(L("取消", "Cancel"), (_, _) => Close())); body.Children.Add(actions);
    }
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
