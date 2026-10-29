using System.Runtime.InteropServices;
using System.Text;
using System.Windows;
using System.Windows.Interop;

namespace OxpInstaller;

// Explorer normally runs below the installer's integrity level. Accept only the
// legacy Shell file-drop transport on this HWND; dropped bytes remain untrusted.
internal sealed class DeviceFileDrop : IDisposable
{
    private const int DropFiles = 0x0233;
    private const uint CopyGlobalData = 0x0049;
    private readonly HwndSource source;
    private readonly Action<IReadOnlyList<string>> received;
    private bool disposed;

    internal DeviceFileDrop(HwndSource source, Action<IReadOnlyList<string>> received)
    {
        this.source = source; this.received = received;
        // Do not change the process-wide filter or accept arbitrary WM_COPYDATA.
        if (!ChangeWindowMessageFilterEx(source.Handle, DropFiles, 1, IntPtr.Zero)
            || !ChangeWindowMessageFilterEx(source.Handle, CopyGlobalData, 1, IntPtr.Zero))
        {
            var error = Marshal.GetLastWin32Error();
            ChangeWindowMessageFilterEx(source.Handle, DropFiles, 0, IntPtr.Zero);
            throw new System.ComponentModel.Win32Exception(error);
        }
        source.AddHook(WindowMessage);
        DragAcceptFiles(source.Handle, true);
    }

    private IntPtr WindowMessage(IntPtr hwnd, int message, IntPtr wParam, IntPtr lParam, ref bool handled)
    {
        if (message != DropFiles) return IntPtr.Zero;
        handled = true;
        var paths = new List<string>();
        try
        {
            if (lParam != IntPtr.Zero || wParam == IntPtr.Zero) return IntPtr.Zero;
            var count = DragQueryFile(wParam, uint.MaxValue, null, 0);
            var total = 0;
            if (count is > 0 and <= 32)
            {
                for (uint index = 0; index < count; index++)
                {
                    var length = DragQueryFile(wParam, index, null, 0);
                    if (length is 0 or > 32767 || (total += (int)length) > 131072) { paths.Clear(); break; }
                    var buffer = new StringBuilder((int)length + 1);
                    if (DragQueryFile(wParam, index, buffer, (uint)buffer.Capacity) != length) { paths.Clear(); break; }
                    paths.Add(buffer.ToString());
                }
            }
        }
        finally { if (wParam != IntPtr.Zero) DragFinish(wParam); }
        // Dispatch after the native handle has been released. The receiver checks
        // current route, busy state, format, and session trust before selection.
        if (!disposed) received(paths);
        return IntPtr.Zero;
    }

    public void Dispose()
    {
        if (disposed) return;
        disposed = true;
        if (!source.IsDisposed)
        {
            source.RemoveHook(WindowMessage);
            DragAcceptFiles(source.Handle, false);
            ChangeWindowMessageFilterEx(source.Handle, DropFiles, 0, IntPtr.Zero);
            ChangeWindowMessageFilterEx(source.Handle, CopyGlobalData, 0, IntPtr.Zero);
        }
    }

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool ChangeWindowMessageFilterEx(IntPtr hwnd, uint message, uint action, IntPtr change);
    [DllImport("shell32.dll")]
    private static extern void DragAcceptFiles(IntPtr hwnd, [MarshalAs(UnmanagedType.Bool)] bool accept);
    [DllImport("shell32.dll", EntryPoint = "DragQueryFileW", CharSet = CharSet.Unicode)]
    private static extern uint DragQueryFile(IntPtr drop, uint index, StringBuilder? buffer, uint length);
    [DllImport("shell32.dll")]
    private static extern void DragFinish(IntPtr drop);
}

public partial class MainWindow
{
    private DeviceFileDrop? deviceFileDrop;
    private bool deviceDropLifetimeInitialized;

    private void InitializeDeviceFileDrop()
    {
        if (deviceDropLifetimeInitialized) return;
        deviceDropLifetimeInitialized = true;
        void Attach()
        {
            var handle = new WindowInteropHelper(this).Handle;
            if (deviceFileDrop != null || handle == IntPtr.Zero || HwndSource.FromHwnd(handle) is not HwndSource source) return;
            try
            {
                deviceFileDrop = new DeviceFileDrop(source, paths => { _ = ReceiveDroppedInputFilesAsync(paths); });
            }
            catch (System.ComponentModel.Win32Exception error)
            {
                // File and folder pickers remain available if Shell drop cannot
                // be registered by the OS. Do not silently claim drag is ready.
                inputDropButton!.ToolTip = PartitionInventory.L("系统无法启用拖入，请点击选择文件。", "Shell drop is unavailable. Click to choose a file.") + "\n" + error.Message;
            }
        }
        SourceInitialized += (_, _) => Attach();
        Closed += (_, _) => { deviceIndexCancellation?.Cancel(); inputCancellation?.Cancel(); deviceFileDrop?.Dispose(); };
        Attach();
    }
}
