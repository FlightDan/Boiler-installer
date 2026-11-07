using System.Runtime.InteropServices;

namespace OxpInstaller;

/// <summary>Host-derived QEMU allowance for one managed guest task.</summary>
internal sealed record GuestResources(int MemoryMiB, int CpuCount, string Accel, bool ReducedForHost, IReadOnlyList<string>? AccelAttempts = null)
{
    public string Summary => $"-m {MemoryMiB} -smp {CpuCount} -accel {Accel}" + (ReducedForHost ? " (host-memory-fallback)" : "");

    /// <summary>Accelerators to try in order; the first is the reported one.</summary>
    public IReadOnlyList<string> Attempts => AccelAttempts ?? [Accel];
}

/// <summary>
/// Pure selection of the guest memory/vCPU/accelerator triple. The host probe is
/// deliberately separate so the policy can be unit tested without a VM.
/// </summary>
internal static class GuestResourcesPolicy
{
    internal const int HeavyFloorMiB = 4096;
    internal const int OrdinaryFloorMiB = 2048;
    internal const int MinimumMiB = 1024;
    internal const int CeilingMiB = 8192;
    internal const int MaximumCpuCount = 8;
    // Leave the Windows host room for its own working set before handing memory to QEMU.
    internal const long HostReserveBytes = 1024L * 1024 * 1024;
    internal const string Tcg = "tcg,thread=multi";
    internal const string Whpx = "whpx";

    // These tasks mount both slots, install the component payload and run the
    // legacy adaptation hooks; they are the only guests that benefit from the
    // larger allowance.
    internal static bool IsHeavy(string task) =>
        task is "integration" or "integration-verify" or "model-maintenance-check";

    internal static GuestResources Allocate(string task, int logicalCpus, long availablePhysicalBytes, long totalPhysicalBytes,
        bool whpxAvailable = false, string? forcedAccel = null)
    {
        var target = IsHeavy(task) ? HeavyFloorMiB : OrdinaryFloorMiB;
        if (IsHeavy(task) && totalPhysicalBytes > 0)
        {
            // A quarter of physical memory, but never below the heavy floor.
            var quarter = totalPhysicalBytes / 4 / (1024 * 1024);
            target = (int)Math.Min(CeilingMiB, Math.Max(HeavyFloorMiB, quarter));
        }
        var reduced = false;
        if (availablePhysicalBytes > 0)
        {
            var usable = (availablePhysicalBytes - HostReserveBytes) / (1024 * 1024);
            if (usable < target)
            {
                target = (int)Math.Max(MinimumMiB, usable);
                reduced = target < (IsHeavy(task) ? HeavyFloorMiB : OrdinaryFloorMiB);
            }
        }
        var cpus = Math.Clamp(logicalCpus / 2, 2, MaximumCpuCount);
        var (accel, attempts) = Accel(whpxAvailable, NormalizeForced(forcedAccel));
        return new(target, cpus, accel, reduced, attempts);
    }

    private static (string Accel, IReadOnlyList<string>? Attempts) Accel(bool whpxAvailable, string? forced)
    {
        // A forced accelerator is a deliberate measurement input: it is used as
        // given and never silently replaced, so a baseline stays comparable.
        if (forced is not null) return (forced, null);
        if (!whpxAvailable) return (Tcg, null);
        // The first two attempts only differ in the interrupt chip; the final
        // attempt keeps the task runnable on a host that cannot enter WHPX.
        return (Whpx, [Whpx, Whpx + ",kernel-irqchip=off", Tcg]);
    }

    private static string? NormalizeForced(string? value) => value?.Trim().ToLowerInvariant() switch
    {
        null or "" => null,
        "tcg" => Tcg,
        "whpx" => Whpx,
        _ => null,
    };

    /// <summary>Current host allowance; the sole reader of live machine state.</summary>
    internal static GuestResources ForHost(string task)
    {
        var status = new MemoryStatus { Length = (uint)Marshal.SizeOf<MemoryStatus>() };
        var available = GlobalMemoryStatusEx(ref status) ? (long)status.AvailPhys : 0L;
        var forced = Environment.GetEnvironmentVariable("BOILER_QEMU_ACCEL");
        return Allocate(task, Environment.ProcessorCount, available, (long)status.TotalPhys,
            WhpxAvailable(), forced);
    }

    /// <summary>
    /// Windows Hypervisor Platform probe. A missing DLL, absent optional
    /// feature or unsupported hypervisor all mean "use the software path".
    /// </summary>
    internal static bool WhpxAvailable()
    {
        try
        {
            return WHvGetCapability(WhvCapabilityCodeHypervisorPresent, out var present,
                sizeof(int), out _) == 0 && present != 0;
        }
        catch (Exception error) when (error is DllNotFoundException or EntryPointNotFoundException or BadImageFormatException)
        {
            return false;
        }
    }

    private const int WhvCapabilityCodeHypervisorPresent = 0x00000000;

    [DllImport("WinHvPlatform.dll", ExactSpelling = true)]
    private static extern int WHvGetCapability(int capabilityCode, out int capabilityBuffer,
        uint capabilityBufferSizeInBytes, out uint writtenSizeInBytes);

    [StructLayout(LayoutKind.Sequential)]
    private struct MemoryStatus
    {
        public uint Length;
        public uint MemoryLoad;
        public ulong TotalPhys;
        public ulong AvailPhys;
        public ulong TotalPageFile;
        public ulong AvailPageFile;
        public ulong TotalVirtual;
        public ulong AvailVirtual;
        public ulong AvailExtendedVirtual;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GlobalMemoryStatusEx(ref MemoryStatus buffer);
}
