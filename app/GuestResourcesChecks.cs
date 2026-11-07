namespace OxpInstaller;

internal static class GuestResourcesChecks
{
    internal static void Run(Action<bool, string> require)
    {
        const long gib = 1024L * 1024 * 1024;
        // Heavy tasks take the larger allowance, bounded by the ceiling.
        var smallHost = GuestResourcesPolicy.Allocate("integration", 8, 32 * gib, 8 * gib);
        require(smallHost.MemoryMiB == GuestResourcesPolicy.HeavyFloorMiB && smallHost.CpuCount == 4,
            "guest-resources-heavy-task-keeps-floor-below-materialized-limit");
        var largeHost = GuestResourcesPolicy.Allocate("integration", 16, 96 * gib, 64 * gib);
        require(largeHost.MemoryMiB == GuestResourcesPolicy.CeilingMiB && largeHost.CpuCount == 8,
            "guest-resources-heavy-task-scales-to-ceiling");
        require(GuestResourcesPolicy.Allocate("integration-verify", 4, 64 * gib, 32 * gib).MemoryMiB == 8192
            && GuestResourcesPolicy.Allocate("model-maintenance-check", 4, 64 * gib, 32 * gib).MemoryMiB == 8192,
            "guest-resources-every-heavy-task-shares-the-policy");
        require(GuestResourcesPolicy.Allocate("integration-check", 16, 96 * gib, 64 * gib).MemoryMiB == GuestResourcesPolicy.OrdinaryFloorMiB,
            "guest-resources-ordinary-task-keeps-conservative-floor");
        // Host pressure never drops below the minimum and is reported.
        var pressured = GuestResourcesPolicy.Allocate("integration", 8, gib + gib / 2, 16 * gib);
        require(pressured.MemoryMiB == GuestResourcesPolicy.MinimumMiB && pressured.ReducedForHost,
            "guest-resources-reduces-and-reports-host-memory-fallback");
        require(!GuestResourcesPolicy.Allocate("integration", 8, 64 * gib, 64 * gib).ReducedForHost,
            "guest-resources-no-fallback-flag-with-capacity");
        // Unknown host readings keep the policy default instead of guessing.
        require(GuestResourcesPolicy.Allocate("integration", 8, 0, 0).MemoryMiB == GuestResourcesPolicy.HeavyFloorMiB,
            "guest-resources-unknown-host-memory-keeps-floor");
        // CPU count is clamped to a real parallelism window.
        require(GuestResourcesPolicy.Allocate("integration", 1, 64 * gib, 64 * gib).CpuCount == 2
            && GuestResourcesPolicy.Allocate("integration", 2, 64 * gib, 64 * gib).CpuCount == 2
            && GuestResourcesPolicy.Allocate("integration", 6, 64 * gib, 64 * gib).CpuCount == 3
            && GuestResourcesPolicy.Allocate("integration", 64, 64 * gib, 64 * gib).CpuCount == GuestResourcesPolicy.MaximumCpuCount,
            "guest-resources-cpu-count-clamps-to-parallel-window");
        require(GuestResourcesPolicy.Allocate("integration", 8, 64 * gib, 64 * gib).Accel == GuestResourcesPolicy.Tcg,
            "guest-resources-default-uses-multithreaded-translation");
        // WHPX is selected only when the host reports it, with a bounded
        // interrupt-chip attempt and a software fallback in the same ladder.
        var accelerated = GuestResourcesPolicy.Allocate("integration", 8, 64 * gib, 64 * gib, whpxAvailable: true);
        require(accelerated.Accel == GuestResourcesPolicy.Whpx
            && accelerated.Attempts.SequenceEqual(new[] { "whpx", "whpx,kernel-irqchip=off", "tcg,thread=multi" }),
            "guest-resources-whpx-ladder-falls-back-to-translation");
        // A forced accelerator is a measurement input and never replaced.
        var forcedTcg = GuestResourcesPolicy.Allocate("integration", 8, 64 * gib, 64 * gib, whpxAvailable: true, forcedAccel: "TCG");
        require(forcedTcg.Accel == GuestResourcesPolicy.Tcg && forcedTcg.Attempts.Count == 1,
            "guest-resources-forced-translation-is-not-upgraded");
        var forcedWhpx = GuestResourcesPolicy.Allocate("integration", 8, 64 * gib, 64 * gib, whpxAvailable: true, forcedAccel: "whpx");
        require(forcedWhpx.Accel == GuestResourcesPolicy.Whpx && forcedWhpx.Attempts.Count == 1,
            "guest-resources-forced-whpx-has-no-silent-fallback");
        require(GuestResourcesPolicy.Allocate("integration", 8, 64 * gib, 64 * gib, forcedAccel: "unknown").Accel == GuestResourcesPolicy.Tcg,
            "guest-resources-unknown-override-is-ignored");
        require(GuestResourcesPolicy.ForHost("integration").MemoryMiB >= GuestResourcesPolicy.MinimumMiB
            && GuestResourcesPolicy.ForHost("source-info").CpuCount >= 2,
            "guest-resources-host-probe-produces-a-usable-allowance");
    }
}
